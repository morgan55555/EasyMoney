"""AGENTS.md §2.4: settlement lifecycle, permissions, validation and races.

Scenarios run against the test DB (fresh schema per scenario) and exercise the
service layer directly; the domain-error -> HTTP mapping (403/409/422) is the
router wiring in main.py plus the B7 API walkthrough.

Covered:
  * proposals validate against the current pairwise view — 422 when the
    sender owes nothing to the recipient, exceeds the debt, names a
    non-member or themselves, or is finer than the currency's minor unit;
  * only the recipient confirms/rejects, only the sender cancels (Forbidden);
  * transitions are terminal — any second transition is a Conflict (409),
    including the two-concurrent-confirms race (exactly one wins);
  * pending settlements have no balance effect, confirmed ones do (Σ = 0);
  * feed rows + notifications to exactly the two parties involved;
  * closed groups still accept settlements (§2.9).
"""

from __future__ import annotations

import asyncio
import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.errors import Conflict, Forbidden, ValidationFailed
from app.models.activity import ActivityEvent
from app.models.expense import Expense
from app.models.expense_participant import ExpenseParticipant
from app.models.group import Group
from app.models.membership import Membership
from app.models.notification import Notification
from app.models.settlement import Settlement
from app.models.user import User
from app.schemas.settlements import SettlementCreate
from app.services import balances as balances_service
from app.services import settlements as settlements_service

ALICE, BOB, CAROL = 1, 2, 3
GROUP_ID = 1


async def _setup_group(factory: async_sessionmaker):
    """Group 1 (EUR, owner alice) with three members and one equal expense of
    100.00 paid by alice (shares 33.34/33.33/33.33 in join order).

    Balances: alice +66.66, bob -33.33, carol -33.33; the pairwise view has
    exactly the pairs bob -> alice 33.33 and carol -> alice 33.33.
    """
    async with factory() as db:
        users: dict[int, User] = {}
        for uid in (ALICE, BOB, CAROL):
            user = User(
                id=uid,
                email=f"u{uid}@test.local",
                password_hash="not-a-real-hash",
                display_name=f"User {uid}",
            )
            db.add(user)
            users[uid] = user
        group = Group(id=GROUP_ID, name="Settlements", currency="EUR", created_by=ALICE)
        db.add(group)
        for uid in (ALICE, BOB, CAROL):
            db.add(Membership(group_id=GROUP_ID, user_id=uid))
        expense = Expense(
            group_id=GROUP_ID,
            payer_id=ALICE,
            created_by=ALICE,
            description="dinner",
            amount=Decimal("100.00"),
            split_method="equal",
            category="misc",
            date=dt.date(2026, 1, 1),
        )
        db.add(expense)
        await db.flush()
        for uid, share in ((ALICE, "33.34"), (BOB, "33.33"), (CAROL, "33.33")):
            db.add(ExpenseParticipant(expense_id=expense.id, user_id=uid, share=Decimal(share)))
        await db.commit()
        return group, users


async def _proposal_scenario(factory: async_sessionmaker) -> None:
    group, users = await _setup_group(factory)
    async with factory() as db:
        # self, non-member, no debt in that direction, over the debt, sub-minor
        bad = [
            (BOB, BOB, "10.00"),
            (BOB, 99, "10.00"),
            (BOB, CAROL, "10.00"),
            (BOB, ALICE, "33.34"),
            (BOB, ALICE, "10.001"),
        ]
        for frm, to, amount in bad:
            with pytest.raises(ValidationFailed):
                await settlements_service.create_settlement(
                    db, group, users[frm],
                    SettlementCreate(to_user_id=to, amount=Decimal(amount)),
                )
        # the exact debt and partial debts are accepted
        full = await settlements_service.create_settlement(
            db, group, users[BOB], SettlementCreate(to_user_id=ALICE, amount=Decimal("33.33"))
        )
        assert full.state == "pending"
        partial = await settlements_service.create_settlement(
            db, group, users[CAROL], SettlementCreate(to_user_id=ALICE, amount=Decimal("1.00"))
        )
        assert partial.state == "pending"
        # pending settlements do not shrink the cap: balances are unchanged
        again = await settlements_service.create_settlement(
            db, group, users[CAROL], SettlementCreate(to_user_id=ALICE, amount=Decimal("33.33"))
        )
        assert again.state == "pending"
        # closed groups still accept settlements (§2.9)
        group.status = "closed"
        await db.commit()
        ok = await settlements_service.create_settlement(
            db, group, users[BOB], SettlementCreate(to_user_id=ALICE, amount=Decimal("5.00"))
        )
        assert ok.state == "pending"


