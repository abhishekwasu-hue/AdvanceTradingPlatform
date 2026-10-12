"""CH5-a review (ADR-0004): broker calls the API makes for charts (`/api/market-data/ltp`) draw from ONE budget per
broker credential, shared by every API process, so a page that polls - a multi-chart workspace in an open tab, the
same symbol on many screens - can never spend the broker allowance the trading worker's exits need.

- **Shared, per credential.** The counters live in Redis, keyed by the credential row (`record.id`) - so every API
  worker and replica counts together, and two members who each brought their own broker (BYOK / BT1) never share one -
  exactly where `app.core.rate_limit` keeps its counters (production and staging, or `RATE_LIMIT_BACKEND=redis`). With
  Redis not configured (one dev process) or unreachable, an in-process window takes over, as for the rate limiter.
- **Windows.** A token is granted only when every window has room, checked and counted in one Redis script: per second,
  per minute and - for Upstox, whose standard APIs allow 2,000 requests per 30 minutes per user - per 30 minutes, at
  most UPSTOX_API_PER_30MIN (500) for the API. Each window is half or less of the worker's own conservative limits
  (`rate_budget.limits_for`), so API plus worker stay inside the broker's allowance whatever the number of API workers.
- **No queue.** A request that gets no token within TOKEN_WAIT_SECONDS is answered 429 (the chart keeps its last price).
- **One broker call per symbol.** A quote is cached in Redis for QUOTE_CACHE_SECONDS per (credential, exchange, symbol),
  and one request at a time fetches it (a short lock); the others wait for its result. Every user and tab on the same
  credential shares that one call; a BYOK member's own credential has its own entries.
Nothing here places an order.
"""
import asyncio
import json
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, List, Optional, Tuple

from app.brokers.rate_budget import limits_for

API_SHARE = 0.5                   # of the worker's conservative per-broker limits
UPSTOX_API_PER_30MIN = 500        # of Upstox's 2,000 standard requests per 30 minutes per user
TOKEN_WAIT_SECONDS = 2.0
RETRY_EVERY_SECONDS = 0.2
QUOTE_CACHE_SECONDS = 4
FETCH_LOCK_SECONDS = 5


@dataclass(frozen=True)
class ApiLimits:
    per_second: int
    per_minute: int
    per_30min: Optional[int] = None

    def windows(self) -> List[Tuple[int, int]]:
        """(window seconds, limit) for every window that applies."""
        out = [(1, self.per_second), (60, self.per_minute)]
        if self.per_30min is not None:
            out.append((1800, self.per_30min))
        return out


def api_limits(broker: str) -> ApiLimits:
    """The API's share for one credential of this broker."""
    base = limits_for(broker)
    share = ApiLimits(per_second=max(1, int(base.per_second * API_SHARE)), per_minute=max(1, int(base.per_minute * API_SHARE)))
    if (broker or "").lower() == "upstox":
        return ApiLimits(share.per_second, share.per_minute, UPSTOX_API_PER_30MIN)
    return share


# --- the shared windows --------------------------------------------------------------------------------------------------
# Check every window, then count in every window - in one script, so two workers never both take the last token, and a
# refused call costs nothing in any window.
_TAKE_LUA = (
    "for i = 1, #KEYS do local c = tonumber(redis.call('get', KEYS[i]) or '0'); "
    "if c + 1 > tonumber(ARGV[2 * i]) then return 0 end end; "
    "for i = 1, #KEYS do local c = redis.call('incr', KEYS[i]); "
    "if c == 1 then redis.call('expire', KEYS[i], ARGV[2 * i - 1]) end end; return 1"
)
_LOCAL: Dict[str, Deque[float]] = defaultdict(deque)


def _keys(record_id: int, windows: List[Tuple[int, int]]) -> List[str]:
    return [f"ab:{int(record_id)}:{seconds}" for seconds, _ in windows]


def _local_take(record_id: int, windows: List[Tuple[int, int]]) -> bool:
    now = time.monotonic()
    buckets = [(_LOCAL[key], seconds, limit) for key, (seconds, limit) in zip(_keys(record_id, windows), windows)]
    for bucket, seconds, limit in buckets:
        while bucket and now - bucket[0] > seconds:
            bucket.popleft()
        if len(bucket) + 1 > limit:
            return False
    for bucket, _, _ in buckets:
        bucket.append(now)
    return True


