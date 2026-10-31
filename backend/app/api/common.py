"""Shared response builders for the API layer."""

from __future__ import annotations

from decimal import Decimal

from app.core.config import get_settings
from app.models.attachment import Attachment
from app.models.expense import Expense
from app.models.group import Group
from app.models.settlement import Settlement
from app.schemas.expenses import AttachmentOut, ExpenseOut, ParticipantOut
from app.schemas.groups import GroupOut
from app.schemas.settlements import SettlementOut
from app.schemas.user import avatar_url
from app.services import currency as currency_service


def build_group_out(group: Group, member_count: int, balance: Decimal) -> GroupOut:
    settings = get_settings()
    return GroupOut(
        id=group.id,
        name=group.name,
        currency=group.currency,
        status=group.status,
        created_by=group.created_by,
        closed_at=group.closed_at,
        member_count=member_count,
        balance=currency_service.format_money(balance, group.currency),
        debt_overdue_days=group.debt_overdue_days,
        reminder_interval_days=group.reminder_interval_days,
        debt_overdue_days_default=settings.debt_overdue_days_default,
        reminder_interval_days_default=settings.reminder_interval_days_default,
        reminder_interval_days_min=settings.reminder_interval_days_min,
    )


def build_attachment_out(attachment: Attachment) -> AttachmentOut:
    return AttachmentOut(
        id=attachment.id,
        filename=attachment.filename,
        content_type=attachment.content_type,
        size=attachment.size,
    )


def build_expense_out(expense: Expense) -> ExpenseOut:
    """Serialize an expense (relationships must be eager-loaded by the caller)."""
    group = expense.group
    currency = group.currency
    return ExpenseOut(
        id=expense.id,
        group_id=expense.group_id,
        payer_id=expense.payer_id,
        payer=expense.payer.display_name if expense.payer else f"user {expense.payer_id}",
        payer_avatar_url=(
            avatar_url(expense.payer_id, expense.payer.avatar_data) if expense.payer else None
        ),
        created_by=expense.created_by,
        description=expense.description,
        comment=expense.comment,
        amount=currency_service.format_money(expense.amount, currency),
        split_method=expense.split_method,
        category=expense.category,
        date=expense.date,
        deleted_at=expense.deleted_at,
        participants=[
            ParticipantOut(
                user_id=p.user_id,
                display_name=p.user.display_name if p.user else f"user {p.user_id}",
                share=currency_service.format_money(p.share, currency),
                weight=str(p.weight) if p.weight is not None else None,
                avatar_url=avatar_url(p.user_id, p.user.avatar_data) if p.user else None,
            )
            for p in expense.participants
        ],
        attachment=build_attachment_out(expense.attachment) if expense.attachment else None,
        created_at=expense.created_at,
    )


def build_settlement_out(settlement: Settlement) -> SettlementOut:
    """Serialize a settlement (relationships must be eager-loaded by the caller)."""
    return SettlementOut(
        id=settlement.id,
        group_id=settlement.group_id,
        from_user_id=settlement.from_user_id,
        from_name=(
            settlement.from_user.display_name
            if settlement.from_user
            else f"user {settlement.from_user_id}"
        ),
        from_avatar_url=(
            avatar_url(settlement.from_user_id, settlement.from_user.avatar_data)
            if settlement.from_user
            else None
        ),
        to_user_id=settlement.to_user_id,
        to_name=(
            settlement.to_user.display_name
            if settlement.to_user
            else f"user {settlement.to_user_id}"
        ),
        to_avatar_url=(
            avatar_url(settlement.to_user_id, settlement.to_user.avatar_data)
            if settlement.to_user
            else None
        ),
        amount=currency_service.format_money(settlement.amount, settlement.group.currency),
        state=settlement.state,
        confirmed_at=settlement.confirmed_at,
        created_at=settlement.created_at,
    )
