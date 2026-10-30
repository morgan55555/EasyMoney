"""Post-commit WebSocket emission hook (AGENTS.md §3.1, §3.2).

Every money- or membership-changing service calls into this module **after**
its transaction commits:

* ``emit_group(db, group, type, data)`` — fan a group event out to every
  subscribed socket of the group, with a fresh on-read ``group_balances``
  snapshot injected into ``data`` (invariant 4: the aggregate is already
  correct post-commit, so the push is just a re-read);
* ``emit_notifications(group_id, rows)`` — user-scoped fan-out of the bell:
  each notified user's tabs learn of their own notification row; every row
  additionally fires a Web Push task (``services/push``, §2.14);
* ``emit_notification_read(user_id)`` — user-scoped read-state change: every
  open tab of the user refetches the bell so a mark-read in one tab clears the
  unread badge in the others (AGENTS.md §2.6, req 7);
* ``resubscribe_user(user_id, group_id)`` — add a group to every open socket
  of the user (mid-session join, AGENTS.md §3.2);
* ``unsubscribe_user(user_id, group_id)`` — remove a group from every open
  socket of the user (mid-session leave/remove): the leaver's tabs stop
  receiving the group's events immediately.

Fan-out goes through the event bus (``app.ws.bus``) when one is plugged in —
``LocalBus`` in single-process mode, ``RedisBus`` behind a load balancer —
otherwise straight to the process-local registry. With Redis, every group
emit also refreshes the balance-snapshot cache key with the same wire dict it
pushes (exact invalidation; the TTL is only the safety net).

The app plugs the registry (and bus) in at startup via :func:`set_manager` /
:func:`set_bus`; without a manager the calls are no-ops, so the pure DB
service tests stay WS-free.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.group import Group
from app.models.notification import Notification
from app.services import balances as balances_service
from app.services import push as push_service
from app.ws import user_groups
from app.ws.bus import EventBus
from app.ws.registry import ConnectionManager

_manager: ConnectionManager | None = None
_bus: EventBus | None = None


def set_manager(manager: ConnectionManager | None) -> None:
    """Called from the app lifespan: plug the registry in (or out)."""
    global _manager
    _manager = manager


def get_manager() -> ConnectionManager | None:
    return _manager


def set_bus(bus: EventBus | None) -> None:
    """Called from the app lifespan: plug the cross-instance bus in (or out)."""
    global _bus
    _bus = bus


def get_bus() -> EventBus | None:
    return _bus


def snapshot_to_dict(snapshot: balances_service.GroupBalanceSnapshot) -> dict[str, Any]:
    """The fresh balance state as the wire dict.

    Same shape as ``GET /groups/{id}/balances``: per-user balances, the
    pairwise view and the suggested transfers — money as strings (§2.12).
    Delegates to the service so the WS push and the (cached) REST read share
    one builder.
    """
    return balances_service.snapshot_to_wire(snapshot)


def notification_data(notification: Notification) -> dict[str, Any]:
    """The bell event's data: the notification row itself (AGENTS.md §3.2)."""
    return {
        "id": notification.id,
        "type": notification.type,
        "payload": notification.payload,
        "created_at": notification.created_at.isoformat(),
    }


async def emit_group(db: AsyncSession, group: Group, type: str, data: dict[str, Any]) -> None:
    """Group event: fresh snapshot in, fan-out to all subscribed sockets out."""
    if _bus is None and _manager is None:
        return
    snapshot = await balances_service.group_balance_snapshot(db, group)
    wire = balances_service.snapshot_to_wire(snapshot)
    message = json.dumps(
        {
            "type": type,
            "group_id": group.id,
            "data": {**data, "group_balances": wire},
        }
    )
    # Exact invalidation of the snapshot cache: the same wire dict that goes
    # over the WS lands in Redis (no-op in local mode, fail-open on errors).
    await balances_service.refresh_snapshot_cache(group.id, wire)
    if _bus is not None:
        await _bus.publish_group(group.id, message)
    elif _manager is not None:
        await _manager.broadcast_to_group(group.id, message)


async def emit_notifications(group_id: int, notifications: list[Notification]) -> None:
    """Bell event: one ``notification.created`` per row, to that user only.

    Independent of the WebSocket fan-out, every row also fires a Web Push
    task (``services.push.push_notification_row``, AGENTS.md §2.14): a
    fire-and-forget delivery to the user's browser subscriptions that reaches
    the OS notification even when the app is fully closed. Without a sender
    plugged in (VAPID keys unset) the tasks no-op — exactly today's behaviour.
    """
    for notification in notifications:
        push_service.track(push_service.push_notification_row(notification.id))
    if _bus is None and _manager is None:
        return
    for notification in notifications:
        message = json.dumps(
            {
                "type": "notification.created",
                "group_id": group_id,
                "data": notification_data(notification),
            }
        )
        if _bus is not None:
            await _bus.publish_users({notification.user_id}, message)
        elif _manager is not None:
            await _manager.broadcast_to_users({notification.user_id}, message)


async def emit_notification_read(user_id: int) -> None:
    """Read-state change: one ``notification.read`` to every tab of the user.

    The client only needs the signal to refetch the bell (the list endpoint is
    the source of truth), so the payload carries no data.
    """
    if _bus is None and _manager is None:
        return
    message = json.dumps({"type": "notification.read", "group_id": None, "data": {}})
    if _bus is not None:
        await _bus.publish_users({user_id}, message)
    elif _manager is not None:
        await _manager.broadcast_to_users({user_id}, message)


async def resubscribe_user(user_id: int, group_id: int) -> None:
    """Add the group to every open socket of the user (mid-session join)."""
    if _bus is not None:
        await _bus.user_subscribe(user_id, group_id)
    elif _manager is not None:
        _manager.subscribe_user_to_group(user_id, group_id)
    await user_groups.invalidate_user_groups(user_id)


async def unsubscribe_user(user_id: int, group_id: int) -> None:
    """Remove the group from every open socket of the user (leave/remove).

    Called *before* the ``member.left`` emit, so the leaver's tabs stop
    receiving the group's events immediately — including the leave event
    itself, which they no longer concern.
    """
    if _bus is not None:
        await _bus.user_unsubscribe(user_id, group_id)
    elif _manager is not None:
        _manager.unsubscribe_user_from_group(user_id, group_id)
    await user_groups.invalidate_user_groups(user_id)
