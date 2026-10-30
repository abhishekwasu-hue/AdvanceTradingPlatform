"""M55 T4 (owner order): the history owner's index intraday rows (DATA-H) read by every organisation.

Covered: a member of ANOTHER organisation reads NIFTY's stored 1-minute rows (15M / 1H built from them) older than the
broker's 30 days while `byob_data_tier` is off, and nothing of it while the flag is on for his organisation or the
owner's; the shared scope never reads the owner's Daily rows nor a non-index key, and never equals the owner's own scope
(caches keyed by scope cannot mix them); the reader's own broker bars still win in the candles route; the zones job
builds 15M / 1H zones for another organisation's opt-in settings (INTRADAY_PRESET) from them, and the platform scope
stays on the shared lake; backtest bars beyond 30 days come from the stored history only while the flag is off.
No network: rows are written straight into the owner's private store; the clock is fixed (2023, no holiday calendar).
"""
import json
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


def test_another_organisation_reads_index_15m_and_1h_beyond_30_days_only_while_byob_is_off(lake):
    from app.charts import price_action_layer as pal
    owner, other = lake
    lo, hi = (NOW - timedelta(days=90)).astimezone(UTC), NOW.astimezone(UTC)

    async def reads():
        async with _session_factory() as session:
            scope = await own_bars.scope_for(session, other, KEY)
            q15, src15 = await pal.load_frame(session, KEY, "15m", lo, hi, private=scope)
            h1, src1h = await pal.load_frame(session, KEY, "1h", lo, hi, private=scope)
            day = await own_bars.fetch_rows(session, scope, KEY, "day", lo, hi)
            close = await own_bars.official_close(session, KEY, scope)
            return scope, q15, src15, h1, src1h, day, close
    scope, q15, src15, h1, src1h, day, close = _run(reads())
    assert own_bars.is_shared(scope) and tuple(scope[:2]) == owner.scope
    assert scope != owner.scope and hash(scope) != hash(owner.scope)       # never the owner's own cache entry
    assert src15 == "shared:1m" and src1h == "shared:1m"
    assert len(q15) == 75 and len(h1) == 21                                 # three sessions: 25 x 15M, 7 x 1H each
    first = min(q15["timestamp"]).to_pydatetime()
    assert first.astimezone(IST).date() == OLD and NOW - first > timedelta(days=30)
    assert day == [] and close is None                                       # never the owner's Daily rows

    _set_flag(BYOB, other.tenant_id, on=True)                                # the reader's organisation brings its own broker

    async def refused():
        async with _session_factory() as session:
            scope = await own_bars.scope_for(session, other, KEY)
            frame, src = await pal.load_frame(session, KEY, "15m", lo, hi, private=scope)
            return scope, frame, src
    scope, frame, src = _run(refused())
    assert scope is None and len(frame) == 0 and src is None

    _set_flag(BYOB, other.tenant_id, on=False)
    _set_flag(BYOB, owner.tenant_id, on=True)                                # or the owner's organisation does

    async def refused_by_owner():
        async with _session_factory() as session:
            return await own_bars.scope_for(session, other, KEY)
    assert _run(refused_by_owner()) is None


def test_the_shared_scope_reads_index_keys_only_and_the_owner_keeps_his_own(lake):
    owner, other = lake
    mate = _teammate(owner.user, f"m55t4-mate-{uuid.uuid4().hex[:8]}@example.com")

    async def go():
        async with _session_factory() as session:
            shared = await own_bars.scope_for(session, other, KEY)
            mine = await own_bars.scope_for(session, owner.user, KEY)
            mates = await own_bars.scope_for(session, mate, KEY)
            stock = await own_bars.scope_for(session, other, "NSE:RELIANCE")
            with pytest.raises(PermissionError):
                await own_bars.fetch_rows(session, shared, "NSE:RELIANCE", "1m", datetime(2023, 1, 1, tzinfo=UTC),
                                          NOW.astimezone(UTC))
            return shared, mine, mates, stock
    shared, mine, mates, stock = _run(go())
    assert mine == owner.scope and not own_bars.is_shared(mine)             # the owner reads his own rows, Daily too
    assert own_bars.is_shared(mates) and own_bars.is_shared(shared)
    assert stock is None


