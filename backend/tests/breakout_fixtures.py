"""U6 BK1: synthetic bar fixtures for the breakout screens (spec §7). Written out bar by bar - no randomness - so each
test says exactly what the market did: a trend to set ATR and swings, a base, then the break (or the fake)."""
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

Bar = Tuple[float, float, float, float, float]          # open, high, low, close, volume


def zigzag(points: Sequence[float], per_leg: int, spread: float = 0.5, volume: float = 1000.0) -> List[Bar]:
    """Bars walking straight between the pivot prices, `per_leg` bars a leg; each bar opens at the last close."""
    closes: List[float] = [float(points[0])]
    for a, b in zip(points, points[1:]):
        closes += list(np.linspace(a, b, per_leg + 1)[1:])
    out: List[Bar] = []
    prev = closes[0]
    for c in closes:
        out.append((prev, max(prev, c) + spread, min(prev, c) - spread, c, volume))
        prev = c
    return out


def box(n: int, low: float, high: float, volume: float = 1000.0) -> List[Bar]:
    """A tight range: closes alternate a quarter in from each edge; every high is `high`, every low `low`."""
    q = (high - low) / 4.0
    out: List[Bar] = []
    for i in range(n):
        o, c = (low + q, high - q) if i % 2 == 0 else (high - q, low + q)
        out.append((o, high, low, c, volume))
    return out


def frame(bars: Sequence[Bar], start: str = "2025-01-06 03:45", freq: str = "B") -> pd.DataFrame:
    """Bars on business days (the default) or any pandas frequency, UTC-stamped at the bar start."""
    idx = pd.date_range(start, periods=len(bars), freq=freq, tz="UTC")
    return pd.DataFrame(list(bars), columns=["open", "high", "low", "close", "volume"], index=idx).astype(float)


UPTREND = [60, 75, 66, 82, 73, 90, 81, 98, 90, 101]       # HH / HL: the daily Dow trend reads UP at the end


def base_then(break_bars: Sequence[Bar], box_n: int = 25) -> pd.DataFrame:
    """An uptrend into a 100-102 box of `box_n` bars, then `break_bars`."""
    return frame(zigzag(UPTREND, 5) + box(box_n, 100.0, 102.0) + list(break_bars))


TRUE_BREAK: Bar = (101.5, 104.0, 101.2, 103.8, 3000.0)    # closes 1.8 above the 102 box top (about 0.8 ATR) on 3x volume
WICK_ONLY: Bar = (101.5, 104.0, 101.2, 101.6, 3000.0)     # same wick, close back inside the box
LOW_VOLUME_BREAK: Bar = (101.5, 104.0, 101.2, 103.8, 1000.0)


def context_frame(points: Sequence[float], end_before: pd.Timestamp, freq: str, per_leg: int = 4) -> pd.DataFrame:
    """A higher-timeframe zigzag (weekly 'W-MON' or monthly 'MS' bars) that closes before `end_before`."""
    bars = zigzag(points, per_leg, spread=1.0)
    idx = pd.date_range(end=end_before - pd.Timedelta(days=62), periods=len(bars), freq=freq, tz="UTC")
    return pd.DataFrame(bars, columns=["open", "high", "low", "close", "volume"], index=idx).astype(float)


HTF_DOWN = [200, 180, 190, 170, 180, 160, 170, 150, 160, 140, 150, 130, 140]
HTF_UP = [100, 120, 110, 130, 120, 140, 130, 150, 140, 160, 150, 170, 160]


# ----------------------------------------------------------------------------------------------- U6 BK2 intraday sessions
# 5-minute NSE sessions, UTC-stamped at the bar start (09:15 IST = 03:45 UTC). Volume is U-shaped by time of day, as on a
# real exchange: heavy for the first 30 minutes and the last 30, light in between - so a plain n-bar average would call
# every opening bar "high volume" and the time-of-day average would not.
SESSION_5M = 75


def profile(slot: int) -> float:
    return 3000.0 if slot < 6 else 2500.0 if slot >= SESSION_5M - 6 else 1000.0


def quiet_day(n: int = SESSION_5M, mid: float = 100.0) -> List[Bar]:
    """A flat session: closes alternate mid -/+ 0.1, every high mid + 0.25, every low mid - 0.25, U-shaped volume."""
    out: List[Bar] = []
    for i in range(n):
        o, c = (mid - 0.1, mid + 0.1) if i % 2 == 0 else (mid + 0.1, mid - 0.1)
        out.append((o, mid + 0.25, mid - 0.25, c, profile(i)))
    return out


def sessions_frame(days: Sequence[Sequence[Bar]], first: str = "2026-02-02", minutes: int = 5,
                   starts: Optional[Dict[int, str]] = None) -> pd.DataFrame:
    """One business day per entry of `days`; day k's bars start at 03:45 UTC (09:15 IST) or at starts[k] ("HH:MM" UTC)."""
    dates = pd.bdate_range(first, periods=len(days), tz="UTC")
    rows: List[Bar] = []
    idx: List[pd.Timestamp] = []
    for k, (d, bars) in enumerate(zip(dates, days)):
        hh, mm = (int(x) for x in (starts or {}).get(k, "03:45").split(":"))
        t0 = d + pd.Timedelta(hours=hh, minutes=mm)
        for i, bar in enumerate(bars):
            rows.append(bar)
            idx.append(t0 + pd.Timedelta(minutes=minutes * i))
    return pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"], index=pd.DatetimeIndex(idx)).astype(float)


# The opening range of the test day: 09:15-09:30 high 101.0, low 99.9 (midpoint 100.45); the break comes at 09:45 (slot 6)
# on 2.5x the volume of that time of day, well above the session VWAP.
OR_BARS: List[Bar] = [(100.0, 100.8, 99.9, 100.5, 3000.0), (100.5, 101.0, 100.3, 100.6, 3000.0), (100.6, 100.9, 100.2, 100.4, 3000.0)]
HOLD_BARS: List[Bar] = [(100.4, 100.7, 100.2, 100.5, 3000.0), (100.5, 100.8, 100.3, 100.6, 3000.0), (100.6, 100.9, 100.4, 100.7, 3000.0)]
ORB_BREAK: Bar = (100.7, 101.8, 100.6, 101.6, 2500.0)
# A wick far above the range on ten times the volume drags the VWAP above the later break's close.
VWAP_WICK: Bar = (100.5, 106.0, 100.3, 100.9, 30000.0)


def above(n: int, start: float = 101.8, step: float = 0.05, volume: Optional[float] = None, first_slot: int = 7) -> List[Bar]:
    """`n` bars drifting up from `start`, every close above the opening range (volume: the time-of-day profile)."""
    out: List[Bar] = []
    for i in range(n):
        c = start + step * i
        out.append((c - 0.1, c + 0.2, c - 0.2, c, profile(first_slot + i) if volume is None else volume))
    return out


def orb_day(*, wick: bool = False, rest: Optional[Sequence[Bar]] = None) -> List[Bar]:
    hold = [HOLD_BARS[0], VWAP_WICK, HOLD_BARS[2]] if wick else list(HOLD_BARS)
    tail = list(rest) if rest is not None else above(SESSION_5M - 7)
    return OR_BARS + hold + [ORB_BREAK] + tail


def history(n: int = 21) -> List[List[Bar]]:
    return [quiet_day() for _ in range(n)]
