"""AUD-T8 (reliability promise): uptime per market session, the in-app degradation notice and the published SLOs.

`GET /api/status/sessions` (no login, read-only, rate-limited with the status page's own limiter; a plain 404 while the
flag `status_page` is off). Built only from what the platform measures itself:

* **Samples** - the trading worker writes one `platform_status_samples` row per cycle, at most once every
  `SAMPLE_MIN_SECONDS` (`record_sample`, called after its heartbeat; it never blocks a cycle). A row carries the time and
  how many decisions that cycle refused for stale market data. Rows older than `RETENTION_DAYS` are pruned on write.
  The sampler runs whatever the flag says, so the history has no gap when the page is turned on or off.
* **Uptime** - per exchange family (NSE, MCX, CDS, crypto: app/market_data/calendar.py) and per regular session (crypto:
  one session per IST calendar day), the share of the session's minutes in which the engine had checked in within
  `stale_seconds()` (SLO-2: three cycles) and market data was fresh (SLO-4: fewer than `STALE_LIMIT` refusals in the last
  `STALE_WINDOW_SECONDS`). A minute with no recent sample is a down minute. Minutes before the first stored sample are
  not counted (not measured), so a fresh install does not read as down.
* **Notice** - the one in-app notice (v2 shell): the engine has not checked in while a session is open, or market data
  is behind. Plain words; nothing while all is well or while every market is closed.
* **SLOs** - the published targets (app/observability/slo.py, mirrored from docs/SLO.md).

Never in the body: user, organisation, price, symbol, host, error text or version. Nothing here touches an order.
"""
from __future__ import annotations

import asyncio
import bisect
import logging
import math
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import WORKER_CYCLE_SECONDS
from app.db.models import PlatformStatusSampleRecord
from app.db.session import get_session
from app.market_data.calendar import EXCHANGE_SESSIONS, IST, session_status
from app.observability.slo import public_slos
from app.observability.status_page import status_rate_limit

logger = logging.getLogger(__name__)

FLAG = "status_page"
PUBLIC_PAGE_FLAG = "public_status_page"
FAMILIES: Tuple[str, ...] = ("NSE", "MCX", "CDS", "CRYPTO")
NAMES = {"NSE": "NSE equity and F&O", "MCX": "MCX commodities", "CDS": "Currency derivatives", "CRYPTO": "Crypto"}
SAMPLE_MIN_SECONDS = 50          # one row per cycle at most about once a minute (the cycle is WORKER_CYCLE_SECONDS)
RETENTION_DAYS = 14
PRUNE_EVERY_SECONDS = 3600
LOOKBACK_DAYS = 10               # how far back the recent sessions are read (inside the retention)
RECENT_SESSIONS = 5              # sessions shown per market, newest first
STALE_WINDOW_SECONDS = 600       # SLO-4: "fewer than 5 decisions per 10 minutes refused for stale data"
STALE_LIMIT = 5
CACHE_SECONDS = 15.0

OPERATIONAL, DEGRADED, OUTAGE, UNKNOWN, CLOSED = "operational", "degraded", "outage", "unknown", "closed"

router = APIRouter(tags=["system"])

_cache: Dict[str, Tuple[float, Optional[Dict[str, Any]]]] = {}
_locks: Dict[int, asyncio.Lock] = {}
_writer: Dict[str, float] = {}       # "sampled" / "pruned" -> monotonic time of the last write / prune (worker process)


def stale_seconds() -> int:
    """SLO-2 and `/api/system/health/deep`: a heartbeat older than three cycles is stale."""
    return WORKER_CYCLE_SECONDS * 3


def reset_cache() -> None:
    """Forget the cached body (the admin console when the flag `status_page` or `public_status_page` is turned on or off)."""
    _cache.clear()


def reset() -> None:
    """Tests: forget the cached body and the writer's clocks."""
    _cache.clear()
    _writer.clear()


def _aware(ts: datetime) -> datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


# --- writer (the trading worker) -----------------------------------------------------------------------------------------

