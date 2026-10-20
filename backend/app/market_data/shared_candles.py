"""LD-10: broker candles shared by every user who asks for the same bars, with one broker fetch at a time.

The owner's decision (load test, 20 Oct): the API's broker budget stays at 1 call a second per credential
(`api_budget`), and a chart spends fewer calls instead. Candles are public market data: the bars a broker returns for
(exchange, symbol, interval, window) are the same whoever asks, so this module keeps them for a short time and every
user, tenant and bring-your-own-broker session reads the same copy.

- **Key.** `md:shared:<broker>:<exchange>:<symbol>:<interval>:<part>:<anchor>` - the broker (data identical to what the
  user's own broker would return: never another broker's bars), the base interval fetched (`1min` or `day`; every chart
  timeframe is resampled from it), the part (`hist` = completed days before today, `today` = today's bars so far) and
  the window anchor (`window_anchor`). Nothing per account (positions, orders, balances, tokens) is ever stored here.
- **Lifetime.** Completed history: HISTORY_TTL_SECONDS (its bars never change; the anchor moves with the window).
  Today's bars: at most TODAY_TTL_SECONDS and never past the close of the bar that was still forming when they were
  fetched (`forming_capped_ttl`), so a copy never serves a cut-off bar as a closed one.
- **One fetch.** In one process, concurrent requests for a key share one task; across API processes a short Redis lock
  (SET NX) lets one process fetch while the others poll for its result. A waiter that sees the fetch fail (the filler's
  budget or token) or take longer than FILL_WAIT_SECONDS fetches itself.
- **Whose budget.** The fetch runs through the requesting user's own adapter: their credential and their API budget
  (`api_budget.BudgetedAdapter`), exactly as before. No platform credential exists or is added; a waiter spends nothing.
- **Storage.** Redis when the app keeps shared state there (`rate_limit.redis_backed()`: production and staging), plus
  a small in-process copy (LOCAL_MAX_ENTRIES) in front of it. Redis is optional: without it the process shares in
  itself only. Empty answers are never stored.
Nothing here places an order.
"""
import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

from app.core.models import OHLCVBar
from app.market_data.calendar import IST

logger = logging.getLogger(__name__)

HISTORY_TTL_SECONDS = 300            # completed days: final bars; short so a broker's late correction shows within minutes
HISTORY_MINUTE_TTL_SECONDS = 90      # a minute-anchored window is read only during its own minute
TODAY_TTL_SECONDS = 60               # today's bars, then capped at the forming bar's close (service.forming_capped_ttl)
FILL_LOCK_SECONDS = 10               # a crashed filler never blocks the key for longer
FILL_WAIT_SECONDS = 2.5              # a waiter on another process's fill fetches itself after this (LD-10 review)
JOIN_WAIT_SECONDS = 2.5              # the same for a request joined to another request's fill in this process
POLL_SECONDS = 0.05
LOCAL_MAX_ENTRIES = 64
MAX_JOINS = 3                        # times a request joins another's fetch that then failed, before fetching itself
# LD-10 review: the trading and research workers set this False at start (`never_wait_on_other_processes`): they read a
# copy when there is one and otherwise fetch with their own budget - no lock, no wait on an API process's fill.
WAIT_ON_OTHER_PROCESSES = True

Fetch = Callable[[], Awaitable[List[OHLCVBar]]]
TtlFor = Callable[[List[OHLCVBar]], int]

_clock = time.monotonic             # tests replace it to step past an entry's expiry
_LOCAL: Dict[str, Tuple[float, List[OHLCVBar]]] = {}
_INFLIGHT: Dict[str, "asyncio.Future[List[OHLCVBar]]"] = {}


def key(broker: str, exchange: str, symbol: str, interval: str, part: str, anchor: str) -> str:
    return f"md:shared:{(broker or '').lower()}:{exchange.upper()}:{symbol.upper()}:{interval}:{part}:{anchor}"


def _ist(dt: datetime) -> datetime:
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(IST)


def window_anchor(by_date: bool, start: datetime, end: datetime) -> str:
    """What makes two requests for the history window the same request. A broker whose history API takes dates
    (`history_by_date`: Dhan, Upstox) returns the same bars for any time on the same IST day, so its window is its two
    dates. Any other broker takes times: its window is the two times to the minute - every bar starts on a whole minute,
    so requests within the same minute select the same bars - and the entry is read during that minute only."""
    if by_date:
        return f"d:{_ist(start).date().isoformat()}:{_ist(end).date().isoformat()}"
    return f"m:{_ist(start).strftime('%Y-%m-%dT%H:%M')}:{_ist(end).strftime('%Y-%m-%dT%H:%M')}"


