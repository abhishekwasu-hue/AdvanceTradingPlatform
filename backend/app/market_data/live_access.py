"""BT1b: one door for every live market-data read outside `candles_routes` while `byob_data_tier` is on.

BT1 put the entitlement guard on the chart / candles / option-chain endpoints. Other features read the broker too - the
screener run and its alert rules, the Copilot's evidence and tools, the strategist, portfolio exposure, option Greeks,
the deployment preview, the PAPER exit price on an approved proposal. With the flag off they keep their organisation-
level behaviour exactly as before. With it on they come through here:

* `user_source(session, user, symbols, ...)` - the signed-in user's **own** session at a broker whose data terms are
  confirmed, for the symbols inside that session's budget (`entitlement.resolve_many`). Everything it fetches is cached
  under the session's namespace (`MarketDataService(cache_namespace=...)`, `TickCache(namespace=...)`), never in the
  platform-wide keys. No such session -> `LiveDataRefused` (409, `X-Data-Tier: C`, the reason).
* A read with no user behind it (an organisation-level job) gets no live data at all while the flag is on.

Organisation-wide stores derived from live data (market memory, sentiment, the OI banner collector, the option-chain
recorder, the org quote stream) are not fed from per-user sessions while the flag is on; see the worker.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Sequence, Tuple

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BrokerCredentialRecord, User
from app.market_data import entitlement
from app.market_data.entitlement import DataTier, Entitlement
from app.market_data.fanin import FLAG, Priority, SessionRef
from app.market_data.freshness import timeframe_seconds

NO_USER = ("Live data needs the signed-in user's own broker session (bring your own broker): an organisation-level job "
           "gets no live broker data while the data tier is on.")


class LiveDataRefused(HTTPException):
    """Tier C: this read may not use live broker data for this user. 409, so callers that handled "no broker session"
    before handle it the same way."""

    def __init__(self, reason: str) -> None:
        super().__init__(status_code=409, detail=reason, headers={"X-Data-Tier": DataTier.EOD.value})


async def byob_on(session: AsyncSession, tenant_id: Optional[int]) -> bool:
    from app.platform.controls import flag_enabled
    return await flag_enabled(session, FLAG, tenant_id)


def _build_adapter(record: BrokerCredentialRecord) -> Any:
    from app.brokers.token_lifecycle import build_adapter
    return build_adapter(record)


@dataclass
class UserSource:
    """The user's own live session for one read: which credential, which cache namespace, which symbols it covers."""
    record: BrokerCredentialRecord
    ref: SessionRef
    entitlements: Dict[Tuple[str, str], Entitlement] = field(default_factory=dict)

    @property
    def namespace(self) -> str:
        return self.ref.namespace

    @property
    def source(self) -> str:
        return f"broker:{self.record.broker_name}"

    def allows(self, symbol: str, exchange: str) -> bool:
        ent = self.entitlements.get((symbol.strip().upper(), exchange.strip().upper()))
        return ent is not None and ent.live

    def reason(self, symbol: str, exchange: str) -> str:
        ent = self.entitlements.get((symbol.strip().upper(), exchange.strip().upper()))
        return (ent.reason if ent is not None else None) or "Only end-of-day prices for this symbol."

    def adapter(self) -> Any:
        return _build_adapter(self.record)

    def market_data(self, adapter: Any = None, **kw: Any) -> Any:
        from app.market_data.service import MarketDataService
        return MarketDataService(adapter if adapter is not None else self.adapter(), cache_namespace=self.namespace, **kw)

    def badge(self, as_of: Optional[datetime] = None, now: Optional[datetime] = None, *, timeframe: Optional[str] = None,
              last_bar_start: Optional[datetime] = None) -> dict:
        """The Tier A badge for what was read, timed by the data itself: a tick / quote time (`as_of`) or the newest bar
        (`last_bar_start` + `timeframe`). Unknown time -> stale, alerts suppressed."""
        live = next((e for e in self.entitlements.values() if e.live), None)
        ent = live or Entitlement(tier=DataTier.LIVE_OWN_BROKER, session=self.ref, record=self.record)
        return ent.badge(as_of=as_of, now=now, timeframe=timeframe, last_bar_start=last_bar_start)


async def user_source(session: AsyncSession, user: Optional[User], symbols: Sequence[Tuple[str, str]], *,
                      broker: Optional[str] = None, account_label: Optional[str] = None,
                      priority: Priority = Priority.CHART, register: bool = True) -> UserSource:
    """The user's own confirmed session for these symbols, or `LiveDataRefused` with the reason (none of them live)."""
    if user is None:
        raise LiveDataRefused(NO_USER)
    wanted = list(dict.fromkeys((s.strip().upper(), e.strip().upper()) for s, e in symbols if s and s.strip()))
    if not wanted:
        raise LiveDataRefused("No symbol to read.")
    ents = await entitlement.resolve_many(session, user, wanted, broker=broker, account_label=account_label, priority=priority, register=register)
    live = next((e for e in ents.values() if e.live and e.record is not None and e.session is not None), None)
    if live is None or live.record is None or live.session is None:
        first = next(iter(ents.values()), None)
        raise LiveDataRefused((first.reason if first is not None else None) or "Only end-of-day prices.")
    return UserSource(record=live.record, ref=live.session, entitlements=ents)


async def own_session_record(session: AsyncSession, user: Optional[User], now: Optional[datetime] = None) -> Optional[BrokerCredentialRecord]:
    """The user's own credential that can serve live data right now (confirmed broker, usable token), or None."""
    if user is None:
        return None
    from datetime import timezone
    return entitlement.pick_session(await entitlement.own_records(session, user), now or datetime.now(timezone.utc))


async def load_user(session: AsyncSession, user_id: Optional[int]) -> Optional[User]:
    return await session.get(User, user_id) if user_id else None


def bar_end(last_bar_start: Optional[datetime], timeframe: str) -> Optional[datetime]:
    """When the newest bar's data ends (its start + one timeframe) - the time a badge reports for bars."""
    if last_bar_start is None:
        return None
    return last_bar_start + timedelta(seconds=timeframe_seconds(timeframe))
