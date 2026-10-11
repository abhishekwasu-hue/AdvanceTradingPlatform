"""Glue between the AI layer and the trading worker / broker sessions - kept apart so the pure
modules (regime, monitor, generator) stay testable without a broker."""
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.token_lifecycle import build_adapter, token_is_usable
from app.db.models import BrokerCredentialRecord
from app.market_data.service import MarketDataService


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

        async def own_lookup(symbol: str) -> float:
            own = await live_access.user_source(session, user, [(symbol, "NSE")])
            if not own.allows(symbol, "NSE"):
                raise live_access.LiveDataRefused(own.reason(symbol, "NSE"))
            return await own.market_data().get_ltp(symbol)
        return own_lookup
    records = list(await session.scalars(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == tenant_id)))
    for record in records:
        if token_is_usable(record):
            service = MarketDataService(build_adapter(record))

            async def lookup(symbol: str, _service=service) -> float:
                return await _service.get_ltp(symbol)
            return lookup
    return None
