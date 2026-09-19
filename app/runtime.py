from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

from app.config import ConfigStore
from app.mijia.client import MijiaClient, fetch_latest_question
from app.models import AppConfig, EffectiveDeviceConfig, LLMProfile, resolve_device
from app.services.llm import LLMClient
from app.services.memory import ConversationMemory
from app.services.tts import TTSService
from app.services.wake import match_wake_word
from app.services.web_search import (
    TIME_WORDS_RE,
    WebSearchService,
    inject_current_datetime,
    inject_web_context,
    needs_web_search,
)

logger = logging.getLogger("xiaoai.runtime")


class DeviceWorker:
    def __init__(self, manager: "RuntimeManager", did: str, account_id: str):
        self.manager = manager
        self.did = did
        self.account_id = account_id
        self.watermark: str | None = None
        self.lock = asyncio.Lock()
        self.task: asyncio.Task | None = None
        self.active_until = 0.0
        self.stats: dict[str, Any] = {
            "listening": False,
            "last_poll_ts": None,
            "last_question": None,
            "last_hit": None,
            "last_llm_ms": None,
            "last_llm_at": None,
            "last_error": None,
            "consecutive_errors": 0,
            "processed": 0,
            "poll_mode": "idle",
            "poll_interval_seconds": None,
            "account_id": account_id,
        }

    def start(self) -> None:
        self.task = asyncio.create_task(self.run(), name=f"listener-{self.did}")

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
        self.stats["listening"] = False

    async def run(self) -> None:
        self.stats["listening"] = True
        logger.info("开始监听设备 %s", self.did, extra={"device": self.did})
        while not self.manager.stopping:
            try:
                interval = await self.poll_once()
                self.stats["consecutive_errors"] = 0
                self.stats["last_error"] = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                interval = self.manager.backoff_interval(self.stats["consecutive_errors"])
                self.stats["consecutive_errors"] += 1
                self.stats["last_error"] = str(exc)
                if "未登录" in str(exc):
                    interval = 15
                    self.manager.needs_relogin = True
                    logger.warning("米家账号未登录，暂停监听直至重新登录: %s", exc, extra={"device": self.did})
                else:
                    logger.error("设备 %s 轮询失败: %s", self.did, exc, extra={"device": self.did})
            await asyncio.sleep(interval)

    async def poll_once(self) -> float:
        manager = self.manager
        config = manager.config_store.value
        device = resolve_device(config, self.did)
        client = manager.mijia_client(self.account_id)
        if client is None or not client.authenticated:
            self.stats["last_error"] = "账号未登录"
            return self.next_poll_interval(config)
        source = config.mijia.poll_source
        try:
            record = await fetch_latest_question(
                client, device, source, config.mijia.poll_fallback_to_ubus
            )
        except Exception as exc:
            if "未登录" in str(exc):
                self.manager.mark_needs_relogin(self.account_id)
            raise
        self.stats["last_poll_ts"] = time.time()
        if record is None:
            return self.next_poll_interval(config)
        record_id = str(record.get("id") or "")
        if self.watermark is None:
            # First poll after (re)start only establishes the watermark so that
            # historical questions are never answered out of context.
            self.watermark = record_id
            return self.next_poll_interval(config)
        if not record_id or record_id == self.watermark:
            return self.next_poll_interval(config)
        # userprofile records are per-hardware: with several bound speakers of
        # the same model (possibly across accounts), only the first worker to
        # claim a record processes it.
        claim_key = f"{self.account_id}:{device.hardware}"
        if source == "userprofile" and not self.manager.claim_record(
            claim_key, record_id
        ):
            logger.debug(
                "同型号记录已被其他设备处理，跳过: %s", record_id, extra={"device": self.did}
            )
            return self.next_poll_interval(config)
        self.watermark = record_id
        self.activate(config)
        query = str(record.get("query") or "").strip()
        self.stats["last_question"] = query
        if query:
            logger.info("获取到新问句: %s", query, extra={"device": self.did})
            await self.handle(config, device, query)
            self.activate(config)
        return self.next_poll_interval(config)

    def activate(self, config: AppConfig) -> None:
        self.active_until = time.monotonic() + config.mijia.active_window_seconds

    def next_poll_interval(self, config: AppConfig) -> float:
        active = time.monotonic() < self.active_until
        interval = (
            config.mijia.poll_interval_seconds
            if active
            else config.mijia.idle_poll_interval_seconds
        )
        self.stats["poll_mode"] = "active" if active else "idle"
        self.stats["poll_interval_seconds"] = interval
        self.stats["poll_source"] = config.mijia.poll_source
        return interval

    async def handle(self, config: AppConfig, device: EffectiveDeviceConfig, query: str) -> None:
        if self.lock.locked():
            logger.warning("设备 %s 正在处理上一条问题，丢弃新问句", self.did, extra={"device": self.did})
            return
        async with self.lock:
            match = match_wake_word(query, device.wake_words, device.wake_word_mode)
            if match is None:
                logger.debug("未命中唤醒词，交由小爱原生处理: %s", query, extra={"device": self.did})
                return
            word, question = match
            self.stats["last_hit"] = {"word": word, "query": query, "time": time.time()}
            logger.info("命中唤醒词“%s”: %s", word, query, extra={"device": self.did})

            # Best-effort mute: must happen before the LLM call; failure is logged
            # but never blocks the pipeline.
            client = self.manager.mijia_client(self.account_id)
            if client is None or not client.authenticated:
                raise RuntimeError("米家账号未登录")
            mute_started = time.perf_counter()
            try:
                await asyncio.wait_for(client.mute(device), timeout=5)
                logger.info("已暂停原生播放 (%.0fms)", (time.perf_counter() - mute_started) * 1000, extra={"device": self.did})
            except Exception as exc:
                logger.warning("暂停原生播放失败（继续大模型链路）: %s", exc, extra={"device": self.did})

            # Optional UBus read for logging the native reply; disabled by
            # default to keep UBus traffic minimal.
            if config.mijia.fetch_native_answer:
                try:
                    native = await client.fetch_native_answer(device)
                    if native:
                        logger.info(
                            "原生回答（可选读取）: %s", native[:200], extra={"device": self.did}
                        )
                except Exception as exc:
                    logger.warning("读取原生回答失败: %s", exc, extra={"device": self.did})

            if not question:
                await self._speak(config, device, "请在我名字后面加上问题再问我一次")
                return

            # Optional "thinking" prompt: played after mute, before the LLM
            # call so the user gets immediate feedback that the request was
            # received.  Empty string disables the prompt.
            thinking_msg = config.tts.thinking_message.strip()
            if thinking_msg:
                await self._speak(config, device, thinking_msg)

            try:
                answer = await self._answer(config, device, question)
            except Exception as exc:
                logger.exception("问答链路失败: %s", exc, extra={"device": self.did})
                self.stats["last_error"] = str(exc)
                answer = ""
            if not answer:
                answer = config.tts.error_message
            self.stats["processed"] += 1
            await self._speak(config, device, answer)

    async def _answer(self, config: AppConfig, device: EffectiveDeviceConfig, question: str) -> str:
        profile = self.manager.profile(config, device.llm_profile_id)
        search_text = ""
        if device.web_search_enabled and config.web_search.enabled and needs_web_search(question):
            started = time.perf_counter()
            try:
                search_text = await self.manager.web_search.research(config.web_search, question)
                logger.info(
                    "网络搜索完成 (%.0fms, %d 字)",
                    (time.perf_counter() - started) * 1000,
                    len(search_text),
                    extra={"device": self.did},
                )
            except Exception as exc:
                logger.warning("网络搜索失败，降级为普通回答: %s", exc, extra={"device": self.did})

        messages = self.manager.memory.build_messages(
            self.did,
            device.persona,
            question,
            device.context_enabled,
            device.session_timeout_minutes,
            device.max_context_tokens,
        )
        if TIME_WORDS_RE.search(question):
            messages = inject_current_datetime(messages)
        if search_text:
            messages = inject_web_context(messages, search_text)

        started = time.perf_counter()
        answer = (await self.manager.llm.complete(profile, messages)).strip()
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        self.stats["last_llm_ms"] = elapsed_ms
        self.stats["last_llm_at"] = time.time()
        logger.info("LLM 返回 %d 字，耗时 %dms", len(answer), elapsed_ms, extra={"device": self.did})
        if not answer:
            raise RuntimeError("大模型返回了空回答")
        self.manager.memory.remember(self.did, question, answer, device.context_enabled)
        return answer

    async def _speak(self, config: AppConfig, device: EffectiveDeviceConfig, text: str) -> None:
        client = self.manager.mijia_client(self.account_id)
        if client is None or not client.authenticated:
            logger.error("账号 %s 未登录，跳过 TTS 播报", self.account_id, extra={"device": self.did})
            self.stats["last_error"] = "TTS: 账号未登录"
            return
        try:
            await self.manager.tts.speak(config, device, text, client)
        except Exception as exc:
            logger.error("TTS 播报失败: %s", exc, extra={"device": self.did})
            self.stats["last_error"] = f"TTS: {exc}"


