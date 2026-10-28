"""Avatar service: set, clear, and decode (AGENTS.md §2.13).

Avatars are stored as base64 in the DB (no file system). The upload cap
(``MAX_AVATAR_MB``) and content-type allow-list are enforced here, before
encoding. Services raise domain errors (never import FastAPI); the API layer
maps them to HTTP statuses.
"""

from __future__ import annotations

import base64

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import ValidationFailed
from app.models.user import User

# Allowed content types for avatar uploads (raster images only).
_ALLOWED_TYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/webp",
        "image/gif",
    }
)


async def set_avatar(db: AsyncSession, user: User, file_bytes: bytes, content_type: str) -> None:
    """Store the avatar as base64 on the user row.

    Raises ``ValidationFailed`` (HTTP 422) when the content type is not an
    allowed image type or the file exceeds ``MAX_AVATAR_MB``.
    """
    settings = get_settings()
    if content_type not in _ALLOWED_TYPES:
        raise ValidationFailed("Only PNG, JPEG, WebP, or GIF images are allowed")
    max_bytes = settings.max_avatar_mb * 1024 * 1024
    if len(file_bytes) > max_bytes:
        raise ValidationFailed(f"Avatar exceeds the {settings.max_avatar_mb} MB limit")
    user.avatar_data = base64.b64encode(file_bytes).decode("ascii")
    user.avatar_content_type = content_type
    await db.commit()
    await db.refresh(user)


async def clear_avatar(db: AsyncSession, user: User) -> None:
    """Remove the avatar (both fields set to NULL)."""
    user.avatar_data = None
    user.avatar_content_type = None
    await db.commit()
    await db.refresh(user)


def decode_avatar(user: User) -> tuple[bytes, str] | None:
    """Return ``(raw_bytes, content_type)`` or ``None`` when the user has no
    avatar. The caller is responsible for HTTP 404 handling."""
    if user.avatar_data is None or user.avatar_content_type is None:
        return None
    raw = base64.b64decode(user.avatar_data)
    return raw, user.avatar_content_type
