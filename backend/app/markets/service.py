"""MO4 slice 2 (ATP_SESSION_GROWTH_MASTER.md PART 2 item 6): the Sector & index view - a "ready-made dish" built from
the system watchlists (WL1, app/watchlists/system.py) and the shared end-of-day history (the daily lake).

For every index of the "Indices" system list and every list of the "Sectors" group:
- its move over the day, the week (5 sessions) and the month (21 sessions): an index's own closes; a sector (no index
  of its own) is the plain average of its stocks' moves, and says so (`method`);
- breadth: how many of its stocks rose, fell or did not change over the same period;
- its strongest and weakest stocks over the period (names are fine here: the user's own market data view);
- the move against the NIFTY 50 over the same period (percentage points), i.e. relative strength.

Where the data comes from: the system lists (no download here) and `md_candles` daily closes (NSE cash shares
`NSE:<symbol>`, NSE indices `NSE_INDEX:<code>`). The sessions are the NIFTY 50's own stored days (a cash bar shares the
index bar's end time, as the context panel's breadth tile reads them). A number that is not stored is null and the page
says "I don't know" - never a sample or a guess. Nothing per user or per organisation is read, so one answer serves
everyone for a few minutes (`CACHE_SECONDS`). Information only: no buy / sell wording, nothing places an order.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.context_panel.service import freshness, index_key
from app.db.models import MdCandleRecord, SystemWatchlistRecord
from app.market_data.calendar import IST
from app.universe.indices import catalogue
from app.watchlists import system

FLAG = "markets_view"
PAGE_FLAG = "ui_v2"
TIMEFRAME = "day"
BENCHMARK = "NIFTY 50"
# (period, sessions back from the latest close)
PERIODS: Tuple[Tuple[str, int], ...] = (("day", 1), ("week", 5), ("month", 21))
PERIOD_KEYS: Tuple[str, ...] = tuple(p for p, _ in PERIODS)
CALENDAR_DAYS = 60          # history read for the session list: 21 sessions back plus holidays
TOP_N = 3                   # strongest / weakest stocks per group and period
CHUNK = 400                 # keys per IN (...) read
CACHE_SECONDS = 300
FAMILY_LABELS: Dict[str, str] = {"BROAD": "Broad market", "SECTORAL": "Sector indices", "THEMATIC": "Themes",
                                 "STRATEGY": "Strategy indices", "BSE": "BSE"}
FAMILY_ORDER: Tuple[str, ...] = ("BROAD", "SECTORAL", "THEMATIC", "STRATEGY", "BSE")
SOURCE = "NSE end-of-day closes (the daily history every user shares)"
NOTE = "How each index and sector moved at the close, for your own reading."
SECTOR_METHOD = "The average move of the sector's stocks (each stock counts the same)."
INDEX_METHOD = "The index's own closing value."
NO_LISTS = "The index and sector lists are not ready yet. They are built once a day after the market data update."
NO_DATA = "I don't know yet: fewer than two market closes are stored, so no move can be worked out."


def _day(ts: datetime) -> date:
    return (ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)).astimezone(IST).date()


# --- pure ------------------------------------------------------------------------------------------------------------

def session_refs(sessions: Sequence[date]) -> Dict[str, Optional[date]]:
    """The latest session (`as_of`) and, per period, the session that many sessions before it (None: not stored)."""
    days = sorted(set(sessions))
    out: Dict[str, Optional[date]] = {"as_of": days[-1] if days else None}
    for name, back in PERIODS:
        out[name] = days[-1 - back] if len(days) > back else None
    return out


def pct_change(now: Optional[float], then: Optional[float]) -> Optional[float]:
    if now is None or then is None or then <= 0 or now <= 0:
        return None
    return round(100.0 * (now / then - 1.0), 2)


def changes(closes: Dict[date, float], refs: Dict[str, Optional[date]]) -> Dict[str, Optional[float]]:
    """% change per period: the close on `as_of` against the close on the period's reference session. A close missing
    on either session (a suspended share, a symbol not yet stored) is null."""
    as_of = refs.get("as_of")
    last = closes.get(as_of) if as_of else None
    out: Dict[str, Optional[float]] = {}
    for name in PERIOD_KEYS:
        ref = refs.get(name)
        out[name] = pct_change(last, closes.get(ref) if ref else None)
    return out


def breadth(values: Iterable[Optional[float]]) -> Dict[str, int]:
    up = down = flat = missing = 0
    for v in values:
        if v is None:
            missing += 1
        elif v > 0:
            up += 1
        elif v < 0:
            down += 1
        else:
            flat += 1
    return {"advances": up, "declines": down, "unchanged": flat, "counted": up + down + flat, "missing": missing}


def average(values: Iterable[Optional[float]]) -> Optional[float]:
    known = [v for v in values if v is not None]
    return round(sum(known) / len(known), 2) if known else None


def relative(change: Dict[str, Optional[float]], bench: Dict[str, Optional[float]]) -> Dict[str, Optional[float]]:
    """The move against the benchmark, in percentage points (null when either is not known)."""
    out: Dict[str, Optional[float]] = {}
    for p in PERIOD_KEYS:
        c, b = change.get(p), bench.get(p)
        out[p] = round(c - b, 2) if c is not None and b is not None else None
    return out


def extremes(members: Sequence[Dict[str, Any]], period: str, n: int = TOP_N) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(strongest, weakest) stocks over `period`: the n largest moves, then the n smallest of the rest (a short list never
    names one stock in both). Ties go by symbol. Only stocks with a known move."""
    ranked = sorted((m for m in members if m["change"].get(period) is not None), key=lambda m: (-m["change"][period], m["symbol"]))
    pick = lambda m: {"symbol": m["symbol"], "exchange": m["exchange"], "name": m["name"], "change": m["change"][period]}  # noqa: E731
    strongest = ranked[:n]
    taken = {m["symbol"] for m in strongest}
    weakest = [m for m in reversed(ranked) if m["symbol"] not in taken][:n]
    return [pick(m) for m in strongest], [pick(m) for m in weakest]


