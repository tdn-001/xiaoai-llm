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

USERPROFILE_TIMEOUT_SECONDS = 5
USERPROFILE_UA = (
    "Mozilla/5.0 (Linux; Android 10; 000; wv) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Version/4.0 Chrome/119.0.6045.193 Mobile Safari/537.36 /XiaoMi/HybridView/ "
    "micoSoundboxApp/i appVersion/A_2.4.40"
)
USERPROFILE_REFERER = "https://userprofile.mina.mi.com/dialogue-note/index.html"

_CODE_CAPTCHA_REQUIRED = 87001
_CODE_LOGIN_VERIFY = 70016


def build_pass_token_file(user_id: str, pass_token: str, device_id: str | None = None) -> dict[str, str]:
    return {
        "deviceId": device_id or "".join(random.sample(string.ascii_letters + string.digits, 16)).upper(),
        "userId": str(user_id),
        "passToken": pass_token,
    }


def _detect_verification_type(response: dict[str, Any]) -> str | None:
    code = response.get("code", 0)
    captcha_url = response.get("captchaUrl") or ""
    notification_url = response.get("notificationUrl") or ""
    if code == _CODE_CAPTCHA_REQUIRED or captcha_url:
        return "captcha"
    if code == _CODE_LOGIN_VERIFY:
        return "sms"
    if notification_url:
        return "sms"
    return None


class LoginVerificationRequired(Exception):
    def __init__(
        self,
        *,
        ver_type: str,
        captcha_url: str = "",
        notification_url: str = "",
        sid: str = "",
        response: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"需要验证码: {ver_type}")
        self.ver_type = ver_type
        self.captcha_url = captcha_url
        self.notification_url = notification_url
        self.sid = sid
        self.response = response or {}


