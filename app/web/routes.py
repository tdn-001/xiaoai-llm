from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from starlette import status as http_status
from pydantic import BaseModel, Field

from app.auth import (
    COOKIE_NAME,
    require_auth,
    set_session_cookie,
    verify_password,
)
from app.config import ConfigStore, merge_secret_fields
from app.logging_store import memory_logs
from app.mijia.client import MijiaClient
from app.mijia.compatibility import MODELS, get_model
from app.models import AppConfig, DeviceOverride, MijiaAccount
from app.runtime import RuntimeManager

AUDIO_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+\.mp3$")


class PasswordBody(BaseModel):
    password: str = Field(min_length=6, max_length=128)
    setup_token: str = ""


class PasswordResetBody(BaseModel):
    token: str = Field(min_length=8, max_length=512)
    new_password: str = Field(min_length=6, max_length=128)


class ConfigBody(BaseModel):
    model_config = {"extra": "allow"}

    version: int = 1


class MijiaLoginBody(BaseModel):
    account_id: str | None = None
    login_type: str | None = None
    username: str | None = None
    password: str | None = None
    user_id: str | None = None
    pass_token: str | None = None
    region: str | None = None


class MijiaAccountBody(BaseModel):
    id: str | None = None
    name: str | None = None
    enabled: bool | None = None
    login_type: str | None = None
    username: str | None = None
    password: str | None = None
    user_id: str | None = None
    pass_token: str | None = None
    region: str | None = None


class BindingsBody(BaseModel):
    account_id: str = ""
    dids: list[str]


class DeviceUpdateBody(BaseModel):
    model_config = {"extra": "allow"}


class TextBody(BaseModel):
    text: str = Field(default="你好，这是一条测试播报", max_length=500)


class LLMTestBody(BaseModel):
    profile_id: str = "default"
    api_base: str | None = None
    api_key: str | None = None
    model: str | None = None


class WebSearchTestBody(BaseModel):
    query: str = "今天北京的天气"


