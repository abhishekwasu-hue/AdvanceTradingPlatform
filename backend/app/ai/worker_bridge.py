"""Glue between the AI layer and the trading worker / broker sessions - kept apart so the pure
modules (regime, monitor, generator) stay testable without a broker."""
from typing import Awaitable, Callable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.base import BrokerInterface
from app.brokers.token_lifecycle import build_adapter, token_is_usable
from app.db.models import BrokerCredentialRecord
from app.market_data.service import MarketDataService


async def broker_for_trade(session: AsyncSession, trade) -> Optional["BrokerInterface"]:
    """P0.8 / A1: the adapter for the session a LIVE trade was placed through (`broker_name`, and the account when the
    trade carries one), or None - an approved AI exit on a LIVE position must square off at the broker, never only in
    the database."""
    if trade is None or trade.mode != "LIVE":
        return None
    from app.db.models import BrokerAccountRecord
    query = select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == trade.tenant_id)
    account = await session.get(BrokerAccountRecord, trade.broker_account_id) if getattr(trade, "broker_account_id", None) else None
    if account is not None:
        query = query.where(BrokerCredentialRecord.broker_name == account.broker_name, BrokerCredentialRecord.account_label == account.account_label)
    for record in await session.scalars(query.order_by(BrokerCredentialRecord.id)):
        if token_is_usable(record):
            return build_adapter(record)
    return None


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
