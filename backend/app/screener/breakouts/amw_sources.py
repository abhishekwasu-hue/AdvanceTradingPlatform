"""Causal AMW level candidates from existing ATP price-action readers."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

import numpy as np
import pandas as pd

from app.price_action.zones.levels import cluster_prices
from app.screener.breakouts import screens, session

from .amw_breakout import Candidate, Settings

Side = Literal[1, -1]


def crosses(bars: screens.Bars, j: int, side: Side, level: float, buffer: float) -> bool:
    """Whether the current closed bar first crosses this already-known buffered level."""
    if j < 1 or not np.isfinite(level) or not np.isfinite(bars.atr_prev[j]):
        return False
    prior_atr = bars.atr_prev[j - 1]
    return bool(side * (bars.c[j] - level) > buffer * bars.atr_prev[j] and
                side * (bars.c[j - 1] - level) <= buffer * (prior_atr if np.isfinite(prior_atr) else 0))


def horizontal_candidates(bars: screens.Bars, closes: pd.DatetimeIndex, j: int, settings: Settings) -> list[Candidate]:
    """Cluster positive-price swing highs/lows visible before the decision candle."""
    result = []
    minimum = max(0, j - settings.horizontal_lookback)
    pivots = bars.pivots(settings.swing_degree)
    sides: tuple[tuple[Side, str], ...] = ((1, "H"), (-1, "L"))
    for side, kind in sides:
        prices = [p.price for p in pivots if p.kind == kind and p.price > 0 and minimum <= p.bar_idx < j
                  and p.confirmed_idx is not None and p.confirmed_idx < j]
        for row in cluster_prices(prices, settings.horizontal_tolerance_pct):
            if row["touches"] < settings.horizontal_min_touches:
                continue
            level = float(row["level"])
            if crosses(bars, j, side, level, settings.buffer):
                key = f"HORIZONTAL:{side}:{closes[j].isoformat()}:{level.hex()}"
                result.append(Candidate(key=key, side=side, level=level, sources=("HORIZONTAL",)))
    return result


def session_candidates(bars: screens.Bars, starts: pd.DatetimeIndex, closes: pd.DatetimeIndex,
                       calendar: session.SessionCalendar, previous: dict, settings: Settings) -> dict[int, list[Candidate]]:
    """Break an explicitly verified previous regular session's high or low."""
    sm = session.map_sessions(starts, bars.tf_minutes(), calendar)
    hi, lo = session.previous_session_levels(bars.h, bars.l, sm)
    prior_regular = None
    origins = {}
    for sid, day in enumerate(sm.dates):
        if prior_regular is not None and previous.get(day) == prior_regular[0] and prior_regular[1] is not None:
            origins[sid] = prior_regular[1]
        if not sm.special[sid]:
            rows = np.flatnonzero(sm.session == sid)
            op, cl = calendar.special.get(day, (calendar.open, calendar.close))
            start = pd.Timestamp(datetime.combine(day, op, tzinfo=calendar.tz))
            end = pd.Timestamp(datetime.combine(day, cl, tzinfo=calendar.tz))
            complete = (len(rows) and starts[rows[0]] == start and closes[rows[-1]] == end
                        and (starts[rows[1:]] == closes[rows[:-1]]).all())
            prior_regular = (day, end if complete else None)
    result: dict[int, list[Candidate]] = {}
    sides: tuple[tuple[Side, np.ndarray], ...] = ((1, hi), (-1, lo))
    emitted: set[tuple[int, Side]] = set()
    for j, sid in enumerate(sm.session):
        if sid not in origins:
            continue
        for side, values in sides:
            level = float(values[j])
            if (sid, side) not in emitted and crosses(bars, j, side, level, settings.buffer):
                emitted.add((sid, side))
                key = f"SESSION:{side}:{origins[sid].isoformat()}:{level.hex()}"
                result.setdefault(j, []).append(Candidate(key=key, side=side, level=level, sources=("SESSION",)))
    return result
