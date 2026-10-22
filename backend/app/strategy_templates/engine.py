"""Strategy templates: the setup / trigger state machine - a PURE FUNCTION OF BARS (owner order R3, S1-S7, R8).

`compute(recipe, frames)` evaluates every part of the recipe once over the whole series (the ScreenQL runtime, closed
bars, context values aligned by close time - S1, R9) and then walks the bars once, per side:

    IDLE --setup--> ARMED --trigger (+ context)--> SIGNAL (the instance is consumed: one signal per instance)
                    ARMED --expiry / state ends / arm level breached--> IDLE

* A setup event starts an instance (its id: the side and the setup bar's time - a time, not a bar number, so a trailing
  window and the full history name the same instance). A newer setup event replaces the older instance (R8).
* `expiry_bars` counts signal-timeframe bars from the setup bar; the trigger may fire on the setup bar itself only with
  `same_bar` (R8).
* Before the trigger, a breach of the arm level (close or wick, as the template says) voids the instance - a gap through
  it included (S4, S7). A trigger reads only its own bar (no retroactive fill).
* Both sides triggering on one bar: no signal for either, both instances consumed, reason "conflict" (S5, R8).
* The invalidation is read on the trigger bar and must be on the losing side of the close (long: below; short: above),
  else no signal, reason "invalid_geometry"; a missing level gives "no_invalidation" (S6). Both consume the instance.
* Warm-up (S3): no signal until every series the recipe reads has had a value and the validator's bars of history are
  there, on the signal timeframe and on every context timeframe; reason "warmup".

Being in a position never changes this state (R3): the series is the same whatever the trades. The backtest reads it
at each decision bar through the adapter, which computes it once per run (app/strategy_templates/adapter.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from app.screener import nodes as n
from app.screener.registry import BOOL, FIELDS, FUNCTIONS
from app.screener.runtime import SymbolData, _Eval
from app.strategy_templates.compile import SIDES, Recipe, Side

# Series that are legitimately missing between their events: they do not hold the warm-up back. Family G (review finding
# WB4): the alternate wave count (missing when there is no second reading) and the wave level companions - the wave's
# origin / ends, the hard-rule invalidation and the cap, the ratio levels and the anchored wave lines (NaN off their
# waves; registered with the W2-W4 step). The R2 breakout level companions join the same way.
WAVE_SPARSE_SERIES: Tuple[str, ...] = ("WaveAlt", "WaveOrigin", "WaveEnd", "WaveInvalidation", "WaveCap", "WaveRetrace",
                                       "WaveProjection", "WaveLineLevel")
# Strategy templates step 2 (R2): the breakout level companions are prices on their signal bars only (held_level: from the
# first signal on), so they never hold the warm-up back either.
BREAKOUT_SPARSE_SERIES: Tuple[str, ...] = ("RangeBreakoutRef", "LineBreakoutRef")
SPARSE_SERIES: frozenset = frozenset(WAVE_SPARSE_SERIES + BREAKOUT_SPARSE_SERIES)

REASON_WARMUP = "warmup"
REASON_CONFLICT = "conflict"
REASON_GEOMETRY = "invalid_geometry"
REASON_NO_LEVEL = "no_invalidation"


@dataclass
class SignalSeries:
    """The recipe's answer on every bar of the signal timeframe."""
    index: pd.DatetimeIndex
    direction: np.ndarray                 # +1 long, -1 short, 0 none
    invalidation: np.ndarray              # the price on signal bars, NaN elsewhere
    setup_bar: np.ndarray                 # the setup bar (position) of the instance that signalled, -1 elsewhere
    reason: np.ndarray                    # "" or why a bar had no signal although something happened (warm-up, conflict ...)
    exit_long: np.ndarray                 # the template's exit signal for a long idea (S10: the caller applies it)
    exit_short: np.ndarray
    evidence: Dict[int, Tuple[str, ...]] = field(default_factory=dict)
    warmup_bars: int = 0

    def instance_id(self, t: int) -> Optional[str]:
        """The setup instance of the signal on bar t: side and the setup bar's time (stable across fetch windows)."""
        if self.direction[t] == 0:
            return None
        side = "long" if self.direction[t] > 0 else "short"
        return f"{side}@{pd.Timestamp(self.index[int(self.setup_bar[t])]).isoformat()}"

    def signal_bars(self) -> List[int]:
        return [int(i) for i in np.flatnonzero(self.direction)]


