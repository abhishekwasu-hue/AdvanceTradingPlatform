"""U6 BK3: the indicator breakouts B10 / B11 / B12 / B15 as causal, bar-by-bar series (spec
docs/specs/ATP_BREAKOUT_SCREENERS_ADDENDUM.md; behind `screener_breakouts` AND `screener_breakouts_bk3`).

  B10 Donchian channel    close beyond the highest high (up) / lowest low (down) of the previous `donchian_n` bars by
                          `min_break_atr` x ATR (the ATR filter; ATR of the bars before the signal bar)
  B11 squeeze -> expansion
        bollinger         a squeeze bar has the lowest band width ((upper - lower) / middle) of the last `squeeze_lookback`
                          bars; within `squeeze_within` bars after the last one, a close outside the band (above the upper
                          for up) on volume >= `vol_mult` x average
        ttm               the squeeze is on while the Bollinger bands sit inside the Keltner channel (EMA `kc_n` +/- `kc_mult`
                          x ATR(`kc_n`)); the signal is its release (on the bar before, off on this one) with the TTM momentum
                          (linear-regression value of close - the mean of the Donchian midpoint and the SMA, over `bb_n`) on the
                          breakout side
  B12 moving average      a close above the key MA (`ma_n`, `ma_type`) by `min_break_atr` x ATR and above every close of the
                          `ma_consol_n` bars before, when those closes stayed within `ma_band_atr` ATRs of the MA (a
                          consolidation around the line - a close merely flipping above the line inside the chop is not a
                          break) and the MA is flat or rising (fell by at most `ma_flat_pct` % over `ma_slope_n` bars);
                          optional MA stack (`ma_stack`: fast > key > slow); one per leg
  B15 RSI / ADX regime    RSI stayed inside 50 +/- `rsi_band` on the `regime_n` bars before and leaves the band on this bar
                          (above for up), ADX was at or below `adx_low` inside that window and rises on this bar with the DI of
                          the breakout side leading, and the close clears that window's high by `min_break_atr` x ATR

Every value at bar j uses bars 0..j only: indicators are recursive (EMA, Wilder) or trailing windows, levels come from
the bars BEFORE the signal bar except the band / line the close is compared with on its own bar. The down side is the up
rule on mirrored prices (high <-> -low): RSI mirrors to 100 - RSI, the DIs swap, bands and averages mirror - so the two
sides cannot drift apart. One signal per leg (screens.one_per_leg).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Tuple

import numpy as np
import pandas as pd

if TYPE_CHECKING:  # pragma: no cover - typing only (screens.py imports this module)
    from app.screener.breakouts.screens import Bars, _Side

SCREENS: Dict[str, str] = {
    "B10": "Donchian channel breakout",
    "B11": "Bollinger squeeze -> expansion (TTM squeeze variant)",
    "B12": "Moving-average breakout",
    "B15": "RSI / ADX regime breakout",
}


def _shift(x: np.ndarray, k: int = 1) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if 0 < k < len(x):
        out[k:] = x[:len(x) - k]
    return out


def _prior_max(x: np.ndarray, m: int) -> np.ndarray:
    return pd.Series(x).shift(1).rolling(m, min_periods=m).max().to_numpy(float)


def _prior_min(x: np.ndarray, m: int) -> np.ndarray:
    return pd.Series(x).shift(1).rolling(m, min_periods=m).min().to_numpy(float)


def _beyond(s: "_Side", b: "Bars", level: np.ndarray, k: float) -> Tuple[np.ndarray, np.ndarray]:
    from app.screener.breakouts.screens import _beyond as beyond
    return beyond(s, b, level, k)


# ------------------------------------------------------------------------------------------------------------------ B10
def b10(b: "Bars", s: "_Side", p: Dict[str, Any], out: Dict[str, np.ndarray]) -> None:
    m = int(p["donchian_n"])
    hi, lo = _prior_max(s.h, m), _prior_min(s.l, m)
    ok, wick = _beyond(s, b, hi, float(p["min_break_atr"]))
    out["raw"][:] = ok
    out["level"][:], out["fail_level"][:] = hi, hi
    out["base_len"][:] = np.where(np.isfinite(hi), m, np.nan)
    out["base_high"][:], out["base_low"][:] = hi, lo
    out["wick"][:] = wick & np.isfinite(hi)


# ------------------------------------------------------------------------------------------------------------------ B11
def bollinger(c: np.ndarray, n: int, k: float) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(middle, upper, lower): SMA +/- k sample standard deviations (app.indicators.volatility.bollinger's definition)."""
    cs = pd.Series(c)
    mid = cs.rolling(n, min_periods=n).mean().to_numpy(float)
    sd = cs.rolling(n, min_periods=n).std(ddof=1).to_numpy(float)
    return mid, mid + k * sd, mid - k * sd


