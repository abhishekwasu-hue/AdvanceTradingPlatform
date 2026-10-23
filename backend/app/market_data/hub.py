"""TM Part 1: the data hub in the middle (docs/design/DATA_HUB.md; flag `data_hub`, OFF by default).

One broker connection = ONE live-data session and ONE market-data stream, owned here. Every consumer reads its prices
through the hub instead of asking the broker itself:

* **One stream per connection.** The worker keeps one `TickStream` per broker connection, keyed by the connection's
  namespace: `SessionRef.namespace` for a user's own broker session (bring your own broker, `byob_data_tier` on), or
  `org_namespace(tenant)` for the organisation's own session (`byob_data_tier` off). Its symbol set is the union of
  what the connection's consumers asked for - open positions and running strategies (the worker), open charts, the
  watchlist, the option chain page and the browser's live ticks (the API, `register_symbols`) - inside the session's
  cap (`fanin.session_cap`), positions first (`fanin.plan_symbols`).
* **Ticks stay with the connection.** A hub stream writes only into its own namespaced tick keys (never the platform-wide
  `md:tick:*` keys, never the lake recorder): a price fetched with one user's token is never read for another user or
  another organisation. Licensed shared EOD history (the stored candles) is a separate store and is not touched.
* **One status per connection.** `live`, `connecting`, `reconnecting` (backoff, then every symbol is subscribed again),
  `renew_needed` (the broker refused the token: the stream stops retrying and ONE renew prompt is raised; when the
  login is renewed the stream is opened again and resubscribed) or `polling` (no stream for this broker: REST through
  the per-connection budget). Published per connection, read by the API for the browser.
* **To the browser** as a `ticks` topic on the existing live stream (TM-12): only the symbols the page registered,
  at most one update per symbol per second, plus a `hub` event when the status changes.
* **Chain snapshots** are shared within one connection for a few seconds (`shared_chain`), so the chain page, a chart
  and a Copilot question asked in the same moment make one broker call, not three.

Nothing here places, changes or blocks an order; exits read prices exactly as before when the hub has no fresh tick.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from app.cache.client import cache_get, cache_set
from app.core import config
from app.market_data.fanin import Priority, SessionSymbols, session_cap, session_symbols
from app.market_data.stream import Tick, TickCache, TickStream, _tick_key, stream_for

logger = logging.getLogger(__name__)

FLAG = "data_hub"

STATUS_TTL_SECONDS = 180          # a status the worker stopped refreshing lapses (the API then derives it from the row)
SNAPSHOT_TTL_SECONDS = 30         # the per-connection tick snapshot the browser fan-out reads
SNAPSHOT_FLUSH_SECONDS = 1.0      # at most one snapshot write per connection per second
BROWSER_SYMBOL_CAP = 50           # symbols one browser tab may register for live ticks
BROWSER_REGISTRY_TTL_SECONDS = 600
BROWSER_MIN_INTERVAL_SECONDS = 1.0   # TM-12: at most one update per symbol per second
CHAIN_SNAPSHOT_SECONDS = config.HUB_CHAIN_SNAPSHOT_SECONDS

Pair = Tuple[str, str]   # (symbol, exchange)


class HubState(str, Enum):
    LIVE = "live"
    CONNECTING = "connecting"
    RECONNECTING = "reconnecting"
    RENEW_NEEDED = "renew_needed"
    POLLING = "polling"


def display_broker(name: str) -> str:
    from app.market_data.data_terms import terms_for
    try:
        return terms_for(name).display_name or name.title()
    except Exception:  # noqa: BLE001 - an unknown broker keeps its id, title-cased
        return (name or "broker").replace("_", " ").title()


def plain_message(state: str, broker: str) -> str:
    """What the trader reads. Plain words; no internal state names, ids or technical terms."""
    name = display_broker(broker)
    if state == HubState.RECONNECTING.value:
        return f"Live prices from {name} are reconnecting. Prices may be a few seconds old until then."
    if state == HubState.RENEW_NEEDED.value:
        return f"Your {name} login has ended. Log in again to resume live prices."
    if state == HubState.CONNECTING.value:
        return f"Connecting to live prices from {name}."
    if state == HubState.POLLING.value:
        return f"Prices from {name} refresh every few seconds."
    return f"Live prices from {name}."


@dataclass
class HubStatus:
    namespace: str
    broker: str
    state: str
    since: str
    reconnects: int = 0
    subscribed: int = 0
    retry_seconds: Optional[int] = None
    account_label: str = "primary"

    def to_json(self) -> str:
        return json.dumps(self.__dict__)

    @classmethod
    def from_json(cls, raw: str) -> Optional["HubStatus"]:
        try:
            data = json.loads(raw)
            return cls(namespace=str(data["namespace"]), broker=str(data["broker"]), state=str(data["state"]), since=str(data["since"]),
                       reconnects=int(data.get("reconnects") or 0), subscribed=int(data.get("subscribed") or 0),
                       retry_seconds=data.get("retry_seconds"), account_label=str(data.get("account_label") or "primary"))
        except (ValueError, KeyError, TypeError):
            return None

    def public(self) -> dict:
        """The browser's view: the state, the broker as the trader knows it and the sentence to show. No namespace."""
        return {"state": self.state, "broker": self.broker, "broker_name": display_broker(self.broker), "account_label": self.account_label,
                "message": plain_message(self.state, self.broker), "since": self.since, "retry_seconds": self.retry_seconds}


