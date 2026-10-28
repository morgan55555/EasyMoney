"""In-app notification schemas (AGENTS.md §2.6)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class NotificationOut(BaseModel):
    id: int
    type: str
    payload: dict[str, Any] | None
    read_at: datetime | None
    created_at: datetime


class NotificationListOut(BaseModel):
    items: list[NotificationOut]
    unread_count: int


class NotificationReadRequest(BaseModel):
    """Mark read: the listed ids, or all of them when omitted."""

    ids: list[int] | None = Field(default=None, max_length=500)
