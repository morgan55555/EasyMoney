"""Overdue-debt reminders (AGENTS.md §2.7).

Business rules for the hourly scan; the APScheduler wiring lives in
``app.workers.scheduler``. Every decision takes an explicit ``now`` and the
two day thresholds, so the tests can drive a fake clock and a fake mailer
(AGENTS.md §5):

* an *owed expense* is an active expense the debtor takes part in with a
  larger share than what they paid for it; its debt portion is
  ``share − paid``;
* confirmed settlements the debtor sent cover owed expenses FIFO, oldest
  ``(date, id)`` first — bookkeeping for aging only, it never touches
  balances (invariant 4);
* the *debt age* is the date of the first owed expense not fully covered
  (None when everything is covered);
* the debtor is *overdue* iff their balance is negative and
  ``now − debt_age > DEBT_OVERDUE_DAYS`` (strictly greater);
* at most one reminder per ``REMINDER_INTERVAL_DAYS`` (skipped only when
  less than that has passed, so a send exactly at the boundary is allowed);
* closed groups are reminded too, until the debt is settled (§2.7).

The scan is two-phase: phase 1 collects the candidates; phase 2 re-verifies
each one in a **fresh session** at send time (§2.7 step 3) — a settlement
that landed in between kills the reminder — and only then sends the mail
and stamps ``debt_reminders.last_sent_at``. A per-pair mail failure is
logged and the rest of the scan continues (and the pair stays un-stamped,
so the next scan retries it).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.models.debt_reminder import DebtReminder
from app.models.expense import Expense
from app.models.expense_participant import ExpenseParticipant
from app.models.group import Group
from app.models.membership import Membership
from app.models.settlement import Settlement
from app.models.user import User
from app.services import balances as balances_service
from app.services import mail as mail_service

logger = logging.getLogger("easymoney.reminders")

# The mailer hook: (debtor, group, fresh snapshot) -> sends one email.
SendFn = Callable[[User, Group, balances_service.GroupBalanceSnapshot], Awaitable[None]]


def debt_age(owed: Sequence[tuple[date, Decimal]], coverage: Decimal) -> date | None:
    """Pure FIFO attribution of confirmed payments onto owed expenses (§2.7).

    ``owed`` carries the debtor's owed portions in ascending ``(date, id)``
    order; ``coverage`` is the total of their confirmed settlements sent in
    the group. Returns the date of the first portion not fully covered, or
    None when everything is covered.
    """
    remaining = coverage
    for day, portion in owed:
        if remaining >= portion:
            remaining -= portion
        else:
            return day
    return None


def _day_start(day: date) -> datetime:
    """Debt ages are expense *dates*; the scan compares them to a datetime."""
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


async def _owed_expenses(
    db: AsyncSession, group_id: int, user_id: int
) -> list[tuple[date, Decimal]]:
    """The debtor's owed portions, oldest ``(date, id)`` first (§2.7)."""
    # paid(M, E) is E.amount when M paid E, 0 otherwise — a single expense
    # has one payer, so no aggregate is needed (and Postgres rejects the
    # non-aggregate columns otherwise).
    paid = case((Expense.payer_id == user_id, Expense.amount), else_=0)
    rows = (
        await db.execute(
            select(
                Expense.date,
                (ExpenseParticipant.share - paid).label("portion"),
            )
            .select_from(ExpenseParticipant)
            .join(Expense, ExpenseParticipant.expense_id == Expense.id)
            .where(
                Expense.group_id == group_id,
                ExpenseParticipant.user_id == user_id,
                Expense.deleted_at.is_(None),
                ExpenseParticipant.share > paid,
            )
            .order_by(Expense.date.asc(), Expense.id.asc())
        )
    ).all()
    return [(row[0], row[1]) for row in rows]


async def _confirmed_sent(db: AsyncSession, group_id: int, user_id: int) -> Decimal:
    """Total of the debtor's confirmed settlements sent in the group."""
    total = await db.scalar(
        select(func.coalesce(func.sum(Settlement.amount), 0)).where(
            Settlement.group_id == group_id,
            Settlement.from_user_id == user_id,
            Settlement.state == "confirmed",
        )
    )
    return Decimal(0) if total is None else total


async def _debt_age(db: AsyncSession, group_id: int, user_id: int) -> date | None:
    owed = await _owed_expenses(db, group_id, user_id)
    if not owed:
        return None
    coverage = await _confirmed_sent(db, group_id, user_id)
    return debt_age(owed, coverage)


