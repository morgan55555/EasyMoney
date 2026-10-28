"""In-app notifications (AGENTS.md §2.6).

One ``Notification`` row per user per event. ``notify_users`` inserts the rows
inside the caller's transaction (same commit as the action + feed row); the
WebSocket push happens post-commit (B8). Read state is explicit:
``POST /notifications/read`` sets ``read_at``.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification
from app.models.user import User


async def notify_users(
    db: AsyncSession, user_ids: Iterable[int], type: str, payload: dict[str, Any] | None = None
) -> list[Notification]:
    """Insert one notification per distinct user. The caller commits.

    Returns the rows so the caller can fan the ``notification.created`` WS
    event out post-commit (B8).
    """
    rows: list[Notification] = []
    for user_id in sorted(set(user_ids)):
        notification = Notification(user_id=user_id, type=type, payload=payload)
        db.add(notification)
        rows.append(notification)
    return rows


async def list_for_user(db: AsyncSession, user: User) -> tuple[list[Notification], int]:
    """Latest 50 notifications + the unread count for the bell."""
    items = (
        await db.execute(
            select(Notification)
            .where(Notification.user_id == user.id)
            .order_by(Notification.created_at.desc(), Notification.id.desc())
            .limit(50)
        )
    ).scalars().all()
    unread = await db.scalar(
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
    )
    return list(items), int(unread or 0)


async def mark_read(db: AsyncSession, user: User, ids: list[int] | None = None) -> int:
    """Mark read the given ids (or all of them when omitted).

    Only touches the caller's own rows; already-read rows are skipped.
    Returns the number of rows flipped.
    """
    stmt = select(Notification).where(Notification.user_id == user.id)
    if ids is not None:
        stmt = stmt.where(Notification.id.in_(ids))
    rows = (await db.execute(stmt)).scalars().all()
    now = datetime.now(UTC)
    marked = 0
    for notification in rows:
        if notification.read_at is None:
            notification.read_at = now
            marked += 1
    await db.commit()
    return marked
