"""The cross-instance WS bus in isolation (AGENTS.md §3.2, Redis scaling).

Drives the real ``RedisBus`` (two replicas over one ``fakeredis`` server)
without a database: publishes an envelope through one replica's bus and
checks that *both* replicas' consumer tasks apply it to their own local
registry — the core property that lets a REST write on one replica reach a
socket sitting on another.

Covered:
  * group fan-out reaches a socket on the publisher's replica **and** one on
    the other replica (symmetric delivery, exactly once each);
  * user-scoped fan-out reaches only the named user's socket, on any replica;
  * the join control (``k:"s"``) subscribes a user's socket on the *other*
    replica; the leave control (``k:"x"``) stops it (the leaver-tab leak fix);
  * a publish failure (Redis down) falls back to local delivery on the
    publisher's replica (fail-open, at-most-once elsewhere);
  * a malformed envelope is dropped without killing the consumer.
"""

from __future__ import annotations

import asyncio
import json

from app.ws import bus as ws_bus
from tests import bus_util


async def _group_message(group_id: int = 5) -> str:
    return json.dumps(
        {"type": "expense.created", "group_id": group_id, "data": {"amount": "1.00"}}
    )


async def test_group_fanout_reaches_both_replicas(replicas) -> None:
    rep0, rep1 = replicas
    s0 = rep0.connect(1)
    rep0.subscribe(s0, 5)
    s1 = rep1.connect(2)
    rep1.subscribe(s1, 5)

    await rep0.bus.publish_group(5, await _group_message())
    await bus_util.wait_until(lambda: len(s0.of_type("expense.created", 5)) == 1)
    await bus_util.wait_until(lambda: len(s1.of_type("expense.created", 5)) == 1)

    # exactly once each, identical payload (the forwarded bytes)
    assert len(s0.of_type("expense.created", 5)) == 1
    assert len(s1.of_type("expense.created", 5)) == 1
    assert s0.of_type("expense.created", 5)[0] == s1.of_type("expense.created", 5)[0]
    assert s0.of_type("expense.created", 5)[0]["data"]["amount"] == "1.00"


async def test_user_fanout_only_named_user(replicas) -> None:
    rep0, rep1 = replicas
    s0 = rep0.connect(1)  # user 1
    s1 = rep1.connect(2)  # user 2 (other replica)

    await rep0.bus.publish_users(
        {1}, json.dumps({"type": "notification.read", "group_id": None, "data": {}})
    )
    await bus_util.wait_until(lambda: len(s0.of_type("notification.read")) == 1)
    # give the consumer a beat to (not) deliver to user 2
    await asyncio.sleep(0.1)
    assert s1.of_type("notification.read") == []
    assert len(s0.of_type("notification.read")) == 1


async def test_join_control_subscribes_other_replica(replicas) -> None:
    rep0, rep1 = replicas
    # user 7's socket lives on replica 1, subscribed to nothing yet
    s1 = rep1.connect(7)

    await rep0.bus.user_subscribe(7, 9)
    # the control must land on replica 1 before a group event can reach it:
    # probe by publishing the group event and waiting for delivery.
    await rep0.bus.publish_group(9, await _group_message(9))
    await bus_util.wait_until(lambda: len(s1.of_type("expense.created", 9)) == 1)
    assert len(s1.of_type("expense.created", 9)) == 1


async def test_leave_control_unsubscribes_other_replica(replicas) -> None:
    rep0, rep1 = replicas
    s1 = rep1.connect(7)
    rep1.subscribe(s1, 9)

    # a pre-leave event is delivered (sanity: the socket is subscribed)
    await rep0.bus.publish_group(9, await _group_message(9))
    await bus_util.wait_until(lambda: len(s1.of_type("expense.created", 9)) == 1)

    await rep0.bus.user_unsubscribe(7, 9)
    await rep0.bus.publish_group(9, await _group_message(9))
    # the second event must NOT arrive (the socket was unsubscribed)
    await asyncio.sleep(0.2)
    assert len(s1.of_type("expense.created", 9)) == 1  # still just the first


async def test_publish_failure_falls_back_to_local(replicas) -> None:
    rep0, rep1 = replicas
    s0 = rep0.connect(1)
    rep0.subscribe(s0, 5)
    s1 = rep1.connect(2)
    rep1.subscribe(s1, 5)

    # take Redis down: PUBLISH raises, so the publisher must deliver locally
    assert rep0.server is not None
    rep0.server.connected = False
    try:
        await rep0.bus.publish_group(5, await _group_message())
    finally:
        rep0.server.connected = True

    # the publisher's own socket still got it (local fallback)...
    assert len(s0.of_type("expense.created", 5)) == 1
    # ...but the other replica did not (at-most-once across the outage)
    assert s1.of_type("expense.created", 5) == []


async def test_malformed_envelope_is_dropped(replicas) -> None:
    rep0, rep1 = replicas
    s0 = rep0.connect(1)
    rep0.subscribe(s0, 5)

    # raw garbage straight onto the shared channel (bypasses the bus API)
    assert rep0.client is not None
    await rep0.client.publish(ws_bus.CHANNEL, "this is not json")
    await rep0.client.publish(ws_bus.CHANNEL, json.dumps({"k": "nope", "id": 5}))
    # then a real event: the consumer must still be alive to deliver it
    await rep0.bus.publish_group(5, await _group_message())
    await bus_util.wait_until(lambda: len(s0.of_type("expense.created", 5)) == 1)
    assert len(s0.of_type("expense.created", 5)) == 1


async def test_local_bus_delivers_in_process() -> None:
    # LocalBus (no Redis) is the pre-Redis path: direct manager fan-out.
    from app.ws.registry import ConnectionManager

    manager = ConnectionManager()
    bus = ws_bus.LocalBus(manager)
    await bus.start()
    try:
        socket = bus_util.FakeSocket()
        manager.connect(socket, 1)
        manager.subscribe_group(socket, 5)
        await bus.publish_group(5, await _group_message())
        assert len(socket.of_type("expense.created", 5)) == 1
    finally:
        await bus.close()
