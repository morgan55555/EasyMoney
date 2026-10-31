"""Group request/response schemas (AGENTS.md §2.9, §2.12).

Money is always a string in the API (§2.12): the balance chip arrives
pre-formatted with the group currency's minor digits.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    currency: str = Field(min_length=3, max_length=3)


class GroupOut(BaseModel):
    id: int
    name: str
    currency: str
    status: str
    created_by: int
    closed_at: datetime | None
    member_count: int
    balance: str
    # Per-group reminder settings (AGENTS.md §2.7): null = the app-wide
    # default applies (the defaults/floor are echoed so the UI can show
    # "— (default)" without another call).
    debt_overdue_days: int | None = None
    reminder_interval_days: int | None = None
    debt_overdue_days_default: int = 7
    reminder_interval_days_default: int = 7
    reminder_interval_days_min: int = 7


class GroupRename(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class GroupSettingsUpdate(BaseModel):
    """Owner-only ``PATCH /api/groups/{id}/settings`` body (full state).

    A field set to ``null`` clears the override (the app-wide default
    applies again). The interval is rejected (422) when set below
    ``REMINDER_INTERVAL_DAYS_MIN``.
    """

    debt_overdue_days: int | None = Field(ge=0, le=3650)
    reminder_interval_days: int | None = Field(ge=1, le=3650)


class OwnerTransfer(BaseModel):
    user_id: int = Field(ge=1)


class MemberOut(BaseModel):
    id: int
    display_name: str
    email: str
    joined_at: datetime
    is_owner: bool
    avatar_url: str | None = None
