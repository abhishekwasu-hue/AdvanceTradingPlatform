"""BT1: the entitlement guard - which data tier a user may see for a symbol (beta spec §2, Phase 0 §4a).

Every live-data read on the user-facing paths (chart last price, candles, option chains) goes through `resolve` when the
`byob_data_tier` flag is on:

* Tier A ("Live - your <broker>") only when the user has their own broker session (`SessionRef`: a credential row the
  user linked themselves, `user_id` = the user) at a broker whose data terms are "confirmed" (app/market_data/
  broker_data_terms.json = docs/BROKER_DATA_TERMS.md), with a usable token today, and the symbol fits inside that
  session's symbol budget (positions > open charts > watchlist > screener universe; `fanin.session_cap`).
* Otherwise Tier C ("EOD - <date>"): reference data only. No broker linked, an execution-only broker (Zerodha), a broker
  still pending written confirmation, an expired token, or a symbol outside the budget all land here, with the reason.

Tenancy: broker credentials are stored per tenant (the organisation) with `user_id` for attribution. A broker
entitlement is personal - it is the account holder's - so Tier A is granted on the credential the *user* linked, never
on a teammate's credential in the same tenant. In the beta one registration = one tenant = one user, so the two
coincide; in a team tenant they do not, and the user rule is the one enforced.

There is no delayed-data tier in Phase 0 (no Tier B). Nothing here places or changes an order.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.token_lifecycle import token_is_usable
from app.db.models import BrokerCredentialRecord, IndexEodRecord, User
from app.market_data.calendar import IST, is_trading_day
from app.market_data.data_terms import DataTermsStatus, terms_for
from app.market_data.fanin import FLAG, Priority, SessionRef, SessionSymbols, session_cap, session_symbols
from app.market_data.freshness import candle_staleness, quote_is_stale

__all__ = ["FLAG", "DataTier", "Entitlement", "resolve", "resolve_many", "own_records", "eod_as_of", "eod_staleness", "eod_badge",
           "session_summaries", "pick_session"]


class DataTier(str, Enum):
    LIVE_OWN_BROKER = "A"   # the user's own broker session
    EOD = "C"               # end-of-day / reference data (bhavcopy etc.)


@dataclass(frozen=True)
class Entitlement:
    tier: DataTier
    symbol: Optional[str] = None
    exchange: Optional[str] = None
    segment: Optional[str] = None
    reason: Optional[str] = None
    session: Optional[SessionRef] = None
    record: Optional[BrokerCredentialRecord] = field(default=None, compare=False, repr=False)
    cap: Optional[int] = None
    broker_cap: Optional[int] = None
    poll_seconds: Optional[int] = None
    degraded: bool = False

    @property
    def live(self) -> bool:
        return self.tier is DataTier.LIVE_OWN_BROKER

    @property
    def source(self) -> str:
        return f"broker:{self.session.broker}" if self.live and self.session else "eod"

    def badge(self, *, as_of: Optional[datetime] = None, eod_date: Optional[date] = None, now: Optional[datetime] = None,
              holidays: Iterable[date] = (), timeframe: Optional[str] = None, last_bar_start: Optional[datetime] = None) -> dict:
        """What the page shows next to the number: tier, label, timestamp, staleness. Stale data suppresses alerts
        (the Phase G1 / U3 rule: no decision and no alert on data older than the staleness limit).

        BT1b: the time is the data's own - the tick / quote time (`as_of`), or for bars the newest bar
        (`last_bar_start` + `timeframe`, judged by the G1 candle rule). Unknown time = stale (fails closed)."""
        now = now or datetime.now(timezone.utc)
        if self.live and self.session is not None:
            name = terms_for(self.session.broker).display_name
            stale_reason = _live_staleness(as_of, now, timeframe, last_bar_start)
            return {"tier": self.tier.value, "label": f"Live · your {name}", "source": self.source, "broker": self.session.broker,
                    "display_name": name, "as_of": as_of.isoformat() if as_of else None, "eod_date": None,
                    "stale": stale_reason is not None, "stale_reason": stale_reason, "alerts_suppressed": stale_reason is not None,
                    "reason": None, "cap": self.cap, "broker_cap": self.broker_cap, "poll_seconds": self.poll_seconds, "degraded": self.degraded,
                    "symbol": self.symbol, "exchange": self.exchange}
        out = eod_badge(eod_date, now, holidays)
        out.update({"reason": self.reason, "cap": self.cap, "broker_cap": self.broker_cap, "poll_seconds": self.poll_seconds,
                    "degraded": self.degraded, "symbol": self.symbol, "exchange": self.exchange})
        return out


def _live_staleness(as_of: Optional[datetime], now: datetime, timeframe: Optional[str], last_bar_start: Optional[datetime]) -> Optional[str]:
    if timeframe and last_bar_start is not None:
        return candle_staleness(last_bar_start, timeframe, now)
    if as_of is not None:
        return quote_is_stale(as_of, now)
    return "no timestamp from the broker for this data: its age is unknown"


# --- EOD (Tier C) date and staleness ------------------------------------------------------------------------------------

async def eod_as_of(session: AsyncSession) -> Optional[date]:
    """The newest exchange EOD file ingested (all-indices closes, U1-c) - the date market-wide EOD pages are as of."""
    return await session.scalar(select(func.max(IndexEodRecord.trade_date)))


def _last_closed_session(now: datetime, holidays: Iterable[date]) -> date:
    """The latest trading day strictly before today (IST): today's EOD files are not out until the evening."""
    hol = set(holidays)
    day = now.astimezone(IST).date() - timedelta(days=1)
    for _ in range(15):
        if is_trading_day(day, hol):
            return day
        day -= timedelta(days=1)
    return day


