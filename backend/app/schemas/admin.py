"""Admin panel schemas (AGENTS.md §2.13).

Admins manage users directly (no email confirmation) and can read platform
statistics. Money in the stats is always a string (§2.12).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# User management
# ---------------------------------------------------------------------------


class AdminUserCreateRequest(BaseModel):
    """Create a user without the registration/confirmation flow."""

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=200)
    display_name: str | None = Field(default=None, max_length=120)


class AdminUserPatchRequest(BaseModel):
    """Edit any user. All fields optional; omitted fields are unchanged."""

    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    email: str | None = Field(default=None, min_length=3, max_length=320)
    password: str | None = Field(default=None, min_length=8, max_length=200)
    # Toggling the admin bit. The last remaining admin cannot be demoted.
    is_admin: bool | None = None


class AdminUserOut(BaseModel):
    """One user in the admin list."""

    id: int
    email: str
    display_name: str
    is_admin: bool
    email_verified: bool
    avatar_url: str | None = None
    created_at: datetime


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


class CurrencyTotalOut(BaseModel):
    """A money total rendered in a single currency (string, §2.12)."""

    currency: str
    total: str


class AdminStatsOut(BaseModel):
    """Platform-wide statistics for the admin panel (§2.13)."""

    users_total: int
    users_admin: int
    users_unverified: int
    groups_active: int
    groups_closed: int
    expenses_total: int
    expenses_by_currency: list[CurrencyTotalOut]
    settlements_by_state: dict[str, int]
    settlements_confirmed_by_currency: list[CurrencyTotalOut]
