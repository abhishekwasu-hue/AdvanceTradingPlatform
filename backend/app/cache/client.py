"""A thin, fail-open async Redis wrapper. Every call swallows connection/timeout errors and
returns as if the cache were simply empty - Redis is optional infrastructure here (caching
short-lived, pure-computation results), never a hard dependency the API can go down over.
"""
from typing import Optional

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


async def cache_acquire_lock(key: str, holder: str, ttl_seconds: int) -> bool:
    """Best-effort distributed lock (SET NX EX). Returns True when this holder now owns the lock
    - or when Redis is unreachable: the lock exists to stop *two* worker replicas double-trading
    when someone scales the service, and a Redis outage must not stop the *one* replica that is
    running. Deployments with more than one worker replica therefore require Redis."""
    try:
        return bool(await _get_client().set(key, holder, nx=True, ex=ttl_seconds))
    except Exception:
        return True


async def cache_release_lock(key: str, holder: str) -> None:
    """Releases the lock only if this holder still owns it (never someone else's fresh lock)."""
    try:
        client = _get_client()
        if await client.get(key) == holder:
            await client.delete(key)
    except Exception:
        pass