async def record_sample(session: AsyncSession, now: Optional[datetime] = None, stale_skips: int = 0) -> bool:
    """One sample for this cycle (False when the last one is under `SAMPLE_MIN_SECONDS` old); prunes past the retention
    at most once every `PRUNE_EVERY_SECONDS`. Commits its own work. Writes nothing about any organisation or user."""
    clock = _time.monotonic()
    last = _writer.get("sampled")
    if last is not None and clock - last < SAMPLE_MIN_SECONDS:
        return False
    now = _aware(now or datetime.now(timezone.utc))
    session.add(PlatformStatusSampleRecord(sampled_at=now, stale_skips=max(0, int(stale_skips or 0))))
    pruned = _writer.get("pruned")
    if pruned is None or clock - pruned >= PRUNE_EVERY_SECONDS:
        await session.execute(delete(PlatformStatusSampleRecord).where(
            PlatformStatusSampleRecord.sampled_at < now - timedelta(days=RETENTION_DAYS)))
        _writer["pruned"] = clock
    await session.commit()
    _writer["sampled"] = clock
    return True


# --- pure math -------------------------------------------------------------------------------------------------------------

Sample = Tuple[datetime, int]         # (sampled_at, stale_skips), sorted by time


def data_ok_flags(samples: Sequence[Sample]) -> List[bool]:
    """Per sample: market data was fresh at that moment (SLO-4) - the refusals in the window ending at it, this sample
    included, stay under `STALE_LIMIT`."""
    flags: List[bool] = []
    start, total = 0, 0
    for ts, skips in samples:
        total += skips
        while samples[start][0] <= ts - timedelta(seconds=STALE_WINDOW_SECONDS):
            total -= samples[start][1]
            start += 1
        flags.append(total < STALE_LIMIT)
    return flags


def uptime_pct(samples: Sequence[Sample], ok: Sequence[bool], start: datetime, end: datetime,
               measured_from: Optional[datetime]) -> Optional[float]:
    """The share (percent, 2 decimals) of the minutes in [start, end) that were up: at the end of each minute the newest
    sample is at most `stale_seconds()` old and its data was fresh. Minutes before `measured_from` (the first stored
    sample) are not counted; None when nothing of the window was measured."""
    if measured_from is None:
        return None
    lo = max(start, measured_from)
    if end <= lo:
        return None
    times = [s[0] for s in samples]
    limit = timedelta(seconds=stale_seconds())
    minutes = math.ceil((end - lo).total_seconds() / 60)
    up = 0
    for k in range(minutes):
        point = min(lo + timedelta(minutes=k + 1), end)
        i = bisect.bisect_right(times, point) - 1
        if i >= 0 and point - times[i] <= limit and ok[i]:
            up += 1
    return round(100.0 * up / minutes, 2)


def session_windows(family: str, now: datetime, holidays: Iterable[date] = (), count: int = RECENT_SESSIONS,
                    lookback_days: int = LOOKBACK_DAYS) -> List[Tuple[datetime, datetime]]:
    """The newest `count` regular sessions of a family that had begun by `now`, newest first, as aware (open, close)
    pairs. Crypto trades around the clock: one session per IST calendar day."""
    spec = EXCHANGE_SESSIONS[family]
    holiday_set: Set[date] = set(holidays)
    today = now.astimezone(IST).date()
    out: List[Tuple[datetime, datetime]] = []
    for back in range(lookback_days + 1):
        day = today - timedelta(days=back)
        if spec.always_open:
            opens = datetime.combine(day, time(0, 0), tzinfo=IST)
            closes = opens + timedelta(days=1)
        else:
            if (spec.weekdays_only and day.weekday() >= 5) or day in holiday_set:
                continue
            opens = datetime.combine(day, spec.open, tzinfo=IST)
            closes = datetime.combine(day, spec.close, tzinfo=IST)
        if opens > now:
            continue
        out.append((opens, closes))
        if len(out) >= count:
            break
    return out


@dataclass(frozen=True)
class Measured:
    heartbeat_age: Optional[int]       # seconds; None = the engine has never checked in
    data_fresh: Optional[bool]         # None = no sample in the last window


def measure(samples: Sequence[Sample], ok: Sequence[bool], heartbeat_at: Optional[datetime], now: datetime) -> Measured:
    age = None if heartbeat_at is None else max(0, int((now - _aware(heartbeat_at)).total_seconds()))
    recent = [i for i, s in enumerate(samples) if s[0] > now - timedelta(seconds=STALE_WINDOW_SECONDS)]
    return Measured(age, (ok[recent[-1]] if recent else None))


def session_state(open_now: bool, m: Measured) -> str:
    if not open_now:
        return CLOSED
    if m.heartbeat_age is None:
        return UNKNOWN
    if m.heartbeat_age > stale_seconds():
        return OUTAGE
    if m.data_fresh is False:
        return DEGRADED
    return OPERATIONAL


