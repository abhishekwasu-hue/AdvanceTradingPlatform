"""Part B3: each crafted problem raises exactly one data-quality event; a clean session raises none; re-scanning
records nothing new. The day is an arbitrary weekday and prices are made up.
"""
import asyncio
from datetime import date, timedelta

from sqlalchemy import delete, select

from app.core import config
from app.db.models import DataQualityEventRecord, MdCandleRecord
from app.market_lake import ingest, quality
from tests.test_auth_api import _session_factory

DAY = date(2000, 1, 3)       # a Monday; only the session layout matters
TF = "15m"


def _run(coro):
    return asyncio.run(coro)


def _session(key: str, closes=None):
    ends = quality.expected_ends(DAY, TF)
    closes = closes or [100.0] * len(ends)
    return [ingest.LakeBar(key, TF, e, c, c + 0.5, c - 0.5, c, 10) for e, c in zip(ends, closes)]


def test_clean_session_has_no_events():
    bars = _session("K")
    assert len(bars) == len(quality.expected_ends(DAY, TF)) > 2
    assert quality.find_gaps(bars, DAY, "s") == [] and quality.find_bar_problems(bars, "s") == []
    assert quality.find_duplicates(bars, "s") == [] and quality.find_mismatches({"a": bars, "b": bars}) == []


def test_each_problem_raises_exactly_one_event(monkeypatch):
    monkeypatch.setattr(config, "LAKE_SPIKE_PCT", 10.0)
    monkeypatch.setattr(config, "LAKE_LATE_SECONDS", 300.0)
    monkeypatch.setattr(config, "LAKE_MISMATCH_PCT", 0.5)
    bars = _session("K")
    gapped = [b for i, b in enumerate(bars) if i not in (4, 5, 9)]                 # two runs of missing bars
    gaps = quality.find_gaps(gapped, DAY, "s")
    assert [e.kind for e in gaps] == ["gap", "gap"] and "2 missing" in gaps[0].detail and "1 missing" in gaps[1].detail
    assert quality.find_gaps(gapped, DAY, "s", until=bars[3].ts) == []            # nothing expected yet

    shifted = _session("K", [100.0] * 10 + [125.0] * (len(bars) - 10))              # a level shift, not a bad print
    assert [e.kind for e in quality.find_bar_problems(shifted, "s")] == ["spike"]

    broken = list(bars)
    b = broken[7]
    broken[7] = ingest.LakeBar(b.instrument_key, b.timeframe, b.ts, b.open, b.close - 1, b.low, b.close, b.volume)
    assert [e.kind for e in quality.find_bar_problems(broken, "s")] == ["invalid"]

    ingested = {x.ts: x.ts + timedelta(seconds=10) for x in bars}
    ingested[bars[3].ts] = bars[3].ts + timedelta(minutes=10)
    assert [e.kind for e in quality.find_bar_problems(bars, "s", ingested=ingested)] == ["late"]

    assert [e.kind for e in quality.find_duplicates(bars + [bars[2]], "s")] == ["duplicate"]

    other = _session("K", [100.0] * 6 + [102.0] + [100.0] * (len(bars) - 7))
    mism = quality.find_mismatches({"a": bars, "b": other})
    assert [e.kind for e in mism] == ["mismatch"] and mism[0].ts == bars[6].ts


def test_scan_day_records_events_once():
    key = "TEST|B3-SCAN"

    async def setup():
        async with _session_factory() as session:
            await session.execute(delete(MdCandleRecord).where(MdCandleRecord.instrument_key == key))
            await session.execute(delete(DataQualityEventRecord).where(DataQualityEventRecord.instrument_key == key))
            await session.commit()
            bars = [b for i, b in enumerate(_session(key)) if i != 3]
            last = bars[-1]
            ingested = last.ts + timedelta(hours=2)                                 # the last bar arrived late
            for bar in bars[:-1]:                                                     # each on time
                await ingest.write_candles(session, [bar], "src", ingested_at=bar.ts + timedelta(seconds=5))
            await ingest.write_candles(session, [last], "src", ingested_at=ingested)
    _run(setup())

    async def scan():
        async with _session_factory() as session:
            counts = await quality.scan_day(session, key, TF, DAY)
            rows = list(await session.scalars(select(DataQualityEventRecord).where(DataQualityEventRecord.instrument_key == key)))
            return counts, len(rows)
    counts, stored = _run(scan())
    assert counts == {"gap": 1, "late": 1} and stored == 2
    assert _run(scan())[1] == 2                                                    # re-scan: nothing new
