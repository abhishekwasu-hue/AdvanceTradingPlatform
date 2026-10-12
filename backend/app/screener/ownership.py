"""N3 S8a (spec §2.4 category E): ownership and events in ScreenQL - point in time - and their flag gate.

Functions (flag `screener_ownership`, off by default; `needs: ownership`):
    Holding(holder)                   the holder's stake in the latest shareholding pattern known by the bar's close, %
                                      (holder: promoter / pledge / fii / dii / public; pledge = % of promoter shares pledged);
    HoldingChange(holder, quarters)   that stake minus the stake `quarters` patterns before (percentage points);
    DaysToEvent(kind)                 calendar days from the bar's session date to the next event of that kind known by
                                      the bar's close (0 = that day; NaN when none is known): results / board_meeting /
                                      agm / buyback / dividend / ex_date / any.

Point in time - what a bar may know is decided by `known_from`, never by the row's own date:
* a shareholding pattern (`shareholding_snapshots`, append-only): from 09:15 IST on the day after its cited
  publication date when that date is on or after the quarter end and not after the row was stored; otherwise from the
  time it was stored (`created_at`). Two rows for one quarter: the later one is a correction, known from its own time
  (the LG3 rule for reported figures). A pattern whose quarter ended more than STALE_DAYS before the bar is not used.
* a calendar event (`earnings_calendar_events`, append-only, entered with a citation): the same rule, without the
  quarter-end condition - it is known from the day after its cited publication, or from when it was stored.
* an ex-date (`md_corporate_actions`, the lake's splits / bonuses / dividends): from its ingestion (`ingested_at`).
So a value at bar t never changes when later bars or later rows are added (tested, Z10-a causal cases).

While the flag is off the registry endpoint hides the functions and a screen using one is refused with the reason;
alert rules and screen history do not load this data yet (refused with the reason; OPEN_QUESTIONS S8-4). Research only -
matches, never orders.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from app.screener import nodes
from app.screener.registry import OWNERSHIP_FLAG as FLAG

HOLDERS = ("promoter", "pledge", "fii", "dii", "public")
EVENT_KINDS = ("results", "board_meeting", "agm", "buyback", "dividend", "ex_date", "any")
# Calendar event types (app.fundamentals.models.EarningsCalendarEvent.event_type) per kind; ex_date reads the lake.
CALENDAR_KINDS = {"results": ("RESULTS",), "board_meeting": ("BOARD_MEETING",), "agm": ("AGM",), "buyback": ("BUYBACK",),
                  "dividend": ("DIVIDEND",)}
STALE_DAYS = 200                   # a quarter, its 21-day filing window and a missed quarter
SESSION_OPEN_UTC = time(3, 45)     # 09:15 IST
IST_OFFSET = timedelta(hours=5, minutes=30)


@dataclass(frozen=True)
class Pattern:
    quarter_end: date
    known_from: datetime
    stakes: Dict[str, Optional[float]]


@dataclass(frozen=True)
class Event:
    kind: str                      # one of EVENT_KINDS except "any"
    day: date
    known_from: datetime


@dataclass
class Ownership:
    """One symbol's ownership and event rows, as stored (each with when it became known)."""
    patterns: List[Pattern]
    events: List[Event]


def known_from(*, publication_date: Optional[date], stored_at: datetime, not_before: Optional[date] = None) -> datetime:
    """When a row became public (module docstring): the session open after its cited publication, if that is not after
    it was stored (and, for a pattern, not before its quarter ended); otherwise the time it was stored."""
    stored = stored_at if stored_at.tzinfo else stored_at.replace(tzinfo=timezone.utc)
    if publication_date is not None and (not_before is None or publication_date >= not_before):
        public = datetime.combine(publication_date + timedelta(days=1), SESSION_OPEN_UTC, tzinfo=timezone.utc)
        if public <= stored:
            return public
    return stored


def _publication(source_json: Optional[str]) -> Optional[date]:
    import json
    try:
        raw = json.loads(source_json or "null")
    except ValueError:
        return None
    value = raw.get("publication_date") if isinstance(raw, dict) else None
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


