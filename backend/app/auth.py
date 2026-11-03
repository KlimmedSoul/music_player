"""Мини-JWT (HS256) без внешних зависимостей + проверка пароля."""
import base64
import hashlib
import hmac
import json
import time

from fastapi import Depends, HTTPException, Request

from .config import settings


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def create_token(sub: str = "owner") -> str:
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64(
        json.dumps({"sub": sub, "exp": int(time.time()) + settings.token_ttl_s}).encode()
    )
    signing = f"{header}.{payload}".encode()
    sig = _b64(hmac.new(settings.jwt_secret.encode(), signing, hashlib.sha256).digest())
    return f"{header}.{payload}.{sig}"


def verify_token(token: str) -> dict | None:
    try:
        header, payload, sig = token.split(".")
        signing = f"{header}.{payload}".encode()
        expect = _b64(hmac.new(settings.jwt_secret.encode(), signing, hashlib.sha256).digest())
        if not hmac.compare_digest(expect, sig):
            return None
        data = json.loads(base64.urlsafe_b64decode(payload + "=="))
        return data if data.get("exp", 0) > time.time() else None
    except Exception:
        return None


async def current_user(request: Request) -> dict:
    auth = request.headers.get("Authorization", "")
    token = auth[7:] if auth.lower().startswith("bearer ") else ""
    data = verify_token(token)
    if not data:
        raise HTTPException(401, "unauthorized")
    return data


auth_required = Depends(current_user)
