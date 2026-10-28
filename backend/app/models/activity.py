"""Append-only activity feed row (AGENTS.md §3.3).

No ``updated_at``: feed rows are never mutated, only appended. Soft-deleted
expenses still render here ("deleted an expense").
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IDMixin

if TYPE_CHECKING:
    from app.models.group import Group
    from app.models.user import User


class ActivityEvent(Base, IDMixin):
    __tablename__ = "activity"

    group_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("groups.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Mirrors the WS event catalogue plus member.left / close / reopen.
    type: Mapped[str] = mapped_column(String(40), nullable=False)
    # The user who caused the event; NULL for system events.
    actor_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id"),
        nullable=True,
    )
    ref_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    ref_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # §3.7: activity(group_id, created_at desc). Column object is referenced
    # here, after its definition, for the explicit descending order.
    __table_args__ = (
        Index("ix_activity_group_created", "group_id", created_at.desc()),
    )

    group: Mapped[Group] = relationship(back_populates="activity")
    actor: Mapped[User] = relationship()