# ------------------------------------------------------------------------------------------------------------- the gate
def flagged_calls(ast: Any) -> List[str]:
    from app.screener.registry import FUNCTIONS
    out: List[str] = []
    if ast is None:
        return out
    for node in nodes.walk(ast):
        spec = FUNCTIONS.get(node.name) if isinstance(node, nodes.Call) else None
        if spec is not None and spec.flag == FLAG and spec.name not in out:
            out.append(spec.name)
    return out


def refusal(names: List[str]) -> str:
    return f"{', '.join(n + '()' for n in names)}: ownership and event screens are not enabled for this organisation (flag {FLAG})"


def elsewhere_refusal(names: List[str], where: str) -> str:
    return f"{', '.join(n + '()' for n in names)}: {where} do not read ownership or events yet - run the screen from the Screener"


async def enabled(session: AsyncSession, tenant_id: Any) -> bool:
    from app.platform.controls import flag_enabled
    return await flag_enabled(session, FLAG, tenant_id)


async def blocked_calls(session: AsyncSession, tenant_id: Any, ast: Any) -> List[str]:
    names = flagged_calls(ast)
    if not names or await enabled(session, tenant_id):
        return []
    return names


# ------------------------------------------------------------------------------------------------------------- the data
async def load(session: AsyncSession, symbols: Sequence[str], exchange: str = "NSE", until: Optional[datetime] = None
               ) -> Dict[str, Ownership]:
    """Every symbol's patterns and events known by `until` (all of them when None), in three reads. Shared reference
    data (companies, their patterns and calendars, the lake) - no organisation's own rows."""
    from sqlalchemy import func, select

    from app.db.models import CompanyRecord, EarningsCalendarEventRecord, MdCorporateActionRecord, ShareholdingSnapshotRecord
    names = sorted({s.strip().upper() for s in symbols if s and s.strip()})
    out: Dict[str, Ownership] = {n: Ownership([], []) for n in names}
    if not names:
        return out
    rows = await session.execute(select(CompanyRecord.symbol, ShareholdingSnapshotRecord)
                                 .join(CompanyRecord, CompanyRecord.id == ShareholdingSnapshotRecord.company_id)
                                 .where(func.upper(CompanyRecord.symbol).in_(names)))
    for sym, r in rows.all():
        kf = known_from(publication_date=_publication(r.source_json), stored_at=r.created_at, not_before=r.as_of_date)
        if until is None or kf <= until:
            out[sym.upper()].patterns.append(Pattern(r.as_of_date, kf, {"promoter": r.promoter_pct, "pledge": r.promoter_pledge_pct,
                                                                        "fii": r.fii_pct, "dii": r.dii_pct, "public": r.public_pct}))
    by_type = {t: kind for kind, types in CALENDAR_KINDS.items() for t in types}
    events = await session.execute(select(CompanyRecord.symbol, EarningsCalendarEventRecord)
                                 .join(CompanyRecord, CompanyRecord.id == EarningsCalendarEventRecord.company_id)
                                 .where(func.upper(CompanyRecord.symbol).in_(names)))
    for sym, ev in events.all():
        kind = by_type.get(str(ev.event_type or "").strip().upper())
        if kind is None:
            continue
        kf = known_from(publication_date=_publication(ev.source_json), stored_at=ev.created_at)
        if until is None or kf <= until:
            out[sym.upper()].events.append(Event(kind, ev.event_date, kf))
    actions = await session.execute(select(MdCorporateActionRecord).where(func.upper(MdCorporateActionRecord.symbol).in_(names),
                                                                       MdCorporateActionRecord.exchange == exchange))
    for ca in actions.scalars():
        kf = ca.ingested_at if ca.ingested_at.tzinfo else ca.ingested_at.replace(tzinfo=timezone.utc)
        if until is None or kf <= until:
            out[ca.symbol.upper()].events.append(Event("ex_date", ca.ex_date, kf))
    for own in out.values():
        own.patterns.sort(key=lambda p: (p.known_from, p.quarter_end))
        own.events.sort(key=lambda e: (e.known_from, e.day))
    return out


async def attach(session: AsyncSession, universe: List[Any], exchange: str = "NSE", end: Optional[datetime] = None) -> None:
    """Every symbol's rows known by `end` (now), onto its SymbolData."""
    found = await load(session, [d.symbol for d in universe], exchange, until=end or datetime.now(timezone.utc))
    for data in universe:
        data.ownership = found.get(data.symbol.strip().upper(), Ownership([], []))


