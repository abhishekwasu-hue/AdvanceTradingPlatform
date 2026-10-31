"""M71 PL-3: the reader's OWN broker session as the REST source of the newest bars (M71Q-2, PL-5 terms).

The newest bars are topped up only through a credential the reader linked himself (or, for the tenant's OWNER, the
tenant's own credential) - never through another member's session, so one user's broker data never reaches another.
No usable own session -> None: the store's bars are read and the newest ones are a reported gap.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.market_store.models import RestSource
from app.market_store.reader import may_top_up


def from_record(user: Any, record: Any, adapter: Any, wait_seconds: float | None = None) -> RestSource | None:
    """A RestSource over an adapter the caller already built for `record`, when `user` may read through it."""
    if adapter is None or not may_top_up(user, record):
        return None
    return RestSource(adapter=adapter, record_id=int(record.id), broker=str(record.broker_name), wait_seconds=wait_seconds)


async def own_rest(session: AsyncSession, user: Any, symbol: str, exchange: str, *, broker: str | None = None) -> RestSource | None:
    """The reader's own usable session for `symbol` (the bring-your-own-broker entitlement when that tier is on, else
    the tenant's usable session when he may read through it), or None."""
    from fastapi import HTTPException

    from app.market_data import live_access
    if user is None:
        return None
    tenant_id = getattr(user, "tenant_id", None)
    try:
        if await live_access.byob_on(session, tenant_id):
            own = await live_access.user_source(session, user, [(symbol, exchange)], broker=broker, register=False)
            if not own.allows(symbol, exchange):
                return None
            return from_record(user, own.record, own.adapter())
        from app.brokers.token_lifecycle import build_adapter
        from app.market_data.candles_routes import _pick_record
        record = await _pick_record(session, int(tenant_id or 0), broker, "primary")
    except (HTTPException, live_access.LiveDataRefused):
        return None
    if not may_top_up(user, record):
        return None
    return from_record(user, record, build_adapter(record))


__all__ = ["from_record", "own_rest"]
