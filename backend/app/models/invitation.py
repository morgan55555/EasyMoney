"""Invitations: join a group by link (reusable) or email (single-use) (§2.10)."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import BaseModel

if TYPE_CHECKING:
    from app.models.group import Group


class Invitation(BaseModel):
    __tablename__ = "invitations"

    group_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("groups.id", ondelete="CASCADE"),
        nullable=False,
    )
    # URL-safe 128-bit token, unique.
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # "link" | "email"
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    # Set only for email-kind invites; the token is bound to this address.
    email_to: Mapped[str | None] = mapped_column(String(320), nullable=True)
    created_by: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id"),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # "active" | "used" | "revoked"
    status: Mapped[str] = mapped_column(String(8), nullable=False, default="active")
    # Link invites stay active for everyone; these record the most recent joiner.
    used_by: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id"),
        nullable=True,
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    group: Mapped[Group] = relationship(back_populates="invitations")
