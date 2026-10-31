"""M71 PL-2: every broker's PUBLISHED API limits, held per broker credential in Redis for every process.

The table of figures, their sources and their status is docs/design/M71_RATE_LIMITS.md; the figures themselves are
`PUBLISHED` below (as the broker states them; ATP's safety margin is applied when a call is counted, never written into
the table).

- **Windows.** Every window a broker publishes is enforced: per second, per minute, per 30 minutes, per hour and per
  day. Each is a sliding window (a Redis sorted set of call times), the day one too: a rolling 24 hours is never more
  than a calendar day allows.
- **Pools.** A broker's limits are grouped in buckets (Kite: quotes, history, other reads, orders); every bucket belongs
  to the DATA pool or the ORDER pool. Orders, exits, stops, changes and cancels draw from the order pool only, so no
  data call (a chart, a screen, a backfill) can use what an order needs. A broker that publishes one allowance for
  everything (Fyers) has it split into a data part and an order part that together stay inside it.
- **Exit reserve (ADR-0004).** Of every order-pool window, BROKER_ORDER_EXIT_RESERVE_SHARE is kept for exits, stops,
  changes and cancels: a new entry stops before it. Of every data-pool window, BROKER_DATA_PRIORITY_RESERVE_SHARE is kept
  for an exit's own reads (its price read, the position and order-book checks). A priority call waits at most
  BROKER_EXIT_MAX_WAIT_SECONDS for the next slot and is then sent anyway (counted): an exit is never refused here.
- **Safety margin.** BROKER_BUDGET_SAFETY_MARGIN (default 10 %): ATP counts each window to `floor(limit x (1 - margin))`
  (at least 1). An operational setting, not a trading value.
- **Per credential, shared, atomic.** Keys `bl:{<credential id>}:<bucket>:<seconds>` (the credential as a hash tag: one
  slot under Redis Cluster). One Lua script checks every window and records the call in all of them, so two processes
  never both take the last slot and a refused call costs nothing. The API's chart and quote budget
  (app/market_data/api_budget.py) records its calls in the same keys inside its own script.
- **Redis down.** The trading worker counts in-process (the same windows, the full budget: it is the one process that
  sends orders) - orders and exits always flow. The API processes skip the shared windows and stay at api_budget's own
  per-process share (unchanged). Fail-open for orders and exits, paced for data.
- **HTTP 429 / "too many requests".** `backoff_delay`: exponential (BROKER_429_BASE_SECONDS x 2^attempt, capped at
  BROKER_429_CAP_SECONDS) with equal jitter (between half and all of it); RateLimitedBroker retries within
  BROKER_429_DATA_MAX_WAIT_SECONDS (data) or BROKER_429_ORDER_MAX_WAIT_SECONDS (orders) in all, then answers one plain
  "busy" error.
- **Calls per day.** `HTTP_COUNTER` counts every HTTP request an adapter's client sends (app/brokers/shared_http.py,
  hooked in by token_lifecycle.build_adapter) and `COUNTER` every call that took a budget slot, per IST day by broker,
  pool and endpoint kind (and the 429s), in Redis (`bhttp:` / `bcalls:<YYYYMMDD>`, kept 8 days); `calls_today()` reads
  both for the admin (read-only).
- **Eviction.** The window keys carry a TTL; under `volatile-*` / `allkeys-*` eviction Redis could drop them and ATP
  would under-count. The first use logs a warning unless maxmemory-policy is `noeviction`; `calls_today()` reports it.
Nothing here places, blocks or changes an order's content.
"""
import asyncio
import logging
import math
import random
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core import config

logger = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))
SECOND, MINUTE, HALF_HOUR, HOUR, DAY = 1, 60, 1800, 3600, 86400
DATA_POOL, ORDER_POOL = "data", "orders"
KINDS = ("history", "quote", "account", "orders")


