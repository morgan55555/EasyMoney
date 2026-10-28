"""Activity feed schemas (AGENTS.md §3.3).

The feed is cursor-paginated on ``(created_at, id)`` descending; ``cursor``
is an opaque token produced by the service (never a raw database value).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel


class ActivityEventOut(BaseModel):
    id: int
    type: str
    actor_id: int | None
    # The actor's display name, so a client can render a feed line without a
    # user lookup (NULL for system events, which have no actor).
    actor_name: str | None
    actor_avatar_url: str | None = None
    ref_type: str | None
    ref_id: int | None
    # Event-specific data (mirrors the WS catalogue payloads, §3.2/§3.3).
    payload: dict[str, Any] | None
    created_at: dt.datetime


class ActivityPageOut(BaseModel):
    items: list[ActivityEventOut]
    next_cursor: str | None
