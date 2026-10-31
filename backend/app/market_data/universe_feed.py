"""M71 PL-1b / PL-4: the worker's market-data feed for the active universe -> ATP's own 1-minute (and 5m / 15m / 60m)
bars, with feed health. Off unless MARKET_FEED_ENABLED.

What it extends (no second feed): the Phase S `TickStream` (Upstox Market Data Feed V3 / Kite ticker; connect, subscribe,
decode, reconnect with back-off), the B2 idea of building bars from streamed ticks (now `bar_builder`, with a grace
window, the session rules and resampling), and the HB2 owner session and private store the backfill already uses.

- **Active universe** (`active_universe`): the indices the platform knows (`instruments.master.INDEX_SYMBOLS` and
  LAKE_BACKFILL_INDICES), the F&O stocks (the system watchlist "fno-stocks") and every symbol on any user's watchlist,
  deduplicated by store key (indices first, then F&O, then watchlists - the order the connection split keeps). NSE / BSE
  cash and indices only (the NSE clock; F&O contracts and MCX are not built here). Re-read every
  MARKET_FEED_UNIVERSE_SECONDS.
- **Whose data** (M71Q-1 option 1, as a setting, never a hard-coded user): MARKET_FEED_CREDENTIAL=owner = HB2's owner
  session (`intraday_history.owner_session`: HISTORY_OWNER_TENANT_ID confirmed by _NAME, HISTORY_OWNER_USER_ID or the
  lowest-id active OWNER). The bars go to that tenant's PRIVATE store under the holder's source
  (`broker:<name>:u<holder>`), so only that member reads them (BT1b; M71Q-2: a credential's live bars are visible only to
  its own user). Each other user's own broker data stays in his own session (fanin / hub), never here. While
  MARKET_FEED_SHARE_WITH_OTHERS is off (default; docs/BROKER_DATA_TERMS.md) the index keys that `own_bars.shared_scope`
  serves to other organisations (M55 T4) are held back: no live bar of them is stored, so this feed never widens that
  path. The ticks go to a namespaced tick cache and never to the platform-wide listeners (the B2 lake recorder).
- **Connections**: the universe is split into chunks of the broker's per-connection cap (`FEED_LIMITS`, from the
  brokers' published figures - "to confirm" values, overridable by MARKET_FEED_SYMBOLS_PER_CONNECTION) over at most
  MARKET_FEED_MAX_CONNECTIONS connections; what does not fit is reported (`symbols_over_limit`), never subscribed.
- **Reconnect and gaps**: the stream reconnects with its back-off. The moment a connection drops, the minutes from then
  on are suppressed for its symbols (the bar being built is discarded, never written half); when it is live again the
  outage (the drop minute to the reconnect minute, inside the session) is the gap: for each symbol and interval ONLY that
  span is asked through `feed_health.request_gap_fill` (the PL-1c backfill fills it; until a filler is registered the gap
  stays "pending" in the health document). Nothing here fetches history over REST.
- Connections are a Phase S `StreamManager` of the feed's own (keys c1, c2, ...; one per connection), bars are built
  and written by `recorder.LiveBarRecorder` (bar_builder) once per worker cycle: closed bars only, labelled by END.
- Per-user connection limit: the credential's other streams (the organisation stream, the data hub) count against the
  broker's per-user figure too; MARKET_FEED_MAX_CONNECTIONS defaults to 1 so the feed leaves them room.
Nothing here reads, places or changes an order.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.market_data import feed_health
from app.market_data.bar_builder import MINUTE, gap_range, minute_start, session_bounds
from app.market_data.stream import StreamManager, Tick, TickCache, TickStream, UpstoxTickStream, stream_for

logger = logging.getLogger(__name__)

GROUP_ORDER = {"index": 0, "fno": 1, "watchlist": 2}
INTERVALS = ("1m", "5m", "15m", "60m")
OPEN_GAP = timedelta(days=1)            # an outage still going on is suppressed open-ended until the reconnect

# Instruments per connection and connections per user, from each broker's published figures (docs/BROKER_DATA_TERMS.md,
# M71 PL-5 research, 31 Oct). All "to confirm" - MARKET_FEED_SYMBOLS_PER_CONNECTION / _MAX_CONNECTIONS can only lower them.
# Upstox Market Data Feed V3 (upstox.com/developer/api-documentation/v3/get-market-data-feed): per connection 5000
# instrument keys in ltpc, 2000 in full (one mode per connection); 2 connections per user (5 with Upstox Plus).
# Kite Connect WebSocket: 3000 instruments per connection, 3 connections per api_key. Dhan: 5000 per connection, 5
# connections. Angel One SmartAPI: 1000 per session. Kotak Neo: 200 per subscription request. Groww: 1000.
FEED_LIMITS: dict[str, dict[str, int]] = {
    "upstox": {"ltpc": 5000, "full": 2000, "connections": 2},
    "zerodha": {"any": 3000, "connections": 3},
    "dhan": {"any": 5000, "connections": 5},
    "angel_one": {"any": 1000, "connections": 1},
    "kotak_neo": {"any": 200, "connections": 1},
    "groww": {"any": 1000, "connections": 1},
}


@dataclass(frozen=True)
class UniverseItem:
    key: str            # the store's instrument key (NSE:RELIANCE, NSE_INDEX:NIFTY50)
    symbol: str         # the broker-facing symbol (RELIANCE, NIFTY 50)
    exchange: str       # the broker-facing exchange (NSE / BSE)
    group: str          # index | fno | watchlist


# --- the universe ----------------------------------------------------------------------------------------------------
def _index_item(name: str) -> UniverseItem | None:
    from app.market_lake.index_history import target
    try:
        t = target(name)
    except ValueError:
        return None
    return UniverseItem(t.key, t.symbol, t.exchange, "index")


def index_items() -> list[UniverseItem]:
    from app.instruments.master import INDEX_SYMBOLS
    out = [_index_item(n) for n in list(INDEX_SYMBOLS) + list(config.LAKE_BACKFILL_INDICES)]
    return [i for i in out if i is not None]


def item_for(symbol: str, exchange: str | None, group: str) -> UniverseItem | None:
    """One watchlist row -> a universe item, or None when it is not an NSE / BSE cash symbol or a known index."""
    from app.instruments.master import INDEX_SYMBOLS
    from app.market_lake.intraday_history import parse_instrument
    sym = " ".join((symbol or "").upper().split())
    ex = (exchange or "NSE").strip().upper()
    by_symbol = {v: k for k, v in INDEX_SYMBOLS.items()}
    if not sym:
        return None
    if ex.endswith("_INDEX") or sym in by_symbol or sym in INDEX_SYMBOLS:
        return _index_item(by_symbol.get(sym, sym))
    if ex not in ("NSE", "BSE"):
        return None
    try:
        inst = parse_instrument(f"{ex}:{sym}")
    except ValueError:
        return None
    return UniverseItem(inst.key, inst.symbol, inst.exchange, group)


def dedupe(items: Iterable[UniverseItem]) -> list[UniverseItem]:
    """One item per store key; the earlier group wins (indices, F&O, watchlists); order kept inside a group."""
    out: dict[str, UniverseItem] = {}
    for item in sorted(items, key=lambda i: GROUP_ORDER.get(i.group, 9)):      # sorted() is stable
        out.setdefault(item.key, item)
    return list(out.values())


async def active_universe(session: AsyncSession) -> list[UniverseItem]:
    from app.db.models import WatchlistRecord
    from app.watchlists import service as watchlist_service
    from app.watchlists import system as system_lists
    items: list[UniverseItem | None] = list(index_items())
    fno = await system_lists.get(session, "fno-stocks")
    if fno is not None:
        items += [item_for(i.get("symbol", ""), i.get("exchange"), "fno") for i in system_lists.watch_items(fno)]
    for row in await session.scalars(select(WatchlistRecord).order_by(WatchlistRecord.id)):
        items += [item_for(i.get("symbol", ""), i.get("exchange"), "watchlist") for i in watchlist_service.items_of(row)]
    return dedupe(i for i in items if i is not None)


# --- limits and the split ---------------------------------------------------------------------------------------------
def per_connection_cap(broker: str, mode: str) -> int:
    limits = FEED_LIMITS.get(broker, {})
    cap = limits.get(mode, limits.get("any", 0))
    if config.MARKET_FEED_SYMBOLS_PER_CONNECTION:
        cap = min(cap, config.MARKET_FEED_SYMBOLS_PER_CONNECTION) if cap else config.MARKET_FEED_SYMBOLS_PER_CONNECTION
    return cap


def max_connections(broker: str) -> int:
    return max(1, min(FEED_LIMITS.get(broker, {}).get("connections", 1), config.MARKET_FEED_MAX_CONNECTIONS))


def split(items: Sequence[UniverseItem], cap: int, connections: int) -> tuple[list[list[UniverseItem]], list[UniverseItem]]:
    """Chunks of at most `cap` items over at most `connections` connections, in universe order; the rest overflow."""
    if cap <= 0 or connections <= 0:
        return [], list(items)
    fit = list(items[:cap * connections])
    return [fit[i:i + cap] for i in range(0, len(fit), cap)], list(items[cap * connections:])


# --- whose data -------------------------------------------------------------------------------------------------------
async def owner_credential(session: AsyncSession, now: datetime) -> tuple[int, Any, Any] | None:
    """(tenant_id, user, credential) of HB2's owner session, or None (not configured / not logged in today)."""
    from app.market_lake import intraday_history
    try:
        tenant_id = await intraday_history.owner_tenant(session)
    except intraday_history.IntradayConfigError as exc:
        feed_health.health.note = str(exc)[:200]
        return None
    found = await intraday_history.owner_session(session, tenant_id, now)
    if found is None:
        feed_health.health.note = "no usable broker session of the data owner today (the owner logs in to the broker in ATP)"
        return None
    return tenant_id, found[0], found[1]


