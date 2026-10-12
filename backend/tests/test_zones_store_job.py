"""Z7a-2 storage and the nightly market-zones job: tenant settings (the OI banner overrides pattern, all-daily platform
defaults, the Trade intraday preset), as-of snapshots read with the reader's own settings, level memory from one night to
the next, the `market_zones` flag (off by default), tenant isolation, no look-ahead (bars closed AND ingested by the run
time; higher timeframes rebuilt from 1-minute bars per exchange session), the time budget and resume, retention, and the
worker running it as its own task. Synthetic bars only (tests/fixtures/zones), no broker, no network, no NIFTY data."""
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
from app.price_action.zones.settings import ZoneSettings, resolve_settings
from tests import zones_golden as G
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner

KEY, NATIVE, CUT, DAILY, MCX = "NSE:ZTESTA", "NSE:ZTESTB", "NSE:ZTESTC", "NSE:ZTESTD", "MCX:ZTESTM"
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


async def _write(key: str, timeframe: str, bars: pd.DataFrame, lag: timedelta = timedelta(seconds=30)) -> None:
    """Bars as the lake holds them: each ingested `lag` after its END (the tick writer flushes a closed minute within the
    next cycle; HB1's daily file lands hours after the close) - a run never sees a bar before it was ingested."""
    async with _session_factory() as session:
        session.add_all([MdCandleRecord(instrument_key=key, timeframe=timeframe, ts=b.ts, source="test", version=1, open=float(b.open),
                                        high=float(b.high), low=float(b.low), close=float(b.close), volume=0,
                                        ingested_at=pd.Timestamp(b.ts).to_pydatetime() + lag) for b in bars.itertuples()])
        await session.commit()


S15 = _fixture_15m()
M1 = _minutes_from_15m(S15)
DAYS = sorted(S15["timestamp"].dt.date.unique())


# The Trade intraday preset as the platform layer (these 1-minute fixtures have no daily bars); no warm-up on 84 hours.
INTRA = {**store.PRESETS["trade_intraday"], "warmup_bars": 0}


def _night(day_index: int, hour: int = 20) -> datetime:
    """`hour`:00 IST on the given session day, in UTC."""
    return pd.Timestamp(f"{DAYS[day_index]} {hour:02d}:00").tz_localize(IST).tz_convert("UTC").to_pydatetime()


@pytest.fixture(scope="module", autouse=True)
def _lake():
    async def go():
        async with _session_factory() as session:
            await session.execute(delete(MdCandleRecord).where(MdCandleRecord.instrument_key.in_([KEY, NATIVE, CUT, DAILY, MCX])))
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




async def _rows(run):
    async with _session_factory() as session:
        return await store.rows_of(session, run)


async def _runs():
    async with _session_factory() as session:
        return list(await session.scalars(select(MarketZoneRunRecord).order_by(MarketZoneRunRecord.as_of, MarketZoneRunRecord.id)))


def _nightly(now, **kw):
    return _run(job.run_nightly(_session_factory, now=now, **kw))