@dataclass(frozen=True)
class Bucket:
    """One published allowance: the windows (seconds, limit) a group of endpoints shares."""

    name: str
    pool: str
    windows: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class BrokerSpec:
    broker: str
    buckets: tuple[Bucket, ...]
    kinds: tuple[tuple[str, tuple[str, ...]], ...]   # endpoint kind -> the buckets it draws from
    status: str                                      # "published" | "to confirm" | "conservative (none published)"
    source: str

    def buckets_for(self, kind: str) -> list[Bucket]:
        names = dict(self.kinds).get(kind) or dict(self.kinds).get("account") or ()
        return [b for b in self.buckets if b.name in names]


def _spec(broker: str, status: str, source: str, buckets: dict[str, tuple[str, tuple[tuple[int, int], ...]]],
          kinds: dict[str, tuple[str, ...]]) -> BrokerSpec:
    return BrokerSpec(broker, tuple(Bucket(n, p, w) for n, (p, w) in buckets.items()), tuple(kinds.items()), status, source)


_ALL_DATA: dict[str, tuple[str, ...]] = {"history": ("data",), "quote": ("data",), "account": ("data",), "orders": ("orders",)}

# Upstox: the owner's research R33 (M71) and older replies on Upstox's own community disagree; exceeding may suspend
# access, so the lower set is the default until the figures are confirmed (BROKER_UPSTOX_LIMIT_SET = "r33" raises them).
UPSTOX_CONSERVATIVE = _spec(
    "upstox", "to confirm (lower set, default)",
    "upstox.com/developer/api-documentation/rate-limiting (not reachable from the build session); community.upstox.com "
    "t/rate-limits-on-api-usage/3384 and t/how-to-rate-limit/4726: 25/s, 250/min, 1,000/30 min",
    {"standard": (DATA_POOL, ((SECOND, 25), (MINUTE, 250), (HALF_HOUR, 1000))),
     "orders": (ORDER_POOL, ((SECOND, 10), (MINUTE, 250), (HALF_HOUR, 1000)))},
    {"history": ("standard",), "quote": ("standard",), "account": ("standard",), "orders": ("orders",)})
UPSTOX_R33 = _spec(
    "upstox", "to confirm (owner research R33)",
    "owner order M71 / research R33, citing upstox.com/developer/api-documentation/rate-limiting",
    {"standard": (DATA_POOL, ((SECOND, 50), (MINUTE, 500), (HALF_HOUR, 2000))),
     "orders": (ORDER_POOL, ((SECOND, 10), (MINUTE, 500), (HALF_HOUR, 2000)))},
    {"history": ("standard",), "quote": ("standard",), "account": ("standard",), "orders": ("orders",)})

