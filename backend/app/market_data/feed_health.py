"""M71 PL-4: the universe feed's health - what the admin's System page shows (admin only, never a public route).

The worker's feed (app/market_data/universe_feed.py) records here: the symbols subscribed per connection, each
connection's state and last tick, reconnects, the gaps found after a reconnect and whether each was filled, and the bars
written - counters per IST day (they start again at zero on a new IST day). The worker publishes the document to Redis
(`md:feed:health`, refreshed every cycle, lapses when the worker stops) so the API process reads the same figures;
without Redis the API reads what its own process holds (nothing, unless the feed runs there).

`request_gap_fill(symbol, interval, start, end)` is the hook a reconnect calls for every missing span: the gap is recorded
as "pending" and handed to the registered filler (`register_gap_filler`, the PL-1c paced backfill); with no filler
registered it stays "pending" (nothing here fetches history). The filler reports back with `mark_gap_filled`.
Nothing here reads or places an order.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from app.cache.client import cache_get, cache_set
from app.core import config
from app.market_data.calendar import IST

logger = logging.getLogger(__name__)

REDIS_KEY = "md:feed:health"
TTL_SECONDS = 600
MAX_GAPS_KEPT = 200            # the newest gaps of the day kept in the document (the counters count all of them)
GAPS_SHOWN = 20

GapFiller = Callable[[str, str, datetime, datetime], Awaitable[bool]]
_fillers: list[GapFiller] = []


def _iso(ts: datetime | None) -> str | None:
    return ts.astimezone(UTC).isoformat() if ts is not None else None


def _ist_day(now: datetime) -> date:
    return now.astimezone(IST).date()


@dataclass
class ConnectionHealth:
    id: str
    broker: str
    state: str = "connecting"
    symbols: int = 0
    last_tick_at: datetime | None = None
    reconnects_today: int = 0

    def as_dict(self, now: datetime) -> dict[str, Any]:
        return {"id": self.id, "broker": self.broker, "state": self.state, "symbols": self.symbols,
                "last_tick_at": _iso(self.last_tick_at),
                "last_tick_age_seconds": round((now - self.last_tick_at).total_seconds(), 1) if self.last_tick_at else None,
                "reconnects_today": self.reconnects_today}


@dataclass
class Gap:
    symbol: str
    interval: str
    start: datetime
    end: datetime
    status: str = "pending"          # pending | requested | filled | failed
    detected_at: datetime | None = None

    def same(self, symbol: str, interval: str, start: datetime, end: datetime) -> bool:
        return (self.symbol, self.interval, self.start, self.end) == (symbol, interval, start, end)

    def as_dict(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "interval": self.interval, "start": _iso(self.start), "end": _iso(self.end),
                "status": self.status, "detected_at": _iso(self.detected_at)}


@dataclass
class FeedHealth:
    state: str = "off"               # off | no_session | live | reconnecting | closed | error
    broker: str | None = None
    note: str = ""
    day: date | None = None
    connections: dict[str, ConnectionHealth] = field(default_factory=dict)
    symbols_wanted: int = 0
    symbols_over_limit: int = 0
    symbols_held_back: int = 0       # M71Q-2: index keys another organisation would read - no live bar stored
    stock_bars_without_volume: int = 0
    per_connection_cap: int | None = None
    max_connections: int | None = None
    universe_at: datetime | None = None
    reconnects_today: int = 0
    gaps_detected_today: int = 0
    gaps_filled_today: int = 0
    bars_written_today: int = 0
    late_ticks_today: int = 0
    gaps: list[Gap] = field(default_factory=list)
    updated_at: datetime | None = None

    def roll(self, now: datetime) -> None:
        """A new IST day starts every daily counter (and the gap list) at zero."""
        today = _ist_day(now)
        if self.day == today:
            return
        self.day = today
        self.reconnects_today = self.gaps_detected_today = self.gaps_filled_today = 0
        self.bars_written_today = self.late_ticks_today = 0
        self.gaps = [g for g in self.gaps if g.status in ("pending", "requested")]
        for c in self.connections.values():
            c.reconnects_today = 0

    def connection(self, cid: str, broker: str) -> ConnectionHealth:
        conn = self.connections.get(cid)
        if conn is None:
            conn = self.connections[cid] = ConnectionHealth(cid, broker)
        return conn

    def reconnected(self, cid: str, broker: str, now: datetime) -> None:
        self.roll(now)
        self.connection(cid, broker).reconnects_today += 1
        self.reconnects_today += 1

    def add_gap(self, symbol: str, interval: str, start: datetime, end: datetime, now: datetime) -> Gap:
        self.roll(now)
        for g in self.gaps:
            if g.same(symbol, interval, start, end):
                return g
        gap = Gap(symbol, interval, start, end, detected_at=now)
        self.gaps.append(gap)
        self.gaps = self.gaps[-MAX_GAPS_KEPT:]
        self.gaps_detected_today += 1
        return gap

    def gap_done(self, symbol: str, interval: str, start: datetime, end: datetime, ok: bool, now: datetime) -> bool:
        self.roll(now)
        for g in self.gaps:
            if g.same(symbol, interval, start, end) and g.status in ("pending", "requested"):
                g.status = "filled" if ok else "failed"
                if ok:
                    self.gaps_filled_today += 1
                return True
        return False

    def as_dict(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        conns = [c.as_dict(now) for c in sorted(self.connections.values(), key=lambda c: c.id)]
        ages = [c["last_tick_age_seconds"] for c in conns if c["last_tick_age_seconds"] is not None]
        open_gaps = [g for g in self.gaps if g.status in ("pending", "requested")]
        return {
            "enabled": config.MARKET_FEED_ENABLED,
            "credential": config.MARKET_FEED_CREDENTIAL,
            "state": self.state,
            "broker": self.broker,
            "note": self.note,
            "day": self.day.isoformat() if self.day else None,
            "connections": conns,
            "symbols_subscribed": sum(c["symbols"] for c in conns),
            "symbols_wanted": self.symbols_wanted,
            "symbols_over_limit": self.symbols_over_limit,
            "symbols_held_back": self.symbols_held_back,
            "per_connection_cap": self.per_connection_cap,
            "max_connections": self.max_connections,
            "universe_at": _iso(self.universe_at),
            "last_tick_age_seconds": min(ages) if ages else None,
            "reconnects_today": self.reconnects_today,
            "gaps_detected_today": self.gaps_detected_today,
            "gaps_filled_today": self.gaps_filled_today,
            "gaps_pending": len(open_gaps),
            "pending_gaps": [g.as_dict() for g in open_gaps[-GAPS_SHOWN:]],
            "bars_written_today": self.bars_written_today,
            "late_ticks_today": self.late_ticks_today,
            "stock_bars_without_volume": self.stock_bars_without_volume,
            "updated_at": _iso(self.updated_at),
        }


health = FeedHealth()


async def publish(now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    health.roll(now)
    health.updated_at = now
    await cache_set(REDIS_KEY, json.dumps(health.as_dict(now)), TTL_SECONDS)


async def snapshot() -> dict[str, Any]:
    """The admin's figures: the worker's published document (Redis), else this process's own, with the tick ages
    computed now. Keys: enabled, credential, state, broker, note, day, connections[], symbols_subscribed, symbols_wanted,
    symbols_over_limit, symbols_held_back, per_connection_cap, max_connections, universe_at, last_tick_age_seconds,
    reconnects_today, gaps_detected_today, gaps_filled_today, gaps_pending, pending_gaps[], bars_written_today,
    late_ticks_today, stock_bars_without_volume, updated_at."""
    now = datetime.now(UTC)
    raw = await cache_get(REDIS_KEY)
    if raw:
        try:
            doc = json.loads(raw)
            ages = []
            for c in doc.get("connections", []):
                if c.get("last_tick_at"):
                    c["last_tick_age_seconds"] = round((now - datetime.fromisoformat(c["last_tick_at"])).total_seconds(), 1)
                    ages.append(c["last_tick_age_seconds"])
            doc["last_tick_age_seconds"] = min(ages) if ages else None
            return doc
        except (ValueError, TypeError, KeyError):
            logger.warning("feed health document unreadable; showing this process's own")
    return health.as_dict(now)


# --- the gap hook ----------------------------------------------------------------------------------------------------
def register_gap_filler(filler: GapFiller) -> None:
    """PL-1c: the paced backfill registers `filler(symbol, interval, start, end) -> accepted` here."""
    if filler not in _fillers:
        _fillers.append(filler)


def clear_gap_fillers() -> None:
    _fillers.clear()


async def request_gap_fill(symbol: str, interval: str, start: datetime, end: datetime) -> str:
    """Ask for ONLY the missing span (start, end] of `symbol` (the store's instrument key, e.g. NSE:RELIANCE or
    NSE_INDEX:NIFTY50) at `interval` ("1m", "5m", "15m", "60m"; UTC, END-labelled bars). Records the gap and hands it
    to the registered filler. Returns "requested" when a filler accepted it, else "pending" (logged for the admin)."""
    now = datetime.now(UTC)
    gap = health.add_gap(symbol, interval, start, end, now)
    for filler in list(_fillers):
        try:
            if await filler(symbol, interval, start, end):
                gap.status = "requested"
                return gap.status
        except Exception:  # noqa: BLE001 - a filler problem leaves the gap pending, never breaks the feed
            logger.exception("gap filler failed for %s %s", symbol, interval)
    logger.info("feed gap pending: %s %s %s..%s", symbol, interval, _iso(start), _iso(end))
    return gap.status


def mark_gap_filled(symbol: str, interval: str, start: datetime, end: datetime, ok: bool = True) -> bool:
    """The filler's report: the gap was written (ok) or could not be (failed). True when the gap was known."""
    return health.gap_done(symbol, interval, start, end, ok, datetime.now(UTC))


def reset() -> None:
    """Tests: a fresh document."""
    global health
    health = FeedHealth()
