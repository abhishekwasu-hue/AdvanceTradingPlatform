"""P0.10: a Python port of the frontend's sample candle generator (frontend/src/utils/sampleData.ts) - the same seeded
PRNG and the same session shape - so backend tests run the strategist on the candles the "Sample" data source produces."""
import math
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import pandas as pd

SESSION_BARS = 375
SESSION_OPEN_UTC_MINUTES = 3 * 60 + 45


def _imul(a: int, b: int) -> int:
    return (a * b) & 0xFFFFFFFF


def mulberry32(seed: int):
    state = seed & 0xFFFFFFFF

    def rand() -> float:
        nonlocal state
        state = (state + 0x6D2B79F5) & 0xFFFFFFFF
        t = _imul(state ^ (state >> 15), 1 | state)
        t = ((t + _imul(t ^ (t >> 7), 61 | t)) & 0xFFFFFFFF) ^ t
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296
    return rand


def trading_days(n: int, end: datetime) -> List[datetime]:
    out: List[datetime] = []
    d = datetime(end.year, end.month, end.day, tzinfo=timezone.utc)
    while len(out) < n:
        if d.weekday() < 5:
            out.insert(0, d)
        d -= timedelta(days=1)
    return out


def generate(count: int, start_price: float, seed: int, *, daily: bool = False, end: Optional[datetime] = None) -> pd.DataFrame:
    rand = mulberry32(seed)

    def gauss() -> float:
        u = max(rand(), 1e-12)
        return math.sqrt(-2 * math.log(u)) * math.cos(2 * math.pi * rand())

    end = end or datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
    days = trading_days(count if daily else math.ceil(count / SESSION_BARS), end)
    rows, index = [], []
    close, day_vol = start_price, 0.0075
    for day in days:
        day_vol = min(0.011, max(0.004, 0.6 * day_vol + 0.4 * (0.004 + rand() * 0.007)))
        pull = ((start_price - close) / start_price) * 0.15
        day_drift = pull * 0.01 + gauss() * day_vol * 0.25
        gap = gauss() * day_vol * 0.25
        if daily:
            o = close * math.exp(gap)
            close = o * math.exp(day_drift + gauss() * day_vol * 0.8)
            h = max(o, close) * (1 + rand() * day_vol * 0.6)
            lo = min(o, close) * (1 - rand() * day_vol * 0.6)
            rows.append((o, h, lo, close, 150_000 + round(rand() * 100_000)))
            index.append(day + timedelta(minutes=SESSION_OPEN_UTC_MINUTES))
            continue
        close = close * math.exp(gap)
        per_bar = day_vol / math.sqrt(SESSION_BARS)
        for m in range(SESSION_BARS):
            edge = 1.45 if m < 30 or m >= SESSION_BARS - 30 else 0.75 if 150 < m < 270 else 1.0
            o = close
            close = o * math.exp(day_drift / SESSION_BARS + per_bar * edge * gauss())
            wick = o * per_bar * edge * (0.2 + rand() * 0.6)
            rows.append((o, max(o, close) + wick, min(o, close) - wick, close, round((700 + rand() * 500) * edge * edge)))
            index.append(day + timedelta(minutes=SESSION_OPEN_UTC_MINUTES + m))
    df = pd.DataFrame(rows[-count:], columns=["open", "high", "low", "close", "volume"], index=pd.DatetimeIndex(index[-count:]))
    return df.round({"open": 2, "high": 2, "low": 2, "close": 2})
