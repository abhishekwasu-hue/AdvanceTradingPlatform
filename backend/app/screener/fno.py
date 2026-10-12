"""S5-C (ATP_SCREENER_SPEC §2.3, Category C - F&O, India first), first slice: open-interest screens and the futures
figures the shared NSE F&O bhavcopy carries. Behind the operator flag `screener_fno` (off by default).

Two kinds of ScreenQL function:

* On the screen's own bars (any frame with an `oi` column - a futures contract's bars from the user's broker):
  `OIChange(n)` - the % change of open interest over n bars; `Buildup(n, price_pct, oi_pct)` - the four buildups from
  the close and the OI n bars back (price up + OI up = LONG_BUILDUP, price down + OI up = SHORT_BUILDUP, price up + OI
  down = SHORT_COVERING, price down + OI down = LONG_UNWINDING; "" when either move is within its threshold, missing
  without OI data).
* From the SHARED lake's end-of-day F&O data (`needs` "fno_eod": NSE's F&O bhavcopy futures rows - `NFO:<SYM>:FUT:
  <expiry>`, timeframe `day` - the cash / index close, and NSE's own F&O ban list `fo_ban_history`), for a cash
  symbol's screen: `FutOIChange(n)` (all expiries' OI), `FutBuildup(n, price_pct, oi_pct)` (the near-month contract's
  close against ITS OWN close n trade dates back, and the OI of all expiries), `RolloverPct()` ((next + far OI) / all
  OI), `CostOfCarry()` (near-month premium over the spot, annualised over the calendar days to expiry) and `InFnoBan()`.
  The lake holds exchange data, the same for everyone; no user's broker data and no tenant store is read.

As-of rules (no look-ahead): a trade date's figures belong to that session's close (the lake labels a daily bar by its
END, 15:30 IST) and are published after it; a bar sees the newest trade date whose close is strictly before the bar's own
close (every intraday bar of day d, the 15:25 one too, sees d-1; the daily bar of d sees d). The ban list for trade date
d is known before d's open (NSE publishes it the evening before), so every bar of d reads it. The loader reads only rows
ingested by the run time.
Places no orders; informational screens only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from app.screener import nodes
from app.screener.registry import FNO_FLAG as FLAG

BUILDUPS: Tuple[str, ...] = ("LONG_BUILDUP", "SHORT_BUILDUP", "SHORT_COVERING", "LONG_UNWINDING")
EOD_FUNCTIONS: Tuple[str, ...] = ("FutOIChange", "FutBuildup", "RolloverPct", "CostOfCarry", "InFnoBan")
LAKE_TIMEFRAMES: Tuple[str, ...] = ("day", "1d")
CLOSE_IST = (15, 30)                                   # NSE's regular close: a trade date's figures belong to it
IST_OFFSET = timedelta(hours=5, minutes=30)
# Index futures underlyings -> the index whose close is the spot (U1 index catalogue names; the code via fo_eod.code_for)
INDEX_SPOT: Dict[str, str] = {"NIFTY": "NIFTY 50", "BANKNIFTY": "NIFTY BANK", "FINNIFTY": "NIFTY FINANCIAL SERVICES",
                              "MIDCPNIFTY": "NIFTY MIDCAP SELECT", "NIFTYNXT50": "NIFTY NEXT 50"}


# ------------------------------------------------------------------------------------------------------------- the gate
def flagged_calls(ast: Any) -> List[str]:
    """F&O functions used in a screen (each once, in order)."""
    from app.screener.registry import FUNCTIONS
    out: List[str] = []
    if ast is None:
        return out
    for node in nodes.walk(ast):
        spec = FUNCTIONS.get(node.name) if isinstance(node, nodes.Call) else None
        if spec is not None and spec.flag == FLAG and spec.name not in out:
            out.append(spec.name)
    return out


def eod_calls(ast: Any) -> List[str]:
    return [n for n in flagged_calls(ast) if n in EOD_FUNCTIONS]


def refusal(names: List[str]) -> str:
    return f"{', '.join(n + '()' for n in names)}: the F&O screens are not enabled for this organisation (flag {FLAG})"


async def enabled(session: AsyncSession, tenant_id: Any) -> bool:
    from app.platform.controls import flag_enabled
    return await flag_enabled(session, FLAG, tenant_id)


async def blocked_calls(session: AsyncSession, tenant_id: Any, ast: Any) -> List[str]:
    names = flagged_calls(ast)
    if not names or await enabled(session, tenant_id):
        return []
    return names


# ------------------------------------------------------------------------------------------- bar functions (own OI)
def _pct_change(x: np.ndarray, n: int) -> np.ndarray:
    prev = np.full(len(x), np.nan)
    if 0 < n < len(x):
        prev[n:] = x[:len(x) - n]
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(np.isfinite(prev) & (prev != 0), (x - prev) / np.abs(prev) * 100.0, np.nan)


def classify(price_pct: np.ndarray, oi_pct: np.ndarray, price_min: float, oi_min: float) -> np.ndarray:
    """The buildup per bar: None where either change is unknown, "" where either move is within its threshold."""
    out = np.full(len(price_pct), None, dtype=object)
    known = np.isfinite(price_pct) & np.isfinite(oi_pct)
    up, down = price_pct > price_min, price_pct < -price_min
    oi_up, oi_down = oi_pct > oi_min, oi_pct < -oi_min
    out[known] = ""
    out[known & up & oi_up] = "LONG_BUILDUP"
    out[known & down & oi_up] = "SHORT_BUILDUP"
    out[known & up & oi_down] = "SHORT_COVERING"
    out[known & down & oi_down] = "LONG_UNWINDING"
    return out


def _oi(f: pd.DataFrame) -> np.ndarray:
    """The bars' open interest. A frame with no OI column is refused with the reason, never read as "no buildup": the
    broker bars the screener loads today carry no OI (OPEN_QUESTIONS S5C-2) - the EOD functions read NSE's own figures."""
    from app.screener.runtime import ScreenRuntimeError
    if "oi" not in f.columns:
        raise ScreenRuntimeError("these bars carry no open interest (OIChange / Buildup read the screen's own bars: a futures or options"
                                 " contract with OI); FutOIChange / FutBuildup read NSE's end-of-day F&O figures")
    return f["oi"].to_numpy(float)


