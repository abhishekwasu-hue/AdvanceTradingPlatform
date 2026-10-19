"""Strategy registry and evaluation entry point."""
from __future__ import annotations

from .base import Signal, Strategy, StrategyContext
from .breakout import BreakoutStrategy
from .intraday import IntradayStrategy
from .pullback import PullbackStrategy
from .reversal import ReversalStrategy
from .swing import SwingStrategy
from .trend import TrendContinuationStrategy

REGISTRY: dict[str, type[Strategy]] = {
    "breakout": BreakoutStrategy,
    "pullback": PullbackStrategy,
    "reversal": ReversalStrategy,
    "trend_continuation": TrendContinuationStrategy,
    "intraday": IntradayStrategy,
    "swing": SwingStrategy,
}


def enabled_strategies(cfg: dict, only: list[str] | None = None) -> list[Strategy]:
    out = []
    for name, cls in REGISTRY.items():
        s = cfg["strategies"].get(name)
        if s and s.get("enabled", True) and (not only or name in only):
            out.append(cls(cfg))
    return out


def required_timeframes(strategies: list[Strategy]) -> list[str]:
    from ..config import TIMEFRAMES
    need = {"1D"}
    for s in strategies:
        need.update(s.timeframes)
        if s.name == "swing":
            need.add("1W")
    return [t for t in TIMEFRAMES if t in need]


def evaluate_all(ctx: StrategyContext, strategies: list[Strategy]) -> list[Signal]:
    out: list[Signal] = []
    for s in strategies:
        out.extend(s.evaluate(ctx))
    return out