async def _redis_take(record_id: int, windows: List[Tuple[int, int]]) -> Optional[bool]:
    from app.cache.client import _get_client
    args: List[Any] = []
    for seconds, limit in windows:
        args += [seconds, limit]
    try:
        client: Any = _get_client()                       # redis-py types eval as sync-or-async
        return bool(int(await client.eval(_TAKE_LUA, len(windows), *_keys(record_id, windows), *args)))
    except Exception:  # noqa: BLE001 - Redis down: the in-process window takes over (never fails open)
        return None


async def try_take(record_id: int, broker: str) -> bool:
    """One token for this credential now, or False."""
    from app.core.rate_limit import redis_backed
    windows = api_limits(broker).windows()
    if redis_backed():
        verdict = await _redis_take(record_id, windows)
        if verdict is not None:
            return verdict
    return _local_take(record_id, windows)


async def take(record_id: int, broker: str, wait_seconds: Optional[float] = None) -> bool:
    """A token within `wait_seconds` (TOKEN_WAIT_SECONDS), or False - the caller answers 429 rather than queue."""
    deadline = time.monotonic() + (TOKEN_WAIT_SECONDS if wait_seconds is None else wait_seconds)
    while True:
        if await try_take(record_id, broker):
            return True
        if time.monotonic() + RETRY_EVERY_SECONDS > deadline:
            return False
        await asyncio.sleep(RETRY_EVERY_SECONDS)


# --- the shared quote ----------------------------------------------------------------------------------------------------
_LOCAL_QUOTES: Dict[str, Tuple[float, str]] = {}
_LOCAL_FETCHING: Dict[str, float] = {}


def _quote_key(record_id: int, exchange: str, symbol: str) -> str:
    return f"ltpq:{int(record_id)}:{exchange.upper()}:{symbol.upper()}"


async def cached_quote(record_id: int, exchange: str, symbol: str) -> Optional[Dict[str, Any]]:
    """The body another request fetched for this credential and symbol in the last QUOTE_CACHE_SECONDS, or None."""
    from app.core.rate_limit import redis_backed
    key = _quote_key(record_id, exchange, symbol)
    raw: Optional[str] = None
    if redis_backed():
        from app.cache.client import cache_get
        raw = await cache_get(key)
    if raw is None:
        hit = _LOCAL_QUOTES.get(key)
        raw = hit[1] if hit is not None and hit[0] > time.monotonic() else None
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


async def store_quote(record_id: int, exchange: str, symbol: str, body: Dict[str, Any]) -> None:
    from app.core.rate_limit import redis_backed
    key = _quote_key(record_id, exchange, symbol)
    raw = json.dumps(body, default=str)
    _LOCAL_QUOTES[key] = (time.monotonic() + QUOTE_CACHE_SECONDS, raw)
    if redis_backed():
        from app.cache.client import cache_set
        await cache_set(key, raw, QUOTE_CACHE_SECONDS)


async def claim_fetch(record_id: int, exchange: str, symbol: str) -> Optional[str]:
    """The right to make THE broker call for this credential and symbol now (a holder id), or None when another
    request is making it. Redis SET NX with a short expiry (a crashed holder never blocks for long); in-process otherwise."""
    from app.core.rate_limit import redis_backed
    key = "lock:" + _quote_key(record_id, exchange, symbol)
    holder = uuid.uuid4().hex
    if redis_backed():
        from app.cache.client import cache_try_lock
        acquired, reachable = await cache_try_lock(key, holder, FETCH_LOCK_SECONDS)
        if reachable:
            return holder if acquired else None
    now = time.monotonic()
    if _LOCAL_FETCHING.get(key, 0.0) > now:
        return None
    _LOCAL_FETCHING[key] = now + FETCH_LOCK_SECONDS
    return holder


async def release_fetch(record_id: int, exchange: str, symbol: str, holder: str) -> None:
    from app.core.rate_limit import redis_backed
    key = "lock:" + _quote_key(record_id, exchange, symbol)
    _LOCAL_FETCHING.pop(key, None)
    if redis_backed():
        from app.cache.client import cache_release_lock
        await cache_release_lock(key, holder)


async def wait_for_quote(record_id: int, exchange: str, symbol: str, wait_seconds: float = TOKEN_WAIT_SECONDS) -> Optional[Dict[str, Any]]:
    """While another request fetches: its result as soon as it is cached, or None after `wait_seconds`."""
    deadline = time.monotonic() + wait_seconds
    while True:
        body = await cached_quote(record_id, exchange, symbol)
        if body is not None or time.monotonic() >= deadline:
            return body
        await asyncio.sleep(0.1)


def reset() -> None:
    """Tests: forget the in-process windows, quotes and fetch locks."""
    _LOCAL.clear()
    _LOCAL_QUOTES.clear()
    _LOCAL_FETCHING.clear()
