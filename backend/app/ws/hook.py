"""Post-commit WebSocket emission hook (AGENTS.md §3.1, §3.2).

Every money- or membership-changing service calls into this module **after**
its transaction commits:

* ``emit_group(db, group, type, data)`` — fan a group event out to every
  subscribed socket of the group, with a fresh on-read ``group_balances``
  snapshot injected into ``data`` (invariant 4: the aggregate is already
  correct post-commit, so the push is just a re-read);
* ``emit_notifications(group_id, rows)`` — user-scoped fan-out of the bell:
  each notified user's tabs learn of their own notification row;
* ``resubscribe_user(user_id, group_id)`` — add a group to every open socket
  of the user (mid-session join, AGENTS.md §3.2).

The app plugs the registry in at startup via :func:`set_manager`; without a
manager the calls are no-ops, so the pure DB service tests stay WS-free.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.group import Group
from app.models.notification import Notification
from app.services import balances as balances_service
from app.services import currency as currency_service
from app.ws.registry import ConnectionManager

_manager: ConnectionManager | None = None


def set_manager(manager: ConnectionManager | None) -> None:
    """Called from the app lifespan: plug the registry in (or out)."""
    global _manager
    _manager = manager


def get_manager() -> ConnectionManager | None:
    return _manager


def _transfer_dict(t: balances_service.Transfer, cur: str) -> dict[str, Any]:
    return {
        "from_user_id": t.from_user_id,
        "to_user_id": t.to_user_id,
        "amount": currency_service.format_money(t.amount, cur),
    }


def snapshot_to_dict(snapshot: balances_service.GroupBalanceSnapshot) -> dict[str, Any]:
    """The fresh balance state as wire JSON.

    Same shape as ``GET /groups/{id}/balances`` (B6): per-user balances, the
    pairwise view and the suggested transfers — money as strings (§2.12).
    """
    cur = snapshot.currency
    return {
        "currency": cur,
        "balances": [
            {
                "user_id": b.user_id,
                "display_name": b.display_name,
                "balance": currency_service.format_money(b.balance, cur),
            }
            for b in snapshot.balances
        ],
        "pairs": [_transfer_dict(t, cur) for t in snapshot.pairs],
        "transfers": [_transfer_dict(t, cur) for t in snapshot.transfers],
    }


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
    if _manager is None:
        return
    snapshot = await balances_service.group_balance_snapshot(db, group)
    message = json.dumps(
        {
            "type": type,
            "group_id": group.id,
            "data": {**data, "group_balances": snapshot_to_dict(snapshot)},
        }
    )
    await _manager.broadcast_to_group(group.id, message)


async def emit_notifications(group_id: int, notifications: list[Notification]) -> None:
    """Bell event: one ``notification.created`` per row, to that user only."""
    if _manager is None:
        return
    for notification in notifications:
        message = json.dumps(
            {
                "type": "notification.created",
                "group_id": group_id,
                "data": notification_data(notification),
            }
        )
        await _manager.broadcast_to_users({notification.user_id}, message)


def resubscribe_user(user_id: int, group_id: int) -> None:
    """Add the group to every open socket of the user (mid-session join)."""
    if _manager is not None:
        _manager.subscribe_user_to_group(user_id, group_id)
