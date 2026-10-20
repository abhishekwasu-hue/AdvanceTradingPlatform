"""CH5-a review (ADR-0004): broker calls the API makes for charts (`/api/market-data/ltp`) draw from ONE budget per
broker credential, shared by every API process, so a page that polls - a multi-chart workspace in an open tab, the
same symbol on many screens - can never spend the broker allowance the trading worker's exits need.

- **Shared, per credential.** The counters live in Redis, keyed by the credential row (`record.id`) - so every API
  worker and replica counts together, and two members who each brought their own broker (BYOK / BT1) never share one -
  exactly where `app.core.rate_limit` keeps its counters (production and staging, or `RATE_LIMIT_BACKEND=redis`). The
  keys carry the credential as a hash tag (`ab:{<id>}:<seconds>`), so the one multi-key script stays on one slot under
  Redis Cluster.
- **Redis missing or down.** Each API process then counts in its own in-process windows, at its part of the share:
  every limit divided by the host's API processes (`API_WORKERS`), a limit below one per process becoming one per
  proportionally longer window. Together the processes stay at the share; what this cannot see is another host's
  replicas (production runs one host).
- **Windows.** A token is granted only when every window has room, checked and counted in one Redis script. The windows
  slide (a sorted set of call times per window, timed by the Redis server's clock): a fixed window would let twice the
  limit through across its boundary. Per second at API_SHARE of the worker's conservative rate, per minute at
  API_SHARE of the worker's per-minute limit, and - for Upstox, whose standard APIs allow 2,000 requests per 30 minutes
  per user - per 30 minutes, at most UPSTOX_API_PER_30MIN (500) for the API. So API plus worker stay inside the
  broker's allowance whatever the number of API workers (ADR-0004: Kite allows 3 quote calls a second; the worker takes
  up to 2, the API at most 1).
- **No queue.** Only the per-second window is waited for (it refills within a second); a full minute or 30-minute window
  refuses at once. `/ltp` waits at most TOKEN_WAIT_SECONDS, then answers 429 (the chart keeps its last price); a
  `/candles` request waits at most CANDLE_WAIT_SECONDS in all, and a symbol without a token says "budget used".
- **One broker call per symbol.** A quote is cached in Redis for QUOTE_CACHE_SECONDS per (credential, exchange, symbol),
  and one request at a time fetches it (a short lock); the others wait for its result. Every user and tab on the same
  credential shares that one call; a BYOK member's own credential has its own entries.
Nothing here places an order.
"""
import asyncio
import json
import os
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, List, Optional, Tuple

from app.brokers.rate_budget import limits_for

API_SHARE = 0.5                   # of the worker's conservative per-broker limits (per second and per minute)
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
    # ADR-0004: the per-second share too - the worker's own budget keeps the whole conservative rate for its exits.
    share = ApiLimits(per_second=max(1, int(base.per_second * API_SHARE)), per_minute=max(1, int(base.per_minute * API_SHARE)))
    if (broker or "").lower() == "upstox":
        return ApiLimits(share.per_second, share.per_minute, UPSTOX_API_PER_30MIN)
    return share


# --- the shared windows --------------------------------------------------------------------------------------------------
# Sliding windows: KEYS[i] is a sorted set of call times (Redis server clock, seconds) for window i; ARGV holds
# (seconds, limit) per window, then a unique member. Prune, check every window (the longest first), then record the call
# in every one - in one script, so two workers never both take the last token, and a refused call costs nothing in any
# window. Returns 1 (granted) or -i (refused by the i-th window, 1-based; the longest full window is reported, so a caller
# never waits on the second when the minute or the 30 minutes are used up).
_TAKE_LUA = (
    "local t = redis.call('time'); local now = tonumber(t[1]) + tonumber(t[2]) / 1000000; "
    "for i = #KEYS, 1, -1 do local w = tonumber(ARGV[2 * i - 1]); "
    "redis.call('zremrangebyscore', KEYS[i], '-inf', now - w); "
    "if redis.call('zcard', KEYS[i]) + 1 > tonumber(ARGV[2 * i]) then return -i end end; "
    "local member = ARGV[2 * #KEYS + 1]; "
    "for i = 1, #KEYS do redis.call('zadd', KEYS[i], now, member); "
    "redis.call('expire', KEYS[i], math.ceil(tonumber(ARGV[2 * i - 1])) + 1) end; return 1"
)
_LOCAL: Dict[str, Deque[float]] = defaultdict(deque)


def _keys(record_id: int, windows: List[Tuple[int, int]]) -> List[str]:
    return [f"ab:{{{int(record_id)}}}:{seconds}" for seconds, _ in windows]


def _api_processes() -> int:
    try:
        return max(1, int(os.environ.get("API_WORKERS", "1") or "1"))
    except ValueError:
        return 1


