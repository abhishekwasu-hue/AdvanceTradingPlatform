"""SFC P0: THE evaluator - one pure function of closed bars that the chart, paper and live all read.

`evaluate(compiled, frames)` runs every condition ONCE over the whole series (the ScreenQL runtime: closed bars, a higher
timeframe's value aligned by close time, NaN never a match), combines the AND / OR trees bar by bar, and returns, per
bar of the strategy's timeframe:
* `truth[leaf]` / `values[leaf][role]`: which condition held and with what values (the chart's "why", P3);
* `raw[role]`: the tree's truth (entry_long, entry_short, exit_long, exit_short);
* `signal_long` / `signal_short`: the bars a side's entry rule TURNS true (OWNER decision OQ-1: a signal is the bar the
  rule turns true; a rule already true when the history starts has no known start, so it signals only after a bar
  where it was false, as the templates' state setups);
* `warm`: every condition's inputs have values and the validator's history exists on every timeframe read.

No look-ahead: a value on bar t reads bars <= t only (every registry function is registered causal in
app/quality/causal_cases.py), so a signal once placed never moves when more bars arrive.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Union

import numpy as np
import pandas as pd

from app.screener import nodes as n
from app.screener.registry import FIELDS, FUNCTIONS
from app.screener.runtime import SymbolData, _Eval, no_index_volume
from app.strategy_templates.engine import SPARSE_SERIES, _bool, _closed_count, _num, _num_or_category
from app.strategy_v2.compile import Compiled, Node

ROLES = ("entry_long", "entry_short", "exit_long", "exit_short")


@dataclass
class Evaluation:
    index: pd.DatetimeIndex
    truth: dict[str, np.ndarray]
    values: dict[str, dict[str, np.ndarray]]
    raw: dict[str, np.ndarray]
    signal_long: np.ndarray
    signal_short: np.ndarray
    warm: np.ndarray
    extras: dict[str, np.ndarray] = field(default_factory=dict)
    close: np.ndarray = field(default_factory=lambda: np.zeros(0))
    high: np.ndarray = field(default_factory=lambda: np.zeros(0))
    low: np.ndarray = field(default_factory=lambda: np.zeros(0))

    @property
    def warmup_bars(self) -> int:
        return int(np.argmax(self.warm)) if self.warm.any() else len(self.index)

    def signal_bars(self, side: str) -> list[int]:
        arr = self.signal_long if side == "long" else self.signal_short
        return [int(i) for i in np.flatnonzero(arr)]

    def true_leaves(self, t: int) -> list[str]:
        return [k for k, v in self.truth.items() if bool(v[t])]


def _combine(node: Union[str, Node], truth: dict[str, np.ndarray], size: int) -> np.ndarray:
    if isinstance(node, str):
        return truth[node]
    parts = [_combine(item, truth, size) for item in node.items]
    out = parts[0].copy()
    for p in parts[1:]:
        out = (out & p) if node.op == "AND" else (out | p)
    return out


def _started(ev: _Eval, ast: Any, tf: str, index: pd.Index) -> np.ndarray:
    """True from the first bar where every value series the expression reads has a value (sparse series exempt)."""
    ready = np.ones(len(index), dtype=bool)
    for node in n.walk(ast):
        leaf: Any = None
        if isinstance(node, n.Field) and node.name in FIELDS:
            leaf = node
        elif isinstance(node, n.Call):
            spec = FUNCTIONS.get(node.name)
            if spec is not None and spec.returns != "bool" and node.name not in SPARSE_SERIES:
                leaf = node
        if leaf is None:
            continue
        ready &= np.maximum.accumulate(~np.isnan(_num_or_category(ev.ev(leaf, tf), index)))
    return ready


def evaluate(compiled: Compiled, frames: dict[str, pd.DataFrame], symbol: str = "S") -> Evaluation:
    """The whole evaluation of a compiled strategy on closed bars (`frames`: ScreenQL timeframe -> bars indexed by bar
    start; a higher timeframe that is missing is resampled from the strategy's bars by the runtime). An index `symbol`'s
    0 volume is no volume data (as the screener and the templates)."""
    tf = compiled.timeframe
    bars = SymbolData(symbol, dict(frames))
    no_index_volume([bars])
    ev = _Eval(bars, tf, {}, {}, memo={})
    frame = ev.frame(tf)
    index = frame.index
    size = len(index)

    warm = np.arange(1, size + 1) >= compiled.min_bars
    for other, need in compiled.lookback.items():
        if other != tf:
            warm &= _closed_count(ev, other, tf) >= need
    truth: dict[str, np.ndarray] = {}
    values: dict[str, dict[str, np.ndarray]] = {}
    for leaf in compiled.leaves.values():
        truth[leaf.id] = _bool(ev.ev(leaf.ast, tf), index)
        values[leaf.id] = {role: _num(ev.ev(ast, tf), index) for role, ast in leaf.operands.items()}
        warm &= _started(ev, leaf.ast, tf, index)
    raw = {role: _combine(tree, truth, size) & warm for role, tree in compiled.trees.items()}

    def edge(arr: np.ndarray) -> np.ndarray:
        out = np.zeros(size, dtype=bool)
        if size > 1:
            out[1:] = arr[1:] & ~arr[:-1] & warm[:-1]
        return out

    long_raw = raw.get("entry_long", np.zeros(size, dtype=bool))
    short_raw = raw.get("entry_short", np.zeros(size, dtype=bool))
    signal_long, signal_short = edge(long_raw), edge(short_raw)
    both = signal_long & signal_short                  # both sides on one bar: neither (no side is picked for him)
    signal_long &= ~both
    signal_short &= ~both
    extras = {name: _num(ev.ev(ast, tf), index) for name, ast in compiled.extras.items()}
    return Evaluation(index, truth, values, raw, signal_long, signal_short, warm, extras,
                      frame["close"].to_numpy(dtype=float), frame["high"].to_numpy(dtype=float), frame["low"].to_numpy(dtype=float))


__all__ = ["Evaluation", "evaluate", "ROLES"]
