"""Helpers for the multi-replica WS-bus and Redis tests (AGENTS.md §3.2).

A ``Replica`` bundles everything one backend process needs to serve
WebSockets: a process-local ``ConnectionManager`` plus the event bus it is
wired to. In Redis tests two replicas share one ``fakeredis`` ``FakeServer``,
so their ``RedisBus``es talk to the same Pub/Sub channel — exactly what two
real replicas do behind a load balancer. ``FakeSocket`` duck-types a Starlette
WebSocket for the registry's send path.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.ws import bus as ws_bus
from app.ws.registry import ConnectionManager
from tests.conftest import _TRUNCATE, TEST_DATABASE_URL


class FakeSocket:
    """Records every frame the registry sends to it (the send path only)."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(text)

    def events(self) -> list[dict[str, Any]]:
        return [json.loads(t) for t in self.sent]

    def of_type(self, event_type: str, group_id: int | None = None) -> list[dict[str, Any]]:
        events = [e for e in self.events() if e["type"] == event_type]
        if group_id is not None:
            events = [e for e in events if e["group_id"] == group_id]
        return events


class Replica:
    """One backend process: a local socket registry plus its event bus.

    ``client`` is the (fakeredis) Redis client the bus publishes through and
    ``server`` the shared ``FakeServer`` — exposed so a test can publish raw
    envelopes or take Redis down to force a publish failure.
    """

    def __init__(
        self,
        manager: ConnectionManager,
        bus: ws_bus.EventBus,
        client: Any | None = None,
        server: Any | None = None,
    ) -> None:
        self.manager = manager
        self.bus = bus
        self.client = client
        self.server = server

    def connect(self, user_id: int) -> FakeSocket:
        """Open a socket for the user on this replica (returns the fake)."""
        socket = FakeSocket()
        self.manager.connect(socket, user_id)
        return socket

    def subscribe(self, socket: FakeSocket, group_id: int) -> None:
        self.manager.subscribe_group(socket, group_id)


async def make_redis_replicas(server: Any, count: int = 2) -> list[Replica]:
    """``count`` replicas whose buses share one Pub/Sub ``FakeServer``."""
    from fakeredis import FakeAsyncRedis

    replicas: list[Replica] = []
    for _ in range(count):
        manager = ConnectionManager()
        client = FakeAsyncRedis(server=server, decode_responses=True)
        bus = ws_bus.RedisBus(client, manager)
        await bus.start()
        replicas.append(Replica(manager, bus, client, server))
    return replicas


async def close_replicas(replicas: list[Replica]) -> None:
    for replica in replicas:
        await replica.bus.close()


async def wait_until(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    """Poll until ``predicate`` is true (bus delivery happens on a task)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("wait_until timed out")
        await asyncio.sleep(0.01)


async def with_test_db(
    scenario: Callable[[async_sessionmaker[AsyncSession]], Awaitable[Any]],
) -> Any:
    """Run one async scenario against a clean test DB **in the current loop**.

    Unlike the ``run_db`` fixture (which spins its own loop via ``asyncio.run``),
    this keeps the DB work in the caller's loop so it can share the loop with
    background bus-consumer tasks. Truncates first, disposes after.
    """
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text(_TRUNCATE))
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    try:
        return await scenario(factory)
    finally:
        await engine.dispose()
