"""BT1: per-user broker-session fan-in for the bring-your-own-broker data tier (beta spec §2 Tier A, §4a).

One user's live data comes through that user's own broker session and is shown to that user only. This module holds
what such a session carries:

* `SessionRef` - which session: the broker credential row the user linked themselves (`user_id` on the row), inside
  their tenant. Its `namespace` keys every cache entry the session writes (ticks, candles, its symbol set), so nothing
  fetched under one user's entitlement is readable through another user's keys.
* `SessionSymbols` - the session's symbol set: demands with a priority (open positions > open charts > watchlist >
  screener universe) and when they were first / last wanted. The API process registers what the user opens; the worker
  registers the user's open positions. In-process, mirrored to Redis so both processes plan from the same set (Redis is
  optional: without it each process plans from what it saw itself).
* `plan_symbols` - the set trimmed to the session's cap by priority, then by how long a symbol has been held (the latest
  scan for the screener universe), with
  graceful degradation: fewer symbols (the rest are served as EOD) and a slower refresh hint when the set is large.
* `UserSessionFanIn` - the worker side: after the user's daily broker login (a usable token at a confirmed broker) one
  live session per user credential, a websocket stream into the session's own tick cache when streaming is enabled
  (`STREAMING_QUOTES_ENABLED`; otherwise REST through the same session), torn down at day end (market closed, token
  no longer usable, or a new IST day). Reuses the Phase S `TickStream`; a per-user stream never feeds the
  platform-wide tick listeners (the lake recorder), so per-user data is never pooled.

Nothing here runs unless the `byob_data_tier` flag is on. Nothing here places or changes an order.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import IntEnum
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from app.cache.client import cache_get, cache_set
from app.core import config
from app.market_data.calendar import IST
from app.market_data.data_terms import confirmed_brokers, terms_for
from app.market_data.stream import TickCache, TickStream, stream_for

logger = logging.getLogger(__name__)

FLAG = "byob_data_tier"

REGISTRY_TTL_SECONDS = 3600
# Refresh hint (seconds between polls of the user's charts / quotes): normal, busy (over half the cap), over the cap.
POLL_SECONDS = (5, 10, 15)

SymbolKey = Tuple[str, str]   # (symbol, exchange)


class Priority(IntEnum):
    """Lower wins a slot first."""
    POSITION = 1
    CHART = 2
    WATCHLIST = 3
    SCREENER = 4


# Demands the UI re-asserts while the page is open (a chart polls its last price every few seconds); they lapse when
# the page is closed. Positions and the screener universe are replaced wholesale by the worker instead.
IDLE_SECONDS = {int(Priority.CHART): 600, int(Priority.WATCHLIST): 1800, int(Priority.SCREENER): 900}


@dataclass(frozen=True)
class SessionRef:
    """A user's own broker session: the credential row they linked (tenant-scoped, attributed to them)."""
    user_id: int
    tenant_id: int
    credential_id: int
    broker: str
    account_label: str

    @property
    def namespace(self) -> str:
        return f"u{self.user_id}.c{self.credential_id}"

    @classmethod
    def of(cls, record: Any) -> "SessionRef":
        return cls(user_id=int(record.user_id), tenant_id=int(record.tenant_id), credential_id=int(record.id),
                   broker=str(record.broker_name), account_label=str(record.account_label or "primary"))


def session_cap(broker: str) -> int:
    """The ATP per-session budget, lowered to the broker's per-connection cap when one is recorded (never raised)."""
    cap = config.BYOB_SESSION_SYMBOL_CAP
    broker_cap = terms_for(broker).symbol_cap
    return min(cap, broker_cap) if broker_cap else cap


def _key(symbol: str, exchange: str) -> str:
    return f"{(exchange or 'NSE').strip().upper()}:{(symbol or '').strip().upper()}"


def _split(key: str) -> SymbolKey:
    exchange, _, symbol = key.partition(":")
    return symbol, exchange


