"""Z7a-2 storage and the nightly market-zones job: tenant settings (the OI banner overrides pattern), as-of snapshots in
the database, level memory from one night to the next, the `market_zones` flag (off by default), tenant isolation and no
look-ahead when higher timeframes are rebuilt from 1-minute lake bars. Synthetic bars only (tests/fixtures/zones), no
broker, no network, no NIFTY data."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
from sqlalchemy import delete, func, select

from app.core import config
from app.db.models import MarketZoneRecord, MarketZoneRunRecord, MarketZoneSettingRecord, MdCandleRecord
from app.platform import controls
from app.price_action.zones import job, store
from app.price_action.zones.settings import ZoneSettings
from tests import zones_golden as G
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner

KEY, NATIVE, CUT = "NSE:ZTESTA", "NSE:ZTESTB", "NSE:ZTESTC"
IST = "Asia/Kolkata"
SESSIONS = 12


def _run(coro):
    return asyncio.run(coro)


def _minutes_from_15m(df15: pd.DataFrame) -> pd.DataFrame:
    """Each 15m fixture bar as 15 one-minute bars (open -> low/high -> high/low -> close), END-labelled in UTC."""
    rows = []
    for r in df15.itertuples():
        first, second = (r.low, r.high) if r.close >= r.open else (r.high, r.low)
        pts = [r.open + (first - r.open) * k / 5 for k in range(1, 6)] + [first + (second - first) * k / 5 for k in range(1, 6)] + \
              [second + (r.close - second) * k / 5 for k in range(1, 6)]
        prev = r.open
        start = pd.Timestamp(r.timestamp).tz_localize(IST)
        for i, p in enumerate(pts):
            rows.append({"ts": (start + pd.Timedelta(minutes=i + 1)).tz_convert("UTC").to_pydatetime(), "open": prev, "high": max(prev, p),
                         "low": min(prev, p), "close": p})
            prev = p
    return pd.DataFrame(rows)


def _fixture_15m() -> pd.DataFrame:
    s15 = G.load("session_15m")
    days = sorted(s15["timestamp"].dt.date.unique())[:SESSIONS]
    return s15[s15["timestamp"].dt.date.isin(days)].reset_index(drop=True)


async def _write(key: str, timeframe: str, bars: pd.DataFrame) -> None:
    async with _session_factory() as session:
        session.add_all([MdCandleRecord(instrument_key=key, timeframe=timeframe, ts=b.ts, source="test", version=1, open=float(b.open),
                                        high=float(b.high), low=float(b.low), close=float(b.close), volume=0,
                                        ingested_at=datetime(2000, 1, 1, tzinfo=timezone.utc)) for b in bars.itertuples()])
        await session.commit()


S15 = _fixture_15m()
M1 = _minutes_from_15m(S15)
DAYS = sorted(S15["timestamp"].dt.date.unique())


def _night(day_index: int, hour: int = 16) -> datetime:
    """`hour`:00 IST on the given session day, in UTC."""
    return pd.Timestamp(f"{DAYS[day_index]} {hour:02d}:00").tz_localize(IST).tz_convert("UTC").to_pydatetime()


@pytest.fixture(scope="module", autouse=True)
def _lake():
    async def go():
        async with _session_factory() as session:
            await session.execute(delete(MdCandleRecord).where(MdCandleRecord.instrument_key.in_([KEY, NATIVE, CUT])))
            await session.commit()
        await _write(KEY, "1m", M1)
        native = S15.assign(ts=(S15["timestamp"].dt.tz_localize(IST) + pd.Timedelta(minutes=15)).dt.tz_convert("UTC"))
        await _write(NATIVE, "15m", native)
        cut_at = _night(7)
        await _write(CUT, "1m", M1[M1["ts"] <= cut_at])
    _run(go())
    yield


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(config, "MARKET_ZONES_SYMBOLS", [])
    monkeypatch.setattr(config, "MARKET_ZONES_DEFAULTS", {})

    async def go():
        async with _session_factory() as session:
            for model in (MarketZoneRecord, MarketZoneRunRecord, MarketZoneSettingRecord):
                await session.execute(delete(model))
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": {}}, None)
            await session.commit()
    _run(go())
    yield


async def _flag(on: bool, tenants=()):
    async with _session_factory() as session:
        await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": {job.FLAG: {"on": on, "tenants": list(tenants)}}}, None)
        await session.commit()


async def _call(fn, *args, **kwargs):
    async with _session_factory() as session:
        result = await fn(session, *args, **kwargs)
        await session.commit()
        return result


async def _count(model):
    async with _session_factory() as session:
        return int(await session.scalar(select(func.count()).select_from(model)))


# --- settings --------------------------------------------------------------------------------------------------------------
def test_settings_layer_platform_tenant_default_and_instrument(monkeypatch):
    _, me = _owner("zones-settings@example.com")
    tid = me["tenant_id"]
    monkeypatch.setattr(config, "MARKET_ZONES_DEFAULTS", {"impulse_mult": 2.0})
    _run(_call(store.save_settings, tid, "*", {"avg_window": 30, "memory_retire_days_by_tf": {"5m": 9}}))
    _run(_call(store.save_settings, tid, "nse:ztesta", {"impulse_mult": 1.8}, enabled=True))
    s = _run(_call(store.settings_for, tid, KEY))
    assert (s.impulse_mult, s.avg_window, s.memory_retire_days_by_tf["5m"], s.memory_retire_days_by_tf["1m"]) == (1.8, 30, 9, 2)
    other = _run(_call(store.settings_for, tid, "NSE:OTHER"))
    assert (other.impulse_mult, other.avg_window) == (2.0, 30)
    assert _run(_call(store.followed)) == [(tid, KEY)]
    with pytest.raises(ValueError):
        _run(_call(store.save_settings, tid, KEY, {"impulse": 2}))                      # unknown key: nothing stored
    with pytest.raises(ValueError):
        _run(_call(store.save_settings, tid, KEY, {"avg_min_periods": 50}))             # inconsistent with the "*" row (avg 30)
    with pytest.raises(ValueError):
        _run(_call(store.save_settings, tid, "*", None, enabled=True))
    with pytest.raises(ValueError):
        store.normalise_key("BAD KEY;DROP")

    async def corrupt():
        async with _session_factory() as session:
            row = await session.scalar(select(MarketZoneSettingRecord).where(MarketZoneSettingRecord.instrument_key == KEY))
            row.overrides = json.dumps({"impulse_mult": -5})
            await session.commit()
    _run(corrupt())
    fallback = _run(_call(store.settings_for, tid, KEY))                                # a bad stored row never fails the job
    assert fallback.impulse_mult == 2.0


# --- the job ---------------------------------------------------------------------------------------------------------------
def test_flag_is_off_by_default_and_nothing_is_computed(monkeypatch):
    assert job.FLAG in controls.FEATURE_FLAGS and job.FLAG in controls.DEFAULT_OFF_FLAGS
    _, me = _owner("zones-off@example.com")
    _run(_call(store.save_settings, me["tenant_id"], KEY, {}, enabled=True))
    monkeypatch.setattr(config, "MARKET_ZONES_SYMBOLS", [KEY])
    assert _run(_call(job.run_all, _night(8))) == {"runs": 0, "unchanged": 0, "failed": 0}
    assert _run(_count(MarketZoneRunRecord)) == 0


def test_nightly_runs_store_as_of_snapshots_with_memory_and_are_idempotent(monkeypatch):
    monkeypatch.setattr(config, "MARKET_ZONES_SYMBOLS", [KEY, "not a key"])
    _run(_flag(True))
    first = _run(_call(job.run_all, _night(8)))
    assert first == {"runs": 1, "unchanged": 0, "failed": 0}
    assert _run(_call(job.run_all, _night(8) + timedelta(minutes=30))) == {"runs": 0, "unchanged": 1, "failed": 0}   # same bars
    assert _run(_call(job.run_all, _night(9))) == {"runs": 1, "unchanged": 0, "failed": 0}

    async def read():
        async with _session_factory() as session:
            runs = list(await session.scalars(select(MarketZoneRunRecord).order_by(MarketZoneRunRecord.as_of)))
            return runs, [await store.rows_of(session, r) for r in runs]
    runs, rows = _run(read())
    assert [r.scope for r in runs] == ["platform", "platform"] and runs[1].previous_run_id == runs[0].id
    close8 = pd.Timestamp(f"{DAYS[8]} 15:30").tz_localize(IST).tz_convert("UTC")
    assert pd.Timestamp(store._utc(runs[0].as_of)) == close8                        # the close of the last bar used
    assert runs[0].settings_hash == store.settings_hash(ZoneSettings()) and json.loads(runs[0].bars)["1m"] > 1000
    families = {r["zone_type"] for r in rows[0]}
    assert {"BULLISH_OB", "DEMAND_ZONE"} & families and any(t.startswith("DYNAMIC_SR_") for t in families)
    states = {r["state"] for r in rows[0]}
    assert "fresh" in states and states & {"tested", "flipped", "invalidated"}
    # level memory: a line level of night 8 seen again on night 9 keeps its price, formed time and tracker progress
    lines8 = {r["zone_key"]: r for r in rows[0] if "SR" in r["zone_key"].split("|")[0]}
    lines9 = {r["zone_key"]: r for r in rows[1] if "SR" in r["zone_key"].split("|")[0]}
    carried = set(lines8) & set(lines9)
    assert carried
    for k in carried:
        assert lines9[k]["formed_at"] == lines8[k]["formed_at"] and lines9[k]["touches"] >= lines8[k]["touches"]
        assert pd.Timestamp(lines9[k]["tracker"]["last_bar_at"]) > pd.Timestamp(lines8[k]["tracker"]["last_bar_at"])
    # as-of reads: between the two runs -> the first; before any run -> nothing
    mid = _night(9, hour=12)
    run, got = _run(_call(store.zones_as_of, KEY, mid))
    assert run.id == runs[0].id and len(got) == runs[0].zone_count
    assert _run(_call(store.zones_as_of, KEY, _night(7))) == (None, [])


def test_a_tenant_with_its_own_settings_gets_its_own_scope_and_never_sees_another_tenants():
    _, a = _owner("zones-tenant-a@example.com")
    _, b = _owner("zones-tenant-b@example.com")
    _, c = _owner("zones-tenant-c@example.com")
    _run(_call(store.save_settings, a["tenant_id"], KEY, {"impulse_mult": 1.2}, enabled=True))
    _run(_call(store.save_settings, b["tenant_id"], KEY, {}, enabled=True))
    _run(_call(store.save_settings, c["tenant_id"], KEY, {"impulse_mult": 1.3}, enabled=True))
    _run(_flag(False, tenants=[a["tenant_id"], b["tenant_id"]]))                        # c is not in the rollout
    plan = _run(_call(job.plan))
    assert set(plan) == {(store.tenant_scope(a["tenant_id"]), KEY), ("platform", KEY)}
    assert _run(_call(job.run_all, _night(8)))["runs"] == 2
    run_a, rows_a = _run(_call(store.zones_as_of, KEY, _night(9), tenant_id=a["tenant_id"]))
    run_b, _ = _run(_call(store.zones_as_of, KEY, _night(9), tenant_id=b["tenant_id"]))
    run_c, _ = _run(_call(store.zones_as_of, KEY, _night(9), tenant_id=c["tenant_id"]))
    assert run_a.scope == store.tenant_scope(a["tenant_id"]) and run_a.tenant_id == a["tenant_id"]
    assert run_b.scope == "platform" and run_c.scope == "platform"                     # never tenant a's private snapshot
    assert run_a.settings_hash != run_b.settings_hash and rows_a


def test_no_look_ahead_rebuilt_higher_timeframes_use_only_bars_closed_by_the_run():
    _run(_flag(True))
    now = _night(7)
    with_future = _run(_call(job.run_instrument, KEY, now=now))                        # the lake also holds days 8..11
    without = _run(_call(job.run_instrument, CUT, now=now))                            # a lake that stops at `now`

    async def rows(run):
        async with _session_factory() as session:
            return await store.rows_of(session, run)
    a, b = _run(rows(with_future)), _run(rows(without))
    strip = lambda rs: sorted(json.dumps({k: v for k, v in r.items()}, sort_keys=True, default=str) for r in rs)
    assert a and strip(a) == strip(b)
    as_of = store._utc(with_future.as_of)
    assert as_of <= now
    for r in a:
        for when in (r["formed_at"], r["confirmed_at"], r["invalidated_at"], (r["last_event"] or {}).get("time"),
                     (r["first_touch"] or {}).get("time"), r["tracker"].get("last_bar_at")):
            assert when is None or pd.Timestamp(when) < pd.Timestamp(as_of)               # START stamps of bars closed by as_of

    # mid-session: the 1h bar still forming is not there, nor the 15m bar still forming
    async def frames(at):
        async with _session_factory() as session:
            return await job.load_frames(session, KEY, ZoneSettings(), at)
    at = pd.Timestamp(f"{DAYS[9]} 11:07").tz_localize(IST).tz_convert("UTC").to_pydatetime()
    fr, dyn, counts = _run(frames(at))
    last_1h, last_15m = fr["1h"].iloc[-1], fr["15m"].iloc[-1]
    assert last_1h["close_at"] <= pd.Timestamp(at) and last_1h["timestamp"].tz_convert(IST).strftime("%H:%M") == "09:15"
    assert last_15m["timestamp"].tz_convert(IST).strftime("%H:%M") == "10:45" and last_15m["close_at"] <= pd.Timestamp(at)
    assert (dyn["1m"]["close_at"] <= pd.Timestamp(at)).all() and counts["1m"] >= len(dyn["1m"]) > 0


def test_multi_timeframe_frames_rebuilt_from_1m_equal_native_bars():
    async def load(key, tf, at):
        async with _session_factory() as session:
            return await job.load_frame(session, key, tf, at - timedelta(days=60), at, ZoneSettings())
    at = _night(11)
    rebuilt, native = _run(load(KEY, "15m", at)), _run(load(NATIVE, "15m", at))
    cols = ["timestamp", "open", "high", "low", "close", "close_at"]
    pd.testing.assert_frame_equal(rebuilt[cols].reset_index(drop=True), native[cols].reset_index(drop=True), check_exact=False, rtol=1e-9)
    # the 1h bars are the session-anchored hours of the same 15m bars (09:15-10:15 ... 15:15-15:30)
    hour = _run(load(KEY, "1h", at))
    q = native.assign(local=native["timestamp"].dt.tz_convert(IST))
    anchor = q["local"].dt.normalize() + pd.Timedelta(hours=9, minutes=15)
    q["bucket"] = anchor + ((q["local"] - anchor) // pd.Timedelta(hours=1)) * pd.Timedelta(hours=1)
    agg = q.groupby("bucket").agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last")).reset_index()
    assert len(agg) == len(hour) == SESSIONS * 7
    assert (hour["timestamp"].dt.tz_convert(IST).to_numpy() == agg["bucket"].to_numpy()).all()
    for col in ("open", "high", "low", "close"):
        assert (hour[col] - agg[col]).abs().max() < 1e-6
    assert (hour["close_at"].dt.tz_convert(IST).dt.strftime("%H:%M").isin(["10:15", "11:15", "12:15", "13:15", "14:15", "15:15", "15:30"])).all()
    # same zones from either source of 15m bars
    s = ZoneSettings(zone_timeframe="15m", sr_timeframe="15m", dynamic_sr_timeframes=["15m"])
    _run(_flag(True))
    run_a = _run(_call(job.run_instrument, KEY, settings=s, now=at))
    run_b = _run(_call(job.run_instrument, NATIVE, settings=s, now=at))

    async def rows(run):
        async with _session_factory() as session:
            return await store.rows_of(session, run)
    norm = lambda rs: sorted((r["zone_key"], r["state"], r["touches"]) for r in rs)
    assert norm(_run(rows(run_a))) == norm(_run(rows(run_b)))


def test_worker_schedule_once_per_ist_day_after_the_hour(monkeypatch):
    monkeypatch.setattr(config, "MARKET_ZONES_HOUR_IST", 16)
    morning, evening = _night(3, hour=10), _night(3, hour=17)
    assert not job.due(morning, None) and job.due(evening, None)
    assert not job.due(evening, DAYS[3])
