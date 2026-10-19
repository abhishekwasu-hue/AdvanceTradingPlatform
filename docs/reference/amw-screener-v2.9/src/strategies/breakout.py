"""Breakout strategy (independent long/short rules).

Levels evaluated (each documented in the signal):
  * Horizontal: last CONFIRMED swing high (long) / swing low (short).
  * Range/consolidation: high/low of the `range_bars` bars preceding the breakout window, only if its
    height <= max_range_atr * ATR.
  * Pattern boundary: upper/lower boundary of a detected triangle/rectangle/wedge/flag/pennant.
  * Trendline: line through the last 3 confirmed swing highs (falling, for longs) / lows (rising, for shorts).
Mandatory: completed close beyond the level within `fresh_bars`, still holding, close beyond by
`min_close_beyond_atr`, R:R >= min_rr, RVOL >= min_rvol (if require_volume), retest (if require_retest).
A close back inside the level = FAILED breakout (bull/bear trap) -> rejected with that reason.
"""
from __future__ import annotations

from typing import Callable

import numpy as np

from ..analysis.context import TFAnalysis
from .base import (Condition, Signal, Strategy, StrategyContext, directional, momentum_score, mtf_alignment,
                   risk_reward, rr_score, rs_score, sgn, HIGHER)


def volume_component(rv: float | None, thr: float) -> tuple[float | None, str]:
    if rv is None or not np.isfinite(rv):
        return None, "RVOL NA (volume missing)"
    return float(np.clip((rv - 0.8) / (1.5 * thr - 0.8), 0, 1)), f"RVOL {rv:.2f} (threshold {thr})"


