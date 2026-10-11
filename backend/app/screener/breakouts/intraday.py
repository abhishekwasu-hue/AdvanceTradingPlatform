"""U6 BK2: the intraday breakout screens B16 / B17 / B18 as causal, bar-by-bar series (spec §3 "Intraday specials").

  B16 opening range breakout   close beyond the high / low of the session's first `or_minutes`
  B17 session level break      close beyond the first hour's high / low, the previous regular session's high / low, the
                               session's first candle, or the pre-open bars' high / low (where the data has them)
  B18 new high / low of day    close beyond the session's high / low so far

Common rules (all editable, app/screener/breakouts/params.py):
* the close must be beyond the level by `min_break_atr` x ATR (ATR of the bars before the signal bar);
* confirmation on the signal bar: relative volume against the SAME TIME OF DAY >= `tod_vol_mult` (session.py; the average
  uses prior sessions only) and the close on the breakout side of the session VWAP (`vwap_agree`);
* the time-of-day window: no signal from a bar that starts in the first `skip_first_min` minutes, or (B16) inside the
  opening range itself, or that closes after the exchange's no-new-entries time (`tod_window_end`);
* closed bars only; a value at bar j uses bars 0..j.

One signal per leg:
* B16 / B17 levels are fixed for the session, so the event is the CROSS: the first eligible bar that closes beyond the
  level after a close that was not (the first eligible bar of a session counts when it is already beyond - a gap that
  held through the skipped minutes). A new leg needs a close back inside first, and `intraday_max_per_session` caps the
  signals per side per session (default 1: the first break).
* B18's level moves with every new extreme, so it uses the swing-leg rule of BK1 (screens.one_per_leg), re-armed at the
  start of every session.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Tuple

import numpy as np

from app.screener.breakouts import session as ses

if TYPE_CHECKING:  # pragma: no cover - typing only (screens.py imports this module)
    from app.screener.breakouts.screens import Bars, _Side

SCREENS: Dict[str, str] = {
    "B16": "Opening range breakout",
    "B17": "First-hour / previous-day / first-candle / pre-market level break",
    "B18": "New high / low of day",
}
TIMEFRAMES: Tuple[str, ...] = ("1m", "3m", "5m", "15m")          # the Intraday style (spec §2)
FIXED_LEVEL = ("B16", "B17")


def _whole_bars(minutes: int, b: "Bars", what: str) -> None:
    tf = b.tf_minutes()
    if minutes % tf:
        raise ValueError(f"{what} ({minutes} min) is not a whole number of {tf:g}-minute bars")


def confirmations(b: "Bars", s: "_Side", p: Dict[str, Any]) -> Tuple[np.ndarray, np.ndarray]:
    """(volume ok, VWAP ok) on every bar, in the side's orientation. NaN (no history, no volume) never passes."""
    mult = float(p["tod_vol_mult"])
    rv = b.tod_rvol()
    vol_ok = np.ones(b.n, dtype=bool) if mult <= 0 else np.nan_to_num(rv, nan=-1.0) >= mult
    if p["vwap_agree"]:
        vw = s.sign * b.vwap()
        with np.errstate(invalid="ignore"):
            vwap_ok = np.isfinite(vw) & (s.c > vw)
    else:
        vwap_ok = np.ones(b.n, dtype=bool)
    return vol_ok, vwap_ok


