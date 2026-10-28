"""Tracks the last overdue-debt reminder sent per (group, debtor) (§2.7).

Unique per pair so the scheduler can enforce the at-most-once-per-week cap.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import BaseModel

if TYPE_CHECKING:
    from app.models.group import Group
    from app.models.user import User


class DebtReminder(BaseModel):
    __tablename__ = "debt_reminders"
    __table_args__ = (
        Index("uq_debt_reminders_group_debtor", "group_id", "debtor_id", unique=True),
    )

    group_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("groups.id", ondelete="CASCADE"),
        nullable=False,
    )
    debtor_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    last_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    group: Mapped[Group] = relationship()
    debtor: Mapped[User] = relationship()
