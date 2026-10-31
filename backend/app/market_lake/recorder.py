"""Part B2: the tick writer - streamed ticks become 1-minute lake bars (flag LAKE_TICK_WRITER_ENABLED, off by default).

The stream calls `on_tick` for every tick it caches (never awaits, never raises into the stream); the worker calls
`drain` once a cycle to publish the bars that have closed. A bar is written only after its end (CandleBuilder), as
source `stream:<broker>`, instrument key `<EXCHANGE>:<SYMBOL>`. A bounded backlog protects memory if the database is down.

M71 PL-1b: `write_live_bars` is the universe feed's write path (app/market_data/universe_feed.py) - the holder's own
private store, never the shared lake.
"""
import logging
from datetime import datetime
from typing import Dict, List

from sqlalchemy.ext.asyncio import AsyncSession

from app.market_lake.ingest import CandleBuilder, LakeBar, WriteResult, write_candles, write_candles_bulk

logger = logging.getLogger(__name__)


class LakeRecorder:
    def __init__(self, timeframe: str = "1m", max_backlog: int = 50_000) -> None:
        self.timeframe = timeframe
        self.max_backlog = max_backlog
        self._builders: Dict[str, CandleBuilder] = {}
        self._pending: Dict[str, List[LakeBar]] = {}
        self.dropped = 0

    def _queue(self, broker: str, bars: List[LakeBar]) -> None:
        if not bars:
            return
        queue = self._pending.setdefault(broker, [])
        room = self.max_backlog - sum(len(q) for q in self._pending.values())
        if room < len(bars):
            self.dropped += len(bars) - max(room, 0)
            bars = bars[:max(room, 0)]
        queue.extend(bars)

    def on_tick(self, tick) -> None:
        try:
            builder = self._builders.setdefault(tick.broker, CandleBuilder(self.timeframe))
            key = f"{tick.exchange.upper()}:{tick.symbol.upper()}"
            self._queue(tick.broker, builder.feed(key, tick.best_ts, float(tick.ltp)))
        except Exception:  # noqa: BLE001 - recording must never disturb the quote stream
            logger.exception("Lake recorder: tick dropped")

    async def drain(self, session: AsyncSession, now: datetime) -> WriteResult:
        total = WriteResult()
        for broker, builder in self._builders.items():
            self._queue(broker, builder.flush(now))
        for broker in list(self._pending):
            bars = self._pending.pop(broker)
            if bars:
                res = await write_candles(session, bars, f"stream:{broker}")
                total.inserted += res.inserted
                total.corrected += res.corrected
                total.skipped += res.skipped
        return total



def _is_index_key(key: str) -> bool:
    return key.split(":", 1)[0].upper().endswith("_INDEX")


class LiveBarRecorder:
    """M71 PL-1b: the B2 recorder's twin for the universe feed - ticks of ONE credential become closed 1m / 5m / 15m /
    60m bars (app/market_data/bar_builder.py: grace window, session rules, the store's own resampler, DH3) and are
    written once a cycle into that credential holder's PRIVATE store (`write_live_bars`). Never the shared lake (that
    stays LakeRecorder's, from organisation streams), never `tick_listeners`. A bar whose volume the feed did not carry
    is written for an index only (indices carry no traded volume; their broker history stores 0 too); for a stock it is
    left out and counted (`no_volume`), never stored with a made-up 0. A bounded backlog protects memory."""

    def __init__(self, grace_seconds: float = 3.0, max_backlog: int = 200_000) -> None:
        from app.market_data.bar_builder import BarPipeline
        self.pipeline = BarPipeline(grace_seconds)
        self.max_backlog = max_backlog
        self._pending: list[LakeBar] = []
        self.dropped = 0
        self.no_volume = 0

    def _queue(self, bars: list[LakeBar]) -> None:
        room = self.max_backlog - len(self._pending)
        if len(bars) > room:
            self.dropped += len(bars) - max(room, 0)
            bars = bars[:max(room, 0)]
        self._pending.extend(bars)

    def on_tick(self, key: str, tick) -> None:
        """One tick of `key` (the store key). Never raises into the stream."""
        try:
            self._queue(self.pipeline.feed(key, tick.best_ts, float(tick.ltp), tick.received_at,
                                           getattr(tick, "volume_total", None)))
        except Exception:  # noqa: BLE001 - recording must never disturb the stream
            logger.exception("Live bar recorder: tick dropped")

    def take(self, now: datetime) -> list[LakeBar]:
        """The closed bars to write now (the queue plus everything due at `now`), stock bars without volume left out."""
        bars = self._pending + self.pipeline.flush(now)
        self._pending = []
        kept = [b for b in bars if getattr(b, "volume_known", True) or _is_index_key(b.instrument_key)]
        self.no_volume += len(bars) - len(kept)
        return kept

    def requeue(self, bars: list[LakeBar]) -> None:
        """A write failed: the bars wait for the next cycle (bounded)."""
        self._queue(bars)

    async def drain(self, session: AsyncSession, tenant_id: int, source: str, now: datetime) -> WriteResult:
        bars = self.take(now)
        try:
            return await write_live_bars(session, tenant_id, bars, source)
        except Exception:
            await session.rollback()
            self.requeue(bars)
            raise


async def write_live_bars(session: AsyncSession, tenant_id: int, bars: list[LakeBar], source: str) -> WriteResult:
    """M71 PL-1b: the universe feed's CLOSED bars (1m and the 5m / 15m / 60m built from them) into the credential
    holder's own PRIVATE store (`md_tenant_candles`, source `broker:<name>:u<holder>` - read by that member only, BT1b /
    M71Q-2), with the same skip / next-version rules as every other write. DH3: a 1-minute bar that starts at or after
    the session close gets its `post_close` flag (index_history.auction_flags, the backfill's own rule), so the readers
    drop it. One transaction (commits)."""
    from app.market_lake.index_history import auction_flags, write_flags
    if not bars:
        return WriteResult()
    if not source.startswith("broker:"):
        raise ValueError("live bars are stored only under their holder's source (broker:<name>:u<id>)")
    seen: dict[tuple, LakeBar] = {}
    for bar in bars:
        seen[(bar.instrument_key, bar.timeframe, bar.ts)] = bar      # the later copy of one END wins (never two)
    unique = [LakeBar(b.instrument_key, b.timeframe, b.ts, b.open, b.high, b.low, b.close, int(b.volume or 0), b.oi)
              for b in seen.values()]
    result = await write_candles_bulk(session, unique, source, tenant_id=tenant_id)
    minutes: dict[str, list[LakeBar]] = {}
    for bar in unique:
        if bar.timeframe == "1m":
            minutes.setdefault(bar.instrument_key, []).append(bar)
    for key, group in minutes.items():
        await write_flags(session, tenant_id, auction_flags(key, {}, group))
    await session.commit()
    return result
