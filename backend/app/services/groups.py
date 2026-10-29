"""Group lifecycle: create, list, rename, membership, close/reopen
(AGENTS.md §2.9–2.10).

Membership checks (is the caller a member / the owner) are enforced by the
API dependencies (``get_group_member`` / ``get_group_owner``); this service
owns the business rules and the feed side effects.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, Forbidden, ValidationFailed
from app.models.group import Group
from app.models.membership import Membership
from app.models.user import User
from app.services import balances as balances_service
from app.services import currency as currency_service
from app.services import feed as feed_service
from app.services import mail as mail_service
from app.services import notifications as notifications_service
from app.ws import hook as ws_hook

logger = logging.getLogger("easymoney.groups")


async def create_group(db: AsyncSession, user: User, name: str, currency: str) -> Group:
    clean = name.strip()
    if not clean:
        raise ValidationFailed("Group name must not be empty")
    code = currency_service.validate_currency(currency)
    group = Group(name=clean[:120], currency=code, created_by=user.id, status="active")
    db.add(group)
    await db.flush()  # need the id for the membership + feed rows
    # The creator joins automatically; joined_at is set by the database.
    db.add(Membership(group_id=group.id, user_id=user.id))
    await feed_service.add_event(
        db,
        group.id,
        "group.created",
        actor_id=user.id,
        ref_type="group",
        ref_id=group.id,
        payload={"name": group.name, "currency": code},
    )
    payload = {"user_id": user.id, "display_name": user.display_name}
    await feed_service.add_event(
        db,
        group.id,
        "member.joined",
        actor_id=user.id,
        ref_type="user",
        ref_id=user.id,
        payload=payload,
    )
    # §2.6: a user is never notified about their own join, and the creator is
    # the only member here — so no notification, just the feed + WS event.
    await db.commit()
    await db.refresh(group)
    # The creator's already-open tabs get the new group immediately: re-
    # subscribe them before the fan-out so they see the member.joined event.
    ws_hook.resubscribe_user(user.id, group.id)
    await ws_hook.emit_group(db, group, "member.joined", payload)
    return group


async def list_my_groups(db: AsyncSession, user: User) -> list[tuple[Group, int, Decimal]]:
    """``(group, member_count, balance)`` for every group the user is in."""
    rows = (
        await db.execute(
            select(
                Group,
                select(func.count())
                .select_from(Membership)
                .where(Membership.group_id == Group.id)
                .correlate(Group)
                .scalar_subquery(),
            )
            .join(Membership, Membership.group_id == Group.id)
            .where(Membership.user_id == user.id)
            .group_by(Group.id)
            .order_by(Group.created_at.desc())
        )
    ).all()
    result: list[tuple[Group, int, Decimal]] = []
    for group, count in rows:
        balance = await balances_service.group_balance_for_user(db, group.id, user.id)
        result.append((group, int(count), balance))
    return result


async def group_with_count(db: AsyncSession, group: Group, user: User) -> tuple[int, Decimal]:
    """Member count + the caller's balance chip for one group."""
    count = await db.scalar(
        select(func.count()).select_from(Membership).where(Membership.group_id == group.id)
    )
    balance = await balances_service.group_balance_for_user(db, group.id, user.id)
    return int(count or 0), balance


async def rename_group(db: AsyncSession, group: Group, user: User, name: str) -> Group:
    clean = name.strip()
    if not clean:
        raise ValidationFailed("Group name must not be empty")
    group.name = clean[:120]
    await db.commit()
    await db.refresh(group)
    return group


async def list_member_ids(db: AsyncSession, group_id: int) -> set[int]:
    """The current members of a group (Membership rows)."""
    rows = (
        (await db.execute(select(Membership.user_id).where(Membership.group_id == group_id)))
        .scalars()
        .all()
    )
    return set(rows)


async def list_members(db: AsyncSession, group: Group) -> list[tuple[User, Membership]]:
    rows = (
        await db.execute(
            select(User, Membership)
            .join(Membership, Membership.user_id == User.id)
            .where(Membership.group_id == group.id)
            .order_by(Membership.joined_at.asc(), User.id.asc())
        )
    ).all()
    return [(u, m) for u, m in rows]


async def leave_group(db: AsyncSession, group: Group, user: User) -> None:
    if group.created_by == user.id:
        raise Conflict("The group owner cannot leave; close the group instead")
    membership = await db.scalar(
        select(Membership).where(Membership.group_id == group.id, Membership.user_id == user.id)
    )
    if membership is None:
        raise Conflict("You are not a member of this group")
    payload = {
        "user_id": user.id,
        "display_name": user.display_name,
        "group_id": group.id,
        "group_name": group.name,
    }
    await db.delete(membership)
    await feed_service.add_event(
        db,
        group.id,
        "member.left",
        actor_id=user.id,
        ref_type="user",
        ref_id=user.id,
        payload=payload,
    )
    # §2.6: the remaining members are notified.
    await db.flush()
    remaining = set(
        (await db.execute(select(Membership.user_id).where(Membership.group_id == group.id)))
        .scalars()
        .all()
    )
    notifications = await notifications_service.notify_users(db, remaining, "member.left", payload)
    await db.commit()
    await ws_hook.emit_group(db, group, "member.left", payload)
    await ws_hook.emit_notifications(group.id, notifications)


