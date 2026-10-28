"""AGENTS.md §2.6: a user is never notified about their own action.

  * creating a group does not bell-notify the creator (they are the only
    member, and the notification is theirs);
  * joining via a link invite does not bell-notify the joiner — only the
    existing members receive ``member.joined``.

Both scenarios run against the test DB (fresh schema per scenario).
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.group import Group
from app.models.invitation import Invitation
from app.models.membership import Membership
from app.models.notification import Notification
from app.models.user import User
from app.services import groups as groups_service
from app.services import invites as invites_service

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


# --- entry points ------------------------------------------------------------


def test_create_group_does_not_notify_creator(run_db) -> None:
    run_db(_creator_is_not_self_notified)


def test_invite_joiner_not_notified_about_own_join(run_db) -> None:
    run_db(_joiner_is_not_notified_about_own_join)