def linreg_value(x: np.ndarray, n: int) -> np.ndarray:
    """The least-squares line's value at the last bar of each n-bar window (NaN until n finite values)."""
    xs = pd.Series(x)
    idx = np.arange(len(x), dtype=float)
    sx = xs.rolling(n, min_periods=n).sum().to_numpy(float)
    six = pd.Series(idx * x).rolling(n, min_periods=n).sum().to_numpy(float)
    u_bar = (n - 1) / 2.0
    start = idx - (n - 1)
    denom = n * (n * n - 1) / 12.0
    with np.errstate(invalid="ignore", divide="ignore"):
        slope = (six - (start + u_bar) * sx) / denom if denom > 0 else np.full(len(x), np.nan)
        return sx / n + slope * u_bar


def ttm_momentum(s: "_Side", n: int) -> np.ndarray:
    """TTM squeeze momentum: linreg(close - mean(mean(highest high, lowest low), SMA(close)), n), in the side's orientation."""
    hh = pd.Series(s.h).rolling(n, min_periods=n).max().to_numpy(float)
    ll = pd.Series(s.l).rolling(n, min_periods=n).min().to_numpy(float)
    sma = pd.Series(s.c).rolling(n, min_periods=n).mean().to_numpy(float)
    return linreg_value(s.c - ((hh + ll) / 2.0 + sma) / 2.0, n)


def _keltner_atr(b: "Bars", n: int) -> np.ndarray:
    return b.memo(("kc_atr", n), lambda: b.wilder(b.true_range(), n))      # ATR(n), app.indicators.volatility.atr's definition