class RuntimeManager:
    def __init__(self, config_store: ConfigStore, audio_dir: str = "data/audio"):
        self.config_store = config_store
        self._mijia_clients: dict[str, MijiaClient] = {}
        self.memory = ConversationMemory()
        self.llm = LLMClient()
        self.web_search = WebSearchService()
        self.tts = TTSService(audio_dir=audio_dir)
        self.workers: dict[str, DeviceWorker] = {}
        self.stopping = False
        self._needs_relogin: set[str] = set()
        self._cleanup_task: asyncio.Task | None = None
        self._applied_account_ids: set[str] = set()
        # Per-account hardware -> last claimed record id; keeps same-model
        # speakers from double-processing shared userprofile records.
        self._claimed_records: dict[str, str] = {}

    def set_mijia_client(self, account_id: str, client: MijiaClient) -> None:
        """Test/install helper: replace the client for one account."""
        self._mijia_clients[account_id] = client

    @property
    def mijia(self) -> MijiaClient | None:
        """Back-compat shim: returns the first authenticated MijiaClient.

        Existing routes (mute test, tts test, audio test) still take a single
        MijiaClient. They need to be called with the device's owning client
        instead; use mijia_client(account_id) when you have a specific account.
        """
        for client in self._mijia_clients.values():
            if client.authenticated:
                return client
        return None

    def mijia_client(self, account_id: str) -> MijiaClient | None:
        return self._mijia_clients.get(account_id)

    @property
    def needs_relogin(self) -> bool:
        return bool(self._needs_relogin)

    @needs_relogin.setter
    def needs_relogin(self, value: bool) -> None:
        if value:
            self._needs_relogin = set(self._mijia_clients.keys())
        else:
            self._needs_relogin.clear()

    def mark_needs_relogin(self, account_id: str) -> None:
        self._needs_relogin.add(account_id)

    def claim_record(self, claim_key: str, record_id: str) -> bool:
        if not claim_key:
            return True
        if self._claimed_records.get(claim_key) == record_id:
            return False
        self._claimed_records[claim_key] = record_id
        return True

    @property
    def running(self) -> bool:
        return any(worker.task and not worker.task.done() for worker in self.workers.values())

    def profile(self, config: AppConfig, profile_id: str) -> LLMProfile:
        for candidate in config.llm_profiles:
            if candidate.id == profile_id:
                return candidate
        raise ValueError(f"LLM 预设 {profile_id} 不存在")

    async def start(self) -> None:
        self.stopping = False
        self._cleanup_task = asyncio.create_task(self._cleanup_loop(), name="audio-cleanup")
        await self.apply_config()

    async def stop(self) -> None:
        self.stopping = True
        if self._cleanup_task:
            self._cleanup_task.cancel()
            self._cleanup_task = None
        await self._stop_workers()
        for client in list(self._mijia_clients.values()):
            await client.close()
        self._mijia_clients.clear()

    async def _stop_workers(self) -> None:
        for worker in self.workers.values():
            await worker.stop()
        self.workers.clear()

    async def rebuild_workers(self) -> int:
        """Reconcile listener tasks with the saved bindings."""
        await self._stop_workers()
        config = self.config_store.value
        accounts_by_id = {acct.id: acct for acct in config.mijia_accounts}
        for did in config.mijia.selected_devices:
            device = config.devices.get(did)
            if not device or not device.enabled or not device.mina_device_id:
                continue
            account_id = device.account_id or next(
                (a.id for a in config.mijia_accounts if a.enabled and MijiaClient.account_has_credentials(a)),
                "",
            )
            if not account_id:
                logger.warning("设备 %s 未关联任何米家账号，跳过", did)
                continue
            client = self._mijia_clients.get(account_id)
            if not client or not client.authenticated:
                logger.warning("设备 %s 关联的米家账号 %s 未登录，跳过", did, account_id)
                continue
            worker = DeviceWorker(self, did, account_id)
            self.workers[did] = worker
            worker.start()
        return len(self.workers)

    async def apply_config(self) -> dict[str, Any]:
        """Hot-apply configuration: rebuild listeners and (re)login each account."""
        config = self.config_store.value
        await self._stop_workers()
        result: dict[str, Any] = {"mijia": {}, "devices": 0}

        # Build a fresh map of clients for the configured accounts, keeping
        # existing clients for accounts whose credentials did not change.
        desired_ids = {acct.id for acct in config.mijia_accounts}
        stale = [aid for aid in self._mijia_clients if aid not in desired_ids]
        for aid in stale:
            await self._mijia_clients[aid].close()
            self._mijia_clients.pop(aid, None)
        self._needs_relogin.difference_update(stale)

        for acct in config.mijia_accounts:
            existing = self._mijia_clients.get(acct.id)
            creds_changed = (
                existing is None
                or existing._last_credentials is None
                or existing._last_credentials.login_type != acct.login_type
                or existing._last_credentials.username != acct.username
                or existing._last_credentials.password != acct.password
                or existing._last_credentials.user_id != acct.user_id
                or existing._last_credentials.pass_token != acct.pass_token
                or existing._last_credentials.region != acct.region
            )
            if not MijiaClient.account_has_credentials(acct):
                if existing:
                    await existing.close()
                    self._mijia_clients.pop(acct.id, None)
                self._needs_relogin.discard(acct.id)
                result["mijia"][acct.id] = "no_credentials"
                continue
            if acct.enabled and (creds_changed or not existing or not existing.authenticated):
                client = existing or MijiaClient(acct.id)
                self._mijia_clients[acct.id] = client
                try:
                    await client.login(acct)
                    result["mijia"][acct.id] = "logged_in"
                    self._needs_relogin.discard(acct.id)
                except Exception as exc:
                    result["mijia"][acct.id] = f"login_failed: {exc}"
                    self._needs_relogin.add(acct.id)
                    logger.error("米家账号 %s 登录失败: %s", acct.name or acct.id, exc)
            elif not acct.enabled and existing:
                await existing.close()
                self._mijia_clients.pop(acct.id, None)
                result["mijia"][acct.id] = "disabled"
            elif existing and existing.authenticated:
                result["mijia"][acct.id] = "reused_token"

        result["devices"] = await self.rebuild_workers()

        # Log cross-account same-hardware warning
        if config.mijia.poll_source == "userprofile":
            grouped: dict[str, list[str]] = {}
            for did in config.mijia.selected_devices:
                dev = config.devices.get(did)
                if dev and dev.enabled and dev.hardware:
                    grouped.setdefault(f"{dev.account_id}:{dev.hardware}", []).append(did)
            shared = [k for k, dids in grouped.items() if len(dids) > 1]
            if shared:
                logger.warning(
                    "同账号下同型号设备 %s 共用 userprofile 对话记录，问句归属按记录去重处理",
                    ",".join(shared),
                )
        logger.info(
            "配置已应用：监听 %d 台设备，米家账号 %s",
            result["devices"],
            ", ".join(f"{k}={v}" for k, v in result["mijia"].items()),
        )
        return result

    def backoff_interval(self, consecutive_errors: int) -> float:
        config = self.config_store.value
        base = config.mijia.poll_interval_seconds
        backoff = min(base * (2 ** max(consecutive_errors, 0)), config.mijia.max_backoff_seconds)
        return backoff * random.uniform(0.8, 1.2)

    def device_status(self) -> list[dict[str, Any]]:
        config = self.config_store.value
        statuses = []
        for did in config.mijia.selected_devices:
            worker = self.workers.get(did)
            device = config.devices.get(did)
            if worker is None:
                statuses.append(
                    {
                        "did": did,
                        "listening": False,
                        "configured": bool(device),
                        "enabled": bool(device and device.enabled),
                        "account_id": device.account_id if device else "",
                    }
                )
            else:
                statuses.append({"did": did, "configured": True, "enabled": True, **worker.stats})
        return statuses

    async def status(self) -> dict[str, Any]:
        config = self.config_store.value
        return {
            "mijia_authenticated": any(c.authenticated for c in self._mijia_clients.values()),
            "needs_relogin": self.needs_relogin,
            "listener_running": self.running,
            "device_count": len(config.mijia.selected_devices),
            "devices": self.device_status(),
        }

    async def _cleanup_loop(self) -> None:
        while not self.stopping:
            await asyncio.sleep(600)
            try:
                self.tts.cleanup()
            except Exception as exc:
                logger.warning("音频清理失败: %s", exc)
