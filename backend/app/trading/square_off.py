"""The INTRADAY square-off at the owner's own time, and the expiry settlement of a structure held for days.

Moved here unchanged from the worker (`TradingWorker._square_off_all`, which now calls it) so the user's runner
(Runner S2, docs/design/USER_RUNNER.md) runs the same exits as the server - never a second copy (ADR-0004: exits are
never blocked; one close that fails never ends the square-off). `price_lookup(symbol, exchange)` is the cycle's LTP
read (the worker: MarketDataService.get_ltp; the runner: the user's own broker feed). The close functions are read
from `position_monitor` at call time, so a test that replaces them there sees every caller.
"""
import logging
from collections.abc import Awaitable, Callable
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.base import BrokerInterface
from app.core.enums import ExecutionMode
from app.db.models import TradeRecord
from app.db.savepoint import guarded
from app.execution import exit_settings
from app.market_data.calendar import session_family
from app.trading import position_monitor as pm

logger = logging.getLogger(__name__)

PriceLookup = Callable[[str, str], Awaitable[float]]


def held_spread_expires(trade: TradeRecord, today: date | None) -> bool:
    """M55 T1: a deployment's structure held for days (SWING legs in a group) settles at the square-off time of its expiry
    day, as the option backtest settles it (SETTLEMENT_FROM). Single swing positions are unchanged."""
    expiry = getattr(trade, "expiry", None)
    return (today is not None and expiry is not None and bool(trade.leg_group_id) and trade.deployment_id is not None
            and expiry <= today)


async def square_off_due(
    session: AsyncSession, tenant_id: int, price_lookup: PriceLookup,
    live_broker: BrokerInterface | None, user_id: int | None, families: set | None = None,
    today: date | None = None, settle_families: set | None = None, now_ist: datetime | None = None,
) -> int:
    """M68 PR-3 (TS1-026, D1): the INTRADAY positions whose owner's own Square-off time has come (Not set: none - ATP
    squares off nothing by itself), and the held spreads expiring today on a venue in `settle_families` (M55 T1).
    `now_ist` None (a direct call): every INTRADAY position of `families` whose owner set a time, as at that time."""
    open_trades = list(await session.scalars(
        select(TradeRecord).where(TradeRecord.tenant_id == tenant_id, TradeRecord.exit_time.is_(None))
    ))
    venues = await pm.trade_venues(session, open_trades)       # BK2-8: a row without an exchange follows its deployment
    closed = 0
    groups: dict[str, list[TradeRecord]] = {}
    expiring: dict[str, list[TradeRecord]] = {}
    own: dict[int, exit_settings.ExitSettings] = {}
    settle = families if settle_families is None else settle_families
    for trade in open_trades:
        family = session_family(venues[trade.id])
        if families is not None and family not in families:
            continue
        if (getattr(trade, "holding", None) or "INTRADAY") == "SWING":
            if (settle is None or family in settle) and held_spread_expires(trade, today):
                expiring.setdefault(trade.leg_group_id, []).append(trade)   # M55 T1: settles on its expiry day
            continue   # Phase AS: a swing position is held overnight by design
        if trade.user_id not in own:
            own[trade.user_id] = await exit_settings.load(session, tenant_id=tenant_id, user_id=trade.user_id)
        cut = own[trade.user_id].square_off_at(family)
        if cut is None or (now_ist is not None and now_ist.time() < cut):
            continue   # M68 D1: his own time not set (or not reached) - ATP closes nothing; his broker's MIS close is a fact
        if trade.leg_group_id:
            groups.setdefault(trade.leg_group_id, []).append(trade)
            continue
        try:
            price = await price_lookup(trade.symbol, venues[trade.id])
        except Exception as exc:  # noqa: BLE001
            logger.warning("Square-off: no price for %s (%s) - left open", trade.symbol, exc)
            continue
        broker = None
        if trade.mode == ExecutionMode.LIVE.value:
            # #221 MED-2: the trade's own account (cached per cycle), as the monitor does, when no LIVE deployment named
            # a session - a LIVE position with no deployment is squared off too (an exit: the Phase 0 chokepoint lets
            # it through).
            broker = live_broker or await pm.broker_for_trade(session, trade)
        trade_id = trade.id
        try:
            # Review LOW-2 (ADR-0004): one savepoint per position - one close that fails never ends the square-off.
            async with guarded(session, keep=(trade,)):
                outcome = await pm.close_position(session, trade, price, "Intraday square-off", broker=broker, user_id=user_id)
            closed += int(outcome.closed)
        except Exception as exc:  # noqa: BLE001 - left open, squared off again next cycle
            logger.error("Square-off of trade %s failed: %s", trade_id, exc)
    jobs = [(gid, legs, "Intraday square-off") for gid, legs in groups.items()]
    jobs += [(gid, legs, "Expiry settlement") for gid, legs in expiring.items()]
    for group_id, legs, reason in jobs:
        # MED-1 (naked-short guard): a structure's legs close through close_group - shorts first, and its wings are
        # held while a LIVE short has not closed (selling them first would leave a naked short).
        prices: dict[int, float] = {}
        for leg in legs:
            try:
                prices[leg.id] = await price_lookup(leg.symbol, venues[leg.id])
            except Exception as exc:  # noqa: BLE001 - that leg is left open (a short without a quote holds its wings)
                logger.warning("Square-off: no price for %s (%s) - left open", leg.symbol, exc)
        try:
            async with guarded(session, keep=tuple(legs)):
                outcomes = await pm.close_group(session, legs, prices, reason, broker=live_broker, user_id=user_id)
            closed += sum(int(o.closed) for o in outcomes)
        except Exception as exc:  # noqa: BLE001 - left open, squared off again next cycle
            logger.error("Square-off of group %s failed: %s", group_id, exc)
    if closed:
        logger.info("Square-off closed %d position(s) for tenant %s", closed, tenant_id)
    return closed


__all__ = ["held_spread_expires", "square_off_due"]
