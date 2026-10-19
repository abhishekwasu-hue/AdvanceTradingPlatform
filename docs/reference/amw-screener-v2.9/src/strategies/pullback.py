"""Pullback strategy (trend-continuation pullbacks), independent long/short rules.

Reference levels: EMA20, EMA50, last BOS (previous breakout) level, S/R cluster, demand/supply zone,
Fibonacci 38.2-61.8 % retracement of the last confirmed impulse.
Mandatory: trend score >= min_trend_score in direction with matching structure trend; price touched a reference
within `touch_bars` and has not closed through it; not extended (> max_distance_atr from reference);
rejection confirmation (if require_rejection); R:R >= min_rr.
"""
from __future__ import annotations

import numpy as np

from ..analysis.context import TFAnalysis
from ..analysis.indicators import fib_levels
from .base import (Condition, Signal, Strategy, StrategyContext, directional, momentum_score, mtf_alignment,
                   risk_reward, rr_score, rs_score, sgn)

BULL_REJECT = {"Hammer", "Bullish Engulfing", "Morning Star"}
BEAR_REJECT = {"Shooting Star", "Bearish Engulfing", "Evening Star"}


def impulse(t: TFAnalysis, direction: str):
    """Last confirmed impulse: swing low -> later swing high (bullish) or high -> later low (bearish)."""
    hs, ls = t.highs(), t.lows()
    if direction == "bullish":
        if not hs:
            return None
        h = hs[-1]
        prior = [s for s in ls if s.i < h.i]
        return (prior[-1], h) if prior else None
    if not ls:
        return None
    l_ = ls[-1]
    prior = [s for s in hs if s.i < l_.i]
    return (prior[-1], l_) if prior else None