async def _lifecycle_scenario(factory: async_sessionmaker) -> None:
    group, users = await _setup_group(factory)
    async with factory() as db:
        s = await settlements_service.create_settlement(
            db, group, users[BOB], SettlementCreate(to_user_id=ALICE, amount=Decimal("33.33"))
        )
        # only the recipient confirms/rejects; only the sender cancels
        with pytest.raises(Forbidden):
            await settlements_service.confirm_settlement(db, s, users[BOB])
        with pytest.raises(Forbidden):
            await settlements_service.reject_settlement(db, s, users[CAROL])
        with pytest.raises(Forbidden):
            await settlements_service.cancel_settlement(db, s, users[ALICE])
        # the recipient confirms
        confirmed = await settlements_service.confirm_settlement(db, s, users[ALICE])
        assert confirmed.state == "confirmed"
        assert confirmed.confirmed_at is not None
        # terminal: a party of the settlement gets a 409 on every further
        # transition
        for fn, actor in (
            (settlements_service.confirm_settlement, users[ALICE]),
            (settlements_service.reject_settlement, users[ALICE]),
            (settlements_service.cancel_settlement, users[BOB]),
        ):
            with pytest.raises(Conflict):
                await fn(db, confirmed, actor)
        # a third party gets a 403 instead (permission is checked first)
        with pytest.raises(Forbidden):
            await settlements_service.confirm_settlement(db, confirmed, users[CAROL])


async def _reject_cancel_scenario(factory: async_sessionmaker) -> None:
    group, users = await _setup_group(factory)
    async with factory() as db:
        s1 = await settlements_service.create_settlement(
            db, group, users[BOB], SettlementCreate(to_user_id=ALICE, amount=Decimal("33.33"))
        )
        rejected = await settlements_service.reject_settlement(db, s1, users[ALICE])
        assert rejected.state == "rejected"
        with pytest.raises(Conflict):
            await settlements_service.confirm_settlement(db, rejected, users[ALICE])
        s2 = await settlements_service.create_settlement(
            db, group, users[CAROL], SettlementCreate(to_user_id=ALICE, amount=Decimal("33.33"))
        )
        cancelled = await settlements_service.cancel_settlement(db, s2, users[CAROL])
        assert cancelled.state == "cancelled"
        with pytest.raises(Conflict):
            await settlements_service.reject_settlement(db, cancelled, users[ALICE])


async def _balance_effect_scenario(factory: async_sessionmaker) -> None:
    group, users = await _setup_group(factory)
    async with factory() as db:
        before = await balances_service.group_balances(db, group)
        assert before == {
            ALICE: Decimal("66.66"),
            BOB: Decimal("-33.33"),
            CAROL: Decimal("-33.33"),
        }
        s = await settlements_service.create_settlement(
            db, group, users[BOB], SettlementCreate(to_user_id=ALICE, amount=Decimal("33.33"))
        )
        # pending: no balance effect
        during = await balances_service.group_balances(db, group)
        assert during == before
        await settlements_service.confirm_settlement(db, s, users[ALICE])
        # bob's 33.33 pays off his debt: his balance goes to 0 and the same
        # amount falls off alice's claim (66.66 - 33.33 = 33.33)
        after = await balances_service.group_balances(db, group)
        assert after == {
            ALICE: Decimal("33.33"),
            BOB: Decimal("0.00"),
            CAROL: Decimal("-33.33"),
        }
        assert sum(after.values()) == Decimal(0)


