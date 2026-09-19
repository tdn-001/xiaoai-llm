import json

from app.config import ConfigStore
from app.web.app import create_app


def make_client(tmp_path):
    from fastapi.testclient import TestClient

    store = ConfigStore(tmp_path / "config.json")
    store.load()
    app = create_app(store)
    return TestClient(app), store


def setup_admin(client, store):
    return client.post(
        "/api/auth/setup",
        json={"password": "secret123", "setup_token": store.value.web.bootstrap_token},
    )


def test_health_and_setup_login_flow(tmp_path):
    client, store = make_client(tmp_path)

    assert client.get("/health").json()["status"] == "ok"

    # Protected APIs require password setup first (428), then login (401).
    assert client.get("/api/config").status_code == 428
    assert client.post("/api/auth/login", json={"password": "secret123"}).status_code == 428
    assert client.post("/api/auth/setup", json={"password": "secret123"}).status_code == 403

    response = setup_admin(client, store)
    assert response.status_code == 200

    assert client.get("/api/config").status_code == 200

    client.post("/api/auth/logout")
    assert client.get("/api/config").status_code == 401

    assert client.post("/api/auth/login", json={"password": "wrong-pass"}).status_code == 401
    assert client.post("/api/auth/login", json={"password": "secret123"}).status_code == 200
    assert client.get("/api/config").status_code == 200


def test_config_roundtrip_redacts_secrets(tmp_path):
    client, store = make_client(tmp_path)
    setup_admin(client, store)

    body = store.public_dict()
    body["llm_profiles"][0]["api_key"] = "sk-test"
    body["mijia"]["username"] = "user@example.com"
    body["mijia"]["password"] = "pw"
    response = client.put("/api/config", json=body)
    assert response.status_code == 200

    data = client.get("/api/config").json()
    assert data["llm_profiles"][0]["api_key"] == ""
    assert data["llm_profiles"][0]["api_key_configured"] is True
    assert data["mijia"]["password"] == ""
    assert store.value.llm_profiles[0].api_key == "sk-test"

    # Saving again with blank secrets must keep stored values.
    response = client.put("/api/config", json=data)
    assert response.status_code == 200
    assert store.value.llm_profiles[0].api_key == "sk-test"
    assert store.value.mijia.password == "pw"


def test_invalid_config_rejected(tmp_path):
    client, store = make_client(tmp_path)
    setup_admin(client, store)
    body = store.public_dict()
    body["defaults"]["wake_words"] = []
    response = client.post("/api/config/validate", json=body)
    assert response.status_code == 422


def test_logs_endpoints(tmp_path):
    client, _ = make_client(tmp_path)
    setup_admin(client, _)
    assert client.get("/api/logs").status_code == 200
    assert client.delete("/api/logs").json()["message"]


def test_status_endpoint(tmp_path):
    client, _ = make_client(tmp_path)
    setup_admin(client, _)
    status = client.get("/api/status").json()
    assert status["mijia_authenticated"] is False
    assert status["listener_running"] is False
    assert status["devices"] == []


def test_mijia_login_validates_before_network(tmp_path):
    client, _ = make_client(tmp_path)
    setup_admin(client, _)

    # passToken mode without credentials must fail fast with a clear message.
    response = client.post("/api/mijia/login", json={"login_type": "pass_token", "user_id": ""})
    assert response.status_code == 400
    assert "userId" in response.json()["detail"]

    # Password mode without credentials likewise.
    response = client.post("/api/mijia/login", json={"login_type": "password", "username": "user"})
    assert response.status_code == 400


def test_mijia_status_reports_login_type(tmp_path):
    from app.models import MijiaAccount

    client, store = make_client(tmp_path)
    setup_admin(client, store)
    status = client.get("/api/mijia/status").json()
    assert status["login_type"] == "password"

    config = store.value
    config.mijia_accounts = [
        MijiaAccount(id="a1", login_type="pass_token", user_id="987654321", pass_token="V3_SECRET")
    ]
    store.save(config)

    data = client.get("/api/config").json()
    acct = data["mijia_accounts"][0]
    assert acct["login_type"] == "pass_token"
    assert acct["user_id"] == "987654321"
    assert acct["pass_token"] == ""
    assert acct["pass_token_configured"] is True

    status = client.get("/api/mijia/status").json()
    assert status["login_type"] == "pass_token"
    assert "987654321" not in json.dumps(status)


def test_audio_rejects_path_traversal(tmp_path):
    client, _ = make_client(tmp_path)
    assert client.get("/audio/..%2Fconfig.json").status_code == 404
    assert client.get("/audio/whatever.txt").status_code == 404