PUBLISHED: dict[str, BrokerSpec] = {
    "zerodha": _spec(
        "zerodha", "published (orders: lower of two figures)",
        "kite.trade/docs/connect/v3/exceptions/ (API rate limit); owner research R33: quote 1/s, historical 3/s, other "
        "10/s, orders 10/s + 200/min + 3,000/day (a newer forum reply says 400/min and 5,000/day: the lower is used)",
        {"quote": (DATA_POOL, ((SECOND, 1),)), "history": (DATA_POOL, ((SECOND, 3),)),
         "other": (DATA_POOL, ((SECOND, 10),)),
         "orders": (ORDER_POOL, ((SECOND, 10), (MINUTE, 200), (DAY, 3000)))},
        {"history": ("history",), "quote": ("quote",), "account": ("other",), "orders": ("orders",)}),
    "dhan": _spec(
        "dhan", "published", "dhanhq.co/docs/v2/ (Rate limit) and dhan.co/support/platforms/dhanhq-api/"
        "what-are-the-api-rate-limits-for-dhan/: orders 10/s, 250/min, 1,000/h, 7,000/day; data 5/s, 100,000/day; "
        "quote 1/s; non-trading 20/s",
        {"data": (DATA_POOL, ((SECOND, 5), (DAY, 100000))), "quote": (DATA_POOL, ((SECOND, 1),)),
         "non_trading": (DATA_POOL, ((SECOND, 20),)),
         "orders": (ORDER_POOL, ((SECOND, 10), (MINUTE, 250), (HOUR, 1000), (DAY, 7000)))},
        {"history": ("data",), "quote": ("quote",), "account": ("non_trading",), "orders": ("orders",)}),
    "angel_one": _spec(
        "angel_one", "published (forum table); account reads conservative",
        "smartapi.angelone.in forum topic 4387 'Changes in API rate limit': placeOrder / modify / cancel 20/s, 500/min, "
        "1,000/h; getCandleData 3/s, 180/min, 5,000/h; getLtpData 10/s, 500/min, 5,000/h; account reads not found: 1/s",
        {"history": (DATA_POOL, ((SECOND, 3), (MINUTE, 180), (HOUR, 5000))),
         "quote": (DATA_POOL, ((SECOND, 10), (MINUTE, 500), (HOUR, 5000))),
         "account": (DATA_POOL, ((SECOND, 1),)),
         "orders": (ORDER_POOL, ((SECOND, 20), (MINUTE, 500), (HOUR, 1000)))},
        {"history": ("history",), "quote": ("quote",), "account": ("account",), "orders": ("orders",)}),
    "fyers": _spec(
        "fyers", "published (one allowance, split 70 / 30)",
        "fyers.in/api-docs (Plans & Rate Limits, quoted on fyers.in/community): 10/s, 200/min, 100,000/day per client "
        "for every REST call; ATP splits it: data 7/s, 140/min, 70,000/day; orders 3/s, 60/min, 30,000/day",
        {"data": (DATA_POOL, ((SECOND, 7), (MINUTE, 140), (DAY, 70000))),
         "orders": (ORDER_POOL, ((SECOND, 3), (MINUTE, 60), (DAY, 30000)))}, _ALL_DATA),
    "groww": _spec(
        "groww", "to confirm (search extracts)", "Groww trade API docs as quoted in docs/design/BROKER_GROWW.md: orders "
        "10/s, 250/min, 3,000/day; live data 10/s, 300/min, 5,000/day; non-trading 20/s, 500/min, 3,000/day",
        {"live_data": (DATA_POOL, ((SECOND, 10), (MINUTE, 300), (DAY, 5000))),
         "non_trading": (DATA_POOL, ((SECOND, 20), (MINUTE, 500), (DAY, 3000))),
         "orders": (ORDER_POOL, ((SECOND, 10), (MINUTE, 250), (DAY, 3000)))},
        {"history": ("live_data",), "quote": ("live_data",), "account": ("non_trading",), "orders": ("orders",)}),
    "kotak_neo": _spec(
        "kotak_neo", "quotes published; orders to confirm; account reads conservative",
        "Kotak Neo SDK docs quotes.md: 25 requests/s; Kotak FAQ (search extract): up to 10 orders/s; nothing else "
        "published (docs/design/BROKER_KOTAK_NEO.md): account reads 3/s, 120/min",
        {"quote": (DATA_POOL, ((SECOND, 25),)), "account": (DATA_POOL, ((SECOND, 3), (MINUTE, 120))),
         "orders": (ORDER_POOL, ((SECOND, 10),))},
        {"history": ("quote",), "quote": ("quote",), "account": ("account",), "orders": ("orders",)}),
    "shoonya": _spec(
        "shoonya", "conservative (none published)", "no figure found in Finvasia's API docs or forums: ATP's earlier "
        "conservative budget, 3/s and 120/min, for each pool", {"data": (DATA_POOL, ((SECOND, 3), (MINUTE, 120))),
                                                              "orders": (ORDER_POOL, ((SECOND, 3), (MINUTE, 120)))},
        _ALL_DATA),
    "coindcx": _spec(
        "coindcx", "conservative (none published)", "no official figure; the coindcx Ruby client README says about 4 "
        "orders/s and 10 calls/s in all: data 6/s, orders 4/s",
        {"data": (DATA_POOL, ((SECOND, 6),)), "orders": (ORDER_POOL, ((SECOND, 4),))}, _ALL_DATA),
}
PUBLISHED["upstox"] = UPSTOX_CONSERVATIVE
DEFAULT_SPEC = _spec("default", "conservative (none recorded)", "any broker without a recorded figure: 2/s and 100/min "
                     "for each pool", {"data": (DATA_POOL, ((SECOND, 2), (MINUTE, 100))),
                                       "orders": (ORDER_POOL, ((SECOND, 2), (MINUTE, 100)))}, _ALL_DATA)