def today_anchor(now: datetime) -> str:
    """Today's part: the IST trading day (and the UTC date, which some adapters' "today" follows before 05:30 IST)."""
    now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    return f"t:{_ist(now).date().isoformat()}:{now.astimezone(timezone.utc).date().isoformat()}"


def _redis() -> bool:
    from app.core.rate_limit import redis_backed
    return redis_backed()


def _local_put(name: str, bars: List[OHLCVBar], ttl: float) -> None:
    now = _clock()
    if len(_LOCAL) >= LOCAL_MAX_ENTRIES and name not in _LOCAL:
        for stale in [k for k, (expires, _) in _LOCAL.items() if expires <= now]:
            _LOCAL.pop(stale, None)
        while len(_LOCAL) >= LOCAL_MAX_ENTRIES:
            _LOCAL.pop(min(_LOCAL, key=lambda k: _LOCAL[k][0]), None)     # the one expiring first goes
    _LOCAL[name] = (now + ttl, list(bars))


Got = Tuple[List[OHLCVBar], Optional[float]]        # (bars, seconds the copy has left; None = not known)


async def load_with_ttl(name: str) -> Optional[Got]:
    """The shared bars under this key with the seconds the copy has left, or None."""
    hit = _LOCAL.get(name)
    if hit is not None and hit[0] > _clock():
        return list(hit[1]), hit[0] - _clock()
    if hit is not None:
        _LOCAL.pop(name, None)
    if not _redis():
        return None
    from app.cache.client import cache_get_with_ttl
    raw, left = await cache_get_with_ttl(name)
    if not raw:
        return None
    try:
        bars = [OHLCVBar.model_validate(item) for item in json.loads(raw)]
    except (ValueError, TypeError):
        logger.warning("Discarding unreadable shared candles for %s", name)
        return None
    if left is not None and left > 0:
        _local_put(name, bars, left)       # parsed once per process for the copy's remaining life, never longer
    return bars, left


async def load(name: str) -> Optional[List[OHLCVBar]]:
    """The shared bars under this key, or None."""
    got = await load_with_ttl(name)
    return got[0] if got is not None else None


async def store(name: str, bars: List[OHLCVBar], ttl: int) -> None:
    if not bars or ttl <= 0:
        return
    _local_put(name, bars, ttl)
    if _redis():
        from app.cache.client import cache_set
        await cache_set(name, json.dumps([b.model_dump(mode="json") for b in bars]), ttl)


async def _claim(name: str) -> Optional[str]:
    """The right to fetch this key across processes (a holder id), or None when another process holds it. Without Redis,
    or in a process that never waits on another's fill (WAIT_ON_OTHER_PROCESSES off), this process alone: no lock."""
    holder = uuid.uuid4().hex
    if not _redis() or not WAIT_ON_OTHER_PROCESSES:
        return holder
    from app.cache.client import cache_try_lock
    acquired, reachable = await cache_try_lock("lock:" + name, holder, FILL_LOCK_SECONDS)
    return holder if acquired or not reachable else None


async def _release(name: str, holder: str) -> None:
    if _redis() and WAIT_ON_OTHER_PROCESSES:
        from app.cache.client import cache_release_lock
        await cache_release_lock("lock:" + name, holder)


async def _await_other_process(name: str) -> Optional[Got]:
    """Another API process holds the fetch: its result once stored, or None when it gave up or took too long."""
    from app.cache.client import cache_lock_holder
    deadline = time.monotonic() + FILL_WAIT_SECONDS
    while time.monotonic() < deadline:
        await asyncio.sleep(POLL_SECONDS)
        got = await load_with_ttl(name)
        if got is not None:
            return got
        holder, reachable = await cache_lock_holder("lock:" + name)
        if holder is None or not reachable:
            return await load_with_ttl(name)     # released without a result (its fetch failed): the caller fetches
    return None


async def _own(name: str, fetch: Fetch, ttl_for: TtlFor) -> Got:
    """This request's own fetch, its budget waited for here - no lock held, nobody joined to it."""
    got = await load_with_ttl(name)
    if got is not None and got[0]:
        _count(name, "copy")
        return got
    bars = await fetch()
    ttl = ttl_for(bars) if bars else 0
    await store(name, bars, ttl)
    _count(name, "own")
    return bars, float(ttl) if bars else None


