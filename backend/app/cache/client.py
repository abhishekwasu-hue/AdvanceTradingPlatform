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
