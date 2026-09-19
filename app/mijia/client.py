from __future__ import annotations

import json
import hashlib
import logging
import os
import random
import string
import time
from pathlib import Path
from typing import Any

from aiohttp import ClientSession, ClientTimeout
from miservice import MiAccount, MiIOService, MiNAService, miio_command

from app.models import AppConfig, EffectiveDeviceConfig, MijiaAccount


log = logging.getLogger(__name__)

# userprofile GET must never hang a worker loop; keep it well below the idle
# poll interval so one stalled request cannot freeze question intake.
USERPROFILE_TIMEOUT_SECONDS = 5
USERPROFILE_UA = (
    "Mozilla/5.0 (Linux; Android 10; 000; wv) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Version/4.0 Chrome/119.0.6045.193 Mobile Safari/537.36 /XiaoMi/HybridView/ "
    "micoSoundboxApp/i appVersion/A_2.4.40"
)
USERPROFILE_REFERER = "https://userprofile.mina.mi.com/dialogue-note/index.html"


def build_pass_token_file(user_id: str, pass_token: str, device_id: str | None = None) -> dict[str, str]:
    """Token file understood by miservice's MiTokenStore/MiAccount.

    With userId + passToken present, MiAccount.serviceLogin exchanges the
    passToken for fresh service tokens without touching the stored password.
    """
    return {
        "deviceId": device_id or "".join(random.sample(string.ascii_letters + string.digits, 16)).upper(),
        "userId": str(user_id),
        "passToken": pass_token,
    }


class StableMiAccount(MiAccount):
    """MiAccount with non-destructive SID refresh.

    Xiaomi sometimes omits passToken when an existing passToken is exchanged
    for a second service SID (notably xiaomiio). miservice-fork treats that as
    a KeyError and deletes the otherwise valid token file.
    """

    async def login(self, sid: str) -> bool:
        if not self.token:
            self.token = self.token_store.load_token() if self.token_store else None
        if not self.token:
            self.token = {"deviceId": build_pass_token_file("", "")["deviceId"]}
        try:
            response = await self._serviceLogin(f"serviceLogin?sid={sid}&_json=true")
            if response["code"] != 0:
                data = {
                    "_json": "true",
                    "qs": response["qs"],
                    "sid": response["sid"],
                    "_sign": response["_sign"],
                    "callback": response["callback"],
                    "user": self.username,
                    "hash": hashlib.md5(self.password.encode()).hexdigest().upper(),
                }
                response = await self._serviceLogin("serviceLoginAuth2", data)
                if response["code"] != 0:
                    raise RuntimeError("Xiaomi service login rejected")

            self.token["userId"] = response.get("userId", self.token.get("userId"))
            pass_token = response.get("passToken") or self.token.get("passToken")
            if not self.token["userId"] or not pass_token:
                raise RuntimeError("Xiaomi login response is missing account credentials")
            self.token["passToken"] = pass_token
            service_token = await self._securityTokenService(
                response["location"], response["nonce"], response["ssecurity"]
            )
            self.token[sid] = (response["ssecurity"], service_token)
            if self.token_store:
                self.token_store.save_token(self.token)
                os.chmod(self.token_store.token_path, 0o600)
            return True
        except Exception as exc:
            # Keep the base passToken and already valid service tokens. A
            # failure to obtain one SID must not log the user out everywhere.
            self.token.pop(sid, None)
            log.warning("小米服务 %s 授权失败: %s", sid, exc)
            return False


async def fetch_latest_question(
    client: "MijiaClient",
    device: EffectiveDeviceConfig,
    source: str = "ubus",
    fallback: bool = True,
) -> dict[str, Any] | None:
    """Fetch the newest ASR question for a device.

    source="userprofile" uses a lightweight HTTPS conversation endpoint (no
    UBus traffic); source="ubus" uses the device-scoped mibrain/nlp_result_get
    UBus call. When userprofile fails and fallback is enabled, UBus is used for
    that single poll.
    """
    if source == "userprofile" and device.hardware:
        try:
            return await client.latest_question_userprofile(device)
        except Exception as exc:
            log.warning("userprofile 轮询失败: %s", exc, extra={"device": device.did})
            if not fallback:
                raise
    return await client.latest_question_ubus(device)