# --- settings --------------------------------------------------------------------------------------------------------------
def test_platform_defaults_are_all_daily_and_trade_intraday_is_a_tenant_preset(monkeypatch):
    plat = store.platform_settings()
    assert (plat.zone_timeframe, plat.sr_timeframe, plat.gap_timeframe, plat.dynamic_sr_timeframes) == ("1d", "1d", "1d", ["1d"])
    assert (plat.history_days, plat.recent_days_by_tf["1d"], plat.memory_retire_days_by_tf["1d"], plat.gap_min_pct) == (730, 450, 90, 0.30)
    assert plat.memory_families == ["DYNAMIC_SR"] and plat.impulse_mult == 1.5                       # Trade detection values
    _, me = _owner("zones-preset@example.com")
    _run(_call(store.save_settings, me["tenant_id"], KEY, {"impulse_mult": 1.7}, enabled=True, preset="trade_intraday"))
    s = _run(_call(store.settings_for, me["tenant_id"], KEY))
    assert (s.zone_timeframe, s.gap_timeframe, s.dynamic_sr_timeframes, s.history_days, s.impulse_mult) == \
        ("1h", "15m", ["1m", "5m", "15m", "30m", "1h"], 365, 1.7)
    with pytest.raises(ValueError):
        _run(_call(store.save_settings, me["tenant_id"], KEY, {}, preset="nope"))
    monkeypatch.setattr(config, "MARKET_ZONES_DEFAULTS", {"impulse_mult": -1})                       # a bad operator layer
    assert store.platform_settings().zone_timeframe == "1d" and store.platform_settings().impulse_mult == 1.5


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
        _run(_call(store.save_settings, tid, KEY, {"memory_families": ["OB"]}))
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
    assert _run(_call(job.any_enabled)) is False
    rep = _nightly(_night(8))
    assert (rep["runs"], rep["complete"]) == (0, True) and _run(_count(MarketZoneRunRecord)) == 0


def test_nightly_runs_store_as_of_snapshots_with_memory_and_are_idempotent(monkeypatch):
    monkeypatch.setattr(config, "MARKET_ZONES_DEFAULTS", {**INTRA, "memory_families": ["SR", "DYNAMIC_SR"]})
    monkeypatch.setattr(config, "MARKET_ZONES_SYMBOLS", [KEY, "not a key", "NSE:ZNOBARS"])
    _run(_flag(True))
    first = _nightly(_night(8))
    assert (first["runs"], first["no_data"], first["complete"]) == (1, 1, True)                 # ZNOBARS: counted and logged
    again = _nightly(_night(8) + timedelta(minutes=30))
    assert (again["runs"], again["unchanged"]) == (0, 1)                                         # same bars: nothing new
    assert _nightly(_night(9))["runs"] == 1
    runs = _run(_runs())
    rows = [_run(_rows(r)) for r in runs]
    assert [r.scope for r in runs] == ["platform", "platform"] and runs[1].previous_run_id == runs[0].id
    close8 = pd.Timestamp(f"{DAYS[8]} 15:30").tz_localize(IST).tz_convert("UTC")
    assert pd.Timestamp(store._utc(runs[0].as_of)) == close8                        # the close of the last bar used
    assert runs[0].settings_hash == store.settings_hash(store.platform_settings()) and json.loads(runs[0].bars)["1m"] > 1000
    families = {r["zone_type"] for r in rows[0]}
    assert {"BULLISH_OB", "DEMAND_ZONE"} & families and any(t.startswith("DYNAMIC_SR_") for t in families)
    states = {r["state"] for r in rows[0]}
    assert "fresh" in states and states & {"tested", "flipped", "invalidated"}
    assert all(r["tracker"] == {} for r in rows[0] if "SR" not in r["zone_key"].split("|")[0])  # only line levels keep a tracker
    # level memory: a line level of night 8 seen again on night 9 keeps its price, formed time and tracker progress
    lines8 = {r["zone_key"]: r for r in rows[0] if "SR" in r["zone_key"].split("|")[0]}
    lines9 = {r["zone_key"]: r for r in rows[1] if "SR" in r["zone_key"].split("|")[0]}
    carried = set(lines8) & set(lines9)
    assert carried
    for k in carried:
        assert lines9[k]["formed_at"] == lines8[k]["formed_at"] and lines9[k]["touches"] >= lines8[k]["touches"]
        assert pd.Timestamp(lines9[k]["tracker"]["last_bar_at"]) > pd.Timestamp(lines8[k]["tracker"]["last_bar_at"])
        assert lines9[k]["tracker"]["unseen"] == []                                  # the read reached back to its last bar
    # as-of reads: between the two runs -> the first; before any run -> nothing; a naive time is refused
    got = _run(_call(store.zones_as_of, KEY, _night(9, hour=12)))
    assert got.run.id == runs[0].id and len(got.rows) == runs[0].zone_count and got.scope == "platform" and not got.fallback
    assert _run(_call(store.zones_as_of, KEY, _night(7))).run is None
    with pytest.raises(ValueError):
        _run(_call(store.zones_as_of, KEY, datetime(2026, 3, 1, 12, 0)))