class PullbackStrategy(Strategy):
    name = "pullback"
    title = "Pullback"

    def evaluate_tf(self, ctx: StrategyContext, t: TFAnalysis, direction: str) -> list[Signal]:
        p, n, A, d = self.p, t.n, t.atr, sgn(direction)
        df = t.df
        C = float(df.Close.iloc[-1])
        if t.trend_score is None or t.trend_score * d < 0.25:      # cheap pre-filter; strict rule below
            return []
        sig = self.new_signal(ctx, t, "Pullback", direction)
        cond = sig.conditions
        st_ok = t.structure_trend == ("up" if d > 0 else "down")
        cond.append(Condition("trend in direction", t.trend_score * d >= p["min_trend_score"] and st_ok, True,
                              f"trend score {t.trend_score:+.2f} (min {p['min_trend_score']}), structure {t.structure_trend}"))
        refs: list[tuple[str, float]] = []
        for nm in ("ema20", "ema50"):
            v = df[nm].iloc[-1]
            if np.isfinite(v):
                refs.append((nm.upper(), float(v)))
        bos = [e for e in t.events if e.type == "BOS" and e.direction == direction]
        if bos:
            refs.append((f"prior breakout level {bos[-1].level:.2f}", float(bos[-1].level)))
        for s in t.sr:
            if (s["level"] - C) * d < 0:
                refs.append((f"{s['role']} {s['level']:.2f} ({s['touches']}x)", float(s["level"])))
        for z in t.zones:
            if z["kind"] == ("demand" if d > 0 else "supply"):
                refs.append((f"{z['kind']} zone {z['bottom']:.2f}-{z['top']:.2f}", float(z["top"] if d > 0 else z["bottom"])))
        imp = impulse(t, direction)
        depth = None
        if imp:
            a, b = imp
            fl = fib_levels(min(a.price, b.price), max(a.price, b.price), direction)
            refs.append((f"Fib 38.2-61.8% ({fl['61.8%']:.2f}-{fl['38.2%']:.2f})", float(fl["38.2%"] if d > 0 else fl["38.2%"])))
            refs.append(("Fib 61.8%", float(fl["61.8%"])))
            ext = float(df.Low.iloc[b.i:].min()) if d > 0 else float(df.High.iloc[b.i:].max())
            depth = abs(b.price - ext) / max(1e-9, abs(b.price - a.price)) * 100
        tb = int(p["touch_bars"])
        recent_ext = float(df.Low.iloc[-tb:].min()) if d > 0 else float(df.High.iloc[-tb:].max())
        touched = [(nm, v) for nm, v in refs if (recent_ext - v) * d <= 0.25 * A and (C - v) * d >= -0.25 * A]
        cond.append(Condition("touched pullback reference", bool(touched), True,
                              ", ".join(nm for nm, _ in touched) if touched else "no reference touched in last bars"))
        best = min(touched, key=lambda x: abs(C - x[1])) if touched else None
        dist = abs(C - best[1]) / A if best else None
        cond.append(Condition("not extended from reference", None if dist is None else dist <= p["max_distance_atr"], True,
                              "NA" if dist is None else f"{dist:.2f} ATR (max {p['max_distance_atr']})"))
        cond.append(Condition("pullback depth in range", None if depth is None else p["min_depth_pct"] <= depth <= p["max_depth_pct"], False,
                              "impulse NA" if depth is None else f"{depth:.1f}% of impulse ({p['min_depth_pct']}-{p['max_depth_pct']})"))
        names = BULL_REJECT if d > 0 else BEAR_REJECT
        rej = [c for c in t.candles if c["i"] >= n - 2 and c["pattern"] in names and c["status"] != "failed"]
        e20 = df.ema20
        reclaim = bool(np.isfinite(e20.iloc[-1]) and (df.Close.iloc[-1] - e20.iloc[-1]) * d > 0
                       and ((df.Close.iloc[-tb - 1:-1] - e20.iloc[-tb - 1:-1]) * d < 0).any())
        rtxt = ", ".join(f"{c['pattern']} [{c['status']}] {t.time(c['i'])}" for c in rej) or ("EMA20 reclaim" if reclaim else "none")
        cond.append(Condition("rejection confirmation", bool(rej) or reclaim, bool(p["require_rejection"]), rtxt))
        pb_start = imp[1].i if imp else n - tb
        pb_rv = df.rvol.iloc[pb_start + 1:-1]
        contraction = None if pb_rv.dropna().empty else float(pb_rv.mean()) < 1.0
        trig_rv = df.rvol.iloc[-1]
        cond.append(Condition("volume contraction in pullback", contraction, False,
                              "NA" if contraction is None else f"avg RVOL {pb_rv.mean():.2f}"))
        stop = recent_ext - d * p["stop_buffer_atr"] * A
        prior_ext = imp[1].price if imp else None
        if prior_ext is not None and (prior_ext - C) * d > 0:
            target, tm = prior_ext, f"Structure: prior swing {'high' if d > 0 else 'low'} {prior_ext:.2f}"
        else:
            target, tm = C + d * p["target_r"] * abs(C - stop), f"{p['target_r']}R projection"
        rr = risk_reward(C, stop, target, direction)
        cond.append(Condition("risk/reward", None if rr is None else rr >= p["min_rr"], True, "NA" if rr is None else f"{rr:.2f} (min {p['min_rr']})"))
        sig.entry_zone = tuple(sorted((best[1], best[1] + d * 0.3 * A))) if best else None
        sig.entry_condition = "hold above reference after rejection" if d > 0 else "hold below reference after rejection"
        sig.invalidation = float(stop)
        sig.stop, sig.stop_method = float(stop), f"Structure: pullback extreme ± {p['stop_buffer_atr']} ATR"
        sig.target, sig.target_method, sig.rr = float(target), tm, rr
        sig.expiry = self.expiry(t, int(p["expiry_bars"]))
        vol_s = None if contraction is None and not np.isfinite(trig_rv) else (0.5 * bool(contraction) + 0.5 * (np.isfinite(trig_rv) and trig_rv >= 1.0))
        lq = (0.5 if len(touched) >= 2 else 0.3 if touched else 0) + (0.3 if depth is not None and 38.2 <= depth <= 61.8 else 0) + (0.2 if rej else 0)
        sig.components = {
            "trend_mtf": mtf_alignment(ctx, t.tf, direction),
            "structure": (directional(t.structure_bias, direction), f"structure bias {t.structure_bias}"),
            "volume": (vol_s, f"pullback contraction {contraction}, trigger RVOL {trig_rv:.2f}" if np.isfinite(trig_rv) else "trigger RVOL NA"),
            "level_quality": (float(lq), f"{len(touched)} confluent references; depth {depth if depth is None else round(depth, 1)}%"),
            "momentum": momentum_score(t, direction),
            "relative_strength": rs_score(ctx, t, direction),
            "risk_reward": rr_score(rr),
        }
        return [sig]
