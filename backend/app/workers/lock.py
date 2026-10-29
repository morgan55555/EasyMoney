"""Single-flight lock for the hourly reminder scan (AGENTS.md §2.7, multi-replica).

With N backend replicas, the hourly scan must run once per interval, not N
times (each run re-sends every overdue reminder). The lock is a Redis
``SET ... NX EX``:

* key ``easymoney:lock:overdue-reminder-scan``;
* value = this instance's unique id, so release is a **compare-and-delete** —
  a replica never deletes a lock it does not hold;
* TTL ``scheduler_lock_ttl_s`` (3300 s): below the 1 h interval, so a dead
  holder can never block the next run; far above any realistic scan duration.

Release prefers a Lua compare-and-delete; on servers without scripting
(fakeredis without Lua, some managed Redis) it falls back to ``GET`` +
``DELETE`` of a still-matching value — the only race is a TTL expiry in the
microsecond gap, which is harmless here (the scan is far shorter than the
TTL; worst case one duplicate run in that hour).

Fail-open: local mode (no Redis) means "no lock, run in every process" —
exactly the pre-Redis behaviour. A Redis error on acquire also means "run"
(the weekly cap table plus the at-send-time re-verification in §2.7 keep the
damage bounded); an error on release means "let the TTL expire it".
"""

from __future__ import annotations

import logging
import secrets

from app.core.config import get_settings
from app.core.redis_client import get_redis

logger = logging.getLogger("easymoney.scheduler.lock")

#: One shared lock for the reminder scan across all replicas.
KEY = "easymoney:lock:overdue-reminder-scan"

_RELEASE_LUA = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("DEL", KEYS[1])
end
return 0
"""

# Per-process holder id (two calls — acquire, release — in the same scan
# must agree on who held the lock).
_owner = secrets.token_hex(8)
_acquired = False


def reset_lock() -> None:
    """Test seam: forget any in-process hold (does not touch Redis)."""
    global _acquired
    _acquired = False


async def acquire() -> bool:
    """Try to take the lock; ``True`` iff this replica should run the scan."""
    global _acquired
    redis = get_redis()
    if redis is None:
        # Local mode: no lock at all (pre-Redis behaviour; one process).
        _acquired = False
        return True
    try:
        got = await redis.set(KEY, _owner, nx=True, ex=get_settings().scheduler_lock_ttl_s)
    except Exception:
        logger.error("Scheduler lock acquire failed; running scan (fail-open)", exc_info=True)
        _acquired = False
        return True
    _acquired = bool(got)
    return _acquired


async def release() -> None:
    """Release the lock if this replica still holds it (compare-and-delete)."""
    global _acquired
    redis = get_redis()
    if redis is None or not _acquired:
        return
    try:
        try:
            await redis.eval(_RELEASE_LUA, 1, KEY, _owner)
        except Exception:
            # Scripting unavailable: GET + DELETE fallback (see module doc).
            if await redis.get(KEY) == _owner:
                await redis.delete(KEY)
    except Exception:
        logger.warning("Scheduler lock release failed; the TTL will expire it", exc_info=True)
    finally:
        _acquired = False