def org_namespace(tenant_id: int) -> str:
    """The organisation's own connection (byob off). The same keys BT1b gives the organisation's cache."""
    return f"org.t{int(tenant_id)}"


def _status_key(namespace: str) -> str:
    return f"md:hub:{namespace}:status"


def _snapshot_key(namespace: str) -> str:
    return f"md:hub:{namespace}:ticks"


_local_status: Dict[str, HubStatus] = {}
_local_snapshot: Dict[str, Dict[str, list]] = {}


async def publish_status(status: HubStatus) -> None:
    _local_status[status.namespace] = status
    await cache_set(_status_key(status.namespace), status.to_json(), STATUS_TTL_SECONDS)


async def read_status(namespace: str) -> Optional[HubStatus]:
    raw = await cache_get(_status_key(namespace))
    if raw:
        found = HubStatus.from_json(raw)
        if found is not None:
            return found
    return _local_status.get(namespace)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def reset() -> None:
    """Tests only: forget every process-local status, snapshot, browser registry and chain copy."""
    _local_status.clear()
    _local_snapshot.clear()
    _browser_local.clear()
    _snapshot_memo.clear()
    _chain_local.clear()
    _chain_locks.clear()
    _hub_caches.clear()


# --- the per-connection tick cache and its snapshot -------------------------------------------------------------------

class HubTickCache(TickCache):
    """A connection's own tick cache (its namespace). Ticks are kept in the process (the worker's decisions read them
    there) and published as ONE snapshot per connection at most once a second (`flush`, also scheduled after the last
    tick), instead of one Redis write per tick: 500 connections x 50 symbols is 500 writes a second, not 25,000. The
    API's reads, the browser fan-out and the Copilot read that snapshot (`HubSnapshotReader`)."""

    def __init__(self, namespace: str) -> None:
        super().__init__(namespace=namespace)
        self.latest: Dict[str, list] = {}
        self.dirty = False
        self._flushed_at = 0.0
        self._scheduled = False

    async def put(self, tick: Tick) -> None:
        self._local[_tick_key(tick.broker, tick.exchange, tick.symbol, self.namespace)] = tick
        self.received += 1
        self.latest[f"{tick.exchange.upper()}:{tick.symbol.upper()}"] = [float(tick.ltp), tick.best_ts.isoformat()]
        self.dirty = True
        if time.monotonic() - self._flushed_at >= SNAPSHOT_FLUSH_SECONDS:
            await self.flush()
        elif not self._scheduled:
            self._scheduled = True
            try:
                loop = asyncio.get_running_loop()
                loop.call_later(SNAPSHOT_FLUSH_SECONDS, lambda: loop.create_task(self._deferred_flush()))
            except RuntimeError:          # no loop (a synchronous caller): the next tick or refresh flushes
                self._scheduled = False

    async def _deferred_flush(self) -> None:
        self._scheduled = False
        await self.flush()

    async def flush(self) -> bool:
        if not self.dirty or self.namespace is None:
            return False
        self.dirty = False
        self._flushed_at = time.monotonic()
        snapshot = dict(self.latest)
        _local_snapshot[self.namespace] = snapshot
        await cache_set(_snapshot_key(self.namespace), json.dumps(snapshot, separators=(",", ":")), SNAPSHOT_TTL_SECONDS)
        return True

    def clear(self) -> None:
        super().clear()
        self.latest.clear()
        self.dirty = False


