"""U6 BK1: the price-action breakout screens B1, B2, B4, B7, B13, B14 as causal, bar-by-bar series.

Every value at bar j is computed from bars 0..j only (closed bars; the data layer never passes a bar still forming), so a
screen evaluated on the last bar, a screen alert at bar close and a historical run agree, and appending later bars never
changes an earlier value (tested). Distances are ATR multiples, with ATR read on the bar BEFORE the signal bar so a wide
breakout bar cannot inflate its own yardstick.

Each screen gives, per side ("up" = breakout, "down" = breakdown), a frame with:
  raw         the screen's condition on this bar
  signal      `raw`, one per leg: after a signal the side re-arms only once a `leg_degree` swing AGAINST it is confirmed
              after the signal bar, the break fails (a close back inside the fail level), or `leg_rearm_bars` bars pass
              without a new extreme (a new base) - so an alert fires once per leg
  level       the breakout level (B13: the wide bar's midpoint; B14 'at the level' without a close through it: the bar's
              opposite extreme)
  fail_level  the level a close back inside of means "failed" (B4: the mother bar's opposite extreme - the mother-bar rule)
  base_len, base_high, base_low   the base / reference window before the break
  wick        a wick beyond the level with the close not beyond it (a sweep, not a breakout; B1 / B2 / B4 / B7)
  event       B2: BOS (break with the swing trend), CHOCH (against it) or BREAK (range / no trend read yet); B4: the setup;
              B7: fresh / extended
  days_since  B7: calendar days since the old high (low) was set
  through     B14: the close went through the level by the minimum distance
The down side is the up rule on mirrored prices (high <-> -low), so the two sides cannot drift apart.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from app.indicators.volatility import atr as _atr

SCREENS: Dict[str, str] = {
    "B1": "Range / consolidation breakout",
    "B2": "Swing-high / swing-low breakout (BOS / CHoCH)",
    "B4": "Inside-bar / NR4 / NR7 breakout",
    "B7": "52-week / N-week high breakout",
    "B13": "ATR / range expansion",
    "B14": "Volume breakout",
}
# Timeframes a screen is meant for (spec §3: B7 is a swing screen on Daily / Weekly); the others run on every signal TF.
SCREEN_TIMEFRAMES: Dict[str, Tuple[str, ...]] = {"B7": ("1d", "1w")}
SIDES = ("up", "down")
# Bars a Dow trend needs before it is read, by swing degree: D0 is the `dow_min_bars` parameter (40, measured - see
# params.py); D1-D3 keep the swing history (registry SWING_DEGREE_MIN_BARS), not measured for the trend.
DOW_MIN_BARS: Tuple[int, ...] = (40, 250, 600, 1200)
COLUMNS = ("raw", "signal", "level", "fail_level", "base_len", "base_high", "base_low", "wick", "event", "days_since", "through")


def _shift(x: np.ndarray, k: int = 1) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if k < len(x):
        out[k:] = x[:len(x) - k]
    return out


@dataclass
class _Side:
    """One side's prices in 'up' orientation: for the down side every price is negated and high/low swap."""
    sign: int
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray

    def back(self, x: Any) -> Any:
        return x * self.sign


