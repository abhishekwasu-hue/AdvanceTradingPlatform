"""Part B2: the tick writer - streamed ticks become 1-minute lake bars (flag LAKE_TICK_WRITER_ENABLED, off by default).

The stream calls `on_tick` for every tick it caches (never awaits, never raises into the stream); the worker calls
`drain` once a cycle to publish the bars that have closed. A bar is written only after its end (CandleBuilder), as
source `stream:<broker>`, instrument key `<EXCHANGE>:<SYMBOL>`. A bounded backlog protects memory if the database is down.
"""
import logging
from datetime import datetime
from typing import Dict, List

from sqlalchemy.ext.asyncio import AsyncSession

from app.market_lake.ingest import CandleBuilder, LakeBar, WriteResult, write_candles

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
