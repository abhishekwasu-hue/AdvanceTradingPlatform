"""U6 BK4: B8 gap breakout - a gap beyond a level on the open that holds, with the gap-and-go / gap-hold / gap-fill
classification (spec docs/specs/ATP_BREAKOUT_SCREENERS_ADDENDUM.md B8; behind `screener_breakouts` AND
`screener_breakouts_bk4`).

  level      prev_day: the previous regular session's high (up) / low (down); base: the highest high / lowest low of the
             previous `gap_base_n` regular sessions
  gap        the session's open is beyond the level by at least `gap_min_atr` daily ATRs (ATR(atr_n) of the sessions
             before; 0 = any open beyond it)
  hold       intraday: every close of the session's first `gap_hold_min` minutes stays beyond the level, read on the bar
             that ends that window (a whole number of bars; the session must start at its open in the data); Daily: the
             day's close
  class      GAP_AND_GO  held, and the close at the end of the window is at or beyond the open (the gap kept going)
             GAP_HOLD    held, but the close is back between the open and the level
             GAP_FILL    a close back inside the level within the window (no signal; the class is still reported)

The signal (held: GAP_AND_GO or GAP_HOLD) is one bar per session by construction, so there is no leg dedupe. Intraday
sessions come from the exchange calendar (session.py, as BK2); a value at bar j uses bars 0..j only. The down side is the
up rule on mirrored prices.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Tuple

import numpy as np
import pandas as pd

from app.screener.breakouts.params import TF_MINUTES

if TYPE_CHECKING:  # pragma: no cover - typing only (screens.py imports this module)
    from app.screener.breakouts.screens import Bars, _Side

SCREENS: Dict[str, str] = {"B8": "Gap breakout (gap-and-go / gap-fill)"}
TIMEFRAMES: Tuple[str, ...] = ("1m", "3m", "5m", "15m", "1d")
CLASSES: Tuple[str, ...] = ("GAP_AND_GO", "GAP_HOLD", "GAP_FILL")


def is_daily(b: "Bars") -> bool:
    if b.tf is not None:
        return TF_MINUTES.get(b.tf, 0) >= TF_MINUTES["1d"]
    idx = b.f.index
    if not isinstance(idx, pd.DatetimeIndex) or len(idx) < 2:
        return True
    steps = np.diff(idx.asi8)
    steps = steps[steps > 0]
    return bool(len(steps)) and float(steps.min()) >= 20 * 3600 * 1e9


def _wilder(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).ewm(alpha=1 / n, adjust=False, min_periods=n).mean().to_numpy(float)


def _levels(top: np.ndarray, bot: np.ndarray, p: Dict[str, Any]) -> Tuple[np.ndarray, np.ndarray]:
    """Per session (in sequence): the level and the opposite side from the sessions BEFORE it."""
    k = 1 if p["gap_level"] == "prev_day" else int(p["gap_base_n"])
    hi = pd.Series(top).shift(1).rolling(k, min_periods=k).max().to_numpy(float)
    lo = pd.Series(bot).shift(1).rolling(k, min_periods=k).min().to_numpy(float)
    return hi, lo


def _classify(close: float, opn: float, level: float, held: bool) -> str:
    if not held:
        return "GAP_FILL"
    return "GAP_AND_GO" if close >= opn else "GAP_HOLD"


def _daily(b: "Bars", s: "_Side", p: Dict[str, Any], out: Dict[str, np.ndarray]) -> None:
    level, opposite = _levels(s.h, s.l, p)
    with np.errstate(invalid="ignore"):
        gap = np.isfinite(level) & np.isfinite(b.atr_prev) & (s.o > level + float(p["gap_min_atr"]) * b.atr_prev) & (s.o > level)
    for tt in np.flatnonzero(gap):
        t = int(tt)
        held = bool(s.c[t] > level[t])
        out["event"][t] = _classify(float(s.c[t]), float(s.o[t]), float(level[t]), held)
        out["raw"][t] = held
        out["level"][t] = out["fail_level"][t] = level[t]
        out["base_high"][t], out["base_low"][t] = level[t], opposite[t]
        out["base_len"][t] = 1 if p["gap_level"] == "prev_day" else int(p["gap_base_n"])


def _intraday(b: "Bars", s: "_Side", p: Dict[str, Any], out: Dict[str, np.ndarray]) -> None:
    sm = b.sessions()
    tf = b.tf_minutes()
    hold = int(p["gap_hold_min"])
    if hold % tf:
        raise ValueError(f"the gap hold window ({hold} min) is not a whole number of {tf:g}-minute bars")
    regular = [k for k in range(len(sm.dates)) if not sm.special[k]]
    pos = {k: i for i, k in enumerate(regular)}
    top, bot, cls = (np.full(len(regular), np.nan) for _ in range(3))
    first: Dict[int, int] = {}
    last: Dict[int, int] = {}
    ins = np.flatnonzero(sm.in_session)
    if len(ins):                                                        # sessions are contiguous blocks of in-session bars
        sid = sm.session[ins]
        starts = np.r_[0, np.flatnonzero(np.diff(sid)) + 1]
        ends = np.r_[starts[1:], len(ins)] - 1
        hmax = np.maximum.reduceat(s.h[ins], starts)
        lmin = np.minimum.reduceat(s.l[ins], starts)
        for k0, a, z, hv, lv_ in zip(sid[starts].tolist(), starts.tolist(), ends.tolist(), hmax.tolist(), lmin.tolist()):
            first.setdefault(k0, int(ins[a]))
            last[k0] = int(ins[z])
            if k0 in pos:
                i = pos[k0]
                top[i] = hv if not np.isfinite(top[i]) else max(top[i], hv)
                bot[i] = lv_ if not np.isfinite(bot[i]) else min(bot[i], lv_)
                cls[i] = s.c[ins[z]]
    prev_close = np.r_[np.nan, cls[:-1]]
    with np.errstate(invalid="ignore"):
        tr = np.fmax(np.fmax(top - bot, np.abs(top - prev_close)), np.abs(bot - prev_close))
    atr_known = np.r_[np.nan, _wilder(tr, int(p["atr_n"]))[:-1]]        # the daily ATR of the sessions before
    level_seq, opp_seq = _levels(top, bot, p)
    gap_k = float(p["gap_min_atr"])
    prev_regular = -1
    for k in range(len(sm.dates)):
        # the level of session k comes from the regular sessions before it (a special session is never a level source)
        i_prev = prev_regular
        if k in pos:
            prev_regular = pos[k]
        if k not in first or i_prev < 0:
            continue
        j0 = first[k]
        if sm.minute[j0] != 0:                                          # the data starts after the open: no open to read
            continue
        lv = level_seq[i_prev + 1] if i_prev + 1 < len(level_seq) and k in pos else _levels_at(top, bot, p, i_prev)
        opp = opp_seq[i_prev + 1] if i_prev + 1 < len(opp_seq) and k in pos else np.nan
        a = atr_known[i_prev + 1] if i_prev + 1 < len(atr_known) and k in pos else np.nan
        opn = float(s.o[j0])
        if not np.isfinite(lv) or not opn > lv or (gap_k > 0 and not (np.isfinite(a) and opn > lv + gap_k * a)):
            continue
        e = j0 + int(round(hold / tf)) - 1                              # the bar that ends the window, if the data has it
        if e > last[k] or sm.session[e] != k or sm.minute[e] + tf != hold:
            continue
        held = bool(np.all(s.c[j0:e + 1] > lv))
        out["event"][e] = _classify(float(s.c[e]), opn, float(lv), held)
        out["raw"][e] = held
        out["level"][e] = out["fail_level"][e] = lv
        out["base_high"][e], out["base_low"][e] = lv, opp
        out["base_len"][e] = e - j0 + 1


def _levels_at(top: np.ndarray, bot: np.ndarray, p: Dict[str, Any], i_last: int) -> float:
    """The level for a special session after regular session i_last (the regular sessions up to it)."""
    k = 1 if p["gap_level"] == "prev_day" else int(p["gap_base_n"])
    if i_last + 1 < k:
        return float("nan")
    return float(np.max(top[i_last + 1 - k:i_last + 1]))


def b8(b: "Bars", s: "_Side", p: Dict[str, Any], out: Dict[str, np.ndarray]) -> None:
    if is_daily(b):
        _daily(b, s, p, out)
    else:
        _intraday(b, s, p, out)


RULES = {"B8": b8}

__all__ = ["SCREENS", "TIMEFRAMES", "CLASSES", "RULES", "b8", "is_daily"]