def adx(b: "Bars", s: "_Side", n: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(ADX, +DI, -DI) in the side's orientation - app.indicators.directional.adx's definition (tested equal), on the shared
    true range (symmetric, so the down side only swaps the directional movement)."""
    up_move = np.r_[np.nan, np.diff(s.h)]
    down_move = np.r_[np.nan, -np.diff(s.l)]
    with np.errstate(invalid="ignore", divide="ignore"):
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        tr = b.wilder(b.true_range(), n)
        tr = np.where(tr == 0.0, np.nan, tr)
        plus_di = 100 * (b.wilder(plus_dm, n) / tr)
        minus_di = 100 * (b.wilder(minus_dm, n) / tr)
        di_sum = plus_di + minus_di
        dx = 100 * np.abs(plus_di - minus_di) / np.where(di_sum == 0.0, np.nan, di_sum)
    return b.wilder(dx, n), plus_di, minus_di


def _wilder(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).ewm(alpha=1 / n, adjust=False, min_periods=n).mean().to_numpy(float)


def rsi(c: np.ndarray, n: int) -> np.ndarray:
    """RSI - app.indicators.momentum.rsi's definition (tested equal), on numpy arrays (no pandas clip / where)."""
    delta = np.r_[np.nan, np.diff(c)]
    avg_gain, avg_loss = _wilder(np.maximum(delta, 0.0), n), _wilder(-np.minimum(delta, 0.0), n)
    with np.errstate(invalid="ignore", divide="ignore"):
        rs = avg_gain / np.where(avg_loss == 0.0, np.nan, avg_loss)
        out = 100 - (100 / (1 + rs))
    return np.where(avg_loss != 0, out, 100.0)


def _run_start(on: np.ndarray, t: int) -> int:
    """First bar of the run of True values that ends at bar t."""
    i = t
    while i > 0 and on[i - 1]:
        i -= 1
    return i


def b11(b: "Bars", s: "_Side", p: Dict[str, Any], out: Dict[str, np.ndarray]) -> None:
    n = int(p["bb_n"])
    mid, up, lo = bollinger(s.c, n, float(p["bb_k"]))
    if p["squeeze_mode"] == "bollinger":
        with np.errstate(invalid="ignore", divide="ignore"):
            width = (up - lo) / np.abs(mid)
        look = int(p["squeeze_lookback"])
        floor = pd.Series(width).rolling(look, min_periods=look).min().to_numpy(float)
        with np.errstate(invalid="ignore"):
            squeeze = np.isfinite(floor) & (width <= floor)
        last = pd.Series(np.where(squeeze, np.arange(b.n, dtype=float), np.nan)).ffill().to_numpy(float)
        last_before = _shift(last)                                   # the last squeeze bar BEFORE this one
        within = int(p["squeeze_within"])
        with np.errstate(invalid="ignore"):
            raw = (np.isfinite(last_before) & (np.arange(b.n) - last_before <= within) & np.isfinite(up) & (s.c > up)
                   & b.vol_ok(float(p["vol_mult"])))
        level, fail = up, mid
        start = np.where(np.isfinite(last_before), last_before, np.nan)
        for tt in np.flatnonzero(raw):
            t = int(tt)
            a = int(start[t])
            out["base_len"][t] = t - a
            out["base_high"][t], out["base_low"][t] = s.h[a:t].max(), s.l[a:t].min()
            out["event"][t] = "BB_SQUEEZE"
    else:
        kc_n = int(p["kc_n"])
        ema = pd.Series(s.c).ewm(span=kc_n, adjust=False, min_periods=kc_n).mean().to_numpy(float)
        width = float(p["kc_mult"]) * _keltner_atr(b, kc_n)
        with np.errstate(invalid="ignore"):
            on = np.isfinite(up) & np.isfinite(ema) & np.isfinite(width) & (up < ema + width) & (lo > ema - width)
        mom = ttm_momentum(s, n)
        prev_on = np.zeros(b.n, dtype=bool)
        prev_on[1:] = on[:-1]
        with np.errstate(invalid="ignore"):
            raw = prev_on & ~on & np.isfinite(mom) & (mom > 0) & np.isfinite(ema)
        level, fail = ema, ema                                     # the Keltner middle: the momentum side must hold
        for tt in np.flatnonzero(raw):
            t = int(tt)
            a = _run_start(on, t - 1)
            out["base_len"][t] = t - a
            out["base_high"][t], out["base_low"][t] = s.h[a:t].max(), s.l[a:t].min()
            out["event"][t] = "TTM_FIRE"
    out["raw"][:] = raw
    out["level"][:], out["fail_level"][:] = level, fail


# ------------------------------------------------------------------------------------------------------------------ B12
def _ma(c: np.ndarray, n: int, kind: str) -> np.ndarray:
    cs = pd.Series(c)
    if kind == "ema":                                               # app.indicators.trend.ema's definition
        return cs.ewm(span=n, adjust=False, min_periods=n).mean().to_numpy(float)
    return cs.rolling(n, min_periods=n).mean().to_numpy(float)


def b12(b: "Bars", s: "_Side", p: Dict[str, Any], out: Dict[str, np.ndarray]) -> None:
    kind = str(p["ma_type"])
    ma = _ma(s.c, int(p["ma_n"]), kind)
    then = _shift(ma, int(p["ma_slope_n"]))
    m = int(p["ma_consol_n"])
    dist = pd.Series(np.abs(s.c - ma)).shift(1).rolling(m, min_periods=m).max().to_numpy(float)
    buf = float(p["min_break_atr"]) * b.atr_prev
    with np.errstate(invalid="ignore", divide="ignore"):
        flat_or_rising = (ma - then) / np.abs(then) * 100.0 >= -float(p["ma_flat_pct"])
        consolidated = dist <= float(p["ma_band_atr"]) * b.atr_prev
        cleared = (s.c > ma + buf) & (s.c > _prior_max(s.c, m))           # above the line AND every close of the base around it
        raw = np.isfinite(buf) & flat_or_rising & consolidated & cleared
        if p["ma_stack"]:
            fast, slow = _ma(s.c, int(p["ma_stack_fast"]), kind), _ma(s.c, int(p["ma_stack_slow"]), kind)
            raw &= (fast > ma) & (ma > slow)
    out["raw"][:] = raw
    out["level"][:], out["fail_level"][:] = ma, ma
    out["base_len"][:] = np.where(np.isfinite(dist), m, np.nan)
    out["base_high"][:], out["base_low"][:] = _prior_max(s.h, m), _prior_min(s.l, m)


# ------------------------------------------------------------------------------------------------------------------ B15
def b15(b: "Bars", s: "_Side", p: Dict[str, Any], out: Dict[str, np.ndarray]) -> None:
    m = int(p["regime_n"])
    r = rsi(s.c, int(p["rsi_n"]))
    ax, plus, minus = adx(b, s, int(p["adx_n"]))
    band = float(p["rsi_band"])
    with np.errstate(invalid="ignore"):
        inside = np.isfinite(r) & (r >= 50.0 - band) & (r <= 50.0 + band)
    compressed = pd.Series(inside.astype(float)).shift(1).rolling(m, min_periods=m).sum().to_numpy(float) == m
    adx_floor = pd.Series(ax).shift(1).rolling(m, min_periods=m).min().to_numpy(float)
    hi, lo = _prior_max(s.h, m), _prior_min(s.l, m)
    ok, _ = _beyond(s, b, hi, float(p["min_break_atr"]))
    with np.errstate(invalid="ignore"):
        raw = (compressed & (r > 50.0 + band) & (adx_floor <= float(p["adx_low"])) & (ax > _shift(ax)) & (plus > minus) & ok)
    out["raw"][:] = raw
    out["level"][:], out["fail_level"][:] = hi, hi
    out["base_len"][:] = np.where(np.isfinite(hi), m, np.nan)
    out["base_high"][:], out["base_low"][:] = hi, lo


RULES = {"B10": b10, "B11": b11, "B12": b12, "B15": b15}

__all__ = ["SCREENS", "RULES", "b10", "b11", "b12", "b15", "bollinger", "linreg_value", "ttm_momentum", "adx", "rsi"]