# --- B9: close / reopen (AGENTS.md §2.9) ---------------------------------------


async def close_group(db: AsyncSession, group: Group, user: User) -> Group:
    """Owner-only. Closes the group and mails every member the final summary.

    While closed, new expenses and receipt uploads are rejected with 409
    (``services/expenses.py``); everything else — settlements, invites,
    leaving, reads — still works. The summary is computed on read
    (invariant 4) and sent post-commit, member by member: a mail failure
    must never undo the close.
    """
    if user.id != group.created_by:
        raise Forbidden("Only the group owner can close the group")
    if group.status == "closed":
        raise Conflict("The group is already closed")
    group.status = "closed"
    group.closed_at = datetime.now(UTC)
    payload = {"closed_by": user.id, "group_id": group.id, "group_name": group.name}
    await feed_service.add_event(
        db,
        group.id,
        "group.closed",
        actor_id=user.id,
        ref_type="group",
        ref_id=group.id,
        payload=payload,
    )
    # §2.6: every member is notified of the close.
    member_ids = await list_member_ids(db, group.id)
    notifications = await notifications_service.notify_users(
        db, member_ids, "group.closed", payload
    )
    await db.commit()
    await db.refresh(group)
    # Post-commit summary email to every member (§2.9).
    snapshot = await balances_service.group_balance_snapshot(db, group)
    names = {b.user_id: b.display_name for b in snapshot.balances}
    # §2.9: the summary is a complete statement — every member, zero balances
    # included, not just the ones with outstanding debts.
    all_balances = [(b.display_name, b.balance) for b in snapshot.balances]
    pairs = [
        (
            names.get(t.from_user_id, f"user {t.from_user_id}"),
            names.get(t.to_user_id, f"user {t.to_user_id}"),
            t.amount,
        )
        for t in snapshot.pairs
    ]
    transfers = [
        (
            names.get(t.from_user_id, f"user {t.from_user_id}"),
            names.get(t.to_user_id, f"user {t.to_user_id}"),
            t.amount,
        )
        for t in snapshot.transfers
    ]
    if member_ids:
        members = (await db.execute(select(User).where(User.id.in_(member_ids)))).scalars().all()
        for member in members:
            try:
                # One mail per member, greeted by name (§2.9).
                subject, text, html = mail_service.close_summary_message(
                    group.name,
                    group.currency,
                    all_balances,
                    pairs,
                    transfers,
                    recipient_name=member.display_name,
                    group_id=group.id,
                    lang=member.preferred_language,
                )
                await mail_service.send_mail(member.email, subject, text, html)
            except Exception:
                logger.exception(
                    "Close summary mail failed for %s (group=%s)", member.email, group.id
                )
    await ws_hook.emit_group(db, group, "group.closed", payload)
    await ws_hook.emit_notifications(group.id, notifications)
    return group


async def reopen_group(db: AsyncSession, group: Group, user: User) -> Group:
    """Owner-only. Reopens a closed group; no email (§2.9)."""
    if user.id != group.created_by:
        raise Forbidden("Only the group owner can reopen the group")
    if group.status != "closed":
        raise Conflict("The group is not closed")
    group.status = "active"
    group.closed_at = None
    payload = {"reopened_by": user.id, "group_id": group.id, "group_name": group.name}
    await feed_service.add_event(
        db,
        group.id,
        "group.reopened",
        actor_id=user.id,
        ref_type="group",
        ref_id=group.id,
        payload=payload,
    )
    # §2.6: every member is notified of the reopen.
    member_ids = await list_member_ids(db, group.id)
    notifications = await notifications_service.notify_users(
        db, member_ids, "group.reopened", payload
    )
    await db.commit()
    await db.refresh(group)
    await ws_hook.emit_group(db, group, "group.reopened", payload)
    await ws_hook.emit_notifications(group.id, notifications)
    return group


async def remove_member(db: AsyncSession, group: Group, actor: User, user_id: int) -> None:
    if actor.id != group.created_by:
        raise Forbidden("Only the group owner can remove members")
    if user_id == actor.id:
        raise Conflict("You cannot remove yourself; close the group instead")
    membership = await db.scalar(
        select(Membership).where(Membership.group_id == group.id, Membership.user_id == user_id)
    )
    if membership is None:
        raise Conflict("That user is not a member of this group")
    member = await db.get(User, user_id)
    payload = {
        "user_id": user_id,
        "display_name": member.display_name if member else f"user {user_id}",
        "removed_by": actor.id,
        "group_id": group.id,
        "group_name": group.name,
    }
    await db.delete(membership)
    await feed_service.add_event(
        db,
        group.id,
        "member.left",
        actor_id=actor.id,
        ref_type="user",
        ref_id=user_id,
        payload=payload,
    )
    # §2.6: the remaining members are notified.
    await db.flush()
    remaining = set(
        (await db.execute(select(Membership.user_id).where(Membership.group_id == group.id)))
        .scalars()
        .all()
    )
    notifications = await notifications_service.notify_users(db, remaining, "member.left", payload)
    await db.commit()
    await ws_hook.emit_group(db, group, "member.left", payload)
    await ws_hook.emit_notifications(group.id, notifications)