async def _race_scenario(factory: async_sessionmaker) -> None:
    group, users = await _setup_group(factory)
    async with factory() as db:
        s = await settlements_service.create_settlement(
            db, group, users[BOB], SettlementCreate(to_user_id=ALICE, amount=Decimal("33.33"))
        )
        sid = s.id
    # two independent sessions confirm the same pending settlement at once;
    # the WHERE state='pending' guard lets exactly one through (§2.8)
    async with factory() as db_a, factory() as db_b:
        sa = await db_a.get(Settlement, sid)
        sb = await db_b.get(Settlement, sid)
        assert sa is not None and sb is not None
        results = await asyncio.gather(
            settlements_service.confirm_settlement(db_a, sa, users[ALICE]),
            settlements_service.confirm_settlement(db_b, sb, users[ALICE]),
            return_exceptions=True,
        )
    wins = [r for r in results if isinstance(r, Settlement)]
    conflicts = [r for r in results if isinstance(r, Conflict)]
    assert len(wins) == 1 and len(conflicts) == 1
    assert wins[0].state == "confirmed"
    async with factory() as db:
        final = await db.get(Settlement, sid)
        assert final is not None
        assert final.state == "confirmed"
        group_row = await db.get(Group, GROUP_ID)
        assert group_row is not None
        bals = await balances_service.group_balances(db, group_row)
        # the money moved exactly once: bob paid 33.33, so his balance is 0
        # and alice's claim fell from 66.66 to 33.33
        assert bals[ALICE] == Decimal("33.33")
        assert bals[BOB] == Decimal("0.00")
        assert sum(bals.values()) == Decimal(0)


async def _feed_notifications_scenario(factory: async_sessionmaker) -> None:
    group, users = await _setup_group(factory)
    async with factory() as db:
        s = await settlements_service.create_settlement(
            db, group, users[BOB], SettlementCreate(to_user_id=ALICE, amount=Decimal("33.33"))
        )
        await settlements_service.confirm_settlement(db, s, users[ALICE])
        feed = (
            (
                await db.execute(
                    select(ActivityEvent)
                    .where(
                        ActivityEvent.ref_type == "settlement",
                        ActivityEvent.ref_id == s.id,
                    )
                    .order_by(ActivityEvent.id)
                )
            )
            .scalars()
            .all()
        )
        assert [e.type for e in feed] == ["settlement.created", "settlement.confirmed"]
        assert feed[0].payload["state"] == "pending"
        assert feed[1].payload["state"] == "confirmed"
        assert feed[1].payload["amount"] == "33.33"
        # exactly the two parties are notified, on both events
        rows = (
            (
                await db.execute(
                    select(Notification.user_id, Notification.type).order_by(Notification.id)
                )
            )
            .all()
        )
        per_user: dict[int, set[str]] = {}
        for uid, ntype in rows:
            per_user.setdefault(uid, set()).add(ntype)
        assert per_user[BOB] == {"settlement.created", "settlement.confirmed"}
        assert per_user[ALICE] == {"settlement.created", "settlement.confirmed"}
        assert CAROL not in per_user


def test_proposal_validation(run_db):
    run_db(_proposal_scenario)


def test_lifecycle_permissions_and_terminality(run_db):
    run_db(_lifecycle_scenario)


def test_reject_and_cancel(run_db):
    run_db(_reject_cancel_scenario)


def test_pending_has_no_balance_effect_confirmed_does(run_db):
    run_db(_balance_effect_scenario)


def test_two_confirms_one_wins(run_db):
    run_db(_race_scenario)


def test_feed_and_notifications(run_db):
    run_db(_feed_notifications_scenario)
