"""Poll-source dispatch: userprofile (no UBus) vs UBus, and record claiming."""

import json
from types import SimpleNamespace

import pytest

from app.config import ConfigStore
from app.mijia.client import MijiaClient, fetch_latest_question
from app.models import DeviceOverride, resolve_device
from app.runtime import DeviceWorker, RuntimeManager
from tests.test_pipeline import FakeLLM, FakeMijia, FakeTTS, FakeWebSearch


def make_device(hardware="S12A", did="d1", mina_device_id="mina-1"):
    return SimpleNamespace(did=did, hardware=hardware, mina_device_id=mina_device_id)


def test_parse_userprofile_payload():
    payload = {
        "code": 0,
        "message": "Success",
        "data": json.dumps(
            {
                "records": [
                    {
                        "query": "AI助手 什么是黑洞",
                        "time": 1700000000000,
                        "requestId": "req-1",
                        "answers": [{"type": "TTS", "tts": {"text": "黑洞是一种天体"}}],
                    }
                ]
            }
        ),
    }
    record = MijiaClient._parse_userprofile(payload)
    assert record == {
        "id": "req-1",
        "time": 1700000000000,
        "query": "AI助手 什么是黑洞",
        "native_answer": "黑洞是一种天体",
    }


def test_parse_userprofile_empty_and_errors():
    assert MijiaClient._parse_userprofile({"code": 0, "data": "{}"}) is None
    with pytest.raises(RuntimeError):
        MijiaClient._parse_userprofile({"code": 1, "message": "auth error"})


def make_client(calls):
    client = MijiaClient("default")
    client.authenticated = True

    async def userprofile(device):
        calls.append(("userprofile", device.hardware))
        return {"id": "1", "query": "问AI 测试"}

    async def ubus(device):
        calls.append(("ubus", device.did))
        return None

    client.latest_question_userprofile = userprofile
    client.latest_question_ubus = ubus
    return client


async def test_userprofile_source_avoids_ubus():
    calls = []
    client = make_client(calls)
    await fetch_latest_question(client, make_device(), "userprofile", True)
    assert calls == [("userprofile", "S12A")]


async def test_userprofile_failure_falls_back_to_ubus():
    calls = []
    client = MijiaClient("default")
    client.authenticated = True

    async def broken(device):
        calls.append(("userprofile", device.hardware))
        raise RuntimeError("userprofile down")

    async def ubus(device):
        calls.append(("ubus", device.did))
        return None

    client.latest_question_userprofile = broken
    client.latest_question_ubus = ubus

    await fetch_latest_question(client, make_device(), "userprofile", True)
    assert calls == [("userprofile", "S12A"), ("ubus", "d1")]

    with pytest.raises(RuntimeError):
        await fetch_latest_question(client, make_device(), "userprofile", False)
    assert calls[-1] == ("userprofile", "S12A")


async def test_ubus_source_and_missing_hardware_use_ubus():
    calls = []
    client = make_client(calls)
    await fetch_latest_question(client, make_device(), "ubus", True)
    await fetch_latest_question(client, make_device(hardware=""), "userprofile", True)
    assert calls == [("ubus", "d1"), ("ubus", "d1")]


def test_claim_record_dedupes_same_hardware(tmp_path):
    manager = RuntimeManager(ConfigStore(tmp_path / "config.json"), audio_dir=tmp_path / "audio")
    assert manager.claim_record("S12A", "req-1") is True
    assert manager.claim_record("S12A", "req-1") is False
    assert manager.claim_record("S12A", "req-2") is True
    assert manager.claim_record("LX04", "req-1") is True
    assert manager.claim_record("", "req-1") is True  # no hardware: always claim


class NativeFakeMijia(FakeMijia):
    async def fetch_native_answer(self, device):
        self.calls.append(("native", device.did))
        return "原生回答内容"


class EmptyWebSearch:
    async def research(self, config, query):
        return ""


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self, content_type=None):
        return self._payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.captured = {}

    def get(self, url, cookies=None, headers=None, timeout=None):
        self.captured = {"url": url, "cookies": cookies, "headers": headers, "timeout": timeout}
        return FakeResponse(self.payload)


async def test_userprofile_uses_mina_device_cookie():
    client = MijiaClient("default")
    client.authenticated = True
    client.mina = SimpleNamespace()
    client.miio = SimpleNamespace()
    client.account = SimpleNamespace(
        token={"userId": "919341392", "deviceId": "PASSPORT_ID", "micoapi": ["sec", "svc-token"]}
    )
    client.session = FakeSession({"code": 0, "data": json.dumps({"records": []})})

    device = make_device()
    assert await client.latest_question_userprofile(device) is None

    captured = client.session.captured
    assert captured["cookies"]["deviceId"] == "mina-1"  # MiNA deviceID, not passport
    assert captured["cookies"]["userId"] == "919341392"
    assert captured["cookies"]["serviceToken"] == "svc-token"
    assert "S12A" in captured["url"]
    assert captured["headers"].get("Referer")


async def test_userprofile_requires_mina_device_id():
    client = MijiaClient("default")
    client.authenticated = True
    client.mina = SimpleNamespace()
    client.miio = SimpleNamespace()
    client.account = SimpleNamespace(
        token={"userId": "1", "deviceId": "X", "micoapi": ["sec", "svc"]}
    )
    with pytest.raises(RuntimeError, match="MiNA deviceID"):
        await client.latest_question_userprofile(make_device(mina_device_id=""))
    client.session = FakeSession({"code": 0, "data": "{}"})
    await client.latest_question_userprofile(make_device())  # device id present: OK


async def test_native_answer_fetch_disabled_by_default(tmp_path):
    from app.models import MijiaAccount

    store = ConfigStore(tmp_path / "config.json")
    store.load()
    config = store.value
    config.mijia_accounts = [MijiaAccount(id="default", login_type="pass_token", user_id="1", pass_token="t")]
    config.devices["did1"] = DeviceOverride(name="音箱", hardware="S12A", account_id="default", mina_device_id="mina-1")
    store.save(config)

    manager = RuntimeManager(store, audio_dir=tmp_path / "audio")
    mijia = NativeFakeMijia()
    manager.set_mijia_client("default", mijia)
    manager.llm, manager.tts, manager.web_search = FakeLLM("回答"), FakeTTS(), EmptyWebSearch()
    config = store.value
    device = resolve_device(config, "did1")

    worker = DeviceWorker(manager, "did1", "default")
    await worker.handle(config, device, "问AI 你好")
    assert ("native", "did1") not in mijia.calls

    config.mijia.fetch_native_answer = True
    await worker.handle(config, device, "问AI 你好")
    assert ("native", "did1") in mijia.calls