def group(*, kind: str, name: str, list_key: Optional[str], change: Dict[str, Optional[float]], members: Sequence[Dict[str, Any]],
          bench: Dict[str, Optional[float]], symbol: Optional[str] = None, exchange: Optional[str] = None,
          family: Optional[str] = None, close: Optional[float] = None) -> Dict[str, Any]:
    """One index or sector as the page shows it."""
    strongest: Dict[str, List[Dict[str, Any]]] = {}
    weakest: Dict[str, List[Dict[str, Any]]] = {}
    for p in PERIOD_KEYS:
        strongest[p], weakest[p] = extremes(members, p)
    return {
        "kind": kind, "name": name, "list_key": list_key, "symbol": symbol, "exchange": exchange,
        "family": family, "family_label": FAMILY_LABELS.get(family or "", None), "close": close,
        "method": SECTOR_METHOD if kind == "sector" else INDEX_METHOD,
        "count": len(members), "change": change, "vs_benchmark": relative(change, bench),
        "breadth": {p: breadth(m["change"].get(p) for m in members) for p in PERIOD_KEYS} if members else None,
        "strongest": strongest, "weakest": weakest,
        "known": any(v is not None for v in change.values()),
    }


# --- reads -----------------------------------------------------------------------------------------------------------

async def _sessions(session: AsyncSession, today: date) -> Tuple[List[date], Dict[date, Set[datetime]]]:
    """The benchmark's stored sessions up to today (ascending) and each session's bar end time(s)."""
    lo = datetime.combine(today - timedelta(days=CALENDAR_DAYS), datetime.min.time(), tzinfo=IST).astimezone(timezone.utc)
    hi = datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=IST).astimezone(timezone.utc)
    rows = await session.execute(select(MdCandleRecord.ts).where(
        MdCandleRecord.instrument_key == index_key(BENCHMARK), MdCandleRecord.timeframe == TIMEFRAME,
        MdCandleRecord.ts > lo, MdCandleRecord.ts <= hi).distinct())
    stamps: Dict[date, Set[datetime]] = {}
    for (ts,) in rows.all():
        stamps.setdefault(_day(ts), set()).add(ts)
    return sorted(stamps), stamps


async def _closes(session: AsyncSession, keys: Iterable[str], stamps: Sequence[datetime]) -> Dict[str, Dict[date, float]]:
    """{key: {session day: close}} for the given bar times; per key and day the highest version wins."""
    best: Dict[Tuple[str, date], Tuple[float, int]] = {}
    ordered = sorted(set(keys))
    if not ordered or not stamps:
        return {}
    for i in range(0, len(ordered), CHUNK):
        chunk = ordered[i:i + CHUNK]
        rows = await session.execute(select(MdCandleRecord.instrument_key, MdCandleRecord.ts, MdCandleRecord.close, MdCandleRecord.version).where(
            MdCandleRecord.instrument_key.in_(chunk), MdCandleRecord.timeframe == TIMEFRAME, MdCandleRecord.ts.in_(list(stamps))))
        for key, ts, close, version in rows.all():
            spot = (key, _day(ts))
            v = int(version or 0)
            if spot not in best or v > best[spot][1]:
                best[spot] = (float(close), v)
    out: Dict[str, Dict[date, float]] = {}
    for (key, day), (close, _) in best.items():
        out.setdefault(key, {})[day] = close
    return out