class HubSnapshotReader(TickCache):
    """A connection's ticks read from another process (the API): the per-symbol keys a stream outside the hub writes
    (as before), else the connection's hub snapshot (at most a second behind the stream)."""

    async def get(self, broker: str, exchange: str, symbol: str) -> Optional[Tick]:
        tick = await super().get(broker, exchange, symbol)
        if tick is not None or self.namespace is None:
            return tick
        found = (await read_snapshot(self.namespace)).get(f"{exchange.strip().upper()}:{symbol.strip().upper()}")
        if not found:
            return None
        try:
            ts = datetime.fromisoformat(str(found[1]))
            return Tick(broker=broker, exchange=exchange.strip().upper(), symbol=symbol.strip().upper(), ltp=float(found[0]),
                        exchange_ts=ts, received_at=ts)
        except (TypeError, ValueError):
            return None


_hub_caches: Dict[str, HubTickCache] = {}


def tick_cache_for(namespace: str) -> TickCache:
    """The connection's tick cache: the hub stream's own instance in the process that runs it (the worker), else a
    reader of the same keys through Redis (the API)."""
    return _hub_caches.get(namespace) or HubSnapshotReader(namespace=namespace)


_snapshot_memo: Dict[str, Tuple[float, Dict[str, list]]] = {}


async def read_snapshot(namespace: str, *, max_age: float = 0.5) -> Dict[str, list]:
    """{"EXCH:SYMBOL": [ltp, iso time]} for one connection. Every browser stream of the same connection in one API
    process shares one read per `max_age` (500 open tabs on one connection = 2 reads a second, not 1,000)."""
    memo = _snapshot_memo.get(namespace)
    now = time.monotonic()
    if memo is not None and now - memo[0] < max_age:
        return memo[1]
    raw = await cache_get(_snapshot_key(namespace))
    data: Dict[str, list] = {}
    if raw:
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                data = {str(k): list(v) for k, v in loaded.items() if isinstance(v, list) and len(v) == 2}
        except (ValueError, TypeError):
            data = {}
    if not data:
        data = dict(_local_snapshot.get(namespace, {}))
    _snapshot_memo[namespace] = (now, data)
    return data


# --- consumers register what they need ---------------------------------------------------------------------------------

async def register_symbols(namespace: str, broker: str, pairs: Sequence[Pair], priority: Priority = Priority.CHART, *,
                           now: Optional[datetime] = None, symbols: Optional[SessionSymbols] = None) -> List[Pair]:
    """Record that a consumer of this connection needs `pairs` live; returns the ones inside the session's cap (the hub
    streams those; the rest stay on the per-connection REST budget)."""
    registry = symbols or session_symbols
    now = now or datetime.now(timezone.utc)
    wanted = list(dict.fromkeys((s.strip().upper(), e.strip().upper()) for s, e in pairs if s and s.strip()))
    if not wanted:
        return []
    answers = await registry.admit_many(namespace, wanted, priority, session_cap(broker), now, register=True)
    return [pair for pair, (kept, _) in zip(wanted, answers) if kept]


_browser_local: Dict[str, Tuple[float, List[Pair]]] = {}


def _browser_key(user_id: int, sid: str) -> str:
    return f"live:ticks:u{int(user_id)}:{sid}"


async def set_browser_symbols(user_id: int, sid: str, pairs: Sequence[Pair]) -> None:
    clean = list(dict.fromkeys((s.strip().upper(), e.strip().upper()) for s, e in pairs))[:BROWSER_SYMBOL_CAP]
    _browser_local[_browser_key(user_id, sid)] = (time.monotonic(), clean)
    await cache_set(_browser_key(user_id, sid), json.dumps(clean), BROWSER_REGISTRY_TTL_SECONDS)


async def browser_symbols(user_id: int, sid: str) -> List[Pair]:
    raw = await cache_get(_browser_key(user_id, sid))
    if raw:
        try:
            return [(str(s), str(e)) for s, e in json.loads(raw)][:BROWSER_SYMBOL_CAP]
        except (ValueError, TypeError):
            pass
    found = _browser_local.get(_browser_key(user_id, sid))
    if found is None or time.monotonic() - found[0] > BROWSER_REGISTRY_TTL_SECONDS:
        return []
    return list(found[1])


