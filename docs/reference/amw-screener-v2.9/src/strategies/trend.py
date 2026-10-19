"""Trend-continuation strategy, independent long/short rules.

Mandatory: EMA alignment (close>EMA20>EMA50>EMA200 for longs; EMA200 omitted only if unavailable, noted),
structure bias >= min_structure_bias (HH/HL or LH/LL + last break), ADX >= min_adx with DI agreement, R:R >= min_rr.
Optional evidence: consolidation (ATR compression) followed by a close beyond the consolidation range,
relative strength vs benchmark.
"""
from __future__ import annotations

import numpy as np

from ..analysis.context import TFAnalysis
from .base import (Condition, Signal, Strategy, StrategyContext, directional, momentum_score, mtf_alignment,
                   risk_reward, rr_score, rs_score, sgn)


class TrendContinuationStrategy(Strategy):
    name = "trend_continuation"
    title = "Trend Continuation"

    def evaluate_tf(self, ctx: StrategyContext, t: TFAnalysis, direction: str) -> list[Signal]:
        p, A, d = self.p, t.atr, sgn(direction)
        df, L = t.df, t.df.iloc[-1]
        if t.trend_score is None or t.trend_score * d < 0.5:
            return []
        sig = self.new_signal(ctx, t, "Trend Continuation", direction)
        cond = sig.conditions
        chain = [L.Close, L.ema20, L.ema50] + ([L.ema200] if np.isfinite(L.ema200) else [])
        aligned = all(np.isfinite(x) for x in chain) and all((a - b) * d > 0 for a, b in zip(chain, chain[1:]))
        e200_txt = ((">" if d > 0 else "<") + "EMA200") if np.isfinite(L.ema200) else " (EMA200 NA)"
        cond.append(Condition("EMA alignment", aligned, True,
                              ("close>EMA20>EMA50" if d > 0 else "close<EMA20<EMA50") + e200_txt))
        sb = t.structure_bias
        cond.append(Condition("structure intact", None if sb is None else sb * d >= p["min_structure_bias"], True, f"structure bias {sb}"))
        adx_ok = None if not np.isfinite(L.adx) else (L.adx >= p["min_adx"] and (L.plus_di - L.minus_di) * d > 0)
        cond.append(Condition("ADX / DI strength", adx_ok, True,
                              "NA" if not np.isfinite(L.adx) else f"ADX {L.adx:.1f} (min {p['min_adx']}), +DI {L.plus_di:.1f} / -DI {L.minus_di:.1f}"))
        cb = int(p["consolidation_bars"])
        win = df.iloc[-cb - 1:-1]
        ratio = (win.atr14 / win.atr50).min() if win.atr50.notna().any() else np.nan
        box = win.High.max() if d > 0 else win.Low.min()
        cont = bool(np.isfinite(ratio) and ratio < 1.0 and (L.Close - box) * d > 0)
        cond.append(Condition("consolidation then continuation", cont, False,
                              f"min ATR14/ATR50 {ratio:.2f}, close vs {cb}-bar {'high' if d > 0 else 'low'} {box:.2f}" if np.isfinite(ratio) else "NA"))
        rs, rs_txt = rs_score(ctx, t, direction)
        cond.append(Condition("relative strength vs benchmark", None if rs is None else rs > 0.5, False, rs_txt))
        sw = t.lows() if d > 0 else t.highs()
        stop = (sw[-1].price - d * 0.2 * A) if sw and (L.Close - sw[-1].price) * d > 0 else L.Close - d * 2 * A
        target = L.Close + d * p["target_r"] * abs(L.Close - stop)
        rr = risk_reward(float(L.Close), float(stop), float(target), direction)
        cond.append(Condition("risk/reward", None if rr is None else rr >= p["min_rr"], True, "NA" if rr is None else f"{rr:.2f}"))
        sig.entry_zone = tuple(sorted((float(L.ema20), float(L.Close))))
        sig.entry_condition = "on strength or shallow dip toward EMA20"
        sig.invalidation, sig.stop = float(stop), float(stop)
        sig.stop_method = "Structure: last higher-low / lower-high ± 0.2 ATR (fallback 2 ATR)"
        sig.target, sig.target_method, sig.rr = float(target), f"{p['target_r']}R projection", rr
        sig.expiry = self.expiry(t, int(p["expiry_bars"]))
        rv = L.rvol
        sig.components = {
            "trend_mtf": mtf_alignment(ctx, t.tf, direction),
            "structure": (directional(sb, direction), f"structure bias {sb}"),
            "volume": (None, "RVOL NA") if not np.isfinite(rv) else (float(np.clip((rv - 0.8) / 1.2, 0, 1)), f"RVOL {rv:.2f}"),
            "level_quality": (1.0 if cont else 0.4, "consolidation breakout" if cont else "no fresh consolidation breakout"),
            "momentum": momentum_score(t, direction),
            "relative_strength": (rs, rs_txt),
            "risk_reward": rr_score(rr),
        }
        return [sig]
