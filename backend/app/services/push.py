"""Web Push delivery (AGENTS.md §2.14).

OS-level notifications are sent through ``pywebpush`` (``webpush_async``,
aiohttp-backed), **post-commit**, fire-and-forget, with their own DB session:
the push never blocks or fails the triggering request, and it runs on the
replica that wrote the data (so each notification is pushed exactly once in
multi-replica mode) — also when the user has no open sockets.

The sender is pluggable: :func:`set_sender` wires in the real
:class:`WebPushSender` at startup (only when both VAPID keys are configured);
without a sender, :func:`push_notification_row` is a no-op — nothing is sent,
nothing errors (the pre-push behaviour). Subscriptions dead at the push service
(HTTP 404/410) are pruned; other errors are logged and swallowed.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Coroutine
from typing import Any, Protocol, runtime_checkable

from pywebpush import WebPushException, webpush_async
from sqlalchemy import delete, select

from app.db.session import get_session_factory
from app.models.notification import Notification
from app.models.push_subscription import PushSubscription
from app.models.user import User

logger = logging.getLogger("easymoney.push")


class DeadSubscriptionError(Exception):
    """The push service reported the subscription as gone (HTTP 404/410)."""


@runtime_checkable
class PushSender(Protocol):
    """One push to one subscription."""

    async def send(self, endpoint: str, p256dh: str, auth: str, payload: bytes) -> None:
        """Send ``payload`` to ``endpoint``.

        Raises :class:`DeadSubscriptionError` when the subscription no longer
        exists (the row must be pruned); any other error is the sender's
        business to raise — the caller logs and swallows it.
        """


class WebPushSender:
    """Real sender backed by ``pywebpush.webpush_async`` (aiohttp).

    The VAPID private key is the base64url PKCS8 DER produced by
    ``backend/scripts/generate_vapid.py`` — exactly what
    ``py_vapid.Vapid.from_string`` consumes.
    """

    def __init__(self, vapid_private_key: str, vapid_subject: str) -> None:
        self._private_key = vapid_private_key
        self._subject = vapid_subject

    async def send(self, endpoint: str, p256dh: str, auth: str, payload: bytes) -> None:
        subscription = {
            "endpoint": endpoint,
            "keys": {"p256dh": p256dh, "auth": auth},
        }
        try:
            await webpush_async(
                subscription_info=subscription,
                data=payload,
                vapid_private_key=self._private_key,
                vapid_claims={"sub": self._subject},
            )
        except WebPushException as exc:
            # 404 Gone / 410 Gone: the endpoint is dead — prune the row.
            # ``status_code`` is None-safe on every pywebpush failure shape.
            if exc.status_code in (404, 410):
                raise DeadSubscriptionError() from exc
            raise


# ---------------------------------------------------------------------------
# Process-wide registry: the plugged-in sender + in-flight push tasks
# ---------------------------------------------------------------------------

_sender: PushSender | None = None
_tasks: set[asyncio.Task[None]] = set()


def set_sender(sender: PushSender | None) -> None:
    """Called from the app lifespan: plug the sender in (or out)."""
    global _sender
    _sender = sender


def get_sender() -> PushSender | None:
    return _sender


def track(coro: Coroutine[Any, Any, None]) -> None:
    """Run a fire-and-forget push task; it is kept alive until :func:`drain`."""
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def drain() -> None:
    """Await all in-flight pushes (called on shutdown)."""
    pending = list(_tasks)
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


# ---------------------------------------------------------------------------
# The fire path
# ---------------------------------------------------------------------------


async def push_notification_row(notification_id: int) -> None:
    """Deliver one notification row to every push subscription of its user.

    Opens its own session — the triggering transaction is already committed —
    and never raises: every failure path is either a prune or a log line.
    """
    sender = _sender
    if sender is None:
        return
    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                select(Notification, User)
                .join(User, User.id == Notification.user_id)
                .where(Notification.id == notification_id)
            )
        ).first()
        if row is None:
            return
        notification, user = row
        subs = (
            await session.execute(
                select(PushSubscription).where(PushSubscription.user_id == user.id)
            )
        ).scalars().all()
        if not subs:
            return
        payload = json.dumps(
            {
                # The user's language at send time (§2.14): the service worker
                # renders in it even if the browser UI is in another language.
                "lang": user.preferred_language,
                "notification": {
                    "id": notification.id,
                    "type": notification.type,
                    "payload": notification.payload,
                    "created_at": notification.created_at.isoformat(),
                },
            }
        ).encode("utf-8")
        sub_rows = [(s.id, s.endpoint, s.p256dh, s.auth) for s in subs]
    for sub_id, endpoint, p256dh, auth in sub_rows:
        try:
            await sender.send(endpoint, p256dh, auth, payload)
        except DeadSubscriptionError:
            async with factory() as session:
                await session.execute(
                    delete(PushSubscription).where(PushSubscription.id == sub_id)
                )
                await session.commit()
        except Exception:
            logger.exception("push failed for subscription %s", sub_id)
