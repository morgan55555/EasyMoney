"""Expense: what someone paid, split among participants (§2.1, §2.5, §3.5)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import BaseModel

if TYPE_CHECKING:
    from app.models.attachment import Attachment
    from app.models.expense_participant import ExpenseParticipant
    from app.models.group import Group
    from app.models.user import User


class Expense(BaseModel):
    __tablename__ = "expenses"
    __table_args__ = (
        # AGENTS.md §3.7: expenses(group_id, date) and expenses.payer.
        Index("ix_expenses_group_date", "group_id", "date"),
        Index("ix_expenses_payer", "payer_id"),
    )

    group_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("groups.id", ondelete="CASCADE"),
        nullable=False,
    )
    payer_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id"),
        nullable=False,
    )
    # Who created the record (edit rights: creator or group owner, §2.5).
    created_by: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id"),
        nullable=False,
    )
    description: Mapped[str] = mapped_column(String(200), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    # "equal" | "weighted" | "exact"
    split_method: Mapped[str] = mapped_column(String(16), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    # Soft delete; excluded from every balance/summary sum (§2.2, §2.5).
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- relationships ---
    group: Mapped[Group] = relationship(back_populates="expenses")
    payer: Mapped[User] = relationship(
        foreign_keys=[payer_id],
        back_populates="paid_expenses",
    )
    creator: Mapped[User] = relationship(
        foreign_keys=[created_by],
        back_populates="created_expenses",
    )
    participants: Mapped[list[ExpenseParticipant]] = relationship(
        back_populates="expense",
        cascade="all, delete-orphan",
        # Deterministic display order: insertion order (participants are
        # written in earliest-joined order, §2.1). Services append/clear
        # this collection directly (never db.add the rows separately) so
        # the in-memory state stays authoritative and no lazy load — a
        # MissingGreenlet under asyncio — is ever triggered.
        order_by="ExpenseParticipant.id",
    )
    # One receipt per expense; uploading replaces the previous attachment.
    attachment: Mapped[Attachment | None] = relationship(
        back_populates="expense",
        uselist=False,
        cascade="all, delete-orphan",
    )