def oi_change(f: pd.DataFrame, n: int) -> pd.Series:
    return pd.Series(_pct_change(_oi(f), int(n)), index=f.index, dtype=float)


def buildup(f: pd.DataFrame, n: int, price_min: float, oi_min: float) -> pd.Series:
    out = classify(_pct_change(f["close"].to_numpy(float), int(n)), _pct_change(_oi(f), int(n)), float(price_min), float(oi_min))
    return pd.Series(out, index=f.index, dtype=object)


# --------------------------------------------------------------------------------------------------- the EOD timeline
@dataclass
class DayRow:
    """One trade date of an underlying: its futures contracts (key, expiry, close, OI), the spot close, the ban flag."""
    trade_date: date
    contracts: List[Tuple[str, date, float, Optional[float]]]
    spot: Optional[float] = None


@dataclass
class FnoTimeline:
    """An underlying's F&O figures per trade date, in date order; `close_ns` is each date's session close (UTC ns)."""
    symbol: str
    close_ns: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype="int64"))
    total_oi: np.ndarray = field(default_factory=lambda: np.zeros(0))
    near_key: List[Optional[str]] = field(default_factory=list)
    closes: List[Dict[str, float]] = field(default_factory=list)            # per date: contract -> close
    rollover_pct: np.ndarray = field(default_factory=lambda: np.zeros(0))
    carry_pct: np.ndarray = field(default_factory=lambda: np.zeros(0))
    ban_days: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype="datetime64[D]"))   # trade dates in ban, sorted

    def near_change(self, n: int) -> np.ndarray:
        """% change of the near-month contract's close against its own close n trade dates back."""
        out = np.full(len(self.near_key), np.nan)
        for i, key in enumerate(self.near_key):
            if key is None or i - n < 0:
                continue
            before = self.closes[i - n].get(key)
            now = self.closes[i].get(key)
            if before and now is not None and np.isfinite(before) and np.isfinite(now):
                out[i] = (now - before) / abs(before) * 100.0
        return out


def _ist_ns(d: date, hm: Tuple[int, int]) -> int:
    t = datetime(d.year, d.month, d.day, hm[0], hm[1], tzinfo=timezone.utc) - IST_OFFSET
    return int(pd.Timestamp(t).value)


