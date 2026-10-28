"""Account service: register, login, refresh rotation, logout (§2.11).

Emails are normalized to lowercase before persisting, so the plain unique
column on ``users.email`` enforces case-insensitive uniqueness. Services raise
domain errors; the API layer maps them to HTTP statuses.
"""

from __future__ import annotations

from datetime import UTC, datetime

from jwt import PyJWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, Unauthorized, ValidationFailed
from app.core.security import (
    create_access_token,
    decode_token,
    hash_password,
    issue_refresh,
    verify_password,
)
from app.models.token import RefreshToken
from app.models.user import User


def _norm_email(email: str) -> str:
    return email.strip().lower()


def _valid_email(email: str) -> bool:
    local, _, domain = email.partition("@")
    return bool(local) and bool(domain) and "." in domain


async def register(db: AsyncSession, email: str, password: str, display_name: str | None) -> User:
    email_norm = _norm_email(email)
    if not _valid_email(email_norm):
        raise ValidationFailed("A valid email address is required")
    if len(password) < 8:
        raise ValidationFailed("Password must be at least 8 characters")
    existing = await db.scalar(select(User).where(User.email == email_norm))
    if existing is not None:
        raise Conflict("An account with this email already exists")
    name = (display_name or "").strip() or email_norm.partition("@")[0]
    user = User(
        email=email_norm,
        password_hash=hash_password(password),
        display_name=name[:120],
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def login(db: AsyncSession, email: str, password: str) -> User:
    email_norm = _norm_email(email)
    user = await db.scalar(select(User).where(User.email == email_norm))
    if user is None or not verify_password(password, user.password_hash):
        raise Unauthorized("Invalid email or password")
    return user


async def issue_tokens(db: AsyncSession, user: User) -> tuple[str, str]:
    """Create the ``(access, refresh)`` pair for ``user`` and persist its jti."""
    access = create_access_token(user.id)
    refresh_jwt, jti, expires_at = issue_refresh(user.id)
    db.add(RefreshToken(user_id=user.id, jti=jti, expires_at=expires_at))
    await db.commit()
    return access, refresh_jwt


async def refresh(db: AsyncSession, token: str) -> tuple[str, str]:
    """Rotate a refresh token: revoke the old jti, issue a fresh pair (§2.11)."""
    try:
        payload = decode_token(token)
    except PyJWTError:
        raise Unauthorized("Invalid or expired refresh token") from None
    if payload.get("type") != "refresh" or not payload.get("jti"):
        raise Unauthorized("Not a valid refresh token")
    row = await db.scalar(select(RefreshToken).where(RefreshToken.jti == payload["jti"]))
    if row is None or row.revoked_at is not None:
        raise Unauthorized("Refresh token has been revoked")
    user = await db.get(User, int(payload["sub"]))
    if user is None:
        raise Unauthorized("User not found")
    row.revoked_at = datetime.now(UTC)
    return await issue_tokens(db, user)


async def logout(db: AsyncSession, token: str) -> None:
    """Revoke the presented refresh token.

    Idempotent on purpose: an already-revoked, expired or unknown token is a
    no-op — the caller has already lost it, and logout must not fail.
    """
    try:
        payload = decode_token(token)
    except PyJWTError:
        return
    if payload.get("type") != "refresh" or not payload.get("jti"):
        return
    row = await db.scalar(select(RefreshToken).where(RefreshToken.jti == payload["jti"]))
    if row is not None and row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        await db.commit()


async def update_display_name(db: AsyncSession, user: User, display_name: str) -> User:
    name = display_name.strip()
    if not name:
        raise ValidationFailed("Display name must not be empty")
    user.display_name = name[:120]
    await db.commit()
    await db.refresh(user)
    return user
