"""Reading stored market zones as of a time (Z7a-3) - for the chart layer and the ScreenQL zone functions.

Every read goes through `store.zones_as_of` (the tenant's settings-hash scope rule: the platform snapshot when its
settings resolve to the platform's, else its own scope only; the latest snapshot with as_of <= the time; never another
tenant's): `read_as_of` for one time (the chart), `timeline` for a run of bars (ScreenQL) - the
snapshot each bar sees is the one zones_as_of returns at that bar's close, found by asking zones_as_of at every time the
answer can change (the as_of of each snapshot in the window), so the two can never disagree (tested bar by bar).
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MarketZoneRecord, MarketZoneRunRecord
from app.price_action.zones import store
from app.price_action.zones.chart import is_live

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
    _arrays: Dict[str, Dict[str, np.ndarray]] = field(default_factory=dict, repr=False, compare=False)

    def arrays(self, family: str = "all") -> Dict[str, np.ndarray]:
        """The live rows of a family as arrays for the vectorised zone functions, ordered strongest first, then by
        zone_key - so the first of equally near zones is the one `chart.nearest_zone` picks. Built once per snapshot."""
        if family not in self._arrays:
            from app.price_action.zones.chart import in_family, zone_strength
            rows = [r for r in self.rows if is_live(r) and in_family(r, family)]
            strength = [zone_strength(r) for r in rows]
            order = sorted(range(len(rows)), key=lambda i: (-strength[i], str(rows[i]["zone_key"])))
            pick = [rows[i] for i in order]
            self._arrays[family] = {
                "low": np.array([float(r["zone_low"]) for r in pick], dtype=float),
                "high": np.array([float(r["zone_high"]) for r in pick], dtype=float),
                "strength": np.array([strength[i] for i in order], dtype=float),
                "state": np.array([str(r["state"]).upper() for r in pick], dtype=object),
            }
        return self._arrays[family]


@dataclass
class ZoneTimeline:
    """Which snapshot a bar closing at time t sees: snapshots[i] from times[i] (inclusive) to times[i + 1]; None = no
    snapshot known yet. Before times[0] nothing was read (the window starts there)."""
    key: str
    times: np.ndarray = field(default_factory=lambda: np.array([], dtype="int64"))     # ns since epoch, UTC, ascending
    snapshots: List[Optional[Snapshot]] = field(default_factory=list)
    truncated: bool = False         # more snapshots in the window than a read takes: the zone series refuse, never guess

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


MAX_TIMELINE_RUNS = 500                # snapshots one timeline reads at most; past that the series refuses (never a silent False)
_ROW_COLUMNS = ("run_id", "zone_key", "zone_type", "timeframe", "zone_low", "zone_high", "strength", "state", "touches", "remembered")
_IN_CHUNK = 500                        # ids per IN (...) - under SQLite's parameter limit


async def timelines(session: AsyncSession, keys: Sequence[str], tenant_id: Optional[int], start: Any, end: Any,
                    max_runs: int = MAX_TIMELINE_RUNS) -> Dict[str, ZoneTimeline]:
    """For every key, the snapshot each time in [start, end] sees - what `store.zones_as_of` (no fallback) returns then:
    the latest run with as_of <= t in the key's read scope (store.read_scopes: platform when the tenant's settings resolve
    to the platform's, else its own). Batched for a whole universe: one settings query, one query for the runs in the
    window plus the latest one before it per key, and the live rows of all of them in one `run_id IN (...)` query - a
    fixed number of queries whatever the number of keys or snapshots (tested). A key with more than `max_runs` runs in
    the window keeps the latest ones and is marked `truncated` (its zone series then refuse with the reason)."""
    lo, hi = _aware_utc(start), _aware_utc(end)
    norm = {k: store.normalise_key(k) for k in keys}
    scopes = await store.read_scopes(session, list(set(norm.values())), tenant_id) if norm else {}
    pairs = sorted(set(scopes.items()))
    out: Dict[str, ZoneTimeline] = {}
    if not pairs:
        return out
    R = MarketZoneRunRecord
    key_in, scope_in = R.instrument_key.in_([k for k, _ in pairs]), R.scope.in_(sorted({sc for _, sc in pairs}))
    floor = (select(R.instrument_key.label("k"), R.scope.label("sc"), func.max(R.as_of).label("m"))
             .where(key_in, scope_in, R.as_of <= lo).group_by(R.instrument_key, R.scope).subquery())
    cols = (R.id, R.instrument_key, R.scope, R.as_of, R.price)
    floor_runs = (await session.execute(select(*cols).join(floor, and_(R.instrument_key == floor.c.k, R.scope == floor.c.sc,
                                                                         R.as_of == floor.c.m)))).all()
    window_runs = (await session.execute(select(*cols).where(key_in, scope_in, R.as_of > lo, R.as_of <= hi))).all()
    by_key: Dict[str, List[Any]] = {}
    for run in [*floor_runs, *window_runs]:
        if scopes.get(run.instrument_key) == run.scope:
            by_key.setdefault(run.instrument_key, []).append(run)
    kept: Dict[str, Tuple[List[Any], bool, bool]] = {}
    for key, runs in by_key.items():
        runs.sort(key=lambda r: _run_time(r))
        has_floor = bool(runs) and _run_time(runs[0]) <= lo
        inside = runs[1:] if has_floor else runs
        truncated = len(inside) > max_runs
        kept[key] = (runs[-max_runs:] if truncated else runs, has_floor and not truncated, truncated)
    rows = await _live_rows(session, [r.id for runs, _, _ in kept.values() for r in runs])
    for original, key in norm.items():
        tl = ZoneTimeline(key)
        runs, floor_known, truncated = kept.get(key, ([], False, False))
        times: List[int] = []
        snaps: List[Optional[Snapshot]] = []
        if not truncated and not floor_known:
            times.append(pd.Timestamp(lo).value)
            snaps.append(None)                                  # nothing known at the window start
        for i, r in enumerate(runs):
            as_of = store._utc(r.as_of)
            assert as_of is not None
            t = lo if (i == 0 and floor_known) else as_of
            times.append(pd.Timestamp(t).value)
            snaps.append(Snapshot(int(r.id), as_of, r.scope, None if r.price is None else float(r.price), rows.get(int(r.id), [])))
        tl.times, tl.snapshots, tl.truncated = np.array(times, dtype="int64"), snaps, truncated
        out[original] = tl
    return out


def _run_time(run: Any) -> datetime:
    t = store._utc(run.as_of)
    assert t is not None                    # as_of is NOT NULL
    return t


async def _live_rows(session: AsyncSession, run_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
    """The live rows (not invalidated) of many runs, only the columns the chart and ScreenQL read (no tracker JSON)."""
    out: Dict[int, List[Dict[str, Any]]] = {}
    Z = MarketZoneRecord
    for i in range(0, len(run_ids), _IN_CHUNK):
        chunk = run_ids[i:i + _IN_CHUNK]
        result = await session.execute(select(*(getattr(Z, c) for c in _ROW_COLUMNS))
                                       .where(Z.run_id.in_(chunk), Z.state != "invalidated").order_by(Z.run_id, Z.id))
        for rec in result.all():
            row = dict(zip(_ROW_COLUMNS, rec))
            out.setdefault(int(row.pop("run_id")), []).append(row)
    return out


async def timeline(session: AsyncSession, key: str, tenant_id: Optional[int], start: Any, end: Any,
                   max_runs: int = MAX_TIMELINE_RUNS) -> ZoneTimeline:
    """`timelines` for one key."""
    return (await timelines(session, [key], tenant_id, start, end, max_runs))[key]


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


__all__ = ["Snapshot", "ZoneTimeline", "read_as_of", "timeline", "timelines", "window_start", "MAX_TIMELINE_RUNS"]
