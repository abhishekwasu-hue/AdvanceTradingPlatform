"""M55 T4 superseded by M86 D4 (owner decision): the history owner's index intraday rows (DATA-H) are NOT read by any
other organisation or member any more, whatever `byob_data_tier` says.

Updated to M86 (these tests encoded the old shared behaviour): a member of ANOTHER organisation gets no scope and no rows
with the flag off or on; a teammate in the owner's own organisation gets none either; the owner keeps reading his own
rows, Daily too (D5); the candles route adds no older stored bars for the other reader; the zones job builds nothing for
another organisation's opt-in intraday settings from them (the platform scope stays on the shared lake); a backtest
beyond 30 days has no stored history for him - the broker's answer stands with the plain note.
No network: rows are written straight into the owner's private store; the clock is fixed (2023, no holiday calendar).
"""
import uuid
from datetime import UTC, date, datetime, time, timedelta

import pytest

from app.core import config
from app.core.models import OHLCVBar
from app.db.models import MdTenantCandleRecord
from app.market_data.calendar import IST
from app.market_lake import own_bars
from tests.test_amw2_watchlists import _set_flag
from tests.test_auth_api import _session_factory
from tests.test_data_h_lake_backfill import FRI, NOW, THU, Owner, _run, _teammate, _user, minute_bars

KEY = "NSE_INDEX:NIFTY50"
OLD = date(2023, 1, 10)                       # 59 days before NOW: beyond the broker's 30 days
MINUTE = timedelta(minutes=1)
BYOB = "byob_data_tier"


def _seed(owner: Owner, days=(OLD, THU, FRI)) -> None:
    """The owner's private 1-minute rows of NIFTY (END-labelled, ingested at their END) plus one Daily row."""
    async def go():
        async with _session_factory() as session:
            src = f"broker:upstox:u{owner.user.id}"
            for day in days:
                for b in minute_bars(day, 18000):
                    end = (b.timestamp + MINUTE).astimezone(UTC)
                    session.add(MdTenantCandleRecord(tenant_id=owner.tenant_id, instrument_key=KEY, timeframe="1m", ts=end, source=src,
                                                     version=1, open=b.open, high=b.high, low=b.low, close=b.close, volume=0,
                                                     ingested_at=end))
            close = datetime.combine(FRI, time(15, 30), tzinfo=IST).astimezone(UTC)
            session.add(MdTenantCandleRecord(tenant_id=owner.tenant_id, instrument_key=KEY, timeframe="day", ts=close, source=src,
                                             version=1, open=1, high=2, low=0.5, close=99999.0, volume=0, ingested_at=close))
            await session.commit()
    _run(go())


@pytest.fixture
def lake(monkeypatch):
    _uid = uuid.uuid4().hex[:8]
    owner = Owner(f"m55t4-owner-{_uid}@example.com", monkeypatch, indices=("NIFTY",))
    monkeypatch.setattr(config, "LAKE_BACKFILL_INTRADAY_DAYS", 200)
    _seed(owner)
    other = _user(f"m55t4-other-{_uid}@example.com")
    yield owner, other
    _set_flag(BYOB, other.tenant_id, on=False)
    _set_flag(BYOB, owner.tenant_id, on=False)


def test_another_organisation_reads_no_index_intraday_rows_with_the_flag_off_or_on(lake):
    from app.charts import price_action_layer as pal
    owner, other = lake
    lo, hi = (NOW - timedelta(days=90)).astimezone(UTC), NOW.astimezone(UTC)

    async def reads():
        async with _session_factory() as session:
            scope = await own_bars.scope_for(session, other, KEY)
            q15, src15 = await pal.load_frame(session, KEY, "15m", lo, hi, private=scope)
            return scope, q15, src15
    for on in (False, True):
        _set_flag(BYOB, other.tenant_id, on=on)
        scope, q15, src15 = _run(reads())
        assert scope is None and len(q15) == 0 and src15 is None
    _set_flag(BYOB, other.tenant_id, on=False)

    async def shared():
        async with _session_factory() as session:
            return await own_bars.shared_scope(session, other.tenant_id, KEY), await own_bars.shared_off(session, other.tenant_id, owner.tenant_id)
    assert _run(shared()) == (None, True)


