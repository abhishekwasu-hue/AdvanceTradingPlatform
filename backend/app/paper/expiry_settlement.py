"""PAPER expiry settlement: an open PAPER index option or index future still held after its expiry close is settled the way
the exchange settles it - in cash, against the final settlement price.

Why (owner bug report): a Terminal PAPER option bought on an earlier day stayed "open" after its expiry. The only expiry
close ATP had was the trading worker's held-spread settlement (M55 T1: a deployment's SWING legs in a group, at their last
price); a single Terminal position (no deployment, no group) was never settled, and an expired contract has no quote to
close it at, so it stayed open with no mark.

The rule (NSE index derivatives are cash-settled; no shares change hands):
- the final settlement price is the closing value of the underlying index on the expiry day - read here from the stored
  Daily bar of that day only (the licensed exchange close files, `eod_routes.daily_bars`; never a later bar: no look-ahead);
- an option settles at its intrinsic value against it: CE max(close - strike, 0), PE max(strike - close, 0);
- a future settles at the close itself.

How it is booked: through the one exit path (`position_monitor.close_position`), reason "Expired - settled", with the exit
time at the expiry day's close (15:30 IST), so the P&L belongs to the expiry day (the Terminal's "Today" strip counts it on
that day, never on the later day the sweep ran). An exit: never blocked, never by the AI (ADR-0004 / ADR-0006).

What it never touches:
- a LIVE position (the broker settles it; reconciliation reads what the broker reports);
- a stock option or stock future (they settle by delivery of shares - app/paper/settlement_watch.py warns before expiry);
- a contract whose strike / right cannot be read, or whose expiry-day close is not stored yet: left open and tried again on
  the next sweep (logged), nothing is made up.

Idempotent: only rows with no exit are read, and close_position claims the close atomically (one P&L, one notification).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InstrumentRecord, TradeRecord
from app.instruments import master
from app.market_data.calendar import IST, MARKET_CLOSE

logger = logging.getLogger(__name__)

SETTLED_REASON = "Expired - settled"
SWEEP_SECONDS = 600            # the worker's pass: every ten minutes (an indexed read of the open rows with an expiry)
KINDS = ("OPTION", "FUTURE")
PAPER = "PAPER"


@dataclass
class SettleReport:
    settled: list[int] = field(default_factory=list)
    waiting: list[tuple[int, str]] = field(default_factory=list)     # (trade id, why it was left open)


def expiry_close_at(expiry: date) -> datetime:
    """The expiry day's close (15:30 IST) as an aware UTC instant - the settlement's exit time."""
    return datetime.combine(expiry, MARKET_CLOSE, tzinfo=IST).astimezone(UTC)


def is_past_expiry_close(expiry: date, now: datetime) -> bool:
    return now >= expiry_close_at(expiry)


def settlement_value(right: str, strike: float | None, close: float) -> float | None:
    """The per-unit value at the final settlement price `close`. Pure."""
    if right == "FUT":
        return round(float(close), 2)
    if strike is None:
        return None
    if right == "CE":
        return round(max(float(close) - float(strike), 0.0), 2)
    if right == "PE":
        return round(max(float(strike) - float(close), 0.0), 2)
    return None


async def _contract(session: AsyncSession, trade: TradeRecord) -> tuple[str, str, float | None] | None:
    """(underlying, right, strike) of the position's contract: the trading symbol first (it names the contract by itself;
    the daily master sync drops an expired contract), else the instrument master's row by its key. None when unread."""
    from app.paper.fno_contracts import parse_symbol
    parsed = parse_symbol(trade.symbol)
    if parsed is not None:
        return parsed.underlying, parsed.right, parsed.strike
    if trade.instrument_key:
        row = await session.scalar(select(InstrumentRecord).where(InstrumentRecord.instrument_key == trade.instrument_key).limit(1))
        if row is not None and row.underlying and row.instrument_type in ("CE", "PE", "FUT"):
            strike = float(row.strike) if row.strike is not None else None
            return row.underlying.upper(), row.instrument_type, strike
    return None


