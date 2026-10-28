"""Declarative base and shared mixins.

Money columns are NUMERIC (see AGENTS.md §3.7); ids are BIGSERIAL. The mixins
below are reused by every model in ``app.models``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all ORM models."""


class IDMixin:
    """Auto-incrementing BIGSERIAL primary key."""

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)


class TimestampMixin:
    """created_at / updated_at, set by the database (UTC)."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class BaseModel(Base, IDMixin, TimestampMixin):
    """Abstract base combining id + timestamps for concrete models."""

    __abstract__ = True
