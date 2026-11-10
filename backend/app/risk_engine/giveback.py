"""M94 R3 (owner order M94, spec docs/design/M94_INTRADAY_PACK.md R3; OPEN_QUESTIONS M94-Q8): the profit give-back stop.

The rule (the trader's own, OFF until he switches "Profit give-back stop" on in Settings > Risk limits and saves his %):
once today's P&L has fallen back his % from today's peak profit, no NEW entry for the rest of the trading day. Exits are
never checked (ADR-0004): nothing here is asked on an exit, a close or a square-off.

* Today's P&L is the one the daily loss limit reads (app/trading/persistence.day_pnl): closed today + open today at its
  last mark, per organisation, the Indian trading day.
* Today's peak is the highest of those values ATP saw today - on every worker cycle (`sweep`, stored) and every entry
  check (`state_for`, through `build_trading_day_state`: the value now counts for that check). It only moves up. No
  minimum peak: a give-back needs a profit (a peak above 0). A peak stored on another day reads as no peak - it starts
  afresh each trading day (M94-Q8).
* Once reached, the day is marked (`giveback_hit_at`), so new entries stay refused for the rest of that day even when the
  P&L recovers ("for the rest of the day"). Switching the check off lifts it (the engine then reads it as not set).

Storage: the organisation's `risk_settings` row (`giveback_pct`, `day_peak_pnl`, `day_peak_on`, `giveback_hit_at`). Only the
worker cycle writes it, in its own short transaction: an entry check only reads, so no order path holds a lock that could
hold up the worker's exits. Each write is a conditional UPDATE that only raises the peak / sets the mark once a day, and
`updated_at` (the trader's last save) is left as it was. Nothing is read or written for an organisation with the check off.
The marks of open positions are written by the worker cycle itself, so the cycle sees every P&L an entry check can see.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RiskSettingsRecord
from app.market_data.calendar import to_ist
from app.risk_engine import risk_limits

CHECK_ID = "profit_giveback"


# --- pure ---------------------------------------------------------------------------------------------------------------

def trading_day(now: datetime | None = None) -> date:
    """The Indian trading day `now` belongs to (the day the daily counters use, app/market_data/calendar.trading_day_start)."""
    return to_ist(now).date()


def peak_today(stored_peak: float | None, stored_day: date | None, today: date, current: float) -> float:
    """Today's peak: the higher of the peak stored for today and the current P&L (a peak of another day does not count)."""
    if stored_peak is not None and stored_day == today and stored_peak > current:
        return float(stored_peak)
    return float(current)


def reached(peak: float, current: float, pct: float) -> bool:
    """Whether today's P&L fell back `pct` % (or more) from a peak profit. No profit (peak at or below 0): never."""
    return peak > 0 and (peak - current) >= peak * pct / 100.0 - 1e-9


@dataclass(frozen=True)
class DayPeak:
    """Today's peak and whether the give-back holds now (reached now or earlier today)."""
    peak: float
    hit: bool


def on_of(record: Any) -> float | None:
    """The trader's own give-back % when the check is switched on with a value, else None (off: nothing is tracked)."""
    if record is None:
        return None
    pct = getattr(record, "giveback_pct", None)
    if not isinstance(pct, (int, float)) or not risk_limits.is_set("giveback_pct", pct):
        return None
    return float(pct) if CHECK_ID in risk_limits.switches_of(record)["enabled"] else None


# --- stored -------------------------------------------------------------------------------------------------------------

async def _store(session: AsyncSession, tenant_id: int, today: date, peak: float, hit_at: datetime | None) -> None:
    """Stores today's peak (only ever up; a new day starts afresh and clears the previous day's mark) and marks the
    give-back reached at `hit_at` (once a day). Conditional UPDATEs in the caller's transaction; `updated_at` (the
    trader's last save) is kept."""
    row = RiskSettingsRecord
    keep_saved_at = {"updated_at": row.updated_at}
    new_day = or_(row.day_peak_on.is_(None), row.day_peak_on != today)
    await session.execute(update(row).where(row.tenant_id == tenant_id, new_day)
                          .values(day_peak_pnl=peak, day_peak_on=today, giveback_hit_at=None, **keep_saved_at)
                          .execution_options(synchronize_session=False))
    await session.execute(update(row).where(row.tenant_id == tenant_id, row.day_peak_on == today,
                                            or_(row.day_peak_pnl.is_(None), row.day_peak_pnl < peak))
                          .values(day_peak_pnl=peak, **keep_saved_at).execution_options(synchronize_session=False))
    if hit_at is not None:
        await session.execute(update(row).where(row.tenant_id == tenant_id, row.day_peak_on == today, row.giveback_hit_at.is_(None))
                              .values(giveback_hit_at=hit_at, **keep_saved_at).execution_options(synchronize_session=False))


async def observe(session: AsyncSession, record: Any, current: float, *, now: datetime | None = None,
                  store: bool = False) -> DayPeak | None:
    """Today's peak for one organisation's row with today's P&L `current` (None: the check is off). `store` (the worker
    cycle only): a higher peak is stored, and the mark the first time the give-back is reached today."""
    pct = on_of(record)
    if pct is None:
        return None
    at = now or datetime.now(timezone.utc)
    today = trading_day(at)
    stored_day = getattr(record, "day_peak_on", None)
    stored_peak = getattr(record, "day_peak_pnl", None)
    peak = peak_today(stored_peak, stored_day, today, current)
    hit_before = stored_day == today and getattr(record, "giveback_hit_at", None) is not None
    hit_now = reached(peak, current, pct)
    higher = stored_day != today or stored_peak is None or peak > float(stored_peak)
    if store and (higher or (hit_now and not hit_before)):
        await _store(session, int(record.tenant_id), today, peak, at if hit_now and not hit_before else None)
    return DayPeak(peak=peak, hit=hit_before or hit_now)


async def state_for(session: AsyncSession, tenant_id: int, current: float, *, now: datetime | None = None) -> DayPeak | None:
    """The entry check's reading (app/trading/persistence.build_trading_day_state): today's peak and whether the give-back
    holds, or None with the check off. Reads only (see the module note)."""
    record = await session.scalar(select(RiskSettingsRecord).where(RiskSettingsRecord.tenant_id == tenant_id))
    return await observe(session, record, current, now=now)


async def sweep(session: AsyncSession, now: datetime | None = None) -> int:
    """The worker cycle: today's peak of every organisation with the check on, from its P&L now (closed + open marks).
    Commits; returns how many organisations were read. Bookkeeping only - it never places, blocks or changes an order."""
    from app.trading.persistence import day_pnl
    rows = list(await session.scalars(select(RiskSettingsRecord).where(RiskSettingsRecord.giveback_pct.is_not(None))))
    read = 0
    for record in rows:
        if on_of(record) is None:
            continue
        await observe(session, record, await day_pnl(session, int(record.tenant_id), now=now), now=now, store=True)
        read += 1
    if read:
        await session.commit()
    return read


__all__ = ["CHECK_ID", "DayPeak", "trading_day", "peak_today", "reached", "on_of", "observe", "state_for", "sweep"]
