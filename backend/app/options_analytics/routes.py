"""N7 I: options analytics over HTTP (flag `options_analytics`, off by default).

`GET /api/options/analytics?underlying=NIFTY&at=<ISO time>` - ATM IV and the term structure, skew, expected move, PCR,
max pain per expiry and IV rank / percentile, as of `at` (default now), from the chain recorder's captures and the
caller's organisation's own uploads. BT1b: with the bring-your-own-broker data tier on, the recorder's rows (captured
through other organisations' broker sessions) are not used - only the organisation's own uploads. Information only.

No look-ahead: a quote counts at `at` only when it was captured AND stored by then (`chain_recorder.known_by`), so an
upload of older captures made after `at` never changes the answer at `at`.

Bounds: one call reads at most MAX_STAMPS distinct capture times and MAX_ROWS quotes (the newest first, cut only at a
whole session's window; the answer says when), in IN lists of at most IN_CHUNK keys; at most MAX_CONCURRENT answers are
computed at once per API process (then 429; a slot is held until its worker thread has finished, even when the client
went away) on top of the per-user rate limit. The event loop is never held: quotes are
read as plain column tuples in partitions of PARTITION rows (an await between partitions) and the analytics run in a
worker thread.

IV unit: declared per source (IV_UNIT) - the recorder stores what the broker's chain quotes, which is percent for every
broker that records chains (Upstox, Dhan); an upload is percent (the `OptionChainSnapshotRow` convention). A source
declared as a fraction is scaled to percent before the analytics read it.
"""
from __future__ import annotations

import asyncio
from bisect import bisect_left, bisect_right
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.core.rate_limit import user_rate_limit
from app.db.models import User
from app.db.session import get_session
from app.market_data.calendar import IST
from app.options_analytics import analytics as A

FLAG = "options_analytics"
MAX_LOOKBACK_DAYS = 400
MAX_STAMPS = 50_000         # distinct capture times read for one answer (the recorder writes ~75 a session)
MAX_ROWS = 200_000          # quotes read for one answer
IN_CHUNK = 5_000            # keys per IN list
PARTITION = 5_000           # rows read between two awaits
MAX_CONCURRENT = 4          # answers computed at once per API process
IV_UNIT: Dict[str, str] = {"recorder": "percent", "upload": "percent"}
_busy = 0
DISCLAIMER = "Options analytics on recorded quotes - information, not a recommendation; nothing here places an order."

router = APIRouter(tags=["options-analytics"])
analytics_rate_limit = user_rate_limit("options_analytics", limit=30, window_seconds=60)


def _utc(ts: datetime) -> datetime:
    if ts.tzinfo is timezone.utc:
        return ts                                         # asyncpg's values: no new object per row
    return ts.astimezone(timezone.utc) if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def source_kind(source: str) -> str:
    from app.backtest.chain_recorder import UPLOAD_SOURCE
    return "upload" if (source or "").startswith(UPLOAD_SOURCE) else "recorder"


def iv_percent(value: Optional[float], source: str) -> Optional[float]:
    """The stored IV in percent, by the unit its source declares (IV_UNIT)."""
    if value is None:
        return None
    return float(value) * 100.0 if IV_UNIT.get(source_kind(source), "percent") == "fraction" else float(value)


def windows(stamps: List[datetime], at: datetime, start: datetime) -> List[Tuple[datetime, List[datetime]]]:
    """Of the capture times (UTC, any order), the groups an answer at `at` reads, newest first: the snapshot (those within
    MAX_AGE before `at`), then each IST session's window (within MAX_AGE before its last capture at or after `start`)."""
    asc = sorted(set(stamps))
    last: Dict[date, datetime] = {}
    for ts in asc:
        if ts >= start:
            last[ts.astimezone(IST).date()] = ts
    ends = [at, *sorted(last.values(), reverse=True)]
    return [(end, asc[bisect_left(asc, end - A.MAX_AGE): bisect_right(asc, end)]) for end in ends]


def needed_stamps(stamps: List[datetime], at: datetime, start: datetime) -> List[datetime]:
    """Every capture time the windows read, newest first."""
    return sorted({ts for _, group in windows(stamps, at, start) for ts in group}, reverse=True)


