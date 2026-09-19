import json

import pytest

from app.config import ConfigStore, merge_secret_fields
from app.models import AppConfig, DeviceOverride, LLMProfile


def test_defaults_are_valid():
    config = AppConfig()
    assert config.web.port == 33003
    assert config.defaults.wake_words
    assert config.llm_profiles[0].id == "default"
    assert config.web_search.search_url.startswith("https://")


def test_invalid_profile_reference_rejected():
    with pytest.raises(ValueError):
        AppConfig(defaults={"llm_profile_id": "missing"})


def test_duplicate_profile_ids_rejected():
    with pytest.raises(ValueError):
        AppConfig(
            llm_profiles=[
                LLMProfile(id="a", name="A"),
                LLMProfile(id="a", name="B"),
            ]
        )


def test_device_override_merges_with_defaults():
    config = AppConfig()
    config.devices["123"] = DeviceOverride(tts_provider="edge_tts", wake_words=["测试"])
    from app.models import resolve_device

    effective = resolve_device(config, "123")
    assert effective.tts_provider == "edge_tts"
    assert effective.wake_words == ["测试"]
    assert effective.persona == config.defaults.persona
    assert effective.llm_profile_id == "default"
    assert effective.web_search_enabled is False


def test_tts_and_compatibility_defaults_resolve_to_device():
    config = AppConfig()
    config.tts.default_provider = "edge_tts"
    config.devices["123"] = DeviceOverride(compatibility_key="s12a")
    from app.models import resolve_device

    effective = resolve_device(config, "123")
    assert effective.tts_provider == "edge_tts"
    assert effective.tts_command == "5-1"
    assert effective.wake_command == "5-5"


def test_save_load_roundtrip_and_permissions(tmp_path):
    path = tmp_path / "config.json"
    store = ConfigStore(path)
    store.load()
    config = store.value
    config.web.password_hash = "hash"
    config.llm_profiles[0].api_key = "sk-secret"
    store.save(config)

    assert (oct(path.stat().st_mode)[-3:]) == "600"
    loaded = ConfigStore(path).load()
    assert loaded.llm_profiles[0].api_key == "sk-secret"
    assert loaded.web.password_hash == "hash"


def test_public_dict_redacts_secrets(tmp_path):
    store = ConfigStore(tmp_path / "config.json")
    store.load()
    config = store.value
    config.llm_profiles[0].api_key = "sk-secret"
    config.mijia.password = "pw"
    store.save(config)

    data = store.public_dict()
    assert data["llm_profiles"][0]["api_key"] == ""
    assert data["llm_profiles"][0]["api_key_configured"] is True
    assert data["mijia"]["password"] == ""
    assert data["mijia"]["password_configured"] is True


def test_merge_secret_fields_keeps_existing_when_blank():
    current = {
        "mijia": {"password": "pw"},
        "llm_profiles": [{"id": "default", "api_key": "sk-1"}],
    }
    incoming = {
        "mijia": {"password": ""},
        "llm_profiles": [{"id": "default", "api_key": ""}],
    }
    merged = merge_secret_fields(incoming, current)
    assert merged["mijia"]["password"] == "pw"
    assert merged["llm_profiles"][0]["api_key"] == "sk-1"


def test_legacy_mcp_search_config_migrates(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "mcp": {"enabled": True, "timeout_seconds": 30, "max_result_chars": 9000},
                "llm_profiles": [
                    {"id": "default", "name": "默认模型", "enable_mcp_search": True}
                ],
            }
        ),
        "utf-8",
    )
    config = ConfigStore(path).load()
    assert config.web_search.enabled is True
    assert config.web_search.timeout_seconds == 30
    assert config.web_search.max_result_chars == 9000
    assert config.defaults.web_search_enabled is True


def test_merge_secret_fields_allows_new_values():
    current = {"llm_profiles": [{"id": "default", "api_key": ""}]}
    merged = merge_secret_fields({"llm_profiles": [{"id": "default", "api_key": "sk-new"}]}, current)
    assert merged["llm_profiles"][0]["api_key"] == "sk-new"


def test_atomic_save_keeps_old_file_on_failure(tmp_path):
    path = tmp_path / "config.json"
    store = ConfigStore(path)
    store.load()
    original = json.loads(path.read_text())

    class Broken:
        class web:
            session_secret = "keep"

        def model_dump_json(self, indent):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        store.save(Broken())
    assert json.loads(path.read_text()) == original
