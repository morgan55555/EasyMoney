"""User account (AGENTS.md §2.11). No money ever lives on the user row."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import BaseModel

if TYPE_CHECKING:
    from app.models.expense import Expense
    from app.models.group import Group
    from app.models.membership import Membership
    from app.models.notification import Notification
    from app.models.token import RefreshToken


class User(BaseModel):
    """A registered account. ``email`` is unique, case-insensitively: the
    service layer normalizes it to lowercase before persisting, so a plain
    unique column enforces case-insensitive uniqueness."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)

    # --- relationships ---
    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    created_groups: Mapped[list[Group]] = relationship(
        back_populates="creator",
    )
    memberships: Mapped[list[Membership]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    paid_expenses: Mapped[list[Expense]] = relationship(
        foreign_keys="Expense.payer_id",
        back_populates="payer",
    )
    created_expenses: Mapped[list[Expense]] = relationship(
        foreign_keys="Expense.created_by",
        back_populates="creator",
    )
    notifications: Mapped[list[Notification]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
