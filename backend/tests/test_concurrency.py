"""AGENTS.md §2.8 / req 21: concurrent expense creation.

Members adding expenses "simultaneously" — parallel tasks, independent
sessions, no shared state — must all commit: balances are aggregates
(invariant 4), so there is no shared mutable row to lock and no optimistic
locking is needed. Every task builds its group/user references from its own
session, mirroring two real client requests.

Covered:
  * two parallel adds (different payers) → both present, two feed rows,
    each expense's stored shares sum to its amount exactly (invariant 2),
    Σ balances = 0 with the exact per-user values;
  * a stress batch of 8 parallel adds over 3 members → all 8 recorded,
    shares still exact, Σ balances = 0 (exact per-user values asserted).
"""

from __future__ import annotations

import asyncio
import datetime as dt
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.activity import ActivityEvent
from app.models.expense import Expense
from app.models.expense_participant import ExpenseParticipant
from app.models.group import Group
from app.models.membership import Membership
from app.models.user import User
from app.schemas.expenses import ExpenseCreate, ParticipantIn
from app.services import balances as balances_service
from app.services import expenses as expenses_service

ALICE, BOB, CAROL = 1, 2, 3
GROUP_ID = 1


async def _setup_users_group(factory: async_sessionmaker, members: tuple[int, ...]) -> None:
    async with factory() as db:
        for uid in members:
            db.add(
                User(
                    id=uid,
                    email=f"u{uid}@test.local",
                    password_hash="not-a-real-hash",
                    display_name=f"User {uid}",
                )
            )
        db.add(Group(id=GROUP_ID, name="Concurrent", currency="EUR", created_by=ALICE))
        for uid in members:
            db.add(Membership(group_id=GROUP_ID, user_id=uid))
        await db.commit()


async def _add_expense(db: async_sessionmaker, user: User, data: ExpenseCreate) -> Expense:
    """One create_expense call on its own session (the unit of parallel work).

    The group is loaded from the caller's session, so the tasks share no
    ORM state — each one behaves like an independent HTTP request.
    """
    group = await db.get(Group, GROUP_ID)
    assert group is not None
    return await expenses_service.create_expense(db, group, user, data)


async def _assert_shares_and_balances(
    factory: async_sessionmaker, expected: dict[int, Decimal]
) -> None:
    """Invariants after the concurrent adds: exact shares, Σ balances = 0."""
    async with factory() as db:
        rows = (
            (
                await db.execute(
                    select(Expense).where(
                        Expense.group_id == GROUP_ID, Expense.deleted_at.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )
        amounts = {r.id: r.amount for r in rows}
        share_sums = (
            await db.execute(
                select(ExpenseParticipant.expense_id, func.sum(ExpenseParticipant.share)).group_by(
                    ExpenseParticipant.expense_id
                )
            )
        ).all()
        assert {expense_id for expense_id, _ in share_sums} == set(amounts)
        for expense_id, total in share_sums:
            assert total == amounts[expense_id]
        group = await db.get(Group, GROUP_ID)
        assert group is not None
        bals = await balances_service.group_balances(db, group)
    assert bals == expected
    assert sum(bals.values()) == Decimal(0)


async def _two_parallel_adds(factory: async_sessionmaker) -> None:
    await _setup_users_group(factory, (ALICE, BOB))
    data_a = ExpenseCreate(
        payer_id=ALICE,
        description="lunch",
        amount=Decimal("100.00"),
        split_method="equal",
        category="misc",
        date=dt.date(2026, 10, 1),
        participants=[ParticipantIn(user_id=ALICE), ParticipantIn(user_id=BOB)],
    )
    data_b = ExpenseCreate(
        payer_id=BOB,
        description="taxi",
        amount=Decimal("33.33"),
        split_method="equal",
        category="misc",
        date=dt.date(2026, 10, 1),
        participants=[ParticipantIn(user_id=ALICE), ParticipantIn(user_id=BOB)],
    )
    async with factory() as db_a, factory() as db_b:
        user_a = await db_a.get(User, ALICE)
        user_b = await db_b.get(User, BOB)
        assert user_a is not None and user_b is not None
        results = await asyncio.gather(
            _add_expense(db_a, user_a, data_a),
            _add_expense(db_b, user_b, data_b),
            return_exceptions=True,
        )
    assert not any(isinstance(r, Exception) for r in results), results
    expense_a, expense_b = results
    assert expense_a.id != expense_b.id
    async with factory() as db:
        feed = (
            (
                await db.execute(
                    select(ActivityEvent.type)
                    .where(ActivityEvent.group_id == GROUP_ID)
                    .order_by(ActivityEvent.id)
                )
            )
            .scalars()
            .all()
        )
        assert feed == ["expense.created", "expense.created"]
    # alice paid 100.00 (her 50.00 half → +50.00) and takes the earliest-
    # joiner share of 16.67 on the 33.33 split; bob mirrors (+16.67/−50.00)
    await _assert_shares_and_balances(
        factory, {ALICE: Decimal("33.33"), BOB: Decimal("-33.33")}
    )


async def _stress_batch_of_eight(factory: async_sessionmaker) -> None:
    await _setup_users_group(factory, (ALICE, BOB, CAROL))
    payers = (ALICE, BOB, CAROL)
    datas = [
        ExpenseCreate(
            payer_id=payers[i % 3],
            description=f"order {i}",
            amount=Decimal("10.01"),
            split_method="equal",
            category="misc",
            date=dt.date(2026, 10, 2),
            participants=[ParticipantIn(user_id=uid) for uid in (ALICE, BOB, CAROL)],
        )
        for i in range(8)
    ]

    async def run_one(i: int) -> Expense:
        async with factory() as db:
            user = await db.get(User, payers[i % 3])
            assert user is not None
            return await _add_expense(db, user, datas[i])

    results = await asyncio.gather(*(run_one(i) for i in range(8)), return_exceptions=True)
    assert not any(isinstance(r, Exception) for r in results), results
    assert len({r.id for r in results}) == 8
    # alice and bob each pay 3 of the 8 orders (30.03) and take the
    # earliest-joiner share 3.34 on all 8 (26.72); carol pays 2 (20.02) and
    # takes 3.33 × 8 = 26.64: +3.31 / +3.31 / −6.62, summing to 0
    await _assert_shares_and_balances(
        factory,
        {ALICE: Decimal("3.31"), BOB: Decimal("3.31"), CAROL: Decimal("-6.62")},
    )


def test_two_parallel_adds_both_recorded(run_db):
    run_db(_two_parallel_adds)


def test_stress_batch_of_eight_parallel_adds(run_db):
    run_db(_stress_batch_of_eight)
