"""Expenses: create / list / edit / soft-delete + receipts (AGENTS.md §2.1, §2.5, §3.5).

Rules implemented here (the routers stay thin):

* the split is always computed **server-side** by :mod:`app.services.splits`
  (§2.1, invariant 2 — stored shares sum to the amount exactly);
* the payer must be a group member **and** one of the participants;
* every participant must be a group member;
* edits are allowed for the expense creator or the group owner (§2.5);
* a closed group rejects new expenses and receipt uploads (409, §2.9);
* deletes are soft (``deleted_at``); deleted rows stay in the feed but are
  excluded from every balance sum (invariant 4 does the rest).

Each mutation happens in one transaction: state change + feed row +
notification rows; the WS push goes out post-commit through ``app.ws.hook``
(B8), with a fresh balance snapshot in every group event.
"""

from __future__ import annotations

import contextlib
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.core.config import get_settings
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.models.attachment import Attachment
from app.models.expense import Expense
from app.models.expense_participant import ExpenseParticipant
from app.models.group import Group
from app.models.membership import Membership
from app.models.user import User
from app.schemas.expenses import ExpenseCreate, ExpenseUpdate, ParticipantIn
from app.services import currency as currency_service
from app.services import feed as feed_service
from app.services import notifications as notifications_service
from app.services import splits as splits_service
from app.ws import hook as ws_hook

# Receipt content types accepted by POST /expenses/{id}/receipt (§3.5).
_CONTENT_TYPE_EXT: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "application/pdf": "pdf",
}


def _expense_options() -> list[Any]:
    """Eager-load everything the response builders need (no lazy loads)."""
    return [
        joinedload(Expense.group),
        joinedload(Expense.payer),
        joinedload(Expense.participants).joinedload(ExpenseParticipant.user),
        joinedload(Expense.attachment),
    ]


async def _load_expense(db: AsyncSession, expense_id: int) -> Expense:
    # .unique(): the joined collection loads (participants) duplicate rows.
    result = await db.execute(
        select(Expense).options(*_expense_options()).where(Expense.id == expense_id)
    )
    return result.scalars().unique().one()


async def _memberships(
    db: AsyncSession, group_id: int, user_ids: set[int]
) -> dict[int, Membership]:
    rows = (
        await db.execute(
            select(Membership).where(
                Membership.group_id == group_id, Membership.user_id.in_(user_ids)
            )
        )
    ).scalars().all()
    return {m.user_id: m for m in rows}


async def _display_names(db: AsyncSession, user_ids: set[int]) -> dict[int, str]:
    rows = (await db.execute(select(User.id, User.display_name).where(User.id.in_(user_ids)))).all()
    return {uid: name for uid, name in rows}


async def expense_payload(db: AsyncSession, expense: Expense) -> dict[str, Any]:
    """Full expense dict for the feed row / WS / notification payload (§3.2).

    Contains everything a client needs to update the UI without a REST call:
    id, description, amount (string), currency, payer, exact participant shares.
    """
    group = expense.group
    ids = {expense.payer_id, *[p.user_id for p in expense.participants]}
    names = await _display_names(db, ids)
    return {
        "id": expense.id,
        "group_id": expense.group_id,
        "description": expense.description,
        "comment": expense.comment,
        "amount": currency_service.format_money(expense.amount, group.currency),
        "currency": group.currency,
        "split_method": expense.split_method,
        "category": expense.category,
        "date": expense.date.isoformat(),
        "deleted_at": expense.deleted_at.isoformat() if expense.deleted_at else None,
        "payer": {
            "id": expense.payer_id,
            "display_name": names.get(expense.payer_id, f"user {expense.payer_id}"),
        },
        "participants": [
            {
                "user_id": p.user_id,
                "display_name": names.get(p.user_id, f"user {p.user_id}"),
                "share": currency_service.format_money(p.share, group.currency),
            }
            for p in expense.participants
        ],
    }


class _Plan:
    """Resolved edit plan: field values + per-participant weight/amount."""

    def __init__(self) -> None:
        # (user_id, weight, amount) in earliest-joined order
        self.participants: list[tuple[int, Decimal | None, Decimal | None]] = []
        self.shares: list[int] = []


