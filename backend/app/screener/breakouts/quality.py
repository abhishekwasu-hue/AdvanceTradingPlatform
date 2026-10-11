"""U6 BK1 (spec §3 quality overlays): grade A / B / C and "why it matched" for every breakout signal, plus the follow-up
status (accepted / failed) that tags the result for the journal and the fitness stats.

The GRADE is fixed at the signal bar and uses only bars up to it (closed bars; context timeframes by close time):
  HTF trend agreement   Dow trend on each context timeframe (1 point each when it is WITH the break; a break against
                        every context timeframe is capped at C)
  volume expansion      relative volume >= vol_mult (the default when the screen's volume condition is off; no volume
                        data: no point, said so)
  base dry-up           the last dryup_n bars' mean volume <= dryup_ratio x the vol_avg_n-bar mean before the break
  volatility regime     ATR / close percentile over atr_pct_lookback bars: low (<= atr_pct_low) earns the point
  room to run           distance to the next opposing level (confirmed swings + classical S/R from the zones port) >=
                        room_min_r x the stop distance; no opposing level (blue sky) counts as room
A >= grade_a_min points, B >= grade_b_min, else C - equal weights, nothing fitted (app/screener/breakouts/params.py).
The liquidity floor is a hard filter: a signal below it is left out (counted in `excluded`).

The STATUS reads the bars after the signal (as of the run's last bar): failed (a close back inside the fail level within
failed_break_bars), accepted (acceptance_closes consecutive closes beyond the level, or a retest that held), pending
(too few bars yet) or unconfirmed. A wick beyond the level with the close back inside is never a signal (screens.py).
PAPER / research only: nothing here places, changes or blocks an order.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from app.screener.breakouts import params as bp
from app.screener.breakouts import style
from app.screener.breakouts.screens import SCREEN_TIMEFRAMES, SCREENS, Bars, evaluate_screen

DISCLAIMER = "Breakout matches are symbols whose bars passed the screen's rules; they are not recommendations."


@dataclass
class BreakoutRun:
    screen: str
    signal_tf: str
    context_tfs: Tuple[str, ...]
    params: Dict[str, Any]
    signals: List[Dict[str, Any]] = field(default_factory=list)
    excluded: int = 0
    notes: List[str] = field(default_factory=list)


def context_trends(frames: Dict[str, pd.DataFrame], signal_tf: str, context: Tuple[str, ...], p: Dict[str, Any]) -> Dict[str, pd.Series]:
    """{context tf: Dow trend on the signal bars}, each value from context bars that had CLOSED by the signal bar's close."""
    sig_index = frames[signal_tf].index
    out: Dict[str, pd.Series] = {}
    for tf in context:
        f = frames[tf]
        if f is None or f.empty:
            out[tf] = pd.Series([None] * len(sig_index), index=sig_index, dtype=object)
            continue
        trend = pd.Series(Bars(f, p).dow(int(p["htf_degree"])), index=f.index, dtype=object)
        out[tf] = style.align_closed(trend, tf, sig_index, signal_tf).astype(object).where(lambda x: x.notna(), None)
    return out


def htf_pass(trends: List[Optional[str]], side: str, mode: str) -> bool:
    """agree: every context trend is WITH the side; not_against: every one is known and none against; off: always."""
    with_, against = ("UP", "DOWN") if side == "up" else ("DOWN", "UP")
    if mode == "off":
        return True
    if mode == "agree":
        return bool(trends) and all(t == with_ for t in trends)
    return bool(trends) and all(t is not None and t != against for t in trends)


def atr_percentile_series(atr_prev: np.ndarray, close: np.ndarray, lookback: int) -> np.ndarray:
    """On every bar: the percentile (0-100) of the previous bar's ATR / close among the last `lookback` such values
    (ties counted in; NaN until `lookback` finite values exist)."""
    with np.errstate(divide="ignore", invalid="ignore"):
        natr = atr_prev / np.abs(np.r_[np.nan, close[:-1]])
    roll = pd.Series(natr).rolling(lookback, min_periods=lookback)
    return roll.apply(lambda w: float((w <= w[-1]).sum()) / len(w) * 100.0, raw=True).to_numpy(float)


def atr_percentile(b: Bars, j: int, lookback: int) -> float:
    """atr_percentile_series at bar j, from bars 0..j only."""
    lo = j - lookback + 1
    if lo < 0:
        return float("nan")
    return float(atr_percentile_series(b.atr_prev[:j + 1], b.c[:j + 1], lookback)[-1])


