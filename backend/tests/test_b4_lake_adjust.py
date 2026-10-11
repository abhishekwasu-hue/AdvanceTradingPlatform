"""Part B4: adjusted / unadjusted views. A 1:2 split halves prices and doubles volume before the ex-date only, in the
adjusted view only; actions compound; an action not yet known at `as_of` does not apply; derivatives are never
adjusted; dividends adjust only with the flag, from the cum-dividend close, leaving volume alone.
Dates and prices are crafted.
"""
import asyncio
from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import delete

from app.db.models import MdCandleRecord, MdCorporateActionRecord
from app.market_data.calendar import IST
from app.market_lake import adjust, ingest
from tests.test_auth_api import _session_factory

D0 = date(2000, 1, 3)
KEY, SYM = "TEST|B4", "B4TEST"


def _run(coro):
    return asyncio.run(coro)


def _bar(day_offset: int, close: float, volume: int = 100) -> ingest.LakeBar:
    ts = datetime.combine(D0 + timedelta(days=day_offset), time(15, 30), tzinfo=IST).astimezone(timezone.utc)
    return ingest.LakeBar(KEY, "1d", ts, close, close, close, close, volume)


def _action(kind, ex_offset, *, new=None, old=None, amount=None, version=1, ingested=None):
    return MdCorporateActionRecord(symbol=SYM, exchange="NSE", ex_date=D0 + timedelta(days=ex_offset), action=kind,
                                   ratio_new=new, ratio_old=old, amount=amount, version=version, source="test",
                                   ingested_at=ingested or datetime(2000, 1, 1, tzinfo=timezone.utc))


def test_split_halves_prices_and_doubles_volume_before_the_ex_date_only():
    bars = [_bar(i, 200.0 if i < 3 else 100.0) for i in range(5)]
    out = adjust.adjust(bars, [_action("SPLIT", 3, new=2, old=1)])
    assert [b.close for b in out] == [100.0] * 5
    assert [b.volume for b in out] == [200, 200, 200, 100, 100]
    assert [b.close for b in bars] == [200.0, 200.0, 200.0, 100.0, 100.0]          # the input is not mutated


def test_actions_compound_and_dividends_need_the_flag():
    bars = [_bar(i, 400.0) for i in range(2)] + [_bar(i, 200.0) for i in range(2, 4)] + [_bar(i, 100.0) for i in range(4, 6)]
    acts = [_action("SPLIT", 2, new=2, old=1), _action("BONUS", 4, new=2, old=1)]
    assert [b.close for b in adjust.adjust(bars, acts)] == [100.0] * 6
    div = [_action("DIVIDEND", 4, amount=10.0)]                                       # cum-dividend close 200 -> factor 0.95
    assert adjust.adjust(bars, div, dividends=False) == sorted(bars, key=lambda b: b.ts)
    with_div = adjust.adjust(bars, div, dividends=True)
    assert [round(b.close, 2) for b in with_div] == [380.0, 380.0, 190.0, 190.0, 100.0, 100.0]
    assert [b.volume for b in with_div] == [100] * 6


def test_lake_view_respects_as_of_and_never_adjusts_derivatives():
    async def setup():
        async with _session_factory() as session:
            await session.execute(delete(MdCandleRecord).where(MdCandleRecord.instrument_key == KEY))
            await session.execute(delete(MdCorporateActionRecord).where(MdCorporateActionRecord.symbol == SYM))
            await session.commit()
            await ingest.write_candles(session, [_bar(i, 200.0 if i < 3 else 100.0) for i in range(5)], "test",
                                       ingested_at=datetime(2000, 1, 1, tzinfo=timezone.utc))
            session.add(_action("SPLIT", 3, new=2, old=1, ingested=datetime(2000, 1, 5, tzinfo=timezone.utc)))
            await session.commit()
    _run(setup())
    start, end = datetime(1999, 12, 1, tzinfo=timezone.utc), datetime(2000, 2, 1, tzinfo=timezone.utc)

    def view(**kw):
        async def go():
            async with _session_factory() as session:
                return [b.close for b in await adjust.candles(session, KEY, "1d", start, end, symbol=SYM, exchange="NSE", **kw)]
        return _run(go())
    assert view() == [100.0] * 5
    assert view(adjusted=False) == [200.0, 200.0, 200.0, 100.0, 100.0]
    assert view(as_of=datetime(2000, 1, 4, tzinfo=timezone.utc)) == [200.0, 200.0, 200.0, 100.0, 100.0]  # split not known yet
    assert view(kind="FUT") == [200.0, 200.0, 200.0, 100.0, 100.0]


@pytest.mark.parametrize("bad", [{"new": None, "old": 1}, {"new": 2, "old": None}])
def test_incomplete_ratios_are_ignored_not_guessed(bad):
    bars = [_bar(0, 50.0)]
    assert adjust.adjust(bars, [_action("SPLIT", 3, **bad)])[0].close == 50.0