# --- chain snapshots shared within one connection ----------------------------------------------------------------------

_chain_local: Dict[str, Tuple[float, str]] = {}
_chain_locks: Dict[str, asyncio.Lock] = {}


async def shared_chain(credential_id: int, underlying: str, expiry: Any, fetch: Callable[[], Awaitable[Any]]) -> Any:
    """One option-chain fetch per connection, underlying and expiry every CHAIN_SNAPSHOT_SECONDS, whoever asks (the
    chain page, a chart, a Copilot tool). Kept per credential row: one user's chain is never read under another's."""
    from app.brokers.models import OptionChain
    if CHAIN_SNAPSHOT_SECONDS <= 0:
        return await fetch()
    key = f"md:hub:c{int(credential_id)}:chain:{underlying.upper()}:{expiry or 'near'}"

    async def cached() -> Optional[Any]:
        raw = await cache_get(key)
        if not raw:
            local = _chain_local.get(key)
            if local is not None and time.monotonic() - local[0] < CHAIN_SNAPSHOT_SECONDS:
                raw = local[1]
        if raw:
            try:
                return OptionChain.model_validate_json(raw)
            except ValueError:
                return None
        return None

    hit = await cached()
    if hit is not None:
        return hit
    lock = _chain_locks.setdefault(key, asyncio.Lock())
    async with lock:                      # one fetch at a time in this process; the others read its copy
        hit = await cached()
        if hit is not None:
            return hit
        chain = await fetch()
        try:
            raw = chain.model_dump_json()
        except AttributeError:
            return chain
        _chain_local[key] = (time.monotonic(), raw)
        await cache_set(key, raw, CHAIN_SNAPSHOT_SECONDS)
        return chain


# --- the worker side: one stream per connection ------------------------------------------------------------------------

def stream_adapter(adapter: Any) -> Any:
    """The broker adapter itself under the worker's wrappers (the rate budget's `RateLimitedBroker`, the contract-symbol
    wrapper): a stream speaks the broker's own protocol (Upstox's authorised feed URL, Kite's api key and token), which
    the wrappers do not pass through. Only the stream's connect goes this way; every REST call keeps its budget."""
    found = adapter
    for _ in range(4):
        inner = getattr(found, "inner", None)
        if inner is None or inner is found:
            break
        found = inner
    return found


def adapter_fingerprint(adapter: Any) -> str:
    """Changes when the connection's login is renewed (a new access token). Hashed: never the token itself."""
    token = getattr(adapter, "access_token", None)
    if not token:
        token = getattr(getattr(adapter, "credentials", None), "access_token", None)
    return hashlib.sha256(str(token or "").encode()).hexdigest()[:16]


def record_fingerprint(record: Any) -> str:
    """A credential row's login fingerprint: store_access_token / mark_verified move these on a renewed login."""
    parts = (getattr(record, "id", None), getattr(record, "token_status", None), getattr(record, "token_expires_at", None),
             getattr(record, "last_verified_at", None))
    return hashlib.sha256(repr(parts).encode()).hexdigest()[:16]


@dataclass
class RenewPrompt:
    tenant_id: int
    user_id: Optional[int]
    broker: str


@dataclass
class HubConnection:
    namespace: str
    broker: str
    account_label: str
    stream: TickStream
    task: "asyncio.Task[None]"
    fingerprint: str
    cache: HubTickCache