def _opposing_level(b: Bars, j: int, side: str, p: Dict[str, Any]) -> Optional[float]:
    """The nearest level beyond the close on the breakout side, known at bar j: confirmed swings of room_swing_degree and
    the classical S/R levels (zones port, find_support_resistance_levels on bars 0..j)."""
    from app.price_action.zones.levels import find_support_resistance_levels
    close = b.c[j]
    kind = "H" if side == "up" else "L"
    cands = [pv.price for pv in b.pivots(int(p["room_swing_degree"])) if pv.kind == kind and int(pv.confirmed_idx) <= j]
    sr = find_support_resistance_levels(b.f.iloc[:j + 1])
    cands += [lv["level"] for lv in sr["resistance" if side == "up" else "support"]]
    beyond = [x for x in cands if (x > close if side == "up" else x < close)]
    if not beyond:
        return None
    return min(beyond) if side == "up" else max(beyond)


def _status(b: Bars, j: int, sign: int, level: float, fail_level: float, p: Dict[str, Any]) -> Tuple[str, Optional[str], Optional[int]]:
    """(status, retest, decided bar) from the bars after j up to the last one."""
    c, lo = b.c * sign, (b.l if sign > 0 else -b.h)
    lv, fl = level * sign, fail_level * sign
    tol = p["retest_tol_atr"] * (b.atr_prev[j] if np.isfinite(b.atr_prev[j]) else 0.0)
    last = b.n - 1
    for t in range(j + 1, min(j + int(p["failed_break_bars"]), last) + 1):
        if c[t] < fl:
            return "failed", None, t
    retest: Optional[str] = None
    retest_bar: Optional[int] = None
    for t in range(j + 1, last + 1):
        if lo[t] <= lv + tol:
            retest, retest_bar = ("held" if c[t] >= lv else "failed"), t
            break
    need = int(p["acceptance_closes"]) - 1
    if j + need <= last and all(c[t] > lv for t in range(j, j + need + 1)):
        return "accepted", retest, j + need
    if retest == "held":
        return "accepted", retest, retest_bar
    if last - j < max(need, int(p["failed_break_bars"])):
        return "pending", retest, None
    return "unconfirmed", retest, None


def grade_signal(b: Bars, j: int, side: str, row: pd.Series, trends: Dict[str, Optional[str]], p: Dict[str, Any]) -> Dict[str, Any]:
    """Checklist points, grade and the reasons, from bars 0..j only (deterministic: same bars + parameters -> same grade)."""
    sign = 1 if side == "up" else -1
    with_, against = ("UP", "DOWN") if side == "up" else ("DOWN", "UP")
    why: List[str] = []
    checks: Dict[str, Any] = {}
    points = 0
    agree = [tf for tf, t in trends.items() if t == with_]
    opposed = [tf for tf, t in trends.items() if t == against]
    points += len(agree)
    checks["htf_agree"] = agree
    checks["htf_against"] = opposed
    if agree:
        why.append(f"higher-timeframe trend agrees on {', '.join(agree)}")
    if opposed:
        why.append(f"against the trend on {', '.join(opposed)}")
    unknown = [tf for tf, t in trends.items() if t is None]
    if unknown:
        why.append(f"no trend read yet on {', '.join(unknown)}")
    rvol = b.rvol[j]
    need = p["vol_mult"] if p["vol_mult"] > 0 else bp.DEFAULTS["vol_mult"]   # volume off in the screen: the check keeps the default
    vol_exp = bool(np.isfinite(rvol) and rvol >= need)
    checks["volume_expansion"] = vol_exp
    if not np.isfinite(rvol):
        why.append("no volume data (volume checks skipped)")
    elif vol_exp:
        points += 1
        why.append(f"volume {rvol:.1f}x the {p['vol_avg_n']}-bar average")
    n_avg, n_dry = int(p["vol_avg_n"]), int(p["dryup_n"])
    dry = False
    if j >= n_avg:
        recent, base = np.nanmean(b.v[j - n_dry:j]), np.nanmean(b.v[j - n_avg:j])
        dry = bool(np.isfinite(recent) and np.isfinite(base) and base > 0 and recent <= p["dryup_ratio"] * base)
    checks["base_dry_up"] = dry
    if dry:
        points += 1
        why.append("volume dried up into the break")
    pct = atr_percentile(b, j, int(p["atr_pct_lookback"]))
    regime = None if not np.isfinite(pct) else "low" if pct <= p["atr_pct_low"] else "high" if pct >= p["atr_pct_high"] else "normal"
    checks["atr_percentile"] = None if regime is None else round(pct, 1)
    checks["regime"] = regime
    if regime == "low":
        points += 1
        why.append(f"from a low-volatility regime (ATR percentile {pct:.0f})")
    elif regime == "high":
        why.append(f"high-volatility regime (ATR percentile {pct:.0f})")
    level = float(row["level"])
    close = float(b.c[j])
    a = b.atr_prev[j]
    if p["stop_mode"] == "base" and np.isfinite(row["base_low" if side == "up" else "base_high"]):
        stop = float(row["base_low" if side == "up" else "base_high"])
    else:
        stop = level - sign * p["stop_atr_mult"] * (a if np.isfinite(a) else 0.0)
    risk = (close - stop) * sign
    nxt = _opposing_level(b, j, side, p)
    room_r = float("inf") if nxt is None else ((nxt - close) * sign / risk if risk > 0 else float("nan"))
    room_ok = bool(risk > 0 and (nxt is None or room_r >= p["room_min_r"]))
    checks.update(stop_ref=round(stop, 6), next_level=nxt, room_r=None if not np.isfinite(room_r) else round(room_r, 2), room_ok=room_ok)
    if room_ok:
        points += 1
        why.append("no opposing level beyond (blue sky)" if nxt is None else f"room to the next level {room_r:.1f}R")
    elif nxt is not None:
        why.append(f"next opposing level close ({room_r:.1f}R)" if np.isfinite(room_r) else "no room: the stop reference is beyond the close")
    grade = "A" if points >= p["grade_a_min"] else "B" if points >= p["grade_b_min"] else "C"
    if trends and len(opposed) == len(trends):
        grade = "C"
    return {"points": points, "grade": grade, "why": why, "checks": checks, "stop_ref": stop}


