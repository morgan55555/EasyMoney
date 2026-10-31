"""User profile schemas (AGENTS.md §2.11, §2.13).

``UserOut`` is the shape every user-bearing response projects. ``avatar_url``
is a short URL (not the base64 blob) so list payloads stay light; ``None``
means the user has no avatar and the client falls back to initials.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


def avatar_url(user_id: int, avatar_data: str | None) -> str | None:
    """Short avatar URL for list payloads; ``None`` when the user has no avatar."""
    return f"/api/users/{user_id}/avatar" if avatar_data is not None else None


class UserOut(BaseModel):
    id: int
    email: str
    display_name: str
    created_at: datetime
    is_admin: bool = False
    email_verified: bool = True
    preferred_language: str = "en"
    # Preferred currency (ISO 4217 code); None = none set. Pinned to the top of
    # the currency picker and used as its default in the UI.
    preferred_currency: str | None = None
    # Short URL or null; the image itself is served by GET /users/{id}/avatar.
    avatar_url: str | None = None
    # Effective server-side confirmation toggles (AGENTS.md §2.13): true only
    # when the corresponding confirmation flow is actually enforced. Lets the
    # settings modal show/hide the confirmation hints. Populated on GET /me.
    email_change_confirmation: bool = False
    password_change_confirmation: bool = False


class MePatchRequest(BaseModel):
    # All fields optional; omitted fields are unchanged.
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    # "en" | "ru". Changing this is the only way to set preferred_language.
    preferred_language: str | None = Field(default=None, pattern="^(en|ru)$")
    # ISO 4217 currency code (e.g. "USD"); null clears the preference.
    preferred_currency: str | None = Field(default=None, pattern="^[A-Z]{3}$")


class AvatarSetResponse(BaseModel):
    """Returned after a successful avatar upload."""

    ok: bool = True
    avatar_url: str
