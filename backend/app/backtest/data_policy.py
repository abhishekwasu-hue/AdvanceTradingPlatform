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
    """The holdout boundary: the EARLIER of the per-run override and BACKTEST_HOLDOUT_START (a run may seal more, never
    open the operator's holdout), or None when neither is set. A boundary without a timezone is read in the data's own
    clock (ATP candles carry the exchange time)."""
    env = os.environ.get("BACKTEST_HOLDOUT_START", "").strip() or None
    found = [_naive_ist(pd.Timestamp(x)) for x in (override, env) if x is not None]
    return min(found) if found else None


def _naive_ist(ts: pd.Timestamp) -> pd.Timestamp:
    """An aware boundary -> exchange time (IST) without a zone, so boundaries compare with each other and with candles."""
    return ts.tz_convert("Asia/Kolkata").tz_localize(None) if ts.tzinfo is not None else ts


def _aligned(index, boundary: pd.Timestamp):
    idx = pd.DatetimeIndex(index)
    boundary = _naive_ist(boundary)
    if idx.tz is not None:
        boundary = boundary.tz_localize("Asia/Kolkata").tz_convert(idx.tz)
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
