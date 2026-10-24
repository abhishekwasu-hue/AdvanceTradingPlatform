"""TT-7 (owner, 24 Oct): ONE pure function per trigger rule, shared by the trigger engine (app/execution/trigger_engine.py,
live ticks and the worker cycle), the position monitor's level checks (app/trading/exit_logic.py) and both backtest
engines (app/backtest/engine.py through exit_logic, app/backtest/options_engine.py). A rule changed here changes in all
of them at once; tests/test_tm2_trigger_rules.py holds the parity.

* **touch** - any print reaches the level: a live tick is a one-price range (low == high == the tick); a backtest bar
  is its own low / high (it cannot see inside the bar);
* **close** - only a CLOSED candle of the order's own timeframe counts (`closed_candles`): a candle still forming is
  never read (no look-ahead), and a candle that closed before the order was armed never fires it;
* **stop first** - inside one bar (or one tick) where a stop and a target are both reached, the stop counts first
  (`STOP_FIRST`, the pessimistic way `options_engine` already resolved it).

No number here is a trading rule: the level, the side, touch / close and the timeframe are the user's own order settings.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional, Sequence

ABOVE, BELOW = "ABOVE", "BELOW"
TOUCH, CLOSE = "TOUCH", "CLOSE"
SIDES = (ABOVE, BELOW)
MODES = (TOUCH, CLOSE)
# Inside one bar / tick: a stop is judged before a target, a target before a waiting entry.
STOP_FIRST = ("STOP", "TARGET", "LIMIT")
IST = timezone(timedelta(hours=5, minutes=30))
NSE_OPEN_MINUTE = 9 * 60 + 15        # candles of N minutes are counted from the NSE open, as every chart counts them


def touched(level: float, side: str, low: float, high: float) -> bool:
    """The price reached `level` from its `side` within [low, high] (a tick: low == high)."""
    return high >= level if side == ABOVE else low <= level


def closed_beyond(level: float, side: str, close: float) -> bool:
    """A closed candle's `close` is at or beyond `level` on its `side`."""
    return close >= level if side == ABOVE else close <= level


def order_trigger_side(order_side: str, order_type: str) -> str:
    """Which way the price must go for an order to be reached: a BUY limit waits for the price to come DOWN to it, a BUY
    stop for the price to go UP through its trigger (and the reverse for a SELL) - the exchange's own meaning."""
    buy = order_side.upper() == "BUY"
    kind = order_type.upper()
    if kind == "LIMIT":
        return BELOW if buy else ABOVE
    if kind in ("SL", "SL-M"):
        return ABOVE if buy else BELOW
    raise ValueError(f"{order_type} does not wait for a price")


def stop_first(kinds: Iterable[str]) -> List[str]:
    """The order in which reached orders of one bar / tick are acted on: stops, then targets, then entries."""
    rank = {k: i for i, k in enumerate(STOP_FIRST)}
    return sorted(kinds, key=lambda k: rank.get(k, len(STOP_FIRST)))


@dataclass(frozen=True)
class Candle:
    start: datetime
    end: datetime
    close: float
    low: float
    high: float


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def closed_candles(bars: Sequence[object], timeframe_minutes: int, now: datetime, *,
                   anchor_minute: int = NSE_OPEN_MINUTE) -> List[Candle]:
    """N-minute candles built from ascending one-minute bars (each with `timestamp` = its start, `low`, `high`, `close`),
    counted from `anchor_minute` of the IST day; only candles that have CLOSED by `now` (end <= now) are returned. A
    one-minute bar still forming at `now` is never read."""
    if timeframe_minutes < 1:
        raise ValueError("timeframe_minutes must be at least 1")
    now = _utc(now)
    out: List[Candle] = []
    current: Optional[List] = None             # [start, end, close, low, high]
    for bar in bars:
        start = _utc(getattr(bar, "timestamp"))
        if start + timedelta(minutes=1) > now:
            break                              # forming (or future) bar: never read
        ist = start.astimezone(IST)
        minute = ist.hour * 60 + ist.minute - anchor_minute
        bucket = minute // timeframe_minutes
        day = ist.replace(hour=0, minute=0, second=0, microsecond=0)
        c_start = (day + timedelta(minutes=anchor_minute + bucket * timeframe_minutes)).astimezone(timezone.utc)
        low, high, close = float(getattr(bar, "low")), float(getattr(bar, "high")), float(getattr(bar, "close"))
        if current is None or current[0] != c_start:
            if current is not None:
                out.append(Candle(*current))
            current = [c_start, c_start + timedelta(minutes=timeframe_minutes), close, low, high]
        else:
            current[2] = close
            current[3] = min(current[3], low)
            current[4] = max(current[4], high)
    if current is not None:
        out.append(Candle(*current))
    return [c for c in out if c.end <= now]


def close_trigger(level: float, side: str, bars: Sequence[object], timeframe_minutes: int, now: datetime,
                  armed_at: Optional[datetime] = None) -> Optional[Candle]:
    """The first closed candle (of `timeframe_minutes`, ending after `armed_at`) whose close is at or beyond `level`."""
    since = _utc(armed_at) if armed_at is not None else None
    for candle in closed_candles(bars, timeframe_minutes, now):
        if since is not None and candle.end <= since:
            continue
        if closed_beyond(level, side, candle.close):
            return candle
    return None
