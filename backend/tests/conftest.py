"""Shared fixtures for the DB-backed tests (AGENTS.md §5).

The suite runs against a dedicated ``easymoney_test`` PostgreSQL database —
the same schema the app migrates, reached from the host at
``localhost:5432`` (compose publishes the port for local development). Each
scenario runs in its own event loop and connection pool and starts from a
truncated schema, so randomized sequences never see each other's rows.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# The boot-time JWT_SECRET guard (app.core.config, A-8) must be satisfied by
# the test environment. A strong secret is valid regardless of DEBUG, so it is
# set before any test module imports ``app.main`` (which instantiates
# Settings). ``setdefault`` lets a real env var win if one is provided.
os.environ.setdefault(
    "JWT_SECRET",
    "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
)

TEST_DATABASE_URL = "postgresql+asyncpg://easymoney:easymoney@localhost:5432/easymoney_test"

# Every table in the schema. One statement truncates them all, cascades the
# FKs and restarts the identity sequences.
_TRUNCATE = (
    "TRUNCATE debt_reminders, activity, attachments, notifications, "
    "expense_participants, settlements, invitations, memberships, expenses, "
    "groups, refresh_tokens, email_tokens, users RESTART IDENTITY CASCADE"
)


@pytest.fixture
def run_db():
    """Run one async scenario against a clean test database and return its
    result.

    The scenario receives a fresh ``async_sessionmaker``; it owns its own
    sessions and commits. The engine is disposed before the result is
    returned, so nothing leaks between scenarios.
    """

    def _run(scenario: Callable[[async_sessionmaker[AsyncSession]], Awaitable[Any]]) -> Any:
        async def _main() -> Any:
            engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
            async with engine.begin() as conn:
                await conn.execute(text(_TRUNCATE))
            # Mirror the production factory (app.db.session): autoflush is off,
            # so services must flush explicitly before querying their own
            # staged rows — the test DB then behaves like the app's.
            factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
            try:
                return await scenario(factory)
            finally:
                await engine.dispose()

        return asyncio.run(_main())

    return _run


@pytest.fixture
async def replicas():
    """Two in-process 'replicas' sharing one ``fakeredis`` ``FakeServer``.

    Each test gets a fresh, isolated server, so Pub/Sub state never leaks
    between tests. Both bus consumer tasks run in the test's own event loop
    (the fixture is async, so it shares it) and are torn down after the test.
    """
    from fakeredis import FakeServer

    from tests import bus_util

    server = FakeServer()
    rs = await bus_util.make_redis_replicas(server)
    try:
        yield rs
    finally:
        await bus_util.close_replicas(rs)