@dataclass(frozen=True)
class Plan:
    cap: int
    kept: Tuple[SymbolKey, ...]
    dropped: Tuple[SymbolKey, ...]

    @property
    def demand(self) -> int:
        return len(self.kept) + len(self.dropped)

    @property
    def degraded(self) -> bool:
        return bool(self.dropped)

    @property
    def poll_seconds(self) -> int:
        if self.dropped:
            return POLL_SECONDS[2]
        return POLL_SECONDS[0] if len(self.kept) * 2 <= self.cap else POLL_SECONDS[1]

    def keeps(self, symbol: str, exchange: str) -> bool:
        return (symbol.strip().upper(), exchange.strip().upper()) in self.kept

    def as_dict(self) -> dict:
        return {"cap": self.cap, "symbols": len(self.kept), "dropped": len(self.dropped), "degraded": self.degraded,
                "poll_seconds": self.poll_seconds}


Entries = Dict[str, List[float]]   # "EXCH:SYMBOL" -> [priority, last_seen, first_seen] (epoch seconds)


def _rank(item: Tuple[str, List[float]]) -> Tuple[int, float, str]:
    key, (priority, last_seen, first_seen) = item[0], item[1]
    if int(priority) == int(Priority.SCREENER):
        return int(priority), -float(last_seen), key   # a new scan replaces the previous scan's symbols
    return int(priority), float(first_seen), key       # a held position / chart / watchlist symbol keeps its slot


def plan_symbols(entries: Entries, cap: int) -> Plan:
    """Keep the `cap` most important symbols: lowest priority number first. Within positions, charts and the watchlist
    the longest held wins - a symbol already in the session keeps its slot (no flapping between two charts) and a new
    one waits until a slot lapses or frees up; within the screener universe the most recent scan wins."""
    ordered = sorted(entries.items(), key=_rank)
    kept = tuple(_split(k) for k, _ in ordered[:max(0, cap)])
    dropped = tuple(_split(k) for k, _ in ordered[max(0, cap):])
    return Plan(cap=cap, kept=kept, dropped=dropped)


def _prune(entries: Entries, now: datetime) -> Entries:
    ts = now.timestamp()
    out: Entries = {}
    for key, value in entries.items():
        priority, seen, first = int(value[0]), float(value[1]), float(value[2])
        idle = IDLE_SECONDS.get(priority)
        if idle is not None and ts - seen > idle:
            continue
        out[key] = [priority, seen, first]
    return out


def _upsert(entries: Entries, key: str, priority: int, now: datetime) -> None:
    ts = now.timestamp()
    current = entries.get(key)
    if current is None:
        entries[key] = [priority, ts, ts]
    else:
        entries[key] = [min(priority, int(current[0])), ts, float(current[2])]


class SessionSymbols:
    """Each session's symbol set, keyed by `SessionRef.namespace`. Process-local plus a Redis mirror."""

    def __init__(self) -> None:
        self._local: Dict[str, Entries] = {}

    @staticmethod
    def _redis_key(namespace: str) -> str:
        return f"md:byob:{namespace}:symbols"

    async def load(self, namespace: str) -> Entries:
        raw = await cache_get(self._redis_key(namespace))
        if raw:
            try:
                data = json.loads(raw)
                entries = {str(k): [int(v[0]), float(v[1]), float(v[2] if len(v) > 2 else v[1])] for k, v in data.items()}
                self._local[namespace] = entries
                return dict(entries)
            except (ValueError, TypeError, IndexError, AttributeError):
                logger.warning("Discarding an unreadable symbol set for session %s", namespace)
        return dict(self._local.get(namespace, {}))

    async def _save(self, namespace: str, entries: Entries) -> None:
        self._local[namespace] = entries
        await cache_set(self._redis_key(namespace), json.dumps(entries), REGISTRY_TTL_SECONDS)

    async def admit(self, namespace: str, symbol: str, exchange: str, priority: Priority, cap: int, now: datetime, *,
                    register: bool = True) -> Tuple[bool, Plan]:
        """Is the symbol inside the session's budget? With `register` the demand is recorded (and refreshed); without,
        the answer is what it would be if it were (a badge lookup must not take a slot)."""
        entries = _prune(await self.load(namespace), now)
        _upsert(entries, _key(symbol, exchange), int(priority), now)
        plan = plan_symbols(entries, cap)
        if register:
            await self._save(namespace, entries)
        return plan.keeps(symbol, exchange), plan

    async def replace(self, namespace: str, priority: Priority, symbols: Iterable[SymbolKey], now: datetime) -> None:
        """Make the session's demands at `priority` exactly `symbols` (the worker's positions / screener universe)."""
        entries = _prune(await self.load(namespace), now)
        previous = {k: v for k, v in entries.items() if int(v[0]) == int(priority)}
        entries = {k: v for k, v in entries.items() if int(v[0]) != int(priority)}
        for symbol, exchange in symbols:
            key = _key(symbol, exchange)
            if key in previous and key not in entries:
                entries[key] = [int(priority), now.timestamp(), float(previous[key][2])]   # keeps its place in the queue
            else:
                _upsert(entries, key, int(priority), now)
        await self._save(namespace, entries)

    async def plan(self, namespace: str, cap: int, now: datetime) -> Plan:
        return plan_symbols(_prune(await self.load(namespace), now), cap)

    async def clear(self, namespace: str) -> None:
        self._local.pop(namespace, None)
        await cache_set(self._redis_key(namespace), "{}", 1)


