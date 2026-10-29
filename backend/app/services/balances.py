"""Balances computed on read (AGENTS.md invariant 4, §2.2).

Nothing here is stored; every value is an aggregate over expenses and
confirmed settlements. ``pairwise_debts`` and ``optimize_settlements`` are
pure functions over a ``{user_id: balance}`` mapping (§2.3–2.4); the
snapshot built from them is what ``GET /groups/{id}/balances`` serves (B6)
and what the WS layer pushes on every event (B8).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.redis_client import get_redis
from app.models.expense import Expense
from app.models.expense_participant import ExpenseParticipant
from app.models.group import Group
from app.models.membership import Membership
from app.models.settlement import Settlement
from app.models.user import User
from app.schemas.user import avatar_url
from app.services import currency as currency_service

logger = logging.getLogger("easymoney.balances")


def _dec(value: Decimal | None) -> Decimal:
    """The aggregates above are COALESCEd to 0 in SQL, so None is only a
    theoretical edge; treat it as zero either way."""
    return Decimal(0) if value is None else value


async def group_balance_for_user(db: AsyncSession, group_id: int, user_id: int) -> Decimal:
    """§2.2: paid − shares + confirmed sent − confirmed received.

    Soft-deleted expenses are excluded; only confirmed settlements count.
    """
    paid: Decimal | None = await db.scalar(
        select(func.coalesce(func.sum(Expense.amount), 0)).where(
            Expense.group_id == group_id,
            Expense.payer_id == user_id,
            Expense.deleted_at.is_(None),
        )
    )
    shares: Decimal | None = await db.scalar(
        select(func.coalesce(func.sum(ExpenseParticipant.share), 0))
        .select_from(ExpenseParticipant)
        .join(Expense, ExpenseParticipant.expense_id == Expense.id)
        .where(
            Expense.group_id == group_id,
            ExpenseParticipant.user_id == user_id,
            Expense.deleted_at.is_(None),
        )
    )
    sent: Decimal | None = await db.scalar(
        select(func.coalesce(func.sum(Settlement.amount), 0)).where(
            Settlement.group_id == group_id,
            Settlement.from_user_id == user_id,
            Settlement.state == "confirmed",
        )
    )
    received: Decimal | None = await db.scalar(
        select(func.coalesce(func.sum(Settlement.amount), 0)).where(
            Settlement.group_id == group_id,
            Settlement.to_user_id == user_id,
            Settlement.state == "confirmed",
        )
    )
    return _dec(paid) - _dec(shares) + _dec(sent) - _dec(received)


# --- B6: snapshots, pairwise debts, optimizer ---------------------------------


@dataclass(frozen=True)
class Transfer:
    """One money movement from debtor to creditor (amount > 0)."""

    from_user_id: int
    to_user_id: int
    amount: Decimal


@dataclass(frozen=True)
class MemberBalance:
    """One user's net balance in a group (positive = owed to them)."""

    user_id: int
    display_name: str
    balance: Decimal
    # Raw base64 (or None); the API/WS layers map it to a short avatar URL.
    avatar_data: str | None = None


@dataclass(frozen=True)
class GroupBalanceSnapshot:
    """Everything the UI and WS need for a group's money state (§2.2, §2.3).

    Built once per read; the balances endpoints serialize it and B8 pushes a
    fresh one with every event so tabs never wait for an extra REST call.
    """

    currency: str
    balances: tuple[MemberBalance, ...]
    pairs: tuple[Transfer, ...]
    transfers: tuple[Transfer, ...]


def pairwise_debts(balances: Mapping[int, Decimal]) -> list[Transfer]:
    """The "current pairwise view" of §2.4.

    Every debtor owes every creditor ``min(what the debtor owes, what the
    creditor is owed)``. Advisory only: settlement proposals validate
    against it (§2.4), but money moves only through the settlement flow.
    Deterministic order: amount desc, then (from_user_id, to_user_id) asc.
    """
    debtors = {uid: -bal for uid, bal in balances.items() if bal < 0}
    creditors = {uid: bal for uid, bal in balances.items() if bal > 0}
    out = [
        Transfer(debtor, creditor, min(owed, credited))
        for debtor, owed in debtors.items()
        for creditor, credited in creditors.items()
    ]
    out.sort(key=lambda t: (-t.amount, t.from_user_id, t.to_user_id))
    return out