def _oriented(s: "_Side", hi: np.ndarray, lo: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """(level, opposite side) of a real-price high / low pair in the side's orientation."""
    return (hi, lo) if s.sign > 0 else (-lo, -hi)


def _fixed_level(b: "Bars", s: "_Side", p: Dict[str, Any], out: Dict[str, np.ndarray], level: np.ndarray, opposite: np.ndarray,
                 elig: np.ndarray, event: str, base_len: float) -> None:
    sm = b.sessions()
    buf = p["min_break_atr"] * b.atr_prev
    with np.errstate(invalid="ignore"):
        beyond = elig & np.isfinite(level) & np.isfinite(buf) & (s.c > level + buf)
        wick = elig & np.isfinite(level) & (s.h > level) & ~beyond
    prev_elig = np.r_[False, elig[:-1]] & (np.r_[-2, sm.session[:-1]] == sm.session)
    prev_beyond = np.r_[False, beyond[:-1]]
    cross = beyond & (~prev_elig | ~prev_beyond)
    vol_ok, vwap_ok = confirmations(b, s, p)
    out["raw"][:] = cross & vol_ok & vwap_ok
    out["wick"][:] = wick
    known = np.isfinite(level)
    out["level"][:] = np.where(known, level, np.nan)
    out["fail_level"][:] = out["level"]
    out["base_high"][:] = np.where(known, level, np.nan)
    out["base_low"][:] = np.where(known, opposite, np.nan)
    out["base_len"][:] = np.where(known, base_len, np.nan)
    out["event"][known] = event


def b16(b: "Bars", s: "_Side", p: Dict[str, Any], out: Dict[str, np.ndarray]) -> None:
    minutes = int(p["or_minutes"])
    _whole_bars(minutes, b, "the opening range")
    sm = b.sessions()
    hi, lo = ses.window_levels(b.h, b.l, sm, minutes)
    level, opposite = _oriented(s, hi, lo)
    elig = ses.eligible(sm, max(minutes, float(p["skip_first_min"])), p["tod_window_end"])
    _fixed_level(b, s, p, out, level, opposite, elig, f"OR{minutes}", minutes / b.tf_minutes())


def b17(b: "Bars", s: "_Side", p: Dict[str, Any], out: Dict[str, np.ndarray]) -> None:
    sm = b.sessions()
    kind = p["day_level"]
    skip = float(p["skip_first_min"])
    if kind == "first_hour":
        minutes = int(p["first_hour_min"])
        _whole_bars(minutes, b, "the first hour")
        hi, lo = ses.window_levels(b.h, b.l, sm, minutes)
        skip, event, length = max(skip, minutes), "FIRST_HOUR", minutes / b.tf_minutes()
    elif kind == "first_candle":
        minutes = int(b.tf_minutes())
        hi, lo = ses.window_levels(b.h, b.l, sm, minutes)
        skip, event, length = max(skip, minutes), "FIRST_CANDLE", 1.0
    elif kind == "prev_day":
        hi, lo = ses.previous_session_levels(b.h, b.l, sm)
        event, length = ("PDH" if s.sign > 0 else "PDL"), float("nan")
    else:
        hi, lo = ses.premarket_levels(b.h, b.l, sm)
        event, length = "PREMARKET", float("nan")
    level, opposite = _oriented(s, hi, lo)
    _fixed_level(b, s, p, out, level, opposite, ses.eligible(sm, skip, p["tod_window_end"]), event, length)


def b18(b: "Bars", s: "_Side", p: Dict[str, Any], out: Dict[str, np.ndarray]) -> None:
    sm = b.sessions()
    level, opposite = ses.session_extremes_before(s.h, s.l, sm)        # the side's orientation already: s.h is -low going down
    elig = ses.eligible(sm, float(p["skip_first_min"]), p["tod_window_end"])
    buf = p["min_break_atr"] * b.atr_prev
    with np.errstate(invalid="ignore"):
        beyond = elig & np.isfinite(level) & np.isfinite(buf) & (s.c > level + buf)
        wick = elig & np.isfinite(level) & (s.h > level) & ~beyond
    vol_ok, vwap_ok = confirmations(b, s, p)
    out["raw"][:] = beyond & vol_ok & vwap_ok
    out["wick"][:] = wick
    out["level"][:], out["fail_level"][:] = level, level
    out["base_high"][:], out["base_low"][:] = level, opposite
    idx = np.arange(b.n)
    first = np.full(b.n, -1)
    starts = sm.first_of_session()
    cur = -1
    for j in range(b.n):
        if starts[j]:
            cur = j
        first[j] = cur if sm.session[j] >= 0 else -1
    out["base_len"][:] = np.where((first >= 0) & np.isfinite(level), idx - first, np.nan)
    out["event"][np.isfinite(level)] = "HOD" if s.sign > 0 else "LOD"


RULES = {"B16": b16, "B17": b17, "B18": b18}


def session_cap(raw: np.ndarray, sm: ses.SessionMap, cap: int) -> np.ndarray:
    """At most `cap` signals per session (per side: called once per side)."""
    out = np.zeros(len(raw), dtype=bool)
    counts: Dict[int, int] = {}
    for jj in np.flatnonzero(raw):
        j = int(jj)
        sid = int(sm.session[j])
        if counts.get(sid, 0) < cap:
            out[j] = True
            counts[sid] = counts.get(sid, 0) + 1
    return out


__all__ = ["SCREENS", "TIMEFRAMES", "FIXED_LEVEL", "RULES", "confirmations", "session_cap", "b16", "b17", "b18"]
