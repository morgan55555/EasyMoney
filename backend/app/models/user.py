"""User account (AGENTS.md §2.11). No money ever lives on the user row."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import BaseModel

if TYPE_CHECKING:
    from app.models.email_token import EmailToken
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

    # --- profile / account controls (§2.13) ---
    # Whether the user may use the admin panel. The first-ever user is admin.
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Whether the address is confirmed. True by default (existing installs);
    # new users are unverified only when register-confirmation is active.
    email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Language of this user's emails + default UI language ("en" | "ru").
    preferred_language: Mapped[str] = mapped_column(String(2), nullable=False, default="en")
    # Base64 avatar image (no "data:" prefix); null = no avatar.
    avatar_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Matches avatar_data, e.g. "image/png"; null = no avatar.
    avatar_content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Preferred currency (ISO 4217 code, e.g. "USD"); null = none set. Pinned to
    # the top of the currency picker and used as its default in the UI.
    preferred_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)

    # --- relationships ---
    email_tokens: Mapped[list[EmailToken]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
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