def test_reads_match_the_readers_settings_and_a_fallback_is_explicit(monkeypatch):
    monkeypatch.setattr(config, "MARKET_ZONES_DEFAULTS", INTRA)
    _, a = _owner("zones-tenant-a@example.com")
    _, b = _owner("zones-tenant-b@example.com")
    _, c = _owner("zones-tenant-c@example.com")
    _run(_call(store.save_settings, a["tenant_id"], KEY, {"impulse_mult": 1.2}, enabled=True))
    _run(_call(store.save_settings, b["tenant_id"], KEY, {}, enabled=True))
    _run(_call(store.save_settings, c["tenant_id"], KEY, {"impulse_mult": 1.3}, enabled=True))
    _run(_flag(False, tenants=[a["tenant_id"], b["tenant_id"]]))                        # c is not in the rollout
    plan = _run(_call(job.plan))
    assert set(plan) == {(store.tenant_scope(a["tenant_id"]), KEY), ("platform", KEY)}
    assert _nightly(_night(8))["runs"] == 2
    ra = _run(_call(store.zones_as_of, KEY, _night(9), tenant_id=a["tenant_id"]))
    rb = _run(_call(store.zones_as_of, KEY, _night(9), tenant_id=b["tenant_id"]))
    rc = _run(_call(store.zones_as_of, KEY, _night(9), tenant_id=c["tenant_id"]))
    assert ra.scope == store.tenant_scope(a["tenant_id"]) and ra.run.tenant_id == a["tenant_id"] and ra.rows
    assert rb.scope == "platform" and not rb.fallback
    # c's settings differ and it has no snapshot of its own: nothing - never a's, never silently the platform's
    assert rc.run is None and rc.rows == [] and rc.scope is None
    rc2 = _run(_call(store.zones_as_of, KEY, _night(9), tenant_id=c["tenant_id"], allow_platform_fallback=True))
    assert rc2.scope == "platform" and rc2.fallback is True and rc2.settings_hash != rc2.run.settings_hash
    # a tenant who changes settings afterwards stops reading snapshots made with the old ones
    _run(_call(store.save_settings, b["tenant_id"], KEY, {"impulse_mult": 1.9}))
    assert _run(_call(store.zones_as_of, KEY, _night(9), tenant_id=b["tenant_id"])).run is None


def test_no_look_ahead_closed_and_ingested_by_the_run_rebuilt_per_exchange_session():
    s = resolve_settings(INTRA)
    now = _night(7)
    with_future = _run(_call(job.run_instrument, KEY, settings=s, now=now)).run          # the lake also holds days 8..11
    without = _run(_call(job.run_instrument, CUT, settings=s, now=now)).run              # a lake that stops at `now`
    a, b = _run(_rows(with_future)), _run(_rows(without))
    strip = lambda rs: sorted(json.dumps({k: v for k, v in r.items() if k != "zone_key"}, sort_keys=True, default=str) for r in rs)
    assert a and strip(a) == strip(b)
    as_of = store._utc(with_future.as_of)
    assert as_of <= now
    for r in a:
        for when in (r["formed_at"], r["confirmed_at"], r["invalidated_at"], (r["last_event"] or {}).get("time"),
                     (r["first_touch"] or {}).get("time"), r["tracker"].get("last_bar_at")):
            assert when is None or pd.Timestamp(when) < pd.Timestamp(as_of)               # START stamps of bars closed by as_of

    async def frames(key, at, settings=s):
        async with _session_factory() as session:
            return await job.load_frames(session, key, settings, at)
    # mid-session: the 1h and 15m bars still forming are not there
    at = pd.Timestamp(f"{DAYS[9]} 11:07").tz_localize(IST).tz_convert("UTC").to_pydatetime()
    fr = _run(frames(KEY, at))
    last_1h, last_15m = fr.frames["1h"].iloc[-1], fr.frames["15m"].iloc[-1]
    assert last_1h["close_at"] <= pd.Timestamp(at) and last_1h["timestamp"].tz_convert(IST).strftime("%H:%M") == "09:15"
    assert last_15m["timestamp"].tz_convert(IST).strftime("%H:%M") == "10:45" and last_15m["close_at"] <= pd.Timestamp(at)
    assert (fr.dyn["1m"]["close_at"] <= pd.Timestamp(at)).all() and fr.counts["1m"] >= len(fr.dyn["1m"]) > 0
    # ingest lag: the minute that closed 10 s ago is not in the lake yet (ingested 30 s after its end)
    just = pd.Timestamp(f"{DAYS[9]} 11:07:10").tz_localize(IST).tz_convert("UTC").to_pydatetime()
    assert _run(frames(KEY, just)).dyn["1m"]["close_at"].iloc[-1].tz_convert(IST).strftime("%H:%M") == "11:06"


