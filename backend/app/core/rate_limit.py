"""Request rate limiting (master prompt Section 48; P0.2 / S1).

Two kinds of limiter, both dependency factories:

* `rate_limit(name, limit, window)` - per client IP (the auth endpoints: register, login, refresh, forgot).
* `user_rate_limit(name, limit, window)` - per logged-in user (the CPU-heavy analysis endpoints), so one
  account cannot monopolise the threadpool whatever IPs it comes from.

Counting happens in Redis (fixed window `INCR` + `EXPIRE`, shared by every API replica) in production and
staging or when `RATE_LIMIT_BACKEND=redis`; otherwise in-process (a sliding window per process), which is
exact for the single-replica dev/test setup. With Redis configured but unreachable the limiter falls back to
the in-process window rather than failing open entirely.

The client IP is `request.client.host`: uvicorn rewrites it from `X-Forwarded-For` only for the proxies named in
`FORWARDED_ALLOW_IPS` (`--proxy-headers --forwarded-allow-ips`, Dockerfile), so a client cannot spoof it.
"""
import time
from collections import defaultdict, deque
from typing import Awaitable, Callable, Deque, Dict, Optional, Tuple

from fastapi import Depends, HTTPException, Request, status

from app.core.config import ENVIRONMENT, HARDENED_ENVIRONMENTS, RATE_LIMIT_BACKEND

_WINDOWS: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)


def redis_backed() -> bool:
    return RATE_LIMIT_BACKEND == "redis" or (not RATE_LIMIT_BACKEND and ENVIRONMENT in HARDENED_ENVIRONMENTS)


def _local_hit(name: str, key: str, limit: int, window_seconds: float) -> bool:
    """True when this call is within the limit (and counts it)."""
    bucket = _WINDOWS[(name, key)]
    now = time.monotonic()
    while bucket and now - bucket[0] > window_seconds:
        bucket.popleft()
    if len(bucket) >= limit:
        return False
    bucket.append(now)
    return True


# INCR and EXPIRE in one script: a process dying between the two would leave a counter that never expires
# and a key that refuses its holder forever.
_HIT_LUA = "local c = redis.call('incr', KEYS[1]); if c == 1 then redis.call('expire', KEYS[1], ARGV[1]) end; return c"


async def _redis_hit(name: str, key: str, limit: int, window_seconds: float) -> Optional[bool]:
    """True/False within Redis; None when Redis did not answer (caller falls back to the local window)."""
    from app.cache.client import _get_client
    try:
        count = await _get_client().eval(_HIT_LUA, 1, f"rl:{name}:{key}", max(1, int(window_seconds)))
        return int(count) <= limit
    except Exception:  # noqa: BLE001 - Redis down is handled by the local window
        return None


async def allow(name: str, key: str, limit: int, window_seconds: float) -> bool:
    if redis_backed():
        verdict = await _redis_hit(name, key, limit, window_seconds)
        if verdict is not None:
            return verdict
    return _local_hit(name, key, limit, window_seconds)


def _too_many() -> HTTPException:
    return HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many requests, please try again later.")


def rate_limit(name: str, limit: int, window_seconds: float) -> Callable[[Request], Awaitable[None]]:
    """`Depends(rate_limit("auth_login", limit=10, window_seconds=60))`: 429 once a caller IP has made `limit`
    calls to this named limiter within the trailing `window_seconds`."""

    async def _check(request: Request) -> None:
        ip = request.client.host if request.client else "unknown"
        if not await allow(name, ip, limit, window_seconds):
            raise _too_many()

    return _check


def user_rate_limit(name: str, limit: int, window_seconds: float) -> Callable[..., Awaitable[None]]:
    """Per logged-in user; the dependency resolves the user itself so it composes with any route."""
    from app.auth.dependencies import get_current_user

    async def _check(user=Depends(get_current_user)) -> None:
        if not await allow(name, f"u{user.id}", limit, window_seconds):
            raise _too_many()

    return _check


def reset(name: Optional[str] = None) -> None:
    """Tests: forget the in-process windows (all, or one limiter's)."""
    if name is None:
        _WINDOWS.clear()
        return
    for key in [k for k in _WINDOWS if k[0] == name]:
        del _WINDOWS[key]
