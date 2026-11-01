"""Which data a search may touch - one place, so no optimisation run opens the sealed holdout by accident.

Ported from Trade@0df3e09d0aa5942e3352327d5b998c5f6939ac61, elliott/data_policy.py. Trade hard-codes its own research
periods (IS 2015-2021, VAL 2022-Mar 2024, holdout after); ATP is multi-tenant, so the boundary is a setting:
`BACKTEST_HOLDOUT_START` (operator, env) or a per-run `holdout_start`. Bars at or after it are the sealed holdout - the
optimizer drops them before splitting in-sample / out-of-sample and reports how many it dropped; a range that crosses it
is refused by `check_range`. The holdout is for ONE final check of ONE chosen configuration, outside the optimizer.

M71 PL-7b: on top of that boundary, a tenant may carry data-use WINDOWS loaded from its own private data's policy at
import (`data_use_windows`, app.market_lake.data_use - never dates in this public code). Kinds `sealed` (the final
holdout) and `contaminated` (the logic was designed on it: regression only) are refused by every calibration /
research / optimisation path: their bars are dropped before the search (counted, with a plain reason) and a range that
reaches into one is refused by `check_range`. Charts, live reading, backtests shown to the user and screeners do not
call this module, so they display those bars.
"""
import os
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional, Sequence

import numpy as np
import pandas as pd


class HoldoutError(RuntimeError):
    """An attempt to use the sealed holdout."""


REFUSED_KINDS = ("sealed", "contaminated")
KIND_WORDS = {"sealed": "kept sealed for one final check", "contaminated": "the method was designed on it (regression only)"}


@dataclass(frozen=True)
class UseWindow:
    """One data-use window (dates are exchange (IST) trade dates; end None = open-ended)."""
    kind: str
    start: date
    end: date | None = None
    scope: str = "*"
    label: str = ""

    @property
    def refused(self) -> bool:
        return self.kind in REFUSED_KINDS

    def describe(self) -> str:
        span = f"{self.start.isoformat()} onward" if self.end is None else f"{self.start.isoformat()} to {self.end.isoformat()}"
        return f"{span} ({KIND_WORDS.get(self.kind, self.kind)})"


def _refused(windows: Sequence[UseWindow]) -> list:
    return [w for w in windows or () if w.refused]


def window_mask(index, windows: Sequence[UseWindow] = ()) -> np.ndarray:
    """True for bars outside every sealed / contaminated window (by the bar's IST trade date)."""
    refused = _refused(windows)
    idx = pd.DatetimeIndex(index)
    if not refused or len(idx) == 0:
        return np.ones(len(idx), dtype=bool)
    local = idx.tz_convert("Asia/Kolkata").tz_localize(None) if idx.tz is not None else idx
    days = local.normalize()
    keep = np.ones(len(idx), dtype=bool)
    for w in refused:
        inside = days >= pd.Timestamp(w.start)
        if w.end is not None:
            inside &= days < pd.Timestamp(w.end + timedelta(days=1))
        keep &= ~np.asarray(inside)
    return keep


def _ist_day(ts) -> date:
    """The exchange (IST) trade date of an instant - the same clock `window_mask` uses (a naive time is IST already)."""
    t = pd.Timestamp(ts)
    return (t.tz_convert("Asia/Kolkata") if t.tzinfo is not None else t).date()


def windows_reason(windows: Sequence[UseWindow], dropped: int) -> str | None:
    """The plain line a refused part gets ("12 bars left out: ..."), None when nothing was left out."""
    refused = _refused(windows)
    if not dropped or not refused:
        return None
    return (f"{dropped} bars left out: your data policy reserves " + "; ".join(w.describe() for w in refused)
            + " - searches and calibration never use them (charts and backtests still show them).")


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


def check_range(start, end, boundary, windows: Sequence[UseWindow] = ()) -> bool:
    """[start, end] must end before the holdout and touch no sealed / contaminated window; else HoldoutError."""
    for w in _refused(windows):
        s_day, e_day = _ist_day(start), _ist_day(end)
        if e_day >= w.start and (w.end is None or s_day <= w.end):
            raise HoldoutError(f"{s_day} -> {e_day} reaches data your data policy reserves: {w.describe()}. "
                               "Choose a range outside it.")
    if boundary is None:
        return True
    idx, b = _aligned([pd.Timestamp(start), pd.Timestamp(end)], boundary)
    s, e = idx[0], idx[1]
    if e < s:
        raise ValueError("end < start")
    if e >= b:
        raise HoldoutError(f"{s.date()} -> {e.date()} reaches the sealed holdout (from {b.date()})")
    return True


def research_mask(index, boundary, windows: Sequence[UseWindow] = ()) -> np.ndarray:
    """True for bars a search may use (before the holdout, outside every sealed / contaminated window)."""
    keep = window_mask(index, windows)
    if boundary is None:
        return keep
    idx, b = _aligned(index, boundary)
    return np.asarray(idx < b) & keep


def filter_allowed(df: pd.DataFrame, boundary, windows: Sequence[UseWindow] = ()) -> pd.DataFrame:
    """The bars a search may use (the last guard before a search uses the data)."""
    return df[research_mask(df.index, boundary, windows)]


def final_holdout_mask(index, boundary) -> np.ndarray:
    """The bars of the one final holdout check (the boundary's; the windows' sealed part is reached outside ATP)."""
    return ~research_mask(index, boundary)
