"""Reading stored market zones as of a time (Z7a-3) - for the chart layer and the ScreenQL zone functions.

Every read goes through `store.zones_as_of` (the tenant's settings-hash scope rule: the platform snapshot when its
settings resolve to the platform's, else its own scope only; the latest snapshot with as_of <= the time; never another
tenant's): `read_as_of` for one time (the chart), `timeline` for a run of bars (ScreenQL) - the
snapshot each bar sees is the one zones_as_of returns at that bar's close, found by asking zones_as_of at every time the
answer can change (the as_of of each snapshot in the window), so the two can never disagree (tested bar by bar).
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MarketZoneRunRecord
from app.price_action.zones import store
from app.price_action.zones.chart import is_live

MAX_TIMELINE_RUNS = 64                 # snapshots one timeline reads at most (the latest); older bars then read as missing
SESSION_MINUTES = 375                  # NSE cash session, for the calendar span of a number of intraday bars
TF_MINUTES = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": SESSION_MINUTES, "1w": 5 * SESSION_MINUTES,
              "1M": 21 * SESSION_MINUTES}


def _aware_utc(t: Any) -> datetime:
    ts = pd.Timestamp(t)
    if ts.tzinfo is None:
        raise ValueError(f"a zone read time must be timezone-aware (got {t!r})")
    return ts.tz_convert("UTC").to_pydatetime()


@dataclass
class Snapshot:
    """One stored snapshot as a read returned it: its run id, as_of (UTC), scope and the LIVE rows only."""
    run_id: int
    as_of: datetime
    scope: Optional[str]
    price: Optional[float]
    rows: List[Dict[str, Any]]
    fallback: bool = False          # the platform's snapshot served for a tenant whose own settings have none (chart only)


@dataclass
class ZoneTimeline:
    """Which snapshot a bar closing at time t sees: snapshots[i] from times[i] (inclusive) to times[i + 1]; None = no
    snapshot known yet. Before times[0] nothing was read (the window starts there)."""
    key: str
    times: np.ndarray = field(default_factory=lambda: np.array([], dtype="int64"))     # ns since epoch, UTC, ascending
    snapshots: List[Optional[Snapshot]] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return all(s is None for s in self.snapshots)

    def index_at(self, closes_ns: np.ndarray) -> np.ndarray:
        """For each bar close (ns, UTC) the position in `snapshots`, -1 before the window."""
        return np.searchsorted(self.times, closes_ns, side="right") - 1

    def at(self, t: Any) -> Optional[Snapshot]:
        i = int(self.index_at(np.array([pd.Timestamp(_aware_utc(t)).value], dtype="int64"))[0])
        return self.snapshots[i] if i >= 0 else None


def _snapshot(run: Any, rows: List[Dict[str, Any]], scope: Optional[str], fallback: bool = False) -> Optional[Snapshot]:
    if run is None:
        return None
    as_of = store._utc(run.as_of)
    assert as_of is not None
    return Snapshot(int(run.id), as_of, scope, None if run.price is None else float(run.price), [r for r in rows if is_live(r)],
                    bool(fallback))


async def read_as_of(session: AsyncSession, key: str, at: Any, tenant_id: Optional[int], *,
                     allow_platform_fallback: bool = False) -> Optional[Snapshot]:
    """The snapshot `store.zones_as_of` returns for the tenant at `at` (timezone-aware), live rows only; None when none.
    The settings-hash rule applies: a tenant whose settings differ from the platform's reads its own scope only. With
    `allow_platform_fallback` (the chart layer only, never ScreenQL) such a tenant without its own snapshot gets the
    platform's, marked `fallback` - the caller must say so."""
    result = await store.zones_as_of(session, key, _aware_utc(at), tenant_id, allow_platform_fallback=allow_platform_fallback)
    return _snapshot(result.run, result.rows, result.scope, getattr(result, "fallback", False))


async def timeline(session: AsyncSession, key: str, tenant_id: Optional[int], start: Any, end: Any,
                   max_runs: int = MAX_TIMELINE_RUNS) -> ZoneTimeline:
    """The snapshot each time in [start, end] sees (zones_as_of at that time). The answer can only change at a stored
    snapshot's as_of, so zones_as_of is asked at `start` and at each such as_of in (start, end] of the scopes this tenant
    may read (the platform's and its own) - at most `max_runs` of them, the latest; the window then starts later."""
    key = store.normalise_key(key)
    lo, hi = _aware_utc(start), _aware_utc(end)
    scopes = [store.PLATFORM_SCOPE] + ([store.tenant_scope(tenant_id)] if tenant_id is not None else [])
    stamps = await session.scalars(select(MarketZoneRunRecord.as_of).where(
        MarketZoneRunRecord.instrument_key == key, MarketZoneRunRecord.scope.in_(scopes),
        MarketZoneRunRecord.as_of > lo, MarketZoneRunRecord.as_of <= hi).distinct())
    changes = sorted({t for t in (store._utc(s) for s in stamps) if t is not None})
    points = [lo, *changes]
    if len(points) > max_runs:
        points = points[-max_runs:]
    out = ZoneTimeline(key)
    times: List[int] = []
    cache: Dict[int, Snapshot] = {}
    for t in points:
        snap = await read_as_of(session, key, t, tenant_id)
        if snap is not None:
            snap = cache.setdefault(snap.run_id, snap)
        if out.snapshots and times and (out.snapshots[-1] is snap or (out.snapshots[-1] is None and snap is None)):
            continue                                        # the same answer as the previous point: no new segment
        times.append(pd.Timestamp(t).value)
        out.snapshots.append(snap)
    out.times = np.array(times, dtype="int64")
    return out


def window_start(lookback: Mapping[str, int], end: datetime, min_days: int = 3) -> datetime:
    """How far back a screen's bars reach in calendar time: for each timeframe its lookback in sessions, as calendar days
    (weekends and a few holidays on top), the longest; at least `min_days`."""
    days = float(min_days)
    for tf, bars in lookback.items():
        sessions = max(1, int(bars) + 1) * TF_MINUTES.get(tf, SESSION_MINUTES) / SESSION_MINUTES
        days = max(days, sessions * 7 / 5 + 4)
    return _aware_utc(end) - timedelta(days=days)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


__all__ = ["Snapshot", "ZoneTimeline", "read_as_of", "timeline", "window_start", "MAX_TIMELINE_RUNS"]
