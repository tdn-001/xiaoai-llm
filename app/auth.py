from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from fastapi import HTTPException, Request, Response, status


COOKIE_NAME = "xiaoai_session"


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1).hex()
    return f"scrypt${salt}${digest}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, salt, expected = encoded.split("$", 2)
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def make_session(secret: str, lifetime_seconds: int = 86400) -> str:
    expires = str(int(time.time()) + lifetime_seconds)
    nonce = secrets.token_urlsafe(12)
    payload = f"{expires}.{nonce}"
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def verify_session(token: str, secret: str) -> bool:
    try:
        expires, nonce, signature = token.split(".", 2)
        payload = f"{expires}.{nonce}"
        expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return int(expires) >= int(time.time()) and hmac.compare_digest(signature, expected)
    except (ValueError, TypeError):
        return False


def set_session_cookie(response: Response, secret: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        make_session(secret),
        httponly=True,
        samesite="strict",
        secure=False,
        max_age=86400,
    )


def require_auth(request: Request) -> None:
    config = request.app.state.config_store.value
    if not config.web.password_hash:
        raise HTTPException(status.HTTP_428_PRECONDITION_REQUIRED, "请先设置管理密码")
    if not verify_session(request.cookies.get(COOKIE_NAME, ""), config.web.session_secret):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "请先登录")
