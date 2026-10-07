"""Glue between the AI layer and the trading worker / broker sessions - kept apart so the pure
modules (regime, monitor, generator) stay testable without a broker."""
from typing import Awaitable, Callable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.token_lifecycle import build_adapter, token_is_usable
from app.db.models import BrokerCredentialRecord
from app.market_data.service import MarketDataService


async def price_lookup_for_tenant(session: AsyncSession, tenant_id: int) -> Optional[Callable[[str], Awaitable[float]]]:
    """A `symbol -> LTP` coroutine off any of the tenant's usable broker sessions, or None."""
    records = list(await session.scalars(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == tenant_id)))
    for record in records:
        if token_is_usable(record):
            service = MarketDataService(build_adapter(record))

            async def lookup(symbol: str, _service=service) -> float:
                return await _service.get_ltp(symbol)
            return lookup
    return None
