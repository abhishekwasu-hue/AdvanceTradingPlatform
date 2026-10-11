"""Part D7 (rule IN-SEBI.risk.futeq_mwpl): the exchange's F&O ban period, from lake data (B1 `md_position_limits`).

When an underlying's open interest reaches `ban_threshold_pct` of its market-wide position limit, the exchange allows
only position-reducing trades in its derivatives. With FO_BAN_CHECK_ENABLED on, a new F&O entry (option, future or a
multi-leg structure) on such an underlying is refused - PAPER too, so paper results do not include trades the market
would not have allowed. Exits are never refused (ADR-0004). No lake row for the day (an index has no MWPL; or the data
is not loaded yet) means no refusal - the reason is returned as a note instead, never silently.
"""
from datetime import datetime
from typing import Optional, Tuple

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
