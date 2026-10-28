"""Group: a shared-expense circle with its own immutable currency (§2.9, §2.12)."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import BaseModel

if TYPE_CHECKING:
    from app.models.activity import ActivityEvent
    from app.models.expense import Expense
    from app.models.invitation import Invitation
    from app.models.membership import Membership
    from app.models.settlement import Settlement
    from app.models.user import User


class Group(BaseModel):
    __tablename__ = "groups"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # ISO 4217 code, set at creation and immutable (enforced by the service).
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    created_by: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id"),
        nullable=False,
    )
    # "active" | "closed"
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- relationships ---
    creator: Mapped[User] = relationship(back_populates="created_groups")
    memberships: Mapped[list[Membership]] = relationship(
        back_populates="group",
        cascade="all, delete-orphan",
    )
    invitations: Mapped[list[Invitation]] = relationship(
        back_populates="group",
        cascade="all, delete-orphan",
    )
    expenses: Mapped[list[Expense]] = relationship(
        back_populates="group",
        cascade="all, delete-orphan",
    )
    settlements: Mapped[list[Settlement]] = relationship(
        back_populates="group",
        cascade="all, delete-orphan",
    )
    activity: Mapped[list[ActivityEvent]] = relationship(
        back_populates="group",
        cascade="all, delete-orphan",
    )
