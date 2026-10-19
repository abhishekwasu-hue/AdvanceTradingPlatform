"""OHLCV validation, session/holiday-aware freshness, completed-candle handling. Never fabricates data."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

REQUIRED = ["Open", "High", "Low", "Close", "Volume"]
OK, STALE, INSUFFICIENT, INVALID, UNAVAILABLE = "OK", "STALE", "INSUFFICIENT", "INVALID", "UNAVAILABLE"

TF_DELTA = {
    "1W": pd.Timedelta(days=7), "1D": pd.Timedelta(days=1), "1H": pd.Timedelta(hours=1),
    "30m": pd.Timedelta(minutes=30), "15m": pd.Timedelta(minutes=15), "5m": pd.Timedelta(minutes=5),
}
TF_MINUTES = {"1H": 60, "30m": 30, "15m": 15, "5m": 5}


@dataclass
class QualityReport:
    ticker: str
    timeframe: str
    status: str
    bars: int = 0
    first: str = ""
    last: str = ""
    dropped_forming: bool = False
    duplicates: int = 0
    nan_rows: int = 0
    ohlc_violations: int = 0
    missing_bars: int = 0
    suspected_corporate_actions: int = 0
    zero_volume_pct: float = float("nan")
    data_kind: str = "delayed/historical"
    issues: list[str] = field(default_factory=list)

    def as_row(self) -> dict:
        d = self.__dict__.copy()
        d["issues"] = "; ".join(self.issues)
        return d


def _hm(s: str) -> tuple[int, int]:
    h, m = s.split(":")
    return int(h), int(m)


def trading_days_between(a: pd.Timestamp, b: pd.Timestamp, holidays: list[str]) -> int:
    """Number of NSE trading days strictly after date a up to and including date b."""
    if b.date() <= a.date():
        return 0
    return int(np.busday_count(np.datetime64(a.date()) + 1, np.datetime64(b.date()) + 1, holidays=holidays or []))


def is_trading_day(ts: pd.Timestamp, holidays: list[str]) -> bool:
    return bool(np.is_busday(np.datetime64(ts.date()), holidays=holidays or []))


def is_forming(last_ts: pd.Timestamp, tf: str, now: pd.Timestamp, market: dict) -> bool:
    """True if the bar starting at last_ts has not closed yet at `now` (same tz)."""
    hh, mm = _hm(market.get("session_close", "15:30"))
    if tf == "1W":
        week_end = (last_ts.normalize() + pd.Timedelta(days=4 - last_ts.weekday())) + pd.Timedelta(hours=hh, minutes=mm)
        return now < week_end
    if tf == "1D":
        if last_ts.date() < now.date():
            return False
        return (now.hour, now.minute) < (hh, mm)
    bar_end = last_ts + TF_DELTA[tf]
    close_ts = last_ts.normalize() + pd.Timedelta(hours=hh, minutes=mm)
    if last_ts < close_ts < bar_end:
        bar_end = close_ts
    return bar_end > now


def count_missing_bars(idx: pd.DatetimeIndex, tf: str, market: dict) -> int:
    """Approximate missing bars: daily = trading days absent; intraday = per-session shortfall (full sessions only)."""
    if len(idx) < 2 or tf == "1W":
        return 0
    hol = market.get("holidays", [])
    if tf == "1D":
        expected = trading_days_between(idx[0] - pd.Timedelta(days=1), idx[-1], hol)
        return max(0, expected - len(idx))
    oh, om = _hm(market.get("session_open", "09:15"))
    ch, cm = _hm(market.get("session_close", "15:30"))
    per_session = int(np.ceil(((ch * 60 + cm) - (oh * 60 + om)) / TF_MINUTES[tf]))
    counts = pd.Series(1, index=idx).groupby(idx.date).sum()
    full = counts.iloc[1:-1] if len(counts) > 2 else counts.iloc[0:0]   # first/last session may be partial
    return int((per_session - full).clip(lower=0).sum())


def clean_and_validate(df: pd.DataFrame | None, ticker: str, tf: str, cfg: dict,
                       now: datetime | None = None, data_kind: str = "delayed/historical") -> tuple[pd.DataFrame, QualityReport]:
    d, market = cfg["data"], cfg["market"]
    tz = market.get("timezone", "Asia/Kolkata")
    rep = QualityReport(ticker, tf, OK, data_kind=data_kind)
    if df is None or len(df) == 0:
        rep.status, rep.issues = UNAVAILABLE, ["no data returned"]
        return pd.DataFrame(columns=REQUIRED), rep
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        rep.status, rep.issues = INVALID, [f"missing columns {missing}"]
        return pd.DataFrame(columns=REQUIRED), rep

    out = df[REQUIRED].copy()
    try:
        out.index = pd.to_datetime(out.index)
    except Exception:  # noqa: BLE001
        rep.status, rep.issues = INVALID, ["unparseable timestamps"]
        return pd.DataFrame(columns=REQUIRED), rep
    # naive timestamps (yfinance daily/weekly) are exchange-local dates; aware ones are converted
    out.index = out.index.tz_localize(tz) if out.index.tz is None else out.index.tz_convert(tz)
    out = out.sort_index()
    for c in REQUIRED:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    rep.duplicates = int(out.index.duplicated().sum())
    out = out[~out.index.duplicated(keep="last")]
    price_nan = out[["Open", "High", "Low", "Close"]].isna().any(axis=1)
    rep.nan_rows = int(price_nan.sum())
    out = out[~price_nan]
    o_, h_, l_, c_ = (out[k].to_numpy() for k in ("Open", "High", "Low", "Close"))
    bad = pd.Series((h_ < np.maximum(o_, c_) - 1e-9) | (l_ > np.minimum(o_, c_) + 1e-9) | (l_ <= 0) | (h_ < l_),
                    index=out.index)
    rep.ohlc_violations = int(bad.sum())
    out = out[~bad]
    if rep.duplicates: rep.issues.append(f"{rep.duplicates} duplicate timestamps removed")
    if rep.nan_rows: rep.issues.append(f"{rep.nan_rows} rows with missing prices removed")
    if rep.ohlc_violations: rep.issues.append(f"{rep.ohlc_violations} OHLC-inconsistent rows removed")
    if len(out) == 0:
        rep.status = INVALID
        return out, rep

    nowts = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz=tz)
    nowts = nowts.tz_localize(tz) if nowts.tzinfo is None else nowts.tz_convert(tz)
    if d.get("completed_candles_only", True) and is_forming(out.index[-1], tf, nowts, market):
        out = out.iloc[:-1]
        rep.dropped_forming = True
        rep.issues.append("last (forming) candle excluded")

    # corporate action heuristic: unadjusted overnight jump > 35 % (split/bonus/demerger) - flagged, not altered
    jumps = (out.Close / out.Close.shift() - 1).abs() > 0.35
    rep.suspected_corporate_actions = int(jumps.sum())
    if rep.suspected_corporate_actions:
        rep.issues.append(f"{rep.suspected_corporate_actions} price jump(s) >35% - possible split/bonus/corporate action; verify")

    vol = out["Volume"]
    rep.zero_volume_pct = float((vol.fillna(0) <= 0).mean() * 100) if len(out) else float("nan")
    if rep.zero_volume_pct > 20:
        rep.issues.append(f"volume missing/zero on {rep.zero_volume_pct:.0f}% bars")
    out["Volume"] = vol.where(vol > 0)

    rep.missing_bars = count_missing_bars(out.index, tf, market)
    if rep.missing_bars:
        rep.issues.append(f"~{rep.missing_bars} expected bars missing (holidays not in config are counted)")
    rep.bars = len(out)
    if len(out):
        rep.first, rep.last = str(out.index[0])[:16], str(out.index[-1])[:16]
    if rep.bars < d["min_bars"][tf]:
        rep.status = INSUFFICIENT
        rep.issues.append(f"only {rep.bars} bars (< {d['min_bars'][tf]})")
    elif len(out) and trading_days_between(out.index[-1], nowts, market.get("holidays", [])) > d["stale_days"][tf]:
        rep.status = STALE
        rep.issues.append(f"last bar {rep.last} older than {d['stale_days'][tf]} trading days")
    return out, rep


def usable(rep: QualityReport) -> bool:
    return rep.status in (OK, STALE)


def nan_safe(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(v) else v