def test_nobody_else_reads_the_owners_rows_and_the_owner_keeps_his_own(lake):
    owner, other = lake
    mate = _teammate(owner.user, f"m55t4-mate-{uuid.uuid4().hex[:8]}@example.com")

    async def go():
        async with _session_factory() as session:
            mine = await own_bars.scope_for(session, owner.user, KEY)
            day = await own_bars.fetch_rows(session, mine, KEY, "day", datetime(2023, 1, 1, tzinfo=UTC), NOW.astimezone(UTC))
            return mine, day, await own_bars.scope_for(session, mate, KEY), await own_bars.scope_for(session, other, KEY)
    mine, day, mates, others = _run(go())
    assert mine == owner.scope and not own_bars.is_shared(mine) and day          # D5: his own rows, Daily too
    assert mates is None and others is None


def test_the_candles_route_adds_no_stored_bars_of_the_owner_for_another_reader(lake):
    from app.market_data import candles_routes as cr
    _owner, other = lake
    start = datetime.combine(FRI, time(9, 15), tzinfo=IST)
    broker = [OHLCVBar.model_validate({"timestamp": (start + timedelta(minutes=i)).isoformat(), "open": 1.0, "high": 2.0, "low": 0.5,
                                       "close": 1.5, "volume": 7}) for i in range(375)]

    async def go(bars):
        async with _session_factory() as session:
            return await cr._stored_older(session, other, KEY, bars, 90, None, FRI, NOW.astimezone(UTC))
    assert _run(go(broker)) == []                                            # only his broker's 30 days
    assert cr._index_keys(["NIFTY", "RELIANCE"]) == {"NIFTY": KEY}


def test_no_zones_from_the_owners_rows_for_another_organisations_opt_in(lake):
    from app.price_action.zones import job
    from app.price_action.zones.settings import resolve_settings
    from app.price_action.zones.store import PLATFORM_SCOPE, PRESETS, platform_layers, tenant_scope
    _owner, other = lake
    intraday = resolve_settings(*platform_layers(), PRESETS["trade_intraday"])

    async def run(scope, tenant_id):
        async with _session_factory() as session:
            result = await job.run_instrument(session, KEY, settings=intraday, scope=scope, tenant_id=tenant_id, now=NOW.astimezone(UTC))
            await session.commit()
            return result.status
    assert _run(run(tenant_scope(other.tenant_id), other.tenant_id)) == "no_data"
    assert _run(run(PLATFORM_SCOPE, None)) == "no_data"                      # the platform scope stays on the shared lake


def test_backtest_bars_beyond_30_days_never_come_from_the_owners_history(lake):
    from fastapi import HTTPException

    from app.backtest import bar_source
    _owner, other = lake

    async def no_broker():
        raise HTTPException(status_code=409, detail="no broker session")

    async def go():
        async with _session_factory() as session:
            return await bar_source.resolve(session, other.tenant_id, "NIFTY", "NSE", "15min", lookback_days=90, user=other,
                                            now=NOW.astimezone(UTC), broker_fetch=no_broker, stored_beyond_broker=True)
    with pytest.raises(HTTPException):
        _run(go())                                                           # nothing shared: no broker, no bars

    async def with_broker():
        async def broker():
            import pandas as pd
            return pd.DataFrame(), "broker:upstox"
        async with _session_factory() as session:
            return await bar_source.resolve(session, other.tenant_id, "NIFTY", "NSE", "15min", lookback_days=90, user=other,
                                            now=NOW.astimezone(UTC), broker_fetch=broker, stored_beyond_broker=True)
    kept = _run(with_broker())
    assert kept.source == "broker:upstox" and kept.info()["note"] == own_bars.SHARED_OFF_LINE