def eod_staleness(eod_date: Optional[date], now: datetime, holidays: Iterable[date] = ()) -> Optional[str]:
    """None when the EOD data covers the last closed session; otherwise why it is stale."""
    if eod_date is None:
        return "no EOD data ingested yet"
    expected = _last_closed_session(now, holidays)
    if eod_date < expected:
        return f"EOD data is from {eod_date.isoformat()}; the last closed session was {expected.isoformat()}"
    return None


def eod_badge(eod_date: Optional[date], now: Optional[datetime] = None, holidays: Iterable[date] = ()) -> dict:
    now = now or datetime.now(timezone.utc)
    stale_reason = eod_staleness(eod_date, now, holidays)
    return {"tier": DataTier.EOD.value, "label": f"EOD · {eod_date.isoformat()}" if eod_date else "EOD", "source": "eod", "broker": None,
            "display_name": None, "as_of": None, "eod_date": eod_date.isoformat() if eod_date else None,
            "stale": stale_reason is not None, "stale_reason": stale_reason, "alerts_suppressed": stale_reason is not None}


# --- the guard ----------------------------------------------------------------------------------------------------------

async def own_records(session: AsyncSession, user: User, broker: Optional[str] = None,
                      account_label: Optional[str] = None) -> List[BrokerCredentialRecord]:
    """The broker credentials this user linked themselves (inside their own tenant) - never a teammate's."""
    query = select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == user.tenant_id, BrokerCredentialRecord.user_id == user.id)
    if broker:
        query = query.where(BrokerCredentialRecord.broker_name == broker.lower())
        if account_label:
            query = query.where(BrokerCredentialRecord.account_label == account_label)
    return list(await session.scalars(query.order_by(BrokerCredentialRecord.broker_name, BrokerCredentialRecord.account_label, BrokerCredentialRecord.id)))


async def _teammates_linked(session: AsyncSession, user: User) -> bool:
    count = await session.scalar(select(func.count()).select_from(BrokerCredentialRecord).where(
        BrokerCredentialRecord.tenant_id == user.tenant_id, BrokerCredentialRecord.user_id != user.id))
    return bool(count)


def _no_session_reason(records: Sequence[BrokerCredentialRecord], now: datetime, broker: Optional[str], teammates: bool) -> str:
    if not records:
        base = (f"No {terms_for(broker).display_name} account linked by you" if broker else "No broker linked to your login") + \
               ": EOD data only. Link Upstox or Dhan on the Broker page for live prices."
        if teammates:
            base += " A broker linked by a teammate is theirs: its live data is shown only to that person."
        return base
    reasons: List[str] = []
    for record in records:
        terms = terms_for(record.broker_name)
        name = terms.display_name
        if terms.status is DataTermsStatus.EXECUTION_ONLY:
            reasons.append(f"{name} is execution-only in ATP: its API terms do not allow showing its market data on other "
                           f"platforms. EOD data here; live prices in your {name} app.")
        elif terms.status is DataTermsStatus.PENDING:
            reasons.append(f"Live {name} data in ATP waits for the broker's written confirmation. EOD data until then.")
        elif not token_is_usable(record, now):
            reasons.append(f"Your {name} session has no valid token today. Log in to {name} on the Broker page for live data.")
    # Prefer the actionable reason (log in) over the terms ones.
    reasons.sort(key=lambda r: 0 if "Log in" in r else 1)
    return reasons[0] if reasons else "No live session available: EOD data only."


