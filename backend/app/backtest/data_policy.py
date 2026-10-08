"""Which data a search may touch - one place, so no optimisation run opens the sealed holdout by accident.

Ported from Trade@0df3e09d0aa5942e3352327d5b998c5f6939ac61, elliott/data_policy.py. Trade hard-codes its own research
periods (IS 2015-2021, VAL 2022-Mar 2024, holdout after); ATP is multi-tenant, so the boundary is a setting:
`BACKTEST_HOLDOUT_START` (operator, env) or a per-run `holdout_start`. Bars at or after it are the sealed holdout - the
optimizer drops them before splitting in-sample / out-of-sample and reports how many it dropped; a range that crosses it
is refused by `check_range`. The holdout is for ONE final check of ONE chosen configuration, outside the optimizer.
"""
import os
from typing import Optional

import numpy as np
import pandas as pd


class HoldoutError(RuntimeError):
    """An attempt to use the sealed holdout."""


def holdout_start(override=None) -> Optional[pd.Timestamp]:
    """The holdout boundary: the per-run override, else BACKTEST_HOLDOUT_START, else None (no sealed holdout). A boundary
    without a timezone is read in the data's own clock (ATP candles carry the exchange time)."""
    raw = override if override is not None else (os.environ.get("BACKTEST_HOLDOUT_START", "").strip() or None)
    return None if raw is None else pd.Timestamp(raw)


def _aligned(index, boundary: pd.Timestamp):
    idx = pd.DatetimeIndex(index)
    if idx.tz is not None and boundary.tzinfo is None:
        boundary = boundary.tz_localize(idx.tz)
    elif idx.tz is None and boundary.tzinfo is not None:
        boundary = boundary.tz_localize(None)
    return idx, boundary


def check_range(start, end, boundary) -> bool:
    """[start, end] must end before the holdout; else HoldoutError."""
    if boundary is None:
        return True
    idx, b = _aligned([pd.Timestamp(start), pd.Timestamp(end)], boundary)
    s, e = idx[0], idx[1]
    if e < s:
        raise ValueError("end < start")
    if e >= b:
        raise HoldoutError(f"{s.date()} -> {e.date()} reaches the sealed holdout (from {b.date()})")
    return True


def research_mask(index, boundary) -> np.ndarray:
    """True for bars a search may use (before the holdout)."""
    if boundary is None:
        return np.ones(len(index), dtype=bool)
    idx, b = _aligned(index, boundary)
    return np.asarray(idx < b)


def filter_allowed(df: pd.DataFrame, boundary) -> pd.DataFrame:
    """The bars before the holdout (the last guard before a search uses the data)."""
    return df[research_mask(df.index, boundary)]


def final_holdout_mask(index, boundary) -> np.ndarray:
    """The bars of the one final holdout check."""
    return ~research_mask(index, boundary)