@dataclass
class DataHub:
    """The worker's hub: one stream per connection (organisation sessions here; users' own sessions through the fan-in,
    which calls `attach`). `prompts`: the connections whose login ended, once per login (see `need_renew`)."""

    stream_factory: Callable[[Any], Optional[TickStream]] = stream_for
    symbols: SessionSymbols = field(default_factory=lambda: session_symbols)
    connections: Dict[str, HubConnection] = field(default_factory=dict)
    prompts: Dict[str, RenewPrompt] = field(default_factory=dict)
    _prompted: Dict[str, str] = field(default_factory=dict)      # namespace -> the fingerprint already prompted for

    # -- a stream joins the hub --
    def attach(self, stream: TickStream, namespace: str, broker: str, account_label: str = "primary") -> HubTickCache:
        cache = HubTickCache(namespace)
        _hub_caches[namespace] = cache
        stream.cache = cache
        stream.notify_listeners = False            # never pooled into the lake or a platform-wide consumer
        stream.stop_on_auth_error = True           # a refused token is not retried every few seconds

        async def on_state(s: TickStream) -> None:
            await publish_status(HubStatus(namespace=namespace, broker=broker, state=s.state, since=_now_iso(), reconnects=s.reconnects,
                                           subscribed=s.subscribed, retry_seconds=s.next_retry_seconds, account_label=account_label))
        stream.on_state = on_state
        return cache

    async def ensure_org(self, tenant_id: int, adapter: Any, positions: Iterable[Pair], now: datetime) -> Optional[HubConnection]:
        """The organisation's one stream: positions and running strategies first, then what its pages asked for."""
        namespace = org_namespace(tenant_id)
        broker = str(getattr(adapter, "name", "") or "broker")
        await self.symbols.replace(namespace, Priority.POSITION, sorted(set(positions)), now)
        plan = await self.symbols.plan(namespace, session_cap(broker), now)
        fingerprint = adapter_fingerprint(adapter)
        conn = self.connections.get(namespace)
        if conn is not None and (conn.fingerprint != fingerprint or conn.broker != broker):
            await self.drop(namespace)                      # a renewed login (or another broker): a fresh stream
            conn = None
        if conn is not None and conn.task.done() and not conn.stream.auth_failed:
            await self.drop(namespace)                      # the task ended for another reason: start again
            conn = None
        if conn is None:
            stream = self.stream_factory(stream_adapter(adapter))
            if stream is None:
                await publish_status(HubStatus(namespace=namespace, broker=broker, state=HubState.POLLING.value, since=_now_iso(),
                                               subscribed=len(plan.kept)))
                return None
            cache = self.attach(stream, namespace, broker)
            task = asyncio.create_task(stream.run(), name=f"hub-stream:{namespace}")
            conn = HubConnection(namespace=namespace, broker=broker, account_label="primary", stream=stream, task=task,
                                 fingerprint=fingerprint, cache=cache)
            self.connections[namespace] = conn
            self._prompted.pop(namespace, None)
        if conn.stream.auth_failed:
            self.need_renew(namespace, tenant_id, None, broker, fingerprint)
        else:
            await conn.stream.set_symbols(plan.kept)
        await conn.cache.flush()
        await publish_status(HubStatus(namespace=namespace, broker=broker, state=conn.stream.state, since=_now_iso(),
                                       reconnects=conn.stream.reconnects, subscribed=conn.stream.subscribed,
                                       retry_seconds=conn.stream.next_retry_seconds))
        return conn

    async def org_without_session(self, tenant_id: int, brokers: Sequence[str]) -> None:
        """No usable session for the organisation: its stream stops (a dead token is not retried); the status says renew."""
        namespace = org_namespace(tenant_id)
        await self.drop(namespace)
        broker = brokers[0] if brokers else "broker"
        await publish_status(HubStatus(namespace=namespace, broker=broker, state=HubState.RENEW_NEEDED.value, since=_now_iso()))
        self.need_renew(namespace, tenant_id, None, broker, "no-session")

    def need_renew(self, namespace: str, tenant_id: int, user_id: Optional[int], broker: str, fingerprint: str) -> None:
        """Record ONE renew per connection and login (the same ended login is never counted twice). The prompt itself is
        the app's one notice (the `renew_needed` status every page reads) plus the worker's existing single "session
        expired" notification when its token check finds the login ended - the hub sends no notification of its own, so
        one ended login never makes two notices."""
        if self._prompted.get(namespace) == fingerprint:
            return
        self._prompted[namespace] = fingerprint
        self.prompts[namespace] = RenewPrompt(tenant_id=tenant_id, user_id=user_id, broker=broker)

    def renewed(self, namespace: str) -> None:
        self._prompted.pop(namespace, None)
        self.prompts.pop(namespace, None)

    async def drop(self, namespace: str) -> bool:
        conn = self.connections.pop(namespace, None)
        if conn is None:
            return False
        conn.stream.stop()
        conn.task.cancel()
        try:
            await conn.task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001 - a stream's own error is not the teardown's
            pass
        conn.cache.clear()
        if _hub_caches.get(namespace) is conn.cache:
            _hub_caches.pop(namespace, None)
        return True

    async def stop_all(self) -> None:
        for namespace in list(self.connections):
            await self.drop(namespace)

    def status(self) -> Dict[str, dict]:
        return {ns: conn.stream.status().as_dict() | {"state": conn.stream.state} for ns, conn in self.connections.items()}


