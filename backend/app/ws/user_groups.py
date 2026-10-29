"""WS-handshake group-list cache (AGENTS.md §3.2, multi-replica).

Every tab connect — and every ~15-minute token-expiry reconnect — re-queries
``memberships`` for the user's group set. With Redis, the set is cached under
``easymoney:user_groups:<user_id>`` (TTL ``user_groups_cache_ttl_s``); the
exact invalidation fires on the same post-commit paths that change membership
(accept invite / leave / remove) via :func:`invalidate_user_groups`.

Fail-open: local mode (no Redis) or any Redis error falls back to the DB
query. A stale set only delays a dynamic re-subscribe for up to the
invalidation window, and the client refetches its state on reconnect, so the
cache can never make a handshake wrong in a way the client can't repair.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Sequence

from app.core.config import get_settings
from app.core.redis_client import get_redis

logger = logging.getLogger("easymoney.ws.user_groups")


def _key(user_id: int) -> str:
    return f"easymoney:user_groups:{user_id}"


async def user_group_ids(user_id: int, fetch: Callable[[], Awaitable[Sequence[int]]]) -> list[int]:
    """The user's group ids, with the handshake cache.

    ``fetch`` is the DB query, run only on a cache miss (its result is then
    stored under the TTL). Local mode / Redis error → ``fetch`` directly.
    """
    redis = get_redis()
    if redis is not None:
        key = _key(user_id)
        try:
            cached = await redis.get(key)
        except Exception:
            logger.warning("User-groups cache read failed; querying the DB", exc_info=True)
        else:
            if cached is not None:
                try:
                    return [int(v) for v in json.loads(cached)]
                except (TypeError, ValueError):
                    logger.warning("User-groups cache value unparseable; querying the DB")
    ids = sorted({int(g) for g in await fetch()})
    await refresh_user_groups(user_id, ids)
    return ids


async def refresh_user_groups(user_id: int, group_ids: Sequence[int]) -> None:
    """Store the fresh group set (called on a cache miss). Fail-open."""
    redis = get_redis()
    if redis is None:
        return
    try:
        await redis.set(
            _key(user_id),
            json.dumps(sorted(set(int(g) for g in group_ids))),
            ex=get_settings().user_groups_cache_ttl_s,
        )
    except Exception:
        logger.warning("User-groups cache refresh failed", exc_info=True)


async def invalidate_user_groups(user_id: int) -> None:
    """Drop the cached group set (called on join/leave/remove). Fail-open."""
    redis = get_redis()
    if redis is None:
        return
    try:
        await redis.delete(_key(user_id))
    except Exception:
        logger.warning("User-groups cache invalidation failed", exc_info=True)