async def expiry_close(session: AsyncSession, underlying: str, expiry: date) -> float | None:
    """The stored closing value of `underlying` on its expiry day: the Daily bar ending at that day's close, from the
    licensed exchange files only. None when that day's bar is not stored (yet). Never a later bar."""
    from app.market_data.eod_routes import daily_bars
    end = expiry_close_at(expiry)
    bars = await daily_bars(session, underlying, end - timedelta(hours=12), end)
    for bar in reversed(bars):
        ts = bar.ts if bar.ts.tzinfo else bar.ts.replace(tzinfo=UTC)
        if ts.astimezone(IST).date() == expiry and ts <= end:
            return float(bar.close)
    return None


def _due_query(now: datetime, tenant_id: int | None):
    today = now.astimezone(IST).date()
    last_due = today if now.astimezone(IST).time() >= MARKET_CLOSE else today - timedelta(days=1)
    query = select(TradeRecord).where(
        TradeRecord.exit_time.is_(None), TradeRecord.mode == PAPER, TradeRecord.instrument_kind.in_(KINDS),
        TradeRecord.expiry.is_not(None), TradeRecord.expiry <= last_due)
    if tenant_id is not None:
        query = query.where(TradeRecord.tenant_id == tenant_id)
    return query.order_by(TradeRecord.expiry, TradeRecord.id)


async def settle_expired(session: AsyncSession, now: datetime | None = None, *, tenant_id: int | None = None) -> SettleReport:
    """Settles every open PAPER index option / future past its expiry close (one tenant, or all). Never raises for one
    position: a failure is logged and that position is tried again on the next pass."""
    from app.trading.position_monitor import close_position
    now = now or datetime.now(UTC)
    report = SettleReport()
    trades = list(await session.scalars(_due_query(now, tenant_id)))
    closes: dict[tuple[str, date], float | None] = {}
    for trade in trades:
        trade_id = trade.id
        if trade.mode != PAPER or trade.exit_time is not None or trade.expiry is None:
            continue                                             # belt and braces: a LIVE row is never touched here
        expiry = trade.expiry
        if not is_past_expiry_close(expiry, now):
            continue
        contract = await _contract(session, trade)
        if contract is None:
            report.waiting.append((trade_id, "contract not read from its name"))
            continue
        underlying, right, strike = contract
        if underlying not in master.INDEX_SYMBOLS:
            report.waiting.append((trade_id, "stock F&O settles by delivery"))
            continue
        key = (underlying, expiry)
        if key not in closes:
            closes[key] = await expiry_close(session, underlying, expiry)
        close = closes[key]
        if close is None:
            report.waiting.append((trade_id, "expiry-day close not stored yet"))
            continue
        value = settlement_value(right, strike, close)
        if value is None:
            report.waiting.append((trade_id, "contract not read from its name"))
            continue
        try:
            outcome = await close_position(session, trade, value, SETTLED_REASON, broker=None, user_id=trade.user_id,
                                           now=expiry_close_at(expiry), underlying_price=close)
        except Exception as exc:  # noqa: BLE001 - one position never ends the pass; tried again next time
            logger.error("Expiry settlement of trade %s failed: %s", trade_id, exc)
            await session.rollback()
            continue
        if outcome.closed:
            report.settled.append(trade_id)
    if report.settled:
        logger.info("Expiry settlement: %d PAPER position(s) settled", len(report.settled))
    for trade_id, why in report.waiting:
        logger.debug("Expiry settlement: trade %s left open (%s)", trade_id, why)   # every pass: debug, not info
    return report


__all__ = ["SETTLED_REASON", "SWEEP_SECONDS", "SettleReport", "expiry_close", "expiry_close_at", "is_past_expiry_close",
           "settle_expired", "settlement_value"]
