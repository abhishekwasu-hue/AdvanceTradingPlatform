"""Causal (no-lookahead) multi-degree swings with confirmed pivots and an automatic timeframe choice.

Ported from Trade@0df3e09d0aa5942e3352327d5b998c5f6939ac61, elliott/swings.py (plus the ZigZag / fractal pivot cores from
price_action/legs.py and an NSE session resampler in place of opportunity_engine/sessions.py).

A "degree" is a level of swing threshold (D0 smallest ... D3 largest). For each degree:
  * threshold at bar i, from bars <= i only: atr -> swing_atr_mult[d] x ATR(atr_len); pct -> swing_pct[d] % of the close;
    fractal -> r = swing_fractal_r[d] bars on each side.
  * A pivot is CONFIRMED only when price has moved back from the extreme by the threshold (closed bars' high / low).
    confirmed_idx = that bar, confirmed_at = its bar_end (the moment it became knowable). A confirmed pivot never moves.
  * The running extreme after the last confirmed pivot is the TENTATIVE pivot (current wave only; never used as completed).
Auto TF: a wave [start, end] is read on the smallest timeframe that shows it in tf_bars_min..tf_bars_max closed bars.
Similarity & balance (Neely): of two neighbouring legs the smaller is >= min_ratio x the larger in price OR time.

`known_at(pivots, t)` gives exactly what a run cut at `t` would have produced - the truncation invariance the tests check.
The existing `app.price_action.swings` (centred fractal window, used by market_structure) is unchanged.
"""
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from app.price_action.pa_settings import TF_MIN

SESSION_OPEN_MIN = 9 * 60 + 15        # 09:15 IST
SESSION_CLOSE_MIN = 15 * 60 + 30      # 15:30 IST


@dataclass(frozen=True)
class Pivot:
    degree: int
    kind: str                         # "H" | "L"
    price: float
    bar_idx: int                      # the extreme bar (in this degree's timeframe frame)
    ts: pd.Timestamp                  # start of the extreme bar
    confirmed_idx: Optional[int]
    confirmed_at: Optional[pd.Timestamp]   # knowable at = confirm bar's bar_end; tentative -> None
    status: str                       # "confirmed" | "tentative"
    tf: str = "5m"


@dataclass(frozen=True)
class RawPivot:
    kind: str
    pivot_bar: int
    confirm_bar: int
    price: float


# ---------------------------------------------------------------------------------------------------------------------
# Frames
# ---------------------------------------------------------------------------------------------------------------------
def _ist_naive(ts: pd.Series) -> pd.Series:
    ts = pd.to_datetime(ts)
    if getattr(ts.dt, "tz", None) is not None:
        ts = ts.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    return ts


def build_frame(df1m: pd.DataFrame, tf: str) -> pd.DataFrame:
    """1-minute candles -> NSE 09:15-anchored `tf` bars, CLOSED bars only. Columns: timestamp, bar_end, OHLC (IST, naive).
    A bar is closed when the 1-minute data reaches its end (or the session close for the day's last, shorter bar)."""
    d = df1m[["timestamp", "open", "high", "low", "close"]].copy()
    d["timestamp"] = _ist_naive(d["timestamp"])
    minute = d["timestamp"].dt.hour * 60 + d["timestamp"].dt.minute
    d = d[(minute >= SESSION_OPEN_MIN) & (minute < SESSION_CLOSE_MIN)].reset_index(drop=True)
    if d.empty:
        return pd.DataFrame(columns=["timestamp", "bar_end", "open", "high", "low", "close"])
    last_end = d["timestamp"].iloc[-1] + pd.Timedelta(minutes=1)
    day = d["timestamp"].dt.normalize()
    if tf == "1m":
        d["bar_end"] = d["timestamp"] + pd.Timedelta(minutes=1)
        return d[["timestamp", "bar_end", "open", "high", "low", "close"]]
    if tf == "1d":
        g = d.groupby(day)
        out = pd.DataFrame({"timestamp": g["timestamp"].first(), "open": g["open"].first(), "high": g["high"].max(),
                            "low": g["low"].min(), "close": g["close"].last(),
                            "src_end": g["timestamp"].max() + pd.Timedelta(minutes=1)}).reset_index(drop=True)
        out["bar_end"] = out["timestamp"].dt.normalize() + pd.Timedelta(minutes=SESSION_CLOSE_MIN)
    else:
        step = TF_MIN[tf]
        bucket = (d["timestamp"].dt.hour * 60 + d["timestamp"].dt.minute - SESSION_OPEN_MIN) // step
        start = day + pd.to_timedelta(SESSION_OPEN_MIN + bucket * step, unit="min")
        g = d.groupby(start)
        out = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(), "close": g["close"].last(),
                            "src_end": g["timestamp"].max() + pd.Timedelta(minutes=1)})
        out.index.name = "timestamp"
        out = out.reset_index()
        close_of_day = out["timestamp"].dt.normalize() + pd.Timedelta(minutes=SESSION_CLOSE_MIN)
        out["bar_end"] = np.minimum(out["timestamp"] + pd.Timedelta(minutes=step), close_of_day)
    # Closed per bar, as in Trade: the bar's own 1-minute data must reach its end (a bar with missing trailing minutes -
    # a data gap or a halt - is not treated as complete), and nothing past the newest minute is ever used.
    out = out[(out["src_end"] >= out["bar_end"]) & (out["bar_end"] <= last_end)]
    return out[["timestamp", "bar_end", "open", "high", "low", "close"]].reset_index(drop=True)


