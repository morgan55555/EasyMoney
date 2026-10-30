"""Web Push subscription, one row per (user, browser push endpoint) (AGENTS.md §2.14).

The endpoint uniquely identifies a browser's push subscription. ``p256dh`` and
``auth`` are the base64url-encoded keys the push service needs to encrypt the
payload; they are opaque to the app and only forwarded to ``webpush`` at send
time.
"""

from __future__ import annotations

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import BaseModel


class PushSubscription(BaseModel):
    __tablename__ = "push_subscriptions"
    __table_args__ = (
        UniqueConstraint("user_id", "endpoint", name="uq_push_subscriptions_user_endpoint"),
        Index("ix_push_subscriptions_user_id", "user_id"),
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # The push service endpoint URL. Unique per user (one browser may hold
    # several subscriptions; they are distinct endpoints).
    endpoint: Mapped[str] = mapped_column(String(512), nullable=False)
    # Application server key (EC P-256), base64url.
    p256dh: Mapped[str] = mapped_column(Text, nullable=False)
    # Auth secret, base64url.
    auth: Mapped[str] = mapped_column(Text, nullable=False)