def _local_windows(windows: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """This process's part of each window when the processes count apart (no Redis): the limit divided by the API
    processes, or - where that is under one call - one call per window as many times longer. N processes together then
    stay at the shared limit."""
    n = _api_processes()
    return [(seconds, limit // n) if limit >= n else (seconds * -(-n // limit), 1) for seconds, limit in windows]


def _local_take(record_id: int, windows: List[Tuple[int, int]]) -> int:
    now = time.monotonic()
    windows = _local_windows(windows)
    buckets = [(_LOCAL[key], seconds, limit) for key, (seconds, limit) in zip(_keys(record_id, windows), windows)]
    for i in range(len(buckets), 0, -1):                       # the longest window first, as in the script
        bucket, seconds, limit = buckets[i - 1]
        while bucket and now - bucket[0] >= seconds:
            bucket.popleft()
        if len(bucket) + 1 > limit:
            return -i
    for bucket, _, _ in buckets:
        bucket.append(now)
    return 1


async def _redis_take(record_id: int, windows: List[Tuple[int, int]]) -> Optional[int]:
    from app.cache.client import _get_client
    args: List[Any] = []
    for seconds, limit in windows:
        args += [seconds, limit]
    args.append(uuid.uuid4().hex)
    try:
        client: Any = _get_client()                       # redis-py types eval as sync-or-async
        return int(await client.eval(_TAKE_LUA, len(windows), *_keys(record_id, windows), *args))
    except Exception:  # noqa: BLE001 - Redis down: this process's part of the share, in-process (_local_windows)
        return None


async def _attempt(record_id: int, broker: str) -> int:
    """1 when a token was taken; -i when the i-th window (1 = per second) has no room."""
    from app.core.rate_limit import redis_backed
    windows = api_limits(broker).windows()
    if redis_backed():
        verdict = await _redis_take(record_id, windows)
        if verdict is not None:
            return verdict
    return _local_take(record_id, windows)


async def try_take(record_id: int, broker: str) -> bool:
    """One token for this credential now, or False."""
    return await _attempt(record_id, broker) == 1


async def take(record_id: int, broker: str, wait_seconds: Optional[float] = None) -> bool:
    """A token within `wait_seconds` (TOKEN_WAIT_SECONDS), or False - the caller answers 429 rather than queue. Only
    the per-second window is waited for; a full minute or 30-minute window refuses at once."""
    deadline = time.monotonic() + (TOKEN_WAIT_SECONDS if wait_seconds is None else wait_seconds)
    while True:
        verdict = await _attempt(record_id, broker)
        if verdict == 1:
            return True
        if verdict != -1 or time.monotonic() + RETRY_EVERY_SECONDS > deadline:
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


async def store_quote(record_id: int, exchange: str, symbol: str, body: Dict[str, Any], ttl: Optional[int] = None) -> None:
    """`ttl` (seconds, default QUOTE_CACHE_SECONDS): the watchlist's batch rows are kept a little longer (UI2-4a2-pre)."""
    from app.core.rate_limit import redis_backed
    key = _quote_key(record_id, exchange, symbol)
    raw = json.dumps(body, default=str)
    seconds = QUOTE_CACHE_SECONDS if ttl is None else ttl
    _LOCAL_QUOTES[key] = (time.monotonic() + seconds, raw)
    if redis_backed():
        from app.cache.client import cache_set
        await cache_set(key, raw, seconds)


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


# --- /candles: the same budget around every broker data call --------------------------------------------------------------
CANDLE_WAIT_SECONDS = 8.0        # in all, per /candles request (the per-second window paces a 50-symbol screen run)
BUDGETED_CALLS = frozenset({"get_historical_data", "get_intraday_candles", "get_quote", "get_quote_for_symbol", "get_ltp",
                            "get_ltp_for_symbol", "get_option_chain"})


class BudgetUsed(Exception):
    """No token for this credential right now: the call was not made."""


class BudgetedAdapter:
    """A broker adapter whose data calls each take one token from the credential's API budget first (`BUDGETED_CALLS`;
    every other attribute passes through - the instrument master, the adapter's name). Calls wait for the per-second
    window only within one time allowance for the whole request (`wait_seconds`); a call that gets no token raises
    BudgetUsed without reaching the broker, and after the first refusal the next ones do not wait (the rest of a screen
    run is marked at once)."""

    def __init__(self, inner: Any, record_id: int, broker: str, wait_seconds: Optional[float] = None) -> None:
        self._inner = inner
        self._record_id = record_id
        self._broker = broker
        self._deadline = time.monotonic() + (CANDLE_WAIT_SECONDS if wait_seconds is None else wait_seconds)
        self.refused = 0
        self.taken = 0

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if name not in BUDGETED_CALLS or not callable(attr):
            return attr

        async def call(*args: Any, **kwargs: Any) -> Any:
            wait = 0.0 if self.refused else max(0.0, self._deadline - time.monotonic())
            if not await take(self._record_id, self._broker, wait):
                self.refused += 1
                raise BudgetUsed("broker rate budget used - try again shortly")
            self.taken += 1
            return await attr(*args, **kwargs)
        return call
