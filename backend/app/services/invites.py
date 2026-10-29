"""Invitations: link (reusable) and email (single-use) (AGENTS.md §2.10).

Tokens are 128-bit URL-safe secrets. Link invites stay ``active`` for
everyone (used_by/used_at record the most recent joiner, display only);
email invites flip to ``used`` for the addressed address.
"""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.models.group import Group
from app.models.invitation import Invitation
from app.models.membership import Membership
from app.models.notification import Notification
from app.models.user import User
from app.services import feed as feed_service
from app.services import groups as groups_service
from app.services import mail as mail_service
from app.services import notifications as notifications_service
from app.ws import hook as ws_hook

logger = logging.getLogger("easymoney.invites")


async def create_invite(
    db: AsyncSession, group: Group, actor: User, kind: str, email_to: str | None = None
) -> Invitation:
    if kind not in ("link", "email"):
        raise ValidationFailed("kind must be 'link' or 'email'")
    if kind == "email" and (not email_to or "@" not in email_to):
        raise ValidationFailed("An email address is required for email invites")
    settings = get_settings()
    target = email_to.strip().lower() if email_to else None
    invited: User | None = None
    if kind == "email" and target:
        invited = await db.scalar(select(User).where(User.email == target))
        if invited is not None:
            # Refuse to re-invite an existing member: there is nothing to send.
            already_member = await db.scalar(
                select(Membership).where(
                    Membership.group_id == group.id, Membership.user_id == invited.id
                )
            )
            if already_member is not None:
                raise Conflict("This user is already in this group")
        # A pending (active) email invite for the same address has not been
        # consumed yet — don't send a duplicate one.
        pending = await db.scalar(
            select(Invitation).where(
                Invitation.group_id == group.id,
                Invitation.kind == "email",
                Invitation.email_to == target,
                Invitation.status == "active",
            )
        )
        if pending is not None:
            raise Conflict("This user is already invited to this group")
    invite = Invitation(
        group_id=group.id,
        token=secrets.token_urlsafe(16),
        kind=kind,
        email_to=target,
        created_by=actor.id,
        expires_at=datetime.now(UTC) + timedelta(days=settings.invite_expiry_days),
        status="active",
    )
    db.add(invite)
    # An email invite addressed to an address that already has an account also
    # bell-notifies that user in-app (§2.6), in addition to the email: they can
    # open the notification to join the group. The row is staged in the same
    # transaction so the invite and its notification commit together.
    notifications: list[Notification] = []
    if invited is not None:
        notifications = await notifications_service.notify_users(
            db,
            [invited.id],
            "invited",
            {
                "group_id": group.id,
                "group_name": group.name,
                "token": invite.token,
                "inviter_name": actor.display_name,
            },
        )
    await db.commit()
    await db.refresh(invite)
    if kind == "email" and invite.email_to:
        subject, text, html = mail_service.invite_message(
            group.name, actor.display_name, invite.token, settings.invite_expiry_days, lang="en"
        )
        try:
            await mail_service.send_mail(invite.email_to, subject, text, html)
        except Exception:  # mail must never block the invite itself
            logger.exception("Failed to send invite email (token=%s)", invite.token)
    if notifications:
        await ws_hook.emit_notifications(group.id, notifications)
    return invite


async def list_invites(db: AsyncSession, group: Group) -> list[Invitation]:
    result = await db.execute(
        select(Invitation)
        .where(Invitation.group_id == group.id)
        .order_by(Invitation.created_at.desc())
    )
    return list(result.scalars().all())


async def revoke_invite(db: AsyncSession, group: Group, actor: User, invite_id: int) -> None:
    invite = await db.get(Invitation, invite_id)
    if invite is None or invite.group_id != group.id:
        raise NotFound("Invitation not found")
    if invite.status != "active":
        raise Conflict("This invitation is already used or revoked")
    invite.status = "revoked"
    await db.commit()


async def revoke_invite_by_id(db: AsyncSession, user: User, invite_id: int) -> None:
    """``DELETE /invites/{id}`` — the URL carries no group id, so membership
    is verified here against the invitation's group."""
    invite = await db.get(Invitation, invite_id)
    if invite is None:
        raise NotFound("Invitation not found")
    membership = await db.scalar(
        select(Membership).where(
            Membership.group_id == invite.group_id, Membership.user_id == user.id
        )
    )
    if membership is None:
        raise Forbidden("Not a member of this group")
    if invite.status != "active":
        raise Conflict("This invitation is already used or revoked")
    invite.status = "revoked"
    await db.commit()


async def accept_invite(db: AsyncSession, user: User, token: str) -> Group:
    invite = await db.scalar(select(Invitation).where(Invitation.token == token))
    if invite is None:
        raise NotFound("Invitation not found")
    if invite.status == "revoked":
        raise Conflict("This invitation has been revoked")
    if invite.status == "used":
        raise Conflict("This invitation has already been used")
    now = datetime.now(UTC)
    if invite.expires_at <= now:
        raise Conflict("This invitation has expired")
    if invite.kind == "email" and (not invite.email_to or user.email.lower() != invite.email_to):
        raise Forbidden("This invitation is addressed to a different email")
    existing = await db.scalar(
        select(Membership).where(
            Membership.group_id == invite.group_id, Membership.user_id == user.id
        )
    )
    if existing is not None:
        raise Conflict("You are already a member of this group")
    group = await db.get(Group, invite.group_id)
    if group is None:
        raise NotFound("Group not found")
    db.add(Membership(group_id=group.id, user_id=user.id))
    # autoflush is off (see db/session.py), so the joiner's Membership would
    # be invisible to the member query below — flush it so §2.6 notifies
    # "all members", joiner included.
    await db.flush()
    if invite.kind == "email":
        invite.status = "used"
    invite.used_by = user.id
    invite.used_at = now
    payload = {
        "user_id": user.id,
        "display_name": user.display_name,
        "group_id": group.id,
        "group_name": group.name,
    }
    await feed_service.add_event(
        db,
        group.id,
        "member.joined",
        actor_id=user.id,
        ref_type="user",
        ref_id=user.id,
        payload=payload,
    )
    # §2.6: all members are notified — except the joiner (a user is never
    # notified about their own join).
    member_ids = await groups_service.list_member_ids(db, group.id)
    notifications = await notifications_service.notify_users(
        db, member_ids - {user.id}, "member.joined", payload
    )
    await db.commit()
    # The joiner's already-open tabs get the new group immediately: re-
    # subscribe them before the fan-out so they see the member.joined event.
    await ws_hook.resubscribe_user(user.id, group.id)
    await ws_hook.emit_group(db, group, "member.joined", payload)
    await ws_hook.emit_notifications(group.id, notifications)
    return group
