"""Glue between the AI layer and the trading worker / broker sessions - kept apart so the pure
modules (regime, monitor, generator) stay testable without a broker."""
from typing import Awaitable, Callable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.token_lifecycle import build_adapter, token_is_usable
from app.db.models import BrokerCredentialRecord, User
from app.market_data.service import MarketDataService


async def price_lookup_for_tenant(session: AsyncSession, tenant_id: int) -> Optional[Callable[[str], Awaitable[float]]]:
    """A `symbol -> LTP` coroutine off any of the tenant's usable broker sessions, or None."""
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
        return await MarketDataService(build_adapter(ent.record)).get_ltp(symbol, exchange)
    return lookup