# --- the API side: which connection serves this user -----------------------------------------------------------------

@dataclass
class UserConnection:
    """The broker connection whose hub serves this signed-in user: the user's own session (byob on) or the
    organisation's (byob off). `renew` = a linked connection whose login has ended (the one renew prompt)."""
    namespace: str
    broker: str
    account_label: str
    byob: bool
    renew: bool
    record: Any = None

    @property
    def tick_cache(self) -> TickCache:
        return tick_cache_for(self.namespace)


async def user_connection(session: Any, user: Any, now: Optional[datetime] = None) -> Optional[UserConnection]:
    """None when the hub is off for the user's organisation or no broker is linked (the broker gate covers that)."""
    from sqlalchemy import select

    from app.brokers.token_lifecycle import data_is_usable, token_is_usable
    from app.db.models import BrokerCredentialRecord
    from app.market_data import entitlement
    from app.market_data.data_terms import terms_for
    from app.market_data.fanin import FLAG as BYOB_FLAG, SessionRef
    from app.platform.controls import flag_enabled

    if user is None or not await hub_on(session, user.tenant_id):
        return None
    now = now or datetime.now(timezone.utc)
    if await flag_enabled(session, BYOB_FLAG, user.tenant_id):
        records = await entitlement.own_records(session, user)
        live = entitlement.pick_session(records, now)
        if live is not None:
            ref = SessionRef.of(live)
            return UserConnection(namespace=ref.namespace, broker=ref.broker, account_label=ref.account_label, byob=True, renew=False, record=live)
        shown = [r for r in records if terms_for(r.broker_name).tier_a_allowed]
        if not shown:
            return None
        ref = SessionRef.of(shown[0])
        return UserConnection(namespace=ref.namespace, broker=ref.broker, account_label=ref.account_label, byob=True, renew=True, record=shown[0])
    records = list(await session.scalars(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == user.tenant_id)
                                         .order_by(BrokerCredentialRecord.broker_name, BrokerCredentialRecord.account_label)))
    if not records:
        return None
    usable = [r for r in records if token_is_usable(r, now)] or [r for r in records if data_is_usable(r, now)]
    chosen = usable[0] if usable else records[0]
    return UserConnection(namespace=org_namespace(user.tenant_id), broker=chosen.broker_name, account_label=chosen.account_label or "primary",
                          byob=False, renew=not usable, record=chosen)


async def connection_status(conn: UserConnection) -> HubStatus:
    """The connection's status for the browser: the worker's when it published one, else derived from the row (a login
    that ended reads `renew_needed` even before the worker's next cycle; a usable one `polling` until a stream says)."""
    if conn.renew:
        return HubStatus(namespace=conn.namespace, broker=conn.broker, state=HubState.RENEW_NEEDED.value, since=_now_iso(),
                         account_label=conn.account_label)
    found = await read_status(conn.namespace)
    if found is not None and found.state != HubState.RENEW_NEEDED.value:
        found.account_label = conn.account_label if found.broker == conn.broker else found.account_label
        return found
    # A renew_needed the worker published before the user logged in again is over once the row is usable.
    return HubStatus(namespace=conn.namespace, broker=conn.broker, state=HubState.POLLING.value if found is None else HubState.CONNECTING.value,
                     since=_now_iso(), account_label=conn.account_label)


async def admit_for_user(session: Any, user: Any, conn: UserConnection, pairs: Sequence[Pair]) -> List[Pair]:
    """Register a page's symbols on the user's connection: with byob on through the user's own entitlement (data terms,
    the session's budget), else on the organisation's connection inside its cap. Returns the ones the hub streams."""
    if conn.renew or not pairs:
        return []
    if conn.byob:
        from app.market_data import entitlement
        ents = await entitlement.resolve_many(session, user, list(pairs), priority=Priority.CHART, register=True)
        return [pair for pair, ent in ents.items() if ent.live and ent.session is not None and ent.session.namespace == conn.namespace]
    return await register_symbols(conn.namespace, conn.broker, pairs)