# ---------------------------------------------------------------------------------------------------------- the series
def _known_patterns(patterns: Sequence[Pattern], t_ns: int) -> List[Pattern]:
    """Per quarter, the latest pattern known by t (a later row for a quarter is its correction), oldest quarter first."""
    best: Dict[date, Pattern] = {}
    for p in patterns:
        if pd.Timestamp(p.known_from).value <= t_ns and (p.quarter_end not in best or p.known_from >= best[p.quarter_end].known_from):
            best[p.quarter_end] = p
    return [best[k] for k in sorted(best)]


def holding_series(data: Any, f: pd.DataFrame, tf: str, holder: str, quarters: int = 0) -> pd.Series:
    """Holding (quarters = 0) or HoldingChange (quarters >= 1) on every bar of `f`, from the patterns known by its close."""
    from app.screener.runtime import ScreenRuntimeError
    from app.screener.zones import _closes_ns
    if holder not in HOLDERS:
        raise ScreenRuntimeError(f"unknown holder {holder!r}; one of {', '.join(HOLDERS)}")
    own = getattr(data, "ownership", None)
    if own is None:
        raise ScreenRuntimeError(f"{data.symbol}: ownership data was not loaded for this run")
    out = np.full(len(f), np.nan)
    if not len(f) or not own.patterns:
        return pd.Series(out, index=f.index, dtype=float)
    closes_ns = _closes_ns(f.index, tf)
    points = np.unique(np.array([pd.Timestamp(p.known_from).value for p in own.patterns], dtype="int64"))
    seg = np.searchsorted(points, closes_ns, side="right") - 1       # the last change at or before each close
    for s in np.unique(seg[seg >= 0]):
        known = _known_patterns(own.patterns, int(points[s]))
        if len(known) <= quarters:
            continue
        latest, earlier = known[-1], known[-1 - quarters]
        a, b = latest.stakes.get(holder), earlier.stakes.get(holder)
        if a is None or (quarters and b is None):
            continue
        value = float(a) - (float(b) if quarters and b is not None else 0.0)
        rows = np.flatnonzero(seg == s)
        stale_ns = pd.Timestamp(latest.quarter_end + timedelta(days=STALE_DAYS), tz="UTC").value
        out[rows[closes_ns[rows] <= stale_ns]] = value
    return pd.Series(out, index=f.index, dtype=float)


def days_to_event_series(data: Any, f: pd.DataFrame, tf: str, kind: str) -> pd.Series:
    """DaysToEvent on every bar of `f`: the next event of that kind on or after the bar's session date (IST) among the
    events known by the bar's close."""
    from app.screener.runtime import ScreenRuntimeError
    from app.screener.zones import _closes_ns
    if kind not in EVENT_KINDS:
        raise ScreenRuntimeError(f"unknown event kind {kind!r}; one of {', '.join(EVENT_KINDS)}")
    own = getattr(data, "ownership", None)
    if own is None:
        raise ScreenRuntimeError(f"{data.symbol}: ownership data was not loaded for this run")
    out = np.full(len(f), np.nan)
    events = [e for e in own.events if kind == "any" or e.kind == kind]
    if not len(f) or not events:
        return pd.Series(out, index=f.index, dtype=float)
    closes_ns = _closes_ns(f.index, tf)
    starts = pd.DatetimeIndex(f.index)
    starts = starts.tz_localize("UTC") if starts.tz is None else starts.tz_convert("UTC")
    days = np.array([(ts + IST_OFFSET).date().toordinal() for ts in starts], dtype="int64")      # the bar's session date
    known_ns = np.array([pd.Timestamp(e.known_from).value for e in events], dtype="int64")
    event_day = np.array([e.day.toordinal() for e in events], dtype="int64")
    for i in range(len(f)):
        ahead = event_day[(known_ns <= closes_ns[i]) & (event_day >= days[i])]
        if ahead.size:
            out[i] = float(ahead.min() - days[i])
    return pd.Series(out, index=f.index, dtype=float)


__all__ = ["FLAG", "HOLDERS", "EVENT_KINDS", "STALE_DAYS", "Pattern", "Event", "Ownership", "known_from", "flagged_calls", "refusal",
           "elsewhere_refusal", "enabled", "blocked_calls", "load", "attach", "holding_series", "days_to_event_series"]