def build_timeline(symbol: str, days: Sequence[DayRow], ban_dates: Sequence[date] = ()) -> FnoTimeline:
    """The per-date figures: the near month = the earliest expiry on or after the trade date; all-expiry OI; rollover =
    (all - near OI) / all; carry = (near close / spot - 1) x 365 / calendar days to expiry (at least 1)."""
    rows = sorted(days, key=lambda d: d.trade_date)
    n = len(rows)
    tl = FnoTimeline(symbol, np.array([_ist_ns(d.trade_date, CLOSE_IST) for d in rows], dtype="int64"), np.full(n, np.nan), [None] * n,
                     [{} for _ in range(n)], np.full(n, np.nan), np.full(n, np.nan),
                     np.array(sorted(set(ban_dates)), dtype="datetime64[D]"))
    for i, d in enumerate(rows):
        live = sorted((c for c in d.contracts if c[1] >= d.trade_date), key=lambda c: c[1])
        tl.closes[i] = {c[0]: float(c[2]) for c in live}
        if not live:
            continue
        near = live[0]
        tl.near_key[i] = near[0]
        ois = [c[3] for c in live]
        if all(o is not None for o in ois):
            total = float(sum(ois))                               # type: ignore[arg-type]
            tl.total_oi[i] = total
            if total > 0:
                tl.rollover_pct[i] = (total - float(near[3])) / total * 100.0      # type: ignore[arg-type]
        if d.spot:
            days_left = max(1, (near[1] - d.trade_date).days)
            tl.carry_pct[i] = (near[2] / d.spot - 1.0) * 365.0 / days_left * 100.0
    return tl


def _bar_closes_ns(f: pd.DataFrame, tf: str) -> np.ndarray:
    from app.screener.zones import _closes_ns
    return _closes_ns(f.index, tf)


def _bar_days(f: pd.DataFrame, tf: str) -> Tuple[np.ndarray, np.ndarray]:
    """Each bar's first and last IST trade date (the same date for intraday and daily bars)."""
    idx = pd.DatetimeIndex(f.index)
    idx = idx.tz_localize("UTC") if idx.tz is None else idx
    first = idx.tz_convert("Asia/Kolkata").tz_localize(None).normalize().to_numpy().astype("datetime64[D]")
    if tf not in ("1w", "1M"):
        return first, first
    end = pd.DatetimeIndex(_bar_closes_ns(f, tf)).tz_localize("UTC").tz_convert("Asia/Kolkata").tz_localize(None) - pd.Timedelta(1)
    return first, end.normalize().to_numpy().astype("datetime64[D]")


def fno_series(data: Any, f: pd.DataFrame, tf: str, what: str, n: int = 1, price_min: float = 0.0, oi_min: float = 0.0) -> pd.Series:
    """One EOD F&O function on every bar of `f`, as of the bar's close."""
    from app.screener.runtime import ScreenRuntimeError
    tl: Optional[FnoTimeline] = getattr(data, "fno", None)
    obj = what in ("buildup",)
    if tl is None:
        raise ScreenRuntimeError(f"{data.symbol}: the F&O figures were not loaded for this run")
    if what != "ban" and not len(tl.close_ns):
        # the ban list stands on its own (NSE's list); the other figures need futures rows - say why, never "no match"
        raise ScreenRuntimeError(f"{data.symbol}: no futures rows in the shared F&O data for {tl.symbol} (not an F&O underlying,"
                                 " or NSE's F&O bhavcopy is not loaded for these dates)")
    if what == "ban":
        # the bar's own trade date(s) - the IST date it starts on (a weekly / monthly bar: every date up to its close) - is
        # in NSE's list; the list for date d is published the evening before, so it is known before any bar of d starts
        first, last = _bar_days(f, tf)
        hits = np.searchsorted(tl.ban_days, last, side="right") - np.searchsorted(tl.ban_days, first, side="left")
        return pd.Series(hits > 0, index=f.index, dtype=bool)
    closes = _bar_closes_ns(f, tf)
    if what == "oi_change":
        values: np.ndarray = _pct_change(tl.total_oi, int(n))
    elif what == "buildup":
        values = classify(tl.near_change(int(n)), _pct_change(tl.total_oi, int(n)), float(price_min), float(oi_min))
    elif what == "rollover":
        values = tl.rollover_pct
    elif what == "carry":
        values = tl.carry_pct
    else:
        raise ScreenRuntimeError(f"unknown F&O measure {what!r}")
    # strictly before the bar's close: even the last intraday bar of d (closing at 15:30) sees d-1 - NSE publishes the
    # day's settlement figures after the close; the daily bar of d (closing after 15:30) sees d
    k = np.searchsorted(tl.close_ns, closes, side="left") - 1
    if obj:
        out = np.full(len(f), None, dtype=object)
        out[k >= 0] = values[k[k >= 0]]
        return pd.Series(out, index=f.index, dtype=object)
    vals = np.full(len(f), np.nan)
    vals[k >= 0] = values[k[k >= 0]]
    return pd.Series(vals, index=f.index, dtype=float)