async def _rows(session: AsyncSession, underlying: str, at: datetime, lookback_days: int, tenant_id: int,
                own_only: bool) -> Tuple[List[A.Row], List[str]]:
    from app.backtest import chain_recorder as R
    from app.db.models import OptionChainSnapshotRecord as Rec
    who = (Rec.source == R.upload_source(tenant_id)) if own_only else R.visible_to(tenant_id)
    base = and_(Rec.underlying == underlying, who, R.known_by(at), Rec.captured_at <= at)
    start = at - timedelta(days=lookback_days)
    limits: List[str] = []
    raw = list(await session.scalars(select(Rec.captured_at).where(base, Rec.captured_at >= start - A.MAX_AGE)
                                      .distinct().order_by(Rec.captured_at.desc()).limit(MAX_STAMPS + 1)))
    stamp_floor: Optional[datetime] = None
    if len(raw) > MAX_STAMPS:
        raw = raw[:MAX_STAMPS]
        stamp_floor = min(_utc(v) for v in raw)           # a window reaching below it may be missing captures
        limits.append(f"Only the newest {MAX_STAMPS} capture times were read; IV history starts later than asked")
    by_utc: Dict[datetime, List[datetime]] = {}
    for value in raw:                                     # the stored value is matched exactly in the IN list
        by_utc.setdefault(_utc(value), []).append(value)
    groups = [g for end, g in windows(list(by_utc), at, start) if stamp_floor is None or end - A.MAX_AGE >= stamp_floor]
    wanted = [v for ts in sorted({ts for g in groups for ts in g}, reverse=True) for v in by_utc[ts]]
    cols = (Rec.captured_at, Rec.expiry, Rec.strike, Rec.right, Rec.ltp, Rec.iv, Rec.oi, Rec.underlying_ltp, Rec.source)
    loaded: List[A.Row] = []
    full = False
    for i in range(0, len(wanted), IN_CHUNK):
        q = (select(*cols).where(base, Rec.captured_at.in_(wanted[i:i + IN_CHUNK]))
             .order_by(Rec.captured_at.desc(), Rec.id).limit(MAX_ROWS - len(loaded) + 1))
        result = await session.stream(q)
        async for part in result.partitions(PARTITION):   # an await between partitions: the loop is never held long
            loaded += [A.Row(_utc(ts), e, k, r, ltp, iv_percent(iv, src), oi, u) for ts, e, k, r, ltp, iv, oi, u, src in part]
        if len(loaded) > MAX_ROWS:
            full = True
            break
    if not full:
        return loaded, limits
    # cut at a whole window: the capture time of the first row past the cap is partial, so every window reaching it is dropped
    cut = loaded[MAX_ROWS].timestamp
    whole = {ts for g in groups if all(ts > cut for ts in g) for ts in g}
    limits.append(f"Only the newest {MAX_ROWS} quotes were read (whole sessions only); IV history starts later than asked")
    return [r for r in loaded[:MAX_ROWS] if r.timestamp in whole], limits


@asynccontextmanager
async def analytics_slot() -> AsyncIterator[None]:
    """One of MAX_CONCURRENT answers computed at once in this API process; none free -> 429."""
    global _busy
    if _busy >= MAX_CONCURRENT:
        raise HTTPException(status_code=429, detail="Options analytics are busy - try again in a few seconds", headers={"Retry-After": "5"})
    _busy += 1
    try:
        yield
    finally:
        _busy -= 1


async def _compute(rows: List[A.Row], when: datetime, lookback_days: int) -> Dict[str, Any]:
    """The analytics in a worker thread, so the event loop keeps serving other requests. A thread cannot be stopped: when
    the request is cancelled (the client went away) this waits for the thread before the cancellation goes on, so the
    slot it runs in is held until the work has really finished (never more than MAX_CONCURRENT running)."""
    work = asyncio.ensure_future(asyncio.to_thread(A.analytics, rows, when, lookback_days=lookback_days))
    try:
        return await asyncio.shield(work)
    except asyncio.CancelledError:
        while not work.done():
            try:
                await asyncio.shield(work)
            except asyncio.CancelledError:
                continue
            except Exception:  # noqa: BLE001 - the work's own failure no longer matters to a cancelled request
                break
        raise


@router.get("/api/options/analytics", dependencies=[Depends(analytics_rate_limit)])
async def options_analytics(underlying: str = Query(min_length=1, max_length=30), at: Optional[datetime] = None,
                            lookback_days: int = Query(default=A.LOOKBACK_DAYS, ge=30, le=MAX_LOOKBACK_DAYS),
                            user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    from app.instruments.master import underlying_of
    from app.market_data import live_access
    from app.platform.controls import require_flag
    await require_flag(session, FLAG, user.tenant_id)
    now = datetime.now(timezone.utc)
    when = _utc(at) if at is not None else now
    if when > now:
        raise HTTPException(status_code=422, detail="at: a time in the future has no recorded quotes")
    name = underlying_of(underlying)
    own_only = await live_access.byob_on(session, user.tenant_id)
    async with analytics_slot():
        rows, limits = await _rows(session, name, when, lookback_days, user.tenant_id, own_only)
        out = await _compute(rows, when, lookback_days)
    from app.backtest.chain_recorder import CHAIN_SNAPSHOT_ATM_SPAN
    coverage = (f"PCR, max pain and skew cover only the strikes quoted (each expiry's `strikes`): the recorder keeps "
                f"{CHAIN_SNAPSHOT_ATM_SPAN} strikes either side of the money at each capture, not the whole chain; "
                "an upload covers what was uploaded")
    return {"underlying": name, **out, "limits": limits, "coverage": coverage,
            "data": ("this organisation's uploaded quotes only (bring-your-own-broker data tier)" if own_only
                     else "the platform's recorded quotes and this organisation's uploads"),
            "known_by": "quotes captured and stored by this time only (a later upload or correction never changes it)",
            "disclaimer": DISCLAIMER}