class BrowserFeed:
    """TM-12: one browser tab's `ticks` and `hub` events on the live stream. Each `poll` (once a second) sends the
    registered symbols whose price moved since the last send - at most one update per symbol per poll - from the
    connection's snapshot (one read per connection per process, shared by every tab), and the connection's status when
    it changed. The connection is looked up again every `resolve_seconds` (a renewed login, a new broker), and the
    tab's symbols are registered again every `register_seconds` so the hub keeps streaming them while the page is open."""

    def __init__(self, session_factory: Callable[[], Any], user_id: int, sid: str, *, resolve_seconds: float = 5.0,
                 register_seconds: float = 300.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.session_factory = session_factory
        self.user_id = int(user_id)
        self.sid = sid
        self.resolve_seconds = resolve_seconds
        self.register_seconds = register_seconds
        self.clock = clock
        self.conn: Optional[UserConnection] = None
        self._resolved_at = float("-inf")
        self._registered_at = float("-inf")
        self._registered: Tuple[Pair, ...] = ()
        self._last_status: Optional[Tuple[str, str]] = None
        self._sent: Dict[str, str] = {}

    async def _resolve(self) -> None:
        from app.db.models import User
        async with self.session_factory() as session:
            user = await session.get(User, self.user_id)
            self.conn = await user_connection(session, user)
            pairs = tuple(await browser_symbols(self.user_id, self.sid))
            if self.conn is not None and pairs and (pairs != self._registered or self.clock() - self._registered_at >= self.register_seconds):
                await admit_for_user(session, user, self.conn, pairs)
                self._registered, self._registered_at = pairs, self.clock()

    async def poll(self) -> List[Tuple[str, dict]]:
        out: List[Tuple[str, dict]] = []
        if self.clock() - self._resolved_at >= self.resolve_seconds:
            self._resolved_at = self.clock()
            await self._resolve()
            if self.conn is not None:
                status = await connection_status(self.conn)
                seen = (status.state, status.broker)
                if seen != self._last_status:
                    self._last_status = seen
                    out.append(("hub", status.public()))
        if self.conn is None or self.conn.renew:
            return out
        pairs = await browser_symbols(self.user_id, self.sid)
        if not pairs:
            return out
        snapshot = await read_snapshot(self.conn.namespace)
        items = []
        for symbol, exchange in pairs:
            key = f"{exchange}:{symbol}"
            found = snapshot.get(key)
            mark = f"{found[0]}@{found[1]}" if found else ""
            if not found or self._sent.get(key) == mark:
                continue
            self._sent[key] = mark
            items.append({"symbol": symbol, "exchange": exchange, "ltp": found[0], "ts": found[1]})
        if items:
            out.append(("ticks", {"items": items}))
        return out


async def org_feed(session: Any, tenant_id: int, broker: str, pairs: Sequence[Pair], priority: Priority = Priority.CHART) -> Optional[TickCache]:
    """With the hub on (and byob off), the organisation's own connection's ticks for an API read - registering `pairs`
    on it so its one stream carries them - instead of the platform-wide tick keys. None: the hub is off (as before)."""
    if not await hub_on(session, tenant_id):
        return None
    namespace = org_namespace(tenant_id)
    try:
        await register_symbols(namespace, broker, pairs, priority)
    except Exception:  # noqa: BLE001 - registering is a hint for the worker; the read itself goes on
        logger.debug("hub: could not register symbols for %s", namespace, exc_info=True)
    return tick_cache_for(namespace)


async def hub_on(session: Any, tenant_id: Optional[int]) -> bool:
    from app.platform.controls import flag_enabled
    return await flag_enabled(session, FLAG, tenant_id)


async def hub_tenants(session: Any) -> Tuple[bool, set]:
    """(on for everyone, the tenants it is on for) - one read of the flags for a whole worker cycle."""
    from app.platform.controls import feature_flags
    flag = (await feature_flags(session)).get(FLAG) or {}
    return bool(flag.get("on")), set(flag.get("tenants") or [])


__all__ = [
    "BROWSER_SYMBOL_CAP", "DataHub", "FLAG", "HubState", "HubStatus", "HubTickCache", "browser_symbols", "hub_on", "org_namespace",
    "plain_message", "publish_status", "read_snapshot", "read_status", "register_symbols", "set_browser_symbols", "shared_chain",
]
