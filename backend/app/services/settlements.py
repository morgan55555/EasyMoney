"""Settlements: propose / confirm / reject / cancel (AGENTS.md §2.4).

State machine: ``pending -> confirmed | rejected | cancelled`` (all terminal).

* Proposals validate against the **current pairwise view**: the sender must
  owe the recipient in the group, and the amount may not exceed that debt.
  If the debt shrinks before confirmation (someone else paid, an expense was
  edited or deleted), the confirmed amount still applies in full — the money
  really moved (§2.4 note); no re-validation at confirmation.
* Transitions are guarded by ``WHERE state = 'pending'``: with two
  concurrent confirmations exactly one wins, the loser gets a Conflict (409).
* Pending settlements never touch balances; a confirmed one does — but only
  through the on-read aggregate (invariant 4), never a stored value.
* Closed groups still accept settlements (§2.9: "everything else still
  works").

Each mutation is one transaction: state change + feed row + notification
rows; the WS push goes out post-commit through ``app.ws.hook`` (B8), with a
fresh balance snapshot in every group event.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.models.group import Group
from app.models.membership import Membership
from app.models.settlement import Settlement
from app.models.user import User
from app.schemas.settlements import SettlementCreate
from app.services import balances as balances_service
from app.services import currency as currency_service
from app.services import feed as feed_service
from app.services import notifications as notifications_service
from app.ws import hook as ws_hook


async def _group_of(db: AsyncSession, settlement: Settlement) -> Group:
    group = await db.get(Group, settlement.group_id)
    if group is None:  # pragma: no cover - the FK guarantees existence
        raise NotFound("Group not found")
    return group


async def _display_names(db: AsyncSession, user_ids: set[int]) -> dict[int, str]:
    rows = (
        await db.execute(select(User.id, User.display_name).where(User.id.in_(user_ids)))
    ).all()
    return {uid: name for uid, name in rows}


async def settlement_payload(db: AsyncSession, settlement: Settlement) -> dict[str, Any]:
    """Full settlement for feed / WS / notification payloads (§3.2) —
    everything the client needs to update the UI without an extra REST call."""
    group = await _group_of(db, settlement)
    names = await _display_names(db, {settlement.from_user_id, settlement.to_user_id})
    return {
        "id": settlement.id,
        "group_id": settlement.group_id,
        "from_user_id": settlement.from_user_id,
        "from_name": names.get(settlement.from_user_id, f"user {settlement.from_user_id}"),
        "to_user_id": settlement.to_user_id,
        "to_name": names.get(settlement.to_user_id, f"user {settlement.to_user_id}"),
        "amount": currency_service.format_money(settlement.amount, group.currency),
        "currency": group.currency,
        "state": settlement.state,
        "confirmed_at": settlement.confirmed_at.isoformat() if settlement.confirmed_at else None,
        "created_at": settlement.created_at.isoformat(),
    }


async def _load_settlement(db: AsyncSession, settlement_id: int) -> Settlement:
    """Fresh row with the relationships the response builders need."""
    result = await db.execute(
        select(Settlement)
        .options(
            joinedload(Settlement.group),
            joinedload(Settlement.from_user),
            joinedload(Settlement.to_user),
        )
        .where(Settlement.id == settlement_id)
    )
    return result.scalars().unique().one()


async def create_settlement(
    db: AsyncSession, group: Group, user: User, data: SettlementCreate
) -> Settlement:
    """§2.4: the debtor proposes a (partial) payment to a creditor.

    Validated against the current pairwise view (422 otherwise); pending has
    no balance effect. Notifies sender + recipient, writes the feed row.
    """
    if data.to_user_id == user.id:
        raise ValidationFailed("You cannot settle a debt with yourself")
    member_ids = (
        await db.execute(select(Membership.user_id).where(Membership.group_id == group.id))
    ).scalars().all()
    if data.to_user_id not in member_ids:
        raise ValidationFailed("The recipient must be a member of the group")
    # Reject amounts finer than the currency's minor unit (422, as for expenses).
    currency_service.to_minor(data.amount, group.currency)

    balances = await balances_service.group_balances(db, group)
    pair = next(
        (
            t
            for t in balances_service.pairwise_debts(balances)
            if t.from_user_id == user.id and t.to_user_id == data.to_user_id
        ),
        None,
    )
    if pair is None:
        raise ValidationFailed("You do not owe this member anything right now")
    if data.amount > pair.amount:
        raise ValidationFailed("The amount is greater than the debt you owe this member")

    settlement = Settlement(
        group_id=group.id,
        from_user_id=user.id,
        to_user_id=data.to_user_id,
        amount=data.amount,
    )
    db.add(settlement)
    await db.flush()
    payload = await settlement_payload(db, settlement)
    await feed_service.add_event(
        db,
        group.id,
        "settlement.created",
        actor_id=user.id,
        ref_type="settlement",
        ref_id=settlement.id,
        payload=payload,
    )
    notifications = await notifications_service.notify_users(
        db, {user.id, data.to_user_id}, "settlement.created", payload
    )
    await db.commit()
    await ws_hook.emit_group(db, group, "settlement.created", payload)
    await ws_hook.emit_notifications(group.id, notifications)
    return await _load_settlement(db, settlement.id)


async def list_settlements(db: AsyncSession, group: Group) -> list[Settlement]:
    """Every settlement of the group (all states), newest first."""
    result = await db.execute(
        select(Settlement)
        .options(
            joinedload(Settlement.group),
            joinedload(Settlement.from_user),
            joinedload(Settlement.to_user),
        )
        .where(Settlement.group_id == group.id)
        .order_by(Settlement.created_at.desc(), Settlement.id.desc())
    )
    return list(result.scalars().unique().all())


async def _transition(
    db: AsyncSession,
    settlement: Settlement,
    user: User,
    *,
    by: str,  # "recipient" (confirm/reject) or "sender" (cancel)
    new_state: str,
    feed_type: str,
) -> Settlement:
    if by == "recipient" and user.id != settlement.to_user_id:
        raise Forbidden("Only the recipient can act on this settlement")
    if by == "sender" and user.id != settlement.from_user_id:
        raise Forbidden("Only the sender can act on this settlement")
    if settlement.state != "pending":
        raise Conflict(f"This settlement is already {settlement.state}")

    values: dict[str, Any] = {"state": new_state}
    if new_state == "confirmed":
        values["confirmed_at"] = datetime.now(UTC)
    # A DML execute() returns a CursorResult at runtime; the static return
    # type of AsyncSession.execute is the wider Result, which has no rowcount.
    result = cast(
        CursorResult[Any],
        await db.execute(
            update(Settlement)
            .where(Settlement.id == settlement.id, Settlement.state == "pending")
            .values(**values)
        ),
    )
    if result.rowcount == 0:
        # Lost the race: another session transitioned it first (§2.8).
        raise Conflict("This settlement was already acted on")
    await db.refresh(settlement)

    payload = await settlement_payload(db, settlement)
    await feed_service.add_event(
        db,
        settlement.group_id,
        feed_type,
        actor_id=user.id,
        ref_type="settlement",
        ref_id=settlement.id,
        payload=payload,
    )
    notifications = await notifications_service.notify_users(
        db, {settlement.from_user_id, settlement.to_user_id}, feed_type, payload
    )
    await db.commit()
    group = await _group_of(db, settlement)
    await ws_hook.emit_group(db, group, feed_type, payload)
    await ws_hook.emit_notifications(settlement.group_id, notifications)
    return await _load_settlement(db, settlement.id)


async def confirm_settlement(db: AsyncSession, settlement: Settlement, user: User) -> Settlement:
    """Recipient only. The amount is applied to balances — via the on-read
    aggregate, which now includes this confirmed row (invariant 4)."""
    return await _transition(
        db, settlement, user, by="recipient", new_state="confirmed",
        feed_type="settlement.confirmed",
    )


async def reject_settlement(db: AsyncSession, settlement: Settlement, user: User) -> Settlement:
    """Recipient only. Terminal; the sender is notified."""
    return await _transition(
        db, settlement, user, by="recipient", new_state="rejected",
        feed_type="settlement.rejected",
    )


async def cancel_settlement(db: AsyncSession, settlement: Settlement, user: User) -> Settlement:
    """Sender only. Terminal; the recipient is notified."""
    return await _transition(
        db, settlement, user, by="sender", new_state="cancelled",
        feed_type="settlement.cancelled",
    )