def optimize_settlements(balances: Mapping[int, Decimal]) -> list[Transfer]:
    """AGENTS.md §2.3 greedy, verbatim.

    Debtors and creditors are sorted by amount desc (tie-break user_id
    asc) and matched head-to-head. Yields at most ``n - 1`` transfers
    (n = number of non-zero balances) whose total equals the total debt
    and which leave every balance at exactly zero.
    """
    # Sorted as in the spec: debtors/creditors by amount desc, tie-break
    # user_id asc. The remaining amounts are carried in d_rem / c_rem and
    # reset (to None) only when that side advances, so each partial match
    # persists across iterations — the spec's D[i].amount -= amt / C[j].amount
    # -= amt semantics, without re-reading a fresh tuple every loop.
    debtors = sorted(
        ((uid, -bal) for uid, bal in balances.items() if bal < 0),
        key=lambda pair: (-pair[1], pair[0]),
    )
    creditors = sorted(
        ((uid, bal) for uid, bal in balances.items() if bal > 0),
        key=lambda pair: (-pair[1], pair[0]),
    )
    out: list[Transfer] = []
    i = j = 0
    d_rem: Decimal | None = None
    c_rem: Decimal | None = None
    while i < len(debtors) and j < len(creditors):
        if d_rem is None:
            d_rem = debtors[i][1]
        if c_rem is None:
            c_rem = creditors[j][1]
        amt = min(d_rem, c_rem)
        out.append(Transfer(debtors[i][0], creditors[j][0], amt))
        d_rem -= amt
        c_rem -= amt
        if d_rem == 0:
            i += 1
            d_rem = None
        if c_rem == 0:
            j += 1
            c_rem = None
    return out


# --- B6: on-read aggregates -----------------------------------------------------


async def group_balances(db: AsyncSession, group: Group) -> dict[int, Decimal]:
    """§2.2 per-user balances for one group, computed on read.

    One UNION ALL over the four formula terms, grouped by user. Users with
    no rows (members who never touched money) get an explicit 0; a user who
    left the group but keeps a non-zero balance stays in the map so the
    sum-zero invariant (1) holds over everything this map returns.
    """
    gid = group.id
    paid = select(Expense.payer_id.label("user_id"), Expense.amount.label("delta")).where(
        Expense.group_id == gid,
        Expense.deleted_at.is_(None),
    )
    shares = (
        select(
            ExpenseParticipant.user_id.label("user_id"),
            (-ExpenseParticipant.share).label("delta"),
        )
        .select_from(ExpenseParticipant)
        .join(Expense, ExpenseParticipant.expense_id == Expense.id)
        .where(Expense.group_id == gid, Expense.deleted_at.is_(None))
    )
    sent = select(
        Settlement.from_user_id.label("user_id"),
        Settlement.amount.label("delta"),
    ).where(Settlement.group_id == gid, Settlement.state == "confirmed")
    received = select(
        Settlement.to_user_id.label("user_id"),
        (-Settlement.amount).label("delta"),
    ).where(Settlement.group_id == gid, Settlement.state == "confirmed")
    union = paid.union_all(shares, sent, received).subquery()
    rows = (
        await db.execute(
            select(union.c.user_id, func.coalesce(func.sum(union.c.delta), 0)).group_by(
                union.c.user_id
            )
        )
    ).all()
    result = {int(uid): _dec(total) for uid, total in rows}
    member_ids = (
        await db.execute(select(Membership.user_id).where(Membership.group_id == gid))
    ).scalars()
    for uid in member_ids:
        result.setdefault(int(uid), Decimal(0))
    return result


async def all_balances_for_user(db: AsyncSession, user: User) -> dict[str, Decimal]:
    """§2.2 cross-group view: the caller's balance per currency, no conversion.

    Sums only the caller's own balance over every group they belong to.
    Currencies of groups with a zero total still appear (as 0) so the UI
    can show every open currency.
    """
    my_groups = select(Membership.group_id).where(Membership.user_id == user.id)

    paid = (
        select(
            Group.currency.label("currency"),
            Expense.payer_id.label("user_id"),
            Expense.amount.label("delta"),
        )
        .select_from(Expense)
        .join(Group, Group.id == Expense.group_id)
        .where(Expense.group_id.in_(my_groups), Expense.deleted_at.is_(None))
    )
    shares = (
        select(
            Group.currency.label("currency"),
            ExpenseParticipant.user_id.label("user_id"),
            (-ExpenseParticipant.share).label("delta"),
        )
        .select_from(ExpenseParticipant)
        .join(Expense, ExpenseParticipant.expense_id == Expense.id)
        .join(Group, Group.id == Expense.group_id)
        .where(Expense.group_id.in_(my_groups), Expense.deleted_at.is_(None))
    )
    sent = (
        select(
            Group.currency.label("currency"),
            Settlement.from_user_id.label("user_id"),
            Settlement.amount.label("delta"),
        )
        .select_from(Settlement)
        .join(Group, Group.id == Settlement.group_id)
        .where(Settlement.group_id.in_(my_groups), Settlement.state == "confirmed")
    )
    received = (
        select(
            Group.currency.label("currency"),
            Settlement.to_user_id.label("user_id"),
            (-Settlement.amount).label("delta"),
        )
        .select_from(Settlement)
        .join(Group, Group.id == Settlement.group_id)
        .where(Settlement.group_id.in_(my_groups), Settlement.state == "confirmed")
    )
    union = paid.union_all(shares, sent, received).subquery()
    rows = (
        await db.execute(
            select(union.c.currency, func.coalesce(func.sum(union.c.delta), 0))
            .where(union.c.user_id == user.id)
            .group_by(union.c.currency)
        )
    ).all()
    result = {cur: _dec(total) for cur, total in rows}
    currencies = (
        await db.execute(
            select(Group.currency)
            .join(Membership, Membership.group_id == Group.id)
            .where(Membership.user_id == user.id)
        )
    ).scalars()
    for cur in currencies:
        result.setdefault(cur, Decimal(0))
    return result