def test_the_readers_own_broker_bars_win_in_the_candles_route(lake):
    from app.market_data import candles_routes as cr
    from app.market_data.service import merge_bars
    _owner, other = lake
    start = datetime.combine(FRI, time(9, 15), tzinfo=IST)
    broker = [OHLCVBar(timestamp=start + timedelta(minutes=i), open=1.0, high=2.0, low=0.5, close=1.5, volume=7) for i in range(375)]

    async def go(bars):
        async with _session_factory() as session:
            return await cr._stored_older(session, other, KEY, bars, 90, None, FRI, NOW.astimezone(UTC))
    older = _run(go(broker))
    assert older and all(b.timestamp < broker[0].timestamp for b in older)
    assert {b.timestamp.astimezone(IST).date() for b in older} == {OLD, THU}
    merged = merge_bars(older, broker)
    shaped = cr._shape(merged, "15min")
    assert all(b.timestamp.astimezone(IST).minute in (0, 15, 30, 45) for b in shaped)
    assert {b.timestamp.astimezone(IST).time() for b in shaped if b.timestamp.astimezone(IST).date() == OLD} >= {time(9, 15), time(15, 15)}
    assert [b for b in merged if b.timestamp >= broker[0].timestamp] == broker   # his broker's bars, untouched
    assert cr._index_keys(["NIFTY", "RELIANCE"]) == {"NIFTY": KEY}

    _set_flag(BYOB, other.tenant_id, on=True)
    assert _run(go(broker)) == []                                            # byob on: today's behaviour (30 days)


def test_zones_on_15m_and_1h_for_another_organisations_opt_in(lake):
    from app.price_action.zones import job
    from app.price_action.zones.settings import resolve_settings
    from app.price_action.zones.store import PLATFORM_SCOPE, PRESETS, platform_layers, tenant_scope
    _owner, other = lake
    intraday = resolve_settings(*platform_layers(), PRESETS["trade_intraday"])
    assert intraday.zone_timeframe == "1h" and intraday.gap_timeframe == "15m"

    async def run(scope, tenant_id):
        async with _session_factory() as session:
            result = await job.run_instrument(session, KEY, settings=intraday, scope=scope, tenant_id=tenant_id, now=NOW.astimezone(UTC))
            await session.commit()
            return result.status, (json.loads(result.run.bars or "{}") if result.run is not None else None)
    status, bars = _run(run(tenant_scope(other.tenant_id), other.tenant_id))
    assert status == "stored" and bars.get("1h") and bars.get("15m"), bars
    assert _run(run(PLATFORM_SCOPE, None))[0] == "no_data"                  # the platform scope stays on the shared lake

    fresh = _user(f"m55t4-byob-{uuid.uuid4().hex[:8]}@example.com")
    _set_flag(BYOB, fresh.tenant_id, on=True)
    try:
        assert _run(run(tenant_scope(fresh.tenant_id), fresh.tenant_id))[0] == "no_data"
    finally:
        _set_flag(BYOB, fresh.tenant_id, on=False)


def test_backtest_bars_beyond_30_days_from_the_stored_index_history_only_while_byob_is_off(lake):
    from fastapi import HTTPException

    from app.backtest import bar_source
    _owner, other = lake
    assert bar_source.stored_intraday_tf("15min") == "15m" and bar_source.stored_intraday_tf("60min") == "1h"
    assert bar_source.stored_intraday_tf("5min") is None and bar_source.stored_intraday_key("RELIANCE", "NSE") is None

    async def no_broker():
        raise HTTPException(status_code=409, detail="no broker session")

    async def go():
        async with _session_factory() as session:
            return await bar_source.resolve(session, other.tenant_id, "NIFTY", "NSE", "15min", lookback_days=90, user=other,
                                            now=NOW.astimezone(UTC), broker_fetch=no_broker, stored_beyond_broker=True)
    got = _run(go())
    assert got.source == bar_source.STORED_INTRADAY_SOURCE and got.kind == "stored" and len(got.frame) == 75
    assert got.frame.index[0].tz_convert(IST).date() == OLD

    async def with_broker(**kw):
        async def broker():
            return got.frame.tail(5), "broker:upstox"
        async with _session_factory() as session:
            return await bar_source.resolve(session, other.tenant_id, "NIFTY", "NSE", "15min", lookback_days=90, user=other,
                                            now=NOW.astimezone(UTC), broker_fetch=broker, **kw)
    assert _run(with_broker()).source == "broker:upstox"                    # other callers: the broker read, as before
    _set_flag(BYOB, other.tenant_id, on=True)
    with pytest.raises(HTTPException):
        _run(go())                                                           # nothing shared: the broker's answer stands
    kept = _run(with_broker(stored_beyond_broker=True))
    assert kept.source == "broker:upstox" and kept.info()["note"] == own_bars.SHARED_OFF_LINE
