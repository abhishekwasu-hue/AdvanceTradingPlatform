"""Part D7 (rule IN-SEBI.risk.futeq_mwpl): the exchange's F&O ban period, from lake data (B1 `md_position_limits`).

When an underlying's open interest reaches `ban_threshold_pct` of its market-wide position limit, the exchange allows
only position-reducing trades in its derivatives. With FO_BAN_CHECK_ENABLED on, a new F&O entry (option, future or a
multi-leg structure) on such an underlying is refused - PAPER too, so paper results do not include trades the market
would not have allowed. Exits are never refused (ADR-0004). No lake row for the day (an index has no MWPL; or the data
is not loaded yet) means no refusal - the reason is returned as a note instead, never silently.
"""
from datetime import datetime
from typing import Any, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.compliance import rules
from app.core import config
from app.market_data.calendar import IST
from app.market_lake.asof import position_limit_as_of

RULE = "IN-SEBI.risk.futeq_mwpl"


async def ban_status(session: AsyncSession, underlying: str, when: datetime, *,
                     as_of: Optional[datetime] = None) -> Tuple[Optional[str], Optional[str]]:
    """(refusal, note) for a new F&O entry on `underlying` at `when`. Either or both may be None."""
    day = when.astimezone(IST).date()
    row = await position_limit_as_of(session, (underlying or "").upper(), day, as_of=as_of)
    if row is None:
        return None, f"No MWPL / OI data for {underlying} on {day.isoformat()} - F&O ban not checked"
    threshold = float(rules.load().param(RULE, "ban_threshold_pct", 0) or 0)
    used = 100.0 * float(row.open_interest) / float(row.mwpl) if row.mwpl else 0.0
    if threshold > 0 and used >= threshold:
        return (f"{underlying} is in the F&O ban period (open interest {used:.1f}% of MWPL, ban at {threshold:g}%) - "
                "new F&O entries refused; exits allowed"), None
    return None, None


async def entry_refusal(session: AsyncSession, underlying: Optional[str], when: datetime, *,
                        as_of: Optional[datetime] = None) -> Tuple[Optional[str], Optional[str]]:
    """The check the execution paths call: nothing unless the flag is on and the trade is a derivative on `underlying`."""
    if not config.FO_BAN_CHECK_ENABLED or not underlying:
        return None, None
    return await ban_status(session, underlying, when, as_of=as_of)


# --- the rest of D7 (N7): client-level position limit, lot size as of the trade date, expiry-day margin -----------------
# Behind the platform flag `fo_position_limits` (off by default; per organisation). The ban check above keeps its own
# switch (FO_BAN_CHECK_ENABLED). Exits are never refused: every check here is for NEW entries only (ADR-0004).

FLAG = "fo_position_limits"


async def limits_on(session: AsyncSession, tenant_id: Optional[int]) -> bool:
    from app.platform.controls import flag_enabled
    return tenant_id is not None and await flag_enabled(session, FLAG, tenant_id)


def _param(name: str, default: float) -> float:
    value = rules.load().param(RULE, name, default)
    return float(default if value is None else value)


async def open_derivative_quantity(session: AsyncSession, *, tenant_id: int, user_id: int, underlying: str, mode: str) -> float:
    """The caller's own open F&O quantity on `underlying` (contracts in units of the underlying, every expiry and strike;
    the same mode only - a PAPER book is not a LIVE one)."""
    from sqlalchemy import func, select

    from app.db.models import TradeRecord
    total = await session.scalar(select(func.coalesce(func.sum(TradeRecord.quantity), 0.0)).where(
        TradeRecord.tenant_id == tenant_id, TradeRecord.user_id == user_id, TradeRecord.mode == mode, TradeRecord.exit_time.is_(None),
        TradeRecord.instrument_kind != "UNDERLYING", func.upper(TradeRecord.underlying_symbol) == (underlying or "").upper()))
    return float(total or 0.0)


async def client_limit_cap(session: AsyncSession, *, tenant_id: int, user_id: int, underlying: str, mode: str, lot: int, when: datetime,
                           as_of: Optional[datetime] = None) -> Tuple[Optional[float], Optional[str], Optional[str]]:
    """(max new quantity, refusal, note) under the client-level limit for `underlying` on the trade day: the higher of
    `client_limit_mwpl_pct` % of MWPL (1 % of free float = 5 % of MWPL, which is 20 % of free float) and
    `client_limit_oi_pct` % of the open interest (NSE's stock F&O client limit), less the caller's open quantity there.
    Rounded down to whole lots; under one lot is a refusal. No lake row -> no cap, a note (never silent)."""
    day = when.astimezone(IST).date()
    row = await position_limit_as_of(session, (underlying or "").upper(), day, as_of=as_of)
    if row is None:
        return None, None, f"No MWPL / OI data for {underlying} on {day.isoformat()} - client position limit not checked"
    limit = max(float(row.mwpl or 0) * _param("client_limit_mwpl_pct", 5.0) / 100.0,
                float(row.open_interest or 0) * _param("client_limit_oi_pct", 5.0) / 100.0)
    held = await open_derivative_quantity(session, tenant_id=tenant_id, user_id=user_id, underlying=underlying, mode=mode)
    room = limit - held
    lot = max(int(lot or 1), 1)
    lots = int(room // lot) if room > 0 else 0
    if lots < 1:
        return 0.0, (f"Client position limit on {underlying}: {held:,.0f} held of {limit:,.0f} allowed (the higher of "
                     f"{_param('client_limit_mwpl_pct', 5.0):g}% of MWPL and {_param('client_limit_oi_pct', 5.0):g}% of open interest) - "
                     "no room for one more lot; exits allowed"), None
    return float(lots * lot), None, f"Client position limit on {underlying}: room for {lots} lot(s) ({room:,.0f} of {limit:,.0f})"


def dated_lot(underlying: str, expiry: Optional[Any]) -> Optional[int]:
    """The exchange's lot for the series expiring on `expiry`, from the dated lot table (revisions apply to the series
    expiring on or after their date) - None when the table has no entry for the underlying."""
    if expiry is None:
        return None
    from app.instruments import expiry_calendar
    try:
        value = expiry_calendar.dated_lot_size(underlying, expiry)
    except Exception:  # noqa: BLE001 - a table that cannot be read is "unknown", not a refusal
        return None
    return int(value) if value else None


def lot_refusal(contract: Any) -> Tuple[Optional[str], Optional[str]]:
    """(refusal, note): an order sized on the instrument master's lot when the exchange's lot for that series (as of the
    contract's expiry) is different - a stale master would make a quantity the exchange rejects."""
    expected = dated_lot(getattr(contract, "underlying", "") or "", getattr(contract, "expiry", None))
    if expected is None:
        return None, f"No dated lot size for {getattr(contract, 'underlying', '?')} - the instrument master's lot is used"
    have = int(getattr(contract, "lot_size", 0) or 0)
    if have != expected:
        return (f"Lot size of {getattr(contract, 'tradingsymbol', '?')}: the instrument master says {have}, the exchange's lot for "
                f"the series expiring {contract.expiry} is {expected} - refresh the instrument master; order refused"), None
    return None, None


def expiry_day_factor(expiry: Optional[Any], when: datetime) -> float:
    """The margin multiplier for a SHORT option on its own expiry day (`expiry_day_margin_multiplier`, rule-set data;
    1 elsewhere) - SEBI's additional exposure margin on expiring short options."""
    if expiry is None or when.astimezone(IST).date() != expiry:
        return 1.0
    return max(_param("expiry_day_margin_multiplier", 1.0), 1.0)
