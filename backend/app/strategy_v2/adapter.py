"""SFC P0: an `atp.strategy/2` strategy as a BaseStrategy - the SAME evaluator the chart reads, for the backtest, the
PAPER worker, the user's runner and LIVE (one order path: `execute_signal_for_user`; this module never places anything).

`analyze()` READS the evaluation at the decision bar (the last closed bar given); it never re-derives state. Inside a
backtest run the evaluation is computed ONCE on the whole frame (the prefix cache) and each closed prefix reads its own
last bar, so a run costs one evaluation, not one per bar.

The Signal: entry = the signal bar's close (a reference; the fill is the engine's), stop / target only from the user's
own exit rules (DEF-0: none set = none on the signal; never a default target or R:R rule), no score claim. A stop on the
wrong side of the close is no signal ("the stop is not on the losing side") - never moved by ATP.
The entry window (his own operational setting) refuses a signal outside it. Entries-per-day, trailing and time exits act
on trades, not signals: the trade walker of P1 / the worker of P6 apply them.
"""
from __future__ import annotations

import math
from datetime import time
from typing import Any

import numpy as np
import pandas as pd

from app.core.enums import SignalDirection, StrategyCategory, grade_from_score
from app.core.models import Signal
from app.indicators.prefix_cache import whole_frame
from app.screener.nodes import TF_MINUTES
from app.strategy_engine.base import BaseStrategy
from app.strategy_v2.compile import Compiled, compile_strategy
from app.strategy_v2.evaluate import Evaluation, evaluate
from app.strategy_v2.schema import LevelRule, StrategyV2

ENGINE_LABEL: dict[str, str] = {"1m": "1min", "3m": "3min", "5m": "5min", "15m": "15min", "30m": "30min", "1h": "60min", "1d": "day"}
IST = "Asia/Kolkata"
SCORE = 70                                   # not graded (no quality claim), as the templates (TPL-Q1)
SESSION_MINUTES = 375
SAFETY_FACTOR = 3
MAX_HISTORY_DAYS = 400
WARMING = "Not enough history for this strategy yet"
NO_SIGNAL = "No signal on this bar"
OUTSIDE_WINDOW = "Outside your entry window"
BAD_STOP = "The stop is not on the losing side of the entry"
SIGNAL_TEXT = {"long": "Your long entry conditions turned true on this closed bar",
               "short": "Your short entry conditions turned true on this closed bar"}


def level(rule: LevelRule | None, ev: Evaluation, t: int, side: str, what: str) -> float | None:
    """A stop (`what` = "stop") or target ("target") price on bar t from his own rule; None when no rule or no value
    (a swing not formed yet). "premium" levels belong to the option leg (P6), not to the underlying."""
    if rule is None:
        return None
    entry = float(ev.close[t])
    away = -1.0 if (side == "long") == (what == "stop") else 1.0      # long stop / short target: below the entry
    if rule.kind == "points":
        return entry + away * float(rule.value or 0)
    if rule.kind == "pct":
        return entry * (1 + away * float(rule.value or 0) / 100)
    if rule.kind == "atr":
        atr = ev.extras.get(f"atr:{rule.atr_period}")
        v = float(atr[t]) if atr is not None else math.nan
        return None if not math.isfinite(v) else entry + away * float(rule.value or 0) * v
    if rule.kind == "swing":
        key = f"swing_low:{rule.swing_degree}" if away < 0 else f"swing_high:{rule.swing_degree}"
        series = ev.extras.get(key)
        v = float(series[t]) if series is not None else math.nan
        return v if math.isfinite(v) else None
    if rule.kind == "candle":
        k = t - 1 if rule.candle == "previous" else t
        if k < 0:
            return None
        return float(ev.low[k]) if away < 0 else float(ev.high[k])
    return None                                                         # premium: the option leg's (P6)


def in_window(strategy: StrategyV2, stamp: pd.Timestamp) -> bool:
    w = strategy.entry.window
    if w is None:
        return True
    ts = pd.Timestamp(stamp)
    local = (ts.tz_localize("UTC") if ts.tzinfo is None else ts).tz_convert(IST)
    hh, mm = (int(x) for x in w.start.split(":"))
    eh, em = (int(x) for x in w.end.split(":"))
    return time(hh, mm) <= local.time() <= time(eh, em)


