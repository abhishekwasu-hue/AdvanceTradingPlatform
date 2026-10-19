"""1-minute bar model, validation and tick aggregation.

Completed-bar rule: a bar stamped T (bar OPEN time, exchange timezone) is complete only when now >= T + 1 minute +
grace. Forming bars are never stored or broadcast. Invalid bars (non-finite prices, OHLC violations, negative volume,
outside the session) are rejected with a reason; they are never repaired or fabricated.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import pandas as pd

TZ = "Asia/Kolkata"
ONE_MIN = pd.Timedelta(minutes=1)


@dataclass(frozen=True)
class Bar:
    symbol: str          # NSE symbol, e.g. RELIANCE
    ts: str              # bar open time, ISO 8601 with +05:30
    open: float
    high: float
    low: float
    close: float
    volume: float | None
    source: str          # e.g. "kite", "simulated"

    def to_dict(self) -> dict:
        return asdict(self)


def to_ts(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    t = t.tz_localize(TZ) if t.tzinfo is None else t.tz_convert(TZ)
    return t.floor("min")


def validate_bar(b: Bar, session_open: str = "09:15", session_close: str = "15:30") -> str | None:
    """Return a rejection reason, or None if the bar is valid."""
    vals = (b.open, b.high, b.low, b.close)
    if any(v is None or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in vals):
        return "non-finite or non-positive price"
    if b.high < max(b.open, b.close) - 1e-9 or b.low > min(b.open, b.close) + 1e-9 or b.high < b.low:
        return "OHLC inconsistent"
    if b.volume is not None and (not math.isfinite(b.volume) or b.volume < 0):
        return "negative or non-finite volume"
    t = to_ts(b.ts)
    hm = t.strftime("%H:%M")
    if not (session_open <= hm < session_close):
        return f"outside session ({hm})"
    return None


def is_complete(bar_ts, now, grace_s: float = 2.0) -> bool:
    n = pd.Timestamp(now)
    n = n.tz_localize("UTC").tz_convert(TZ) if n.tzinfo is None else n.tz_convert(TZ)
    return n >= to_ts(bar_ts) + ONE_MIN + pd.Timedelta(seconds=grace_s)


class MinuteAggregator:
    """Builds 1-minute bars from ticks (price, cumulative or incremental volume, exchange timestamp).

    * Ticks older than the current bar's minute are counted as late and ignored (no rewriting of emitted bars).
    * A bar is emitted when a tick for a later minute arrives or `flush(now)` sees the minute has closed.
    * Minutes without any tick produce NO bar (no forward-filling).
    """

    def __init__(self, symbol: str, source: str, cumulative_volume: bool = True):
        self.symbol, self.source, self.cum = symbol, source, cumulative_volume
        self.cur: dict | None = None
        self.last_cum: float | None = None
        self.late = 0

    def _emit(self) -> Bar | None:
        c = self.cur
        self.cur = None
        if c is None:
            return None
        return Bar(self.symbol, c["t"].isoformat(), c["o"], c["h"], c["l"], c["c"], c["v"], self.source)

    def add_tick(self, ts, price: float, volume: float | None = None) -> Bar | None:
        t = to_ts(ts)
        if self.cur is not None and t < self.cur["t"]:
            self.late += 1
            return None
        dv = None
        if volume is not None:
            if self.cum:
                dv = None if self.last_cum is None else max(0.0, volume - self.last_cum)
                self.last_cum = volume
            else:
                dv = volume
        out = None
        if self.cur is not None and t > self.cur["t"]:
            out = self._emit()
        if self.cur is None:
            self.cur = {"t": t, "o": price, "h": price, "l": price, "c": price, "v": dv}
        else:
            c = self.cur
            c["h"], c["l"], c["c"] = max(c["h"], price), min(c["l"], price), price
            if dv is not None:
                c["v"] = (c["v"] or 0.0) + dv
        return out

    def flush(self, now, grace_s: float = 2.0) -> Bar | None:
        if self.cur is not None and is_complete(self.cur["t"], now, grace_s):
            return self._emit()
        return None


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Aggregate 1-minute bars to 5m/15m/30m/1H aligned to the 09:15 session open. Incomplete trailing buckets are
    dropped by the caller via completed-candle validation."""
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    out = df.resample(rule, origin="start_day", offset="15min", label="left", closed="left").agg(agg)
    return out.dropna(subset=["Close"])