class BreakoutStrategy(Strategy):
    name = "breakout"
    title = "Breakout"

    def levels(self, t: TFAnalysis, direction: str) -> list[tuple[str, Callable[[int], float], int, float | None]]:
        """(label, level_at(j), known_from_index, measured_height)."""
        p, n, A = self.p, t.n, t.atr
        out = []
        sws = t.highs() if direction == "bullish" else t.lows()
        if sws:
            s = sws[-1]
            out.append((f"Horizontal {'resistance' if direction == 'bullish' else 'support'} {s.price:.2f} ({t.time(s.i)})",
                        lambda j, v=s.price: v, s.confirmed_at, None))
        rb, fb = int(p["range_bars"]), int(p["fresh_bars"])
        lo_i, hi_i = n - fb - rb, n - fb
        if lo_i >= 0:
            box_h, box_l = float(t.df.High.iloc[lo_i:hi_i].max()), float(t.df.Low.iloc[lo_i:hi_i].min())
            if box_h - box_l <= p["max_range_atr"] * A:
                lvl = box_h if direction == "bullish" else box_l
                out.append((f"Range {rb} bars {box_l:.2f}-{box_h:.2f}", lambda j, v=lvl: v, hi_i, box_h - box_l))
        for pat in t.patterns:
            if pat.get("upper") and pat["bias"] in (direction, "neutral"):
                a0, y0, a1, y1 = pat["upper" if direction == "bullish" else "lower"]
                m = (y1 - y0) / max(1, a1 - a0)
                h = abs(pat["upper"][1] - pat["lower"][1])
                known = max(x[0] for x in pat["points"]) + self.cfg["analysis"]["swing_k"]
                out.append((f"Pattern {pat['pattern']} boundary", lambda j, y0=y0, a0=a0, m=m: y0 + m * (j - a0), known, h))
        pts = sws[-3:]
        if len(pts) == 3:
            x = np.array([s.i for s in pts], float)
            y = np.array([s.price for s in pts], float)
            m, b = np.polyfit(x, y, 1)
            if (direction == "bullish" and m < 0) or (direction == "bearish" and m > 0):
                if np.abs(y - (m * x + b)).max() <= 0.5 * A:
                    out.append((f"Trendline through {len(pts)} swings", lambda j, m=m, b=b: m * j + b, pts[-1].confirmed_at, None))
        return out

    def evaluate_tf(self, ctx: StrategyContext, t: TFAnalysis, direction: str) -> list[Signal]:
        p, n, A, d = self.p, t.n, t.atr, sgn(direction)
        C, O, H, L = (t.df[c].to_numpy() for c in ("Close", "Open", "High", "Low"))
        out = []
        for label, lvl, known, height in self.levels(t, direction):
            win = range(max(known + 1, n - int(p["fresh_bars"])), n)
            cross = [j for j in win if (C[j] - lvl(j)) * d > 0 and (C[j - 1] - lvl(j - 1)) * d <= 0]
            near = abs(C[-1] - lvl(n - 1)) <= 0.5 * A
            if not cross and not near:
                continue
            sig = self.new_signal(ctx, t, f"Breakout: {label.split(' ')[0]}", direction)
            sig.setup = f"Breakout ({label})"
            conds = sig.conditions
            if not cross:
                conds.append(Condition("completed close beyond level", False, True,
                                       f"close {C[-1]:.2f} within 0.5 ATR of {lvl(n - 1):.2f} - awaiting close"))
                sig.entry_condition = f"completed close {'above' if d > 0 else 'below'} {lvl(n - 1):.2f}"
                out.append(sig)
                continue
            j = cross[0]
            L0 = lvl(j)
            sig.signal_time = t.time(j)
            conds.append(Condition("completed close beyond level", True, True, f"close {C[j]:.2f} vs level {L0:.2f} at {t.time(j)}"))
            holding = (C[-1] - lvl(n - 1)) * d > 0
            conds.append(Condition("still holding beyond level", holding, True,
                                   "holding" if holding else f"FAILED breakout ({'bull' if d > 0 else 'bear'} trap): close back inside"))
            beyond = (C[j] - L0) * d
            conds.append(Condition("close beyond by min distance", beyond >= p["min_close_beyond_atr"] * A, True,
                                   f"{beyond / A:.2f} ATR (min {p['min_close_beyond_atr']})"))
            rv = t.df.rvol.iloc[j]
            rv = float(rv) if np.isfinite(rv) else None
            conds.append(Condition("volume confirmation", None if rv is None else rv >= p["min_rvol"], bool(p["require_volume"]),
                                   "RVOL NA" if rv is None else f"RVOL {rv:.2f} (min {p['min_rvol']})"))
            body = abs(C[j] - O[j]) / max(1e-9, H[j] - L[j])
            conds.append(Condition("strong breakout candle", body >= 0.5, False, f"body {body * 100:.0f}% of range"))
            gap = (O[j] - L0) * d
            is_gap = gap > p["max_gap_atr"] * A
            conds.append(Condition("gap within limit", not is_gap, bool(p["reject_large_gaps"]), f"open beyond level by {gap / A:.2f} ATR"))
            if is_gap:
                sig.warnings.append(f"Gap breakout ({gap / A:.1f} ATR) - higher slippage/gap-fill risk")
            retest = any(((L[k] if d > 0 else H[k]) - lvl(k)) * d <= 0.1 * A for k in range(j + 1, n))
            conds.append(Condition("retest of broken level", retest, bool(p["require_retest"]),
                                   "retest held" if retest and holding else "no retest yet"))
            htf = [ctx.get(x) for x in HIGHER.get(t.tf, []) if ctx.get(x) is not None and ctx.get(x).trend_score is not None]
            opposing = [h.tf for h in htf if h.trend_score * d <= -0.5]
            conds.append(Condition("higher timeframe not opposing", None if not htf else not opposing, False,
                                   f"opposing: {opposing}" if opposing else ("aligned/neutral" if htf else "HTF NA")))
            entry = float(C[-1])
            if p["stop_method"] == "atr":
                stop, sm = entry - d * p["atr_stop_mult"] * A, f"ATR: {p['atr_stop_mult']} x ATR from entry"
            else:
                stop, sm = lvl(n - 1) - d * 0.5 * A, "Structure: 0.5 ATR beyond broken level"
            if height:
                target, tm = L0 + d * height, f"Measured move: level ± formation height {height:.2f}"
            else:
                target, tm = entry + d * p["target_r"] * abs(entry - stop), f"{p['target_r']}R projection"
            rr = risk_reward(entry, stop, target, direction)
            conds.append(Condition("risk/reward", None if rr is None else rr >= p["min_rr"], True,
                                   "NA" if rr is None else f"{rr:.2f} (min {p['min_rr']})"))
            sig.entry_zone = tuple(sorted((float(lvl(n - 1)), float(lvl(n - 1) + d * 0.5 * A))))
            sig.entry_condition = "retest/hold of broken level inside entry zone"
            sig.invalidation = float(lvl(n - 1) - d * 0.25 * A)
            sig.stop, sig.stop_method, sig.target, sig.target_method, sig.rr = float(stop), sm, float(target), tm, rr
            sig.expiry = self.expiry(t, int(p["expiry_bars"]))
            touches = sum(1 for s in t.sr if abs(s["level"] - L0) <= 0.5 * A)
            comp_before = t.df.atr14.iloc[max(0, j - 5)] / t.df.atr50.iloc[max(0, j - 5)] if np.isfinite(t.df.atr50.iloc[max(0, j - 5)]) else np.nan
            lq = 0.4 * (touches > 0) + 0.3 * (np.isfinite(comp_before) and comp_before < 1.0) + 0.3 * (body >= 0.5)
            sig.components = {
                "trend_mtf": mtf_alignment(ctx, t.tf, direction),
                "structure": (directional(t.structure_bias, direction), f"structure bias {t.structure_bias}"),
                "volume": volume_component(rv, p["min_rvol"]),
                "level_quality": (float(lq), f"S/R touches {touches}, pre-breakout ATR ratio {comp_before:.2f}, body {body * 100:.0f}%"),
                "momentum": momentum_score(t, direction),
                "relative_strength": rs_score(ctx, t, direction),
                "risk_reward": rr_score(rr),
            }
            out.append(sig)
        return out