session_symbols = SessionSymbols()


# --- the worker side: one live session per user credential --------------------------------------------------------------

@dataclass
class LiveSession:
    ref: SessionRef
    cache: TickCache
    opened_day: date
    stream: Optional[TickStream] = None
    task: Optional["asyncio.Task[None]"] = None
    plan: Optional[Plan] = None
    last_error: Optional[str] = None

    def status(self) -> dict:
        out: Dict[str, Any] = {"user_id": self.ref.user_id, "broker": self.ref.broker, "account_label": self.ref.account_label,
                               "opened_day": self.opened_day.isoformat(), "streaming": self.stream is not None, "last_error": self.last_error}
        if self.plan is not None:
            out.update(self.plan.as_dict())
        if self.stream is not None:
            out["connected"] = self.stream.connected
            out["ticks"] = self.stream.ticks
        return out


@dataclass
class FanInReport:
    opened: int = 0
    closed: int = 0
    active: int = 0
    errors: List[str] = field(default_factory=list)


async def eligible_sessions(db: Any, now: datetime) -> Dict[str, Any]:
    """namespace -> credential row for every user session the worker keeps open while NSE is open: a credential at a
    broker whose data terms are confirmed, with a usable token, in a tenant the `byob_data_tier` flag is on for. The
    worker's `sync` opens exactly these; the public status page (Z10-e) counts them."""
    from sqlalchemy import select

    from app.brokers.token_lifecycle import token_is_usable
    from app.db.models import BrokerCredentialRecord
    from app.platform.controls import feature_flags

    flag = (await feature_flags(db)).get(FLAG) or {}
    if not (flag.get("on") or flag.get("tenants")):
        return {}
    allowed_tenants = set(flag.get("tenants") or [])
    rows = await db.scalars(select(BrokerCredentialRecord).where(BrokerCredentialRecord.broker_name.in_(sorted(confirmed_brokers()))))
    wanted: Dict[str, Any] = {}
    for record in rows:
        if not (flag.get("on") or record.tenant_id in allowed_tenants):
            continue
        if token_is_usable(record, now):
            wanted[SessionRef.of(record).namespace] = record
    return wanted


