"""A participant's exact share of an expense — the authoritative split result.

Stored shares always sum to the expense amount (AGENTS.md §2.1, invariant 2).
For weighted splits the stored ``weight`` is kept so an edit can re-apply the
same weights to a new amount.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, ForeignKey, Index, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import BaseModel

if TYPE_CHECKING:
    from app.models.expense import Expense
    from app.models.user import User


class ExpenseParticipant(BaseModel):
    __tablename__ = "expense_participants"
    __table_args__ = (
        Index("ix_expense_participants_expense_user", "expense_id", "user_id"),
    )

    expense_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("expenses.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id"),
        nullable=False,
    )
    # The exact share this participant owes for the expense (minor units).
    share: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    # Present only for "weighted" splits; reused on edit (§2.1).
    weight: Mapped[Decimal | None] = mapped_column(Numeric(14, 3), nullable=True)

    expense: Mapped[Expense] = relationship(back_populates="participants")
    user: Mapped[User] = relationship()
