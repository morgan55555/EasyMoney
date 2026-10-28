"""Group membership. ``joined_at`` is the stable split-remainder tie-break key
(AGENTS.md §2.1, §3.7) and is set by the database, never client-supplied."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import BaseModel

if TYPE_CHECKING:
    from app.models.group import Group
    from app.models.user import User


class Membership(BaseModel):
    __tablename__ = "memberships"
    __table_args__ = (
        Index("uq_memberships_group_user", "group_id", "user_id", unique=True),
    )

    group_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("groups.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Stable ordering key for the equal-split remainder rotation (§2.1).
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    group: Mapped[Group] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships")
