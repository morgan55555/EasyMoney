"""Notification bell endpoints (AGENTS.md §2.6)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.notifications import NotificationListOut, NotificationOut, NotificationReadRequest
from app.services import notifications as notifications_service

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("", response_model=NotificationListOut)
async def list_notifications(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NotificationListOut:
    items, unread_count = await notifications_service.list_for_user(db, user)
    return NotificationListOut(
        items=[
            NotificationOut(
                id=n.id,
                type=n.type,
                payload=n.payload,
                read_at=n.read_at,
                created_at=n.created_at,
            )
            for n in items
        ],
        unread_count=unread_count,
    )


@router.post("/read", response_model=dict)
async def mark_notifications_read(
    body: NotificationReadRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    marked = await notifications_service.mark_read(db, user, body.ids)
    return {"marked": marked}
