"""The single-flight reminder-scan lock (AGENTS.md §2.7, multi-replica).

Unit-tests ``app.workers.lock`` against a fakeredis client: acquire/release,
compare-and-delete on release, the fail-open paths (Redis down, local mode)
and the no-scripting GET/DELETE fallback.
"""

from __future__ import annotations

import pytest
from fakeredis import FakeAsyncRedis, FakeServer

from app.core import redis_client
from app.workers import lock as scan_lock


@pytest.fixture(autouse=True)
def _clean():
    scan_lock.reset_lock()
    redis_client.reset_redis()
    yield
    scan_lock.reset_lock()
    redis_client.reset_redis()


async def test_acquire_sets_key_release_clears_it() -> None:
    client = FakeAsyncRedis(decode_responses=True)
    redis_client.set_redis(client)
    try:
        assert await scan_lock.acquire() is True
        assert await client.get(scan_lock.KEY) is not None
        assert await scan_lock.release() is None
        assert await client.get(scan_lock.KEY) is None
    finally:
        await client.aclose()


async def test_acquire_fails_while_held_by_other() -> None:
    client = FakeAsyncRedis(decode_responses=True)
    redis_client.set_redis(client)
    try:
        # another replica already holds the lock
        await client.set(scan_lock.KEY, "other-owner", ex=3300)
        assert await scan_lock.acquire() is False
        # we never took it, so release must not clear the other's lock
        await scan_lock.release()
        assert await client.get(scan_lock.KEY) == "other-owner"
    finally:
        await client.aclose()


async def test_release_is_compare_and_delete() -> None:
    client = FakeAsyncRedis(decode_responses=True)
    redis_client.set_redis(client)
    try:
        assert await scan_lock.acquire() is True
        # a successor takes over (TTL expiry race): our value is gone
        await client.set(scan_lock.KEY, "other-owner", ex=3300)
        await scan_lock.release()
        # the compare-and-delete must leave the successor's lock alone
        assert await client.get(scan_lock.KEY) == "other-owner"
    finally:
        await client.aclose()


async def test_release_fallback_when_no_scripting() -> None:
    client = FakeAsyncRedis(decode_responses=True)
    redis_client.set_redis(client)
    try:
        assert await scan_lock.acquire() is True
        # force the no-scripting path (GET + DELETE)
        original_eval = client.eval

        async def boom(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("scripting unavailable")

        client.eval = boom  # type: ignore[method-assign]
        try:
            await scan_lock.release()
        finally:
            client.eval = original_eval  # type: ignore[method-assign]
        assert await client.get(scan_lock.KEY) is None
    finally:
        await client.aclose()


async def test_acquire_fails_open_when_redis_down() -> None:
    server = FakeServer()
    client = FakeAsyncRedis(server=server, decode_responses=True)
    redis_client.set_redis(client)
    try:
        server.connected = False
        # Redis down: the scan still runs (fail-open)
        assert await scan_lock.acquire() is True
        server.connected = True
        # nothing was actually acquired, so release is a no-op
        await scan_lock.release()
        assert await client.get(scan_lock.KEY) is None
    finally:
        await client.aclose()


async def test_local_mode_has_no_lock() -> None:
    # no redis configured: acquire always succeeds, release is a no-op
    assert redis_client.get_redis() is None
    assert await scan_lock.acquire() is True
    await scan_lock.release()
