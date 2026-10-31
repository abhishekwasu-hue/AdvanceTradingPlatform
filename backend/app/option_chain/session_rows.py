"""M61 PCR-1 (owner, 30 Oct): which stored OI snapshots count as session rows, and which are partial.

An operational data-quality rule, not chart reading: the stored chain readings (the OI collector's slots,
`oi_snapshots`, and a user's own chain reads, `md_tenant_chain_snapshots`) are all kept, but only a reading taken
between the exchange's open and close on a trading day (calendar-aware: weekends and the exchange's holidays are not
trading days; NSE 09:15-15:30 IST) is a session row. A reading before the open is "pre-open", one after the close
"after close", one on a closed day "market closed": they are labelled and left out of the charts and the headline by
default (the tables keep them, labelled).

Partial: each reading's strike count (strikes with call and put OI known) is compared with the MEDIAN count of the
session's earlier session rows; below `ratio` x that median (OI_PARTIAL_SNAPSHOT_RATIO, default 0.9) it is "partial" -
some strikes were missing from the broker's answer, so its totals are not comparable with the others. The first
session row has no earlier median and is never partial. Only earlier rows are used, so a row's flag never changes
when later rows arrive. Nothing here reaches an order path (ADR-0006).
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime
from statistics import median

from app.brokers.models import OptionChain
from app.market_data import calendar

SESSION = "session"
PRE_OPEN = "pre_open"
AFTER_CLOSE = "after_close"
CLOSED_DAY = "closed_day"
PHASE_LABELS: dict[str, str] = {SESSION: "Session", PRE_OPEN: "Pre-open", AFTER_CLOSE: "After close", CLOSED_DAY: "Market closed"}


def phase(at: datetime, holidays: Iterable[date] = (), exchange: str = "NSE") -> str:
    """The session phase of a reading taken at `at` (any aware instant; naive = UTC) on `exchange`'s clock and calendar.
    The open and the close minute both count as the session (a 15:30 reading holds the closing OI)."""
    spec = calendar.EXCHANGE_SESSIONS[calendar.session_family(exchange)]
    if spec.always_open:
        return SESSION
    ist = calendar.to_ist(at)
    day = ist.date()
    if (spec.weekdays_only and day.weekday() >= 5) or day in set(holidays):
        return CLOSED_DAY
    clock = ist.time().replace(tzinfo=None)
    if clock < spec.open:
        return PRE_OPEN
    if clock > spec.close:
        return AFTER_CLOSE
    return SESSION


def strike_count(chain: OptionChain) -> int:
    """The strikes of a stored chain with OI on both sides known (a strike the broker sent without OI does not count)."""
    return sum(1 for r in chain.rows if r.call_oi is not None and r.put_oi is not None and (r.call_oi > 0 or r.put_oi > 0))


def partial_flags(counts: Sequence[int | None], phases: Sequence[str], ratio: float) -> list[bool]:
    """Per reading (oldest first): True when it is a session row whose strike count is below `ratio` x the median count
    of the EARLIER session rows. Rows outside the session, and rows without a count, are never partial."""
    seen: list[int] = []
    out: list[bool] = []
    for count, ph in zip(counts, phases):
        if ph != SESSION or count is None:
            out.append(False)
            continue
        out.append(bool(seen) and count < ratio * median(seen))
        seen.append(count)
    return out


__all__ = ["SESSION", "PRE_OPEN", "AFTER_CLOSE", "CLOSED_DAY", "PHASE_LABELS", "phase", "strike_count", "partial_flags"]
