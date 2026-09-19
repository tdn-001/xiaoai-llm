"""passToken login: token-file construction, validation and relogin triggers."""

import json

import pytest

from app.config import ConfigStore
from app.mijia.client import MijiaClient, StableMiAccount, build_pass_token_file
from app.runtime import RuntimeManager


def test_build_pass_token_file_format():
    token = build_pass_token_file("123456789", "V3_PASS_TOKEN", device_id="ABCDEF0123456789")
    assert token == {
        "deviceId": "ABCDEF0123456789",
        "userId": "123456789",
        "passToken": "V3_PASS_TOKEN",
    }
    generated = build_pass_token_file("1", "t")
    assert len(generated["deviceId"]) == 16 and generated["deviceId"].isupper()


async def test_sid_refresh_keeps_existing_pass_token_when_response_omits_it():
    account = StableMiAccount.__new__(StableMiAccount)
    account.token = {
        "deviceId": "ABCDEF0123456789",
        "userId": "123",
        "passToken": "keep-me",
    }
    account.token_store = None
    account.username = ""
    account.password = ""

    async def service_login(uri, data=None):
        return {
            "code": 0,
            "userId": "123",
            "location": "https://example.invalid",
            "nonce": "nonce",
            "ssecurity": "security",
        }

    async def security_token(location, nonce, ssecurity):
        return "service-token"

    account._serviceLogin = service_login
    account._securityTokenService = security_token
    assert await account.login("xiaomiio") is True
    assert account.token["passToken"] == "keep-me"
    assert account.token["xiaomiio"] == ("security", "service-token")


async def test_login_pass_token_requires_fields(tmp_path):
    from app.models import MijiaAccount

    client = MijiaClient("default")
    acct = MijiaAccount(id="default", login_type="pass_token", user_id="", pass_token="")
    with pytest.raises(ValueError, match="请先填写"):
        await client.login(acct)
    assert not client.authenticated


async def test_login_pass_token_writes_token_file(tmp_path, monkeypatch):
    """Happy path: token file written before MiAccount loads it; network stubbed."""
    from app.models import MijiaAccount

    client = MijiaClient("default")
    token_path = tmp_path / "mi-token.json"
    acct = MijiaAccount(
        id="default",
        login_type="pass_token",
        user_id="987654321",
        pass_token="V3_SECRET",
        token_path=str(token_path),
    )

    class FakeAccount:
        def __init__(self, session, username, password, token_store):
            self.token_store = token_store
            # MiAccount reads the token file at construction time.
            self.loaded = json.load(open(token_store)) if token_store else None
            self.token = self.loaded

        async def login(self, sid):
            return bool(self.loaded and self.loaded.get("passToken"))

    import miservice

    monkeypatch.setattr(miservice, "MiAccount", FakeAccount, raising=False)
    import app.mijia.client as client_mod

    monkeypatch.setattr(client_mod, "StableMiAccount", FakeAccount)
    await client.login(acct)

    data = json.loads(token_path.read_text())
    assert data["userId"] == "987654321"
    assert data["passToken"] == "V3_SECRET"
    assert len(data["deviceId"]) == 16
    assert client.authenticated
    await client.close()
    # Token file must be permission-restricted.
    assert oct(token_path.stat().st_mode)[-3:] == "600"


class FakeClient:
    def __init__(self):
        self.login_calls = []
        self.authenticated = False
        self._last_credentials = None

    async def login(self, credentials):
        self.login_calls.append(credentials.model_copy())
        self.authenticated = True
        self._last_credentials = credentials.model_copy()

    async def close(self):
        self.authenticated = False
        self._last_credentials = None


async def test_apply_config_relogs_on_pass_token_change(tmp_path):
    from app.models import MijiaAccount

    store = ConfigStore(tmp_path / "config.json")
    store.load()
    manager = RuntimeManager(store, audio_dir=tmp_path / "audio")

    # No credentials: no login attempt.
    await manager.apply_config()
    assert manager._mijia_clients == {}

    config = store.value
    config.mijia_accounts = [
        MijiaAccount(id="a1", login_type="pass_token", user_id="1", pass_token="t1")
    ]
    store.save(config)
    fake = FakeClient()
    manager._mijia_clients["a1"] = fake

    await manager.apply_config()
    assert len(fake.login_calls) == 1

    # Unchanged credentials: token reuse, no new login.
    await manager.apply_config()
    assert len(fake.login_calls) == 1

    # Changed passToken triggers relogin.
    config.mijia_accounts[0].pass_token = "t2"
    store.save(config)
    await manager.apply_config()
    assert len(fake.login_calls) == 2
    assert fake.login_calls[-1].pass_token == "t2"
    await manager.stop()


async def test_password_mode_still_validated(tmp_path):
    from app.models import MijiaAccount

    client = MijiaClient("default")
    acct = MijiaAccount(id="default", login_type="password", username="user", password="")
    with pytest.raises(ValueError, match="请先填写"):
        await client.login(acct)
