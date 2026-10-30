"""Strategy templates: the parity harness - scan = backtest = live (owner order section 8, S11, R3, R6).

Every path that runs a template reads the SAME signal series (`engine.compute`); what can differ between them is what
they are given and how they ask. The harness runs each path on the same fixed trailing window W of closed bars and
records what it answered on every bar it asked about:

* scan      - the recipe evaluated by the ScreenQL runtime on the window (`engine.compute`, ScreenQL timeframes), the
              answer a "Run as scan" reads on its bar;
* backtest  - `run_backtest` with the adapter (inside the engine's prefix cache, the WindowCursor's prefixes);
* options   - `run_option_backtest` with the adapter (the same signals; the user's spread construction on top);
* worker    - the deployment worker's call: `analyze()` outside a run, on the closed bars up to each bar (the worker
              fetches the window and decides on its last closed bar).

`compare` lists every bar where a path's answer (side, invalidation, setup instance) differs from the scan series.
Parity compares SIGNAL SERIES, not trades (R3): a backtest asks only while it is flat, so it is compared on the bars it
asked about, and every trade it opened must sit on a scan signal bar with that bar's invalidation as its stop.

`fetch_bars` is R6: the signal-timeframe bars a deployment / scan must fetch so every timeframe the recipe reads has
its history (the context frames converted to signal bars), times a safety factor.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from app.core.enums import SignalDirection
from app.core.models import RiskConfig, Signal
from app.screener import nodes as n
from app.strategy_templates import engine
from app.strategy_templates.adapter import TemplateStrategy
from app.strategy_templates.compile import Recipe

SAFETY_FACTOR = 3            # R6: history fetched = the recipe's needs x this (indicator recursions settle well inside it)
PATHS = ("scan", "backtest", "options", "worker")


@dataclass(frozen=True)
class Answer:
    """What a path said on one bar: +1 / -1 / 0, the invalidation on a signal bar, the setup instance."""
    direction: int
    invalidation: Optional[float] = None
    instance: Optional[str] = None


def needs(recipe: Recipe) -> Dict[str, int]:
    """R6 (REV-S3): the bars every timeframe the recipe reads needs before it can answer - its refusal lookback and its
    fetch lookback (a function that reads as missing on a shorter frame: DowTrend at degree 1 on its first 250 bars, so
    `DowTrend(1)@1d` needs 250 daily bars although it refuses nothing)."""
    out = {tf: int(b) for tf, b in recipe.lookback.items()}
    for tf, bars in recipe.fetch_lookback.items():
        out[tf] = max(out.get(tf, 0), int(bars))
    out[recipe.settings.timeframe] = max(out.get(recipe.settings.timeframe, 0), recipe.min_bars)
    return out


def fetch_bars(recipe: Recipe, safety: int = SAFETY_FACTOR) -> int:
    """R6: the signal-timeframe bars to fetch - the most any timeframe the recipe reads needs (`needs`: a context
    timeframe's bars converted to signal bars, a session-sized timeframe by the 375-minute session), times `safety`."""
    tf = recipe.settings.timeframe
    need = recipe.min_bars
    for other, bars in needs(recipe).items():
        need = max(need, bars if other == tf else math.ceil(bars * n.TF_MINUTES[other] / n.TF_MINUTES[tf]))
    return int(need * safety)


def need_sessions(recipe: Recipe) -> int:
    """R6 (REV-S3): the trading sessions of history the recipe reads before its first answer, without the safety factor
    (what a user is told a test window lacks): each timeframe's bars over its bars per 375-minute session."""
    most = 1
    for tf, bars in needs(recipe).items():
        minutes = n.TF_MINUTES[tf]
        sessions = bars * minutes // 375 if minutes >= 375 else math.ceil(bars / math.ceil(375 / minutes))
        most = max(most, int(sessions))
    return most


def scan_answers(recipe: Recipe, window: pd.DataFrame) -> Dict[pd.Timestamp, Answer]:
    """The scan path: the recipe's series over the window, one answer per bar."""
    series = engine.compute(recipe, {recipe.settings.timeframe: window})
    out: Dict[pd.Timestamp, Answer] = {}
    for t, ts in enumerate(series.index):
        d = int(series.direction[t])
        out[pd.Timestamp(ts)] = Answer(d, float(series.invalidation[t]), series.instance_id(t)) if d else Answer(0)
    return out


def _answer(signal: Signal) -> Answer:
    if not signal.is_tradeable:
        return Answer(0)
    d = 1 if signal.direction == SignalDirection.LONG else -1
    setup = (signal.provenance or {}).get("setup")          # TPL-Q2: the provenance lives on the signal, not in its reasons
    return Answer(d, float(signal.stop_loss) if signal.stop_loss is not None else None, setup)


class _Recorder:
    """The adapter, with every answer it gives recorded by the decision bar's start time."""

    def __init__(self, strategy: TemplateStrategy) -> None:
        self.strategy = copy.deepcopy(strategy)
        self.answers: Dict[pd.Timestamp, Answer] = {}
        inner = self.strategy.analyze

        def analyze(data: Dict[str, pd.DataFrame], symbol: str) -> Signal:
            signal = inner(data, symbol)
            df = data.get(self.strategy.label)
            if df is not None and len(df):
                self.answers[pd.Timestamp(df.index[-1])] = _answer(signal)
            return signal
        self.strategy.analyze = analyze          # type: ignore[method-assign]


def backtest_answers(strategy: TemplateStrategy, window: pd.DataFrame, symbol: str,
                     risk: Optional[RiskConfig] = None) -> Tuple[Dict[pd.Timestamp, Answer], List[Tuple[pd.Timestamp, int, float]]]:
    """The backtest path: the answers `run_backtest` got, and its trades as (entry bar, side, stop)."""
    from app.backtest.engine import run_backtest
    rec = _Recorder(strategy)
    result = run_backtest(rec.strategy, window, symbol, rec.strategy.label, risk or RiskConfig(capital=1_000_000, risk_per_trade_pct=0.5))
    trades = [(pd.Timestamp(t.entry_time), 1 if t.direction == SignalDirection.LONG else -1, float(t.stop_loss)) for t in result.trades]
    return rec.answers, trades


def options_answers(strategy: TemplateStrategy, window: pd.DataFrame, symbol: str, config: object,
                    risk: Optional[RiskConfig] = None) -> Dict[pd.Timestamp, Answer]:
    """The options-backtest path: the answers `run_option_backtest` got (its trades are option structures)."""
    from app.backtest.options_engine import OptionBacktestConfig, run_option_backtest
    assert isinstance(config, OptionBacktestConfig)
    rec = _Recorder(strategy)
    run_option_backtest(rec.strategy, window, symbol, rec.strategy.label, risk or RiskConfig(capital=1_000_000, risk_per_trade_pct=0.5),
                        config)
    return rec.answers


def worker_answers(strategy: TemplateStrategy, window: pd.DataFrame, symbol: str,
                   bars: Optional[List[int]] = None) -> Dict[pd.Timestamp, Answer]:
    """The worker path: `analyze()` outside a run on the window's closed bars up to each bar (all bars, or `bars`)."""
    rec = _Recorder(strategy)
    for t in (range(len(window)) if bars is None else bars):
        rec.strategy.analyze({rec.strategy.label: window.iloc[:t + 1]}, symbol)
    return rec.answers


def _same(a: Answer, b: Answer) -> bool:
    if a.direction != b.direction or a.instance != b.instance:
        return False
    if a.invalidation is None or b.invalidation is None:
        return a.invalidation is None and b.invalidation is None
    return bool(np.isclose(a.invalidation, b.invalidation, rtol=0, atol=1e-9))


def compare(reference: Dict[pd.Timestamp, Answer], answers: Dict[pd.Timestamp, Answer], path: str) -> List[str]:
    """Every bar where `answers` differs from the reference series (a bar the reference has no answer for counts too)."""
    out: List[str] = []
    for ts, got in sorted(answers.items()):
        want = reference.get(ts)
        if want is None:
            out.append(f"{path}: {ts} is not a bar of the window")
        elif not _same(want, got):
            out.append(f"{path}: {ts} answered {got}, the scan series says {want}")
    return out


def compare_trades(reference: Dict[pd.Timestamp, Answer], trades: List[Tuple[pd.Timestamp, int, float]],
                   entry_bar: Callable[[pd.Timestamp], pd.Timestamp]) -> List[str]:
    """Every backtest trade must open on a scan signal bar (`entry_bar` maps the fill time to its decision bar), on that
    signal's side, with that bar's invalidation as its stop."""
    out: List[str] = []
    for when, side, stop in trades:
        bar = entry_bar(when)
        want = reference.get(bar)
        if want is None or want.direction != side or want.invalidation is None or not np.isclose(want.invalidation, stop, atol=1e-9):
            out.append(f"trade at {when}: side {side} stop {stop} has no matching scan signal on {bar} ({want})")
    return out


__all__ = ["SAFETY_FACTOR", "PATHS", "Answer", "needs", "fetch_bars", "need_sessions", "scan_answers", "backtest_answers", "options_answers", "worker_answers",
           "compare", "compare_trades"]
