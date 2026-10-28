"""Activity feed endpoint (AGENTS.md §3.3).

Read-only: the feed is written in the same transaction as each action by
``app.services.feed.add_event``; this router only pages through it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, get_group_member
from app.models.group import Group
from app.schemas.feed import ActivityEventOut, ActivityPageOut
from app.services import feed as feed_service

router = APIRouter(prefix="/api", tags=["feed"])


@router.get("/groups/{group_id}/activity", response_model=ActivityPageOut)
async def list_activity(
    group: Group = Depends(get_group_member),
    db: AsyncSession = Depends(get_db),
    cursor: str | None = Query(default=None, min_length=1),
    limit: int = Query(default=30, ge=1, le=100),
) -> ActivityPageOut:
    events, next_cursor = await feed_service.list_events(db, group.id, cursor, limit)
    return ActivityPageOut(
        items=[
            ActivityEventOut(
                id=e.id,
                type=e.type,
                actor_id=e.actor_id,
                actor_name=e.actor.display_name if e.actor else None,
                ref_type=e.ref_type,
                ref_id=e.ref_id,
                payload=e.payload,
                created_at=e.created_at,
            )
            for e in events
        ],
        next_cursor=next_cursor,
    )
