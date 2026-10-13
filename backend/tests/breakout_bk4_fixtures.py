"""U6 BK4 fixtures: deterministic bars (no randomness) for the trendline / channel, flag / pennant / triangle, gap and
retest breakouts. Patterns are drawn around 100 after a short flat lead-in; `lifted` puts any of them after the BK3
zigzag uptrend (the weekly trend reads UP) for the alert-window checks."""
from typing import List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from tests.breakout_bk3_fixtures import frame, path, uptrend

Row = Tuple[float, float, float, float, float]
LEAD = [100.0 + 0.3 * np.sin(i) for i in range(30)]


def _with_volume(closes: Sequence[float], last_volume: float = 3000.0) -> List[float]:
    return [1000.0] * (len(closes) - 1) + [last_volume]


def falling_line(break_by: float = 4.0) -> List[float]:
    """Lower highs (a falling resistance through three swing highs), then a close `break_by` above the last low."""
    legs = path(100.0, [(5, 6.0), (5, -8.0), (5, 5.0), (5, -7.0), (5, 4.0), (5, -6.0), (5, 3.0)])[1:]
    return LEAD + legs + [legs[-1] + break_by]


def rising_channel(break_by: float = 6.0) -> List[float]:
    legs = path(100.0, [(6, 6.0), (4, -3.0)] * 5)[1:]
    return LEAD + legs + [legs[-1] + break_by]


def triangle(break_by: float = 4.0) -> List[float]:
    legs = path(100.0, [(5, 8.0), (5, -7.0), (5, 6.0), (5, -5.0), (5, 4.0), (5, -3.0), (4, 2.0)])[1:]
    return LEAD + legs + [legs[-1] + break_by]


def flag(pennant: bool = False, retrace: float = 2.5, break_by: float = 3.0) -> List[float]:
    if pennant:
        legs = path(100.0, [(5, 12.0), (2, -2.0), (2, 1.6), (2, -1.2), (2, 0.8)])[1:]
    else:
        legs = path(100.0, [(5, 12.0), (8, -retrace)])[1:]
    return LEAD + legs + [legs[-1] + break_by]


def closes_frame(closes: Sequence[float], last_volume: float = 3000.0, lift: float = 0.0) -> pd.DataFrame:
    return frame([c + lift for c in closes], _with_volume(closes, last_volume), half=0.4)


def retest_rows(fail: bool = False, hammer: bool = True) -> List[Row]:
    """A 40-bar range around 100 (high about 100.7), a breakout bar, three bars away from the level, a pull back, then the
    retest bar: a hammer-like reversal at the level (or, hammer=False, a bar that touches and closes weak); fail=True
    puts a close back inside the range before it."""
    base = [100.0 + 0.2 * np.sin(i * 1.3) for i in range(40)]
    rows: List[Row] = [(c - 0.1, c + 0.5, c - 0.5, c, 1000.0) for c in base]
    rows.append((100.2, 102.3, 100.1, 102.1, 3000.0))
    rows += [(c - 0.3, c + 0.4, c - 0.5, c, 1200.0) for c in (103.0, 104.0, 103.5)]
    rows += [(103.4, 103.5, 102.4, 102.6, 900.0), (102.6, 102.7, 101.6, 101.7, 900.0)]
    if fail:
        rows.append((101.7, 101.8, 100.2, 100.4, 900.0))
    rows.append((101.6, 101.9, 100.5, 101.8, 1500.0) if hammer else (101.6, 101.7, 100.6, 100.75, 900.0))
    return rows


def rows_frame(rows: Sequence[Row], lead: Optional[Sequence[float]] = None) -> pd.DataFrame:
    """Daily bars from rows; `lead` closes (half range 0.4) before them, the rows lifted to start where the lead ends."""
    out: List[Row] = []
    lift = 0.0
    if lead is not None:
        lead_f = frame(lead, half=0.4)
        out = [tuple(x) for x in lead_f[["open", "high", "low", "close", "volume"]].to_numpy()]   # type: ignore[misc]
        lift = float(lead[-1]) - rows[0][3]
    out += [(o + lift, h + lift, lo + lift, c + lift, v) for o, h, lo, c, v in rows]
    idx = pd.bdate_range("2025-01-01", periods=len(out), tz="UTC") + pd.Timedelta(hours=3, minutes=45)
    return pd.DataFrame(out, columns=["open", "high", "low", "close", "volume"], index=idx).astype(float)


def trend_lead() -> List[float]:
    """The BK3 zigzag uptrend (213 closes, 30 -> 132): the weekly Dow trend reads UP after it."""
    return path(30.0, uptrend())


def lifted(closes: Sequence[float], last_volume: float = 3000.0) -> pd.DataFrame:
    """`closes` (drawn around 100, after LEAD) moved to follow the uptrend: the pattern starts where the trend ends."""
    lead = trend_lead()
    body = list(closes[len(LEAD):])
    shift = lead[-1] - body[0]
    allc = lead + [c + shift for c in body[1:]]
    return frame(allc, _with_volume(allc, last_volume), half=0.4)


def daily_gap(open_: float, close: float, prev_high: float = 100.5) -> pd.DataFrame:
    """20 quiet days (highs prev_high), then a day that opens at `open_` and closes at `close`."""
    rows: List[Row] = [(100.0, prev_high, 99.5, 100.0, 1000.0) for _ in range(20)]
    rows.append((open_, max(open_, close) + 0.2, min(open_, close) - 0.2, close, 2000.0))
    return rows_frame(rows)