def asof(frame: pd.DataFrame, now) -> pd.DataFrame:
    """Bars that ended by `now` (bar_end <= now)."""
    return frame[frame["bar_end"] <= pd.Timestamp(now)].reset_index(drop=True)


# ---------------------------------------------------------------------------------------------------------------------
# Thresholds and pivot cores
# ---------------------------------------------------------------------------------------------------------------------
def atr(df: pd.DataFrame, n: int) -> np.ndarray:
    """At bar i: the n-bar mean true range from bars <= i; NaN for the first n - 1 bars (no pivot there)."""
    h, l, c = (df[k].to_numpy(float) for k in ("high", "low", "close"))
    pc = np.r_[np.nan, c[:-1]]
    tr = np.nanmax(np.c_[h - l, np.abs(h - pc), np.abs(l - pc)], axis=1)
    return pd.Series(tr).rolling(n, min_periods=n).mean().to_numpy(float)


def threshold(df: pd.DataFrame, degree: int, s: Dict[str, Any]) -> np.ndarray:
    if s["swing_mode"] == "pct":
        return df["close"].to_numpy(float) * s["swing_pct"][degree] / 100.0
    return s["swing_atr_mult"][degree] * atr(df, s["atr_len"])


def zigzag_pivots(df: pd.DataFrame, thr: np.ndarray) -> List[RawPivot]:
    """ZigZag: a move back from the extreme >= thr[i] (known at bar i) confirms the extreme. Pivots alternate H / L."""
    h, l = df["high"].to_numpy(float), df["low"].to_numpy(float)
    n = len(h)
    out: List[RawPivot] = []
    if n < 2:
        return out
    trend, hi_i, lo_i = 0, 0, 0
    for i in range(1, n):
        t = thr[i] if np.isfinite(thr[i]) else np.inf
        if trend >= 0 and h[i] >= h[hi_i]:
            hi_i = i
        if trend <= 0 and l[i] <= l[lo_i]:
            lo_i = i
        if trend >= 0 and hi_i < i and h[hi_i] - l[i] >= t:
            out.append(RawPivot("H", hi_i, i, float(h[hi_i])))
            lo_i = hi_i + 1 + int(np.argmin(l[hi_i + 1:i + 1]))     # the lowest bar after the pivot (up to i only)
            trend = -1
            continue
        if trend <= 0 and lo_i < i and h[i] - l[lo_i] >= t:
            out.append(RawPivot("L", lo_i, i, float(l[lo_i])))
            hi_i = lo_i + 1 + int(np.argmax(h[lo_i + 1:i + 1]))
            trend = 1
    return out