def _default_adapter(record: Any) -> Any:
    from app.brokers.token_lifecycle import build_adapter
    return build_adapter(record)


def default_stream(adapter: Any, mode: str, cache: TickCache) -> TickStream | None:
    if getattr(adapter, "name", "") == "upstox":
        return UpstoxTickStream(adapter, mode=mode, cache=cache)
    stream = stream_for(adapter)
    if stream is not None:
        stream.cache = cache
    return stream


def held_back(key: str) -> bool:
    """M71Q-2: a key whose stored live bars another organisation would read (M55 T4 shared index scope)."""
    if config.MARKET_FEED_SHARE_WITH_OTHERS:
        return False
    from app.market_lake.index_history import is_target_key
    return is_target_key(key)


# --- the feed ---------------------------------------------------------------------------------------------------------
StreamFactory = Callable[[Any, str, TickCache], TickStream | None]
GapRequester = Callable[[str, str, datetime, datetime], Awaitable[str]]


class UniverseFeed:
    """The connections (a Phase S `StreamManager` of its own, keyed c1, c2, ...), the bars (`recorder.LiveBarRecorder`)
    and the health. One instance in the worker."""

    def __init__(self, *, stream_factory: StreamFactory = default_stream, adapter_factory: Callable[[Any], Any] = _default_adapter,
                 universe_loader: Callable[[AsyncSession], Awaitable[list[UniverseItem]]] = active_universe,
                 owner_loader: Callable[[AsyncSession, datetime], Awaitable[tuple[int, Any, Any] | None]] = owner_credential,
                 gap_requester: GapRequester | None = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        from app.market_lake.recorder import LiveBarRecorder
        self.stream_factory = stream_factory
        self.adapter_factory = adapter_factory
        self.universe_loader = universe_loader
        self.owner_loader = owner_loader
        self.gap_requester = gap_requester or feed_health.request_gap_fill
        self.clock = clock
        self.recorder = LiveBarRecorder(config.MARKET_FEED_LATE_GRACE_SECONDS)
        self.streams = StreamManager(factory=self._make_stream, on_created=self._wire)
        self.keys: dict[str, dict[tuple[str, str], str]] = {}     # connection id -> (symbol, exchange) -> store key
        self.items: list[UniverseItem] = []
        self.universe_at: datetime | None = None
        self.credential_id: int | None = None
        self.adapter: Any = None
        self.broker: str | None = None
        self.mode = "any"
        self.cache: TickCache | None = None
        self.down_since: dict[str, datetime] = {}            # connection id -> when it dropped (the gap's start)
        self._seen_live: set[str] = set()
        self._late_seen = 0

    @property
    def pipeline(self) -> Any:
        return self.recorder.pipeline

    # --- streams
    def _make_stream(self, adapter: Any) -> TickStream | None:
        return self.stream_factory(adapter, self.mode, self.cache or TickCache(namespace="feed"))

    def _wire(self, cid: str, stream: TickStream) -> None:
        stream.notify_listeners = False                       # never the platform-wide listeners (data terms)

        def on_tick(tick: Tick) -> None:
            key = self.keys.get(cid, {}).get((tick.symbol, tick.exchange))
            if key is not None:
                self.recorder.on_tick(key, tick)

        async def on_state(s: TickStream) -> None:
            now = self.clock()
            if s.state in ("reconnecting", "renew_needed"):
                await self.went_down(cid, now)
            elif s.state == "live":
                await self.came_up(cid, now)

        stream.listeners.append(on_tick)
        stream.on_state = on_state

    # --- reconnect / gap
    def _keys_of(self, cid: str) -> list[str]:
        return sorted(set(self.keys.get(cid, {}).values()))

    async def went_down(self, cid: str, now: datetime) -> None:
        if cid not in self._seen_live or cid in self.down_since:
            return
        self.down_since[cid] = now
        feed_health.health.reconnected(cid, self.broker or "", now)
        start = minute_start(now)
        self.pipeline.gap(self._keys_of(cid), start, start + OPEN_GAP)      # the forming bar is never written half

    async def came_up(self, cid: str, now: datetime) -> None:
        self._seen_live.add(cid)
        down = self.down_since.pop(cid, None)
        if down is None:
            return
        await self.gap(self._keys_of(cid), down, now)

    async def gap(self, keys: Sequence[str], down: datetime, up: datetime) -> int:
        """The outage [drop minute, reconnect minute] inside the session -> suppressed minutes, and ONLY that span asked
        per symbol and interval. Returns the number of requests."""
        start = minute_start(down)
        end = minute_start(up) + MINUTE                       # the reconnect minute is partial too
        self.pipeline.gap(keys, start, end)
        asked = 0
        for key in keys:
            open_, close = session_bounds(key, start)
            lo, hi = max(start, open_), min(end, close)
            if lo >= hi:
                continue
            for interval in INTERVALS:
                s, e = gap_range(key, lo, hi, int(interval[:-1]))
                await self.gap_requester(key, interval, s, e)
                asked += 1
        return asked

    # --- lifecycle
    async def stop(self) -> None:
        await self.streams.stop_all()
        self.keys.clear()
        self.down_since.clear()
        self._seen_live.clear()
        feed_health.health.connections.clear()

    async def _write(self, session_factory: Any, tenant_id: int, source: str, now: datetime) -> int:
        async with session_factory() as session:
            result = await self.recorder.drain(session, tenant_id, source, now)
        written = int(result.inserted) + int(result.corrected)
        feed_health.health.bars_written_today += written
        return written

    async def cycle(self, session_factory: Any, now: datetime, *, market_open: bool) -> int:
        """One worker cycle: keep the connections on the universe while the market is open, write the closed bars,
        publish the health. Returns the bars written."""
        h = feed_health.health
        h.roll(now)
        if not config.MARKET_FEED_ENABLED or config.MARKET_FEED_CREDENTIAL != "owner":
            h.state, h.note = "off", "the market-data feed is off (MARKET_FEED_ENABLED / MARKET_FEED_CREDENTIAL)"
            await self.stop()
            await feed_health.publish(now)
            return 0
        async with session_factory() as session:
            owner = await self.owner_loader(session, now)
        if owner is None:
            h.state = "no_session"
            await self.stop()
            self.credential_id = None
            await feed_health.publish(now)
            return 0
        tenant_id, user, record = owner
        from app.market_lake.intraday_history import holder_source
        source = holder_source(record.broker_name, user.id)
        if not market_open:
            written = await self._write(session_factory, tenant_id, source, now)
            await self.stop()
            h.state, h.note = "closed", "market closed: no feed"
            await feed_health.publish(now)
            return written
        if self.credential_id != record.id:
            await self.stop()
            self.credential_id, self.broker = record.id, record.broker_name
            self.adapter = self.adapter_factory(record)
            self.cache = TickCache(namespace=f"feed.u{int(user.id)}")      # never the shared md:tick:* keys
        await self._sync_universe(session_factory, now)
        written = await self._write(session_factory, tenant_id, source, now)
        self._health()
        await feed_health.publish(now)
        return written

    async def _sync_universe(self, session_factory: Any, now: datetime) -> None:
        h = feed_health.health
        due = self.universe_at is None or (now - self.universe_at).total_seconds() >= config.MARKET_FEED_UNIVERSE_SECONDS
        if due:
            async with session_factory() as session:
                items = await self.universe_loader(session)
            kept = [i for i in items if not held_back(i.key)]
            h.symbols_held_back = len(items) - len(kept)
            self.items, self.universe_at = kept, now
            h.universe_at = now
        broker = self.broker or ""
        self.mode = (config.MARKET_FEED_MODE if config.MARKET_FEED_MODE in ("ltpc", "full") else "full") if broker == "upstox" else "any"
        cap, conns = per_connection_cap(broker, self.mode), max_connections(broker)
        chunks, overflow = split(self.items, cap, conns)
        h.symbols_wanted, h.symbols_over_limit = len(self.items), len(overflow)
        h.per_connection_cap, h.max_connections, h.broker = cap or None, conns, broker
        wanted = {f"c{n}": chunk for n, chunk in enumerate(chunks, start=1)}
        for cid in [c for c in self.streams.keys() if c not in wanted]:
            await self.streams.drop(cid)
            self.keys.pop(cid, None)
            h.connections.pop(cid, None)
        subscribed = {k for chunk in chunks for k in (i.key for i in chunk)}
        gone = {k for m in self.keys.values() for k in m.values()} - subscribed
        if gone:
            self.pipeline.forget(gone)
        for cid, chunk in wanted.items():
            self.keys[cid] = {(i.symbol, i.exchange): i.key for i in chunk}
            if await self.streams.ensure(cid, self.adapter, list(self.keys[cid])) is None:
                h.state, h.note = "error", f"no live feed for {broker}"
                return

    def _health(self) -> None:
        h = feed_health.health
        late = self.pipeline.builder.stats.late
        h.late_ticks_today += max(0, late - self._late_seen)
        self._late_seen = late
        h.stock_bars_without_volume = self.recorder.no_volume
        states = []
        for cid in self.streams.keys():
            stream = self.streams.get(cid)
            if stream is None:
                continue
            ch = h.connection(cid, self.broker or "")
            ch.state, ch.symbols, ch.last_tick_at = stream.state, stream.subscribed, stream.last_tick_at
            states.append(ch.state)
        if states:
            h.state = "live" if all(s == "live" for s in states) else "reconnecting"
            if h.state == "live":
                h.note = ""
