"""A thin, fail-open async Redis wrapper. Every call swallows connection/timeout errors and
returns as if the cache were simply empty - Redis is optional infrastructure here (caching
short-lived, pure-computation results), never a hard dependency the API can go down over.
"""
from typing import Optional, Tuple

import redis.asyncio as redis

from app.core.config import CACHE_MAX_TTL_SECONDS, REDIS_URL

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


async def cache_incr_window(key: str, ttl_seconds: int) -> Optional[int]:
    """P0.8 / A6: INCR + EXPIRE on first use - a fixed-window counter shared by every replica. None when Redis is
    unreachable, so the caller can fall back to its in-process count instead of failing open or closed blindly."""
    try:
        client = _get_client()
        count = await client.incr(key)
        if count == 1:
            await client.expire(key, ttl_seconds)
        return int(count)
    except Exception:
        return None


async def cache_set(key: str, value: str, ttl_seconds: int) -> None:
    """H-1: a cache entry always expires, and soon (at most CACHE_MAX_TTL_SECONDS) - Redis evicts only keys with a TTL
    (`volatile-lru`), so a cache key without one could never make room under memory pressure. Out-of-range TTLs are
    clamped, never refused: a cache write must not fail the request that made it."""
    try:
        await _get_client().set(key, value, ex=min(max(1, int(ttl_seconds)), CACHE_MAX_TTL_SECONDS))
    except Exception:
        pass


async def cache_lock_holder(key: str) -> Tuple[Optional[str], bool]:
    """H-1: who holds the lock now -> (holder or None, redis_reachable). Tells a lock that was evicted (None) from one a
    second replica took (another holder) from Redis being down."""
    try:
        return await _get_client().get(key), True
    except Exception:
        return None, False


async def cache_memory_ratio() -> Optional[float]:
    """H-1: used_memory / maxmemory, or None when Redis is unreachable or runs without a cap (maxmemory 0)."""
    try:
        info = await _get_client().info("memory")
        cap = float(info.get("maxmemory") or 0)
        return float(info.get("used_memory") or 0) / cap if cap > 0 else None
    except Exception:
        return None


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