async def find_candidates(
    db: AsyncSession,
    now: datetime,
    *,
    overdue_days: int,
    interval_days: int,
) -> list[tuple[Group, User]]:
    """Phase 1 (§2.7 steps 1–2): overdue current members, interval-gated.

    All groups are scanned — closed ones too, until settled. Only current
    members (Membership rows) are reminded.
    """
    groups = (await db.execute(select(Group).order_by(Group.id))).scalars().all()
    candidates: list[tuple[Group, User]] = []
    for group in groups:
        members = (
            (
                await db.execute(
                    select(User)
                    .join(Membership, Membership.user_id == User.id)
                    .where(Membership.group_id == group.id)
                    .order_by(User.id)
                )
            )
            .scalars()
            .all()
        )
        balances = await balances_service.group_balances(db, group)
        sent_rows = (
            await db.execute(
                select(DebtReminder.debtor_id, DebtReminder.last_sent_at).where(
                    DebtReminder.group_id == group.id
                )
            )
        ).all()
        last_sent: dict[int, datetime | None] = {row[0]: row[1] for row in sent_rows}
        for user in members:
            balance = balances.get(user.id, Decimal(0))
            if balance >= 0:
                continue
            age_date = await _debt_age(db, group.id, user.id)
            if age_date is None or (now - _day_start(age_date)) <= timedelta(days=overdue_days):
                continue
            last = last_sent.get(user.id)
            if last is not None and (now - last) < timedelta(days=interval_days):
                continue
            candidates.append((group, user))
    return candidates


def build_reminder_email(
    debtor: User, group: Group, snapshot: balances_service.GroupBalanceSnapshot
) -> tuple[str, str, str, str]:
    """``(to, subject, text, html)`` of one reminder, pure over the snapshot.

    The per-creditor amounts come from the pairwise view restricted to the
    debtor (§2.3): each creditor is listed with ``min(total debt, their
    credit)``, which sums to the total debt because the group sums to zero.
    """
    names = {b.user_id: b.display_name for b in snapshot.balances}
    per_creditor = [
        (names.get(t.to_user_id, f"user {t.to_user_id}"), t.amount)
        for t in snapshot.pairs
        if t.from_user_id == debtor.id
    ]
    total = sum((amount for _, amount in per_creditor), Decimal(0))
    subject, text, html = mail_service.overdue_reminder_message(
        group.name,
        group.currency,
        debtor.display_name,
        per_creditor,
        total,
        group_id=group.id,
        lang=debtor.preferred_language,
    )
    return debtor.email, subject, text, html


async def _send_reminder(
    debtor: User, group: Group, snapshot: balances_service.GroupBalanceSnapshot
) -> None:
    """The default mailer: one reminder email to the debtor (§2.7 step 4)."""
    to, subject, text, html = build_reminder_email(debtor, group, snapshot)
    await mail_service.send_mail(to, subject, text, html)


async def run_reminder_scan(
    factory: async_sessionmaker[AsyncSession],
    now: datetime | None = None,
    *,
    overdue_days: int | None = None,
    interval_days: int | None = None,
    send: SendFn | None = None,
) -> int:
    """Run one full reminder pass; returns the number of emails sent.

    ``now`` (default: UTC now), the two day thresholds (default: settings)
    and ``send`` (default: the real reminder mail) are injectable for the
    fake-clock / fake-mailer tests (AGENTS.md §5).
    """
    settings = get_settings()
    if now is None:
        now = datetime.now(UTC)
    if overdue_days is None:
        overdue_days = settings.debt_overdue_days
    if interval_days is None:
        interval_days = settings.reminder_interval_days
    if send is None:
        send = _send_reminder

    async with factory() as db:
        candidates = await find_candidates(
            db, now, overdue_days=overdue_days, interval_days=interval_days
        )

    sent = 0
    for group, debtor in candidates:
        # Phase 2 (§2.7 step 3): re-verify at send time in a fresh session —
        # a settlement that landed between the scan and the send kills the
        # reminder; never remind about a settled debt.
        async with factory() as db:
            balance = await balances_service.group_balance_for_user(db, group.id, debtor.id)
            if balance >= 0:
                continue
            age_date = await _debt_age(db, group.id, debtor.id)
            if age_date is None or (now - _day_start(age_date)) <= timedelta(days=overdue_days):
                continue
            snapshot = await balances_service.group_balance_snapshot(db, group)
        try:
            await send(debtor, group, snapshot)
        except Exception:
            logger.exception(
                "Reminder mail failed for user %s in group %s; continuing the scan",
                debtor.id,
                group.id,
            )
            continue
        async with factory() as db:
            row = await db.scalar(
                select(DebtReminder).where(
                    DebtReminder.group_id == group.id,
                    DebtReminder.debtor_id == debtor.id,
                )
            )
            if row is None:
                row = DebtReminder(group_id=group.id, debtor_id=debtor.id)
                db.add(row)
            row.last_sent_at = now
            await db.commit()
        sent += 1
    return sent
