"""A thin, fail-open async Redis wrapper. Every call swallows connection/timeout errors and
returns as if the cache were simply empty - Redis is optional infrastructure here (caching
short-lived, pure-computation results), never a hard dependency the API can go down over.
"""
from typing import Optional, Tuple

import redis.asyncio as redis

from app.core.config import REDIS_URL

_client: Optional["redis.Redis"] = None


def _get_client() -> "redis.Redis":
    global _client
    if _client is None:
        _client = redis.from_url(
            REDIS_URL, decode_responses=True, socket_connect_timeout=0.5, socket_timeout=0.5,
        )
    return _client


async def cache_get(key: str) -> Optional[str]:
    try:
        return await _get_client().get(key)
    except Exception:
        return None


async def cache_set(key: str, value: str, ttl_seconds: int) -> None:
    try:
        await _get_client().set(key, value, ex=ttl_seconds)
    except Exception:
        pass


# P0.1 / S2: compare-and-act in one Redis round trip, so a lock that expired and was taken by another
# holder between our GET and DEL (or EXPIRE) is never released or renewed by us.
_RELEASE_LUA = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"
_RENEW_LUA = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('expire', KEYS[1], ARGV[2]) else return 0 end"


async def cache_try_lock(key: str, holder: str, ttl_seconds: int) -> Tuple[bool, bool]:
    """Distributed lock (SET NX EX) -> (acquired, redis_reachable). The lock exists to stop *two* worker
    replicas double-trading when someone scales the service. With Redis unreachable it reports
    (True, False): the one running replica keeps evaluating PAPER, and the worker decides what LIVE may
    do without the lock (P0.1 / S2: fail-closed for LIVE entries where a second replica is possible)."""
    try:
        return bool(await _get_client().set(key, holder, nx=True, ex=ttl_seconds)), True
    except Exception:
        return True, False


async def cache_acquire_lock(key: str, holder: str, ttl_seconds: int) -> bool:
    """`cache_try_lock` without the reachability flag (fail-open, as before)."""
    acquired, _ = await cache_try_lock(key, holder, ttl_seconds)
    return acquired


async def cache_renew_lock(key: str, holder: str, ttl_seconds: int) -> bool:
    """Extends the TTL only while this holder still owns the lock (a cycle that runs long must not let a
    second replica in half-way). False when the lock is gone, owned by someone else, or Redis is down."""
    try:
        return bool(await _get_client().eval(_RENEW_LUA, 1, key, holder, int(ttl_seconds)))
    except Exception:
        return False


async def cache_release_lock(key: str, holder: str) -> None:
    """Releases the lock only if this holder still owns it (never someone else's fresh lock) - atomically."""
    try:
        await _get_client().eval(_RELEASE_LUA, 1, key, holder)
    except Exception:
        pass