class Bars:
    """Closed bars of one timeframe plus the lazily computed pieces every screen shares (ATR, relative volume, pivots)."""

    def __init__(self, f: pd.DataFrame, p: Dict[str, Any]) -> None:
        if f.empty:
            raise ValueError("no bars")
        self.f, self.p, self.n = f, p, len(f)
        self.o, self.h, self.l, self.c = (f[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        self.v = f["volume"].to_numpy(float) if "volume" in f.columns else np.full(self.n, np.nan)
        atr = _atr(f[["open", "high", "low", "close"]].astype(float), int(p["atr_n"])).to_numpy(float)
        self.atr = atr
        self.atr_prev = _shift(atr)                                     # ATR of the bars before this one
        vol = pd.Series(self.v)
        avg = vol.shift(1).rolling(int(p["vol_avg_n"]), min_periods=int(p["vol_avg_n"])).mean().to_numpy(float)
        with np.errstate(divide="ignore", invalid="ignore"):
            self.rvol = np.where(avg > 0, self.v / avg, np.nan)
        self.vol_avg = avg
        self._pivots: Dict[int, List[Any]] = {}
        self._dow: Dict[int, np.ndarray] = {}

    def side(self, sign: int) -> _Side:
        if sign > 0:
            return _Side(1, self.o, self.h, self.l, self.c)
        return _Side(-1, -self.o, -self.l, -self.h, -self.c)

    def vol_ok(self, mult: float) -> np.ndarray:
        if mult <= 0:
            return np.ones(self.n, dtype=bool)
        return np.nan_to_num(self.rvol, nan=-1.0) >= mult

    def pivots(self, degree: int) -> List[Any]:
        """Confirmed causal swings (app/price_action/causal_swings, default settings) in confirmation order."""
        if degree not in self._pivots:
            from app.price_action import causal_swings as cs
            from app.price_action import pa_settings
            s = pa_settings.settings()
            if not 0 <= degree < len(s["swing_atr_mult"]):
                raise ValueError(f"swing degree {degree} is not one of 0-{len(s['swing_atr_mult']) - 1}")
            frame = pd.DataFrame({k: self.f[k].astype(float).to_numpy() for k in ("open", "high", "low", "close")})
            frame["timestamp"] = frame["bar_end"] = self.f.index
            self._pivots[degree] = list(cs.degree_pivots(frame, degree, s))
        return self._pivots[degree]

    def dow(self, degree: int) -> np.ndarray:
        """Dow trend on every bar from the swings confirmed by that bar: UP (last high HH and last low HL), DOWN (LH + LL),
        RANGE (anything else once both kinds are labelled), None before that - and None on the first `dow_min_bars` (D0)
        or DOW_MIN_BARS[degree] bars of the frame, where a shorter history could read a different trend (unknown, never a guess)."""
        if degree not in self._dow:
            out = np.full(self.n, None, dtype=object)
            last: Dict[str, Optional[float]] = {"H": None, "L": None}
            label: Dict[str, Optional[str]] = {"H": None, "L": None}
            piv = self.pivots(degree)
            k = 0
            cur: Optional[str] = None
            for t in range(self.n):
                while k < len(piv) and int(piv[k].confirmed_idx) <= t:
                    pv = piv[k]
                    prev = last[pv.kind]
                    if prev is not None:
                        label[pv.kind] = ("HH" if pv.price > prev else "LH") if pv.kind == "H" else ("HL" if pv.price > prev else "LL")
                    last[pv.kind] = pv.price
                    if label["H"] and label["L"]:
                        cur = "UP" if (label["H"], label["L"]) == ("HH", "HL") else "DOWN" if (label["H"], label["L"]) == ("LH", "LL") else "RANGE"
                    k += 1
                out[t] = cur
            warm = int(self.p["dow_min_bars"]) if degree == 0 else DOW_MIN_BARS[degree]
            out[:min(self.n, warm)] = None
            self._dow[degree] = out
        return self._dow[degree]


def _empty(n: int) -> Dict[str, np.ndarray]:
    nan = lambda: np.full(n, np.nan)  # noqa: E731
    return {"raw": np.zeros(n, dtype=bool), "signal": np.zeros(n, dtype=bool), "level": nan(), "fail_level": nan(),
            "base_len": nan(), "base_high": nan(), "base_low": nan(), "wick": np.zeros(n, dtype=bool),
            "event": np.full(n, None, dtype=object), "days_since": nan(), "through": np.zeros(n, dtype=bool)}


def _beyond(s: _Side, b: Bars, level: np.ndarray, k: float) -> Tuple[np.ndarray, np.ndarray]:
    """(close beyond the level by k x ATR, wick beyond with the close not beyond) in up orientation."""
    with np.errstate(invalid="ignore"):
        buf = k * b.atr_prev
        ok = np.isfinite(level) & np.isfinite(buf) & (s.c > level + buf)
        wick = np.isfinite(level) & (s.h > level) & ~ok
    return ok, wick


def _directional(s: _Side, frac: float) -> np.ndarray:
    rng = s.h - s.l
    with np.errstate(divide="ignore", invalid="ignore"):
        return (rng > 0) & ((s.h - s.c) / np.where(rng > 0, rng, np.nan) <= frac)


# --------------------------------------------------------------------------------------------------------------- screens
def _b1(b: Bars, s: _Side, p: Dict[str, Any], out: Dict[str, np.ndarray]) -> None:
    n = b.n
    vol_ok = b.vol_ok(p["vol_mult"])
    hs, ls = pd.Series(s.h), pd.Series(s.l)
    if p["range_mode"] == "fixed":
        m = int(p["range_n"])
        hi = hs.shift(1).rolling(m, min_periods=m).max().to_numpy(float)
        lo = ls.shift(1).rolling(m, min_periods=m).min().to_numpy(float)
        out["base_len"][:] = np.where(np.isfinite(hi), m, np.nan)
    else:
        hi, lo = np.full(n, np.nan), np.full(n, np.nan)
        kmin, kmax, cap = int(p["base_min_bars"]), int(p["base_max_bars"]), float(p["base_max_height_atr"])
        near_hi = hs.shift(1).rolling(kmin, min_periods=kmin).max().to_numpy(float)
        for jj in np.flatnonzero(np.nan_to_num(s.c - near_hi, nan=-1.0) > 0):    # a base can only break where the close
            j = int(jj)
            a = b.atr_prev[j]                                                      # clears its last kmin bars
            if not np.isfinite(a) or a <= 0:
                continue
            length, top, bot = 0, float("-inf"), float("inf")
            for k in range(1, min(kmax, j) + 1):
                top2, bot2 = max(top, float(s.h[j - k])), min(bot, float(s.l[j - k]))
                if top2 - bot2 > cap * a:
                    break
                length, top, bot = k, top2, bot2
            if length < kmin:
                continue
            if p["base_contraction"]:
                first = slice(j - length, j - length + length // 2)
                second = slice(j - length + length // 2, j)
                if s.h[second].max() - s.l[second].min() > s.h[first].max() - s.l[first].min():
                    continue
            hi[j], lo[j], out["base_len"][j] = top, bot, length
    ok, wick = _beyond(s, b, hi, p["min_break_atr"])
    if p["prior_advance_pct"] > 0:
        adv = np.zeros(n, dtype=bool)
        for jk in np.flatnonzero(ok):
            j = int(jk)
            start = j - int(out["base_len"][j]) - 1
            ref = start - int(p["prior_advance_n"])
            if ref >= 0:
                a0, a1 = s.c[ref], s.c[start]                              # mirrored: a decline for the down side
                gain = (a1 - a0) / abs(a0) * 100.0 if a0 != 0 else np.nan
                adv[j] = bool(np.isfinite(gain) and gain >= p["prior_advance_pct"])
        ok &= adv
    out["raw"][:] = ok & vol_ok
    out["level"][:], out["fail_level"][:] = hi, hi
    out["base_high"][:], out["base_low"][:] = hi, lo
    out["wick"][:] = wick & np.isfinite(hi)


def _b2(b: Bars, s: _Side, p: Dict[str, Any], out: Dict[str, np.ndarray]) -> None:
    """Close beyond the last confirmed swing extreme (confirmed by the bar BEFORE this one), once per swing."""
    degree = int(p["swing_degree"])
    own = "H" if s.sign > 0 else "L"
    piv = [pv for pv in b.pivots(degree) if pv.kind == own]
    trend = b.dow(degree)
    with_trend = "UP" if s.sign > 0 else "DOWN"                         # BOS with it, CHOCH against it, BREAK otherwise
    level = np.full(b.n, np.nan)
    origin = np.full(b.n, -1)
    k, cur, cur_bar, used = 0, np.nan, -1, False
    raw = np.zeros(b.n, dtype=bool)
    buf = p["min_break_atr"] * b.atr_prev
    for t in range(b.n):
        while k < len(piv) and int(piv[k].confirmed_idx) <= t - 1:
            cur, cur_bar, used = s.sign * piv[k].price, int(piv[k].bar_idx), False
            k += 1
        if cur_bar < 0 or used:
            continue
        level[t], origin[t] = cur, cur_bar
        if np.isfinite(buf[t]) and s.c[t] > cur + buf[t]:
            raw[t], used = True, True
            prior = trend[t - 1] if t > 0 else None
            out["event"][t] = "BOS" if prior == with_trend else "BREAK" if prior in (None, "RANGE") else "CHOCH"
    _, wick = _beyond(s, b, level, p["min_break_atr"])
    out["raw"][:] = raw
    out["level"][:], out["fail_level"][:] = level, level
    out["wick"][:] = wick
    for tt in np.flatnonzero(origin >= 0):
        t, o = int(tt), int(origin[tt])
        out["base_len"][t] = t - o
        out["base_high"][t] = level[t]
        out["base_low"][t] = s.l[o:t].min() if t > o else np.nan


def _b4(b: Bars, s: _Side, p: Dict[str, Any], out: Dict[str, np.ndarray]) -> None:
    rng = s.h - s.l
    setup = p["nr_setup"]
    if setup == "inside":
        is_setup = np.zeros(b.n, dtype=bool)
        is_setup[1:] = (s.h[1:] <= s.h[:-1]) & (s.l[1:] >= s.l[:-1])
    else:
        w = 4 if setup == "nr4" else 7
        lowest = pd.Series(rng).rolling(w, min_periods=w).min().to_numpy(float)
        is_setup = np.isfinite(lowest) & (rng <= lowest)
    vol_ok = b.vol_ok(p["vol_mult"])
    trig = int(p["nr_trigger_bars"])
    buf = p["min_break_atr"] * b.atr_prev
    for j in range(1, b.n):
        for back in range(1, trig + 1):                       # the closest setup bar within the trigger window
            sb = j - back
            if sb < 0 or not is_setup[sb]:
                continue
            lv = s.h[sb]
            if np.any(s.c[sb + 1:j] > lv):                    # an earlier bar already closed beyond it: not the first break
                break
            out["level"][j] = lv
            mother = sb - 1 if setup == "inside" else sb
            out["fail_level"][j] = s.l[mother]                # mother-bar rule: a close beyond its opposite extreme = failed
            out["base_len"][j] = 2 if setup == "inside" else 1
            out["base_high"][j], out["base_low"][j] = lv, s.l[mother]
            out["event"][j] = setup.upper()
            beyond = np.isfinite(buf[j]) and s.c[j] > lv + buf[j]
            out["raw"][j] = bool(beyond and vol_ok[j])
            out["wick"][j] = bool(s.h[j] > lv and not beyond)
            break


def _b7(b: Bars, s: _Side, p: Dict[str, Any], out: Dict[str, np.ndarray]) -> None:
    """Close beyond the extreme of the previous N calendar weeks (this bar excluded); the history must cover the window."""
    if not isinstance(b.f.index, pd.DatetimeIndex):
        raise ValueError("B7 needs time-stamped bars")
    window = pd.Timedelta(days=7 * int(p["high_weeks"]))
    hs = pd.Series(s.h, index=b.f.index)
    prior = hs.rolling(window, closed="left").max().to_numpy(float)
    covered = (b.f.index - window >= b.f.index[0])
    prior = np.where(covered, prior, np.nan)
    ok, wick = _beyond(s, b, prior, p["min_break_atr"])
    out["raw"][:] = ok & b.vol_ok(p["vol_mult"])
    out["level"][:], out["fail_level"][:] = prior, prior
    out["wick"][:] = wick
    idx = b.f.index
    for j in np.flatnonzero(out["raw"] | wick):
        lo_pos = int(np.searchsorted(idx, idx[j] - window, side="left"))
        seg = s.h[lo_pos:j]
        if not len(seg):
            continue
        at = lo_pos + len(seg) - 1 - int(np.argmax(seg[::-1]))   # the most recent bar that set the old extreme
        days = int((idx[j] - idx[at]) / pd.Timedelta(days=1))                 # calendar days: the same on Daily and Weekly
        out["days_since"][j] = days
        out["base_len"][j] = j - at
        out["base_high"][j] = prior[j]
        out["base_low"][j] = s.l[at:j].min()
        out["event"][j] = "FRESH" if days >= int(p["fresh_min_days"]) else "EXTENDED"


def _b13(b: Bars, s: _Side, p: Dict[str, Any], out: Dict[str, np.ndarray]) -> None:
    rng = s.h - s.l
    with np.errstate(invalid="ignore"):
        wide = np.isfinite(b.atr_prev) & (rng >= p["expansion_k"] * b.atr_prev) & (b.atr_prev > 0)
    raw = wide & _directional(s, p["close_loc"]) & b.vol_ok(p["vol_mult"])
    mid = (s.h + s.l) / 2.0
    m = int(p["range_n"])
    out["raw"][:] = raw
    out["level"][:], out["fail_level"][:] = mid, mid                     # a wide bar should hold its midpoint
    out["base_len"][:] = m
    out["base_high"][:] = pd.Series(s.h).shift(1).rolling(m, min_periods=m).max().to_numpy(float)
    out["base_low"][:] = pd.Series(s.l).shift(1).rolling(m, min_periods=m).min().to_numpy(float)


def _b14(b: Bars, s: _Side, p: Dict[str, Any], out: Dict[str, np.ndarray]) -> None:
    m = int(p["range_n"])
    level = pd.Series(s.h).shift(1).rolling(m, min_periods=m).max().to_numpy(float)
    with np.errstate(invalid="ignore"):
        at = np.isfinite(level) & np.isfinite(b.atr_prev) & (s.h >= level - p["level_tol_atr"] * b.atr_prev)
    through, _ = _beyond(s, b, level, p["min_break_atr"])
    raw = at & _directional(s, p["close_loc"]) & b.vol_ok(p["shock_mult"])
    out["raw"][:] = raw
    out["through"][:] = through & raw
    out["level"][:] = np.where(through, level, s.l)                       # not through: the shock bar's low must hold
    out["fail_level"][:] = out["level"]
    out["base_len"][:] = m
    out["base_high"][:] = level
    out["base_low"][:] = pd.Series(s.l).shift(1).rolling(m, min_periods=m).min().to_numpy(float)


_RULES = {"B1": _b1, "B2": _b2, "B4": _b4, "B7": _b7, "B13": _b13, "B14": _b14}


def one_per_leg(b: Bars, s: _Side, raw: np.ndarray, fail_level: np.ndarray, leg_degree: int, rearm_bars: int) -> np.ndarray:
    """The first raw bar of each leg. After a signal at bar i the side re-arms when (a) a swing AGAINST it (a swing low
    for a breakout) whose extreme is after bar i is confirmed, (b) a close goes back inside the fail level (the break
    failed), or (c) `rearm_bars` bars pass without a new extreme beyond the highest point since the signal (a new base).
    Each is known at the bar it happens, so the result is causal."""
    against = "L" if s.sign > 0 else "H"
    events = sorted((int(pv.confirmed_idx), int(pv.bar_idx)) for pv in b.pivots(leg_degree) if pv.kind == against)
    out = np.zeros(b.n, dtype=bool)
    armed, sig, fail, k = True, -1, np.nan, 0
    peak, peak_bar = -np.inf, -1
    for t in range(b.n):
        while k < len(events) and events[k][0] <= t:
            if not armed and events[k][1] > sig:
                armed = True
            k += 1
        if not armed:
            if np.isfinite(fail) and s.c[t] < fail:
                armed = True
            elif t - peak_bar >= rearm_bars:
                armed = True
            if s.h[t] > peak:
                peak, peak_bar = s.h[t], t
        if armed and raw[t]:
            out[t], armed, sig, fail = True, False, t, fail_level[t]
            peak, peak_bar = s.h[t], t
    return out


def evaluate_screen(f: pd.DataFrame, screen: str, p: Dict[str, Any], *, bars: Optional[Bars] = None,
                    sides: Tuple[str, ...] = SIDES) -> Dict[str, pd.DataFrame]:
    """{side: frame of COLUMNS} for one screen over closed bars `f` (validated parameters `p`)."""
    if screen not in _RULES:
        raise ValueError(f"unknown breakout screen {screen!r}; one of {', '.join(SCREENS)}")
    b = bars if bars is not None else Bars(f, p)
    result: Dict[str, pd.DataFrame] = {}
    for side in sides:
        s = b.side(1 if side == "up" else -1)
        out = _empty(b.n)
        _RULES[screen](b, s, p, out)
        out["signal"] = one_per_leg(b, s, out["raw"], out["fail_level"], int(p["leg_degree"]), int(p["leg_rearm_bars"]))
        for key in ("level", "fail_level", "base_high", "base_low"):            # prices back to the real orientation
            out[key] = s.back(out[key])
        if s.sign < 0:
            out["base_high"], out["base_low"] = out["base_low"], out["base_high"]
        result[side] = pd.DataFrame(out, index=f.index)
    return result


__all__ = ["SCREENS", "SCREEN_TIMEFRAMES", "SIDES", "COLUMNS", "Bars", "evaluate_screen", "one_per_leg"]