def _bool(value: Any, index: pd.Index) -> np.ndarray:
    if isinstance(value, pd.Series):
        return value.reindex(index).fillna(False).astype(bool).to_numpy()
    return np.full(len(index), bool(value) if value is not None and value == value else False)


def _num(value: Any, index: pd.Index) -> np.ndarray:
    if isinstance(value, pd.Series):
        return pd.to_numeric(value.reindex(index), errors="coerce").to_numpy(dtype=float)
    try:
        return np.full(len(index), float(value))
    except (TypeError, ValueError):
        return np.full(len(index), np.nan)


def _leaves(ast: Any) -> List[Any]:
    """The value series an expression reads (fields and number / category functions) - each must have started."""
    out: List[Any] = []
    for node in n.walk(ast):
        if isinstance(node, n.Field) and node.name in FIELDS:
            out.append(node)
        elif isinstance(node, n.Call):
            spec = FUNCTIONS.get(node.name)
            if spec is not None and spec.returns != BOOL and node.name not in SPARSE_SERIES:
                out.append(node)
    return out


def _closed_count(ev: _Eval, tf: str, base_tf: str) -> np.ndarray:
    """On each signal bar, how many bars of `tf` had closed (the runtime's close-time alignment)."""
    frame = ev.frame(tf)
    counts = pd.Series(np.arange(1, len(frame) + 1, dtype=float), index=frame.index)
    aligned = ev.align(counts, tf, base_tf)
    return _num(aligned, ev.frame(base_tf).index)


