"""Part B1 (ADR-0013): the lake schema and point-in-time reads.

- a row ingested after T is invisible to `as_of=T`; a correction (version 2) replaces version 1 only for `as_of` after
  the correction was ingested; `as_of=None` sees everything;
- candles are selected by bar END (start < ts <= end), one per ts, the preferred source first;
- instrument terms are the latest `valid_from` on or before the day, as known at `as_of` (a lot-size change is not seen
  by a backtest replayed before it was published);
- MWPL / OI per underlying and day, highest visible version (the data D7 needs).
All times and values are crafted relative to a base instant - nothing here is a real date or price.
"""
import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete

from app.db.models import InstrumentMasterVersionRecord, MdCandleRecord, MdPositionLimitRecord
from app.market_lake import asof
from tests.test_auth_api import _session_factory

BASE = datetime(2000, 1, 3, 4, 0, tzinfo=timezone.utc)      # arbitrary instant; only offsets from it matter
KEY = "TEST|LAKE-B1"


def _run(coro):
    return asyncio.run(coro)


def _minutes(n: int) -> datetime:
    return BASE + timedelta(minutes=n)


def _add(*rows):
    async def go():
        async with _session_factory() as session:
            session.add_all(rows)
            await session.commit()
    _run(go())


def _clear(model, column, value):
    async def go():
        async with _session_factory() as session:
            await session.execute(delete(model).where(column == value))
            await session.commit()
    _run(go())


def _candle(minute: int, close: float, *, source="broker", version=1, ingested_minute=None):
    return MdCandleRecord(instrument_key=KEY, timeframe="1m", ts=_minutes(minute), source=source, version=version,
                          open=close, high=close, low=close, close=close, volume=10,
                          ingested_at=_minutes(minute if ingested_minute is None else ingested_minute))


def _candles(start, end, **kw):
    async def go():
        async with _session_factory() as session:
            return [(r.ts.replace(tzinfo=timezone.utc) if r.ts.tzinfo is None else r.ts, float(r.close), r.source, r.version)
                    for r in await asof.candles_as_of(session, KEY, "1m", start, end, **kw)]
    return _run(go())


def test_late_rows_and_corrections_are_invisible_before_they_were_ingested():
    _clear(MdCandleRecord, MdCandleRecord.instrument_key, KEY)
    _add(_candle(1, 100.0), _candle(2, 101.0), _candle(3, 102.0, ingested_minute=10),      # bar 3 arrived late
         _candle(2, 101.5, version=2, ingested_minute=20))                                 # bar 2 corrected later
    now = _candles(_minutes(0), _minutes(3))
    assert [c[1] for c in now] == [100.0, 101.5, 102.0] and now[1][3] == 2
    at_5 = _candles(_minutes(0), _minutes(3), as_of=_minutes(5))
    assert [c[1] for c in at_5] == [100.0, 101.0]                     # late bar unseen, original bar 2
    at_15 = _candles(_minutes(0), _minutes(3), as_of=_minutes(15))
    assert [c[1] for c in at_15] == [100.0, 101.0, 102.0]
    assert [c[1] for c in _candles(_minutes(0), _minutes(3), as_of=_minutes(20))] == [100.0, 101.5, 102.0]


def test_bar_end_window_and_source_preference():
    _clear(MdCandleRecord, MdCandleRecord.instrument_key, KEY)
    _add(_candle(1, 100.0), _candle(2, 101.0), _candle(2, 99.0, source="vendor"), _candle(3, 98.0, source="vendor"))
    assert [c[0] for c in _candles(_minutes(1), _minutes(2))] == [_minutes(2)]      # start excluded, end included
    preferred = _candles(_minutes(0), _minutes(3), sources=["vendor", "broker"])
    assert [(c[1], c[2]) for c in preferred] == [(100.0, "broker"), (99.0, "vendor"), (98.0, "vendor")]
    only_broker = _candles(_minutes(0), _minutes(3), sources=["broker"])
    assert [c[1] for c in only_broker] == [100.0, 101.0]


def test_instrument_terms_as_of_a_day_and_as_known_then():
    key = "TEST|LAKE-B1-FUT"
    _clear(InstrumentMasterVersionRecord, InstrumentMasterVersionRecord.instrument_key, key)
    day0 = BASE.date()
    _add(InstrumentMasterVersionRecord(instrument_key=key, exchange="NFO", tradingsymbol="X", kind="FUT", lot_size=50,
                                       tick_size=0.05, valid_from=day0, source="master", ingested_at=_minutes(0)),
         InstrumentMasterVersionRecord(instrument_key=key, exchange="NFO", tradingsymbol="X", kind="FUT", lot_size=75,
                                       tick_size=0.05, valid_from=day0 + timedelta(days=30), source="master",
                                       ingested_at=_minutes(60 * 24 * 20)))   # published 20 days in, effective day 30

    def lot(day_offset, as_of=None):
        async def go():
            async with _session_factory() as session:
                row = await asof.instrument_as_of(session, key, day0 + timedelta(days=day_offset), as_of=as_of)
                return None if row is None else row.lot_size
        return _run(go())
    assert lot(-1) is None
    assert lot(10) == 50 and lot(30) == 75 and lot(45) == 75
    assert lot(45, as_of=_minutes(60 * 24 * 10)) == 50                     # before the change was published


def test_position_limits_highest_visible_version():
    und = "TEST-LAKE-B1"
    _clear(MdPositionLimitRecord, MdPositionLimitRecord.underlying, und)
    day = BASE.date()
    _add(MdPositionLimitRecord(underlying=und, trade_date=day, source="nse", version=1, mwpl=1000, open_interest=800,
                               ingested_at=_minutes(0)),
         MdPositionLimitRecord(underlying=und, trade_date=day, source="nse", version=2, mwpl=1000, open_interest=860,
                               ingested_at=_minutes(30)))

    def oi(as_of=None):
        async def go():
            async with _session_factory() as session:
                row = await asof.position_limit_as_of(session, und, day, as_of=as_of)
                return None if row is None else row.open_interest
        return _run(go())
    assert oi() == 860 and oi(_minutes(10)) == 800 and oi(_minutes(-1)) is None
