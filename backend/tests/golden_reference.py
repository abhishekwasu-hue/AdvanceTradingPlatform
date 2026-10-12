"""Z10-a (U3 G1): reference formulas for the shipped indicators, written as plain loops over Python floats - no pandas,
no numpy - straight from each indicator's textbook definition and the convention ATP documents for it. The golden tests
(tests/test_z10a_indicator_golden.py) compare app.indicators, the Strategy Builder operands and the ScreenQL functions
with these, value by value.

Conventions (the ones ATP's code documents; OPEN_QUESTIONS Z10-2 for the Wilder seed):
* EMA: alpha = 2 / (n + 1), seeded with the first value, shown from the n-th value (pandas ewm(span, adjust=False)).
* Wilder smoothing (RSI, ATR, ADX): alpha = 1 / n, seeded with the first value, shown from the n-th value. TA-Lib and
  TradingView seed with the SMA of the first n values instead (`wilder_sma_seeded`); the two converge (tested).
* Bollinger: sample standard deviation (n - 1).
* Sessions: the IST trading date. VWAP of a session without volume is the running mean of the typical price.
* None = no value yet (warming up, or undefined such as a division by zero).
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence

Num = Optional[float]
IST = timezone(timedelta(hours=5, minutes=30))


def sma(xs: Sequence[float], n: int) -> List[Num]:
    return [None if i + 1 < n else sum(xs[i + 1 - n:i + 1]) / n for i in range(len(xs))]


def _smooth(xs: Sequence[Num], alpha: float, n: int) -> List[Num]:
    """Exponential smoothing seeded with the first value, shown once n values have been seen. Leading None is skipped
    (the series starts later); a None after the start is not expected in the golden data and refused."""
    out: List[Num] = []
    y: Num = None
    seen = 0
    for x in xs:
        if x is None or isinstance(x, float) and math.isnan(x):
            if seen:
                raise ValueError("a gap inside the series: not part of the golden data")
            out.append(None)
            continue
        y = x if y is None else alpha * x + (1 - alpha) * y
        seen += 1
        out.append(y if seen >= n else None)
    return out


def ema(xs: Sequence[Num], n: int) -> List[Num]:
    return _smooth(xs, 2.0 / (n + 1), n)


def wilder(xs: Sequence[Num], n: int) -> List[Num]:
    return _smooth(xs, 1.0 / n, n)


def wilder_sma_seeded(xs: Sequence[Num], n: int) -> List[Num]:
    """Wilder's own seed (TA-Lib, TradingView `ta.rma`): the first value is the SMA of the first n, then alpha = 1/n."""
    out: List[Num] = []
    window: List[float] = []
    y: Num = None
    for x in xs:
        if x is None:
            out.append(None)
            continue
        if y is None:
            window.append(x)
            if len(window) == n:
                y = sum(window) / n
            out.append(y)
            continue
        y = (y * (n - 1) + x) / n
        out.append(y)
    return out


def rsi(close: Sequence[float], n: int, smoother=wilder) -> List[Num]:
    gains: List[Num] = [None] + [max(close[i] - close[i - 1], 0.0) for i in range(1, len(close))]
    losses: List[Num] = [None] + [max(close[i - 1] - close[i], 0.0) for i in range(1, len(close))]
    ag, al = smoother(gains, n), smoother(losses, n)
    out: List[Num] = []
    for g, lo in zip(ag, al):
        if g is None or lo is None:
            out.append(None)
        elif lo == 0:
            out.append(100.0)
        else:
            out.append(100.0 - 100.0 / (1.0 + g / lo))
    return out


def true_range(high: Sequence[float], low: Sequence[float], close: Sequence[float]) -> List[float]:
    out = [high[0] - low[0]]
    for i in range(1, len(close)):
        pc = close[i - 1]
        out.append(max(high[i] - low[i], abs(high[i] - pc), abs(low[i] - pc)))
    return out


def atr(high, low, close, n: int, smoother=wilder) -> List[Num]:
    return smoother(true_range(high, low, close), n)


def adx(high, low, close, n: int) -> Dict[str, List[Num]]:
    """Wilder's directional system: +DM / -DM (the larger move, if positive), smoothed with TR; DX = |+DI - -DI| /
    (+DI + -DI); ADX = smoothed DX. ATP counts the first bar with DM = 0 and TR = high - low."""
    plus_dm, minus_dm = [0.0], [0.0]
    for i in range(1, len(close)):
        up, down = high[i] - high[i - 1], low[i - 1] - low[i]
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
    s_tr, s_p, s_m = wilder(true_range(high, low, close), n), wilder(plus_dm, n), wilder(minus_dm, n)
    pdi: List[Num] = []
    mdi: List[Num] = []
    dx: List[Num] = []
    for t, p, m in zip(s_tr, s_p, s_m):
        if t is None or p is None or m is None or t == 0:
            pdi.append(None)
            mdi.append(None)
            dx.append(None)
            continue
        a, b = 100.0 * p / t, 100.0 * m / t
        pdi.append(a)
        mdi.append(b)
        dx.append(None if a + b == 0 else 100.0 * abs(a - b) / (a + b))
    return {"plus_di": pdi, "minus_di": mdi, "adx": wilder(dx, n)}


