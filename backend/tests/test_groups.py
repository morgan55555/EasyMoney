"""AGENTS.md §2.6: a user is never notified about their own action.

  * creating a group does not bell-notify the creator (they are the only
    member, and the notification is theirs);
  * joining via a link invite does not bell-notify the joiner — only the
    existing members receive ``member.joined``.

Plus §2.9: while a group is closed, new expenses and receipt uploads are
rejected (409) and reopening restores writes.

And §2.13: the email-invite mail follows the recipient's preferred language
(RU for an account holder who prefers Russian, EN fallback for an unknown
address).

All scenarios run against the test DB (fresh schema per scenario).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.errors import Conflict
from app.models.group import Group
from app.models.invitation import Invitation
from app.models.membership import Membership
from app.models.notification import Notification
from app.models.user import User
from app.schemas.expenses import ExpenseCreate, ParticipantIn
from app.services import expenses as expenses_service
from app.services import groups as groups_service
from app.services import invites as invites_service
from app.services import mail as mail_service

ALICE, BOB = 1, 2
GROUP_ID = 1
TOKEN = "t0k3n-f0r-test-join"


# --- DB fixtures -------------------------------------------------------------


async def _mk_users(factory: async_sessionmaker, uids: tuple[int, ...]) -> None:
    async with factory() as db:
        for uid in uids:
            db.add(
                User(
                    id=uid,
                    email=f"u{uid}@test.local",
                    password_hash="not-a-real-hash",
                    display_name=f"User {uid}",
                )
            )
        await db.commit()


async def _mk_group(
    factory: async_sessionmaker, gid: int, owner: int, members: tuple[int, ...]
) -> None:
    async with factory() as db:
        db.add(Group(id=gid, name=f"Group {gid}", currency="EUR", created_by=owner))
        for uid in members:
            db.add(Membership(group_id=gid, user_id=uid))
        await db.commit()


async def _notifications(factory: async_sessionmaker) -> list[tuple[int, str]]:
    async with factory() as db:
        return list(
            (
                await db.execute(
                    select(Notification.user_id, Notification.type).order_by(Notification.user_id)
                )
            ).all()
        )


async def _invite_count(factory: async_sessionmaker) -> int:
    async with factory() as db:
        count = await db.scalar(select(func.count()).select_from(Invitation))
        return int(count or 0)


# --- scenarios ---------------------------------------------------------------


async def _creator_is_not_self_notified(factory: async_sessionmaker) -> None:
    await _mk_users(factory, (ALICE,))
    async with factory() as db:
        alice = await db.get(User, ALICE)
        assert alice is not None
        group = await groups_service.create_group(db, alice, "Trip", "EUR")
    assert group.created_by == ALICE
    # The creator is the only member, so §2.6 yields zero notifications —
    # the feed (group.created / member.joined) still records the event.
    assert await _notifications(factory) == []


async def _joiner_is_not_notified_about_own_join(factory: async_sessionmaker) -> None:
    await _mk_users(factory, (ALICE, BOB))
    await _mk_group(factory, GROUP_ID, ALICE, (ALICE,))
    async with factory() as db:
        db.add(
            Invitation(
                group_id=GROUP_ID,
                token=TOKEN,
                kind="link",
                created_by=ALICE,
                expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=7),
                status="active",
            )
        )
        await db.commit()
    async with factory() as db:
        group = await invites_service.accept_invite(db, await db.get(User, BOB), TOKEN)
    assert group.id == GROUP_ID
    # Only the existing member (alice) is bell-notified; the joiner (bob) is
    # excluded from the "member.joined" fan-out.
    assert await _notifications(factory) == [(ALICE, "member.joined")]


async def _email_invite_bell_notifies_existing_user(factory: async_sessionmaker) -> None:
    """§2.6: an email invite addressed to an existing account bell-notifies that
    user (in addition to the email) so they can join from the notification."""
    await _mk_users(factory, (ALICE, BOB))
    await _mk_group(factory, GROUP_ID, ALICE, (ALICE,))
    sent: list[str] = []
    original = mail_service.send_mail

    async def recorder(to: str, subject: str, text: str, html: str | None = None) -> None:
        sent.append(to)

    mail_service.send_mail = recorder
    try:
        async with factory() as db:
            group = await db.get(Group, GROUP_ID)
            alice = await db.get(User, ALICE)
            assert group is not None and alice is not None
            # bob has an account (u2@test.local) → email + in-app "invited" bell.
            invite = await invites_service.create_invite(db, group, alice, "email", "u2@test.local")
    finally:
        mail_service.send_mail = original
    assert sent == ["u2@test.local"]
    assert await _notifications(factory) == [(BOB, "invited")]
    # The payload carries everything the bell needs to open the invite page.
    async with factory() as db:
        row = (
            await db.execute(select(Notification).where(Notification.user_id == BOB))
        ).scalar_one()
    assert row.payload["group_id"] == GROUP_ID
    assert row.payload["group_name"] == f"Group {GROUP_ID}"
    assert row.payload["inviter_name"] == "User 1"
    assert row.payload["token"] == invite.token


async def _email_invite_no_bell_for_unknown_email(factory: async_sessionmaker) -> None:
    """An email invite to an address with no account only emails — no bell row."""
    await _mk_users(factory, (ALICE,))
    await _mk_group(factory, GROUP_ID, ALICE, (ALICE,))
    sent: list[str] = []
    original = mail_service.send_mail

    async def recorder(to: str, subject: str, text: str, html: str | None = None) -> None:
        sent.append(to)

    mail_service.send_mail = recorder
    try:
        async with factory() as db:
            group = await db.get(Group, GROUP_ID)
            alice = await db.get(User, ALICE)
            assert group is not None and alice is not None
            # stranger@test.local has no account → email only, no notification.
            await invites_service.create_invite(db, group, alice, "email", "stranger@test.local")
    finally:
        mail_service.send_mail = original
    assert sent == ["stranger@test.local"]
    assert await _notifications(factory) == []


async def _email_invite_rejects_existing_member(factory: async_sessionmaker) -> None:
    """An email invite to an address that is already a member is refused with a
    clear error — no invite row, no email, no bell (§2.10)."""
    await _mk_users(factory, (ALICE, BOB))
    await _mk_group(factory, GROUP_ID, ALICE, (ALICE, BOB))
    sent: list[str] = []
    original = mail_service.send_mail

    async def recorder(to: str, subject: str, text: str, html: str | None = None) -> None:
        sent.append(to)

    mail_service.send_mail = recorder
    try:
        async with factory() as db:
            group = await db.get(Group, GROUP_ID)
            alice = await db.get(User, ALICE)
            assert group is not None and alice is not None
            # bob (u2@test.local) is already in the group → refuse, don't create.
            with pytest.raises(Conflict) as excinfo:
                await invites_service.create_invite(db, group, alice, "email", "u2@test.local")
        assert str(excinfo.value) == "This user is already in this group"
    finally:
        mail_service.send_mail = original
    assert sent == []
    assert await _invite_count(factory) == 0
    assert await _notifications(factory) == []


async def _email_invite_rejects_pending_duplicate(factory: async_sessionmaker) -> None:
    """A second email invite to an address that already has an active (pending)
    invite is refused; revoking the pending one re-opens the invitation
    (§2.10)."""
    await _mk_users(factory, (ALICE,))
    await _mk_group(factory, GROUP_ID, ALICE, (ALICE,))
    sent: list[str] = []
    original = mail_service.send_mail

    async def recorder(to: str, subject: str, text: str, html: str | None = None) -> None:
        sent.append(to)

    mail_service.send_mail = recorder
    try:
        # The first invite to an unknown address succeeds (email-only, no bell).
        async with factory() as db:
            group = await db.get(Group, GROUP_ID)
            alice = await db.get(User, ALICE)
            assert group is not None and alice is not None
            first = await invites_service.create_invite(
                db, group, alice, "email", "stranger@test.local"
            )
        # A duplicate pending invite to the same address is refused.
        async with factory() as db:
            group = await db.get(Group, GROUP_ID)
            alice = await db.get(User, ALICE)
            assert group is not None and alice is not None
            with pytest.raises(Conflict) as excinfo:
                await invites_service.create_invite(
                    db, group, alice, "email", "stranger@test.local"
                )
        assert str(excinfo.value) == "This user is already invited to this group"
        assert await _invite_count(factory) == 1  # only the first one
        # Revoking the pending invite re-opens the invitation.
        async with factory() as db:
            group = await db.get(Group, GROUP_ID)
            alice = await db.get(User, ALICE)
            assert group is not None and alice is not None
            await invites_service.revoke_invite(db, group, alice, first.id)
            second = await invites_service.create_invite(
                db, group, alice, "email", "stranger@test.local"
            )
        assert second.id != first.id
    finally:
        mail_service.send_mail = original
    assert sent == ["stranger@test.local", "stranger@test.local"]
    assert await _invite_count(factory) == 2


async def _email_invite_mail_follows_recipient_language(factory: async_sessionmaker) -> None:
    """§2.13: the invite email follows the recipient's preferred language.

    An account holder who prefers Russian gets a Russian mail; an address with
    no account falls back to English (the same default as the reminder and
    close-summary mails). Regression for finding F3 (invite mail was
    hard-coded to EN).
    """
    await _mk_users(factory, (ALICE, BOB))
    # bob (u2@test.local) prefers Russian.
    async with factory() as db:
        bob = await db.get(User, BOB)
        assert bob is not None
        bob.preferred_language = "ru"
        await db.commit()
    await _mk_group(factory, GROUP_ID, ALICE, (ALICE,))
    sent: list[tuple[str, str, str, str | None]] = []
    original = mail_service.send_mail

    async def recorder(to: str, subject: str, text: str, html: str | None = None) -> None:
        sent.append((to, subject, text, html))

    mail_service.send_mail = recorder
    try:
        async with factory() as db:
            group = await db.get(Group, GROUP_ID)
            alice = await db.get(User, ALICE)
            assert group is not None and alice is not None
            # bob (u2@test.local) has an account and prefers Russian → RU mail.
            await invites_service.create_invite(db, group, alice, "email", "u2@test.local")
            # stranger@test.local has no account → English fallback mail.
            await invites_service.create_invite(db, group, alice, "email", "stranger@test.local")
    finally:
        mail_service.send_mail = original
    assert [m[0] for m in sent] == ["u2@test.local", "stranger@test.local"]
    bob_mail, stranger_mail = sent
    # RU: the subject and body carry the Russian invite strings.
    assert "пригласили" in bob_mail[1]
    assert "пригласил(а)" in bob_mail[2]
    # EN fallback for the no-account address.
    assert "invited you" in stranger_mail[1]
    assert "invited you to join" in stranger_mail[2]


# --- close / reopen write-blocking (§2.9) ------------------------------------


def _expense_data() -> ExpenseCreate:
    return ExpenseCreate(
        payer_id=ALICE,
        description="Dinner",
        amount=Decimal("100.00"),
        split_method="equal",
        category="food",
        date=dt.date(2026, 1, 1),
        comment="",
        participants=[ParticipantIn(user_id=ALICE), ParticipantIn(user_id=BOB)],
    )


async def _close_blocks_writes_and_reopen_restores(factory: async_sessionmaker) -> None:
    """§2.9: while closed, new expenses and receipt uploads are 409s;
    reopening restores them. (The owner/member permission matrix and the
    double-close / reopen-active conflicts live in test_reminders.)"""
    await _mk_users(factory, (ALICE, BOB))
    await _mk_group(factory, GROUP_ID, ALICE, (ALICE, BOB))
    async with factory() as db:
        group = await db.get(Group, GROUP_ID)
        alice = await db.get(User, ALICE)
        assert group is not None and alice is not None
        await expenses_service.create_expense(db, group, alice, _expense_data())

    emails: list[str] = []

    async def recorder(to: str, subject: str, text: str, html: str | None = None) -> None:
        emails.append(to)

    original = mail_service.send_mail
    mail_service.send_mail = recorder
    try:
        async with factory() as db:
            group = await db.get(Group, GROUP_ID)
            alice = await db.get(User, ALICE)
            assert group is not None and alice is not None
            closed = await groups_service.close_group(db, group, alice)
            assert closed.status == "closed"
            assert closed.closed_at is not None
        # One final summary mail per member (§2.9).
        assert sorted(emails) == ["u1@test.local", "u2@test.local"]
        # While closed: no new expenses, no receipt uploads (§2.9).
        async with factory() as db:
            group = await db.get(Group, GROUP_ID)
            alice = await db.get(User, ALICE)
            assert group is not None and alice is not None
            with pytest.raises(Conflict):
                await expenses_service.create_expense(db, group, alice, _expense_data())
            expense = await expenses_service.get_expense(db, 1)
            with pytest.raises(Conflict):
                await expenses_service.upload_receipt(
                    db, expense, alice, "r.png", "image/png", b"x"
                )
        # Reopen restores writes and sends no mail (§2.9).
        async with factory() as db:
            group = await db.get(Group, GROUP_ID)
            alice = await db.get(User, ALICE)
            assert group is not None and alice is not None
            reopened = await groups_service.reopen_group(db, group, alice)
            assert reopened.status == "active"
            assert reopened.closed_at is None
            await expenses_service.create_expense(db, group, alice, _expense_data())
        assert sorted(emails) == ["u1@test.local", "u2@test.local"]
    finally:
        mail_service.send_mail = original


# --- entry points ------------------------------------------------------------


def test_create_group_does_not_notify_creator(run_db) -> None:
    run_db(_creator_is_not_self_notified)


def test_invite_joiner_not_notified_about_own_join(run_db) -> None:
    run_db(_joiner_is_not_notified_about_own_join)


def test_email_invite_bell_notifies_existing_user(run_db) -> None:
    run_db(_email_invite_bell_notifies_existing_user)


def test_email_invite_no_bell_for_unknown_email(run_db) -> None:
    run_db(_email_invite_no_bell_for_unknown_email)


def test_email_invite_rejects_existing_member(run_db) -> None:
    run_db(_email_invite_rejects_existing_member)


def test_email_invite_rejects_pending_duplicate(run_db) -> None:
    run_db(_email_invite_rejects_pending_duplicate)


def test_close_blocks_writes_and_reopen_restores(run_db) -> None:
    run_db(_close_blocks_writes_and_reopen_restores)


def test_email_invite_mail_follows_recipient_language(run_db) -> None:
    run_db(_email_invite_mail_follows_recipient_language)