def stock_key(symbol: str) -> str:
    return f"NSE:{symbol.strip().upper()}"


def _items(row: Optional[SystemWatchlistRecord]) -> List[Dict[str, str]]:
    return system.as_dict(row)["items"] if row is not None else []


def _catalogue() -> Tuple[Dict[str, dict], Dict[str, dict]]:
    """The index catalogue by name and by its constituents' list key (`index-<code>`)."""
    entries = catalogue()
    return ({e["name"].upper(): e for e in entries}, {f"index-{e['index_code'].lower()}": e for e in entries})


def _member(item: Dict[str, str], closes: Dict[str, Dict[date, float]], refs: Dict[str, Optional[date]],
            bench: Dict[str, Optional[float]]) -> Dict[str, Any]:
    series = closes.get(stock_key(item["symbol"]), {})
    as_of = refs.get("as_of")
    change = changes(series, refs)
    return {"symbol": item["symbol"], "exchange": item["exchange"], "name": item.get("name") or "",
            "close": series.get(as_of) if as_of else None, "change": change, "vs_benchmark": relative(change, bench)}


def _periods(refs: Dict[str, Optional[date]]) -> Dict[str, Optional[str]]:
    return {p: (d.isoformat() if (d := refs.get(p)) else None) for p in PERIOD_KEYS}


async def _frame(session: AsyncSession, now: datetime) -> Tuple[Optional[Dict[str, Optional[date]]], List[datetime], bool]:
    """(refs, the bar times to read, stale) - refs None when fewer than two sessions are stored."""
    from app.market_data.calendar import load_holidays
    today = now.astimezone(IST).date()
    days, stamps = await _sessions(session, today)
    if len(days) < 2:
        return None, [], False
    refs = session_refs(days)
    wanted = {d for d in refs.values() if d is not None}
    try:
        holidays = sorted(await load_holidays(session, "NSE"))
    except Exception:  # noqa: BLE001 - the staleness note is extra; the answer stands without the holiday list
        holidays = []
    as_of = refs["as_of"]
    stale = as_of is not None and freshness(as_of, today, holidays) == "stale"
    return refs, [ts for d in wanted for ts in stamps.get(d, ())], stale


_cache: Dict[str, Any] = {"at": None, "value": None}


def reset_cache() -> None:
    _cache["at"], _cache["value"] = None, None


async def overview(session: AsyncSession, *, now: Optional[datetime] = None, use_cache: bool = True) -> Dict[str, Any]:
    """Every index and sector with its moves, breadth, strongest / weakest stocks and the move against the NIFTY 50."""
    if use_cache and _cache["at"] is not None and time.monotonic() - _cache["at"] < CACHE_SECONDS:
        return dict(_cache["value"])
    now = now or datetime.now(timezone.utc)
    value = await _overview(session, now)
    if use_cache:
        _cache["at"], _cache["value"] = time.monotonic(), value
    return dict(value)


async def _overview(session: AsyncSession, now: datetime) -> Dict[str, Any]:
    base: Dict[str, Any] = {"state": "ok", "message": None, "as_of": None, "stale": False, "periods": {p: None for p in PERIOD_KEYS},
                            "benchmark": {"name": BENCHMARK, "change": {p: None for p in PERIOD_KEYS}},
                            "families": [], "indices": [], "sectors": [], "source": SOURCE, "note": NOTE,
                            "generated_at": now.isoformat()}
    rows = await system.list_all(session)
    by_key = {r.key: r for r in rows}
    if not rows:
        return {**base, "state": "no_lists", "message": NO_LISTS}
    refs, stamps, stale = await _frame(session, now)
    if refs is None:
        return {**base, "state": "no_data", "message": NO_DATA}

    by_name, _ = _catalogue()
    index_items = _items(by_key.get("indices"))
    member_rows = [r for r in rows if r.group in ("INDEX_STOCKS", "SECTORS")]
    keys = {index_key(BENCHMARK)} | {index_key(i["name"]) for i in index_items if i["exchange"] == "NSE"}
    keys |= {stock_key(i["symbol"]) for r in member_rows for i in _items(r) if i["exchange"] == "NSE"}
    closes = await _closes(session, keys, stamps)
    bench = changes(closes.get(index_key(BENCHMARK), {}), refs)
    as_of = refs["as_of"]
    members_of: Dict[str, List[Dict[str, Any]]] = {r.key: [_member(i, closes, refs, bench) for i in _items(r)] for r in member_rows}

    indices: List[Tuple[int, int, Dict[str, Any]]] = []
    for order, item in enumerate(index_items):
        entry = by_name.get(item["name"].upper())
        family = "BSE" if item["exchange"] == "BSE" else (entry["family"] if entry else "BROAD")
        list_key = f"index-{entry['index_code'].lower()}" if entry else None
        if list_key not in by_key:
            list_key = None
        series = closes.get(index_key(item["name"]), {}) if item["exchange"] == "NSE" else {}
        indices.append((FAMILY_ORDER.index(family) if family in FAMILY_ORDER else len(FAMILY_ORDER), order, group(
            kind="index", name=item["name"], list_key=list_key, symbol=item["symbol"], exchange=item["exchange"], family=family,
            change=changes(series, refs), close=series.get(as_of) if as_of else None,
            members=members_of.get(list_key, []) if list_key else [], bench=bench)))
    index_groups = [g for _, _, g in sorted(indices, key=lambda t: (t[0], t[1]))]
    sectors = [group(kind="sector", name=r.name, list_key=r.key, change={p: average(m["change"][p] for m in members_of[r.key]) for p in PERIOD_KEYS},
                     members=members_of[r.key], bench=bench) for r in rows if r.group == "SECTORS"]
    families = [{"key": f, "label": FAMILY_LABELS[f]} for f in FAMILY_ORDER if any(g["family"] == f for g in index_groups)]
    known = any(g["known"] for g in index_groups + sectors)
    return {**base, "state": "ok" if known else "no_data", "message": None if known else NO_DATA,
            "as_of": as_of.isoformat() if as_of else None, "stale": stale, "periods": _periods(refs),
            "benchmark": {"name": BENCHMARK, "change": bench}, "families": families, "indices": index_groups, "sectors": sectors}


