"""Intraday strategy (5m/15m/30m; 1H allowed): Opening Range Breakout, VWAP pullback, VWAP-reclaim reversal.

Rules use ONLY the current session's completed bars. VWAP rules are evaluated only when session VWAP is valid
(complete volume); otherwise the condition is 'not evaluable' and, being mandatory, the setup is rejected.
Session filter: no signals before session_start or after session_end (exchange local time).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..analysis.context import TFAnalysis
from .base import (Condition, Signal, Strategy, StrategyContext, momentum_score, mtf_alignment, risk_reward,
                   rr_score, rs_score, sgn)


def _hm(s: str) -> tuple[int, int]:
    h, m = s.split(":")
    return int(h), int(m)


class IntradayStrategy(Strategy):
    name = "intraday"
    title = "Intraday"

    def _session(self, t: TFAnalysis) -> pd.DataFrame:
        last_day = t.df.index[-1].date()
        return t.df[t.df.index.date == last_day]

    def _session_ok(self, t: TFAnalysis) -> tuple[bool, str]:
        ts = t.df.index[-1]
        s0, s1 = _hm(self.p["session_start"]), _hm(self.p["session_end"])
        ok = s0 <= (ts.hour, ts.minute) <= s1
        return ok, f"bar {ts.strftime('%H:%M')} (allowed {self.p['session_start']}-{self.p['session_end']})"

    def _finish(self, sig, ctx, t, direction, entry, stop, target, tm, rv):
        p = self.p
        rr = risk_reward(entry, stop, target, direction)
        sig.conditions.append(Condition("risk/reward", None if rr is None else rr >= p["min_rr"], True, "NA" if rr is None else f"{rr:.2f}"))
        sig.stop, sig.invalidation, sig.target, sig.target_method, sig.rr = float(stop), float(stop), float(target), tm, rr
        sig.expiry = self.expiry(t, int(p["expiry_bars"])) + "; intraday signals expire at session end"
        sig.warnings.append("Intraday: Yahoo data is delayed - not suitable for live execution timing")
        sig.components.update({
            "trend_mtf": mtf_alignment(ctx, t.tf, direction),
            "momentum": momentum_score(t, direction),
            "relative_strength": rs_score(ctx, t, direction),
            "risk_reward": rr_score(rr),
            "volume": (None, "RVOL NA") if rv is None or not np.isfinite(rv) else (float(np.clip((rv - 0.8) / (1.5 * p["min_rvol"] - 0.8), 0, 1)), f"RVOL {rv:.2f}"),
        })
        return sig

    def evaluate_tf(self, ctx: StrategyContext, t: TFAnalysis, direction: str) -> list[Signal]:
        if t.tf in ("1D", "1W"):
            return []
        p, A, d = self.p, t.atr, sgn(direction)
        ses = self._session(t)
        if len(ses) < 2:
            return []
        out: list[Signal] = []
        sess_ok, sess_txt = self._session_ok(t)
        C = float(ses.Close.iloc[-1])
        vw = ses.vwap.iloc[-1] if "vwap" in ses else np.nan
        vwap_ok = bool(np.isfinite(vw))

        # ---- Opening Range Breakout
        oh, om = _hm(self.cfg["market"]["session_open"])
        or_end = ses.index[0].normalize() + pd.Timedelta(hours=oh, minutes=om + int(p["opening_range_minutes"]))
        orng = ses[ses.index < or_end]
        if len(orng) and len(ses) > len(orng):
            orh, orl = float(orng.High.max()), float(orng.Low.min())
            lvl = orh if d > 0 else orl
            after = ses.iloc[len(orng):]
            cross = [k for k in range(len(after)) if (after.Close.iloc[k] - lvl) * d > 0]
            if cross and cross[0] >= len(after) - 3:
                k = cross[0]
                sig = self.new_signal(ctx, t, "Intraday: Opening Range Breakout", direction)
                sig.signal_time = str(after.index[k])[:16]
                c = sig.conditions
                c.append(Condition("session time filter", sess_ok, True, sess_txt))
                c.append(Condition("completed close beyond opening range", True, True, f"OR {orl:.2f}-{orh:.2f}, close {after.Close.iloc[k]:.2f}"))
                c.append(Condition("still holding beyond OR", (C - lvl) * d > 0, True, f"close {C:.2f}"))
                rv = after.rvol.iloc[k]
                rv = float(rv) if np.isfinite(rv) else None
                c.append(Condition("relative volume", None if rv is None else rv >= p["min_rvol"], True, "RVOL NA" if rv is None else f"RVOL {rv:.2f}"))
                c.append(Condition("VWAP side agrees", None if not vwap_ok else (C - vw) * d > 0, False, "VWAP NA" if not vwap_ok else f"VWAP {vw:.2f}"))
                stop = (orl if d > 0 else orh)
                if abs(C - stop) > 2 * A:
                    stop = (orh + orl) / 2
                sig.entry_zone = tuple(sorted((lvl, lvl + d * 0.3 * A)))
                sig.entry_condition = "hold beyond opening range"
                sig.stop_method = "Opposite side of opening range (mid-range if > 2 ATR)"
                sig.components["structure"] = (1.0 if (C - lvl) * d > 0 else 0.0, "holding OR breakout")
                sig.components["level_quality"] = (float(np.clip(1 - (orh - orl) / (3 * A), 0, 1)), f"OR width {(orh - orl) / A:.1f} ATR")
                out.append(self._finish(sig, ctx, t, direction, C, stop, C + d * p["target_r"] * abs(C - stop), f"{p['target_r']}R projection", rv))

        # ---- VWAP pullback (trend day continuation)
        sig = self.new_signal(ctx, t, "Intraday: VWAP Pullback", direction)
        c = sig.conditions
        c.append(Condition("session time filter", sess_ok, True, sess_txt))
        c.append(Condition("valid session VWAP", vwap_ok if vwap_ok else None, True, f"VWAP {vw:.2f}" if vwap_ok else "VWAP NA (volume incomplete)"))
        if vwap_ok:
            side = ((ses.Close - ses.vwap) * d > 0).mean()
            c.append(Condition("session trend vs VWAP", side >= 0.6, True, f"{side * 100:.0f}% of session bars on {direction} side"))
            ext = ses.Low.iloc[-3:].min() if d > 0 else ses.High.iloc[-3:].max()
            touched = (ext - vw) * d <= p["vwap_touch_atr"] * A
            c.append(Condition("pullback touched VWAP", bool(touched), True, f"extreme {ext:.2f} vs VWAP {vw:.2f}"))
            c.append(Condition("close back on trend side", (C - vw) * d > 0, True, f"close {C:.2f}"))
            rv = ses.rvol.iloc[-1]
            stop = ext - d * 0.2 * A
            sig.entry_zone = tuple(sorted((float(vw), float(vw + d * 0.3 * A))))
            sig.entry_condition = "hold on trend side of VWAP"
            sig.stop_method = "Pullback extreme ± 0.2 ATR"
            sig.components["structure"] = (float(side), "session time on trend side of VWAP")
            sig.components["level_quality"] = (1.0 if touched else 0.0, "VWAP touch")
            if touched:
                out.append(self._finish(sig, ctx, t, direction, C, stop, C + d * p["target_r"] * abs(C - stop),
                                        f"{p['target_r']}R projection", float(rv) if np.isfinite(rv) else None))

        # ---- VWAP reclaim reversal after sweeping the session extreme
        if vwap_ok and len(ses) >= 6:
            sess_ext_i = int(np.argmin(ses.Low.to_numpy())) if d > 0 else int(np.argmax(ses.High.to_numpy()))
            prior_side = ((ses.Close.iloc[:-1] - ses.vwap.iloc[:-1]) * d < 0).mean()
            reclaimed = (C - vw) * d > 0 and (ses.Close.iloc[-2] - ses.vwap.iloc[-2]) * d <= 0
            if reclaimed:
                sig = self.new_signal(ctx, t, "Intraday: VWAP Reclaim Reversal", direction)
                c = sig.conditions
                c.append(Condition("session time filter", sess_ok, True, sess_txt))
                c.append(Condition("prior session bias opposite", prior_side >= 0.6, True, f"{prior_side * 100:.0f}% bars on opposite side"))
                c.append(Condition("fresh session extreme (sweep)", sess_ext_i >= len(ses) - 6, True, f"session {'low' if d > 0 else 'high'} at {str(ses.index[sess_ext_i])[11:16]}"))
                c.append(Condition("VWAP reclaimed on close", True, True, f"close {C:.2f} vs VWAP {vw:.2f}"))
                ext = float(ses.Low.iloc[sess_ext_i] if d > 0 else ses.High.iloc[sess_ext_i])
                stop = ext - d * 0.2 * A
                sig.status = "candidate"
                sig.warnings.append("Counter-trend intraday reversal")
                sig.entry_zone = tuple(sorted((float(vw), C)))
                sig.entry_condition = "hold above/below VWAP after reclaim"
                sig.stop_method = "Session extreme ± 0.2 ATR"
                sig.components["structure"] = (0.6, "VWAP reclaim after session extreme")
                sig.components["level_quality"] = (0.6, "session extreme sweep")
                rv = ses.rvol.iloc[-1]
                out.append(self._finish(sig, ctx, t, direction, C, stop, C + d * p["target_r"] * abs(C - stop),
                                        f"{p['target_r']}R projection", float(rv) if np.isfinite(rv) else None))
        return out
