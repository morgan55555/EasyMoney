"""Security: argon2 password hashing, JWT create/decode, and the
``get_current_user`` dependency (AGENTS.md §2.11, §3.1).

Access tokens are stateless JWTs (15 min). Refresh tokens are JWTs (7 days)
whose ``jti`` is persisted in ``refresh_tokens`` so rotation (revoke old) and
logout (revoke presented) can be enforced.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db
from app.models.user import User

ALGORITHM = "HS256"

_ph = PasswordHasher()

# auto_error=False so a missing header yields None here (-> 401), not FastAPI's
# default 403.
bearer_scheme = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, password)
    except VerifyMismatchError:
        return False


def _now() -> datetime:
    return datetime.now(UTC)


def create_access_token(user_id: int) -> str:
    settings = get_settings()
    now = _now()
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": now + timedelta(minutes=settings.jwt_access_ttl_min),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def issue_refresh(user_id: int) -> tuple[str, str, datetime]:
    """Create a new refresh token; return ``(jwt, jti, expires_at)``."""
    settings = get_settings()
    now = _now()
    jti = uuid4().hex
    expires_at = now + timedelta(days=settings.jwt_refresh_ttl_days)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "type": "refresh",
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": expires_at,
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)
    return token, jti, expires_at


def decode_token(token: str) -> dict[str, Any]:
    """Decode and verify a JWT. Raises ``PyJWTError`` on any failure."""
    return jwt.decode(token, get_settings().jwt_secret, algorithms=[ALGORITHM])


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Resolve the authenticated user from the ``Authorization: Bearer`` header."""
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_token(credentials.credentials)
    except PyJWTError:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    if payload.get("type") != "access" or "sub" not in payload:
        raise HTTPException(status_code=401, detail="Invalid token type")
    try:
        user_id = int(payload["sub"])
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token") from None
    # Keep the sub inside BIGINT range: an out-of-range id would otherwise
    # surface as a database error (A-7 / X-1).
    if not 0 < user_id <= 2**63 - 1:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    return user
