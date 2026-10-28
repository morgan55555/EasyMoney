"""Account service: register, login, refresh rotation, logout, email
confirmation, and email change (AGENTS.md §2.11, §2.13).

Emails are normalized to lowercase before persisting, so the plain unique
column on ``users.email`` enforces case-insensitive uniqueness. Services raise
domain errors; the API layer maps them to HTTP statuses.
"""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, timedelta

from jwt import PyJWTError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import Conflict, Forbidden, NotFound, Unauthorized, ValidationFailed
from app.core.security import (
    create_access_token,
    decode_token,
    hash_password,
    issue_refresh,
    verify_password,
)
from app.models.email_token import (
    PURPOSE_CHANGE,
    PURPOSE_PASSWORD,
    PURPOSE_REGISTER,
    STATUS_PENDING,
    STATUS_REVOKED,
    STATUS_USED,
    EmailToken,
)
from app.models.token import RefreshToken
from app.models.user import User
from app.services import mail as mail_service

logger = logging.getLogger(__name__)


def _norm_email(email: str) -> str:
    return email.strip().lower()


def _valid_email(email: str) -> bool:
    local, _, domain = email.partition("@")
    return bool(local) and bool(domain) and "." in domain


async def register(
    db: AsyncSession,
    email: str,
    password: str,
    display_name: str | None,
    preferred_language: str | None = None,
) -> User:
    settings = get_settings()
    email_norm = _norm_email(email)
    if not _valid_email(email_norm):
        raise ValidationFailed("A valid email address is required")
    if len(password) < 8:
        raise ValidationFailed("Password must be at least 8 characters")
    if preferred_language is not None and preferred_language not in ("en", "ru"):
        raise ValidationFailed("preferred_language must be 'en' or 'ru'")

    # Registration restriction (§2.13): when closed, only the first user may
    # register (while the users table is empty).
    if not settings.registration_open:
        user_count = await db.scalar(select(func.count()).select_from(User))
        if user_count and user_count > 0:
            raise Forbidden("Registration is closed")

    existing = await db.scalar(select(User).where(User.email == email_norm))
    if existing is not None:
        raise Conflict("An account with this email already exists")

    name = (display_name or "").strip() or email_norm.partition("@")[0]

    # First-ever user becomes admin (§2.13).
    user_count = await db.scalar(select(func.count()).select_from(User))
    is_first = not user_count or user_count == 0

    # Email verification: unverified only when register-confirmation is active.
    verified = not settings.register_confirmation_active

    user = User(
        email=email_norm,
        password_hash=hash_password(password),
        display_name=name[:120],
        is_admin=is_first,
        email_verified=verified,
        preferred_language=preferred_language or "en",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    # Issue and mail a register-confirmation token when active.
    if not verified:
        await _issue_confirmation_token(db, user, PURPOSE_REGISTER, user.email)
    return user


# ---------------------------------------------------------------------------
# Registration status (§2.13)
# ---------------------------------------------------------------------------


async def get_registration_status(db: AsyncSession) -> bool:
    """Whether the registration form should be shown.

    ``True`` only when ``REGISTRATION_OPEN`` is true **and** the users table
    is still empty (the first user can always register).
    """
    settings = get_settings()
    if not settings.registration_open:
        user_count = await db.scalar(select(func.count()).select_from(User))
        if user_count and user_count > 0:
            return False
    return True


# ---------------------------------------------------------------------------
# Email confirmation tokens (§2.13)
# ---------------------------------------------------------------------------


async def _issue_confirmation_token(
    db: AsyncSession,
    user: User,
    purpose: str,
    target_email: str,
    password_hash: str | None = None,
) -> EmailToken:
    """Create a pending token, revoke any previous pending one for the same
    purpose, and mail the confirmation link.

    The link is always mailed to the user's **current** email address: for
    ``register`` that is the address being verified, and for ``change`` it is
    the *previous* address (AGENTS.md §2.13, req 26) — the new address only
    becomes the account's email once the token is confirmed. ``password``
    tokens additionally carry ``password_hash`` (the pending new-password
    hash), applied to the user only on confirmation.
    """
    # Revoke any existing pending token for this user + purpose.
    previous = await db.scalar(
        select(EmailToken).where(
            EmailToken.user_id == user.id,
            EmailToken.purpose == purpose,
            EmailToken.status == STATUS_PENDING,
        )
    )
    if previous is not None:
        previous.status = STATUS_REVOKED

    token = EmailToken(
        user_id=user.id,
        purpose=purpose,
        token=secrets.token_urlsafe(16),
        target_email=target_email,
        status=STATUS_PENDING,
        expires_at=datetime.now(UTC) + timedelta(hours=24),
        password_hash=password_hash,
    )
    db.add(token)
    await db.commit()
    await db.refresh(token)

    # Mail the confirmation (best-effort: a mail failure must not block the
    # registration or any change flow).
    try:
        if purpose == PURPOSE_REGISTER:
            subject, text, html = mail_service.register_confirmation_message(
                user.display_name, token.token, lang=user.preferred_language
            )
        elif purpose == PURPOSE_PASSWORD:
            subject, text, html = mail_service.password_confirmation_message(
                user.display_name, token.token, lang=user.preferred_language
            )
        else:
            subject, text, html = mail_service.change_confirmation_message(
                user.display_name, target_email, token.token, lang=user.preferred_language
            )
        await mail_service.send_mail(user.email, subject, text, html)
    except Exception:
        logger.exception(
            "Failed to send confirmation email (user=%s, purpose=%s)", user.id, purpose
        )
    return token


async def confirm_email_token(db: AsyncSession, token_str: str) -> User:
    """Apply a confirmation token and mark it used.

    - ``register``: marks the user's ``email_verified = True``.
    - ``change``: swaps the user's email to the token's ``target_email``.
    - ``password``: applies the token's ``password_hash`` to the user.
    """
    row = await db.scalar(select(EmailToken).where(EmailToken.token == token_str))
    if row is None:
        raise NotFound("Confirmation token not found")
    if row.status != STATUS_PENDING:
        raise Conflict("Token has already been used or revoked")
    if row.expires_at < datetime.now(UTC):
        raise Conflict("Token has expired")

    user = await db.get(User, row.user_id)
    if user is None:
        raise NotFound("User not found")

    if row.purpose == PURPOSE_REGISTER:
        user.email_verified = True
    elif row.purpose == PURPOSE_CHANGE:
        # The target email must still be available.
        existing = await db.scalar(select(User).where(User.email == row.target_email))
        if existing is not None:
            raise Conflict("The target email is already in use")
        user.email = row.target_email
    elif row.purpose == PURPOSE_PASSWORD:
        if not row.password_hash:
            raise Conflict("Token carries no password change")
        user.password_hash = row.password_hash

    row.status = STATUS_USED
    await db.commit()
    await db.refresh(user)
    return user


async def resend_confirmation(
    db: AsyncSession, user: User, purpose: str
) -> None:
    """Reissue and resend a confirmation token for the user.

    For ``register``: re-mails to the user's current email.
    For ``change``: re-mails to the **previous** (current) email with the
    pending token's target.
    """
    if purpose == PURPOSE_REGISTER:
        await _issue_confirmation_token(db, user, PURPOSE_REGISTER, user.email)
    elif purpose == PURPOSE_CHANGE:
        # Find the pending change token to get the target email.
        pending = await db.scalar(
            select(EmailToken).where(
                EmailToken.user_id == user.id,
                EmailToken.purpose == PURPOSE_CHANGE,
                EmailToken.status == STATUS_PENDING,
            )
        )
        if pending is None:
            raise NotFound("No pending email-change request")
        await _issue_confirmation_token(db, user, PURPOSE_CHANGE, pending.target_email)
    else:
        raise ValidationFailed("Unknown purpose")


# ---------------------------------------------------------------------------
# Email change (§2.13)
# ---------------------------------------------------------------------------


async def change_email(db: AsyncSession, user: User, new_email: str) -> str:
    """Initiate an email change. Returns ``"changed"`` or ``"confirmation_sent"``.

    - When change-confirmation is active: creates a token, mails the previous
      address, and returns ``"confirmation_sent"`` (the new email is NOT yet
      applied).
    - When not active: applies immediately and returns ``"changed"``.
    """
    settings = get_settings()
    new_norm = _norm_email(new_email)
    if not _valid_email(new_norm):
        raise ValidationFailed("A valid email address is required")
    if new_norm == user.email:
        raise Conflict("You are already using this email address")
    existing = await db.scalar(select(User).where(User.email == new_norm))
    if existing is not None:
        raise Conflict("An account with this email already exists")

    if settings.email_change_confirmation_active:
        await _issue_confirmation_token(db, user, PURPOSE_CHANGE, new_norm)
        return "confirmation_sent"

    user.email = new_norm
    await db.commit()
    await db.refresh(user)
    return "changed"


async def change_password(db: AsyncSession, user: User, new_password: str) -> str:
    """Initiate a password change. Returns ``"changed"`` or ``"confirmation_sent"``.

    - When confirmation is active: creates a ``password`` token carrying the
      new-password hash, mails the user's **current** address, and returns
      ``"confirmation_sent"`` (the password is NOT yet changed).
    - When not active: applies the new hash immediately and returns
      ``"changed"``.
    """
    settings = get_settings()
    if len(new_password) < 8:
        raise ValidationFailed("Password must be at least 8 characters")
    if verify_password(new_password, user.password_hash):
        raise Conflict("You are already using this password")

    if settings.password_change_confirmation_active:
        await _issue_confirmation_token(
            db, user, PURPOSE_PASSWORD, user.email, password_hash=hash_password(new_password)
        )
        return "confirmation_sent"

    user.password_hash = hash_password(new_password)
    await db.commit()
    await db.refresh(user)
    return "changed"


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


async def update_profile(
    db: AsyncSession,
    user: User,
    display_name: str | None = None,
    preferred_language: str | None = None,
) -> User:
    """Update the profile fields a user may change themselves (§2.13).

    ``display_name`` and ``preferred_language`` are applied independently;
    ``None`` leaves the field untouched. Email and password changes go through
    the dedicated flows (change_email / admin), never here.
    """
    changed = False
    if display_name is not None:
        name = display_name.strip()
        if not name:
            raise ValidationFailed("Display name must not be empty")
        user.display_name = name[:120]
        changed = True
    if preferred_language is not None:
        if preferred_language not in ("en", "ru"):
            raise ValidationFailed("preferred_language must be 'en' or 'ru'")
        user.preferred_language = preferred_language
        changed = True
    if not changed:
        return user
    await db.commit()
    await db.refresh(user)
    return user
