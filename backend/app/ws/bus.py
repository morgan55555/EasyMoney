"""Cross-instance event bus (AGENTS.md §3.2, multi-replica scaling).

The process-local ``ConnectionManager`` (``registry.py``) cannot fan an event
out to sockets that live in *another* replica: behind a load balancer a REST
write can hit replica B while the user's sockets sit on replica A. The bus
bridges instances. Two implementations, selected at boot (``app.main``):

* ``LocalBus`` — single process: direct ``ConnectionManager`` calls, i.e.
  exactly the pre-bus behaviour (``REDIS_URL`` empty);
* ``RedisBus`` — every replica publishes one envelope JSON per event to the
  shared channel ``easymoney:events`` and a per-process consumer task applies
  what it receives to its *own* local manager.

Envelope (one JSON string per publish; ``msg`` is the already-serialized wire
message — it embeds the fresh balance snapshot, so subscriber replicas forward
bytes instead of re-querying):

* ``{"k": "g", "id": <group_id>, "msg": "<wire JSON>"}`` — group data;
* ``{"k": "u", "ids": [<user_id>, ...], "msg": "<wire JSON>"}`` — user data;
* ``{"k": "s", "user": <uid>, "group": <gid>}`` — control: the user joins the
  group (their tabs on *every* replica subscribe);
* ``{"k": "x", "user": <uid>, "group": <gid>}`` — control: the user leaves /
  is removed (their tabs on *every* replica unsubscribe).

Delivery rules:

* **Symmetric delivery** — every replica, including the publisher, delivers
  via its own consumer task; the publisher never calls its manager directly.
  No double-delivery, one code path, crash-agnostic.
* **At-most-once** — Pub/Sub does not redeliver; messages missed during a
  Redis outage or a replica restart are unrecoverable. That is acceptable:
  the client refetches its state over REST on reconnect (AGENTS.md §3.2) and
  balances are computed on read, so the worst case is a delayed "instant"
  push, never wrong data.
* **Publish failure fallback** — when ``PUBLISH`` itself fails (Redis down),
  the event is delivered to the local manager directly (an ERROR is logged):
  a Redis outage degrades to single-instance behaviour, not to zero delivery.
* **Consumer resilience** — on a connection error the consumer backs off
  (1 s → 5 s capped), re-subscribes and keeps going. Local socket
  registrations survive (they are in-process).
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import logging
from collections.abc import Callable
from typing import Any, Protocol

from app.ws.registry import ConnectionManager

logger = logging.getLogger("easymoney.ws.bus")

#: The single shared Pub/Sub channel every replica subscribes to.
CHANNEL = "easymoney:events"


class EventBus(Protocol):
    """What the post-commit hook (:mod:`app.ws.hook`) needs from the bus."""

    async def start(self) -> None:
        """Make the bus usable (Redis: subscribe + start the consumer)."""

    async def close(self) -> None:
        """Stop the bus (Redis: cancel the consumer, close the pubsub)."""

    async def publish_group(self, group_id: int, message: str) -> None:
        """Fan one wire message to every socket subscribed to the group."""

    async def publish_users(self, user_ids: set[int], message: str) -> None:
        """Fan one wire message to every socket of the named users."""

    async def user_subscribe(self, user_id: int, group_id: int) -> None:
        """Control: the user joins the group (every replica re-subscribes)."""

    async def user_unsubscribe(self, user_id: int, group_id: int) -> None:
        """Control: the user leaves the group (every replica unsubscribes)."""


class LocalBus:
    """Single-process bus: direct ``ConnectionManager`` calls (the v1 path)."""

    def __init__(self, manager: ConnectionManager) -> None:
        self._manager = manager

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def publish_group(self, group_id: int, message: str) -> None:
        await self._manager.broadcast_to_group(group_id, message)

    async def publish_users(self, user_ids: set[int], message: str) -> None:
        await self._manager.broadcast_to_users(user_ids, message)

    async def user_subscribe(self, user_id: int, group_id: int) -> None:
        self._manager.subscribe_user_to_group(user_id, group_id)

    async def user_unsubscribe(self, user_id: int, group_id: int) -> None:
        self._manager.unsubscribe_user_from_group(user_id, group_id)


class RedisBus:
    """Pub/Sub bridge: publish to the shared channel, apply locally on read."""

    def __init__(self, redis: Any, manager: ConnectionManager) -> None:
        self._redis = redis
        self._manager = manager
        self._pubsub: Any = None
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._pubsub = self._redis.pubsub()
        await self._pubsub.subscribe(CHANNEL)
        self._task = asyncio.create_task(self._consume())

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._pubsub is not None:
            with contextlib.suppress(Exception):
                await self._pubsub.unsubscribe(CHANNEL)
                await self._pubsub.aclose()
            self._pubsub = None

    # --- publishing (symmetric delivery: never touch the manager directly) ---

    async def publish_group(self, group_id: int, message: str) -> None:
        await self._publish(
            {"k": "g", "id": group_id, "msg": message},
            lambda: self._manager.broadcast_to_group(group_id, message),
        )

    async def publish_users(self, user_ids: set[int], message: str) -> None:
        await self._publish(
            {"k": "u", "ids": sorted(user_ids), "msg": message},
            lambda: self._manager.broadcast_to_users(user_ids, message),
        )

    async def user_subscribe(self, user_id: int, group_id: int) -> None:
        await self._publish(
            {"k": "s", "user": user_id, "group": group_id},
            lambda: self._manager.subscribe_user_to_group(user_id, group_id),
        )

    async def user_unsubscribe(self, user_id: int, group_id: int) -> None:
        await self._publish(
            {"k": "x", "user": user_id, "group": group_id},
            lambda: self._manager.unsubscribe_user_from_group(user_id, group_id),
        )

    async def _publish(
        self, envelope: dict[str, Any], local: Callable[[], Any]
    ) -> None:
        try:
            await self._redis.publish(CHANNEL, json.dumps(envelope))
        except Exception:
            # Redis down: degrade to single-instance delivery rather than
            # dropping the event. Other replicas miss it — the client refetches
            # over REST on reconnect (at-most-once, module docstring).
            logger.error(
                "WS bus PUBLISH to %s failed; falling back to local delivery",
                CHANNEL,
                exc_info=True,
            )
            result = local()
            if inspect.isawaitable(result):
                await result

    # --- the consumer task: apply what this replica receives locally --------

    async def _consume(self) -> None:
        backoff = 1.0
        while True:
            try:
                await self._read_loop()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("WS bus consumer error; reconnecting")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 5.0)
            if await self._resubscribe():
                backoff = 1.0

    async def _read_loop(self) -> None:
        while True:
            message = await self._pubsub.get_message(
                ignore_subscribe_messages=True, timeout=1.0
            )
            if message is not None and message.get("type") == "message":
                await self._apply(message["data"])
            else:
                # No message right now. Real redis already blocked up to the
                # timeout inside ``get_message``; fakeredis polls once and
                # returns immediately, so a short sleep keeps the loop from
                # busy-spinning there (negligible next to the 1 s block).
                await asyncio.sleep(0.01)

    async def _resubscribe(self) -> bool:
        try:
            self._pubsub = self._redis.pubsub()
            await self._pubsub.subscribe(CHANNEL)
            logger.info("WS bus re-subscribed to %s", CHANNEL)
            return True
        except Exception:
            logger.exception("WS bus: re-subscribe failed; will retry")
            return False

    async def _apply(self, data: bytes | str) -> None:
        try:
            envelope = json.loads(data)
        except (TypeError, ValueError):
            logger.warning("WS bus: dropping malformed envelope")
            return
        kind = envelope.get("k")
        try:
            if kind == "g":
                await self._manager.broadcast_to_group(
                    int(envelope["id"]), str(envelope["msg"])
                )
            elif kind == "u":
                await self._manager.broadcast_to_users(
                    {int(i) for i in envelope["ids"]}, str(envelope["msg"])
                )
            elif kind == "s":
                self._manager.subscribe_user_to_group(
                    int(envelope["user"]), int(envelope["group"])
                )
            elif kind == "x":
                self._manager.unsubscribe_user_from_group(
                    int(envelope["user"]), int(envelope["group"])
                )
            else:
                logger.warning("WS bus: unknown envelope kind %r", kind)
        except Exception:
            logger.exception("WS bus: failed to apply envelope kind %r", kind)