def notice_for(any_open: bool, m: Measured) -> Optional[Dict[str, str]]:
    """The in-app notice, or None. Only while a market is open: a closed market has nothing to fall behind on."""
    if not any_open or m.heartbeat_age is None:
        return None
    if m.heartbeat_age > stale_seconds():
        return {"state": OUTAGE, "text": "The trading engine is catching up. New signals and alerts resume on their own as soon as it is back."}
    if m.data_fresh is False:
        return {"state": DEGRADED, "text": "Market data is running late. Signals wait for fresh data and resume on their own."}
    return None


# --- the body ------------------------------------------------------------------------------------------------------------

async def build(session: AsyncSession, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """The public body; None while the flag is off."""
    from app.db.models import WorkerHeartbeatRecord
    from app.market_data.calendar import family_holidays
    from app.platform.controls import flag_enabled

    if not await flag_enabled(session, FLAG):
        return None
    now = _aware(now or datetime.now(timezone.utc))
    rows = (await session.execute(
        select(PlatformStatusSampleRecord.sampled_at, PlatformStatusSampleRecord.stale_skips)
        .where(PlatformStatusSampleRecord.sampled_at > now - timedelta(days=LOOKBACK_DAYS + 1),
               PlatformStatusSampleRecord.sampled_at <= now)
        .order_by(PlatformStatusSampleRecord.sampled_at))).all()
    samples: List[Sample] = [(_aware(r[0]), int(r[1] or 0)) for r in rows]
    ok = data_ok_flags(samples)
    first = await session.scalar(select(PlatformStatusSampleRecord.sampled_at).order_by(PlatformStatusSampleRecord.sampled_at).limit(1))
    measured_from = _aware(first) if first is not None else None
    heartbeat_at = await session.scalar(select(WorkerHeartbeatRecord.last_seen_at).where(WorkerHeartbeatRecord.worker_name == "trading_worker"))
    m = measure(samples, ok, heartbeat_at, now)

    sessions: List[Dict[str, Any]] = []
    any_open = False
    for family in FAMILIES:
        holidays = await family_holidays(session, family)
        status = session_status(now, holidays, family)
        any_open = any_open or status.is_open
        recent = []
        for opens, closes in session_windows(family, now, holidays):
            running = opens <= now < closes
            recent.append({"date": opens.date().isoformat(), "opens_at": opens.isoformat(), "closes_at": closes.isoformat(),
                           "in_progress": running,
                           "uptime_pct": uptime_pct(samples, ok, opens, min(closes, now), measured_from)})
        sessions.append({"key": family, "name": NAMES[family], "open": status.is_open,
                         "next_open": status.next_open.isoformat() if status.next_open else None,
                         "state": session_state(status.is_open, m), "recent": recent})
    return {
        "checked_at": now.isoformat(),
        "measured": {"seconds_since_heartbeat": m.heartbeat_age, "heartbeat_limit_seconds": stale_seconds(),
                     "data_fresh": m.data_fresh, "measured_since": measured_from.isoformat() if measured_from else None},
        "sessions": sessions,
        "slos": public_slos(),
        "notice": notice_for(any_open, m),
        "public_page": await flag_enabled(session, PUBLIC_PAGE_FLAG),
    }


def _lock() -> asyncio.Lock:
    loop_id = id(asyncio.get_running_loop())
    lock = _locks.get(loop_id)
    if lock is None:
        _locks.clear()
        lock = _locks[loop_id] = asyncio.Lock()
    return lock


def _cached() -> Tuple[bool, Optional[Dict[str, Any]]]:
    entry = _cache.get("body")
    if entry is not None and _time.monotonic() - entry[0] < CACHE_SECONDS:
        return True, entry[1]
    return False, None


@router.get("/api/status/sessions", dependencies=[Depends(status_rate_limit)])
async def status_sessions(response: Response, session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Unauthenticated, read-only. A plain 404 while the `status_page` flag is off; 503 when it cannot be read."""
    hit, body = _cached()
    if not hit:
        async with _lock():
            hit, body = _cached()
            if not hit:
                try:
                    body = await build(session)
                except Exception:  # noqa: BLE001 - the database is not answering: say so plainly, never a 500 page
                    logger.warning("Status sessions not built", exc_info=True)
                    raise HTTPException(status_code=503, detail="Unavailable") from None
                _cache["body"] = (_time.monotonic(), body)
    if body is None:
        raise HTTPException(status_code=404, detail="Not Found")
    response.headers["Cache-Control"] = f"public, max-age={int(CACHE_SECONDS)}"
    return body
