"""AGENTS.md §2.2 + invariants 1–2: randomized sequences of expense
creates/edits/deletes plus settlements must keep Σ balances == 0, and every
per-user balance must equal the formula recomputed independently in plain
Python (no ORM, no SQL).

The split shares the scenario writes are generated straight from the spec
(§2.1: earliest-joiner remainder / largest remainder / exact), not from
``app.services.splits`` — the balance aggregate is what is under test here,
and the recomputation below trusts only the recorded shares and
settlements.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import select

from app.models.expense import Expense
from app.models.expense_participant import ExpenseParticipant
from app.models.group import Group
from app.models.membership import Membership
from app.models.settlement import Settlement
from app.models.user import User
from app.services import balances as balances_service

CENT = Decimal("0.01")

# --- spec-level split generation (independent of app.services.splits) --------


def _equal_split(total_cents: int, users: list[int]) -> dict[int, int]:
    """§2.1 equal: base to everyone, the r leftover cents to the r earliest
    joiners (join order = user_id order in the scenarios)."""
    n = len(users)
    base, r = divmod(total_cents, n)
    return {u: base + (1 if i < r else 0) for i, u in enumerate(users)}


def _weighted_split(total_cents: int, users: list[int], weights: dict[int, int]) -> dict[int, int]:
    """§2.1 weighted: floor(A·w/Σw) to everyone, the leftover cents to the
    largest fractional parts (tie-break: earlier joiner)."""
    wsum = sum(weights.values())
    numerator = {u: total_cents * weights[u] for u in users}  # A·w, pre /Σw
    base = {u: numerator[u] // wsum for u in users}
    leftover = total_cents - sum(base.values())
    order = sorted(users, key=lambda u: (-(numerator[u] % wsum), u))
    for u in order[:leftover]:
        base[u] += 1
    return base


def _exact_split(draw, total_cents: int, users: list[int]) -> dict[int, int]:
    """§2.1 exact: random non-negative amounts that sum to the total
    (stars & bars over sorted cut points)."""
    if len(users) == 1:
        return {users[0]: total_cents}
    cuts = sorted(
        draw(
            st.lists(
                st.integers(0, total_cents), min_size=len(users) - 1, max_size=len(users) - 1
            )
        )
    )
    bounds = [0, *cuts, total_cents]
    return {u: bounds[i + 1] - bounds[i] for i, u in enumerate(users)}


# --- scenario model -----------------------------------------------------------


@dataclass(frozen=True)
class ExpenseOp:
    eid: int
    payer: int
    users: tuple[int, ...]
    shares: dict[int, int]  # cents per user; always sums to amount_cents
    amount_cents: int


@dataclass(frozen=True)
class SettlementOp:
    frm: int
    to: int
    amount_cents: int
    confirmed: bool  # pending must not touch balances


@dataclass(frozen=True)
class EditOp:
    eid: int
    new_amount_cents: int
    new_shares: dict[int, int]


@dataclass(frozen=True)
class DeleteOp:
    eid: int


Op = ExpenseOp | SettlementOp | EditOp | DeleteOp


@st.composite
def scenario_strategy(draw):
    """A random (users, ops) sequence over one EUR group.

    The payer is always among the participants, like the real API enforces.
    Edits keep the participant set and re-split equally; deletes are soft.
    """
    users = list(range(1, draw(st.integers(min_value=2, max_value=5)) + 1))
    ops: list[Op] = []
    expenses: list[ExpenseOp] = []  # live (not deleted) expenses
    next_eid = 0
    for _ in range(draw(st.integers(min_value=1, max_value=10))):
        kind = draw(st.sampled_from(["expense", "settlement", "pending", "edit", "delete"]))
        if kind == "expense":
            next_eid += 1
            payer = draw(st.sampled_from(users))
            others = draw(st.lists(st.sampled_from(users), min_size=1, max_size=len(users)))
            part = tuple(sorted(set([payer, *others])))
            amount_cents = draw(st.integers(min_value=1, max_value=100_000))
            method = draw(st.sampled_from(["equal", "weighted", "exact"]))
            if method == "equal":
                shares = _equal_split(amount_cents, list(part))
            elif method == "weighted":
                weights = {u: draw(st.integers(min_value=1, max_value=9)) for u in part}
                shares = _weighted_split(amount_cents, list(part), weights)
            else:
                shares = _exact_split(draw, amount_cents, list(part))
            op = ExpenseOp(next_eid, payer, part, shares, amount_cents)
            ops.append(op)
            expenses.append(op)
        elif kind in ("settlement", "pending"):
            frm = draw(st.sampled_from(users))
            to = draw(st.sampled_from(users))
            if frm == to:
                continue
            amount_cents = draw(st.integers(min_value=1, max_value=5_000))
            ops.append(SettlementOp(frm, to, amount_cents, confirmed=(kind == "settlement")))
        else:
            if not expenses:
                continue
            target = expenses[draw(st.integers(min_value=0, max_value=len(expenses) - 1))]
            if kind == "edit":
                new_amount = draw(st.integers(min_value=1, max_value=100_000))
                ops.append(
                    EditOp(target.eid, new_amount, _equal_split(new_amount, list(target.users)))
                )
            else:
                ops.append(DeleteOp(target.eid))
                expenses.remove(target)
    return users, ops


def recompute_balances(users: list[int], ops: list[Op]) -> dict[int, int]:
    """The §2.2 formula in cents, from the recorded events alone.

    balance = paid − shares + confirmed sent − confirmed received;
    soft-deleted expenses are excluded; pending settlements never count.
    """
    paid: dict[int, int] = dict.fromkeys(users, 0)
    owes: dict[int, int] = dict.fromkeys(users, 0)
    sent: dict[int, int] = dict.fromkeys(users, 0)
    received: dict[int, int] = dict.fromkeys(users, 0)
    live: dict[int, ExpenseOp] = {}

    def add(e: ExpenseOp) -> None:
        paid[e.payer] += e.amount_cents
        for u, s in e.shares.items():
            owes[u] += s

    def remove(e: ExpenseOp) -> None:
        paid[e.payer] -= e.amount_cents
        for u, s in e.shares.items():
            owes[u] -= s

    for op in ops:
        if isinstance(op, ExpenseOp):
            live[op.eid] = op
            add(op)
        elif isinstance(op, EditOp):
            target = live[op.eid]
            remove(target)
            updated = ExpenseOp(
                op.eid, target.payer, target.users, op.new_shares, op.new_amount_cents
            )
            live[op.eid] = updated
            add(updated)
        elif isinstance(op, DeleteOp):
            remove(live[op.eid])
            del live[op.eid]
        elif op.confirmed:
            sent[op.frm] += op.amount_cents
            received[op.to] += op.amount_cents
    return {u: paid[u] - owes[u] + sent[u] - received[u] for u in users}


async def run_scenario(factory, users: list[int], ops: list[Op]):
    """Apply the scenario to the test DB and return
    ``(on-read SQL balances, independent Python recompute)``."""
    async with factory() as db:
        for uid in users:
            db.add(
                User(
                    id=uid,
                    email=f"u{uid}@test.local",
                    password_hash="not-a-real-hash",
                    display_name=f"User {uid}",
                )
            )
        db.add(Group(id=1, name="Scenario", currency="EUR", created_by=users[0]))
        for uid in users:
            db.add(Membership(group_id=1, user_id=uid))
        await db.flush()

        for op in ops:
            if isinstance(op, ExpenseOp):
                db.add(
                    Expense(
                        id=op.eid,
                        group_id=1,
                        payer_id=op.payer,
                        created_by=op.payer,
                        description="s",
                        amount=Decimal(op.amount_cents) * CENT,
                        split_method="equal",
                        category="misc",
                        date=dt.date(2026, 1, 1),
                    )
                )
                for u in op.users:
                    db.add(
                        ExpenseParticipant(
                            expense_id=op.eid,
                            user_id=u,
                            share=Decimal(op.shares[u]) * CENT,
                        )
                    )
            elif isinstance(op, SettlementOp):
                db.add(
                    Settlement(
                        group_id=1,
                        from_user_id=op.frm,
                        to_user_id=op.to,
                        amount=Decimal(op.amount_cents) * CENT,
                        state="confirmed" if op.confirmed else "pending",
                    )
                )
            elif isinstance(op, EditOp):
                expense = await db.get(Expense, op.eid)
                assert expense is not None
                expense.amount = Decimal(op.new_amount_cents) * CENT
                rows = (
                    await db.execute(
                        select(ExpenseParticipant).where(ExpenseParticipant.expense_id == op.eid)
                    )
                ).scalars().all()
                for row in rows:
                    row.share = Decimal(op.new_shares[row.user_id]) * CENT
            else:  # DeleteOp — soft delete
                expense = await db.get(Expense, op.eid)
                assert expense is not None
                expense.deleted_at = dt.datetime.now(dt.UTC)
            # The test factory mirrors production (autoflush off, see
            # conftest.py): staged rows must be flushed before the next op
            # can read them, like the services do.
            await db.flush()
        await db.commit()

        group = await db.get(Group, 1)
        assert group is not None
        db_balances = await balances_service.group_balances(db, group)
        expected = {u: Decimal(c) * CENT for u, c in recompute_balances(users, ops).items()}
        return db_balances, expected


# --- the tests ----------------------------------------------------------------


@given(data=scenario_strategy())
@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[
        HealthCheck.too_slow,
        # run_db is function-scoped, but its _run closure builds a fresh engine
        # and truncates on every call, so each generated example is isolated.
        HealthCheck.function_scoped_fixture,
    ],
)
def test_balances_sum_zero_and_match_formula(run_db, data):
    """Invariant 1 (Σ = 0) and §2.2 (each balance = the formula), for any
    randomized sequence of expense creates/edits/deletes and settlements."""
    users, ops = data
    db_balances, expected = run_db(lambda f: run_scenario(f, users, ops))
    assert sum(db_balances.values()) == Decimal(0)
    assert set(db_balances) == set(users)
    assert db_balances == expected


def test_snapshot_matches_balances_and_settles_debts(run_db):
    """group_balance_snapshot: balances agree with group_balances; the
    suggested transfers settle every debt (apply them → all zero)."""

    async def scenario(factory):
        users = [1, 2, 3]
        # one equal 100.00 expense paid by user 1, all three participants:
        # shares 33.34 / 33.33 / 33.33 → balances +66.66 / −33.33 / −33.33
        ops: list[Op] = [ExpenseOp(1, 1, (1, 2, 3), {1: 3334, 2: 3333, 3: 3333}, 10000)]
        db_balances, _ = await run_scenario(factory, users, ops)
        async with factory() as db:
            group = await db.get(Group, 1)
            assert group is not None
            snap = await balances_service.group_balance_snapshot(db, group)
        return db_balances, snap

    db_balances, snap = run_db(scenario)

    assert snap.currency == "EUR"
    assert {b.user_id: b.balance for b in snap.balances} == db_balances
    assert db_balances == {1: Decimal("66.66"), 2: Decimal("-33.33"), 3: Decimal("-33.33")}
    assert [b.display_name for b in snap.balances] == ["User 1", "User 2", "User 3"]

    assert snap.transfers == (
        balances_service.Transfer(2, 1, Decimal("33.33")),
        balances_service.Transfer(3, 1, Decimal("33.33")),
    )
    assert len(snap.transfers) <= 2  # n - 1 with n = 3 non-zero users
    out = dict(db_balances)
    for t in snap.transfers:
        out[t.from_user_id] += t.amount
        out[t.to_user_id] -= t.amount
    assert out == {u: Decimal(0) for u in out}


def test_all_balances_cross_currency(run_db):
    """§2.2 cross-group view: one row per currency, no conversion; a currency
    with no money at all still shows 0 (user 4 is in both groups but
    participates in no expense)."""

    async def scenario(factory):
        users = [1, 2, 3, 4]
        async with factory() as db:
            for uid in users:
                db.add(
                    User(
                        id=uid,
                        email=f"u{uid}@test.local",
                        password_hash="not-a-real-hash",
                        display_name=f"User {uid}",
                    )
                )
            db.add(Group(id=1, name="EUR", currency="EUR", created_by=1))
            db.add(Group(id=2, name="USD", currency="USD", created_by=1))
            for uid in users:
                db.add(Membership(group_id=1, user_id=uid))
                db.add(Membership(group_id=2, user_id=uid))
            await db.flush()

            def expense(eid, group_id, payer, total_cents):
                base, r = divmod(total_cents, 3)
                db.add(
                    Expense(
                        id=eid,
                        group_id=group_id,
                        payer_id=payer,
                        created_by=payer,
                        description="s",
                        amount=Decimal(total_cents) * CENT,
                        split_method="equal",
                        category="misc",
                        date=dt.date(2026, 1, 1),
                    )
                )
                for i, u in enumerate(users[:3]):  # user 4 takes no part
                    db.add(
                        ExpenseParticipant(
                            expense_id=eid,
                            user_id=u,
                            share=Decimal(base + (1 if i < r else 0)) * CENT,
                        )
                    )

            # EUR: user 1 pays 100.00, split 3 ways → 3334/3333/3333
            # → u1 +66.66, u2 −33.33, u3 −33.33
            expense(1, 1, 1, 10000)
            # USD: user 2 pays 30.00, split 3 ways → 1000/1000/1000
            # → u1 −10.00, u2 +20.00, u3 −10.00
            expense(2, 2, 2, 3000)
            await db.commit()

            out = []
            for uid in users:
                user = await db.get(User, uid)
                assert user is not None
                out.append(await balances_service.all_balances_for_user(db, user))
            return out

    b1, b2, b3, b4 = run_db(scenario)
    assert b1 == {"EUR": Decimal("66.66"), "USD": Decimal("-10.00")}
    assert b2 == {"EUR": Decimal("-33.33"), "USD": Decimal("20.00")}
    assert b3 == {"EUR": Decimal("-33.33"), "USD": Decimal("-10.00")}
    assert b4 == {"EUR": Decimal(0), "USD": Decimal(0)}  # zero-filled rows
    # per-currency totals still sum to zero (invariant 1, cross-group)
    assert sum(b1.values()) + sum(b2.values()) + sum(b3.values()) + sum(b4.values()) == Decimal(0)
