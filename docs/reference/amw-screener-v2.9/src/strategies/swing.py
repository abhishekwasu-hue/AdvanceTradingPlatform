"""Swing strategy: daily Breakout / Pullback / Reversal rule sets + weekly alignment, ATR/percentage risk gate,
holding-period assumption and overnight-gap warning. Sub-rules come from the respective strategies' parameters.
"""
from __future__ import annotations

from .base import Condition, Signal, Strategy, StrategyContext, sgn
from .breakout import BreakoutStrategy
from .pullback import PullbackStrategy
from .reversal import ReversalStrategy


class SwingStrategy(Strategy):
    name = "swing"
    title = "Swing"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.subs = [BreakoutStrategy(cfg), PullbackStrategy(cfg), ReversalStrategy(cfg)]

    def evaluate_tf(self, ctx: StrategyContext, t, direction: str) -> list[Signal]:
        p, d = self.p, sgn(direction)
        wk = ctx.get("1W")
        out = []
        for sub in self.subs:
            for sig in sub.evaluate_tf(ctx, t, direction):
                sig.strategy, sig.strategy_version = self.name, self.version
                sig.setup = f"Swing: {sig.setup}"
                if wk is None or wk.trend_score is None:
                    sig.conditions.append(Condition("weekly trend alignment", None, bool(p["weekly_must_align"]), "weekly data NA"))
                else:
                    sig.conditions.append(Condition("weekly trend alignment", wk.trend_score * d > -0.5, bool(p["weekly_must_align"]),
                                                    f"weekly {wk.trend_label} ({wk.trend_score:+.2f})"))
                if sig.stop is not None:
                    pct = abs(t.close - sig.stop) / t.close * 100
                    sig.conditions.append(Condition("stop distance within limit", pct <= p["max_stop_pct"], True,
                                                    f"{pct:.1f}% (max {p['max_stop_pct']}%)"))
                if sig.rr is not None:
                    sig.conditions.append(Condition("swing risk/reward", sig.rr >= p["min_rr"], True, f"{sig.rr:.2f} (min {p['min_rr']})"))
                lo, hi = p["holding_days"]
                sig.expiry += f"; assumed holding {lo}-{hi} sessions"
                sig.warnings.append("Overnight/gap risk: stops may fill beyond the level on gaps")
                out.append(sig)
        return out
