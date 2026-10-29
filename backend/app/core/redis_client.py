"""Shared async Redis client (mirrors ``app.db.session``).

Lazy, process-cached. ``get_redis()`` returns ``None`` when ``REDIS_URL`` is
not set (local mode), so every Redis-backed feature — the cross-instance WS
bus, the balance-snapshot and handshake caches, the scheduler lock — degrades
to its pre-Redis behaviour. The bare-dev boot and the whole test suite run
that way.

Redis holds only derived/ephemeral state (nothing durable): every value is
rebuildable from Postgres or invalidated on the same post-commit path that
already pushes the WS event, and every access is fail-open (a Redis outage
degrades to single-instance behaviour, never a 500).
"""

from __future__ import annotations

import logging

import redis.asyncio as aioredis

from app.core.config import get_settings

logger = logging.getLogger("easymoney.redis")

_redis: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis | None:
    """The shared client, created on first use; ``None`` in local mode."""
    global _redis
    if _redis is None:
        url = get_settings().redis_url
        if not url:
            return None
        # decode_responses: the bus and the caches deal in JSON strings.
        _redis = aioredis.from_url(url, decode_responses=True)
    return _redis


def set_redis(client: aioredis.Redis | None) -> None:
    """Override the shared client (test seam: inject a fakeredis instance)."""
    global _redis
    _redis = client


def reset_redis() -> None:
    """Drop the cached client (test helper); the next ``get_redis`` re-derives
    it from ``REDIS_URL`` (``None`` in local mode)."""
    global _redis
    _redis = None


async def dispose_redis() -> None:
    """Close the client on shutdown (clean pool teardown, like the DB)."""
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
