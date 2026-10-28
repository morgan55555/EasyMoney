"""Activity feed rows (AGENTS.md §3.3).

Rows are appended in the same transaction as the action they record. The
reader is keyset-paginated on ``(created_at, id)`` descending — the same
ordering the §3.7 index serves — with an opaque cursor token.
"""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Any

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import ValidationFailed
from app.models.activity import ActivityEvent

DEFAULT_PAGE_SIZE = 30
MAX_PAGE_SIZE = 100


async def add_event(
    db: AsyncSession,
    group_id: int,
    type: str,
    actor_id: int | None = None,
    ref_type: str | None = None,
    ref_id: int | None = None,
    payload: dict[str, Any] | None = None,
) -> ActivityEvent:
    """Append one feed row. The caller commits the surrounding transaction."""
    event = ActivityEvent(
        group_id=group_id,
        type=type,
        actor_id=actor_id,
        ref_type=ref_type,
        ref_id=ref_id,
        payload=payload,
    )
    db.add(event)
    return event


def encode_cursor(created_at: datetime, event_id: int) -> str:
    raw = f"{created_at.isoformat()}|{event_id}".encode()
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        created, event_id = raw.rsplit("|", 1)
        return datetime.fromisoformat(created), int(event_id)
    except Exception as exc:  # malformed token: treat as a client error, not 500
        raise ValidationFailed("Invalid cursor") from exc


async def list_events(
    db: AsyncSession,
    group_id: int,
    cursor: str | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
) -> tuple[list[ActivityEvent], str | None]:
    """One page of the group feed, newest first, plus the next cursor (or None).

    Rows of other groups never appear; soft-deleted expenses stay in the feed
    (their ``expense.deleted`` row is the record, §3.3).
    """
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    stmt = (
        select(ActivityEvent)
        .where(ActivityEvent.group_id == group_id)
        .options(selectinload(ActivityEvent.actor))
        .order_by(ActivityEvent.created_at.desc(), ActivityEvent.id.desc())
        .limit(limit + 1)  # one extra row decides whether a next page exists
    )
    if cursor is not None:
        c_at, c_id = decode_cursor(cursor)
        stmt = stmt.where(tuple_(ActivityEvent.created_at, ActivityEvent.id) < (c_at, c_id))
    rows = list((await db.execute(stmt)).scalars().all())
    if len(rows) > limit:
        # The probe row (index ``limit``) only tells us a next page exists;
        # the cursor points at the last row of the page we actually return.
        probe = rows[limit - 1]
        next_cursor: str | None = encode_cursor(probe.created_at, probe.id)
    else:
        next_cursor = None
    return rows[:limit], next_cursor
