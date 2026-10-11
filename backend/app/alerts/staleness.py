"""Z10-b (G5): stale data -> zero alerts, end to end.

An alert says "this matched on the bar closing at T". Once the market has produced newer bars, that statement is about
a market that no longer exists - the same reason the Phase G1 gate refuses a signal on stale candles. This module is the
one place that judges it, for every step an alert passes through (flag `alerts_stale_guard`, off by default):

* the bar-close engine, before it records a match (`app/alerts/barclose.py`);
* the rule flush, before held or late events become a notification (`app/alerts/engine.py`);
* the dispatcher, before each send - a retry, a delivery held by quiet hours or the hourly cap, a replayed dead letter
  (`app/alerts/dispatcher.py`).

Age is counted on the exchange clock: the number of bars of the rule's timeframe that have **closed** after the alert's
bar (NSE 09:15-15:30 IST, weekends and holidays skipped). Night and weekend hours add nothing, so a daily alert held by
quiet hours until 07:00 is still current; a 5-minute alert held for an hour is not. More than `MARKET_DATA_MAX_STALE_BARS`
newer bars (the G1 limit, 3 by default; 0 or less turns the check off, as in G1) = stale. A bar whose time or timeframe
cannot be read is stale too: the gate fails closed.

The verdict has the BT1 badge's shape (`stale`, `stale_reason`, `alerts_suppressed`), so the data-tier badge and the
alert path say the same thing about the same data.

Only market-data alerts are ever suppressed (`SUPPRESSIBLE`: screen / instrument rule alerts). Trade, exit, risk, broker,
security and system notifications are never held back here, and nothing in this module touches an order or an exit
(ADR-0004).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, MutableMapping, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import NotificationType
from app.market_data.calendar import IST, MARKET_CLOSE, MARKET_OPEN, is_trading_day

FLAG = "alerts_stale_guard"
REASON = "stale_data"
# Notification types the gate may hold back: alerts computed from market data. Everything else always goes out.
SUPPRESSIBLE = frozenset({NotificationType.SCREEN_ALERT.value})
INTRADAY_MINUTES = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60}
SUPPORTED = frozenset(INTRADAY_MINUTES) | {"1d"}
_MAX_DAYS = 40                                              # walk at most this many calendar days forward


def _utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def max_stale_bars() -> int:
    from app.core import config
    return int(getattr(config, "MARKET_DATA_MAX_STALE_BARS", 3))


def closed_bars_after(bar_start: datetime, tf: str, now: datetime, holidays: Iterable[date] = (), cap: int = 64) -> Optional[int]:
    """How many `tf` bars closed on the exchange clock after the bar starting at `bar_start`, up to `now` (counting
    stops at `cap`). None for a timeframe the engine does not evaluate."""
    if tf not in SUPPORTED:
        return None
    start = _utc(bar_start).astimezone(IST)
    until = _utc(now).astimezone(IST)
    hol = set(holidays)
    count = 0
    day = start.date()
    for _ in range(_MAX_DAYS):
        if day > until.date() or count >= cap:
            break
        if is_trading_day(day, hol):
            open_ = datetime.combine(day, MARKET_OPEN, IST)
            close = datetime.combine(day, MARKET_CLOSE, IST)
            if tf == "1d":
                count += int(day > start.date() and close <= until)      # by IST date: brokers label daily bars at 00:00 or 09:15
            else:
                step = timedelta(minutes=INTRADAY_MINUTES[tf])
                bucket = open_
                while bucket < close and min(bucket + step, close) <= until and count < cap:
                    count += int(bucket > start)
                    bucket += step
        day += timedelta(days=1)
    else:
        count = max(count, cap)                              # further back than the walk: as stale as it gets
    return count


def alert_badge(bar_start: Optional[datetime], tf: Optional[str], now: datetime, holidays: Iterable[date] = (),
                max_bars: Optional[int] = None) -> Dict[str, Any]:
    """The BT1-shaped verdict on an alert's data: `stale`, `stale_reason`, `alerts_suppressed`, plus `as_of` and
    `bars_behind`. Unknown bar time or timeframe -> stale (fails closed)."""
    limit = max_stale_bars() if max_bars is None else max_bars
    behind: Optional[int] = None
    reason: Optional[str] = None
    if limit <= 0:
        reason = None                                        # the operator turned the G1 limit off
    elif bar_start is None or not tf:
        reason = "the alert's data time is unknown"
    else:
        behind = closed_bars_after(bar_start, tf, now, holidays, cap=limit + 1)
        if behind is None:
            reason = f"the alert's timeframe {tf!r} cannot be judged"
        elif behind > limit:
            reason = f"market data stale: more than {limit} newer {tf} bar(s) have closed since the alert's bar"
    return {"as_of": _utc(bar_start).isoformat() if bar_start is not None else None, "timeframe": tf, "bars_behind": behind,
            "stale": reason is not None, "stale_reason": reason, "alerts_suppressed": reason is not None}


async def guard_on(session: AsyncSession, tenant_id: int, memo: Optional[MutableMapping[int, bool]] = None) -> bool:
    """Flag `alerts_stale_guard` for this organisation, remembered in `memo` for the rest of a cycle."""
    if memo is not None and tenant_id in memo:
        return memo[tenant_id]
    from app.platform.controls import flag_enabled
    on = await flag_enabled(session, FLAG, tenant_id)
    if memo is not None:
        memo[tenant_id] = on
    return on


def count_suppressed(kind: str, n: int = 1) -> None:
    """The G1 metric `market_data_stale`, with the step of the alert path that refused (alert_record, alert_flush,
    alert_send)."""
    if n <= 0:
        return
    from app.observability.metrics import MARKET_DATA_STALE
    MARKET_DATA_STALE.labels(kind=kind).inc(n)


__all__ = ["FLAG", "REASON", "SUPPRESSIBLE", "SUPPORTED", "alert_badge", "closed_bars_after", "count_suppressed", "guard_on", "max_stale_bars"]