def spec_for(broker: str | None) -> BrokerSpec:
    name = (broker or "").lower()
    if name == "upstox" and config.BROKER_UPSTOX_LIMIT_SET == "r33":
        return UPSTOX_R33
    return PUBLISHED.get(name, DEFAULT_SPEC)


# --- endpoint kinds -------------------------------------------------------------------------------------------------------
ORDER_METHODS = frozenset({"place_order", "modify_order", "cancel_order", "place_stop_loss_order", "exit_position"})
HISTORY_METHODS = frozenset({"get_historical_data", "get_intraday_candles"})
QUOTE_METHODS = frozenset({"get_ltp", "get_quote", "get_quote_for_symbol", "get_ltp_for_symbol", "get_option_chain",
                           "get_quotes_batch"})


def endpoint_kind(method: str) -> str:
    if method in ORDER_METHODS:
        return "orders"
    if method in HISTORY_METHODS:
        return "history"
    if method in QUOTE_METHODS:
        return "quote"
    return "account"


def pool_of(kind: str) -> str:
    return ORDER_POOL if kind == "orders" else DATA_POOL


def effective(limit: int, margin: float | None = None) -> int:
    """The count ATP stops at for a published limit: the safety margin below it, at least one call."""
    share = config.BROKER_BUDGET_SAFETY_MARGIN if margin is None else margin
    return max(1, math.floor(limit * (1.0 - share) + 1e-9))


def allowed(limit: int, pool: str, priority: bool, margin: float | None = None) -> int:
    """Calls a window lets this call join: the effective limit for a priority call (an exit and its reads); less the
    reserve for anything else."""
    eff = effective(limit, margin)
    if priority:
        return eff
    share = config.BROKER_ORDER_EXIT_RESERVE_SHARE if pool == ORDER_POOL else config.BROKER_DATA_PRIORITY_RESERVE_SHARE
    return max(0, eff - math.floor(eff * share + 1e-9))


def window_key(credential_id: int, bucket: str, seconds: int) -> str:
    return f"bl:{{{int(credential_id)}}}:{bucket}:{int(seconds)}"


def shared_windows(credential_id: int, broker: str, kind: str, *, priority: bool = False,
                   margin: float | None = None) -> list[tuple[str, int, int]]:
    """(key, seconds, allowed) for every published window a call of this kind joins."""
    out: list[tuple[str, int, int]] = []
    for bucket in spec_for(broker).buckets_for(kind):
        for seconds, limit in bucket.windows:
            out.append((window_key(credential_id, bucket.name, seconds), seconds,
                        allowed(limit, bucket.pool, priority, margin)))
    return out


# --- the shared limiter ---------------------------------------------------------------------------------------------------
# KEYS: one sorted set per window. ARGV: now ("" = the Redis server's clock), a unique member, force ("1": record without
# checking - an exit past its wait), then (seconds, allowed) per window. Returns 0 (recorded in every window) or the
# milliseconds until the fullest window has room (nothing recorded anywhere).
_LIMIT_LUA = (
    "local now = tonumber(ARGV[1]); if now == nil then local t = redis.call('time'); "
    "now = tonumber(t[1]) + tonumber(t[2]) / 1000000 end; "
    "local member = ARGV[2]; local wait = 0; "
    "if ARGV[3] ~= '1' then for i = 1, #KEYS do local seconds = tonumber(ARGV[2 + 2 * i]); "
    "local allowed = tonumber(ARGV[3 + 2 * i]); "
    "redis.call('zremrangebyscore', KEYS[i], '-inf', now - seconds); "
    "local c = redis.call('zcard', KEYS[i]); "
    "if c >= allowed then local w = seconds; "
    "if allowed > 0 then local e = redis.call('zrange', KEYS[i], c - allowed, c - allowed, 'WITHSCORES'); "
    "w = tonumber(e[2]) + seconds - now end; "
    "if w < 0.001 then w = 0.001 end; if w > wait then wait = w end end end end; "
    "if wait > 0 then return math.ceil(wait * 1000) end; "
    "for i = 1, #KEYS do redis.call('zadd', KEYS[i], now, member); "
    "redis.call('expire', KEYS[i], math.ceil(tonumber(ARGV[2 + 2 * i])) + 1) end; return 0"
)