def _resolve_plan(
    group: Group,
    *,
    method: str,
    amount: Decimal,
    payer_id: int,
    participants_in: list[ParticipantIn] | None,
    stored: dict[int, ExpenseParticipant],
    memberships: dict[int, Membership],
) -> _Plan:
    """Validate the participants for a create/edit and order them by join.

    ``participants_in`` is None on edits that keep the existing set;
    ``stored`` maps user_id -> existing row (weights survive edits, §2.1).
    """
    if participants_in is not None:
        by_id: dict[int, Any] = {}
        for participant in participants_in:
            if participant.user_id in by_id:
                raise ValidationFailed(f"Duplicate participant: user {participant.user_id}")
            by_id[participant.user_id] = participant
        user_ids = set(by_id)
    else:
        by_id = {}
        user_ids = set(stored)

    if payer_id not in user_ids:
        raise ValidationFailed("The payer must be one of the participants")
    missing = user_ids - set(memberships)
    if missing:
        raise ValidationFailed("All participants must be members of the group")

    # Remainder-tiebreak order (§2.1): joined_at asc, then user_id asc.
    ordered = sorted((memberships[uid] for uid in user_ids), key=lambda m: (m.joined_at, m.user_id))
    amount_minor = currency_service.to_minor(amount, group.currency)

    plan = _Plan()
    if method == "weighted":
        weights: list[Decimal] = []
        for m in ordered:
            weight = by_id[m.user_id].weight if m.user_id in by_id else stored[m.user_id].weight
            if weight is None:
                raise ValidationFailed(
                    f"Participant {m.user_id} has no weight; "
                    "weighted splits need a weight for everyone"
                )
            weights.append(weight)
            plan.participants.append((m.user_id, weight, None))
        shares = splits_service.split_weighted(amount_minor, weights)
    elif method == "exact":
        amount_minors: list[int] = []
        for m in ordered:
            participant_amount = by_id[m.user_id].amount if m.user_id in by_id else None
            if participant_amount is None:
                raise ValidationFailed(
                    f"Participant {m.user_id} has no amount; "
                    "exact splits need an amount for everyone"
                )
            amount_minors.append(currency_service.to_minor(participant_amount, group.currency))
            plan.participants.append((m.user_id, None, participant_amount))
        shares = splits_service.split_exact(amount_minor, amount_minors)
    else:  # equal
        shares = splits_service.split_equal(amount_minor, len(ordered))
        plan.participants = [(m.user_id, None, None) for m in ordered]

    plan.shares = shares
    return plan


async def create_expense(
    db: AsyncSession, group: Group, user: User, data: ExpenseCreate
) -> Expense:
    if group.status == "closed":
        raise Conflict("The group is closed; reopen it before adding expenses")
    memberships = await _memberships(db, group.id, {p.user_id for p in data.participants})
    plan = _resolve_plan(
        group,
        method=data.split_method,
        amount=data.amount,
        payer_id=data.payer_id,
        participants_in=data.participants,
        stored={},
        memberships=memberships,
    )
    expense = Expense(
        group_id=group.id,
        payer_id=data.payer_id,
        created_by=user.id,
        description=data.description.strip(),
        comment=data.comment,
        amount=data.amount,
        split_method=data.split_method,
        category=data.category,
        date=data.date,
    )
    db.add(expense)
    # Append through the relationship (the cascade persists the rows and the
    # in-memory collection stays populated for the payload below).
    for (user_id, weight, _amount), share_minor in zip(plan.participants, plan.shares, strict=True):
        expense.participants.append(
            ExpenseParticipant(
                user_id=user_id,
                share=currency_service.from_minor(share_minor, group.currency),
                weight=weight,
            )
        )
    await db.flush()
    payload = await expense_payload(db, expense)
    await feed_service.add_event(
        db,
        group.id,
        "expense.created",
        actor_id=user.id,
        ref_type="expense",
        ref_id=expense.id,
        payload=payload,
    )
    notifications = await notifications_service.notify_users(
        db, {p[0] for p in plan.participants}, "expense.created", payload
    )
    await db.commit()
    await ws_hook.emit_group(db, group, "expense.created", payload)
    await ws_hook.emit_notifications(group.id, notifications)
    return await _load_expense(db, expense.id)


async def list_expenses(db: AsyncSession, group: Group) -> list[Expense]:
    """Active (non-deleted) expenses, newest date first (§3.5/§2.5)."""
    result = await db.execute(
        select(Expense)
        .options(*_expense_options())
        .where(Expense.group_id == group.id, Expense.deleted_at.is_(None))
        .order_by(Expense.date.desc(), Expense.id.desc())
    )
    return list(result.scalars().unique().all())


async def get_expense(db: AsyncSession, expense_id: int) -> Expense:
    """Detail view — soft-deleted rows are returned too (with ``deleted_at``)."""
    try:
        return await _load_expense(db, expense_id)
    except LookupError as exc:
        raise NotFound("Expense not found") from exc


