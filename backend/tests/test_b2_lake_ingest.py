"""Part B2: ticks -> bars (labelled by END, published only after close), idempotent writes with corrections as new
versions, broker backfill (START labels -> END labels), and the vendor seam with the mock vendor.
Times are offsets from an arbitrary instant; prices are made up.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete

from app.core.models import OHLCVBar
from app.db.models import MdCandleRecord
from app.market_lake import asof, ingest
from tests.test_auth_api import _session_factory

BASE = datetime(2000, 1, 3, 4, 0, tzinfo=timezone.utc)      # a minute boundary; only offsets matter


def _run(coro):
    return asyncio.run(coro)


def _t(minutes: float) -> datetime:
    return BASE + timedelta(minutes=minutes)


def _clear(key):
    async def go():
        async with _session_factory() as session:
            await session.execute(delete(MdCandleRecord).where(MdCandleRecord.instrument_key == key))
            await session.commit()
    _run(go())


def _session_call(fn):
    async def go():
        async with _session_factory() as session:
            return await fn(session)
    return _run(go())


def test_timeframes():
    assert ingest.timeframe_minutes("1m") == 1 and ingest.timeframe_minutes("15min") == 15
    for bad in ("day", "0m", "", "5h"):
        with pytest.raises(ValueError):
            ingest.timeframe_minutes(bad)


def test_bars_are_labelled_by_end_and_published_only_after_close():
    b = ingest.CandleBuilder("1m")
    assert b.feed("K", _t(0.1), 100, 5) == []
    assert b.feed("K", _t(0.5), 103, 2) == []
    assert b.feed("K", _t(0.9), 99, 1) == []
    assert b.flush(_t(0.99)) == []                                   # still forming: nothing published
    closed = b.feed("K", _t(1.0), 101, 4)                            # a tick exactly at the end opens the next bar
    assert len(closed) == 1
    bar = closed[0]
    assert bar.ts == _t(1) and (bar.open, bar.high, bar.low, bar.close, bar.volume) == (100, 103, 99, 99, 8)
    assert b.feed("K", _t(0.95), 50) == [] and b.late_ticks == 1      # a late tick never rewrites a published bar
    flushed = b.flush(_t(2))
    assert [(x.ts, x.open, x.close) for x in flushed] == [(_t(2), 101, 101)]
    assert b.flush(_t(10)) == []


def test_five_minute_bars_and_independent_instruments():
    b = ingest.CandleBuilder("5m")
    b.feed("A", _t(1), 10)
    b.feed("B", _t(2), 20)
    out = b.feed("A", _t(6), 11)
    assert [(x.instrument_key, x.ts) for x in out] == [("A", _t(5))]
    assert [(x.instrument_key, x.ts) for x in b.flush(_t(5))] == [("B", _t(5))]


def test_writes_are_idempotent_and_corrections_become_versions():
    key = "TEST|B2-WRITE"
    _clear(key)
    bar = ingest.LakeBar(key, "1m", _t(1), 100, 101, 99, 100.5, 10)
    first = _session_call(lambda s: ingest.write_candles(s, [bar], "test", ingested_at=_t(1)))
    again = _session_call(lambda s: ingest.write_candles(s, [bar], "test", ingested_at=_t(2)))
    fixed = ingest.LakeBar(key, "1m", _t(1), 100, 101, 99, 100.75, 12)
    corrected = _session_call(lambda s: ingest.write_candles(s, [fixed], "test", ingested_at=_t(30)))
    assert (first.inserted, again.skipped, corrected.corrected) == (1, 1, 1)
    now = _session_call(lambda s: asof.candles_as_of(s, key, "1m", _t(0), _t(1)))
    before = _session_call(lambda s: asof.candles_as_of(s, key, "1m", _t(0), _t(1), as_of=_t(10)))
    assert [(float(r.close), r.version) for r in now] == [(100.75, 2)]
    assert [(float(r.close), r.version) for r in before] == [(100.5, 1)]


class _Broker:
    name = "fakebroker"

    async def get_historical_data(self, symbol, exchange, interval, from_date, to_date):
        return [OHLCVBar(timestamp=_t(i), open=10 + i, high=11 + i, low=9 + i, close=10.5 + i, volume=100) for i in range(3)]


def test_broker_backfill_relabels_start_to_end():
    key = "TEST|B2-BROKER"
    _clear(key)
    res = _session_call(lambda s: ingest.backfill_from_broker(s, _Broker(), instrument_key=key, symbol="X", exchange="NSE",
                                                               timeframe="1m", start=_t(0), end=_t(3)))
    assert res.inserted == 3
    rows = _session_call(lambda s: asof.candles_as_of(s, key, "1m", _t(0), _t(3)))
    assert [r.ts.replace(tzinfo=timezone.utc) if r.ts.tzinfo is None else r.ts for r in rows] == [_t(1), _t(2), _t(3)]
    assert {r.source for r in rows} == {"broker:fakebroker"} and float(rows[0].open) == 10


def test_vendor_seam_with_the_mock_vendor():
    key = "TEST|B2-VENDOR"
    _clear(key)
    canned = [ingest.LakeBar(key, "1m", _t(i), 1, 1, 1, 1, 1) for i in range(1, 6)]
    mock = ingest.vendor("mock", bars=canned)
    res = _session_call(lambda s: ingest.ingest_from_vendor(s, mock, key, "1m", _t(1), _t(4)))
    assert res.inserted == 3                                           # (start, end]: minutes 2, 3, 4
    assert _session_call(lambda s: ingest.candle_count(s, key, "1m")) == 3
    with pytest.raises(ValueError):
        ingest.vendor("paid-vendor-not-chosen")


# --- the tick writer (LAKE_TICK_WRITER_ENABLED) ---------------------------------------------------------------------

from app.market_data import stream as stream_module          # noqa: E402
from app.market_data.stream import Tick, TickCache, TickStream   # noqa: E402
from app.market_lake.recorder import LakeRecorder            # noqa: E402


def _tick(minutes: float, ltp: float, symbol="B2REC") -> Tick:
    return Tick(broker="teststream", exchange="NSE", symbol=symbol, ltp=ltp, exchange_ts=_t(minutes), received_at=_t(minutes))


def test_recorder_writes_closed_bars_only_and_caps_its_backlog():
    key = "NSE:B2REC"
    _clear(key)
    rec = LakeRecorder("1m")
    for m, p in ((0.2, 10), (0.7, 12), (1.1, 11)):
        rec.on_tick(_tick(m, p))
    res = _session_call(lambda s: rec.drain(s, _t(1.5)))               # bar ending at minute 1 closed; minute 2 forming
    assert res.inserted == 1
    rows = _session_call(lambda s: asof.candles_as_of(s, key, "1m", _t(0), _t(5)))
    assert [(float(r.open), float(r.high), float(r.close), r.source) for r in rows] == [(10, 12, 12, "stream:teststream")]
    assert _session_call(lambda s: rec.drain(s, _t(2))).inserted == 1  # the quiet instrument's bar, flushed on time
    small = LakeRecorder("1m", max_backlog=1)
    for i in range(3):
        small.on_tick(_tick(i + 0.5, 1, symbol="B2CAP"))               # two bars close, room for one
    assert small.dropped == 1
    small.on_tick("not a tick")                                        # never raises into the stream


class _FakeStream(TickStream):
    name = "teststream"

    def decode(self, frame):
        return frame


def test_stream_hands_every_cached_tick_to_the_listeners():
    seen = []
    stream = _FakeStream(cache=TickCache())
    stream._wanted = {"W1": ("B2LIS", "NSE")}
    stream_module.tick_listeners.append(seen.append)
    try:
        _run(stream._handle([("W1", 101.5, _t(0.5)), ("UNKNOWN", 1.0, None)]))
    finally:
        stream_module.tick_listeners.remove(seen.append)
    assert [(t.symbol, t.ltp) for t in seen] == [("B2LIS", 101.5)]


def test_worker_registers_the_recorder_only_with_the_flag(monkeypatch):
    from app.core import config
    from app.workers.trading_worker import TradingWorker
    before = list(stream_module.tick_listeners)
    assert TradingWorker(_session_factory, cycle_seconds=60).lake is None and stream_module.tick_listeners == before
    monkeypatch.setattr(config, "LAKE_TICK_WRITER_ENABLED", True)
    worker = TradingWorker(_session_factory, cycle_seconds=60)
    try:
        assert worker.lake is not None and stream_module.tick_listeners[-1] == worker.lake.on_tick
    finally:
        stream_module.tick_listeners.remove(worker.lake.on_tick)