def _default_client() -> Any:
    from app.core.rate_limit import redis_backed
    if not redis_backed():
        return None
    from app.cache.client import _get_client
    return _get_client()


class BrokerLimiter:
    """Takes a slot in every published window of a broker credential before a call. One per process (`LIMITER`); the
    state lives in Redis. `clock` (seconds) and `sleep` are injectable for tests; with the default clock the Redis
    server's own clock times the windows."""

    def __init__(self, *, client_factory: Callable[[], Any] | None = None, clock: Callable[[], float] | None = None,
                 sleep: Callable[[float], Any] = asyncio.sleep, margin: float | None = None) -> None:
        self._client_factory = client_factory or _default_client
        self._clock = clock
        self._sleep = sleep
        self.margin = margin
        self._local: dict[str, deque[float]] = defaultdict(deque)
        self.forced = 0              # priority calls sent after their wait without a free slot (never refused)
        self.refused = 0             # non-priority calls given no slot within their wait
        self._eviction_checked = False

    def now(self) -> float:
        return self._clock() if self._clock is not None else time.time()

    async def _try(self, credential_id: int, broker: str, kind: str, *, priority: bool, force: bool,
                   local_fallback: bool) -> float | None:
        """0.0 = recorded; > 0 = seconds until a slot; None = Redis unreachable and no local fallback (not counted)."""
        windows = shared_windows(credential_id, broker, kind, priority=priority, margin=self.margin)
        if not windows:
            return 0.0
        member = uuid.uuid4().hex
        try:
            client = self._client_factory()
        except Exception:  # noqa: BLE001 - Redis unavailable: counted in-process below
            client = None
        if client is not None:
            if not self._eviction_checked:
                self._eviction_checked = True
                await eviction_policy(lambda: client)
            args: list[Any] = ["" if self._clock is None else repr(self.now()), member, "1" if force else "0"]
            for _, seconds, limit in windows:
                args += [seconds, limit]
            try:
                verdict = await client.eval(_LIMIT_LUA, len(windows), *[k for k, _, _ in windows], *args)
                if isinstance(verdict, int) and not isinstance(verdict, bool) and verdict >= 0:
                    return verdict / 1000.0
            except Exception:  # noqa: BLE001 - Redis down: the in-process windows (worker) or nothing (API)
                pass
        if not local_fallback:
            return None
        return self._local_try(windows, force)

    def _local_try(self, windows: list[tuple[str, int, int]], force: bool) -> float:
        now = self.now()
        wait = 0.0
        if not force:
            for key, seconds, limit in windows:
                calls = self._local[key]
                while calls and calls[0] <= now - seconds:
                    calls.popleft()
                if len(calls) >= limit:
                    w = float(seconds) if limit <= 0 else calls[len(calls) - limit] + seconds - now
                    wait = max(wait, w, 0.001)
            if wait > 0:
                return math.ceil(wait * 1000) / 1000.0
        for key, _, _ in windows:
            self._local[key].append(now)
        return 0.0

    async def try_acquire(self, credential_id: int, broker: str, kind: str, *, priority: bool = False,
                          local_fallback: bool = True) -> float | None:
        """One attempt, no waiting: 0.0 when a slot was taken, else the seconds until one (None: not counted)."""
        return await self._try(credential_id, broker, kind, priority=priority, force=False, local_fallback=local_fallback)

    async def acquire(self, credential_id: int, broker: str, kind: str, *, priority: bool = False,
                      max_wait: float | None = None) -> bool:
        """Wait for a slot in every window. A priority call (an exit, its stop, change or cancel, an exit's reads) waits at
        most BROKER_EXIT_MAX_WAIT_SECONDS and is then sent and counted: True always. Anything else waits at most
        `max_wait` (BROKER_DATA_MAX_WAIT_SECONDS) in all and is refused (False, nothing counted) when the slot is further
        away - a full 30-minute or day window answers at once."""
        bound = config.BROKER_EXIT_MAX_WAIT_SECONDS if priority else (
            config.BROKER_DATA_MAX_WAIT_SECONDS if max_wait is None else max_wait)
        waited = 0.0
        while True:
            wait = await self._try(credential_id, broker, kind, priority=priority, force=False, local_fallback=True)
            if wait is None or wait <= 0:
                return True
            if waited + wait > bound:
                if priority:
                    await self._try(credential_id, broker, kind, priority=True, force=True, local_fallback=True)
                    self.forced += 1
                    return True
                self.refused += 1
                return False
            await self._sleep(wait)
            waited += wait

    def reset(self) -> None:
        """Tests: forget the in-process windows."""
        self._local.clear()
        self.forced = self.refused = 0