# ------------------------------------------------------------------------------------------------------------ the loader
def underlying(symbol: str) -> str:
    s = str(symbol or "").strip().upper()
    return s.split(":", 1)[1] if ":" in s else s


def _spot_key(sym: str) -> str:
    if sym in INDEX_SPOT:
        from app.universe.fo_eod import code_for
        return f"NSE_INDEX:{code_for(INDEX_SPOT[sym])}"
    return f"NSE:{sym}"


def expiry_candidates(start: date, end: date, ahead_months: int = 4) -> List[date]:
    """Every date a monthly futures expiry can fall on between `start` and `ahead_months` after `end`: the 15th to the
    month's end (NSE's monthly expiry is the last Thursday - the last Tuesday from September 2025 - moved earlier for a
    holiday). The loader asks for these exact keys (`NFO:<SYM>:FUT:<date>`) - an equality look-up on the lake's primary
    key, right under any database collation (a key-range or LIKE prefix scan is not: OPEN_QUESTIONS S5C-9)."""
    out: List[date] = []
    y, m = start.year, start.month
    last = (end.year * 12 + end.month - 1) + ahead_months
    while y * 12 + m - 1 <= last:
        nxt = date(y + (m == 12), m % 12 + 1, 1)
        out += [date(y, m, d) for d in range(15, (nxt - timedelta(days=1)).day + 1) if date(y, m, d) >= start]
        y, m = nxt.year, nxt.month
    return out


def _ist_date(t: datetime) -> date:
    ts = pd.Timestamp(t)
    return ((ts.tz_convert("UTC") if ts.tzinfo else ts.tz_localize("UTC")) + IST_OFFSET).date()


async def load_timeline(session: AsyncSession, symbol: str, start: datetime, end: datetime) -> FnoTimeline:
    """The underlying's futures rows, spot closes and ban dates from the shared lake, ended and ingested by `end`."""
    from sqlalchemy import Float, cast, select

    from app.db.models import FoBanRecord, MdCandleRecord
    sym = underlying(symbol)
    keys = [f"NFO:{sym}:FUT:{d.isoformat()}" for d in expiry_candidates(_ist_date(start), _ist_date(end))]
    fut = await session.execute(select(MdCandleRecord.instrument_key, MdCandleRecord.ts, MdCandleRecord.version, cast(MdCandleRecord.close, Float),
                                       MdCandleRecord.oi).where(
        MdCandleRecord.instrument_key.in_(keys), MdCandleRecord.timeframe.in_(LAKE_TIMEFRAMES),
        MdCandleRecord.ts > start, MdCandleRecord.ts <= end, MdCandleRecord.ingested_at <= end).order_by(MdCandleRecord.ts, MdCandleRecord.version))
    spot = await session.execute(select(MdCandleRecord.ts, MdCandleRecord.version, cast(MdCandleRecord.close, Float)).where(
        MdCandleRecord.instrument_key == _spot_key(sym), MdCandleRecord.timeframe.in_(LAKE_TIMEFRAMES), MdCandleRecord.ts > start,
        MdCandleRecord.ts <= end, MdCandleRecord.ingested_at <= end).order_by(MdCandleRecord.ts, MdCandleRecord.version))
    bans = await session.scalars(select(FoBanRecord.trade_date).where(
        FoBanRecord.underlying == sym, FoBanRecord.trade_date > (start - timedelta(days=1)).date(),
        FoBanRecord.trade_date <= _ist_date(end), FoBanRecord.fetched_at <= end))
    by_day: Dict[date, Dict[str, Tuple[date, float, Optional[float]]]] = {}
    for key, ts, _version, close, oi in fut:                     # ascending versions: the last one wins
        try:
            expiry = date.fromisoformat(str(key).split(":")[3])
        except (IndexError, ValueError):
            continue
        day = (pd.Timestamp(ts).tz_convert("UTC") + IST_OFFSET).date() if pd.Timestamp(ts).tzinfo else (pd.Timestamp(ts) + IST_OFFSET).date()
        by_day.setdefault(day, {})[str(key)] = (expiry, float(close), None if oi is None else float(oi))
    spots: Dict[date, float] = {}
    for ts, _version, close in spot:
        t = pd.Timestamp(ts)
        t = t.tz_convert("UTC") if t.tzinfo else t.tz_localize("UTC")
        spots[(t + IST_OFFSET).date()] = float(close)
    days = [DayRow(d, [(k, v[0], v[1], v[2]) for k, v in contracts.items()], spots.get(d)) for d, contracts in by_day.items()]
    return build_timeline(sym, days, list(bans))


