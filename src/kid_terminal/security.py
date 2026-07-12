import hashlib
import hmac
import secrets
from typing import Any

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db import get_db
from .models import Device


def new_token(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def token_hash(token: str) -> str:
    pepper = get_settings().token_pepper.encode()
    return hmac.new(pepper, token.encode(), hashlib.sha256).hexdigest()


def require_admin(x_admin_key: str = Header(default="")) -> None:
    expected = get_settings().admin_api_key
    if not expected or not hmac.compare_digest(x_admin_key, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid admin key")


async def authenticate_token(token: str, db: AsyncSession) -> Device | None:
    from datetime import UTC, datetime

    result = await db.execute(select(Device).where(Device.token_hash == token_hash(token)))
    device = result.scalar_one_or_none()
    current = datetime.now(UTC)
    if not device or device.revoked_at or device.token_expires_at.replace(tzinfo=UTC) <= current:
        return None
    return device


async def require_device(
    authorization: str = Header(default=""), db: AsyncSession = Depends(get_db)
) -> Device:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    device = await authenticate_token(authorization.removeprefix("Bearer "), db)
    if not device:
        raise HTTPException(status_code=401, detail="invalid or revoked device token")
    return device


SENSITIVE_KEYS = {"authorization", "api_key", "token", "password", "crash_stack", "content"}


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if key.lower() in SENSITIVE_KEYS else redact(val)
            for key, val in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value
