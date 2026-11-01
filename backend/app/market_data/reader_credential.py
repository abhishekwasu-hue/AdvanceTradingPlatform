"""M71Q-2 (data terms): the ONE choice of broker credential for a MARKET-DATA read made for a person.

With `byob_data_tier` on, every read already goes through the reader's own entitlement (`live_access.user_source`).
With the flag off, the page routes used to read through the organisation's first usable credential, so in an
organisation with several members one member's broker data could be read for another. `credential_for_reader` picks
only among the credentials the reader linked himself - the same choice as before inside that set (named broker, else
a logged-in session first, else an analytics-token row; never a broker whose market-data gate is closed). A teammate's
credential is never used: the read raises 409 with plain words ("link your own broker"), and the caller serves the
own market-data store (M71) when it holds the data, else its existing "connect your broker" state.

Single-member organisations: the only member linked every credential, so the choice is exactly as before.
READ paths only: orders and exits keep the position's own account (never through this helper).
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BrokerCredentialRecord

NO_READER = "Live prices need your own broker session. Link your broker on the Broker page to read live prices here."


async def credential_for_reader(session: AsyncSession, tenant_id: int, user_id: Optional[int], broker: Optional[str] = None,
                                account_label: str = "primary") -> BrokerCredentialRecord:
    """The reader's own usable credential for a market-data read (404 / 409 with the fix location otherwise)."""
    from app.market_data.candles_routes import _pick_record
    if user_id is None:
        raise HTTPException(status_code=409, detail=NO_READER)
    return await _pick_record(session, tenant_id, broker, account_label, user_id=int(user_id))


def reader_id(user: Any, tenant_id: Optional[int] = None) -> Optional[int]:
    """`user.id` when `user` is a member of `tenant_id` (or of his own tenant when none is named), else None."""
    if user is None or getattr(user, "id", None) is None:
        return None
    if tenant_id is not None and getattr(user, "tenant_id", None) != tenant_id:
        return None
    return int(user.id)


__all__ = ["NO_READER", "credential_for_reader", "reader_id"]
