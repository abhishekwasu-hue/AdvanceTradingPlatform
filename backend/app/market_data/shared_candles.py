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
FILL_WAIT_SECONDS = 6.0              # a waiter fetches itself after this (inside the /candles request's 8 s allowance)
POLL_SECONDS = 0.05
LOCAL_MAX_ENTRIES = 64
MAX_JOINS = 3                        # times a request joins another's fetch that then failed, before fetching itself

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


def _local_get(name: str) -> Optional[List[OHLCVBar]]:
    hit = _LOCAL.get(name)
    if hit is None:
        return None
    if hit[0] <= _clock():
        _LOCAL.pop(name, None)
        return None
    return list(hit[1])


def _local_put(name: str, bars: List[OHLCVBar], ttl: int) -> None:
    now = _clock()
    if len(_LOCAL) >= LOCAL_MAX_ENTRIES and name not in _LOCAL:
        for stale in [k for k, (expires, _) in _LOCAL.items() if expires <= now]:
            _LOCAL.pop(stale, None)
        while len(_LOCAL) >= LOCAL_MAX_ENTRIES:
            _LOCAL.pop(min(_LOCAL, key=lambda k: _LOCAL[k][0]), None)     # the one expiring first goes
    _LOCAL[name] = (now + ttl, list(bars))


async def load(name: str) -> Optional[List[OHLCVBar]]:
    """The shared bars under this key, or None."""
    bars = _local_get(name)
    if bars is not None or not _redis():
        return bars
    from app.cache.client import cache_get
    raw = await cache_get(name)
    if not raw:
        return None
    try:
        bars = [OHLCVBar.model_validate(item) for item in json.loads(raw)]
    except (ValueError, TypeError):
        logger.warning("Discarding unreadable shared candles for %s", name)
        return None
    return bars


async def store(name: str, bars: List[OHLCVBar], ttl: int) -> None:
    if not bars or ttl <= 0:
        return
    _local_put(name, bars, ttl)
    if _redis():
        from app.cache.client import cache_set
        await cache_set(name, json.dumps([b.model_dump(mode="json") for b in bars]), ttl)


async def _claim(name: str) -> Tuple[Optional[str], bool]:
    """(holder, others_may_hold): the right to fetch this key across processes. Without Redis this process alone."""
    holder = uuid.uuid4().hex
    if not _redis():
        return holder, False
    from app.cache.client import cache_try_lock
    acquired, reachable = await cache_try_lock("lock:" + name, holder, FILL_LOCK_SECONDS)
    if not reachable:
        return holder, False
    return (holder if acquired else None), True


async def _release(name: str, holder: str) -> None:
    if _redis():
        from app.cache.client import cache_release_lock
        await cache_release_lock("lock:" + name, holder)


async def _await_other_process(name: str) -> Optional[List[OHLCVBar]]:
    """Another API process holds the fetch: its result once stored, or None when it gave up or took too long."""
    from app.cache.client import cache_lock_holder
    deadline = time.monotonic() + FILL_WAIT_SECONDS
    while time.monotonic() < deadline:
        await asyncio.sleep(POLL_SECONDS)
        bars = await load(name)
        if bars is not None:
            return bars
        holder, reachable = await cache_lock_holder("lock:" + name)
        if holder is None or not reachable:
            return await load(name)       # released without a result (its fetch failed): the caller fetches
    return None


async def _fill(name: str, fetch: Fetch, ttl_for: TtlFor) -> List[OHLCVBar]:
    holder, _ = await _claim(name)
    if holder is None:
        bars = await _await_other_process(name)
        if bars is not None:
            return bars
    try:
        bars = await fetch()
        await store(name, bars, ttl_for(bars) if bars else 0)
        return bars
    finally:
        if holder is not None:
            await _release(name, holder)


def _settled(name: str, done: "asyncio.Future[List[OHLCVBar]]") -> None:
    if _INFLIGHT.get(name) is done:
        _INFLIGHT.pop(name, None)
    if not done.cancelled():
        done.exception()               # retrieved: a fetch whose requester went away never logs "never retrieved"


async def get_or_fetch(name: str, fetch: Fetch, ttl_for: TtlFor, *, read: bool = True) -> List[OHLCVBar]:
    """The shared bars for `name`, fetched once through `fetch` (the caller's own adapter) when no copy is fresh.
    `read=False` skips the copy and any fetch in flight (a caller that needs the bars as of now); its result is still
    stored for the others."""
    if not read:
        bars = await fetch()
        await store(name, bars, ttl_for(bars) if bars else 0)
        return bars
    for _ in range(MAX_JOINS):
        bars = await load(name)
        if bars is not None:
            return bars
        pending = _INFLIGHT.get(name)
        if pending is not None and pending.get_loop() is not asyncio.get_running_loop():
            pending = None                                     # left by another event loop (tests): not joinable
        if pending is None:
            task = asyncio.ensure_future(_fill(name, fetch, ttl_for))
            _INFLIGHT[name] = task
            task.add_done_callback(lambda done, name=name: _settled(name, done))
            return list(await asyncio.shield(task))           # this request's own fetch: its error is its own
        try:
            return list(await asyncio.shield(pending))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - another request's fetch failed (its budget, its token): try with this one's
            continue
    bars = await fetch()
    await store(name, bars, ttl_for(bars) if bars else 0)
    return bars


def reset() -> None:
    """Tests: forget the in-process copies and fetches in flight."""
    _LOCAL.clear()
    _INFLIGHT.clear()
