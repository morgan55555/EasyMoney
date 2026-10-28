"""FastAPI dependencies.

``get_db`` is defined in ``app.db.session`` (next to the session factory) and
re-exported here so routers can import every dependency from one place.
``get_current_user`` lives in ``core.security``. The group-scope dependencies
below resolve the path's ``group_id`` and enforce membership/ownership up
front (404/403), so services can assume the caller is allowed to see the
group.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.core.security import get_current_user
from app.db.session import get_db
from app.models.attachment import Attachment
from app.models.expense import Expense
from app.models.expense_participant import ExpenseParticipant
from app.models.group import Group
from app.models.membership import Membership
from app.models.settlement import Settlement
from app.models.user import User

__all__ = [
    "get_db",
    "get_current_user",
    "get_current_admin",
    "get_group_member",
    "get_group_owner",
    "get_expense_member",
    "get_attachment_member",
    "get_settlement_member",
]


async def get_current_admin(
    user: User = Depends(get_current_user),
) -> User:
    """Require the caller to be an admin (AGENTS.md §2.13)."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


async def get_group_member(
    group_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Group:
    """Resolve ``group_id`` and require the caller to be a member."""
    group = await db.get(Group, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Group not found")
    membership = await db.scalar(
        select(Membership).where(Membership.group_id == group.id, Membership.user_id == user.id)
    )
    if membership is None:
        raise HTTPException(status_code=403, detail="Not a member of this group")
    return group


async def get_group_owner(
    group_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Group:
    """Resolve ``group_id`` and require the caller to be the owner."""
    group = await db.get(Group, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Group not found")
    if group.created_by != user.id:
        raise HTTPException(status_code=403, detail="Only the group owner can do this")
    return group


async def get_expense_member(
    expense_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Expense:
    """Resolve ``expense_id`` and require membership of its group.

    Soft-deleted expenses resolve too (the detail view shows them with
    ``deleted_at``); the edit/delete routes decide how to treat them.
    """
    expense = (
        await db.execute(
            select(Expense)
            .options(
                joinedload(Expense.group),
                joinedload(Expense.payer),
                joinedload(Expense.participants).joinedload(ExpenseParticipant.user),
                joinedload(Expense.attachment),
            )
            .where(Expense.id == expense_id)
        )
    ).scalars().unique().one_or_none()
    if expense is None:
        raise HTTPException(status_code=404, detail="Expense not found")
    membership = await db.scalar(
        select(Membership).where(
            Membership.group_id == expense.group_id, Membership.user_id == user.id
        )
    )
    if membership is None:
        raise HTTPException(status_code=403, detail="Not a member of this group")
    return expense


async def get_attachment_member(
    attachment_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Attachment:
    """Resolve ``attachment_id`` and require membership of the expense's group."""
    attachment = (
        await db.execute(
            select(Attachment)
            .options(joinedload(Attachment.expense))
            .where(Attachment.id == attachment_id)
        )
    ).scalar_one_or_none()
    if attachment is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    membership = await db.scalar(
        select(Membership).where(
            Membership.group_id == attachment.expense.group_id, Membership.user_id == user.id
        )
    )
    if membership is None:
        raise HTTPException(status_code=403, detail="Not a member of this group")
    return attachment


async def get_settlement_member(
    settlement_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Settlement:
    """Resolve ``settlement_id`` and require membership of its group.

    All states resolve (the transition routes decide how non-pending rows are
    treated — with a 409).
    """
    settlement = (
        await db.execute(
            select(Settlement)
            .options(
                joinedload(Settlement.group),
                joinedload(Settlement.from_user),
                joinedload(Settlement.to_user),
            )
            .where(Settlement.id == settlement_id)
        )
    ).scalars().unique().one_or_none()
    if settlement is None:
        raise HTTPException(status_code=404, detail="Settlement not found")
    membership = await db.scalar(
        select(Membership).where(
            Membership.group_id == settlement.group_id, Membership.user_id == user.id
        )
    )
    if membership is None:
        raise HTTPException(status_code=403, detail="Not a member of this group")
    return settlement