class MijiaClient:
    """Per-account MiNA/MIoT session. One instance == one logged-in account."""

    def __init__(self, account_id: str) -> None:
        self.account_id = account_id
        self.session: ClientSession | None = None
        self.account: MiAccount | None = None
        self.mina: MiNAService | None = None
        self.miio: MiIOService | None = None
        self.authenticated = False
        self._last_credentials: MijiaAccount | None = None

    @staticmethod
    def account_has_credentials(acct: MijiaAccount) -> bool:
        if acct.login_type == "pass_token":
            return bool(acct.user_id and acct.pass_token)
        return bool(acct.username and acct.password)

    async def login(self, credentials: MijiaAccount) -> MijiaAccount:
        """Authenticate using the supplied credentials (not the full AppConfig).

        The credentials object is returned (possibly with refreshed passToken)
        so callers can persist it.
        """
        await self.close()
        if not self.account_has_credentials(credentials):
            raise ValueError("请先填写小米账号和密码或 passToken")
        token_path = (
            Path(credentials.token_path) if credentials.token_path
            else Path(f"data/mi-token-{credentials.id}.json")
        )
        token_path.parent.mkdir(parents=True, exist_ok=True)
        # Session-wide cap so a stuck UBus/HTTP call can never freeze a worker;
        # userprofile polls use a tighter per-request timeout.
        self.session = ClientSession(timeout=ClientTimeout(total=30))
        if credentials.login_type == "pass_token":
            token_file = build_pass_token_file(credentials.user_id, credentials.pass_token)
            token_path.write_text(json.dumps(token_file, indent=2), "utf-8")
            os.chmod(token_path, 0o600)
            # Optional stored credentials are a recovery path if Xiaomi has
            # rotated/revoked the pasted passToken. Direct passToken login still
            # works when username/password are empty.
            self.account = StableMiAccount(
                self.session, credentials.username, credentials.password, str(token_path)
            )
        else:
            self.account = StableMiAccount(
                self.session, credentials.username, credentials.password, str(token_path)
            )

        if not await self.account.login("micoapi"):
            await self.close()
            if credentials.login_type == "pass_token":
                raise RuntimeError("passToken 登录失败：令牌可能已失效，请重新获取后再试")
            raise RuntimeError("小米账号登录失败，请检查账号、密码、地区或风控验证")
        if credentials.login_type == "pass_token":
            token = getattr(self.account, "token", None)
            refreshed = token.get("passToken") if token else None
            if refreshed:
                credentials.pass_token = refreshed
        self.mina = MiNAService(self.account)
        self.miio = MiIOService(self.account, credentials.region)
        self.authenticated = True
        self._last_credentials = credentials.model_copy()
        log.info(
            "米家账号 %s 登录成功（%s）",
            credentials.name or credentials.username or credentials.user_id or credentials.id,
            "passToken" if credentials.login_type == "pass_token" else "密码",
        )
        return credentials

    async def close(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()
        self.session = None
        self.account = None
        self.mina = None
        self.miio = None
        self.authenticated = False
        self._last_credentials = None

    async def discover_devices(self) -> list[dict[str, Any]]:
        self._require_login()
        mina_devices = await self.mina.device_list() or []
        miio_devices = []
        try:
            miio_devices = await self.miio.device_list(name="full") or []
        except Exception as exc:
            # MiNA already contains every field required to bind and control a
            # speaker. MIoT is enrichment only and may be unavailable for some
            # passTokens/regions.
            log.warning("MIoT 设备列表获取失败，使用 MiNA 设备列表继续: %s", exc)
        miio_by_did = {str(item.get("did")): item for item in miio_devices}
        devices: list[dict[str, Any]] = []
        for item in mina_devices:
            did = str(item.get("miotDID") or "")
            mina_device_id = str(item.get("deviceID") or "")
            if not did or not mina_device_id:
                log.warning("忽略缺少设备标识的 MiNA 记录: %s", item.get("name", "unknown"))
                continue
            hardware = str(item.get("hardware") or "").upper()
            linked = miio_by_did.get(did, {})
            devices.append(
                {
                    "did": did,
                    "mina_device_id": mina_device_id,
                    "name": item.get("name") or linked.get("name") or hardware or did,
                    "hardware": hardware,
                    "model": linked.get("model", ""),
                    "online": item.get("online", True),
                    "capabilities": item.get("capabilities", {}),
                }
            )
        return devices

    async def latest_question_ubus(self, device: EffectiveDeviceConfig) -> dict[str, Any] | None:
        self._require_login()
        records = await self.mina.get_latest_ask(device.mina_device_id)
        if not records:
            return None
        record = records[0]
        response = record.get("response", {})
        answers = response.get("answer", [])
        answer = answers[0] if answers else {}
        return {
            "id": str(record.get("request_id") or record.get("timestamp_ms") or ""),
            "time": int(record.get("timestamp_ms") or 0),
            "query": str(answer.get("question") or "").strip(),
            "native_answer": str(answer.get("content") or ""),
        }

    async def latest_question_userprofile(self, device: EffectiveDeviceConfig) -> dict[str, Any] | None:
        """Read the newest conversation record via the userprofile endpoint.

        This is a plain HTTPS GET against userprofile.mina.mi.com; it does not
        touch the device UBus channel. Records are keyed by hardware, so two
        bound speakers of the same model share records (the worker layer
        de-duplicates by claim).

        The cookie `deviceId` MUST be the speaker's MiNA deviceID: the
        conversation history is archived per device, and passing the passport
        deviceId returns an empty record list.
        """
        self._require_login()
        token = self.account.token if self.account else None
        service_token = (token or {}).get("micoapi", [None, None])[1]
        if not token or not service_token:
            raise RuntimeError("米家令牌缺少 micoapi serviceToken")
        if not device.mina_device_id:
            raise RuntimeError("设备缺少 MiNA deviceID，无法查询对话记录")
        cookies = {
            "userId": str(token.get("userId", "")),
            "serviceToken": service_token,
            "deviceId": device.mina_device_id,
        }
        url = (
            "https://userprofile.mina.mi.com/device_profile/v2/conversation"
            f"?source=dialogu&hardware={device.hardware}"
            f"&timestamp={int(time.time() * 1000)}&limit=2"
        )
        async with self.session.get(
            url,
            cookies=cookies,
            headers={"User-Agent": USERPROFILE_UA, "Referer": USERPROFILE_REFERER},
            timeout=ClientTimeout(total=USERPROFILE_TIMEOUT_SECONDS),
        ) as response:
            payload = await response.json(content_type=None)
        return self._parse_userprofile(payload)

    @staticmethod
    def _parse_userprofile(payload: Any) -> dict[str, Any] | None:
        if not isinstance(payload, dict) or payload.get("code") != 0:
            raise RuntimeError(f"对话记录接口返回异常: {payload}")
        data = payload.get("data")
        if isinstance(data, str):
            data = json.loads(data)
        records = (data or {}).get("records") or []
        if not records:
            return None
        record = records[0]
        answers = record.get("answers") or []
        native = ""
        if answers and isinstance(answers[0], dict):
            native = str((answers[0].get("tts") or {}).get("text") or "")
        return {
            "id": str(record.get("requestId") or record.get("time") or ""),
            "time": int(record.get("time") or 0),
            "query": str(record.get("query") or "").strip(),
            "native_answer": native,
        }

    async def fetch_native_answer(self, device: EffectiveDeviceConfig) -> str:
        """Optional UBus nlp_result_get read, for logging the native reply."""
        record = await self.latest_question_ubus(device)
        return str(record.get("native_answer") or "") if record else ""

    async def mute(self, device: EffectiveDeviceConfig) -> None:
        self._require_login()
        await self.mina.player_pause(device.mina_device_id)

    async def speak_mijia(self, device: EffectiveDeviceConfig, text: str) -> None:
        self._require_login()
        try:
            await self.mina.text_to_speech(device.mina_device_id, text)
            return
        except Exception as mina_error:
            error_str = str(mina_error)
            if "3012" in error_str or "远程控制超时" in error_str:
                log.warning("MiNA TTS 超时，跳过兼容指令避免重复播放: %s", mina_error)
                return
            if not device.tts_command or not self.miio:
                raise
            log.warning("MiNA TTS 失败，尝试型号兼容指令: %s", mina_error)
        await miio_command(self.miio, device.did, f"{device.tts_command} {text}")

    async def play_url(self, device: EffectiveDeviceConfig, url: str) -> None:
        self._require_login()
        await self.mina.play_by_url(device.mina_device_id, url)

    def _require_login(self) -> None:
        if not self.authenticated or not self.mina or not self.miio:
            raise RuntimeError("米家账号未登录")