async def attach_fno(session: AsyncSession, universe: List[Any], end: datetime, base_tf: str) -> None:
    """Every symbol's F&O timeline from its frame's first bar (less a margin for the n-date changes) up to `end`."""
    margin = timedelta(days=60)
    for data in universe:
        frame = data.frames.get(base_tf)
        first = frame.index[0] if frame is not None and len(frame) else pd.Timestamp(end) - pd.Timedelta(days=30)
        start = pd.Timestamp(first)
        start = (start.tz_convert("UTC") if start.tzinfo else start.tz_localize("UTC")).to_pydatetime() - margin
        data.fno = await load_timeline(session, data.symbol, start, end)


# ---------------------------------------------------------------------------------------------- pre-built F&O screens
@dataclass(frozen=True)
class Preset:
    """A pre-built F&O screen: plain ScreenQL on the daily frame of the F&O stocks (filed in the taxonomy as `fno.<id>`)."""
    id: str
    name: str
    text: str
    direction: str
    note: str
    base_tf: str = "1d"
    universe: str = "fo_stocks"


# Thresholds are provisional defaults (OPEN_QUESTIONS S5C-3): a 0.5% futures move and a 2% OI move over one trade date.
PRESETS: Tuple[Preset, ...] = (
    Preset("long_buildup", "Futures long buildup", 'FutBuildup(1, 0.5, 2) == "LONG_BUILDUP"', "long",
           "near-month futures up and all-expiry OI up on the day"),
    Preset("short_buildup", "Futures short buildup", 'FutBuildup(1, 0.5, 2) == "SHORT_BUILDUP"', "short",
           "near-month futures down and all-expiry OI up on the day"),
    Preset("short_covering", "Futures short covering", 'FutBuildup(1, 0.5, 2) == "SHORT_COVERING"', "long",
           "near-month futures up and all-expiry OI down on the day"),
    Preset("long_unwinding", "Futures long unwinding", 'FutBuildup(1, 0.5, 2) == "LONG_UNWINDING"', "short",
           "near-month futures down and all-expiry OI down on the day"),
    Preset("oi_surge", "Futures OI surge", "FutOIChange(1) >= 10", "both", "all-expiry futures OI up 10% or more on the day"),
    Preset("futures_discount", "Near-month futures at a discount", "CostOfCarry() < 0", "both",
           "near-month futures close below the spot close (negative annualised carry)"),
    Preset("ban_list", "In the F&O ban period", "InFnoBan()", "both", "on NSE's F&O ban list for the trade date"),
)
PRESETS_BY_ID: Dict[str, Preset] = {p.id: p for p in PRESETS}


def presets_payload() -> Dict[str, Any]:
    """The pre-built F&O screens with their canonical ScreenQL (each validated), for the Screener's preset list."""
    from app.screener.parser import parse
    from app.screener.validator import validate
    rows = []
    for p in PRESETS:
        ast = parse(p.text)
        v = validate(ast, base_tf=p.base_tf)
        rows.append({"id": f"fno.{p.id}", "name": p.name, "text": nodes.to_text(ast) if v.ok else p.text, "base_tf": p.base_tf,
                     "direction": p.direction, "universe": p.universe, "note": p.note, "ok": v.ok})
    return {"flag": FLAG, "functions": list(EOD_FUNCTIONS) + ["OIChange", "Buildup"], "presets": rows,
            "note": "Informational screens on NSE's end-of-day F&O data; they place no orders and are not advice."}


def _register_taxonomy() -> None:
    """U9 TX1: every preset is filed in the screen taxonomy (source `fno`), hidden while `screener_fno` is off."""
    from app.screener import taxonomy
    taxonomy.register_source("fno", lambda: [f"fno.{p.id}" for p in PRESETS], flag=FLAG)


_register_taxonomy()


__all__ = ["expiry_candidates", "FLAG", "PRESETS", "PRESETS_BY_ID", "Preset", "presets_payload", "BUILDUPS", "EOD_FUNCTIONS", "flagged_calls", "eod_calls", "refusal", "enabled", "blocked_calls", "classify", "oi_change",
           "buildup", "DayRow", "FnoTimeline", "build_timeline", "fno_series", "underlying", "load_timeline", "attach_fno"]
