"""Group endpoints (AGENTS.md §2.9–2.10, README API overview).

Membership/ownership is enforced by the deps; closed-group restrictions for
expenses/receipts live in ``services/expenses.py`` (409).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.common import build_group_out
from app.core.config import get_settings
from app.core.deps import get_db, get_group_member, get_group_owner
from app.core.security import get_current_user
from app.models.group import Group
from app.models.invitation import Invitation
from app.models.user import User
from app.schemas.groups import (
    GroupCreate,
    GroupOut,
    GroupRename,
    GroupSettingsUpdate,
    MemberOut,
    OwnerTransfer,
)
from app.schemas.invites import InviteCreate, InviteOut
from app.schemas.user import avatar_url
from app.services import balances as balances_service
from app.services import groups as groups_service
from app.services import invites as invites_service

router = APIRouter(prefix="/api/groups", tags=["groups"])


@router.get("", response_model=list[GroupOut])
async def list_groups(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[GroupOut]:
    rows = await groups_service.list_my_groups(db, user)
    return [
        build_group_out(group, count, balance)
        for group, count, balance in rows
    ]


@router.post("", response_model=GroupOut, status_code=201)
async def create_group(
    body: GroupCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GroupOut:
    group = await groups_service.create_group(db, user, body.name, body.currency)
    balance = await balances_service.group_balance_for_user(db, group.id, user.id)
    return build_group_out(group, 1, balance)


@router.get("/{group_id}", response_model=GroupOut)
async def get_group(
    group: Group = Depends(get_group_member),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GroupOut:
    count, balance = await groups_service.group_with_count(db, group, user)
    return build_group_out(group, count, balance)


@router.patch("/{group_id}", response_model=GroupOut)
async def rename_group(
    body: GroupRename,
    group: Group = Depends(get_group_owner),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GroupOut:
    """Owner-only. Renames the group."""
    group = await groups_service.rename_group(db, group, user, body.name)
    count, balance = await groups_service.group_with_count(db, group, user)
    return build_group_out(group, count, balance)


@router.patch("/{group_id}/settings", response_model=GroupOut)
async def update_group_settings(
    body: GroupSettingsUpdate,
    group: Group = Depends(get_group_owner),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GroupOut:
    """Owner-only. Updates the group's reminder settings (AGENTS.md §2.7)."""
    group = await groups_service.update_settings(
        db, group, user, body.debt_overdue_days, body.reminder_interval_days
    )
    count, balance = await groups_service.group_with_count(db, group, user)
    return build_group_out(group, count, balance)


@router.post("/{group_id}/transfer-owner", response_model=GroupOut)
async def transfer_group_owner(
    body: OwnerTransfer,
    group: Group = Depends(get_group_owner),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GroupOut:
    """Owner-only. Transfers the group to another current member."""
    group = await groups_service.transfer_owner(db, group, user, body.user_id)
    count, balance = await groups_service.group_with_count(db, group, user)
    return build_group_out(group, count, balance)


@router.get("/{group_id}/members", response_model=list[MemberOut])
async def list_members(
    group: Group = Depends(get_group_member),
    db: AsyncSession = Depends(get_db),
) -> list[MemberOut]:
    rows = await groups_service.list_members(db, group)
    return [
        MemberOut(
            id=u.id,
            display_name=u.display_name,
            email=u.email,
            joined_at=m.joined_at,
            is_owner=u.id == group.created_by,
            avatar_url=avatar_url(u.id, u.avatar_data),
        )
        for u, m in rows
    ]


@router.post("/{group_id}/members/leave", status_code=204)
async def leave_group(
    group: Group = Depends(get_group_member),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await groups_service.leave_group(db, group, user)


@router.delete("/{group_id}/members/{user_id}", status_code=204)
async def remove_member(
    user_id: int,
    group: Group = Depends(get_group_owner),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await groups_service.remove_member(db, group, user, user_id)


@router.post("/{group_id}/close", response_model=GroupOut)
async def close_group(
    group: Group = Depends(get_group_owner),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GroupOut:
    """Owner-only. Closes the group and mails the final summary to all members."""
    group = await groups_service.close_group(db, group, user)
    count, balance = await groups_service.group_with_count(db, group, user)
    return build_group_out(group, count, balance)


@router.post("/{group_id}/reopen", response_model=GroupOut)
async def reopen_group(
    group: Group = Depends(get_group_owner),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GroupOut:
    """Owner-only. Reopens a closed group (no email)."""
    group = await groups_service.reopen_group(db, group, user)
    count, balance = await groups_service.group_with_count(db, group, user)
    return build_group_out(group, count, balance)


def _invite_out(invite: Invitation) -> InviteOut:
    settings = get_settings()
    return InviteOut(
        id=invite.id,
        kind=invite.kind,
        token=invite.token,
        url=f"{settings.frontend_url.rstrip('/')}/invite/{invite.token}",
        email_to=invite.email_to,
        created_by=invite.created_by,
        expires_at=invite.expires_at,
        status=invite.status,
        used_by=invite.used_by,
        used_at=invite.used_at,
    )


@router.post("/{group_id}/invites", response_model=InviteOut, status_code=201)
async def create_invite(
    body: InviteCreate,
    group: Group = Depends(get_group_member),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> InviteOut:
    invite = await invites_service.create_invite(db, group, user, body.kind, body.email_to)
    return _invite_out(invite)


@router.get("/{group_id}/invites", response_model=list[InviteOut])
async def list_invites(
    group: Group = Depends(get_group_member),
    db: AsyncSession = Depends(get_db),
) -> list[InviteOut]:
    invites = await invites_service.list_invites(db, group)
    return [_invite_out(i) for i in invites]
