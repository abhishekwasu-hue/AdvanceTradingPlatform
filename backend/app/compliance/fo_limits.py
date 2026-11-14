"""Part D7 (rule IN-SEBI.risk.futeq_mwpl): the exchange's F&O ban period, from lake data (B1 `md_position_limits`).

When an underlying's open interest reaches `ban_threshold_pct` of its market-wide position limit, the exchange allows
only position-reducing trades in its derivatives. With FO_BAN_CHECK_ENABLED on, a new F&O entry (option, future or a
multi-leg structure) on such an underlying is refused - PAPER too, so paper results do not include trades the market
would not have allowed. Exits are never refused (ADR-0004). No lake row for the day (an index has no MWPL; or the data
is not loaded yet) means no refusal - the reason is returned as a note instead, never silently.

Runner S2 (docs/design/USER_RUNNER.md): the user's runner has no data lake. It reads the ban list ATP sends it
(`core.runtime_mode.ban_list`, the link in step S4). While no list has arrived it is unknown: a PAPER entry goes ahead
with a note; a LIVE F&O entry is refused with a plain reason. Exits never come here (ADR-0004).
"""
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.compliance import rules
from app.core import config, runtime_mode
from app.market_data.calendar import IST

RULE = "IN-SEBI.risk.futeq_mwpl"
UNKNOWN_LIVE = ("The F&O ban list has not reached your runner from ATP yet - new LIVE F&O entries wait for it; "
                "exits are not affected")


async def ban_status(session: AsyncSession, underlying: str, when: datetime, *,
                     as_of: datetime | None = None) -> tuple[str | None, str | None]:
    """(refusal, note) for a new F&O entry on `underlying` at `when`. Either or both may be None."""
    from app.market_lake.asof import position_limit_as_of  # the server's lake (never loaded on the runner)
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


def runner_ban_status(underlying: str, mode: str | None) -> tuple[str | None, str | None]:
    """Runner S2: (refusal, note) from the ban list ATP sent the runner. Unknown list: LIVE refused, PAPER a note."""
    names = runtime_mode.ban_list()
    if names is None:
        if (mode or "").upper() == "LIVE":
            return UNKNOWN_LIVE, None
        return None, f"F&O ban list not received from ATP yet - {underlying} not checked (PAPER)"
    if config.FO_BAN_CHECK_ENABLED and underlying.upper() in names:
        return f"{underlying} is in the F&O ban period - new F&O entries refused; exits allowed", None
    return None, None


async def entry_refusal(session: AsyncSession, underlying: str | None, when: datetime, *,
                        as_of: datetime | None = None, mode: str | None = None) -> tuple[str | None, str | None]:
    """The check the execution paths call: nothing unless the flag is on and the trade is a derivative on `underlying`.
    In the runner (S2) the list ATP sent is read instead; `mode` decides what an unknown list means there."""
    if not underlying:
        return None, None
    if runtime_mode.is_runner():
        return runner_ban_status(underlying, mode)
    if not config.FO_BAN_CHECK_ENABLED:
        return None, None
    return await ban_status(session, underlying, when, as_of=as_of)