LIMITER = BrokerLimiter()


# --- 429 back-off ---------------------------------------------------------------------------------------------------------
_RNG = random.Random()  # nosec B311


def backoff_delay(attempt: int, *, rng: random.Random | None = None, base: float | None = None,
                  cap: float | None = None) -> float:
    """Seconds before retry number `attempt` (0-based): base x 2^attempt capped at `cap`, with equal jitter - a
    uniform pick between half of it and all of it, so retries from many processes spread out but never come early."""
    b = config.BROKER_429_BASE_SECONDS if base is None else base
    c = config.BROKER_429_CAP_SECONDS if cap is None else cap
    step = min(c, b * (2 ** max(0, min(int(attempt), 30))))
    return step / 2 + (rng or _RNG).uniform(0, step / 2)


def is_rate_limited(exc: BaseException) -> bool:
    """A broker's own "too many requests" answer (HTTP 429 or its wording) - not ATP's own budget refusals, which sent
    nothing (DailyCapReached, BrokerBudgetUsed)."""
    from app.brokers.exceptions import BrokerAPIError, BrokerBudgetUsed
    if not isinstance(exc, BrokerAPIError) or isinstance(exc, BrokerBudgetUsed):
        return False
    if type(exc).__name__ == "DailyCapReached":
        return False
    if exc.status_code == 429:
        return True
    text = f"{exc} {exc.raw or ''}".lower()
    return "too many requests" in text or "rate limit exceeded" in text


BUSY_MESSAGE = "The broker is busy right now; please try again in a minute."


# --- calls per day ----------------------------------------------------------------------------------------------------------
def ist_day(at: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if at is None else at, IST).strftime("%Y%m%d")


