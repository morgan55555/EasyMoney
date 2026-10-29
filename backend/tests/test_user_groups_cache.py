"""The WS-handshake group-list cache (AGENTS.md §3.2, multi-replica).

Unit-tests ``app.ws.user_groups`` against a fakeredis client: the fetch is
called only on a miss, invalidation drops the entry, corrupt values fall back
to the DB, and local mode (no Redis) always fetches.
"""

from __future__ import annotations

import json

import pytest
from fakeredis import FakeAsyncRedis

from app.core import redis_client
from app.ws import user_groups


@pytest.fixture(autouse=True)
def _clean():
    redis_client.reset_redis()
    yield
    redis_client.reset_redis()


async def test_fetch_only_on_miss() -> None:
    client = FakeAsyncRedis(decode_responses=True)
    redis_client.set_redis(client)
    calls = 0

    async def fetch() -> list[int]:
        nonlocal calls
        calls += 1
        return [3, 1, 2]

    try:
        ids = await user_groups.user_group_ids(42, fetch)
        assert ids == [1, 2, 3]
        assert calls == 1
        # the value is stored (sorted, deduped)
        stored = json.loads(await client.get("easymoney:user_groups:42"))
        assert stored == [1, 2, 3]
        # a second call is served from the cache: no fetch
        ids2 = await user_groups.user_group_ids(42, fetch)
        assert ids2 == [1, 2, 3]
        assert calls == 1
    finally:
        await client.aclose()


async def test_invalidate_drops_the_entry() -> None:
    client = FakeAsyncRedis(decode_responses=True)
    redis_client.set_redis(client)
    calls = 0

    async def fetch() -> list[int]:
        nonlocal calls
        calls += 1
        return [1]

    try:
        await user_groups.user_group_ids(7, fetch)
        assert calls == 1
        await user_groups.invalidate_user_groups(7)
        assert await client.get("easymoney:user_groups:7") is None
        # now a miss again: fetch runs and stores the new set
        async def fetch2() -> list[int]:
            nonlocal calls
            calls += 1
            return [1, 2]

        ids = await user_groups.user_group_ids(7, fetch2)
        assert ids == [1, 2]
        assert calls == 2
    finally:
        await client.aclose()


async def test_corrupt_value_falls_back_to_db() -> None:
    client = FakeAsyncRedis(decode_responses=True)
    redis_client.set_redis(client)
    calls = 0

    async def fetch() -> list[int]:
        nonlocal calls
        calls += 1
        return [5]

    try:
        await client.set("easymoney:user_groups:9", "not-json{{")
        ids = await user_groups.user_group_ids(9, fetch)
        assert ids == [5]
        assert calls == 1
        # the corrupt value was replaced by a valid one
        stored = json.loads(await client.get("easymoney:user_groups:9"))
        assert stored == [5]
    finally:
        await client.aclose()


async def test_local_mode_always_fetches() -> None:
    assert redis_client.get_redis() is None
    calls = 0

    async def fetch() -> list[int]:
        nonlocal calls
        calls += 1
        return [8]

    assert await user_groups.user_group_ids(1, fetch) == [8]
    assert await user_groups.user_group_ids(1, fetch) == [8]
    assert calls == 2