def build_api_router(config_store: ConfigStore, runtime: RuntimeManager) -> tuple[APIRouter, APIRouter]:
    protected = APIRouter(dependencies=[Depends(require_auth)])
    public = APIRouter()

    # ------------------------------------------------------------------ auth
    _attempts: dict[str, list[float]] = {}

    def _rate_limit(request: Request, action: str = "default") -> None:
        key = f"{action}:{request.client.host if request.client else 'unknown'}"
        now = time.time()
        recent = [t for t in _attempts.get(key, []) if now - t < 3600]
        if len(recent) >= 10:
            raise HTTPException(http_status.HTTP_429_TOO_MANY_REQUESTS, "尝试过于频繁，请稍后再试")
        recent.append(now)
        _attempts[key] = recent

    @public.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @public.get("/api/auth/status")
    async def auth_status(request: Request) -> dict[str, bool]:
        config = config_store.value
        from app.auth import COOKIE_NAME, verify_session

        authenticated = verify_session(request.cookies.get(COOKIE_NAME, ""), config.web.session_secret)
        return {"initialized": bool(config.web.password_hash), "authenticated": authenticated}

    @public.post("/api/auth/setup")
    async def auth_setup(body: PasswordBody, response: Response) -> dict[str, str]:
        from app.auth import hash_password

        result = config_store.setup_password(hash_password(body.password), body.setup_token)
        if result == "initialized":
            raise HTTPException(http_status.HTTP_409_CONFLICT, "管理密码已设置")
        if result == "invalid":
            raise HTTPException(http_status.HTTP_403_FORBIDDEN, "初始化令牌错误")
        config = config_store.value
        set_session_cookie(response, config.web.session_secret)
        return {"message": "管理密码已设置"}

    @public.post("/api/auth/login")
    async def auth_login(body: PasswordBody, request: Request, response: Response) -> dict[str, str]:
        _rate_limit(request)
        config = config_store.value
        if not config.web.password_hash:
            raise HTTPException(http_status.HTTP_428_PRECONDITION_REQUIRED, "请先设置管理密码")
        if not verify_password(body.password, config.web.password_hash):
            raise HTTPException(http_status.HTTP_401_UNAUTHORIZED, "密码错误")
        set_session_cookie(response, config.web.session_secret)
        return {"message": "登录成功"}

    @public.post("/api/auth/logout")
    async def auth_logout(response: Response) -> dict[str, str]:
        response.delete_cookie(COOKIE_NAME)
        return {"message": "已退出"}

    @public.post("/api/auth/reset-password/request")
    async def auth_reset_request(request: Request) -> dict[str, str]:
        """请求重置管理密码：生成一次性令牌并写入日志。

        无需登录；响应只返回"已生成"，令牌写入服务端日志与 config.json，
        用户需到服务器查看令牌（5 分钟内有效）。
        """
        _rate_limit(request, action="reset_request")
        token = config_store.request_password_reset()
        if token is None:
            raise HTTPException(
                http_status.HTTP_428_PRECONDITION_REQUIRED,
                "尚未设置管理密码，请用初始化令牌完成首次设置",
            )
        # 醒目日志输出，方便用户从终端 / docker logs 复制令牌
        logging.getLogger("xiaoai.security").warning(
            "=" * 60
        )
        logging.getLogger("xiaoai.security").warning(
            "[!!! SECURITY !!!] 重置密码令牌 reset_token=%s（5 分钟内有效，请妥善保管）",
            token,
        )
        logging.getLogger("xiaoai.security").warning(
            "=" * 60
        )
        return {
            "message": (
                "重置令牌已生成。请到服务端日志（或 config.json 中 "
                "web.password_reset_token 字段）查看，5 分钟内有效。"
            )
        }

    @public.post("/api/auth/reset-password/confirm")
    async def auth_reset_confirm(body: PasswordResetBody, request: Request) -> dict[str, str]:
        """使用一次性令牌 + 新密码完成重置。"""
        from app.auth import hash_password

        _rate_limit(request, action="reset_confirm")
        result = config_store.confirm_password_reset(
            body.token, hash_password(body.new_password)
        )
        if result == "ok":
            return {"message": "密码已重置，请使用新密码登录"}
        if result == "not_initialized":
            raise HTTPException(
                http_status.HTTP_428_PRECONDITION_REQUIRED,
                "尚未初始化管理密码",
            )
        if result == "not_requested":
            raise HTTPException(
                http_status.HTTP_400_BAD_REQUEST,
                "未申请过密码重置，请先调用重置请求接口",
            )
        if result == "expired":
            raise HTTPException(
                http_status.HTTP_400_BAD_REQUEST, "重置令牌已过期，请重新申请"
            )
        raise HTTPException(http_status.HTTP_403_FORBIDDEN, "重置令牌错误")

    # ---------------------------------------------------------------- config
    def _validated_from_body(body: dict[str, Any]) -> AppConfig:
        current = config_store.value.model_dump(mode="json")
        merged = merge_secret_fields(body, current)
        try:
            return AppConfig.model_validate(merged)
        except Exception as exc:
            raise HTTPException(http_status.HTTP_422_UNPROCESSABLE_ENTITY, f"配置校验失败: {exc}") from exc

    @protected.get("/api/config")
    async def get_config() -> dict[str, Any]:
        return config_store.public_dict()

    @protected.put("/api/config")
    @protected.post("/api/config")
    async def put_config(request: Request) -> dict[str, str]:
        body = await request.json()
        config = _validated_from_body(body)
        config_store.save(config)
        return {"message": "配置已保存，点击“应用配置”生效"}

    @protected.post("/api/config/validate")
    async def validate_config(request: Request) -> dict[str, str]:
        body = await request.json()
        _validated_from_body(body)
        return {"message": "配置有效"}

    @protected.post("/api/config/apply")
    async def apply_config() -> dict[str, Any]:
        return await runtime.apply_config()

    # ---------------------------------------------------------------- mijia
    def _mask(value: str) -> str:
        return f"{value[:2]}****{value[-3:]}" if len(value) > 5 else ("已配置" if value else "")

    @protected.get("/api/mijia/accounts")
    async def list_mijia_accounts() -> list[dict[str, Any]]:
        config = config_store.value
        result = []
        for acct in config.mijia_accounts:
            client = runtime.mijia_client(acct.id)
            result.append(
                {
                    "id": acct.id,
                    "name": acct.name or acct.username or acct.user_id or acct.id,
                    "login_type": acct.login_type,
                    "region": acct.region,
                    "enabled": acct.enabled,
                    "authenticated": bool(client and client.authenticated),
                    "username": _mask(acct.username),
                    "user_id": _mask(acct.user_id),
                    "token_cached": acct.login_type == "pass_token" and bool(acct.pass_token),
                    "username_or_id": acct.username or acct.user_id,
                    "needs_relogin": acct.id in runtime._needs_relogin,
                }
            )
        return result

    @protected.post("/api/mijia/accounts")
    async def create_mijia_account(body: MijiaAccountBody) -> dict[str, Any]:
        config = config_store.value
        new_id = (body.id or "").strip() or f"account-{int(time.time())}"
        if any(a.id == new_id for a in config.mijia_accounts):
            raise HTTPException(http_status.HTTP_409_CONFLICT, "账号 ID 已存在")
        login_type = body.login_type or "pass_token"
        acct = MijiaAccount(
            id=new_id,
            name=body.name or "",
            enabled=body.enabled if body.enabled is not None else True,
            login_type=login_type,
            username=body.username or "",
            password=body.password or "",
            user_id=body.user_id or "",
            pass_token=body.pass_token or "",
            region=body.region or "cn",
            token_path=f"data/mi-token-{new_id}.json",
        )
        config.mijia_accounts.append(acct)
        config_store.save(config)
        await runtime.apply_config()
        return {"message": "账号已新增", "id": new_id}

    @protected.put("/api/mijia/accounts/{account_id}")
    async def update_mijia_account(account_id: str, body: MijiaAccountBody) -> dict[str, Any]:
        config = config_store.value
        idx = next((i for i, a in enumerate(config.mijia_accounts) if a.id == account_id), None)
        if idx is None:
            raise HTTPException(http_status.HTTP_404_NOT_FOUND, "账号不存在")
        current = config.mijia_accounts[idx].model_dump()
        merged = {**current, **body.model_dump(exclude_none=True)}
        merged["id"] = account_id
        config.mijia_accounts[idx] = MijiaAccount.model_validate(merged)
        config_store.save(config)
        await runtime.apply_config()
        return {"message": "账号已更新"}

    @protected.delete("/api/mijia/accounts/{account_id}")
    async def delete_mijia_account(account_id: str) -> dict[str, Any]:
        config = config_store.value
        before = len(config.mijia_accounts)
        config.mijia_accounts = [a for a in config.mijia_accounts if a.id != account_id]
        if len(config.mijia_accounts) == before:
            raise HTTPException(http_status.HTTP_404_NOT_FOUND, "账号不存在")
        # Detach devices that pointed at this account.
        for did, dev in list(config.devices.items()):
            if dev.account_id == account_id:
                dev.account_id = ""
        config.mijia.selected_devices = [
            d for d in config.mijia.selected_devices if d in config.devices
        ]
        config_store.save(config)
        await runtime.apply_config()
        return {"message": "账号已删除"}

    @protected.post("/api/mijia/accounts/{account_id}/login")
    async def login_mijia_account(account_id: str, body: MijiaLoginBody) -> dict[str, Any]:
        config = config_store.value
        idx = next((i for i, a in enumerate(config.mijia_accounts) if a.id == account_id), None)
        if idx is None:
            raise HTTPException(http_status.HTTP_404_NOT_FOUND, "账号不存在")
        acct = config.mijia_accounts[idx]
        if body.login_type:
            acct.login_type = body.login_type
        if body.region:
            acct.region = body.region
        if acct.login_type == "pass_token":
            if body.user_id is not None:
                acct.user_id = body.user_id.strip()
            if body.pass_token:
                acct.pass_token = body.pass_token.strip()
            if not acct.user_id or not acct.pass_token:
                raise HTTPException(
                    http_status.HTTP_400_BAD_REQUEST, "passToken 登录需要填写 userId 和 passToken"
                )
        else:
            if body.username is not None:
                acct.username = body.username.strip()
            if body.password:
                acct.password = body.password
            if not acct.username or not acct.password:
                raise HTTPException(
                    http_status.HTTP_400_BAD_REQUEST, "请填写小米账号和密码"
                )
        client = runtime.mijia_client(account_id) or MijiaClient(account_id)
        runtime._mijia_clients[account_id] = client
        try:
            await client.login(acct)
        except Exception as exc:
            config_store.save(config)
            runtime.mark_needs_relogin(account_id)
            raise HTTPException(http_status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        config_store.save(config)
        runtime._needs_relogin.discard(account_id)
        await runtime.rebuild_workers()
        return {"message": "登录成功", "authenticated": True}

    @protected.post("/api/mijia/accounts/{account_id}/logout")
    async def logout_mijia_account(account_id: str) -> dict[str, str]:
        config = config_store.value
        client = runtime.mijia_client(account_id)
        if client:
            await client.close()
            runtime._mijia_clients.pop(account_id, None)
        idx = next((i for i, a in enumerate(config.mijia_accounts) if a.id == account_id), None)
        if idx is not None:
            acct = config.mijia_accounts[idx]
            acct.username = ""
            acct.password = ""
            acct.user_id = ""
            acct.pass_token = ""
            config_store.save(config)
        await runtime.rebuild_workers()
        return {"message": "已退出该账号"}

    @protected.get("/api/mijia/accounts/{account_id}/devices")
    async def list_account_devices(account_id: str) -> list[dict[str, Any]]:
        client = runtime.mijia_client(account_id)
        if client is None or not client.authenticated:
            raise HTTPException(http_status.HTTP_409_CONFLICT, "请先登录此账号")
        try:
            devices = await client.discover_devices()
        except Exception as exc:
            raise HTTPException(http_status.HTTP_502_BAD_GATEWAY, f"获取设备列表失败: {exc}") from exc
        for device in devices:
            device["suggested_model"] = _suggest_model(device["hardware"])
        return devices

    # Legacy single-account routes retained for back-compat: route through the
    # first account.
    @protected.post("/api/mijia/login")
    async def mijia_login_legacy(body: MijiaLoginBody) -> dict[str, Any]:
        config = config_store.value
        if not config.mijia_accounts:
            new_id = "default"
            config.mijia_accounts.append(
                MijiaAccount(
                    id=new_id,
                    name=body.username or body.user_id or "默认账号",
                    login_type=body.login_type or "pass_token",
                    username=body.username or "",
                    password=body.password or "",
                    user_id=body.user_id or "",
                    pass_token=body.pass_token or "",
                    region=body.region or config.mijia.region or "cn",
                )
            )
            config_store.save(config)
        account_id = config.mijia_accounts[0].id
        return await login_mijia_account(account_id, body)

    @protected.post("/api/mijia/logout")
    async def mijia_logout_legacy() -> dict[str, str]:
        results = []
        for aid in list(runtime._mijia_clients.keys()):
            r = await logout_mijia_account(aid)
            results.append(r["message"])
        return {"message": "，".join(results) or "已退出米家账号"}

    @protected.get("/api/mijia/status")
    async def mijia_status_legacy() -> dict[str, Any]:
        config = config_store.value
        primary = config.mijia_accounts[0] if config.mijia_accounts else None
        return {
            "login_type": primary.login_type if primary else config.mijia.login_type,
            "authenticated": any(c.authenticated for c in runtime._mijia_clients.values()),
            "needs_relogin": runtime.needs_relogin,
            "username_masked": (
                _mask(primary.username) or _mask(primary.user_id) if primary else ""
            ),
            "token_cached": Path(primary.token_path).exists() if primary and primary.token_path else False,
        }

    @protected.get("/api/mijia/devices")
    async def mijia_devices_all() -> list[dict[str, Any]]:
        """Union of devices across all logged-in accounts, tagged with account_id."""
        results: list[dict[str, Any]] = []
        for account_id, client in runtime._mijia_clients.items():
            if not client.authenticated:
                continue
            try:
                devices = await client.discover_devices()
            except Exception as exc:
                logger.warning("账号 %s 获取设备失败: %s", account_id, exc)
                continue
            for device in devices:
                device["suggested_model"] = _suggest_model(device["hardware"])
                device["account_id"] = account_id
                results.append(device)
        if not results:
            raise HTTPException(http_status.HTTP_409_CONFLICT, "请先登录至少一个米家账号")
        return results

    @protected.get("/api/models")
    async def models() -> list[dict[str, Any]]:
        return MODELS

    def _suggest_model(hardware: str) -> str:
        hardware = hardware.upper()
        for item in MODELS:
            if item["hardware"] == hardware:
                return item["key"]
        return "custom"

    # --------------------------------------------------------------- devices
    @protected.get("/api/devices")
    async def list_devices() -> list[dict[str, Any]]:
        config = config_store.value
        result = []
        for did in config.mijia.selected_devices:
            if did not in config.devices:
                continue
            device = config.devices[did]
            effective = None
            try:
                from app.models import resolve_device

                effective = resolve_device(config, did).model_dump(mode="json")
            except Exception:
                pass
            worker = runtime.workers.get(did)
            result.append(
                {
                    "did": did,
                    "override": device.model_dump(mode="json"),
                    "effective": effective,
                    "compatibility": get_model(device.compatibility_key),
                    "runtime": dict(worker.stats) if worker else {"listening": False},
                }
            )
        return result

    @protected.put("/api/devices/bindings")
    async def bind_devices(body: BindingsBody) -> dict[str, Any]:
        config = config_store.value
        account_id = body.account_id
        account = next((a for a in config.mijia_accounts if a.id == account_id), None)
        if account is None:
            raise HTTPException(http_status.HTTP_409_CONFLICT, "请先选择米家账号")
        client = runtime.mijia_client(account_id)
        if client is None or not client.authenticated:
            raise HTTPException(http_status.HTTP_409_CONFLICT, "该账号尚未登录")
        try:
            discovered = {d["did"]: d for d in await client.discover_devices()}
        except Exception as exc:
            raise HTTPException(http_status.HTTP_502_BAD_GATEWAY, f"获取设备列表失败: {exc}") from exc
        for did in body.dids:
            info = discovered.get(did)
            existing = config.devices.get(did, DeviceOverride())
            existing.name = existing.name or (info["name"] if info else did)
            existing.hardware = info["hardware"] if info else existing.hardware
            existing.mina_device_id = info["mina_device_id"] if info else existing.mina_device_id
            existing.account_id = account_id
            if info:
                existing.compatibility_key = _suggest_model(info["hardware"])
            config.devices[did] = existing
        # Drop bindings from other accounts that are not in this batch.
        for did in list(config.devices):
            if did not in body.dids or config.devices[did].account_id != account_id:
                config.devices.pop(did)
                runtime.memory.clear(did)
        # selected_devices stays global: every bound device, regardless of account.
        config.mijia.selected_devices = list(config.devices.keys())
        config_store.save(config)
        active = await runtime.rebuild_workers()
        return {
            "message": f"账号 {account.name or account_id} 已绑定 {len(body.dids)} 台设备，{active} 台正在监听",
            "count": len(body.dids),
            "active": active,
        }

    @protected.get("/api/devices/{did}")
    async def get_device(did: str) -> dict[str, Any]:
        config = config_store.value
        if did not in config.devices:
            raise HTTPException(http_status.HTTP_404_NOT_FOUND, "设备未绑定")
        from app.models import resolve_device

        return {
            "did": did,
            "override": config.devices[did].model_dump(mode="json"),
            "effective": resolve_device(config, did).model_dump(mode="json"),
        }

    @protected.put("/api/devices/{did}")
    async def update_device(did: str, request: Request) -> dict[str, str]:
        config = config_store.value
        if did not in config.devices:
            raise HTTPException(http_status.HTTP_404_NOT_FOUND, "设备未绑定")
        body = await request.json()
        current = config.devices[did].model_dump(mode="json")
        merged = {**current, **body}
        try:
            config.devices[did] = DeviceOverride.model_validate(merged)
        except Exception as exc:
            raise HTTPException(http_status.HTTP_422_UNPROCESSABLE_ENTITY, f"设备配置校验失败: {exc}") from exc
        config_store.save(config)
        await runtime.rebuild_workers()
        return {"message": "设备配置已保存"}

    @protected.delete("/api/devices/{did}/context")
    async def clear_device_context(did: str) -> dict[str, str]:
        runtime.memory.clear(did)
        return {"message": "已清除该设备的会话上下文"}

    @protected.post("/api/devices/{did}/test/tts")
    async def test_tts(did: str, body: TextBody) -> dict[str, str]:
        config = config_store.value
        _require_bound(config, did)
        from app.models import resolve_device

        device = resolve_device(config, did)
        client = runtime.mijia_client(device.account_id)
        if client is None or not client.authenticated:
            raise HTTPException(http_status.HTTP_409_CONFLICT, "该设备关联的账号未登录")
        try:
            await runtime.tts.speak(config, device, body.text, client)
        except Exception as exc:
            raise HTTPException(http_status.HTTP_502_BAD_GATEWAY, f"播报失败: {exc}") from exc
        return {"message": "已发送播报"}

    @protected.post("/api/devices/{did}/test/mute")
    async def test_mute(did: str) -> dict[str, str]:
        config = config_store.value
        _require_bound(config, did)
        from app.models import resolve_device

        device = resolve_device(config, did)
        client = runtime.mijia_client(device.account_id)
        if client is None or not client.authenticated:
            raise HTTPException(http_status.HTTP_409_CONFLICT, "该设备关联的账号未登录")
        try:
            await client.mute(device)
        except Exception as exc:
            raise HTTPException(http_status.HTTP_502_BAD_GATEWAY, f"暂停失败: {exc}") from exc
        return {"message": "已发送暂停指令"}

    @protected.post("/api/devices/{did}/test/audio")
    async def test_audio(did: str, body: TextBody) -> dict[str, str]:
        config = config_store.value
        _require_bound(config, did)
        from app.models import resolve_device

        device = resolve_device(config, did)
        client = runtime.mijia_client(device.account_id)
        if client is None or not client.authenticated:
            raise HTTPException(http_status.HTTP_409_CONFLICT, "该设备关联的账号未登录")
        try:
            await runtime.tts.speak(config, device, body.text, client)
        except Exception as exc:
            raise HTTPException(http_status.HTTP_502_BAD_GATEWAY, f"播放失败: {exc}") from exc
        return {"message": "已发送音频播放"}

    def _require_bound(config: AppConfig, did: str) -> None:
        if did not in config.devices:
            raise HTTPException(http_status.HTTP_404_NOT_FOUND, "设备未绑定")
        device = config.devices[did]
        if not device.account_id:
            raise HTTPException(http_status.HTTP_409_CONFLICT, "设备未关联米家账号")
        if not runtime.mijia.authenticated:
            raise HTTPException(http_status.HTTP_409_CONFLICT, "请先登录米家账号")

    # ------------------------------------------------------------------- llm
    @protected.post("/api/llm/profiles/{profile_id}/test")
    async def test_llm(profile_id: str, body: LLMTestBody) -> dict[str, str]:
        config = config_store.value
        try:
            profile = runtime.profile(config, profile_id).model_copy(deep=True)
        except ValueError as exc:
            raise HTTPException(http_status.HTTP_404_NOT_FOUND, str(exc)) from exc
        if body.api_base:
            profile.api_base = body.api_base
        if body.api_key:
            profile.api_key = body.api_key
        if body.model:
            profile.model = body.model
        profile.stream = False
        profile.timeout_seconds = min(profile.timeout_seconds, 30)
        try:
            answer = await runtime.llm.complete(profile, [{"role": "user", "content": "请回复：连接成功"}])
        except Exception as exc:
            raise HTTPException(http_status.HTTP_502_BAD_GATEWAY, f"LLM 调用失败: {exc}") from exc
        return {"message": answer[:200]}

    # ------------------------------------------------------------ web search
    @protected.post("/api/web-search/test")
    async def test_web_search(body: WebSearchTestBody) -> dict[str, str]:
        config = config_store.value
        try:
            result = await runtime.web_search.research(config.web_search, body.query)
        except Exception as exc:
            raise HTTPException(http_status.HTTP_502_BAD_GATEWAY, f"网络搜索失败: {exc}") from exc
        return {"message": (result[:300] + "…") if len(result) > 300 else (result or "无返回内容")}

    # ------------------------------------------------------------------ logs
    @protected.get("/api/logs")
    async def logs(level: str | None = None, after: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        return memory_logs.list(level=level, after=after, limit=limit)

    @protected.delete("/api/logs")
    async def clear_logs() -> dict[str, str]:
        memory_logs.clear()
        return {"message": "日志已清空"}

    # ---------------------------------------------------------------- status
    @protected.get("/api/status")
    async def overall_status() -> dict[str, Any]:
        return await runtime.status()

    # ----------------------------------------------------------------- audio
    @public.get("/audio/{name}")
    async def audio(name: str) -> FileResponse:
        if not AUDIO_NAME_PATTERN.match(name):
            raise HTTPException(http_status.HTTP_404_NOT_FOUND, " Not Found")
        path = runtime.tts.audio_dir / name
        if not path.exists():
            raise HTTPException(http_status.HTTP_404_NOT_FOUND, "音频不存在或已清理")
        return FileResponse(path, media_type="audio/mpeg")

    return public, protected
