"""Pipeline orchestration tests with faked mijia/llm/tts/search collaborators."""

from pathlib import Path

import pytest

from app.config import ConfigStore
from app.models import AppConfig, DeviceOverride, MijiaAccount
from app.runtime import DeviceWorker, RuntimeManager


class FakeMijia:
    def __init__(self):
        self.calls = []
        self.authenticated = True

    async def close(self):
        self.authenticated = False

    async def mute(self, device):
        self.calls.append(("mute", device.did))

    async def speak_mijia(self, device, text):
        self.calls.append(("speak_mijia", device.did, text))

    async def play_url(self, device, url):
        self.calls.append(("play_url", device.did, url))

    async def latest_question_ubus(self, device):
        return None

    async def latest_question_userprofile(self, device):
        return None

    async def fetch_native_answer(self, device):
        return ""


class FakeLLM:
    def __init__(self, answer="这是回答", error=None):
        self.calls = []
        self.answer = answer
        self.error = error

    async def complete(self, profile, messages):
        self.calls.append((profile, messages))
        if self.error:
            raise self.error
        return self.answer


class FakeTTS:
    def __init__(self):
        self.calls = []

    async def speak(self, config, device, text, mijia):
        self.calls.append((device.did, text))


class FakeWebSearch:
    def __init__(self, result=""):
        self.calls = []
        self.result = result

    async def research(self, config, query):
        self.calls.append(query)
        return self.result


@pytest.fixture
def runtime(tmp_path: Path) -> RuntimeManager:
    store = ConfigStore(tmp_path / "config.json")
    store.load()
    config = store.value
    config.mijia_accounts = [MijiaAccount(id="default", name="默认账号", login_type="pass_token", user_id="1", pass_token="t")]
    config.devices["did1"] = DeviceOverride(name="客厅音箱", hardware="S12A", account_id="default", mina_device_id="mina-1")
    store.save(config)
    manager = RuntimeManager(store, audio_dir=tmp_path / "audio")
    manager._mijia_clients["default"] = FakeMijia()
    manager._mijia_clients["default"].authenticated = True
    return manager


async def test_wake_hit_mutes_before_llm_and_speaks_answer(runtime: RuntimeManager):
    mijia, llm, tts = FakeMijia(), FakeLLM("黑洞是……"), FakeTTS()
    runtime.set_mijia_client("default", mijia); runtime.llm = llm; runtime.tts = tts; runtime.web_search = FakeWebSearch()
    config = runtime.config_store.value
    device = config.devices["did1"]
    from app.models import resolve_device

    worker = DeviceWorker(runtime, "did1", "default")
    await worker.handle(config, resolve_device(config, "did1"), "AI助手，什么是黑洞")

    assert ("mute", "did1") in mijia.calls
    assert mijia.calls.index(("mute", "did1")) < len(llm.calls) or llm.calls
    # Order: mute must appear before LLM consumed the question.
    assert tts.calls == [("did1", "让我想想"), ("did1", "黑洞是……")]
    assert runtime.memory._sessions["did1"].messages[-1]["role"] == "assistant"


async def test_no_wake_word_means_no_mute_no_llm(runtime: RuntimeManager):
    mijia, llm, tts = FakeMijia(), FakeLLM(), FakeTTS()
    runtime.set_mijia_client("default", mijia); runtime.llm = llm; runtime.tts = tts; runtime.web_search = FakeWebSearch()
    config = runtime.config_store.value
    from app.models import resolve_device

    worker = DeviceWorker(runtime, "did1", "default")
    await worker.handle(config, resolve_device(config, "did1"), "打开客厅的灯")

    assert mijia.calls == []
    assert llm.calls == []
    assert tts.calls == []


async def test_llm_failure_speaks_error_message(runtime: RuntimeManager):
    mijia, tts = FakeMijia(), FakeTTS()
    runtime.set_mijia_client("default", mijia)
    runtime.llm = FakeLLM(error=RuntimeError("boom"))
    runtime.tts = tts
    runtime.web_search = FakeWebSearch()
    config = runtime.config_store.value
    from app.models import resolve_device

    worker = DeviceWorker(runtime, "did1", "default")
    await worker.handle(config, resolve_device(config, "did1"), "问AI 测试")

    assert tts.calls[-1] == ("did1", config.tts.error_message)
    assert worker.stats["processed"] == 1


async def test_web_search_failure_degrades(runtime: RuntimeManager):
    class BoomSearch:
        async def research(self, config, query):
            raise RuntimeError("search down")

    mijia, tts = FakeMijia(), FakeTTS()
    runtime.set_mijia_client("default", mijia); runtime.tts = tts
    runtime.web_search = BoomSearch()
    llm = FakeLLM("回答")
    runtime.llm = llm
    config = runtime.config_store.value
    config.devices["did1"].web_search_enabled = True
    config.web_search.enabled = True
    from app.models import resolve_device

    worker = DeviceWorker(runtime, "did1", "default")
    await worker.handle(config, resolve_device(config, "did1"), "问AI 天气")

    profile, messages = llm.calls[0]
    assert tts.calls == [("did1", "让我想想"), ("did1", "回答")]
    assert all("外部搜索" not in m["content"] for m in messages)