async def group_balance_snapshot(db: AsyncSession, group: Group) -> GroupBalanceSnapshot:
    """Build the full money-state snapshot for a group (§2.2 + §2.3 + §2.4).

    Single entry point for the balances endpoints (B6) and the WS layer
    (B8): balances per user, the pairwise view, and the greedy suggestion.
    """
    balances = await group_balances(db, group)
    name_rows = (
        await db.execute(
            select(User.id, User.display_name, User.avatar_data).where(
                User.id.in_(list(balances) or [0])
            )
        )
    ).all()
    names = {uid: name for uid, name, _avatar in name_rows}
    avatars = {uid: avatar for uid, _name, avatar in name_rows}
    member_balances = tuple(
        MemberBalance(uid, names[uid], bal, avatars.get(uid))
        for uid, bal in sorted(balances.items())
    )
    return GroupBalanceSnapshot(
        currency=group.currency,
        balances=member_balances,
        pairs=tuple(pairwise_debts(balances)),
        transfers=tuple(optimize_settlements(balances)),
    )


# --- Wire dict + Redis snapshot cache (display reads only) -------------------
#
# The cache serves display reads only (``GET /groups/{id}/balances``). It is
# fail-open — any Redis error falls back to the DB path — and never affects
# business validation: settlement proposals still read the pairwise view on
# read (invariant 4 is untouched). Invalidation is exact: every group
# mutation goes through ``ws_hook.emit_group``, which refreshes the key with
# the same wire dict it pushes over the WS (``refresh_snapshot_cache``); the
# TTL is only the safety net for a missed emit.


def _snapshot_key(group_id: int) -> str:
    return f"easymoney:snapshot:{group_id}"


def _transfer_wire(t: Transfer, cur: str) -> dict[str, Any]:
    return {
        "from_user_id": t.from_user_id,
        "to_user_id": t.to_user_id,
        "amount": currency_service.format_money(t.amount, cur),
    }


def snapshot_to_wire(snapshot: GroupBalanceSnapshot) -> dict[str, Any]:
    """The snapshot as the wire dict (the ``group_balances`` payload).

    Same shape as ``GET /groups/{id}/balances``: per-user balances, the
    pairwise view and the suggested transfers — money as strings (§2.12).
    """
    cur = snapshot.currency
    return {
        "currency": cur,
        "balances": [
            {
                "user_id": b.user_id,
                "display_name": b.display_name,
                "balance": currency_service.format_money(b.balance, cur),
                "avatar_url": avatar_url(b.user_id, b.avatar_data),
            }
            for b in snapshot.balances
        ],
        "pairs": [_transfer_wire(t, cur) for t in snapshot.pairs],
        "transfers": [_transfer_wire(t, cur) for t in snapshot.transfers],
    }


async def load_balances_wire(db: AsyncSession, group: Group) -> dict[str, Any]:
    """The group-balance wire dict for display, with the snapshot cache.

    Cache hit → return as-is (no DB); miss → compute the snapshot, store the
    wire dict (TTL ``snapshot_cache_ttl_s``) and return it. Local mode
    (no Redis) is the plain compute path; a dead Redis is fail-open.
    """
    key = _snapshot_key(group.id)
    redis = get_redis()
    if redis is not None:
        try:
            cached = await redis.get(key)
        except Exception:
            logger.warning("Snapshot cache read failed; computing from DB", exc_info=True)
        else:
            if cached is not None:
                try:
                    cached_wire = json.loads(cached)
                except ValueError:
                    logger.warning("Snapshot cache value unparseable; recomputing")
                else:
                    # json.loads is untyped (Any); the stored value is the wire dict
                    if isinstance(cached_wire, dict):
                        return cast(dict[str, Any], cached_wire)
    wire = snapshot_to_wire(await group_balance_snapshot(db, group))
    await refresh_snapshot_cache(group.id, wire)
    return wire


async def refresh_snapshot_cache(group_id: int, wire: dict[str, Any]) -> None:
    """Store the fresh wire dict (called from the post-commit emit path).

    Fail-open: a write failure only means the next read recomputes.
    """
    redis = get_redis()
    if redis is None:
        return
    try:
        await redis.set(
            _snapshot_key(group_id), json.dumps(wire), ex=get_settings().snapshot_cache_ttl_s
        )
    except Exception:
        logger.warning("Snapshot cache refresh failed", exc_info=True)