async def _fill(name: str, fetch: Fetch, ttl_for: TtlFor) -> Got:
    """The shared fill: the broker call only if this request's token is there now (api_budget.no_wait - WouldWait
    otherwise), so a lock or a joiner never waits on one user's budget."""
    from app.market_data import api_budget
    holder = await _claim(name)
    if holder is None:
        started = time.monotonic()
        got = await _await_other_process(name)
        _waited("process", time.monotonic() - started)
        if got is not None and got[0]:
            _count(name, "other_process")
            return got
    try:
        with api_budget.no_wait():
            bars = await fetch()
        ttl = ttl_for(bars) if bars else 0
        await store(name, bars, ttl)
        _count(name, "filled")
        return bars, float(ttl) if bars else None
    finally:
        if holder is not None:
            await _release(name, holder)


def _settled(name: str, done: "asyncio.Future[Got]") -> None:
    if _INFLIGHT.get(name) is done:
        _INFLIGHT.pop(name, None)
    if not done.cancelled():
        done.exception()               # retrieved: a fetch whose requester went away never logs "never retrieved"


async def get_or_fetch_ttl(name: str, fetch: Fetch, ttl_for: TtlFor, *, read: bool = True) -> Got:
    """(bars, seconds the shared copy has left) for `name`, fetched once through `fetch` (the caller's own adapter) when
    no copy is fresh. `read=False` skips the copy and any fetch in flight (a caller that needs the bars as of now); its
    result is still stored for the others. A request never waits on someone else's budget: it joins another request's
    fetch for at most JOIN_WAIT_SECONDS, an empty or failed result is never taken as its own, and its own fill takes its
    token only if it is there now - else it waits for it outside the fill."""
    from app.market_data import api_budget
    if not read:
        bars = await fetch()
        ttl = ttl_for(bars) if bars else 0
        await store(name, bars, ttl)
        return bars, float(ttl) if bars else None
    for _ in range(MAX_JOINS):
        got = await load_with_ttl(name)
        if got is not None:
            _count(name, "copy")
            return got
        pending = _INFLIGHT.get(name)
        if pending is not None and pending.get_loop() is not asyncio.get_running_loop():
            pending = None                                     # left by another event loop (tests): not joinable
        if pending is None:
            task = asyncio.ensure_future(_fill(name, fetch, ttl_for))
            _INFLIGHT[name] = task
            task.add_done_callback(lambda done, name=name: _settled(name, done))
            try:
                bars, left = await asyncio.shield(task)        # this request's own fetch: its error is its own
            except api_budget.WouldWait:
                break                                          # its token is not there yet: wait for it outside the fill
            return list(bars), left
        started = time.monotonic()
        try:
            bars, left = await asyncio.wait_for(asyncio.shield(pending), JOIN_WAIT_SECONDS)
        except asyncio.TimeoutError:
            _waited("join", time.monotonic() - started)
            _count(name, "join_timeout")
            break                                              # someone else's fetch is slow: this request's own
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - another request's fetch failed (its budget, its token): try with this one's
            _waited("join", time.monotonic() - started)
            continue
        _waited("join", time.monotonic() - started)
        if bars:
            _count(name, "joined")
            return list(bars), left
        break                                                  # another session's empty answer is not this one's
    return await _own(name, fetch, ttl_for)


async def get_or_fetch(name: str, fetch: Fetch, ttl_for: TtlFor, *, read: bool = True) -> List[OHLCVBar]:
    """`get_or_fetch_ttl` without the copy's remaining seconds."""
    return (await get_or_fetch_ttl(name, fetch, ttl_for, read=read))[0]


def _count(name: str, outcome: str) -> None:
    try:
        from app.observability.metrics import SHARED_CANDLES
        SHARED_CANDLES.labels(part=name.split(":")[6] if name.count(":") >= 6 else "?", outcome=outcome).inc()
    except Exception:  # noqa: BLE001 - a metric never fails a read
        pass


def _waited(on: str, seconds: float) -> None:
    try:
        from app.observability.metrics import SHARED_CANDLES_WAIT
        SHARED_CANDLES_WAIT.labels(on=on).observe(seconds)
    except Exception:  # noqa: BLE001
        pass


def never_wait_on_other_processes() -> None:
    """This process (a worker) reads shared copies but never takes the fill lock nor waits on another process's fill."""
    global WAIT_ON_OTHER_PROCESSES
    WAIT_ON_OTHER_PROCESSES = False


def reset() -> None:
    """Tests: forget the in-process copies and fetches in flight."""
    _LOCAL.clear()
    _INFLIGHT.clear()