async def list_detail(session: AsyncSession, key: str, *, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """One index's or sector's stocks, each with its moves and the move against the NIFTY 50 (largest day move first).
    None when `key` is not an index-stocks or sector list."""
    row = await system.get(session, key)
    if row is None or row.group not in ("INDEX_STOCKS", "SECTORS"):
        return None
    now = now or datetime.now(timezone.utc)
    _, by_list = _catalogue()
    entry = by_list.get(row.key) if row.group == "INDEX_STOCKS" else None
    name = entry["name"] if entry else row.name
    items = _items(row)
    base: Dict[str, Any] = {"state": "ok", "message": None, "list_key": row.key, "kind": "index" if entry else "sector", "name": name,
                            "symbol": (entry.get("candle_symbol") or entry["name"]) if entry else None, "as_of": None, "stale": False,
                            "periods": {p: None for p in PERIOD_KEYS}, "benchmark": {"name": BENCHMARK, "change": {p: None for p in PERIOD_KEYS}},
                            "summary": None, "members": [], "source": SOURCE, "note": NOTE, "generated_at": now.isoformat()}
    refs, stamps, stale = await _frame(session, now)
    if refs is None:
        unknown: Dict[str, Optional[float]] = {p: None for p in PERIOD_KEYS}
        members: List[Dict[str, Any]] = [{"symbol": i["symbol"], "exchange": i["exchange"], "name": i.get("name") or "", "close": None,
                    "change": dict(unknown), "vs_benchmark": dict(unknown)} for i in items]
        return {**base, "state": "no_data", "message": NO_DATA, "members": members}
    keys = {index_key(BENCHMARK)} | {stock_key(i["symbol"]) for i in items if i["exchange"] == "NSE"}
    if entry:
        keys.add(index_key(entry["name"]))
    closes = await _closes(session, keys, stamps)
    bench = changes(closes.get(index_key(BENCHMARK), {}), refs)
    as_of = refs["as_of"]
    members = [_member(i, closes, refs, bench) for i in items]
    summary: Dict[str, Any]
    if entry:
        series = closes.get(index_key(entry["name"]), {})
        summary = group(kind="index", name=name, list_key=row.key, symbol=base["symbol"], exchange="NSE", family=entry["family"],
                        change=changes(series, refs), close=series.get(as_of) if as_of else None, members=members, bench=bench)
    else:
        summary = group(kind="sector", name=name, list_key=row.key, change={p: average(m["change"][p] for m in members) for p in PERIOD_KEYS},
                        members=members, bench=bench)
    members.sort(key=lambda m: (m["change"]["day"] is None, -(m["change"]["day"] or 0.0), m["symbol"]))
    known = any(m["change"][p] is not None for m in members for p in PERIOD_KEYS) or summary["known"]
    return {**base, "state": "ok" if known else "no_data", "message": None if known else NO_DATA,
            "as_of": as_of.isoformat() if as_of else None, "stale": stale, "periods": _periods(refs),
            "benchmark": {"name": BENCHMARK, "change": bench}, "summary": summary, "members": members}


__all__ = ["FLAG", "PAGE_FLAG", "PERIODS", "session_refs", "pct_change", "changes", "breadth", "average", "relative", "extremes",
           "group", "overview", "list_detail", "reset_cache", "stock_key"]
