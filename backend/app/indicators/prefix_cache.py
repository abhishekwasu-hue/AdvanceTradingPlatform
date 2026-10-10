"""Backtest realism 3 (speed): causal indicators computed once per run, not once per bar.

A backtest shows a strategy, on every bar, the prefix of each frame that had closed (`app.backtest.windows`), and
the strategy recomputes its indicators on that prefix - O(n) work per bar, O(n^2) per run (two years of one-minute
bars would take hours). Every indicator here is causal: its value at bar t depends only on bars <= t, so the
indicator of a prefix IS the prefix of the indicator of the whole frame.

Inside `prefix_cache(frames)` an indicator called on a prefix of one of those frames (an `iloc[:n]` slice - it shares
the frame's memory from row 0) returns the first n values of the indicator computed once on the whole frame. Any
other input (a derived series, a resample, a frame not registered) is computed as before. Outside the context
nothing changes (live trading never enters it).

The guarantee is checked two ways: `tests/test_realism_speed.py` (each cached indicator: prefix of the whole ==
computed on the prefix) and the truncation test (every multi-timeframe strategy: same signals as on data cut at the
decision time, with no cache). Returned prefixes are views of the cached result - callers must not modify them.
"""
from __future__ import annotations

import functools
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Dict, Iterable, Iterator, Optional, Tuple

import numpy as np
import pandas as pd


class _Registry:
    def __init__(self, frames: Iterable[pd.DataFrame] = ()) -> None:
        self.frames: Dict[int, pd.DataFrame] = {}
        self.columns: Dict[int, Tuple[int, str]] = {}      # data pointer of a column -> (frame id, column)
        self.results: Dict[Tuple[Any, ...], Any] = {}
        # Realism C3: what the cache did in this run - whole-frame computations, prefix answers served, and calls it
        # could not serve (a derived series: computed on the spot, the O(n)-per-bar path). See run_stats().
        self.stats = {"computed": 0, "served": 0, "uncached": 0}
        self.add(frames)

    def add(self, frames: Iterable[pd.DataFrame]) -> None:
        for frame in frames:
            self.frames[id(frame)] = frame
            for col in frame.columns:
                ptr = _pointer(frame[col])
                if ptr is not None:
                    self.columns[ptr] = (id(frame), col)


_active: ContextVar[Optional[_Registry]] = ContextVar("indicator_prefix_cache", default=None)


def _pointer(series: pd.Series) -> Optional[int]:
    try:
        values = series.to_numpy(copy=False)
    except Exception:  # noqa: BLE001 - an unusual dtype: not cacheable, computed as before
        return None
    if not isinstance(values, np.ndarray) or len(values) == 0:
        return None
    return int(values.__array_interface__["data"][0])


def _prefix_of(reg: _Registry, obj: Any) -> Optional[Tuple[pd.DataFrame, Optional[str], int]]:
    """(whole frame, column or None for a frame input, prefix length) when `obj` is a row-0 prefix of a registered
    frame (or of one of its columns); else None."""
    if isinstance(obj, pd.Series):
        probe, column = obj, obj.name
    elif isinstance(obj, pd.DataFrame) and "close" in obj.columns:
        probe, column = obj["close"], None
    else:
        return None
    hit = reg.columns.get(_pointer(probe) or -1)
    if hit is None:
        return None
    frame = reg.frames[hit[0]]
    n = len(obj)
    if n == 0 or n > len(frame) or (column is not None and column != hit[1]):
        return None
    if obj.index[0] != frame.index[0] or obj.index[-1] != frame.index[n - 1]:
        return None
    if isinstance(obj, pd.DataFrame) and not set(obj.columns) <= set(frame.columns):
        return None
    return frame, column, n


def prefix_cached(fn: Callable) -> Callable:
    """Decorator for a causal indicator `fn(series_or_frame, *params)`."""
    @functools.wraps(fn)
    def wrapper(data, *args, **kwargs):
        reg = _active.get()
        if reg is None:
            return fn(data, *args, **kwargs)
        hit = _prefix_of(reg, data)
        if hit is None:
            reg.stats["uncached"] += 1
            return fn(data, *args, **kwargs)
        frame, column, n = hit
        key = (fn.__module__, fn.__qualname__, id(frame), column, args, tuple(sorted(kwargs.items())))
        full = reg.results.get(key)
        if full is None:
            whole = frame[column] if column is not None else frame[list(data.columns)]
            full = reg.results[key] = fn(whole, *args, **kwargs)
            reg.stats["computed"] += 1
        reg.stats["served"] += 1
        return full.iloc[:n]
    return wrapper


def register_frames(frames: Iterable[pd.DataFrame]) -> None:
    """Adds the run's frames to the active cache (no-op outside one)."""
    reg = _active.get()
    if reg is not None:
        reg.add(frames)


_last_stats = threading.local()


def run_stats() -> Dict[str, int]:
    """The cache counters of the last backtest run on this thread (realism C3's benchmark and its CI guard)."""
    return dict(getattr(_last_stats, "value", {}))


def with_prefix_cache(run: Callable) -> Callable:
    """Decorator for a backtest entry point: a fresh cache for the duration of one run."""
    @functools.wraps(run)
    def wrapper(*args, **kwargs):
        with prefix_cache(()):
            try:
                return run(*args, **kwargs)
            finally:
                reg = _active.get()
                _last_stats.value = dict(reg.stats) if reg is not None else {}
    return wrapper


@contextmanager
def prefix_cache(frames: Iterable[pd.DataFrame]) -> Iterator[None]:
    """Within the block, indicators on prefixes of `frames` come from one whole-frame computation each."""
    token = _active.set(_Registry(frames))
    try:
        yield
    finally:
        _active.reset(token)


__all__ = ["prefix_cache", "prefix_cached", "register_frames", "run_stats", "with_prefix_cache"]
