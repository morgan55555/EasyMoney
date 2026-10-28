"""AGENTS.md §3.3: the activity feed reader.

Covers the keyset pagination contract: ``(created_at, id)`` descending
(ordering is deterministic even when timestamps collide, because the id
tie-break follows commit order), opaque cursors, per-group isolation, and the
soft-delete rule (deleted expenses stay in the feed as their
``expense.deleted`` row).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.errors import ValidationFailed
from app.models.group import Group
from app.models.membership import Membership
from app.models.user import User
from app.schemas.expenses import ExpenseCreate, ParticipantIn
from app.services import expenses as expenses_service
from app.services import feed as feed_service
from app.services import groups as groups_service

ALICE, BOB = 1, 2
GROUP_ID = 1


async def _setup(factory: async_sessionmaker, group_count: int = 1) -> dict[int, User]:
    """Users 1..2 plus ``group_count`` EUR groups (owner: user 1).

    Bob's memberships are staged directly (no ``member.joined`` feed rows),
    so each group's feed starts with exactly ``group.created`` +
    ``member.joined`` (the creator) — the same shape as a fresh group.
    """
    users: dict[int, User] = {}
    async with factory() as db:
        for uid in (ALICE, BOB):
            user = User(
                id=uid,
                email=f"u{uid}@test.local",
                password_hash="not-a-real-hash",
                display_name=f"User {uid}",
            )
            db.add(user)
            users[uid] = user
        for gid in range(1, group_count + 1):
            await groups_service.create_group(db, users[ALICE], f"Feed {gid}", "EUR")
        for gid in range(1, group_count + 1):
            db.add(Membership(group_id=gid, user_id=BOB))
        await db.commit()
    return users


async def _one_expense(factory: async_sessionmaker, users: dict[int, User]) -> None:
    """One equal-split expense paid by alice, covering both members."""
    data = ExpenseCreate(
        payer_id=ALICE,
        description="dinner",
        amount=Decimal("10.00"),
        split_method="equal",
        category="food",
        date=dt.date(2026, 10, 1),
        participants=[ParticipantIn(user_id=ALICE), ParticipantIn(user_id=BOB)],
    )
    async with factory() as db:
        group = await db.get(Group, GROUP_ID)
        assert group is not None
        expense = await expenses_service.create_expense(db, group, users[ALICE], data)
        await expenses_service.delete_expense(db, expense, users[ALICE])


async def _scenario_ordering_and_pagination(factory: async_sessionmaker) -> None:
    users = await _setup(factory)
    await _one_expense(factory, users)

    async with factory() as db:
        events, cursor = await feed_service.list_events(db, GROUP_ID)
        types = [e.type for e in events]
        # create_group writes group.created + member.joined; the expense adds
        # expense.created and its deletion the expense.deleted row that stays.
        assert types == ["expense.deleted", "expense.created", "member.joined", "group.created"]
        assert cursor is None  # 4 rows < default page size of 30
        assert events[0].actor is not None and events[0].actor_id == ALICE
        deleted = events[0]
        assert deleted.ref_type == "expense" and deleted.ref_id is not None
        assert deleted.payload is not None and deleted.payload["description"] == "dinner"

        # limit=100 is clamped, still one page
        events, cursor = await feed_service.list_events(db, GROUP_ID, limit=100)
        assert len(events) == 4 and cursor is None

        # page through with limit=3: 3 + 1, no overlap, same order overall
        page1, cursor = await feed_service.list_events(db, GROUP_ID, limit=3)
        assert [e.type for e in page1] == ["expense.deleted", "expense.created", "member.joined"]
        assert cursor is not None
        page2, cursor2 = await feed_service.list_events(db, GROUP_ID, cursor=cursor, limit=3)
        assert [e.type for e in page2] == ["group.created"]
        assert cursor2 is None
        assert {e.id for e in page1} | {e.id for e in page2} == {e.id for e in events}
        assert not ({e.id for e in page1} & {e.id for e in page2})

        # a malformed cursor is a client error, not a 500
        try:
            await feed_service.list_events(db, GROUP_ID, cursor="!!!not-base64!!!")
            raise AssertionError("expected ValidationFailed")
        except ValidationFailed:
            pass


async def _scenario_per_group_isolation(factory: async_sessionmaker) -> None:
    users = await _setup(factory, group_count=2)
    await _one_expense(factory, users)

    async with factory() as db:
        other_group_id = 2
        events, _ = await feed_service.list_events(db, GROUP_ID)
        assert {e.group_id for e in events} == {GROUP_ID}
        other, _ = await feed_service.list_events(db, other_group_id)
        # group 2 has only its create rows (the expense went to group 1)
        assert [e.type for e in other] == ["member.joined", "group.created"]


def test_feed_ordering_and_pagination(run_db):
    run_db(_scenario_ordering_and_pagination)


def test_feed_per_group_isolation(run_db):
    run_db(_scenario_per_group_isolation)