class StrategyV2Strategy(BaseStrategy):
    category = StrategyCategory.INDICATOR_BASED

    def __init__(self, strategy_id: str, strategy: StrategyV2) -> None:
        if strategy.timeframe not in ENGINE_LABEL:
            raise ValueError(f"timeframe {strategy.timeframe} is not available to the backtest / bot (charts only)")
        self.id = strategy_id
        self.name = strategy.name
        self.description = strategy.notes or ""
        self.strategy = strategy
        self.compiled: Compiled = compile_strategy(strategy)
        self.label = ENGINE_LABEL[strategy.timeframe]
        self.timeframes = [self.label]
        self._cache: tuple[pd.DataFrame, str, Evaluation] | None = None
        self.computations = 0
        super().__init__()

    def min_history(self) -> dict[str, int]:
        return {self.label: self.compiled.min_bars}

    def history_bars(self) -> int:
        tf = self.strategy.timeframe
        need = 0
        for other, bars in {**self.compiled.lookback, **self.compiled.fetch_lookback}.items():
            need = max(need, math.ceil(bars * TF_MINUTES[other] / TF_MINUTES[tf]))
        return max(1, need) * SAFETY_FACTOR

    def history_days(self) -> int:
        minutes = TF_MINUTES[self.strategy.timeframe]
        bars = self.history_bars()
        sessions = bars if minutes >= SESSION_MINUTES else math.ceil(bars * minutes / SESSION_MINUTES)
        return min(MAX_HISTORY_DAYS, math.ceil(sessions * 7 / 5) + 7)

    def evaluation(self, df: pd.DataFrame, symbol: str = "S") -> tuple[Evaluation, int]:
        """The evaluation and the position of `df`'s last bar in it (one evaluation per backtest run)."""
        whole = whole_frame(df)
        if whole is not None:
            frame, length = whole
            if self._cache is None or self._cache[0] is not frame or self._cache[1] != symbol:
                self._cache = (frame, symbol, self._compute(frame, symbol))
            return self._cache[2], length - 1
        return self._compute(df, symbol), len(df) - 1

    def _compute(self, df: pd.DataFrame, symbol: str) -> Evaluation:
        self.computations += 1
        return evaluate(self.compiled, {self.strategy.timeframe: df}, symbol)

    def signal_at(self, ev: Evaluation, t: int, symbol: str) -> Signal:
        """The Signal of bar t - the chart's markers and the bot's entries read this one function."""
        stamp = pd.Timestamp(ev.index[t])
        if not bool(ev.warm[t]):
            return self.no_trade(symbol, stamp, [WARMING])
        side = "long" if ev.signal_long[t] else "short" if ev.signal_short[t] else None
        if side is None:
            return self.no_trade(symbol, stamp, [NO_SIGNAL])
        if not in_window(self.strategy, stamp):
            return self.no_trade(symbol, stamp, [OUTSIDE_WINDOW])
        ex = self.strategy.exit
        entry = float(ev.close[t])
        stop = level(ex.stop if ex else None, ev, t, side, "stop")
        target = level(ex.target if ex else None, ev, t, side, "target")
        if stop is not None and ((side == "long" and stop >= entry) or (side == "short" and stop <= entry)):
            return self.no_trade(symbol, stamp, [BAD_STOP])
        risk_reward = None
        if stop is not None and target is not None and abs(entry - stop) > 0:
            risk_reward = round(abs(target - entry) / abs(entry - stop), 2)
        provenance = {"strategy": self.id, "settings": self.compiled.params_hash, "kind": "atp.strategy/2"}
        return Signal(provenance=provenance, symbol=symbol, strategy_id=self.id, strategy_name=self.name,
                      direction=SignalDirection.LONG if side == "long" else SignalDirection.SHORT, timestamp=stamp,
                      entry=entry, stop_loss=stop, target1=target, target2=None, risk_reward=risk_reward,
                      score=SCORE, grade=grade_from_score(SCORE), reasons=[SIGNAL_TEXT[side]], timeframe_combo=self.label)

    def analyze(self, data: dict[str, pd.DataFrame], symbol: str) -> Signal:
        df = data.get(self.label)
        if df is None or len(df) == 0:
            return self.no_trade(symbol, pd.Timestamp.now(tz="UTC"), [WARMING])
        if len(df) < self.compiled.min_bars:
            return self.no_trade(symbol, df.index[-1], [WARMING])
        ev, t = self.evaluation(df, symbol)
        return self.signal_at(ev, t, symbol)

    @property
    def acts_on_exit_signal(self) -> bool:
        """His own exit conditions close an open trade (both backtest engines and the worker act on it)."""
        ex = self.strategy.exit
        return ex is not None and (ex.long_conditions is not None or ex.short_conditions is not None)

    def exit_signal(self, data: dict[str, pd.DataFrame], direction: Any, symbol: str = "S") -> bool:
        df = data.get(self.label)
        if df is None or len(df) == 0:
            return False
        ev, t = self.evaluation(df, symbol)
        is_long = direction in (SignalDirection.LONG, "LONG", "long")
        arr = ev.raw.get("exit_long" if is_long else "exit_short")
        return bool(arr[t]) if arr is not None else False


def entry_bars(ev: Evaluation) -> np.ndarray:
    """+1 / -1 on the bars a side's entry rule turned true (the chart's signal markers), 0 elsewhere."""
    return ev.signal_long.astype(np.int8) - ev.signal_short.astype(np.int8)


__all__ = ["StrategyV2Strategy", "ENGINE_LABEL", "level", "in_window", "entry_bars"]