class UserSessionFanIn:
    """The worker's per-user sessions (bring your own broker). `sync` is called once per worker cycle."""

    def __init__(self, *, symbols: Optional[SessionSymbols] = None, stream_factory: Callable[[Any], Optional[TickStream]] = stream_for,
                 adapter_factory: Optional[Callable[[Any], Any]] = None, streaming: Optional[bool] = None) -> None:
        self.symbols = symbols or session_symbols
        self.stream_factory = stream_factory
        self.adapter_factory = adapter_factory
        self.streaming = config.STREAMING_QUOTES_ENABLED if streaming is None else streaming
        self._sessions: Dict[str, LiveSession] = {}

    @property
    def sessions(self) -> Dict[str, LiveSession]:
        return dict(self._sessions)

    def _adapter(self, record: Any) -> Any:
        if self.adapter_factory is not None:
            return self.adapter_factory(record)
        from app.brokers.token_lifecycle import build_adapter
        return build_adapter(record)

    async def open(self, record: Any, now: datetime) -> LiveSession:
        """A live session for this user credential (after their daily login). Replaces one already open."""
        ref = SessionRef.of(record)
        await self.close(ref.namespace)
        live = LiveSession(ref=ref, cache=TickCache(namespace=ref.namespace), opened_day=now.astimezone(IST).date())
        if self.streaming:
            stream = self.stream_factory(self._adapter(record))
            if stream is not None:
                stream.cache = live.cache            # this session's own keys, never the shared worker cache
                stream.notify_listeners = False      # never pooled into the lake or any platform-wide consumer
                live.stream = stream
                live.task = asyncio.create_task(stream.run(), name=f"byob-stream:{ref.namespace}")
        self._sessions[ref.namespace] = live
        logger.info("BYOB session opened for user %s (%s/%s, streaming=%s)", ref.user_id, ref.broker, ref.account_label, live.stream is not None)
        return live

    async def close(self, namespace: str) -> bool:
        live = self._sessions.pop(namespace, None)
        if live is None:
            return False
        if live.stream is not None:
            live.stream.stop()
        if live.task is not None:
            live.task.cancel()
            try:
                await live.task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - a stream's own error is not the teardown's
                pass
        live.cache.clear()
        await self.symbols.clear(namespace)
        return True

    async def close_all(self) -> int:
        closed = 0
        for namespace in list(self._sessions):
            closed += int(await self.close(namespace))
        return closed

    async def refresh(self, namespace: str, now: datetime) -> Optional[Plan]:
        """Re-plan the session's symbol set and point its stream at exactly the kept symbols."""
        live = self._sessions.get(namespace)
        if live is None:
            return None
        live.plan = await self.symbols.plan(namespace, session_cap(live.ref.broker), now)
        if live.stream is not None:
            await live.stream.set_symbols(live.plan.kept)
        return live.plan

    async def sync(self, db: Any, now: datetime, *, market_open: bool) -> FanInReport:
        """Open a session for every user credential at a confirmed broker with a usable token (tenants with the flag
        on), close the rest; then refresh each session's positions and plan. Market closed = day end: all closed."""
        from sqlalchemy import select

        from app.db.models import TradeRecord
        from app.trading.position_monitor import trade_venues, underlying_exchange

        report = FanInReport()
        wanted = await eligible_sessions(db, now) if market_open else {}
        if not wanted:
            report.closed = await self.close_all()
            return report
        today = now.astimezone(IST).date()
        for namespace, live in list(self._sessions.items()):
            if namespace not in wanted or live.opened_day != today:
                report.closed += int(await self.close(namespace))
        for namespace, record in wanted.items():
            try:
                if namespace not in self._sessions:
                    await self.open(record, now)
                    report.opened += 1
                trades = await db.scalars(select(TradeRecord).where(
                    TradeRecord.tenant_id == record.tenant_id, TradeRecord.user_id == record.user_id, TradeRecord.exit_time.is_(None)))
                positions: set = set()
                open_rows = list(trades)
                venues = await trade_venues(db, open_rows)       # BK2-8: a legacy row follows its deployment's venue
                for trade in open_rows:
                    positions.add((trade.symbol, venues[trade.id]))
                    if trade.underlying_symbol:
                        positions.add((trade.underlying_symbol, underlying_exchange(trade.underlying_symbol)))
                await self.symbols.replace(namespace, Priority.POSITION, sorted(positions), now)
                await self.refresh(namespace, now)
            except Exception as exc:  # noqa: BLE001 - one user's session must never stop another's (or the worker)
                failed = self._sessions.get(namespace)
                if failed is not None:
                    failed.last_error = str(exc)[:200]
                report.errors.append(f"byob session {namespace}: {exc}")
                logger.warning("BYOB session %s failed: %s", namespace, exc)
        report.active = len(self._sessions)
        return report

    def status(self) -> Dict[str, dict]:
        return {ns: live.status() for ns, live in self._sessions.items()}