def fractal_pivots(df: pd.DataFrame, r: int) -> List[RawPivot]:
    """Fractal swings: pivot j is confirmed at j + r. Consecutive same-kind pivots are possible."""
    h, l = df["high"].to_numpy(float), df["low"].to_numpy(float)
    n, out = len(h), []
    for j in range(r, n - r):
        if h[j] > h[j - r:j].max() and h[j] >= h[j + 1:j + r + 1].max():
            out.append(RawPivot("H", j, j + r, float(h[j])))
        if l[j] < l[j - r:j].min() and l[j] <= l[j + 1:j + r + 1].min():
            out.append(RawPivot("L", j, j + r, float(l[j])))
    return sorted(out, key=lambda p: (p.confirm_bar, p.pivot_bar))


def _alternating_fractals(df: pd.DataFrame, r: int) -> List[Tuple[str, int, int, float]]:
    """Fractal pivots made to alternate, causally: when a more extreme pivot of the same kind arrives, the opposite extreme
    between the two is inserted as a new confirmed pivot (confirmed now). A confirmed pivot never changes; a less extreme
    new pivot of the same kind is skipped."""
    h, l = df["high"].to_numpy(float), df["low"].to_numpy(float)
    out: List[Tuple[str, int, int, float]] = []
    for p in fractal_pivots(df, r):
        if not out:
            out.append((p.kind, p.pivot_bar, p.confirm_bar, p.price))
            continue
        k0, b0, _, px0 = out[-1]
        if p.kind != k0:
            beyond = p.price > px0 if p.kind == "H" else p.price < px0
            if p.pivot_bar > b0 and beyond:
                out.append((p.kind, p.pivot_bar, p.confirm_bar, p.price))
            continue
        more = p.price > px0 if p.kind == "H" else p.price < px0
        if not more or p.pivot_bar <= b0 + 1:
            continue
        seg = slice(b0 + 1, p.pivot_bar)
        j = b0 + 1 + int(np.argmin(l[seg]) if p.kind == "H" else np.argmax(h[seg]))
        mid = "L" if p.kind == "H" else "H"
        out.append((mid, j, p.confirm_bar, float(l[j] if mid == "L" else h[j])))
        out.append((p.kind, p.pivot_bar, p.confirm_bar, p.price))
    return out


# ---------------------------------------------------------------------------------------------------------------------
# Pivots per degree
# ---------------------------------------------------------------------------------------------------------------------
def degree_pivots(frame: pd.DataFrame, degree: int, s: Dict[str, Any], tf: str = "5m") -> List[Pivot]:
    """One degree's confirmed pivots (in confirmation order). frame = closed bars (timestamp, bar_end, OHLC)."""
    if len(frame) < 3:
        return []
    if s["swing_mode"] == "fractal":
        raw = _alternating_fractals(frame, s["swing_fractal_r"][degree])
    else:
        raw = [(p.kind, p.pivot_bar, p.confirm_bar, p.price) for p in zigzag_pivots(frame, threshold(frame, degree, s))]
    ts, be = frame["timestamp"].to_numpy(), frame["bar_end"].to_numpy()
    return [Pivot(degree, k, float(px), int(b), pd.Timestamp(ts[b]), int(c), pd.Timestamp(be[c]), "confirmed", tf) for k, b, c, px in raw]


def tentative_pivot(frame: pd.DataFrame, confirmed: List[Pivot], degree: int, upto: Optional[int] = None, tf: str = "5m") -> Optional[Pivot]:
    """The running extreme after the last confirmed pivot known by `upto` (bars <= upto). Equal prices -> the last bar."""
    upto = len(frame) - 1 if upto is None else upto
    confirmed = [p for p in confirmed if p.confirmed_idx is not None and p.confirmed_idx <= upto]
    if not confirmed:
        return None
    last = confirmed[-1]
    a = last.bar_idx + 1
    if a > upto:
        return None
    if last.kind == "H":
        seg = frame["low"].to_numpy(float)[a:upto + 1]
        j, kind, px = a + len(seg) - 1 - int(np.argmin(seg[::-1])), "L", float(seg.min())
    else:
        seg = frame["high"].to_numpy(float)[a:upto + 1]
        j, kind, px = a + len(seg) - 1 - int(np.argmax(seg[::-1])), "H", float(seg.max())
    return Pivot(degree, kind, px, j, pd.Timestamp(frame["timestamp"].iloc[j]), None, None, "tentative", tf)


