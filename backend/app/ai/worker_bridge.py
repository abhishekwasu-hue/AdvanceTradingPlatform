"""Glue between the AI layer and the trading worker / broker sessions - kept apart so the pure
modules (regime, monitor, generator) stay testable without a broker."""
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.token_lifecycle import build_adapter, token_is_usable
from app.db.models import BrokerCredentialRecord, User
from app.market_data.service import MarketDataService


async def _exchange_of(session: AsyncSession, tenant_id: int, symbol: str) -> str:
    """The exchange the organisation's open position in `symbol` trades on (a contract is NFO / BFO / MCX), else NSE."""
    from app.db.models import TradeRecord
    from app.trading.position_monitor import exchange_for_trade
    trade = await session.scalar(select(TradeRecord).where(TradeRecord.tenant_id == tenant_id, TradeRecord.symbol == symbol,
                                                           TradeRecord.exit_time.is_(None)).limit(1))
    return exchange_for_trade(trade) if trade is not None else "NSE"


async def price_lookup_for_tenant(session: AsyncSession, tenant_id: int, user: Any = None) -> Optional[Callable[[str], Awaitable[float]]]:
    """A `symbol -> LTP` coroutine off any of the tenant's usable broker sessions, or None.

    BT1b (flag `byob_data_tier`): only `user`'s own session at a confirmed broker (its namespaced tick cache, then REST),
    per symbol inside its budget; None without a user or such a session."""
    from app.market_data import live_access
    if await live_access.byob_on(session, tenant_id):
        if user is None or getattr(user, "tenant_id", None) != tenant_id:
            return None
        if await live_access.own_session_record(session, user) is None:
            return None

        async def own_lookup(symbol: str, exchange: Optional[str] = None) -> float:
            exchange = exchange or await _exchange_of(session, tenant_id, symbol)
            own = await live_access.user_source(session, user, [(symbol, exchange)])
            if not own.allows(symbol, exchange):
                raise live_access.LiveDataRefused(own.reason(symbol, exchange))
            return await own.market_data().get_ltp(symbol, exchange)
        return own_lookup
    records = list(await session.scalars(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == tenant_id)))
    for record in records:
        if token_is_usable(record):
            service = MarketDataService(build_adapter(record))

            async def lookup(symbol: str, exchange: str = "NSE", _service=service) -> float:
                return await _service.get_ltp(symbol, exchange)
            return lookup
    return None


class NoLivePrice(LookupError):
    """Z3 (review M5/M8): the acting user has no Tier A (own, confirmed broker session) live price for the symbol."""


async def price_lookup_for_user(session: AsyncSession, user: User) -> Optional[Callable[..., Awaitable[float]]]:
    """A `(symbol, exchange) -> LTP` coroutine for one person's decision (an approval's re-quote, a manual /paper setup).

    With the `byob_data_tier` flag on, the price comes only through that user's own entitlement (BT1
    `entitlement.resolve`): their own broker session at a broker whose data terms are confirmed, inside its symbol
    budget. Anything below Tier A raises NoLivePrice - never a teammate's credential or a broker with pending terms.
    With the flag off, the organisation's first usable session, as before."""
    from app.market_data import entitlement
    from app.platform.controls import flag_enabled
    if not await flag_enabled(session, entitlement.FLAG, user.tenant_id):
        return await price_lookup_for_tenant(session, user.tenant_id)

    async def lookup(symbol: str, exchange: str = "NSE") -> float:
        ent = await entitlement.resolve(session, user, symbol, exchange, register=False)
        if not ent.live or ent.record is None:
            raise NoLivePrice(ent.reason or "No live price on your own broker session (Tier A) for this symbol")
        # BT1b: the session's own cache keys (its own ticks), never the platform-wide tick / candle keys.
        namespace = ent.session.namespace if ent.session is not None else None
        return await MarketDataService(build_adapter(ent.record), cache_namespace=namespace).get_ltp(symbol, exchange)
    return lookup
