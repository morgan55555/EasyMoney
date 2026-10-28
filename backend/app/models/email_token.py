"""Email confirmation tokens: registration, email-change and password-change
(AGENTS.md §2.13).

A single table serves all three confirmation flows, distinguished by
``purpose``. Tokens are 128-bit URL-safe secrets. A token is valid while
``status`` is ``"pending"`` and unexpired; confirming marks it ``used``.
Reissuing (resend / a new change request) revokes the previous ``pending``
token. Password-change tokens additionally carry the pending new-password
hash in ``password_hash`` (applied to the user only on confirmation).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import BaseModel

if TYPE_CHECKING:
    from app.models.user import User

# --- purpose values ---
PURPOSE_REGISTER = "register"
PURPOSE_CHANGE = "change"
PURPOSE_PASSWORD = "password"

# --- status values ---
STATUS_PENDING = "pending"
STATUS_USED = "used"
STATUS_REVOKED = "revoked"


class EmailToken(BaseModel):
    __tablename__ = "email_tokens"
    __table_args__ = (Index("ix_email_tokens_user_purpose", "user_id", "purpose"),)

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # "register" | "change" | "password" (see the PURPOSE_* constants above).
    purpose: Mapped[str] = mapped_column(String(8), nullable=False)
    # URL-safe 128-bit token, unique.
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # The address the link is sent to; for "change", the new (target) email.
    target_email: Mapped[str] = mapped_column(String(320), nullable=False)
    # "pending" | "used" | "revoked".
    status: Mapped[str] = mapped_column(String(8), nullable=False, default=STATUS_PENDING)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Only for "password" tokens: the argon2 hash of the pending new password
    # (same type as ``users.password_hash``). Applied to the user only when
    # the token is confirmed; NULL otherwise.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)

    user: Mapped[User] = relationship(back_populates="email_tokens")