def degree_tf(s: Dict[str, Any], degree: int) -> str:
    return s["structure_tf"] if s["degree_tf_mode"] == "auto_by_bars" else s["degree_tf"][degree]


def multi_degree(df1m: pd.DataFrame, s: Dict[str, Any], now=None) -> Dict[int, Dict[str, Any]]:
    """Every degree's confirmed pivots plus the tentative one, from the bars closed by `now`.
    {degree: {"tf", "frame", "confirmed": [Pivot], "tentative": Pivot | None}}."""
    out: Dict[int, Dict[str, Any]] = {}
    frames: Dict[str, pd.DataFrame] = {}
    for d in range(s["degree_levels"]):
        tf = degree_tf(s, d)
        if tf not in frames:
            f = build_frame(df1m, tf)
            frames[tf] = asof(f, now) if now is not None else f
        fr = frames[tf]
        conf = degree_pivots(fr, d, s, tf)
        out[d] = {"tf": tf, "frame": fr, "confirmed": conf, "tentative": tentative_pivot(fr, conf, d, tf=tf)}
    return out


def known_at(pivots: List[Pivot], t) -> List[Pivot]:
    """Confirmed pivots known at time `t` (confirmed_at <= t)."""
    t = pd.Timestamp(t)
    return [p for p in pivots if p.confirmed_at is not None and p.confirmed_at <= t]


# ---------------------------------------------------------------------------------------------------------------------
# Similarity & balance, auto timeframe
# ---------------------------------------------------------------------------------------------------------------------
def legs_of(pivots: List[Pivot]) -> List[Tuple[Pivot, Pivot, float, int]]:
    """(start, end, price length, bars incl. both ends) for consecutive pivots."""
    return [(a, b, abs(b.price - a.price), b.bar_idx - a.bar_idx + 1) for a, b in zip(pivots, pivots[1:])]


def similar_degree(leg_a, leg_b, min_ratio: float) -> bool:
    """Neely: the smaller >= min_ratio x the larger, in price OR in time."""
    pa, ta, pb, tb = leg_a[2], max(leg_a[3], 1), leg_b[2], max(leg_b[3], 1)
    price_ok = min(pa, pb) >= min_ratio * max(pa, pb) if max(pa, pb) > 0 else True
    time_ok = min(ta, tb) >= min_ratio * max(ta, tb)
    return bool(price_ok or time_ok)


def balance_rate(pivots: List[Pivot], min_ratio: float) -> float:
    lg = legs_of(pivots)
    pairs = list(zip(lg, lg[1:]))
    return float(np.mean([similar_degree(a, b, min_ratio) for a, b in pairs])) if pairs else float("nan")


def closed_bars_between(frame: pd.DataFrame, start_ts, end_ts) -> int:
    st, en = pd.Timestamp(start_ts), pd.Timestamp(end_ts)
    return int(len(frame[(frame["bar_end"] > st) & (frame["bar_end"] <= en)]))


def auto_tf(frames: Dict[str, pd.DataFrame], start_ts, end_ts, s: Dict[str, Any]) -> Tuple[str, Dict[str, int], bool]:
    """The smallest of `auto_tfs` showing the wave in tf_bars_min..tf_bars_max closed bars. None in range -> the largest with
    at least tf_bars_min bars (structure visible, less noise); none with enough -> the smallest. Returns (tf, counts, in_range).
    A timeframe missing from `frames` is an error (never silently skipped)."""
    missing = [tf for tf in s["auto_tfs"] if tf not in frames]
    if missing:
        raise ValueError(f"auto_tf: no frames for {missing}")
    counts = {tf: closed_bars_between(frames[tf], start_ts, end_ts) for tf in s["auto_tfs"]}
    order = sorted(counts, key=TF_MIN.get)
    for tf in order:
        if s["tf_bars_min"] <= counts[tf] <= s["tf_bars_max"]:
            return tf, counts, True
    enough = [t for t in order if counts[t] >= s["tf_bars_min"]]
    return (enough[-1] if enough else order[0]), counts, False