def pick_session(records: Sequence[BrokerCredentialRecord], now: datetime) -> Optional[BrokerCredentialRecord]:
    for record in records:
        if terms_for(record.broker_name).tier_a_allowed and token_is_usable(record, now):
            return record
    return None


def _segment(exchange: str, segment: Optional[str]) -> str:
    if segment:
        return segment.upper()
    return {"NFO": "NSE_FO", "BFO": "BSE_FO", "NSE": "NSE_EQ", "BSE": "BSE_EQ", "MCX": "MCX_FO", "CDS": "NSE_CD"}.get(exchange.upper(), exchange.upper())


async def resolve_many(session: AsyncSession, user: User, symbols: Sequence[Tuple[str, str]], *, segment: Optional[str] = None,
                       broker: Optional[str] = None, account_label: Optional[str] = None, priority: Priority = Priority.CHART,
                       now: Optional[datetime] = None, register: bool = True,
                       registry: Optional[SessionSymbols] = None) -> Dict[Tuple[str, str], Entitlement]:
    """The tier for each (symbol, exchange). One session is chosen for all of them (the first of the user's own
    credentials at a confirmed broker with a usable token, or the one named); each symbol must then fit its budget.
    `register=False` answers without taking a budget slot (a badge lookup)."""
    now = now or datetime.now(timezone.utc)
    registry = registry or session_symbols
    records = await own_records(session, user, broker, account_label)
    record = pick_session(records, now)
    out: Dict[Tuple[str, str], Entitlement] = {}
    if record is None:
        reason = _no_session_reason(records, now, broker, not records and await _teammates_linked(session, user))
        for symbol, exchange in symbols:
            key = (symbol.strip().upper(), exchange.strip().upper())
            out[key] = Entitlement(tier=DataTier.EOD, symbol=key[0], exchange=key[1], segment=_segment(key[1], segment), reason=reason)
        return out
    ref = SessionRef.of(record)
    terms = terms_for(record.broker_name)
    cap = session_cap(record.broker_name)
    keys = [(symbol.strip().upper(), exchange.strip().upper()) for symbol, exchange in symbols]
    # LD-7: the session's symbol set read and written once for all the symbols, not once per symbol (the same answers)
    for key, (inside, plan) in zip(keys, await registry.admit_many(ref.namespace, keys, priority, cap, now, register=register)):
        common: Dict[str, Any] = {"symbol": key[0], "exchange": key[1], "segment": _segment(key[1], segment), "cap": cap,
                                  "broker_cap": terms.symbol_cap, "poll_seconds": plan.poll_seconds, "degraded": plan.degraded}
        if inside:
            out[key] = Entitlement(tier=DataTier.LIVE_OWN_BROKER, session=ref, record=record, **common)
        else:
            out[key] = Entitlement(tier=DataTier.EOD, reason=(
                f"{key[0]} is outside your {terms.display_name} session budget ({cap} symbols; open positions and charts "
                "come first). EOD data for it until a slot frees up."), **common)
    return out


async def resolve(session: AsyncSession, user: User, symbol: str, exchange: str = "NSE", segment: Optional[str] = None, *,
                  broker: Optional[str] = None, account_label: Optional[str] = None, priority: Priority = Priority.CHART,
                  now: Optional[datetime] = None, register: bool = True, registry: Optional[SessionSymbols] = None) -> Entitlement:
    """`entitlement.resolve(user, symbol, segment) -> tier + source` for one symbol (see `resolve_many`)."""
    result = await resolve_many(session, user, [(symbol, exchange)], segment=segment, broker=broker, account_label=account_label,
                                priority=priority, now=now, register=register, registry=registry)
    return next(iter(result.values()))


async def session_summaries(session: AsyncSession, user: User, now: Optional[datetime] = None,
                            registry: Optional[SessionSymbols] = None) -> List[dict]:
    """The user's own broker sessions with their data status - what the badge popover explains."""
    now = now or datetime.now(timezone.utc)
    registry = registry or session_symbols
    out: List[dict] = []
    for record in await own_records(session, user):
        terms = terms_for(record.broker_name)
        usable = token_is_usable(record, now)
        item: Dict[str, Any] = {"broker": record.broker_name, "display_name": terms.display_name, "account_label": record.account_label,
                                "data_status": terms.status.value, "token_usable": usable, "tier_a": terms.tier_a_allowed and usable,
                                "broker_cap": terms.symbol_cap, "requires_user_data_subscription": terms.requires_user_data_subscription}
        if item["tier_a"]:
            ref = SessionRef.of(record)
            item.update((await registry.plan(ref.namespace, session_cap(record.broker_name), now)).as_dict())
        out.append(item)
    return out
