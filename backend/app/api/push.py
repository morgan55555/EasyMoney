"""Web Push endpoints (AGENTS.md §2.14).

Thin routers over the ``push_subscriptions`` table: the VAPID public key
(public; 404 while push is disabled), and the subscribe/unsubscribe pair the
frontend's Settings toggle drives. Both mutation endpoints are per-user and
idempotent: subscribe upserts by ``(user_id, endpoint)``, unsubscribe deletes
by the same key (a second call is still a 204).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import get_current_user, get_db
from app.models.push_subscription import PushSubscription
from app.models.user import User
from app.schemas.push import (
    PushSubscribeRequest,
    PushUnsubscribeRequest,
    PushVapidKeyResponse,
)

router = APIRouter(prefix="/api/push", tags=["push"])


@router.get("/vapid-public-key", response_model=PushVapidKeyResponse)
async def vapid_public_key() -> PushVapidKeyResponse:
    """The browser's ``applicationServerKey``.

    Public (no auth): the Settings toggle needs it before any gesture. 404
    while push is disabled — the frontend then shows the "unsupported" hint
    and never asks for permission.
    """
    settings = get_settings()
    if not settings.push_enabled:
        raise HTTPException(status_code=404, detail="Web Push is not enabled")
    return PushVapidKeyResponse(key=settings.vapid_public_key)


@router.post("/subscribe", status_code=204)
async def subscribe(
    body: PushSubscribeRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    """Upsert the caller's subscription by (user_id, endpoint)."""
    existing = await db.scalar(
        select(PushSubscription).where(
            PushSubscription.user_id == user.id,
            PushSubscription.endpoint == body.endpoint,
        )
    )
    if existing is None:
        db.add(
            PushSubscription(
                user_id=user.id,
                endpoint=body.endpoint,
                p256dh=body.keys.p256dh,
                auth=body.keys.auth,
            )
        )
    else:
        existing.p256dh = body.keys.p256dh
        existing.auth = body.keys.auth
    await db.commit()


@router.post("/unsubscribe", status_code=204)
async def unsubscribe(
    body: PushUnsubscribeRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    """Forget one of the caller's subscriptions; idempotent."""
    result = await db.execute(
        select(PushSubscription)
        .where(
            PushSubscription.user_id == user.id,
            PushSubscription.endpoint == body.endpoint,
        )
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if row is not None:
        await db.delete(row)
        await db.commit()