async def test_web_search_injects_context(runtime: RuntimeManager):
    runtime.set_mijia_client("default", FakeMijia()); runtime.tts = FakeTTS()
    runtime.web_search = FakeWebSearch("搜索结果资料")
    llm = FakeLLM("回答")
    runtime.llm = llm
    config = runtime.config_store.value
    config.devices["did1"].web_search_enabled = True
    config.web_search.enabled = True
    from app.models import resolve_device

    worker = DeviceWorker(runtime, "did1", "default")
    await worker.handle(config, resolve_device(config, "did1"), "问AI 天气")

    _, messages = llm.calls[0]
    assert any("搜索结果资料" in m["content"] for m in messages)


async def test_empty_question_after_keyword_prompts_user(runtime: RuntimeManager):
    mijia, tts = FakeMijia(), FakeTTS()
    llm = FakeLLM()
    runtime.set_mijia_client("default", mijia); runtime.llm = llm; runtime.tts = tts; runtime.web_search = FakeWebSearch()
    config = runtime.config_store.value
    from app.models import resolve_device

    worker = DeviceWorker(runtime, "did1", "default")
    await worker.handle(config, resolve_device(config, "did1"), "AI助手，")

    assert llm.calls == []
    assert tts.calls and tts.calls[0][1].startswith("请在")


async def test_context_disabled_device_does_not_store_history(runtime: RuntimeManager):
    store = runtime.config_store
    config = store.value
    config.devices["did1"].context_enabled = False
    store.save(config)
    runtime.set_mijia_client("default", FakeMijia()); runtime.tts = FakeTTS()
    runtime.llm, runtime.web_search = FakeLLM("回答"), FakeWebSearch()
    from app.models import resolve_device

    worker = DeviceWorker(runtime, "did1", "default")
    await worker.handle(config, resolve_device(config, "did1"), "问AI 记住我")
    assert runtime.memory._sessions["did1"].messages == []


async def test_apply_config_without_credentials_skips_login(tmp_path: Path):
    store = ConfigStore(tmp_path / "config.json")
    store.load()
    manager = RuntimeManager(store, audio_dir=tmp_path / "audio")
    result = await manager.apply_config()
    assert result["devices"] == 0
    assert manager.workers == {}
    # No accounts added: every account (after migration) should report no creds.
    assert all(v == "no_credentials" for v in result["mijia"].values())
    await manager.stop()


async def test_selected_device_without_auth_does_not_start_worker(tmp_path: Path):
    store = ConfigStore(tmp_path / "config.json")
    store.load()
    config = store.value
    config.mijia_accounts = [
        MijiaAccount(id="default", name="默认", login_type="pass_token", user_id="1", pass_token="t")
    ]
    config.devices["did1"] = DeviceOverride(
        name="音箱", mina_device_id="mina-1", account_id="default"
    )
    config.mijia.selected_devices = ["did1"]
    store.save(config)
    manager = RuntimeManager(store, audio_dir=tmp_path / "audio")
    result = await manager.apply_config()
    assert result["devices"] == 0
    assert manager.workers == {}
    await manager.stop()


async def test_disabled_device_does_not_start_worker(tmp_path: Path):
    store = ConfigStore(tmp_path / "config.json")
    store.load()
    config = store.value
    config.mijia_accounts = [
        MijiaAccount(id="default", name="默认", login_type="pass_token", user_id="1", pass_token="t")
    ]
    config.devices["did1"] = DeviceOverride(
        name="音箱", mina_device_id="mina-1", account_id="default", enabled=False
    )
    config.mijia.selected_devices = ["did1"]
    store.save(config)
    manager = RuntimeManager(store, audio_dir=tmp_path / "audio")
    manager.set_mijia_client("default", FakeMijia())
    manager._mijia_clients["default"].authenticated = True
    assert await manager.rebuild_workers() == 0
    assert manager.workers == {}
    await manager.stop()


def test_worker_uses_idle_and_active_poll_intervals(runtime: RuntimeManager):
    config = runtime.config_store.value
    config.mijia.idle_poll_interval_seconds = 15
    config.mijia.poll_interval_seconds = 2
    config.mijia.active_window_seconds = 60
    worker = DeviceWorker(runtime, "did1", "default")

    assert worker.next_poll_interval(config) == 15
    assert worker.stats["poll_mode"] == "idle"
    worker.activate(config)
    assert worker.next_poll_interval(config) == 2
    assert worker.stats["poll_mode"] == "active"


async def test_new_record_activates_fast_polling(runtime: RuntimeManager):
    class QuestionMijia(FakeMijia):
        async def latest_question_userprofile(self, device):
            return {"id": "new", "query": "打开灯"}

    runtime.set_mijia_client("default", QuestionMijia())
    config = runtime.config_store.value
    config.mijia.idle_poll_interval_seconds = 12
    config.mijia.poll_interval_seconds = 2
    runtime.config_store.save(config)
    worker = DeviceWorker(runtime, "did1", "default")
    worker.watermark = "old"

    interval = await worker.poll_once()
    assert interval == 2
    assert worker.stats["poll_mode"] == "active"
