"""Invite endpoints outside the group scope (AGENTS.md §2.10).

``POST /invites/accept`` joins the caller's account to the group behind the
token. It is authenticated: the spec's acceptance rule ("user not already
a member") requires a resolved user, and a fresh registrant already holds an
access token by the time they accept.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.common import build_group_out
from app.core.deps import get_db
from app.core.security import get_current_user
from app.models.membership import Membership
from app.models.user import User
from app.schemas.groups import GroupOut
from app.schemas.invites import InviteAccept
from app.services import balances as balances_service
from app.services import invites as invites_service

router = APIRouter(prefix="/api/invites", tags=["invites"])


@router.post("/accept", response_model=GroupOut)
async def accept_invite(
    body: InviteAccept,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GroupOut:
    group = await invites_service.accept_invite(db, user, body.token)
    balance = await balances_service.group_balance_for_user(db, group.id, user.id)
    count = await db.scalar(
        select(func.count()).select_from(Membership).where(Membership.group_id == group.id)
    )
    return build_group_out(group, int(count or 0), balance)


@router.delete("/{invite_id}", status_code=204)
async def revoke_invite(
    invite_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await invites_service.revoke_invite_by_id(db, user, invite_id)
