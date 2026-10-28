"""AGENTS.md §2.7: overdue-debt reminders, FIFO aging and group close/reopen.

The pure ``debt_age`` FIFO attribution is tested without a database. The scan
and the close/reopen lifecycle run against the test DB (fresh schema per
scenario) and drive a **fake clock** (the explicit ``now``) and a **fake
mailer** (an injected ``send`` that builds the real email via
``build_reminder_email``), per the AGENTS.md §5 requirement.

Covered:
  * FIFO attribution: partial coverage keeps the oldest date, exact coverage
    of a portion advances to the next, full coverage yields ``None``;
  * the overdue boundary is *strictly* greater than ``DEBT_OVERDUE_DAYS`` —
    exactly N days is not overdue, N days + 1 minute is;
  * the weekly cap: sent at T0, skipped at T0 + 6d23h, allowed at exactly
    T0 + 7d;
  * skip-when-settled: a confirmed settlement that zeroes the balance kills
    the reminder before the scan;
  * the phase-2 re-verify: a settlement confirmed *between* the scan and the
    send kills the reminder for the later candidate;
  * closed groups are still reminded until the debt is settled;
  * close/reopen: owner-only (403), idempotency (409), feed rows, the bell to
    every member, and the final summary mail (unpaid variant lists who owes
    whom; the settled variant says "settled up").
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.errors import Conflict, Forbidden
from app.models.activity import ActivityEvent
from app.models.debt_reminder import DebtReminder
from app.models.expense import Expense
from app.models.expense_participant import ExpenseParticipant
from app.models.group import Group
from app.models.membership import Membership
from app.models.notification import Notification
from app.models.settlement import Settlement
from app.models.user import User
from app.schemas.settlements import SettlementCreate
from app.services import balances as balances_service
from app.services import groups as groups_service
from app.services import mail as mail_service
from app.services import reminders as reminders_service
from app.services import settlements as settlements_service

ALICE, BOB, CAROL, DAVE = 1, 2, 3, 4
GROUP_ID = 1


# --- pure FIFO attribution ---------------------------------------------------


def test_debt_age_fifo() -> None:
    d = dt.date
    owed = [(d(2026, 1, 1), Decimal("10")), (d(2026, 2, 1), Decimal("20"))]
    # zero coverage -> the oldest owed expense
    assert reminders_service.debt_age(owed, Decimal("0")) == d(2026, 1, 1)
    # partial coverage of the first portion -> still the first date
    assert reminders_service.debt_age(owed, Decimal("5")) == d(2026, 1, 1)
    # exact coverage of the first portion -> advances to the second date
    assert reminders_service.debt_age(owed, Decimal("10")) == d(2026, 2, 1)
    # full coverage -> None
    assert reminders_service.debt_age(owed, Decimal("30")) is None
    # more than full coverage -> None
    assert reminders_service.debt_age(owed, Decimal("99")) is None
    # no owed expenses -> None regardless of coverage
    assert reminders_service.debt_age([], Decimal("10")) is None


# --- DB fixtures -------------------------------------------------------------


async def _mk_users(factory: async_sessionmaker, uids: tuple[int, ...]) -> dict[int, User]:
    async with factory() as db:
        out: dict[int, User] = {}
        for uid in uids:
            user = User(
                id=uid,
                email=f"u{uid}@test.local",
                password_hash="not-a-real-hash",
                display_name=f"User {uid}",
            )
            db.add(user)
            out[uid] = user
        await db.commit()
        return out


async def _mk_group(
    factory: async_sessionmaker, gid: int, currency: str, owner: int, members: tuple[int, ...]
) -> None:
    async with factory() as db:
        db.add(Group(id=gid, name=f"Group {gid}", currency=currency, created_by=owner))
        for uid in members:
            db.add(Membership(group_id=gid, user_id=uid))
        await db.commit()


async def _mk_expense(
    factory: async_sessionmaker,
    gid: int,
    payer: int,
    amount: Decimal,
    day: dt.date,
    shares: dict[int, Decimal],
    creator: int | None = None,
) -> int:
    async with factory() as db:
        expense = Expense(
            group_id=gid,
            payer_id=payer,
            created_by=creator or payer,
            description="dinner",
            amount=amount,
            split_method="equal",
            category="misc",
            date=day,
        )
        db.add(expense)
        await db.flush()
        for uid, share in shares.items():
            db.add(ExpenseParticipant(expense_id=expense.id, user_id=uid, share=share))
        await db.commit()
        return expense.id


def _recorder():
    """A fake mailer that builds the real email via ``build_reminder_email``."""
    emails: list[tuple[str, str, str, str]] = []

    async def send(debtor: User, group: Group, snapshot: Any) -> None:
        to, subject, text, html = reminders_service.build_reminder_email(debtor, group, snapshot)
        emails.append((to, subject, text, html))

    return send, emails


async def _scan(
    factory: async_sessionmaker,
    now: dt.datetime,
    send,
    overdue_days: int = 7,
    interval_days: int = 7,
) -> int:
    return await reminders_service.run_reminder_scan(
        factory, now=now, overdue_days=overdue_days, interval_days=interval_days, send=send
    )


# --- overdue boundary --------------------------------------------------------


async def _overdue_boundary(factory: async_sessionmaker) -> None:
    await _mk_users(factory, (ALICE, BOB))
    await _mk_group(factory, GROUP_ID, "EUR", ALICE, (ALICE, BOB))
    await _mk_expense(
        factory, GROUP_ID, ALICE, Decimal("100.00"), dt.date(2026, 10, 20), {BOB: Decimal("100.00")}
    )
    # exactly 7 days old -> NOT overdue (the rule is strictly greater)
    send, emails = _recorder()
    assert await _scan(factory, dt.datetime(2026, 10, 27, 0, 0, tzinfo=dt.UTC), send) == 0
    assert emails == []
    # one minute past -> overdue -> one reminder to bob
    send2, emails2 = _recorder()
    assert await _scan(factory, dt.datetime(2026, 10, 27, 0, 1, tzinfo=dt.UTC), send2) == 1
    assert len(emails2) == 1
    to, subject, text, _ = emails2[0]
    assert to == "u2@test.local"
    assert "Group 1" in subject and "100.00 EUR" in subject
    assert "User 1" in text and "100.00 EUR" in text


# --- weekly cap --------------------------------------------------------------


async def _weekly_cap(factory: async_sessionmaker) -> None:
    await _mk_users(factory, (ALICE, BOB))
    await _mk_group(factory, GROUP_ID, "EUR", ALICE, (ALICE, BOB))
    await _mk_expense(
        factory, GROUP_ID, ALICE, Decimal("100.00"), dt.date(2026, 10, 1), {BOB: Decimal("100.00")}
    )
    t0 = dt.datetime(2026, 10, 10, 0, 0, tzinfo=dt.UTC)
    send, _ = _recorder()
    assert await _scan(factory, t0, send) == 1
    # 6d23h later: the interval has not passed -> skipped
    send1, emails1 = _recorder()
    assert await _scan(factory, t0 + dt.timedelta(days=6, hours=23), send1) == 0
    assert emails1 == []
    # exactly 7d later: the boundary is allowed
    t2 = t0 + dt.timedelta(days=7)
    send2, emails2 = _recorder()
    assert await _scan(factory, t2, send2) == 1
    assert len(emails2) == 1
    async with factory() as db:
        rows = (await db.execute(select(DebtReminder))).scalars().all()
    assert len(rows) == 1
    assert rows[0].debtor_id == BOB
    assert rows[0].last_sent_at == t2


# --- skip when settled (before the scan) -------------------------------------


async def _skip_when_settled(factory: async_sessionmaker) -> None:
    await _mk_users(factory, (ALICE, BOB))
    await _mk_group(factory, GROUP_ID, "EUR", ALICE, (ALICE, BOB))
    await _mk_expense(
        factory, GROUP_ID, ALICE, Decimal("100.00"), dt.date(2026, 10, 1), {BOB: Decimal("100.00")}
    )
    async with factory() as db:
        group = await db.get(Group, GROUP_ID)
        assert group is not None
        s = await settlements_service.create_settlement(
            db,
            group,
            await db.get(User, BOB),
            SettlementCreate(to_user_id=ALICE, amount=Decimal("100.00")),
        )
        await settlements_service.confirm_settlement(db, s, await db.get(User, ALICE))
    # bob's balance is now 0 -> not a candidate -> no mail
    send, emails = _recorder()
    assert await _scan(factory, dt.datetime(2026, 10, 20, tzinfo=dt.UTC), send) == 0
    assert emails == []


# --- phase-2 re-verify -------------------------------------------------------


async def _phase2_reverify(factory: async_sessionmaker) -> None:
    await _mk_users(factory, (ALICE, BOB, CAROL, DAVE))
    await _mk_group(factory, 1, "EUR", ALICE, (ALICE, BOB))
    await _mk_group(factory, 2, "EUR", CAROL, (CAROL, DAVE))
    await _mk_expense(
        factory, 1, ALICE, Decimal("100.00"), dt.date(2026, 10, 1), {BOB: Decimal("100.00")}
    )
    await _mk_expense(
        factory, 2, CAROL, Decimal("100.00"), dt.date(2026, 10, 1), {DAVE: Decimal("100.00")}
    )
    # dave owes carol 100 with a *pending* (unconfirmed) settlement in group 2
    async with factory() as db:
        g2 = await db.get(Group, 2)
        assert g2 is not None
        pend = await settlements_service.create_settlement(
            db,
            g2,
            await db.get(User, DAVE),
            SettlementCreate(to_user_id=CAROL, amount=Decimal("100.00")),
        )
        pend_id = pend.id
    # Reminding bob (group 1, processed first) confirms dave's settlement; the
    # scan must then re-verify dave (group 2) at send time and skip him.
    sent: list[int] = []

    async def send(debtor: User, group: Group, snapshot: Any) -> None:
        sent.append(debtor.id)
        if debtor.id == BOB:
            async with factory() as db2:
                s2 = await db2.get(Settlement, pend_id)
                assert s2 is not None
                await settlements_service.confirm_settlement(db2, s2, await db2.get(User, CAROL))

    n = await reminders_service.run_reminder_scan(
        factory,
        now=dt.datetime(2026, 10, 20, tzinfo=dt.UTC),
        overdue_days=7,
        interval_days=7,
        send=send,
    )
    assert n == 1
    assert sent == [BOB]
    async with factory() as db:
        rows = (await db.execute(select(DebtReminder))).scalars().all()
        g2 = await db.get(Group, 2)
        assert g2 is not None
        bals = await balances_service.group_balances(db, g2)
    assert len(rows) == 1
    assert rows[0].debtor_id == BOB
    assert bals[DAVE] == Decimal("0.00")


# --- closed groups are still reminded ----------------------------------------


async def _closed_still_reminded(factory: async_sessionmaker) -> None:
    await _mk_users(factory, (ALICE, BOB))
    await _mk_group(factory, GROUP_ID, "EUR", ALICE, (ALICE, BOB))
    await _mk_expense(
        factory, GROUP_ID, ALICE, Decimal("100.00"), dt.date(2026, 10, 1), {BOB: Decimal("100.00")}
    )
    async with factory() as db:
        group = await db.get(Group, GROUP_ID)
        assert group is not None
        group.status = "closed"
        await db.commit()
    send, emails = _recorder()
    assert await _scan(factory, dt.datetime(2026, 10, 20, tzinfo=dt.UTC), send) == 1
    assert len(emails) == 1
    assert emails[0][0] == "u2@test.local"


# --- close / reopen lifecycle ------------------------------------------------


async def _close_reopen(factory: async_sessionmaker) -> None:
    await _mk_users(factory, (ALICE, BOB, CAROL))
    await _mk_group(factory, GROUP_ID, "EUR", ALICE, (ALICE, BOB, CAROL))
    # alice paid 150: bob owes 50, carol owes 100 -> alice +150, bob -50, carol -100
    await _mk_expense(
        factory,
        GROUP_ID,
        ALICE,
        Decimal("150.00"),
        dt.date(2026, 10, 1),
        {BOB: Decimal("50.00"), CAROL: Decimal("100.00")},
    )
    emails: list[tuple[str, str, str, str | None]] = []

    async def recorder(to: str, subject: str, text: str, html: str | None = None) -> None:
        emails.append((to, subject, text, html))

    original = mail_service.send_mail
    mail_service.send_mail = recorder
    try:
        # non-owner close -> Forbidden; reopening an active group -> Conflict
        async with factory() as db:
            group = await db.get(Group, GROUP_ID)
            assert group is not None
            with pytest.raises(Forbidden):
                await groups_service.close_group(db, group, await db.get(User, BOB))
        async with factory() as db:
            group = await db.get(Group, GROUP_ID)
            assert group is not None
            with pytest.raises(Conflict):
                await groups_service.reopen_group(db, group, await db.get(User, ALICE))

        # owner closes -> 3 summary mails listing who owes whom
        async with factory() as db:
            group = await db.get(Group, GROUP_ID)
            assert group is not None
            closed = await groups_service.close_group(db, group, await db.get(User, ALICE))
        assert closed.status == "closed" and closed.closed_at is not None
        assert len(emails) == 3
        for _, _, text, _ in emails:
            assert "User 2 owes User 1 50.00 EUR" in (text or "")

        # double close -> Conflict; non-owner reopen -> Forbidden
        async with factory() as db:
            group = await db.get(Group, GROUP_ID)
            assert group is not None
            with pytest.raises(Conflict):
                await groups_service.close_group(db, group, await db.get(User, ALICE))
        async with factory() as db:
            group = await db.get(Group, GROUP_ID)
            assert group is not None
            with pytest.raises(Forbidden):
                await groups_service.reopen_group(db, group, await db.get(User, BOB))

        # owner reopens -> active, no new mails
        count = len(emails)
        async with factory() as db:
            group = await db.get(Group, GROUP_ID)
            assert group is not None
            reopened = await groups_service.reopen_group(db, group, await db.get(User, ALICE))
        assert reopened.status == "active" and reopened.closed_at is None
        assert len(emails) == count

        # settle up, then close again -> 3 "settled up" mails
        async with factory() as db:
            group = await db.get(Group, GROUP_ID)
            assert group is not None
            s_bob = await settlements_service.create_settlement(
                db,
                group,
                await db.get(User, BOB),
                SettlementCreate(to_user_id=ALICE, amount=Decimal("50.00")),
            )
            await settlements_service.confirm_settlement(db, s_bob, await db.get(User, ALICE))
            s_carol = await settlements_service.create_settlement(
                db,
                group,
                await db.get(User, CAROL),
                SettlementCreate(to_user_id=ALICE, amount=Decimal("100.00")),
            )
            await settlements_service.confirm_settlement(db, s_carol, await db.get(User, ALICE))
        async with factory() as db:
            group = await db.get(Group, GROUP_ID)
            assert group is not None
            await groups_service.close_group(db, group, await db.get(User, ALICE))
        assert len(emails) == 6
        for _, _, text, _ in emails[3:]:
            assert "settled up" in (text or "")

        # feed rows + the bell to every member on both events
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
            assert "group.closed" in feed and "group.reopened" in feed
            # the group is closed twice (unpaid, then settled): every member is
            # bell-notified on each close
            closed_n = (
                (
                    await db.execute(
                        select(Notification.user_id)
                        .where(Notification.type == "group.closed")
                        .order_by(Notification.user_id)
                    )
                )
                .scalars()
                .all()
            )
            assert closed_n == [ALICE, ALICE, BOB, BOB, CAROL, CAROL]
            reopened_n = (
                (
                    await db.execute(
                        select(Notification.user_id)
                        .where(Notification.type == "group.reopened")
                        .order_by(Notification.user_id)
                    )
                )
                .scalars()
                .all()
            )
            assert reopened_n == [ALICE, BOB, CAROL]
    finally:
        mail_service.send_mail = original


# --- close summary is a complete statement -----------------------------------


async def _close_summary_includes_zero_balances(factory: async_sessionmaker) -> None:
    await _mk_users(factory, (ALICE, BOB, CAROL))
    await _mk_group(factory, GROUP_ID, "EUR", ALICE, (ALICE, BOB, CAROL))
    # alice paid 100.00 in equal shares: a 33.34 / b 33.33 / c 33.33
    await _mk_expense(
        factory,
        GROUP_ID,
        ALICE,
        Decimal("100.00"),
        dt.date(2026, 10, 1),
        {ALICE: Decimal("33.34"), BOB: Decimal("33.33"), CAROL: Decimal("33.33")},
    )
    # carol settles her whole debt to alice -> her balance is exactly 0.
    async with factory() as db:
        group = await db.get(Group, GROUP_ID)
        assert group is not None
        s = await settlements_service.create_settlement(
            db,
            group,
            await db.get(User, CAROL),
            SettlementCreate(to_user_id=ALICE, amount=Decimal("33.33")),
        )
        await settlements_service.confirm_settlement(db, s, await db.get(User, ALICE))
    emails: list[tuple[str, str, str, str | None]] = []

    async def recorder(to: str, subject: str, text: str, html: str | None = None) -> None:
        emails.append((to, subject, text, html))

    original = mail_service.send_mail
    mail_service.send_mail = recorder
    try:
        async with factory() as db:
            group = await db.get(Group, GROUP_ID)
            assert group is not None
            await groups_service.close_group(db, group, await db.get(User, ALICE))
    finally:
        mail_service.send_mail = original
    # §2.9: the summary is a complete statement — one mail per member, and the
    # zero-balance member (carol) is listed with an explicit 0.00, not skipped.
    assert sorted(to for to, _, _, _ in emails) == [
        "u1@test.local",
        "u2@test.local",
        "u3@test.local",
    ]
    for _, _, text, _ in emails:
        assert "  - User 1: 33.33 EUR" in (text or "")
        assert "  - User 2: -33.33 EUR" in (text or "")
        assert "  - User 3: 0.00 EUR" in (text or "")


# --- entry points ------------------------------------------------------------


def test_overdue_boundary_strictly_greater(run_db) -> None:
    run_db(_overdue_boundary)


def test_weekly_cap_boundary(run_db) -> None:
    run_db(_weekly_cap)


def test_skip_when_settled_before_scan(run_db) -> None:
    run_db(_skip_when_settled)


def test_phase2_reverify_kills_late_candidate(run_db) -> None:
    run_db(_phase2_reverify)


def test_closed_group_still_reminded(run_db) -> None:
    run_db(_closed_still_reminded)


def test_close_reopen_lifecycle(run_db) -> None:
    run_db(_close_reopen)


def test_close_summary_includes_zero_balances(run_db) -> None:
    run_db(_close_summary_includes_zero_balances)
