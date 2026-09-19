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


def test_password_reset_requires_initialized(tmp_path):
    """未初始化密码时，重置请求应返回 428（仍要走 setup 流程）。"""
    client, _store = make_client(tmp_path)
    r = client.post("/api/auth/reset-password/request", json={})
    assert r.status_code == 428


def test_password_reset_full_flow(tmp_path):
    """完整重置流程：申请 → 服务端日志含令牌 → 用令牌 + 新密码重置 → 用新密码登录。"""
    import logging

    client, store = make_client(tmp_path)
    setup_admin(client, store)

    # 申请重置（无需登录），不返回明文令牌，只返回提示
    captured: list[str] = []

    class _CaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record.getMessage())

    handler = _CaptureHandler()
    sec_logger = logging.getLogger("xiaoai.security")
    sec_logger.addHandler(handler)
    sec_logger.setLevel(logging.WARNING)
    try:
        r = client.post("/api/auth/reset-password/request", json={})
        assert r.status_code == 200
        # 服务端日志里应出现 reset_token=xxx
        joined = "\n".join(captured)
        assert "reset_token=" in joined
        token = joined.split("reset_token=", 1)[1].split("（", 1)[0].strip()
        assert len(token) >= 16

        # 错误令牌应被拒绝
        bad = client.post(
            "/api/auth/reset-password/confirm",
            json={"token": "x" * 32, "new_password": "newsecret"},
        )
        assert bad.status_code == 403

        # 正确令牌 + 新密码 → 重置成功
        ok = client.post(
            "/api/auth/reset-password/confirm",
            json={"token": token, "new_password": "newsecret"},
        )
        assert ok.status_code == 200

        # 重置成功后旧密码失效
        client.post("/api/auth/logout")
        assert client.post("/api/auth/login", json={"password": "secret123"}).status_code == 401
        # 新密码可登录
        assert client.post("/api/auth/login", json={"password": "newsecret"}).status_code == 200

        # 重置令牌已清空，再次使用同一 token 应被拒
        assert client.post(
            "/api/auth/reset-password/confirm",
            json={"token": token, "new_password": "anotherpwd"},
        ).status_code == 400
    finally:
        sec_logger.removeHandler(handler)


def test_password_reset_token_in_config_is_redacted(tmp_path):
    """重置令牌不应出现在 /api/config 响应里。"""
    client, store = make_client(tmp_path)
    setup_admin(client, store)
    client.post("/api/auth/reset-password/request", json={})
    config = client.get("/api/config").json()
    assert config["web"].get("password_reset_token") == ""
    assert config["web"].get("password_reset_token_configured") is True


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