def supertrend(high, low, close, n: int, mult: float) -> Dict[str, List[Num]]:
    """The band-flip Supertrend: basic bands hl2 +/- mult x ATR; the final upper band only moves down (and the lower
    only up) unless the previous close broke it; the trend flips when the close crosses the active band. ATP starts at
    bar n (one bar after the first ATR), long when that bar closes at or above the lower band."""
    a = atr(high, low, close, n)
    m = len(close)
    st: List[Num] = [None] * m
    trend = [0] * m
    if n >= m:
        return {"supertrend": st, "trend": trend}
    fu = fl = 0.0
    for i in range(n, m):
        hl2 = (high[i] + low[i]) / 2
        assert a[i] is not None
        bu, bl = hl2 + mult * a[i], hl2 - mult * a[i]
        if i == n:
            fu, fl = bu, bl
            trend[i] = 1 if close[i] >= fl else -1
        else:
            fu = bu if (bu < fu or close[i - 1] > fu) else fu
            fl = bl if (bl > fl or close[i - 1] < fl) else fl
            if trend[i - 1] == 1:
                trend[i] = -1 if close[i] < fl else 1
            else:
                trend[i] = 1 if close[i] > fu else -1
        st[i] = fl if trend[i] == 1 else fu
    return {"supertrend": st, "trend": trend}


def bollinger(close: Sequence[float], n: int, k: float) -> Dict[str, List[Num]]:
    mid = sma(close, n)
    up: List[Num] = []
    lo: List[Num] = []
    for i, mu in enumerate(mid):
        if mu is None:
            up.append(None)
            lo.append(None)
            continue
        w = close[i + 1 - n:i + 1]
        sd = math.sqrt(sum((x - mu) ** 2 for x in w) / (n - 1))
        up.append(mu + k * sd)
        lo.append(mu - k * sd)
    return {"mid": mid, "upper": up, "lower": lo}


def macd(close: Sequence[float], fast: int, slow: int, signal: int) -> Dict[str, List[Num]]:
    f, s = ema(close, fast), ema(close, slow)
    line: List[Num] = [None if a is None or b is None else a - b for a, b in zip(f, s)]
    sig = ema(line, signal)
    hist: List[Num] = [None if a is None or b is None else a - b for a, b in zip(line, sig)]
    return {"macd": line, "signal": sig, "hist": hist}


def ist_dates(stamps: Sequence[datetime]) -> List[object]:
    return [(ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)).astimezone(IST).date() for ts in stamps]


def session_vwap(stamps, high, low, close, volume, *, twap_without_volume: bool) -> List[Num]:
    """Cumulative typical price x volume over volume, restarted each IST date. Until the session has volume: the
    running mean of the typical price (`twap_without_volume`, the operand's index rule) or no value."""
    out: List[Num] = []
    day = None
    pv = v = tp_sum = 0.0
    count = 0
    for ts_day, h, lo, c, vol in zip(ist_dates(stamps), high, low, close, volume):
        if ts_day != day:
            day, pv, v, tp_sum, count = ts_day, 0.0, 0.0, 0.0, 0
        tp = (h + lo + c) / 3.0
        pv += tp * vol
        v += vol
        tp_sum += tp
        count += 1
        out.append(pv / v if v > 0 else (tp_sum / count if twap_without_volume else None))
    return out


def day_open(stamps, open_) -> List[Num]:
    out: List[Num] = []
    day, first = None, 0.0
    for d, o in zip(ist_dates(stamps), open_):
        if d != day:
            day, first = d, o
        out.append(first)
    return out


def previous_session(stamps, values, how: str) -> List[Num]:
    """The previous IST session's max / min / last of `values` (None in the first session)."""
    dates = ist_dates(stamps)
    per_day: Dict[object, float] = {}
    order: List[object] = []
    for d, x in zip(dates, values):
        if d not in per_day:
            order.append(d)
            per_day[d] = x
        elif how == "max":
            per_day[d] = max(per_day[d], x)
        elif how == "min":
            per_day[d] = min(per_day[d], x)
        else:
            per_day[d] = x
    prev = {d: (per_day[order[k - 1]] if k else None) for k, d in enumerate(order)}
    return [prev[d] for d in dates]


def opening_range(stamps, values, minutes: int, base_minutes: int, how: str) -> List[Num]:
    """The high (low) of the bars that START within the first `minutes` of the session, shown from the close of the
    bar that completes the range."""
    dates = ist_dates(stamps)
    out: List[Num] = []
    first: Dict[object, datetime] = {}
    level: Dict[object, float] = {}
    for d, ts, x in zip(dates, stamps, values):
        first.setdefault(d, ts)
        if ts < first[d] + timedelta(minutes=minutes):
            level[d] = x if d not in level else (max(level[d], x) if how == "max" else min(level[d], x))
    for d, ts in zip(dates, stamps):
        complete = ts + timedelta(minutes=base_minutes) >= first[d] + timedelta(minutes=minutes)
        out.append(level[d] if complete else None)
    return out