class CallCounter:
    """Broker REST calls per IST day by broker, pool and endpoint kind (and the 429s among them), shared in Redis; an
    in-process count while Redis is unreachable (added to what Redis holds when read)."""

    KEEP_SECONDS = 8 * DAY

    def __init__(self, prefix: str = "bcalls", *, client_factory: Callable[[], Any] | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.prefix = prefix
        self._client_factory = client_factory or _default_client
        self._clock = clock
        self._local: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    async def add(self, broker: str, pool: str, kind: str, n: int = 1, *, rate_limited: bool = False,
                  purpose: str | None = None) -> None:
        """Count `n` calls (or, `rate_limited`, `n` broker "too many requests" answers). `purpose` (a caller's label:
        "screener", "chart", ...) is counted apart as well, outside the totals."""
        if n <= 0:
            return
        name = (broker or "unknown").lower()
        fields = [f"{name}|{pool}|{kind}" + ("|429" if rate_limited else "")]
        if purpose and not rate_limited:
            fields.append(f"{name}|purpose|{purpose}")
        day = ist_day(self._clock())
        try:
            client = self._client_factory()
            if client is not None:
                key = f"{self.prefix}:{day}"
                for field in fields:
                    await client.hincrby(key, field, int(n))
                await client.expire(key, self.KEEP_SECONDS)
                return
        except Exception:  # noqa: BLE001 - a count never fails a call
            pass
        for field in fields:
            self._local[day][field] += int(n)

    async def today(self, day: str | None = None) -> dict[str, Any]:
        """{"day", "total", "rate_limited", "brokers": {broker: {pool: {kind: calls}}}, "rate_limited_by_broker"}."""
        day = day or ist_day(self._clock())
        raw: dict[str, int] = {}
        try:
            client = self._client_factory()
            if client is not None:
                raw = {str(k): int(v) for k, v in (await client.hgetall(f"{self.prefix}:{day}") or {}).items()}
        except Exception:  # noqa: BLE001 - the local counts only
            raw = {}
        for field, n in self._local.get(day, {}).items():
            raw[field] = raw.get(field, 0) + n
        brokers: dict[str, dict[str, dict[str, int]]] = {}
        purposes: dict[str, dict[str, int]] = {}
        limited: dict[str, int] = {}
        total = 0
        for field, n in sorted(raw.items()):
            parts = field.split("|")
            if len(parts) == 4:
                limited[parts[0]] = limited.get(parts[0], 0) + n
                continue
            if len(parts) != 3:
                continue
            broker, pool, kind = parts
            if pool == "purpose":
                purposes.setdefault(broker, {})[kind] = n
                continue
            brokers.setdefault(broker, {}).setdefault(pool, {})[kind] = n
            total += n
        return {"day": f"{day[:4]}-{day[4:6]}-{day[6:]}", "total": total, "rate_limited": sum(limited.values()),
                "brokers": brokers, "rate_limited_by_broker": limited, "purposes": purposes}

    def reset(self) -> None:
        self._local.clear()


COUNTER = CallCounter("bcalls")          # calls that took a budget slot (RateLimitedBroker, api_budget)
HTTP_COUNTER = CallCounter("bhttp")      # every HTTP request an adapter's client sent (shared_http.count_requests)


async def calls_today() -> dict[str, Any]:
    """Read-only, for the admin: today's broker REST calls - every HTTP request sent (`http`) and those that took a
    budget slot (`budgeted`); the gap is what still bypasses a budget. The "calls per day before / after" proof."""
    http, budgeted = await HTTP_COUNTER.today(), await COUNTER.today()
    return {"day": http["day"], "http": http, "budgeted": budgeted, "redis_eviction_policy": await eviction_policy()}


# --- Redis eviction (the windows must not be evicted) ---------------------------------------------------------------------
SAFE_EVICTION = frozenset({"noeviction"})
_EVICTION_CHECKED: dict[str, Any] = {}


async def eviction_policy(client_factory: Callable[[], Any] | None = None) -> str | None:
    """Redis's maxmemory-policy (None when Redis is not used or does not say). Any policy but "noeviction" may drop a
    window key under memory pressure, so ATP would under-count; a warning is logged once per process."""
    try:
        client = (client_factory or _default_client)()
        if client is None:
            return None
        reply = await client.config_get("maxmemory-policy")
        policy = str((reply or {}).get("maxmemory-policy") or "") or None
    except Exception:  # noqa: BLE001 - managed Redis may refuse CONFIG; nothing to report
        return None
    if policy and policy not in SAFE_EVICTION and not _EVICTION_CHECKED.get(policy):
        _EVICTION_CHECKED[policy] = True
        logger.warning("Redis maxmemory-policy is %s: broker limit windows may be evicted under memory pressure and "
                       "under-count; set it to noeviction (M71 PL-2)", policy)
    return policy


def limits_table() -> list[dict[str, Any]]:
    """Every broker's published windows and what ATP counts to (margin applied), for the admin and the docs."""
    rows = []
    for name in sorted({*PUBLISHED, "default"}):
        spec = spec_for(name) if name != "default" else DEFAULT_SPEC
        for bucket in spec.buckets:
            rows.append({"broker": name, "bucket": bucket.name, "pool": bucket.pool, "status": spec.status,
                         "windows": [{"seconds": s, "published": n, "counted_to": effective(n)} for s, n in bucket.windows]})
    return rows