def compute(recipe: Recipe, frames: Dict[str, pd.DataFrame], symbol: str = "S") -> SignalSeries:
    """The whole signal series of a recipe on closed bars (`frames`: ScreenQL timeframe -> bars indexed by bar start; a
    context timeframe that is missing is resampled from the signal timeframe by the runtime)."""
    s = recipe.settings
    tf = s.timeframe
    ev = _Eval(SymbolData(symbol, dict(frames)), tf, {}, {}, memo={})
    index = ev.frame(tf).index
    size = len(index)

    def run(part_name: str, side: Side) -> Optional[Any]:
        part = recipe.part(part_name, side)
        if part is None:
            return None
        ev.params = part.params
        return ev.ev(part.ast, tf)

    sides = s.side_list()
    # --- every part once, over the whole series (R3) ----------------------------------------------------------------
    ready = np.ones(size, dtype=bool)
    steps = np.arange(1, size + 1)
    ready &= steps >= recipe.min_bars
    for other_tf, bars in recipe.lookback.items():
        if other_tf != tf:
            ready &= _closed_count(ev, other_tf, tf) >= bars
    data: Dict[Side, Dict[str, np.ndarray]] = {}
    for side in sides:
        d: Dict[str, np.ndarray] = {}
        d["setup"] = _bool(run("setup", side), index)
        trig = run("trigger", side)
        d["trigger"] = _bool(trig, index) if trig is not None else np.zeros(size, dtype=bool)
        ctx = run("context", side)
        d["context"] = _bool(ctx, index) if ctx is not None else np.ones(size, dtype=bool)
        d["invalidation"] = _num(run("invalidation", side), index)
        arm = run("arm_level", side)
        d["arm_level"] = _num(arm, index) if arm is not None else np.full(size, np.nan)
        ex = run("exit_signal", side)
        d["exit"] = _bool(ex, index) if ex is not None else np.zeros(size, dtype=bool)
        for e in recipe.template.evidence:
            d[f"evidence:{e.name}"] = _bool(run(f"evidence:{e.name}", side), index)
        for (name, part_side), part in recipe.parts.items():
            if part_side != side or name.startswith("evidence:") or name == "exit_signal":
                continue
            for leaf in _leaves(part.ast):
                ev.params = part.params
                started = np.maximum.accumulate(~np.isnan(_num_or_category(ev.ev(leaf, tf), index)))
                ready &= started
        data[side] = d
    warmup = int(np.argmax(ready)) if ready.any() else size

    # --- the state machine: one pass over the bars ---------------------------------------------------------------------
    close = ev.frame(tf)["close"].to_numpy(dtype=float)
    low = ev.frame(tf)["low"].to_numpy(dtype=float)
    high = ev.frame(tf)["high"].to_numpy(dtype=float)
    setup_t = recipe.chosen_setup
    direction = np.zeros(size, dtype=np.int8)
    invalidation = np.full(size, np.nan)
    setup_bar = np.full(size, -1, dtype=np.int64)
    reason = np.full(size, "", dtype=object)
    evidence: Dict[int, Tuple[str, ...]] = {}
    armed: Dict[Side, int] = {side: -1 for side in sides}
    level: Dict[Side, float] = {side: np.nan for side in sides}
    for t in range(size):
        if not ready[t]:
            reason[t] = REASON_WARMUP
            continue
        fired: Dict[Side, int] = {}
        for side in sides:
            d = data[side]
            raw = bool(d["setup"][t])
            # a state arms only on a rising edge seen after the warm-up (one already true when the history starts has no
            # known start, and a trailing window would arm it on a different bar than the full history)
            starts = raw if setup_t.kind == "event" else raw and t > 0 and bool(ready[t - 1]) and not d["setup"][t - 1]
            if starts:
                armed[side], level[side] = t, float(d["arm_level"][t])
            elif armed[side] >= 0:
                s0 = armed[side]
                lv = level[side]
                expired = recipe.expiry_bars is not None and t - s0 > recipe.expiry_bars
                ended = setup_t.kind == "state" and not raw
                if setup_t.breach_on == "wick":
                    breached = np.isfinite(lv) and (low[t] < lv if side == "long" else high[t] > lv)
                else:
                    breached = np.isfinite(lv) and (close[t] < lv if side == "long" else close[t] > lv)
                if expired or ended or breached:
                    armed[side] = -1
            s0 = armed[side]
            if s0 < 0 or (t == s0 and not setup_t.same_bar):
                continue
            hit = (t == s0) if not recipe.has_trigger else bool(d["trigger"][t])
            if hit and d["context"][t]:
                fired[side] = s0
        if not fired:
            continue
        if len(fired) == 2:                                   # S5: both sides on one bar - neither, both consumed (R8)
            reason[t] = REASON_CONFLICT
            for side in fired:
                armed[side] = -1
            continue
        side, s0 = next(iter(fired.items()))
        armed[side] = -1                                      # one signal per instance, whatever the outcome below
        inv = float(data[side]["invalidation"][t])
        if not np.isfinite(inv):
            reason[t] = REASON_NO_LEVEL
            continue
        if (side == "long" and not inv < close[t]) or (side == "short" and not inv > close[t]):
            reason[t] = REASON_GEOMETRY                       # S6: the idea is wrong already at the trigger close
            continue
        direction[t] = 1 if side == "long" else -1
        invalidation[t] = inv
        setup_bar[t] = s0
        evidence[t] = tuple(e.name for e in recipe.template.evidence if data[side][f"evidence:{e.name}"][t])
    exits = {side: (data[side]["exit"] & ready) if side in data else np.zeros(size, dtype=bool) for side in SIDES}
    return SignalSeries(index, direction, invalidation, setup_bar, reason, exits["long"], exits["short"], evidence, warmup)


def _num_or_category(value: Any, index: pd.Index) -> np.ndarray:
    """A value series as numbers for the 'has it started' test: a category is 1 where it has a value, NaN where not."""
    if isinstance(value, pd.Series) and value.dtype == object:
        return np.where(value.reindex(index).notna().to_numpy(), 1.0, np.nan)
    return _num(value, index)


__all__ = ["SignalSeries", "compute", "REASON_WARMUP", "REASON_CONFLICT", "REASON_GEOMETRY", "REASON_NO_LEVEL", "SPARSE_SERIES",
           "WAVE_SPARSE_SERIES", "BREAKOUT_SPARSE_SERIES"]