def test_mcx_partial_hour_after_1530_is_not_a_closed_bar():
    """MCX trades to 23:30 IST: at 16:21 the 16:00 hour is still forming (the NSE 15:30 close is no close here)."""
    day = pd.Timestamp(f"{DAYS[3]} 09:00").tz_localize(IST)
    ends = [day + pd.Timedelta(minutes=k + 1) for k in range(7 * 60 + 20)]                         # 09:01 .. 16:20
    m1 = pd.DataFrame({"ts": [t.tz_convert("UTC") for t in ends], "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 0})
    _run(_write(MCX, "1m", m1))
    at = (day + pd.Timedelta(hours=7, minutes=21)).tz_convert("UTC").to_pydatetime()
    hours = _run(_call(job.load_frames, MCX, resolve_settings(INTRA), at)).frames["1h"]
    local = hours["timestamp"].dt.tz_convert(IST).dt.strftime("%H:%M").tolist()
    assert local[0] == "09:00" and local[-1] == "15:00" and (hours["close_at"] <= pd.Timestamp(at)).all()
    sess = job.exchange_session(MCX)
    assert (sess.open.strftime("%H:%M"), sess.close.strftime("%H:%M")) == ("09:00", "23:30")
    # the same minutes on an NSE key would end the day at 15:30 - there the 15:15 hour is complete at the close
    nse = job.bars_to_frame(m1[m1["ts"] <= (day.normalize() + pd.Timedelta(hours=15, minutes=30)).tz_convert("UTC")], "1m",
                            job.exchange_session("NSE:X"))
    rebuilt = job.resample_closed(nse, 60, at, job.exchange_session("NSE:X"))
    assert rebuilt["close_at"].iloc[-1].tz_convert(IST).strftime("%H:%M") == "15:30"


def test_multi_timeframe_frames_rebuilt_from_1m_equal_native_bars():
    at = _night(11)
    s = resolve_settings({**INTRA, "zone_timeframe": "15m", "sr_timeframe": "15m", "dynamic_sr_timeframes": ["15m"]})
    rebuilt = _run(_call(job.load_frames, KEY, s, at)).frames["15m"]
    native = _run(_call(job.load_frames, NATIVE, s, at)).frames["15m"]
    cols = ["timestamp", "open", "high", "low", "close", "close_at"]
    pd.testing.assert_frame_equal(rebuilt[cols].reset_index(drop=True), native[cols].reset_index(drop=True), check_exact=False, rtol=1e-9)
    # the 1h bars are the session-anchored hours of the same 15m bars (09:15-10:15 ... 15:15-15:30)
    hour = _run(_call(job.load_frames, KEY, resolve_settings(INTRA), at)).frames["1h"]
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
    run_a = _run(_call(job.run_instrument, KEY, settings=s, now=at)).run
    run_b = _run(_call(job.run_instrument, NATIVE, settings=s, now=at)).run
    norm = lambda rs: sorted((r["zone_key"], r["state"], r["touches"]) for r in rs)
    assert norm(_run(_rows(run_a))) == norm(_run(_rows(run_b)))


def test_reads_are_bounded_per_timeframe_and_per_frame():
    big = resolve_settings({**INTRA, "history_days": 3650, "recent_days_by_tf": {"1m": 3650}, "max_bars_per_frame": 500})
    plan = job.needed_days(big)
    assert plan["1m"] == (30, 30) and plan["1h"][0] == 730 and plan["15m"][0] == 365
    fr = _run(_call(job.load_frames, KEY, big, _night(11)))
    assert max(fr.counts.values()) <= 500 and len(fr.frames["1h"]) == SESSIONS * 7


def test_time_budget_leaves_the_rest_for_the_next_pass(monkeypatch):
    monkeypatch.setattr(config, "MARKET_ZONES_DEFAULTS", INTRA)
    monkeypatch.setattr(config, "MARKET_ZONES_SYMBOLS", [KEY, NATIVE])
    _run(_flag(True))
    out = _nightly(_night(8), budget_seconds=0)
    assert out["complete"] is False and out["pending"] == 2 and out["runs"] == 0
    done = _nightly(_night(8))
    assert done["complete"] is True and done["runs"] == 2
    assert _nightly(_night(8))["unchanged"] == 2                                     # a resumed pass skips what is stored


def test_daily_bars_of_the_shared_lake_alone_are_the_platform_default():
    """HB0 / HB1 put daily history (timeframe "day", END 15:30 IST, ingested after 19:00) into the shared lake; 1-minute
    data stays tenant-private and is never read here. The platform defaults build zones from the daily bars alone."""
    bn = G.load("bn_daily")
    ends = (bn["timestamp"].dt.normalize() + pd.Timedelta(hours=15, minutes=30)).dt.tz_localize(IST).dt.tz_convert("UTC")
    _run(_write(DAILY, "day", bn.assign(ts=ends), lag=timedelta(hours=4)))                         # HB1: 19:30 IST
    s = store.platform_settings()
    evening = (ends.iloc[-1] + pd.Timedelta(hours=5)).to_pydatetime()
    run = _run(_call(job.run_instrument, DAILY, settings=s, now=evening)).run
    got = _run(_rows(run))
    assert run is not None and store._utc(run.as_of) == ends.iloc[-1].to_pydatetime()
    assert json.loads(run.bars)["1d"] == int((ends > pd.Timestamp(evening) - pd.Timedelta(days=730)).sum())
    assert {r["timeframe"] for r in got} == {"1d"} and {"BULLISH_OB", "DYNAMIC_SR_SUPPORT", "DYNAMIC_SR_RESISTANCE"} & {r["zone_type"] for r in got}
    assert all(pd.Timestamp(r["formed_at"]).tz_convert(IST).strftime("%H:%M") == "09:15" for r in got)
    # warm-up: no stored zone formed in the frame's first 50 bars
    first = pd.Timestamp(ends[ends > pd.Timestamp(evening) - pd.Timedelta(days=730)].iloc[50]) - pd.Timedelta(hours=6, minutes=15)
    assert all(pd.Timestamp(r["formed_at"]) >= first for r in got if "SR" not in r["zone_key"].split("|")[0])
    # before HB1 has ingested the day's file (16:00 IST), that day is not there yet
    afternoon = (ends.iloc[-1] + pd.Timedelta(minutes=30)).to_pydatetime()
    early = _run(_call(job.run_instrument, DAILY, settings=s, now=afternoon)).run
    assert store._utc(early.as_of) == ends.iloc[-2].to_pydatetime()


def test_retention_keeps_nightly_then_weekly_and_drops_idle_tenant_scopes():
    from app.retention import service
    from app.retention.policy import RetentionPolicy
    now = datetime(2026, 10, 11, 15, 0, tzinfo=timezone.utc)

    async def seed():
        async with _session_factory() as session:
            for d in range(0, 800, 1):                                                 # a platform run every night for 800 days
                await store.write_run(session, scope="platform", tenant_id=None, key=KEY, as_of=now - timedelta(days=d),
                                      settings=ZoneSettings(), rows=[], price=None, bars={})
            for d in (40, 45):                                                         # a tenant scope idle for 40 days
                await store.write_run(session, scope="tenant:999", tenant_id=None, key=KEY, as_of=now - timedelta(days=d),
                                      settings=ZoneSettings(), rows=[], price=None, bars={})
            await store.write_run(session, scope="tenant:998", tenant_id=None, key=KEY, as_of=now - timedelta(days=3),
                                  settings=ZoneSettings(), rows=[], price=None, bars={})
            await session.commit()
    _run(seed())
    policy = RetentionPolicy(True, 365, 90, 180, 30, 7, 30, batch_size=100000)

    async def go():
        async with _session_factory() as session:
            preview = await service.preview_retention(session, now, policy)
            report = await service.run_retention(session, now, policy)
            return preview, report
    preview, report = _run(go())
    assert report.deleted["market_zone_runs"] == preview.deleted["market_zone_runs"] > 0
    runs = _run(_runs())
    plat = [store._utc(r.as_of) for r in runs if r.scope == "platform"]
    recent = [t for t in plat if t >= now - timedelta(days=90)]
    older = [t for t in plat if t < now - timedelta(days=90)]
    assert len(recent) == 91                                                            # every night of the last 90 days
    weeks = [(t.isocalendar()[0], t.isocalendar()[1]) for t in older]
    assert len(weeks) == len(set(weeks)) and all(t >= now - timedelta(days=730) for t in older)   # one per ISO week, 2 years
    assert {r.scope for r in runs} == {"platform", "tenant:998"}                      # the idle tenant scope is gone


def test_worker_runs_the_job_as_its_own_task_and_cycles_keep_running(monkeypatch):
    from app.workers import trading_worker as tw
    from tests.test_trading_worker import _no_master_sync
    monkeypatch.setattr(tw, "sync_upstox", _no_master_sync)
    release = asyncio.Event()
    calls = []

    async def slow_nightly(factory, now=None, budget_seconds=None):
        calls.append(now)
        await release.wait()
        return {"complete": len(calls) > 1}

    monkeypatch.setattr(job, "run_nightly", slow_nightly)
    _run(_flag(True))

    async def go():
        worker = tw.TradingWorker(_session_factory, cycle_seconds=60)
        beats = []
        original = worker._heartbeat

        async def beat(session, report, ms):
            beats.append(report)
            await original(session, report, ms)
        worker._heartbeat = beat
        night = pd.Timestamp(f"{DAYS[3]} 20:30").tz_localize(IST).tz_convert("UTC").to_pydatetime()
        first = await asyncio.wait_for(worker.run_cycle(now=night), 60)       # returns although the job is still running
        second = await asyncio.wait_for(worker.run_cycle(now=night + timedelta(minutes=1)), 60)
        running, beats_while_running = not worker._market_zones_task.done(), len(beats)
        release.set()
        await worker._market_zones_task                                        # pass 1 ran out of budget: not done yet
        third = await worker.run_cycle(now=night + timedelta(minutes=2))       # started again (resume)
        await worker._market_zones_task
        fourth = await worker.run_cycle(now=night + timedelta(minutes=3))
        return (first.market_zones_started, second.market_zones_started, running, beats_while_running, third.market_zones_started,
                fourth.market_zones_started, worker._last_market_zones_day, first.errors)
    started1, started2, running, beats, started3, started4, last_day, errors = _run(go())
    assert started1 is True and started2 is False and running and beats == 2 and not errors   # cycles finished while it ran
    assert started3 is True and started4 is False and last_day == DAYS[3] and len(calls) == 2


def test_worker_hook_flag_off_and_failure_paths(monkeypatch):
    from app.workers import trading_worker as tw
    calls = []

    async def failing(factory, now=None, budget_seconds=None):
        calls.append(now)
        raise RuntimeError("boom")

    monkeypatch.setattr(job, "run_nightly", failing)
    night = pd.Timestamp(f"{DAYS[3]} 20:30").tz_localize(IST).tz_convert("UTC").to_pydatetime()

    async def turn(worker, when):
        async with _session_factory() as session:
            ok = await worker._maybe_start_market_zones(session, when)
        if worker._market_zones_task is not None:
            await worker._market_zones_task
        return ok
    worker = tw.TradingWorker(_session_factory, cycle_seconds=60)
    assert _run(turn(worker, night)) is False and calls == [] and worker._last_market_zones_day is None   # flag off: asked again
    _run(_flag(False, tenants=[1]))
    assert _run(turn(worker, night - timedelta(hours=2))) is False                     # 18:30 IST: not due yet
    assert _run(turn(worker, night)) is True and len(calls) == 1
    assert worker._last_market_zones_day is None                                       # failed: the day is not done
    assert _run(turn(worker, night + timedelta(minutes=1))) is True and len(calls) == 2   # retried

    async def stopped():
        w = tw.TradingWorker(_session_factory, cycle_seconds=60)
        w._market_zones_task = asyncio.get_running_loop().create_task(asyncio.sleep(3600))
        w.stop()
        with pytest.raises(asyncio.CancelledError):
            await w._market_zones_task
        return w._market_zones_task.cancelled()
    assert _run(stopped()) is True


def test_the_event_loop_is_never_held_for_long_while_an_instrument_runs():
    """ADR-0004: the job shares the worker's loop - its reads stream and every DataFrame / snapshot is built in a thread.
    A 10 ms ticker runs alongside one intraday run; the worst gap between ticks stays small."""
    s = resolve_settings(INTRA)

    async def go():
        gaps, stop = [], asyncio.Event()

        async def ticker():
            loop = asyncio.get_running_loop()
            last = loop.time()
            while not stop.is_set():
                await asyncio.sleep(0.01)
                now = loop.time()
                gaps.append(now - last)
                last = now
        tick = asyncio.get_running_loop().create_task(ticker())
        async with _session_factory() as session:
            res = await job.run_instrument(session, KEY, settings=s, now=_night(10))
            await session.rollback()
        stop.set()
        await tick
        return res.status, max(gaps)
    status, worst = _run(go())
    assert status == "stored" and worst < 0.5, worst


def test_a_resumed_pass_skips_finished_instruments_without_loading_them(monkeypatch):
    s = resolve_settings(INTRA)
    assert _run(_call(job.run_instrument, KEY, settings=s, now=_night(8))).status == "stored"

    async def must_not_load(*a, **k):
        raise AssertionError("frames loaded for an instrument already done tonight")
    monkeypatch.setattr(job, "load_frames", must_not_load)
    assert _run(_call(job.run_instrument, KEY, settings=s, now=_night(8) + timedelta(hours=1))).status == "exists"
    other = resolve_settings({**INTRA, "impulse_mult": 1.9})                       # other settings: not the same snapshot
    with pytest.raises(AssertionError):
        _run(_call(job.run_instrument, KEY, settings=other, now=_night(8) + timedelta(hours=1)))
    assert _run(_call(job.run_instrument, "NSE:ZNOBARS", settings=s, now=_night(8))).status == "no_data"


def test_the_nightly_start_waits_for_hb1_to_finish_the_day(monkeypatch):
    from app.db.models import HistoryBackfillFileRecord
    from app.market_lake import bhavcopy_backfill
    day = DAYS[4]
    at = lambda h, m=0: pd.Timestamp(f"{day} {h:02d}:{m:02d}").tz_localize(IST).tz_convert("UTC").to_pydatetime()
    ready = lambda when: _run(_call(job.eod_ready, day, when))
    assert ready(at(20, 5)) is True                                                   # HB1 off: it does not feed the lake

    async def hb1(on, status=None):
        async with _session_factory() as session:
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": {bhavcopy_backfill.FLAG: {"on": on, "tenants": []}}}, None)
            await session.execute(delete(HistoryBackfillFileRecord).where(HistoryBackfillFileRecord.trade_date == day))
            if status:
                session.add(HistoryBackfillFileRecord(segment="cm", trade_date=day, status=status))
            await session.commit()
    _run(hb1(True))
    assert ready(at(20, 5)) is False                                                  # not attempted yet
    _run(hb1(True, "running"))
    assert ready(at(21, 30)) is False
    _run(hb1(True, "done"))
    assert ready(at(20, 5)) is True
    _run(hb1(True, "running"))
    assert ready(at(23, 1)) is True                                                   # a stuck archive never costs the night
    # a late start hour must not push the wait past midnight: from 23:30 IST it runs whatever the hour setting
    monkeypatch.setattr(config, "MARKET_ZONES_HOUR_IST", 22)
    assert ready(at(23, 29)) is False and ready(at(23, 30)) is True
    monkeypatch.setattr(config, "MARKET_ZONES_HOUR_IST", 23)
    assert ready(at(23, 10)) is False and ready(at(23, 45)) is True
    # a weekend / exchange holiday: nothing to wait for, even with no cash-file row at all
    _run(hb1(True))
    saturday = next(d for d in (day + timedelta(days=k) for k in range(1, 8)) if d.weekday() == 5)
    sat_at = pd.Timestamp(f"{saturday} 20:05").tz_localize(IST).tz_convert("UTC").to_pydatetime()
    assert _run(_call(job.eod_ready, saturday, sat_at)) is True and ready(at(20, 5)) is False
    _run(hb1(False))


def test_reads_survive_a_settings_change_and_say_so(monkeypatch):
    monkeypatch.setattr(config, "MARKET_ZONES_DEFAULTS", INTRA)
    monkeypatch.setattr(config, "MARKET_ZONES_SYMBOLS", [KEY])
    _run(_flag(True))
    assert _nightly(_night(8))["runs"] == 1
    before = _run(_call(store.zones_as_of, KEY, _night(9)))
    assert before.run is not None and before.settings_match is True
    monkeypatch.setattr(config, "MARKET_ZONES_DEFAULTS", {**INTRA, "impulse_mult": 1.6})      # the operator changes a default
    after = _run(_call(store.zones_as_of, KEY, _night(9)))
    assert after.run.id == before.run.id and after.settings_match is False and after.scope == "platform"


def test_retention_never_drops_a_scope_that_is_still_planned(monkeypatch):
    monkeypatch.setattr(config, "MARKET_ZONES_DEFAULTS", INTRA)
    _, me = _owner("zones-retention-planned@example.com")
    tid = me["tenant_id"]
    _run(_call(store.save_settings, tid, KEY, {"impulse_mult": 1.2}, enabled=True))
    _run(_flag(False, tenants=[tid]))
    now = datetime(2026, 10, 11, 15, 0, tzinfo=timezone.utc)

    async def seed():
        async with _session_factory() as session:
            for scope, tenant in ((store.tenant_scope(tid), tid), ("tenant:999", None)):
                await store.write_run(session, scope=scope, tenant_id=tenant, key=KEY, as_of=now - timedelta(days=45),
                                      settings=ZoneSettings(), rows=[], price=None, bars={})
            await session.commit()
    _run(seed())
    ids = _run(_call(store.retention_run_ids, now, daily_days=90, weekly_days=730, tenant_idle_days=30))
    runs = {r.id: r.scope for r in _run(_runs())}
    assert {runs[i] for i in ids} == {"tenant:999"}                                   # the followed tenant's scope stays
