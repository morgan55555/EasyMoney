"""Settlement: a money movement from one member to another (§2.4).

Lifecycle ``pending -> confirmed | rejected | cancelled``. Balances move only
on confirmation, and only then via the on-read aggregate (§2.2).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import BaseModel

if TYPE_CHECKING:
    from app.models.group import Group
    from app.models.user import User


class Settlement(BaseModel):
    __tablename__ = "settlements"
    __table_args__ = (
        Index("ix_settlements_group_state", "group_id", "state"),
    )

    group_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("groups.id", ondelete="CASCADE"),
        nullable=False,
    )
    from_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id"),
        nullable=False,
    )
    to_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id"),
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    # "pending" | "confirmed" | "rejected" | "cancelled"
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # --- relationships ---
    group: Mapped[Group] = relationship(back_populates="settlements")
    from_user: Mapped[User] = relationship(foreign_keys=[from_user_id])
    to_user: Mapped[User] = relationship(foreign_keys=[to_user_id])