def _liquid(b: Bars, j: int, p: Dict[str, Any]) -> bool:
    if p["liq_min_value"] <= 0:
        return True
    lo = max(0, j - int(p["liq_n"]) + 1)
    value = np.nanmedian(b.c[lo:j + 1] * b.v[lo:j + 1])
    return bool(np.isfinite(value) and value >= p["liq_min_value"])


def run_breakout(screen: str, frames: Dict[str, pd.DataFrame], signal_tf: str, *, params: Optional[Dict[str, Any]] = None,
                 within: Optional[int] = None) -> BreakoutRun:
    """One breakout screen with the quality overlay over closed bars. `frames` holds at least the signal timeframe; missing
    context timeframes are built from it. `within`: only signals on the last `within` bars (1 = the last closed bar - a
    screen run); None = every signal in the history (fitness, tests). Raises ValueError for bad input."""
    p = bp.settings(**(params or {}))
    if screen not in SCREENS:
        raise ValueError(f"unknown breakout screen {screen!r}; one of {', '.join(SCREENS)}")
    allowed = SCREEN_TIMEFRAMES.get(screen)
    if allowed and signal_tf not in allowed:
        raise ValueError(f"{screen} runs on {' / '.join(allowed)} bars, not {signal_tf}")
    context = style.context_timeframes(signal_tf, p["context_ladder"])
    all_frames = style.frames_for(frames, signal_tf, context)
    f = all_frames[signal_tf]
    b = Bars(f, p)
    sides = ("up", "down") if p["direction"] == "both" else (p["direction"],)
    result = evaluate_screen(f, screen, p, bars=b, sides=sides)
    trends = context_trends(all_frames, signal_tf, context, p)
    run = BreakoutRun(screen, signal_tf, context, p)
    first = 0 if within is None else max(0, b.n - int(within))
    for side in sides:
        df = result[side]
        sign = 1 if side == "up" else -1
        for j in np.flatnonzero(df["signal"].to_numpy()):
            if j < first:
                continue
            if not _liquid(b, int(j), p):
                run.excluded += 1
                continue
            row = df.iloc[j]
            tr = {tf: trends[tf].iloc[j] for tf in context}
            g = grade_signal(b, int(j), side, row, tr, p)
            status, retest, decided = _status(b, int(j), sign, float(row["level"]), float(row["fail_level"]), p)
            a = b.atr_prev[j]
            close = float(b.c[j])
            base_high, base_low = row["base_high"], row["base_low"]
            run.signals.append({
                "screen": screen, "side": side, "bar": int(j), "time": f.index[j].isoformat() if hasattr(f.index[j], "isoformat") else str(f.index[j]),
                "close": close, "level": float(row["level"]),
                "dist_atr": round((close - float(row["level"])) * sign / a, 3) if np.isfinite(a) and a > 0 else None,
                "rvol": round(float(b.rvol[j]), 3) if np.isfinite(b.rvol[j]) else None,
                "base_len": None if not np.isfinite(row["base_len"]) else int(row["base_len"]),
                "base_height_pct": (round((base_high - base_low) / abs(base_low) * 100.0, 3)
                                    if np.isfinite(base_high) and np.isfinite(base_low) and base_low != 0 else None),
                "event": row["event"], "days_since": None if not np.isfinite(row["days_since"]) else int(row["days_since"]),
                "through": bool(row["through"]) if screen == "B14" else None,
                "htf": tr, "htf_pass": htf_pass(list(tr.values()), side, p["htf_filter"]),
                "grade": g["grade"], "points": g["points"], "why": g["why"], "checks": g["checks"], "stop_ref": g["stop_ref"],
                "status": status, "retest": retest, "decided_bar": decided,
            })
    run.signals.sort(key=lambda r: (r["bar"], r["side"]))
    return run


__all__ = ["run_breakout", "BreakoutRun", "grade_signal", "context_trends", "htf_pass", "atr_percentile", "atr_percentile_series", "DISCLAIMER"]