class StableMiAccount(MiAccount):
    """MiAccount with non-destructive SID refresh and verification support."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._pending_sid: str = ""
        self._pending_login_data: dict[str, Any] = {}
        self._pending_response: dict[str, Any] = {}

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
                # Check for verification even when code == 0 — Xiaomi may
                # return code:0 with notificationUrl + empty location.
                ver_type = _detect_verification_type(response)
                if ver_type or (not response.get("location") and response.get("notificationUrl")):
                    self._pending_sid = sid
                    self._pending_login_data = data
                    self._pending_response = response
                    raise LoginVerificationRequired(
                        ver_type=ver_type or "sms",
                        captcha_url=response.get("captchaUrl") or "",
                        notification_url=response.get("notificationUrl") or "",
                        sid=sid,
                        response=response,
                    )
                if response["code"] != 0:
                    raise RuntimeError(
                        f"小米登录失败: {response.get('description', '未知错误')}"
                    )

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
        except LoginVerificationRequired:
            raise
        except Exception as exc:
            self.token.pop(sid, None)
            log.warning("小米服务 %s 授权失败: %s", sid, exc)
            return False

    async def submit_captcha(self, code: str) -> bool:
        sid = self._pending_sid
        data = self._pending_login_data.copy()
        if not sid or not data:
            raise RuntimeError("没有待处理的验证码会话，请重新发起登录")
        data["captCode"] = code
        try:
            response = await self._serviceLogin("serviceLoginAuth2", data)
            self._pending_sid = ""
            self._pending_login_data = {}
            self._pending_response = {}
            if response["code"] != 0:
                ver_type = _detect_verification_type(response)
                if ver_type:
                    self._pending_sid = sid
                    self._pending_login_data = data
                    self._pending_response = response
                    raise LoginVerificationRequired(
                        ver_type=ver_type,
                        captcha_url=response.get("captchaUrl") or "",
                        notification_url=response.get("notificationUrl") or "",
                        sid=sid,
                        response=response,
                    )
                raise RuntimeError(
                    f"验证码提交失败: {response.get('description', '未知错误')}"
                )
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
        except LoginVerificationRequired:
            raise
        except Exception as exc:
            self.token.pop(sid, None)
            log.warning("验证码登录失败: %s", exc)
            return False

    async def submit_notification(self) -> bool:
        sid = self._pending_sid
        data = self._pending_login_data.copy()
        if not sid or not data:
            raise RuntimeError("没有待处理的验证会话，请重新发起登录")
        try:
            response = await self._serviceLogin("serviceLoginAuth2", data)
            self._pending_sid = ""
            self._pending_login_data = {}
            self._pending_response = {}
            if response["code"] != 0:
                raise RuntimeError(
                    f"验证登录失败: {response.get('description', '未知错误')}"
                )
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
            self.token.pop(sid, None)
            log.warning("通知验证登录失败: %s", exc)
            return False

    async def fetch_captcha_image(self, captcha_url: str) -> bytes:
        if captcha_url.startswith("/"):
            captcha_url = "https://account.xiaomi.com" + captcha_url
        headers = {"User-Agent": self.now_ua}
        cookies: dict[str, str] = {"sdkVersion": "3.9", "deviceId": self.token["deviceId"]}
        if "passToken" in self.token:
            cookies["userId"] = self.token.get("userId", "")
            cookies["passToken"] = self.token.get("passToken", "")
        async with self.session.get(captcha_url, headers=headers, cookies=cookies, ssl=False) as r:
            return await r.read()


async def fetch_latest_question(
    client: "MijiaClient",
    device: EffectiveDeviceConfig,
    source: str = "ubus",
    fallback: bool = True,
) -> dict[str, Any] | None:
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
        self._pending_verification: dict[str, Any] | None = None

    @staticmethod
    def account_has_credentials(acct: MijiaAccount) -> bool:
        if acct.login_type == "pass_token":
            return bool(acct.user_id and acct.pass_token)
        return bool(acct.username and acct.password)

    async def login(self, credentials: MijiaAccount) -> MijiaAccount:
        await self.close()
        if not self.account_has_credentials(credentials):
            raise ValueError("请先填写小米账号和密码或 passToken")
        token_path = (
            Path(credentials.token_path) if credentials.token_path
            else Path(f"data/mi-token-{credentials.id}.json")
        )
        token_path.parent.mkdir(parents=True, exist_ok=True)
        self.session = ClientSession(timeout=ClientTimeout(total=30))
        if credentials.login_type == "pass_token":
            token_file = build_pass_token_file(credentials.user_id, credentials.pass_token)
            token_path.write_text(json.dumps(token_file, indent=2), "utf-8")
            os.chmod(token_path, 0o600)
            self.account = StableMiAccount(
                self.session, credentials.username, credentials.password, str(token_path)
            )
        else:
            self.account = StableMiAccount(
                self.session, credentials.username, credentials.password, str(token_path)
            )

        try:
            await self.account.login("micoapi")
        except LoginVerificationRequired as exc:
            self._pending_verification = {
                "ver_type": exc.ver_type,
                "captcha_url": exc.captcha_url,
                "notification_url": exc.notification_url,
                "sid": exc.sid,
            }
            raise

        if credentials.login_type == "pass_token":
            token = getattr(self.account, "token", None)
            refreshed = token.get("passToken") if token else None
            if refreshed:
                credentials.pass_token = refreshed
        self.mina = MiNAService(self.account)
        self.miio = MiIOService(self.account, credentials.region)
        self.authenticated = True
        self._last_credentials = credentials.model_copy()
        self._pending_verification = None
        log.info(
            "米家账号 %s 登录成功（%s）",
            credentials.name or credentials.username or credentials.user_id or credentials.id,
            "passToken" if credentials.login_type == "pass_token" else "密码",
        )
        return credentials

    async def submit_captcha(self, code: str) -> MijiaAccount:
        if not self.account or not isinstance(self.account, StableMiAccount):
            raise RuntimeError("没有待处理的登录会话，请重新发起登录")
        try:
            success = await self.account.submit_captcha(code)
        except LoginVerificationRequired as exc:
            self._pending_verification = {
                "ver_type": exc.ver_type,
                "captcha_url": exc.captcha_url,
                "notification_url": exc.notification_url,
                "sid": exc.sid,
            }
            raise
        if not success:
            raise RuntimeError("验证码验证失败，请检查验证码是否正确")
        creds = self._last_credentials
        if creds and creds.login_type == "pass_token":
            token = getattr(self.account, "token", None)
            refreshed = token.get("passToken") if token else None
            if refreshed:
                creds.pass_token = refreshed
        self.mina = MiNAService(self.account)
        self.miio = MiIOService(self.account, creds.region if creds else "cn")
        self.authenticated = True
        self._pending_verification = None
        log.info("米家账号验证码登录成功")
        return creds

    async def submit_notification(self) -> MijiaAccount:
        if not self.account or not isinstance(self.account, StableMiAccount):
            raise RuntimeError("没有待处理的登录会话，请重新发起登录")
        success = await self.account.submit_notification()
        if not success:
            raise RuntimeError("通知验证登录失败：请确保已在浏览器中完成验证后再试")
        creds = self._last_credentials
        if creds and creds.login_type == "pass_token":
            token = getattr(self.account, "token", None)
            refreshed = token.get("passToken") if token else None
            if refreshed:
                creds.pass_token = refreshed
        self.mina = MiNAService(self.account)
        self.miio = MiIOService(self.account, creds.region if creds else "cn")
        self.authenticated = True
        self._pending_verification = None
        log.info("米家账号通知验证登录成功")
        return creds

    async def fetch_captcha_image(self) -> bytes:
        if not self.account or not isinstance(self.account, StableMiAccount):
            raise RuntimeError("没有待处理的验证码会话")
        ver = self._pending_verification
        if not ver or not ver.get("captcha_url"):
            raise RuntimeError("当前不需要验证码")
        return await self.account.fetch_captcha_image(ver["captcha_url"])

    async def auto_reconnect(self, credentials: MijiaAccount | None = None) -> bool:
        creds = credentials or self._last_credentials
        if not creds:
            return False
        log.info("米家账号 %s 自动重新登录...", creds.name or creds.username or creds.id)
        try:
            await self.login(creds)
            return True
        except LoginVerificationRequired:
            log.warning("米家账号自动重连需要验证码，无法自动完成")
            return False
        except Exception as exc:
            log.warning("米家账号自动重连失败: %s", exc)
            return False

    async def close(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()
        self.session = None
        self.account = None
        self.mina = None
        self.miio = None
        self.authenticated = False
        self._last_credentials = None
        self._pending_verification = None

    async def discover_devices(self) -> list[dict[str, Any]]:
        self._require_login()
        mina_devices = await self.mina.device_list() or []
        miio_devices = []
        try:
            miio_devices = await self.miio.device_list(name="full") or []
        except Exception as exc:
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
