"""Point-in-time windows for the backtest engines (backtest realism 1 + 3).

Every frame is indexed by bar START (the base candles, and `resample_ohlc`'s start labels). A bar is known only once
it has closed, at start + its length. The engines decide at the close of the primary bar, so at that moment a
timeframe shows exactly the bars whose end is at or before the decision time - never the forming higher-timeframe
bar (the old `index <= current_time` showed an HTF bar from its first minute, with the close of minutes not yet
traded). This is the same rule the declarative strategies apply to their own HTF operands
(`declarative.Operand._higher_timeframe`).

The cursor finds that cut with one binary search per timeframe and returns `iloc` slices (views), instead of a
boolean mask over the whole frame on every bar (O(n) per bar, O(n^2) per run).
"""
from __future__ import annotations

from typing import Dict, Iterable

import pandas as pd

from app.strategy_engine.declarative import tf_minutes

_DAY = {"day", "1d", "d", "daily"}


def bar_length(tf: str) -> pd.Timedelta:
    """How long one bar of `tf` lasts: N minutes for "Nmin", one calendar day for daily bars."""
    if tf.strip().lower() in _DAY:
        return pd.Timedelta(days=1)
    minutes = tf_minutes(tf)
    if not minutes:
        raise ValueError(f"unknown timeframe {tf!r}")
    return pd.Timedelta(minutes=minutes)


class WindowCursor:
    """`at(decision_time)` -> {timeframe: the bars of that timeframe that had closed by then}."""

    def __init__(self, frames: Dict[str, pd.DataFrame], timeframes: Iterable[str]) -> None:
        self._frames = {tf: frames[tf] for tf in timeframes}
        # bar end = start + length; ascending because the starts are (same timezone as the frame's own index)
        self._ends = {tf: pd.DatetimeIndex(frame.index) + bar_length(tf) for tf, frame in self._frames.items()}

    def visible(self, tf: str, decision_time) -> int:
        """How many bars of `tf` had closed at `decision_time`."""
        return int(self._ends[tf].searchsorted(pd.Timestamp(decision_time), side="right"))

    def at(self, decision_time) -> Dict[str, pd.DataFrame]:
        return {tf: frame.iloc[: self.visible(tf, decision_time)] for tf, frame in self._frames.items()}


def decision_time(bar_start, primary_tf: str) -> pd.Timestamp:
    """The moment a strategy decides on a primary bar: when that bar closes."""
    return pd.Timestamp(bar_start) + bar_length(primary_tf)


__all__ = ["WindowCursor", "bar_length", "decision_time"]