async def update_expense(
    db: AsyncSession, expense: Expense, user: User, data: ExpenseUpdate
) -> Expense:
    group = expense.group
    if user.id != expense.created_by and user.id != group.created_by:
        raise Forbidden("Only the expense creator or the group owner can edit it")
    if expense.deleted_at is not None:
        raise Conflict("The expense has been deleted")

    method = data.split_method if data.split_method is not None else expense.split_method
    amount = data.amount if data.amount is not None else expense.amount
    payer_id = data.payer_id if data.payer_id is not None else expense.payer_id
    if data.participants is not None:
        participant_ids = {p.user_id for p in data.participants}
    else:
        participant_ids = {p.user_id for p in expense.participants}
    memberships = await _memberships(db, group.id, participant_ids)

    stored = {p.user_id: p for p in expense.participants}
    if data.participants is None and method == "exact":
        # §2.1: on edit, exact amounts must be re-supplied (they are not stored).
        raise ValidationFailed("Exact splits require explicit participant amounts on every edit")
    plan = _resolve_plan(
        group,
        method=method,
        amount=amount,
        payer_id=payer_id,
        participants_in=data.participants,
        stored=stored,
        memberships=memberships,
    )

    # Rebuild the participant set in place: clear() + the delete-orphan
    # cascade removes the old rows, the appends insert the new ones.
    expense.participants.clear()
    for (user_id, weight, _amount), share_minor in zip(plan.participants, plan.shares, strict=True):
        expense.participants.append(
            ExpenseParticipant(
                user_id=user_id,
                share=currency_service.from_minor(share_minor, group.currency),
                weight=weight,
            )
        )

    expense.payer_id = payer_id
    if data.description is not None:
        expense.description = data.description.strip()
    expense.amount = amount
    expense.split_method = method
    if data.category is not None:
        expense.category = data.category
    if data.date is not None:
        expense.date = data.date
    # A comment is only cleared when the key is explicitly present (as null).
    if "comment" in data.model_fields_set:
        expense.comment = data.comment
    await db.flush()

    payload = await expense_payload(db, expense)
    await feed_service.add_event(
        db,
        group.id,
        "expense.updated",
        actor_id=user.id,
        ref_type="expense",
        ref_id=expense.id,
        payload=payload,
    )
    notifications = await notifications_service.notify_users(
        db, {p[0] for p in plan.participants}, "expense.updated", payload
    )
    await db.commit()
    await ws_hook.emit_group(db, group, "expense.updated", payload)
    await ws_hook.emit_notifications(group.id, notifications)
    return await _load_expense(db, expense.id)


async def delete_expense(db: AsyncSession, expense: Expense, user: User) -> None:
    group = expense.group
    if user.id != expense.created_by and user.id != group.created_by:
        raise Forbidden("Only the expense creator or the group owner can delete it")
    if expense.deleted_at is not None:
        raise NotFound("Expense not found")
    expense.deleted_at = datetime.now(UTC)
    payload = {
        "id": expense.id,
        "description": expense.description,
        "amount": currency_service.format_money(expense.amount, group.currency),
        "currency": group.currency,
    }
    await feed_service.add_event(
        db,
        group.id,
        "expense.deleted",
        actor_id=user.id,
        ref_type="expense",
        ref_id=expense.id,
        payload=payload,
    )
    await db.commit()
    await ws_hook.emit_group(db, group, "expense.deleted", payload)


# --- Receipts (§3.5) ---------------------------------------------------------


async def upload_receipt(
    db: AsyncSession,
    expense: Expense,
    filename: str,
    content_type: str,
    data: bytes,
) -> Attachment:
    if expense.deleted_at is not None:
        raise Conflict("Cannot attach a receipt to a deleted expense")
    if expense.group.status == "closed":
        raise Conflict("The group is closed; reopen it before uploading receipts")
    settings = get_settings()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise ValidationFailed(f"File exceeds the {settings.max_upload_mb} MB limit")
    ext = _CONTENT_TYPE_EXT.get(content_type.lower())
    if ext is None:
        raise ValidationFailed(
            "Only image/jpeg, image/png, image/gif, image/webp or application/pdf are allowed"
        )
    directory = Path(settings.upload_dir)
    directory.mkdir(parents=True, exist_ok=True)
    new_name = f"{uuid.uuid4().hex}.{ext}"
    (directory / new_name).write_bytes(data)

    attachment = expense.attachment
    if attachment is None:
        attachment = Attachment(
            expense_id=expense.id, filename=new_name, content_type=content_type, size=len(data)
        )
        db.add(attachment)
    else:
        # Replace in place: same row (same id), old file removed from disk.
        old_path = directory / attachment.filename
        attachment.filename = new_name
        attachment.content_type = content_type
        attachment.size = len(data)
        # The old file may already be gone (e.g. volume wiped); the row is replaced anyway.
        with contextlib.suppress(OSError):
            old_path.unlink()
    await db.commit()
    await db.refresh(attachment)
    return attachment


async def delete_receipt(db: AsyncSession, expense: Expense) -> None:
    attachment = expense.attachment
    if attachment is None:
        raise NotFound("This expense has no receipt")
    filename = attachment.filename  # the object is detached after the commit
    await db.delete(attachment)
    await db.commit()
    with contextlib.suppress(OSError):
        (Path(get_settings().upload_dir) / filename).unlink()


async def read_attachment(db: AsyncSession, attachment: Attachment) -> tuple[Attachment, bytes]:
    """File bytes for streaming; 404 if the row exists but the file is gone."""
    path = Path(get_settings().upload_dir) / attachment.filename
    if not path.is_file():
        raise NotFound("The receipt file is missing on disk")
    return attachment, path.read_bytes()
