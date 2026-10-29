"""The balance-snapshot cache (AGENTS.md §2.2 / §3.2, multi-replica).

Exercises the real service path against the test DB **and** a fakeredis
client in the same event loop (via ``bus_util.with_test_db``): a cache miss
computes and stores the wire dict, a hit returns it without recomputing, and
``refresh_snapshot_cache`` (what the post-commit emit path calls) invalidates
it. Money stays correct on the wire either way.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fakeredis import FakeAsyncRedis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import redis_client
from app.models.group import Group
from app.models.membership import Membership
from app.models.user import User
from app.schemas.expenses import ExpenseCreate, ParticipantIn
from app.services import balances as balances_service
from app.services import expenses as expenses_service
from app.services import groups as groups_service
from tests import bus_util

ALICE, BOB = 1, 2


def _expense(payer: int, amount: Decimal, description: str) -> ExpenseCreate:
    return ExpenseCreate(
        payer_id=payer,
        description=description,
        amount=amount,
        split_method="equal",
        date=date(2026, 10, 26),
        participants=[ParticipantIn(user_id=ALICE), ParticipantIn(user_id=BOB)],
    )


def _bal(wire: dict, uid: int) -> str:
    return next(row["balance"] for row in wire["balances"] if row["user_id"] == uid)


@pytest.fixture(autouse=True)
def _clean():
    redis_client.reset_redis()
    yield
    redis_client.reset_redis()


async def test_snapshot_cache_hit_and_refresh() -> None:
    client = FakeAsyncRedis(decode_responses=True)
    redis_client.set_redis(client)

    async def scenario(factory: async_sessionmaker[AsyncSession]) -> None:
        async with factory() as db:
            alice = User(id=ALICE, email="a@t.local", password_hash="x", display_name="Alice")
            bob = User(id=BOB, email="b@t.local", password_hash="x", display_name="Bob")
            db.add_all([alice, bob])
            await db.commit()
            group = await groups_service.create_group(db, alice, "Trip", "USD")
            db.add(Membership(group_id=group.id, user_id=BOB))
            await db.commit()
            gid = group.id

        # expense A: alice pays 100, split equal → alice +50, bob -50
        async with factory() as db:
            group = await db.get(Group, gid)
            payer = await db.get(User, ALICE)
            assert group is not None and payer is not None
            await expenses_service.create_expense(
                db, group, payer, _expense(ALICE, Decimal("100.00"), "A")
            )

        # first load: a miss — computes and stores the wire dict
        async with factory() as db:
            group = await db.get(Group, gid)
            assert group is not None
            wire_a = await balances_service.load_balances_wire(db, group)
        assert _bal(wire_a, ALICE) == "50.00"
        assert _bal(wire_a, BOB) == "-50.00"
        assert await client.get(f"easymoney:snapshot:{gid}") is not None

        # expense B: bob pays 100 → net 0,0 — but the cache is NOT refreshed
        async with factory() as db:
            group = await db.get(Group, gid)
            payer = await db.get(User, BOB)
            assert group is not None and payer is not None
            await expenses_service.create_expense(
                db, group, payer, _expense(BOB, Decimal("100.00"), "B")
            )

        # second load without a refresh: a hit returns the stale wire
        async with factory() as db:
            group = await db.get(Group, gid)
            assert group is not None
            wire_stale = await balances_service.load_balances_wire(db, group)
        assert wire_stale == wire_a
        assert _bal(wire_stale, ALICE) == "50.00"  # stale, as expected from a hit

        # the emit path's job: refresh the cache with a fresh snapshot
        async with factory() as db:
            group = await db.get(Group, gid)
            assert group is not None
            fresh = balances_service.snapshot_to_wire(
                await balances_service.group_balance_snapshot(db, group)
            )
            await balances_service.refresh_snapshot_cache(gid, fresh)
        assert _bal(fresh, ALICE) == "0.00"

        # third load: a hit now returns the refreshed wire
        async with factory() as db:
            group = await db.get(Group, gid)
            assert group is not None
            wire_fresh = await balances_service.load_balances_wire(db, group)
        assert wire_fresh == fresh
        assert _bal(wire_fresh, ALICE) == "0.00"
        assert _bal(wire_fresh, BOB) == "0.00"

    await bus_util.with_test_db(scenario)
    await client.aclose()


async def test_load_wire_local_mode_computes() -> None:
    # no redis: load_balances_wire still returns a correct wire (no cache)
    assert redis_client.get_redis() is None

    async def scenario(factory: async_sessionmaker[AsyncSession]) -> None:
        async with factory() as db:
            alice = User(id=ALICE, email="a@t.local", password_hash="x", display_name="Alice")
            bob = User(id=BOB, email="b@t.local", password_hash="x", display_name="Bob")
            db.add_all([alice, bob])
            await db.commit()
            group = await groups_service.create_group(db, alice, "Trip", "USD")
            db.add(Membership(group_id=group.id, user_id=BOB))
            await db.commit()
            gid = group.id

        async with factory() as db:
            group = await db.get(Group, gid)
            payer = await db.get(User, ALICE)
            assert group is not None and payer is not None
            await expenses_service.create_expense(
                db, group, payer, _expense(ALICE, Decimal("100.00"), "A")
            )

        async with factory() as db:
            group = await db.get(Group, gid)
            assert group is not None
            wire = await balances_service.load_balances_wire(db, group)
        assert _bal(wire, ALICE) == "50.00"
        assert _bal(wire, BOB) == "-50.00"

    await bus_util.with_test_db(scenario)
