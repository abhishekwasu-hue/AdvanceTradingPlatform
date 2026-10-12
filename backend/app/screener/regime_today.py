"""U9 TX3 (docs/specs/ATP_SCREENER_TAXONOMY_ADDENDUM.md §3 "regime-aware library"): today's market regime on the index,
and whether each screen of the library fits it.

Two axes, read from the shared history (the lake's NSE bhavcopy daily bars - shared reference data, no organisation's
broker session), on CLOSED sessions only: a bar is used when its session has ended and it was ingested by now
(`asof.candles_as_of(..., as_of=now)`), so the answer at any time is what was known then (no look-ahead).

* **Trend axis** - the Dow trend of NIFTY 50 on daily bars (confirmed swings, the breakout engine's `Bars.dow` at
  degree 1), confirmed by breadth (advancing / declining NSE cash stocks on the last session): UP or DOWN with breadth
  agreeing -> "trend"; RANGE -> "range"; a trend that breadth does not confirm -> unclear (nothing is greyed on it).
* **Volatility axis** - India VIX against its own last year: at or above the 70th percentile of the last 250
  sessions -> "high_vol"; at or below the 30th -> "low_vol"; between -> normal (nothing greyed).

A screen declares the regimes it is designed for in the taxonomy (`regimes`, at most one per axis). It is greyed when an
axis it declares has a clear reading today that differs, and the reason says which and why. Missing data never greys
anything. Information only: greying is a hint, a greyed screen still opens and runs; nothing here ranks or recommends.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MdCandleRecord
from app.market_lake.asof import candles_as_of
from app.screener import taxonomy as tx

DAILY = "day"                                # the lake's daily timeframe (bhavcopy)
INDEX_NAME, VIX_NAME = "NIFTY 50", "INDIA VIX"
TREND_DEGREE = 1
HISTORY_DAYS = 520                           # calendar days read: about 360 sessions (VIX percentile + swings)
VIX_SESSIONS = 250
VIX_HIGH_PCT, VIX_LOW_PCT = 70.0, 30.0
MIN_INDEX_BARS = 250                         # the breakout engine's warm-up for degree-1 swings (DOW_MIN_BARS[1])
NOTE = ("Today's regime is read from closed sessions of NIFTY 50, India VIX and NSE breadth in the shared history. A greyed "
        "screen was designed for another regime; it still opens and runs. This is a hint, not advice.")


def index_key(name: str) -> str:
    from app.universe.fo_eod import code_for
    return f"NSE_INDEX:{code_for(name)}"


@dataclass
class Axis:
    value: Optional[str]                    # trend / range / high_vol / low_vol, or None (unclear or unknown)
    reasons: List[str] = field(default_factory=list)
    numbers: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {"value": self.value, "reasons": list(self.reasons), "numbers": dict(self.numbers)}


@dataclass
class Today:
    as_of: str
    session: Optional[str]                   # the last closed session read (IST date)
    trend: Axis
    volatility: Axis

    def values(self) -> Dict[str, Optional[str]]:
        return {"trend": self.trend.value, "volatility": self.volatility.value}

    def as_dict(self) -> Dict[str, Any]:
        return {"as_of": self.as_of, "session": self.session, "index": INDEX_NAME, "trend": self.trend.as_dict(),
                "volatility": self.volatility.as_dict(), "note": NOTE}


def _frame(rows: Iterable[MdCandleRecord]) -> pd.DataFrame:
    data = [(r.ts, float(r.open), float(r.high), float(r.low), float(r.close), float(r.volume or 0)) for r in rows]
    frame = pd.DataFrame(data, columns=["ts", "open", "high", "low", "close", "volume"])
    if frame.empty:
        return frame
    stamps = pd.to_datetime(frame.pop("ts"), utc=True)
    frame.index = pd.DatetimeIndex(stamps)
    return frame.sort_index()


def trend_of(frame: pd.DataFrame) -> Tuple[Optional[str], str]:
    """(UP / DOWN / RANGE or None, why) - the Dow trend of the last closed bar from confirmed swings."""
    if len(frame) < MIN_INDEX_BARS:
        return None, f"{INDEX_NAME}: {len(frame)} daily bars in the shared history, {MIN_INDEX_BARS} needed"
    from app.screener.breakouts.params import settings
    from app.screener.breakouts.screens import Bars
    # The breakout engine's confirmed swings, labelled here with SYMMETRIC ties: an equal high or low is neither higher
    # nor lower, so a flat market reads RANGE, not DOWN (Bars.dow reads ties as lower - OPEN_QUESTIONS TX2-3).
    pivots = [p for p in Bars(frame, settings()).pivots(TREND_DEGREE) if int(p.confirmed_idx) <= len(frame) - 1]
    last: Dict[str, Optional[float]] = {"H": None, "L": None}
    label: Dict[str, Optional[str]] = {"H": None, "L": None}
    for pv in pivots:
        prev = last[pv.kind]
        if prev is not None:
            label[pv.kind] = "higher" if pv.price > prev else "lower" if pv.price < prev else "equal"
        last[pv.kind] = float(pv.price)
    if not (label["H"] and label["L"]):
        return None, f"{INDEX_NAME}: no Dow trend yet (too few confirmed swings at degree {TREND_DEGREE})"
    trend = "UP" if label["H"] == label["L"] == "higher" else "DOWN" if label["H"] == label["L"] == "lower" else "RANGE"
    return trend, (f"{INDEX_NAME} Dow trend on daily bars (degree {TREND_DEGREE}): {trend} (last swing high {label['H']}, "
                   f"last swing low {label['L']})")


def trend_axis(dow: Optional[str], dow_why: str, breadth: Optional[Tuple[int, int]]) -> Axis:
    numbers: Dict[str, Any] = {"dow": dow}
    reasons = [dow_why]
    if breadth is not None:
        up, down = breadth
        numbers.update(advancers=up, decliners=down)
        reasons.append(f"breadth: {up} advancing, {down} declining NSE stocks on the last session")
    else:
        reasons.append("breadth: not available in the shared history")
    if dow is None:
        return Axis(None, reasons, numbers)
    if dow == "RANGE":
        return Axis("range", reasons, numbers)
    if breadth is None:
        return Axis(None, reasons + ["a trend without breadth to confirm it is left unclear"], numbers)
    up, down = breadth
    agrees = up > down if dow == "UP" else down > up
    if not agrees:
        return Axis(None, reasons + ["breadth does not confirm the index trend: unclear"], numbers)
    return Axis("trend", reasons, numbers)


def vol_axis(vix: pd.Series) -> Axis:
    closes = vix.dropna().iloc[-VIX_SESSIONS:]
    if len(closes) < 20:
        return Axis(None, [f"{VIX_NAME}: {len(closes)} daily closes in the shared history, 20 needed"])
    last = float(closes.iloc[-1])
    pct = float((closes <= last).mean() * 100.0)
    numbers = {"vix": round(last, 2), "percentile": round(pct, 1), "sessions": len(closes)}
    why = f"{VIX_NAME} {last:.2f}: percentile {pct:.0f} of the last {len(closes)} sessions"
    if pct >= VIX_HIGH_PCT:
        return Axis("high_vol", [why], numbers)
    if pct <= VIX_LOW_PCT:
        return Axis("low_vol", [why], numbers)
    return Axis(None, [why + " (normal: nothing greyed on volatility)"], numbers)


async def _breadth(session: AsyncSession, now: datetime) -> Optional[Tuple[int, int]]:
    """Advancers and decliners of NSE cash stocks: the last closed session against the one before (shared history)."""
    visible = [MdCandleRecord.instrument_key.like("NSE:%"), MdCandleRecord.timeframe == DAILY, MdCandleRecord.ts <= now,
               MdCandleRecord.ingested_at <= now]
    last = await session.scalar(select(func.max(MdCandleRecord.ts)).where(*visible))
    if last is None:
        return None
    prev = await session.scalar(select(func.max(MdCandleRecord.ts)).where(*visible, MdCandleRecord.ts < last))
    if prev is None:
        return None
    closes: Dict[Tuple[str, datetime], Tuple[int, float]] = {}
    for r in await session.scalars(select(MdCandleRecord).where(*visible, MdCandleRecord.ts.in_([last, prev]))):
        key = (r.instrument_key, r.ts)
        if key not in closes or r.version > closes[key][0]:
            closes[key] = (r.version, float(r.close))
    up = down = 0
    for (symbol, ts), (_, close) in closes.items():
        if ts != last or (symbol, prev) not in closes:
            continue
        before = closes[(symbol, prev)][1]
        up += close > before
        down += close < before
    return (up, down) if up + down else None


async def today(session: AsyncSession, now: Optional[datetime] = None) -> Today:
    """The regime as of `now` from closed sessions only."""
    now = now or datetime.now(timezone.utc)
    start = now - timedelta(days=HISTORY_DAYS)
    index = _frame(await candles_as_of(session, index_key(INDEX_NAME), DAILY, start, now, as_of=now))
    vix = _frame(await candles_as_of(session, index_key(VIX_NAME), DAILY, start, now, as_of=now))
    dow, why = trend_of(index)
    trend = trend_axis(dow, why, await _breadth(session, now))
    volatility = vol_axis(vix["close"] if not vix.empty else pd.Series(dtype=float))
    last = index.index[-1] if not index.empty else None
    from app.market_data.calendar import IST
    return Today(now.isoformat(), last.tz_convert(IST).date().isoformat() if last is not None else None, trend, volatility)


def fit(screen: tx.Screen, today_: Today, taxonomy: tx.Taxonomy) -> Dict[str, Any]:
    """{fits: True / False / None, reason}: False only when a declared axis has a clear reading today that differs."""
    if not screen.regimes:
        return {"fits": True, "reason": "Designed for any regime."}
    vocab = {r["id"]: r for r in taxonomy.regimes}
    values = today_.values()
    misses: List[str] = []
    unknown: List[str] = []
    for regime in screen.regimes:
        axis = vocab[regime]["axis"]
        now_value = values.get(axis)
        if now_value is None:
            unknown.append(axis)
        elif now_value != regime:
            misses.append(f"designed for a {vocab[regime]['label'].lower()}; today reads {vocab[now_value]['label'].lower()}")
    if misses:
        return {"fits": False, "reason": "Not today's regime: " + "; ".join(misses) + "."}
    if unknown:
        return {"fits": None, "reason": f"Today's {' and '.join(unknown)} reading is unclear, so nothing is greyed."}
    return {"fits": True, "reason": "Fits today's regime."}


def fits(taxonomy: tx.Taxonomy, today_: Today, hidden_sources: Iterable[str] = ()) -> Dict[str, Dict[str, Any]]:
    return {s.id: fit(s, today_, taxonomy) for s in tx.visible(taxonomy, hidden_sources)}


__all__ = ["Axis", "Today", "today", "fit", "fits", "trend_of", "trend_axis", "vol_axis", "index_key", "NOTE", "DAILY"]
