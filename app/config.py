from __future__ import annotations

import json
import hmac
import os
import secrets
import time
from pathlib import Path
from threading import RLock
from typing import Any

from app.models import AppConfig


SENSITIVE_KEYS = {
    "password",
    "api_key",
    "session_secret",
    "bootstrap_token",
    "headers",
    "pass_token",
    "password_reset_token",
}


class ConfigStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = RLock()
        self._config = AppConfig()

    @property
    def value(self) -> AppConfig:
        with self._lock:
            return self._config.model_copy(deep=True)

    def load(self) -> AppConfig:
        with self._lock:
            if self.path.exists():
                raw = json.loads(self.path.read_text("utf-8"))
                search_migrated = self._migrate_legacy_search(raw)
                self._config = AppConfig.model_validate(raw)
            else:
                search_migrated = False
                self._config.web.session_secret = secrets.token_urlsafe(32)
                self._config.web.bootstrap_token = secrets.token_urlsafe(18)
                self.save(self._config)
            self._migrate_legacy_account()
            if search_migrated:
                self.save(self._config)
            if not self._config.web.password_hash and not self._config.web.bootstrap_token:
                self._config.web.bootstrap_token = secrets.token_urlsafe(18)
                self.save(self._config)
            return self._config.model_copy(deep=True)

    @staticmethod
    def _migrate_legacy_search(raw: dict[str, Any]) -> bool:
        """Convert removed MCP search settings into built-in web search.

        Existing installs commonly enabled MCP on the LLM profile. Preserve
        that intent as the default device-level network-search switch.
        """
        migrated = "mcp" in raw or any(
            "enable_mcp_search" in profile
            for profile in (raw.get("llm_profiles") or [])
            if isinstance(profile, dict)
        )
        web_search = raw.get("web_search")
        if isinstance(web_search, dict) and web_search.get("search_url") in (
            "https://html.duckduckgo.com/html/",
            "https://www.bing.com/search",
        ):
            web_search["search_url"] = "https://www.sogou.com/web"
            migrated = True
        if "web_search" not in raw:
            legacy = raw.get("mcp") or {}
            raw["web_search"] = {
                "enabled": bool(legacy.get("enabled", False)),
                "search_url": "https://www.sogou.com/web",
                "max_results": 5,
                "fetch_top_results": 2,
                "timeout_seconds": min(float(legacy.get("timeout_seconds", 15)), 60),
                "max_page_chars": 6000,
                "max_result_chars": int(legacy.get("max_result_chars", 12000)),
            }
        profiles = raw.get("llm_profiles") or []
        defaults = raw.setdefault("defaults", {})
        if "web_search_enabled" not in defaults:
            defaults["web_search_enabled"] = any(
                bool(profile.get("enable_mcp_search"))
                for profile in profiles
                if isinstance(profile, dict)
            )
        raw.pop("mcp", None)
        for profile in profiles:
            if isinstance(profile, dict):
                profile.pop("enable_mcp_search", None)
        return migrated

    def _migrate_legacy_account(self) -> None:
        """Promote the legacy single-account fields into mijia_accounts if any
        credential-bearing fields are populated and the list is still empty.

        Older configs stored username/password/user_id/pass_token directly on
        AppConfig.mijia. The new model keeps global poll settings on mijia but
        moves credentials into the mijia_accounts list.
        """
        from app.models import MijiaAccount

        if self._config.mijia_accounts:
            return
        legacy = self._config.mijia
        if not (
            legacy.username
            or legacy.password
            or legacy.user_id
            or legacy.pass_token
            or legacy.token_path
        ):
            return
        account_id = "default"
        self._config.mijia_accounts = [
            MijiaAccount(
                id=account_id,
                name=legacy.username or legacy.user_id or "默认账号",
                login_type=legacy.login_type,
                username=legacy.username,
                password=legacy.password,
                user_id=legacy.user_id,
                pass_token=legacy.pass_token,
                region=legacy.region,
                token_path=legacy.token_path,
            )
        ]
        self.save(self._config)

    def save(self, config: AppConfig) -> None:
        with self._lock:
            if not config.web.session_secret:
                config.web.session_secret = self._config.web.session_secret or secrets.token_urlsafe(32)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(self.path.suffix + ".tmp")
            temp.write_text(config.model_dump_json(indent=2), "utf-8")
            os.chmod(temp, 0o600)
            temp.replace(self.path)
            os.chmod(self.path, 0o600)
            self._config = config.model_copy(deep=True)

    def setup_password(self, password_hash: str, bootstrap_token: str) -> str:
        """Atomically initialize the administrator password once."""
        with self._lock:
            if self._config.web.password_hash:
                return "initialized"
            if not hmac.compare_digest(bootstrap_token, self._config.web.bootstrap_token):
                return "invalid"
            config = self._config.model_copy(deep=True)
            config.web.password_hash = password_hash
            config.web.bootstrap_token = ""
            self.save(config)
            return "ok"

    RESET_TOKEN_TTL_SECONDS = 300  # 5 分钟

    def request_password_reset(self) -> str | None:
        """Generate a one-time password reset token.

        Returns the token string. Writes it (and its expiry) into config.json.
        Returns None if the password has not been initialized yet (nothing to
        reset — user should run setup instead).
        """
        with self._lock:
            if not self._config.web.password_hash:
                return None
            token = secrets.token_urlsafe(32)
            config = self._config.model_copy(deep=True)
            config.web.password_reset_token = token
            config.web.password_reset_expires_at = (
                time.time() + self.RESET_TOKEN_TTL_SECONDS
            )
            self.save(config)
            return token

    def confirm_password_reset(
        self, token: str, new_password_hash: str
    ) -> str:
        """Verify a reset token and rotate the password hash.

        Returns one of: "ok", "not_requested", "expired", "invalid",
        "not_initialized".
        """
        with self._lock:
            if not self._config.web.password_hash:
                return "not_initialized"
            stored = self._config.web.password_reset_token
            expires = self._config.web.password_reset_expires_at
            if not stored or not expires:
                return "not_requested"
            if time.time() > expires:
                # 清空过期 token
                config = self._config.model_copy(deep=True)
                config.web.password_reset_token = ""
                config.web.password_reset_expires_at = 0.0
                self.save(config)
                return "expired"
            if not hmac.compare_digest(token, stored):
                return "invalid"
            config = self._config.model_copy(deep=True)
            config.web.password_hash = new_password_hash
            config.web.password_reset_token = ""
            config.web.password_reset_expires_at = 0.0
            self.save(config)
            return "ok"

    def public_dict(self) -> dict[str, Any]:
        data = self.value.model_dump(mode="json")
        self._redact(data)
        return data

    @classmethod
    def _redact(cls, value: Any) -> None:
        if isinstance(value, dict):
            for key, item in list(value.items()):
                if key == "headers" and isinstance(item, dict):
                    value[f"{key}_configured"] = bool(item)
                    value[key] = {name: "" for name in item}
                elif key in SENSITIVE_KEYS:
                    value[f"{key}_configured"] = bool(item)
                    value[key] = ""
                else:
                    cls._redact(item)
        elif isinstance(value, list):
            for item in value:
                cls._redact(item)


def merge_secret_fields(incoming: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """An empty secret in a form means keep the stored value."""
    for key, value in list(incoming.items()):
        if key.endswith("_configured"):
            incoming.pop(key)
            continue
        if key == "headers" and isinstance(value, dict):
            stored_headers = current.get(key, {})
            if not value:
                incoming[key] = stored_headers
            else:
                for header, header_value in list(value.items()):
                    if header_value == "" and header in stored_headers:
                        value[header] = stored_headers[header]
        elif key in SENSITIVE_KEYS and (value == "" or value == {}):
            incoming[key] = current.get(key, value)
        elif isinstance(value, dict) and isinstance(current.get(key), dict):
            merge_secret_fields(value, current[key])
        elif isinstance(value, list) and isinstance(current.get(key), list):
            current_by_id = {x.get("id"): x for x in current[key] if isinstance(x, dict)}
            for item in value:
                if isinstance(item, dict) and item.get("id") in current_by_id:
                    merge_secret_fields(item, current_by_id[item["id"]])
    return incoming
