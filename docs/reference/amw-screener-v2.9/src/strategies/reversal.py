"""Reversal strategy, independent long/short rules.

Mandatory: prior opposite trend or extreme (trend score <= prior_trend_max, opposite structure trend,
or RSI <=30/>=70 in the trigger window); at least `min_triggers` independent triggers among
{liquidity sweep, S/R or zone rejection, failed breakdown/breakout, confirmed double bottom/top or (inverse) H&S,
RSI divergence, reversal candle}; R:R >= min_rr; CHoCH/MSS if require_choch.
Without a CHoCH the signal is an EARLY CANDIDATE, never a confirmed reversal.
"""
from __future__ import annotations

import numpy as np

from ..analysis.context import TFAnalysis, rsi_divergence
from .base import (Condition, Signal, Strategy, StrategyContext, momentum_score, mtf_alignment, risk_reward,
                   rr_score, rs_score, sgn)

BULL_CANDLES = {"Hammer", "Bullish Engulfing", "Morning Star"}
BEAR_CANDLES = {"Shooting Star", "Bearish Engulfing", "Evening Star"}


class ReversalStrategy(Strategy):
    name = "reversal"
    title = "Reversal"

    def evaluate_tf(self, ctx: StrategyContext, t: TFAnalysis, direction: str) -> list[Signal]:
        p, n, A, d = self.p, t.n, t.atr, sgn(direction)
        df = t.df
        C = float(df.Close.iloc[-1])
        tb = int(p["trigger_bars"])
        rsi_win = df.rsi14.iloc[-tb:]
        extreme = bool((rsi_win <= 30).any()) if d > 0 else bool((rsi_win >= 70).any())
        prior = (t.trend_score is not None and t.trend_score * d <= p["prior_trend_max"]) or \
                t.structure_trend == ("down" if d > 0 else "up") or extreme
        if not prior:
            return []
        sig = self.new_signal(ctx, t, "Reversal", direction)
        cond = sig.conditions
        cond.append(Condition("prior opposite trend or extreme", True, True,
                              f"trend {t.trend_score if t.trend_score is None else round(t.trend_score, 2)}, structure {t.structure_trend}, RSI extreme {extreme}"))
        trig: list[str] = []
        sw = [s for s in t.liquidity["sweeps"] if s["i"] >= n - tb and s["bias"] == direction]
        if sw:
            trig.append(f"{sw[-1]['side']} liquidity sweep @ {sw[-1]['level']:.2f} ({t.time(sw[-1]['i'])})")
        ext = float(df.Low.iloc[-tb:].min()) if d > 0 else float(df.High.iloc[-tb:].max())
        lvls = [s["level"] for s in t.sr if s["role"] == ("support" if d > 0 else "resistance")]
        lvls += [z["bottom"] if d > 0 else z["top"] for z in t.zones if z["kind"] == ("demand" if d > 0 else "supply")]
        rej = [lv for lv in lvls if abs(ext - lv) <= 0.25 * A and (C - lv) * d > 0]
        if rej:
            trig.append(f"rejection at {'support/demand' if d > 0 else 'resistance/supply'} {rej[0]:.2f}")
        opp = t.lows() if d > 0 else t.highs()
        if opp:
            lv = opp[-1].price
            closes = df.Close.iloc[max(opp[-1].confirmed_at + 1, n - tb):]
            if ((closes - lv) * d < 0).any() and (C - lv) * d > 0:
                trig.append(f"failed {'breakdown' if d > 0 else 'breakout'} of {lv:.2f} (trap)")
        pats = [pt for pt in t.patterns if pt["bias"] == direction and pt["pattern"] in
                ("Double Bottom", "Triple Bottom", "Inverse Head and Shoulders", "Double Top", "Triple Top", "Head and Shoulders")
                and pt["status"] == "confirmed"]
        if pats:
            trig.append(f"{pats[0]['pattern']} confirmed (neckline {pats[0]['neckline']:.2f})")
        div = rsi_divergence(t, direction)
        if div:
            trig.append(div)
        cn = [c for c in t.candles if c["i"] >= n - 2 and c["pattern"] in (BULL_CANDLES if d > 0 else BEAR_CANDLES) and c["status"] != "failed"]
        if cn:
            trig.append(f"{cn[-1]['pattern']} [{cn[-1]['status']}]")
        cond.append(Condition("independent reversal triggers", len(trig) >= p["min_triggers"], True,
                              f"{len(trig)} (min {p['min_triggers']}): " + ("; ".join(trig) or "none")))
        ch = [e for e in t.events if e.type == "CHoCH" and e.direction == direction and e.i >= n - 10]
        cond.append(Condition("CHoCH / MSS confirmation", bool(ch), bool(p["require_choch"]),
                              f"CHoCH @ {ch[-1].level:.2f} ({t.time(ch[-1].i)})" if ch else "no CHoCH yet - early candidate"))
        if not ch:
            sig.status = "candidate"
            sig.warnings.append("Early reversal candidate - structure not yet shifted")
        adx = df.adx
        exh = bool(np.isfinite(adx.iloc[-1]) and adx.iloc[-10:].max() > 25 and adx.iloc[-1] < adx.iloc[-5])
        stretch = df.dist_ema50_atr.iloc[-tb:].abs().max()
        cond.append(Condition("trend exhaustion", exh or (np.isfinite(stretch) and stretch > 2), False,
                              f"ADX falling from >25: {exh}; max distance from EMA50 {stretch:.1f} ATR" if np.isfinite(stretch) else f"ADX falling: {exh}"))
        stop = ext - d * p["stop_buffer_atr"] * A
        tgt_sw = [s.price for s in (t.highs() if d > 0 else t.lows()) if (s.price - C) * d > 0.5 * A]
        e50 = df.ema50.iloc[-1]
        if tgt_sw:
            target, tm = (min(tgt_sw) if d > 0 else max(tgt_sw)), "Structure: nearest opposing swing"
        elif np.isfinite(e50) and (e50 - C) * d > 0.5 * A:
            target, tm = float(e50), "Mean reversion: EMA50"
        else:
            target, tm = C + d * p["target_r"] * abs(C - stop), f"{p['target_r']}R projection"
        rr = risk_reward(C, stop, target, direction)
        cond.append(Condition("risk/reward", None if rr is None else rr >= p["min_rr"], True, "NA" if rr is None else f"{rr:.2f} (min {p['min_rr']})"))
        sig.entry_zone = tuple(sorted((C, C - d * 0.3 * A)))
        sig.entry_condition = "after confirmation; CHoCH strengthens the case"
        sig.invalidation, sig.stop, sig.stop_method = float(stop), float(stop), f"Structure: reversal extreme ± {p['stop_buffer_atr']} ATR"
        sig.target, sig.target_method, sig.rr = float(target), tm, rr
        sig.expiry = self.expiry(t, int(p["expiry_bars"]))
        rv = df.rvol.iloc[-tb:].max()
        htf, htf_txt = mtf_alignment(ctx, t.tf, direction, include_self=False)
        sig.components = {
            "trend_mtf": (htf, f"higher TF only: {htf_txt}"),
            "structure": (1.0 if ch else 0.4 if sw else 0.0, "CHoCH" if ch else "sweep only" if sw else "no structure shift"),
            "volume": (None, "RVOL NA") if not np.isfinite(rv) else (float(np.clip((rv - 0.8) / 1.5, 0, 1)), f"max RVOL in window {rv:.2f}"),
            "level_quality": (float(min(1.0, len(trig) / 4)), f"{len(trig)} triggers"),
            "momentum": (1.0, div) if div else momentum_score(t, direction),
            "relative_strength": rs_score(ctx, t, direction),
            "risk_reward": rr_score(rr),
        }
        return [sig]
