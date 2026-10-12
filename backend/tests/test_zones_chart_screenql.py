"""Z7a-3: the market-zones chart layer and the ScreenQL zone functions.

* chart.py (pure): at most six bands by role and proximity, the strongest of each overlapping cluster, the labels
  ("demand 512.30 (fresh)", touch count), the strength score, the nearest zone; the browser port is checked against the
  same cases (tests/fixtures/zones/chart_bands_cases.json, frontend marketZones.test.ts).
* read.py: zones as of a time per tenant scope (never another tenant's, never a later snapshot); the bar-by-bar timeline
  equals `zones_as_of` at every bar close.
* ScreenQL: NearZone / ZoneRole / ZoneState / ZoneStrength - text vs engine parity, text vs builder tree, no look-ahead
  (appending and mutating later bars and snapshots leaves earlier values unchanged), flag `market_zones` off by default
  (hidden and refused; alert rules refused, paused at bar close, never intrabar).
* the chart layer API (/api/charts/layers/market-zones): flag, as-of, tenant scope, input checks.
Synthetic zones and bars only - no broker, no network, no NIFTY data.
"""
import asyncio
import json
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import delete, select

from app.core import config
from app.db.models import MarketZoneRecord, MarketZoneRunRecord, MarketZoneSettingRecord
from app.price_action.zones import chart, read, store
from app.screener import compile_screen, nodes, parse, validate
from app.screener.runtime import ScreenRuntimeError, SymbolData, bar_close, evaluate, run_screen
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner
from tests.zones_chart_cases import FRONTEND_PATH as CASES_FRONTEND_PATH, PATH as CASES_PATH, build as build_cases

KEY, OTHER = "NSE:ZCHARTA", "NSE:ZCHARTB"
IST = "Asia/Kolkata"
D0, D1, D2, D3 = date(2026, 3, 9), date(2026, 3, 10), date(2026, 3, 11), date(2026, 3, 12)


def _run(coro):
    return asyncio.run(coro)


def ist(d: date, hh: int, mm: int) -> datetime:
    return pd.Timestamp(datetime(d.year, d.month, d.day, hh, mm)).tz_localize(IST).tz_convert("UTC").to_pydatetime()


def _zrow(key, zone_type, low, high, state="fresh", touches=0, tf="1h", strength=None, remembered=False, formed=None):
    formed = formed or ist(D0, 10, 15)
    return {"zone_key": key, "zone_type": zone_type, "timeframe": tf, "zone_low": low, "zone_high": high, "strength": strength,
            "role": None, "origin_role": "SUPPORT", "state": state, "formed_at": formed, "confirmed_at": formed, "first_touch": None,
            "touches": touches, "flips": 0, "last_event": None, "structure_level": None,
            "invalidated_at": formed if state == "invalidated" else None, "remembered": remembered, "tracker": {}}


def _set(**flags):
    async def go():
        from app.platform import controls
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            stored = dict(current.get("flags") or {})
            for name, on in flags.items():
                stored[name] = {"on": on, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": stored}, None)
            await session.commit()
    _run(go())


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(config, "MARKET_ZONES_DEFAULTS", {})

    async def go():
        async with _session_factory() as session:
            for model in (MarketZoneRecord, MarketZoneRunRecord, MarketZoneSettingRecord):
                await session.execute(delete(model))
            await session.commit()
    _run(go())
    yield
    _set(market_zones=False, screener_v2=False, screener_intrabar=False)


async def _write_run(key, as_of, rows, *, tenant_id=None, settings=None, price=100.0):
    async with _session_factory() as session:
        s = settings or store.platform_settings()
        scope = store.PLATFORM_SCOPE if tenant_id is None else store.tenant_scope(tenant_id)
        run = await store.write_run(session, scope=scope, tenant_id=tenant_id, key=key, as_of=as_of, settings=s, rows=rows, price=price,
                                    bars={"1h": 10})
        await session.commit()
        return run.id


async def _tenant_settings(tenant_id, key=KEY):
    """A tenant with its own settings (so its own scope is the one it reads)."""
    async with _session_factory() as session:
        await store.save_settings(session, tenant_id, store.DEFAULT_KEY, {"impulse_mult": 2.0})
        await session.commit()
        return await store.settings_for(session, tenant_id, key)


SNAP0 = [_zrow("d0", "DEMAND_ZONE", 98.0, 99.0), _zrow("s0", "SUPPLY_ZONE", 104.0, 105.0, "tested", 2)]
SNAP1 = [_zrow("d1", "DEMAND_ZONE", 99.5, 100.0, tf="1d"), _zrow("s1", "SUPPLY_ZONE", 110.0, 111.0),
         _zrow("l1", "SUPPORT", 99.0, 99.0, "tested", 1, strength=4)]
SNAP2 = [_zrow("d1", "DEMAND_ZONE", 99.5, 100.0, "invalidated", 1, tf="1d"), _zrow("s2", "SUPPLY_ZONE", 100.2, 100.4, "flipped", 3, tf="15m")]


def _snapshots(key=KEY, tenant_id=None, settings=None, with_last=True):
    async def go():
        ids = [await _write_run(key, ist(D0, 15, 30), SNAP0, tenant_id=tenant_id, settings=settings),
               await _write_run(key, ist(D1, 15, 30), SNAP1, tenant_id=tenant_id, settings=settings)]
        if with_last:
            ids.append(await _write_run(key, ist(D2, 15, 30), SNAP2, tenant_id=tenant_id, settings=settings))
        return ids
    return _run(go())


def _bars(days, closes_per_day=None, minutes=5) -> pd.DataFrame:
    """5-minute bars over the given sessions (09:15 - 15:30 IST), closes swinging around 100."""
    idx, closes = [], []
    k = 0
    for d in days:
        t = ist(d, 9, 15)
        while t < ist(d, 15, 30):
            idx.append(pd.Timestamp(t))
            closes.append(100.0 + 0.9 * np.sin(k / 7.0))
            k += 1
            t += timedelta(minutes=minutes)
    c = np.array(closes)
    return pd.DataFrame({"open": c, "high": c + 0.05, "low": c - 0.05, "close": c, "volume": 1000.0}, index=pd.DatetimeIndex(idx))


def _timeline(key, tenant_id, start, end):
    async def go():
        async with _session_factory() as session:
            return await read.timeline(session, key, tenant_id, start, end)
    return _run(go())


def _read(key, at, tenant_id):
    async def go():
        async with _session_factory() as session:
            return await read.read_as_of(session, key, at, tenant_id)
    return _run(go())


def _series(text, frame, timeline, tf="5m", symbol="ZCHARTA"):
    """The series of a ScreenQL expression (a factor or classifier too - a screen itself must be a condition)."""
    ast = parse(text)
    assert not [p for p in validate(ast, base_tf=tf).problems if "must be a condition" not in p.message]
    return evaluate(ast, SymbolData(symbol, {tf: frame}, zones=timeline), base_tf=tf)


# ------------------------------------------------------------------------------------------------------ chart.py (pure)
def _cand(key, zt, low, high, state="fresh", touches=0, tf="1h", strength=None):
    return chart.candidate(_zrow(key, zt, low, high, state, touches, tf, strength))


def test_at_most_six_bands_three_per_role_by_proximity_top_to_bottom():
    p = 1000.0
    cands = [_cand(f"dem{i}", "DEMAND_ZONE", 990.0 - 10 * i, 992.0 - 10 * i) for i in range(6)] + \
            [_cand(f"sup{i}", "SUPPLY_ZONE", 1008.0 + 10 * i, 1010.0 + 10 * i) for i in range(6)]
    bands = chart.select_bands(cands, p)
    assert len(bands) == 6
    assert [b["zone_key"] for b in bands] == ["sup2", "sup1", "sup0", "dem0", "dem1", "dem2"]       # nearest 3 each, top to bottom
    assert [b["role"] for b in bands] == ["supply"] * 3 + ["demand"] * 3
    assert bands[2]["label"] == "supply 1008.00 (fresh)" and bands[3]["label"] == "demand 992.00 (fresh)"
    assert all(h["high"] >= l["high"] for h, l in zip(bands, bands[1:]))


def test_a_short_side_gives_its_slots_to_the_other_and_the_cap_holds():
    p = 1000.0
    cands = [_cand(f"sup{i}", "SUPPLY_ZONE", 1001.0 + 5 * i, 1003.0 + 5 * i) for i in range(8)] + [_cand("dem", "DEMAND_ZONE", 980.0, 985.0)]
    bands = chart.select_bands(cands, p)
    assert len(bands) == 6 and sum(b["role"] == "supply" for b in bands) == 5 and bands[-1]["zone_key"] == "dem"
    assert [b["zone_key"] for b in chart.select_bands(cands, p, max_bands=2)] == ["sup1", "sup0"]
    assert chart.select_bands(cands, p, max_bands=0) == [] and chart.select_bands(cands, 0.0) == [] and chart.select_bands([], p) == []


def test_overlapping_zones_of_a_role_show_once_as_the_strongest():
    p = 1000.0
    weak = _cand("weak", "BULLISH_OB", 992.0, 997.0, "tested", 4, tf="15m")      # nearer, weaker
    strong = _cand("strong", "DEMAND_ZONE", 990.0, 995.0, tf="1d")
    other = _cand("other", "DEMAND_ZONE", 970.0, 975.0)
    assert strong["strength"] > weak["strength"]
    keys = [b["zone_key"] for b in chart.select_bands([weak, strong, other], p)]
    assert keys == ["strong", "other"]
    # a supply band overlapping a demand band is a different role: both stay
    sup = _cand("sup", "SUPPLY_ZONE", 999.0, 1009.0)
    assert {b["zone_key"] for b in chart.select_bands([strong, sup], p)} == {"strong", "sup"}


def test_role_follows_the_price_and_far_zones_are_left_out():
    demand_origin = _cand("x", "DEMAND_ZONE", 1050.0, 1060.0, "flipped", 2)   # a demand base now above the price: acts as supply
    assert chart.select_bands([demand_origin], 1000.0)[0]["label"] == "supply 1050.00 (flipped)"
    assert chart.select_bands([demand_origin], 1100.0)[0]["label"] == "demand 1060.00 (flipped)"
    assert chart.select_bands([_cand("far", "DEMAND_ZONE", 700.0, 720.0)], 1000.0) == []
    assert len(chart.select_bands([_cand("far", "DEMAND_ZONE", 700.0, 720.0)], 1000.0, max_distance_pct=None)) == 1
    inside = chart.select_bands([_cand("in", "DEMAND_ZONE", 995.0, 1003.0)], 1000.0)[0]
    assert inside["distance_pct"] == 0.0 and inside["role"] == "demand"


def test_bands_from_snapshot_rows_skip_invalidated_and_filter_family():
    rows = [_zrow("dem", "DEMAND_ZONE", 99.0, 99.5), _zrow("dead", "SUPPLY_ZONE", 100.5, 101.0, "invalidated"),
            _zrow("lv", "RESISTANCE", 101.0, 101.0, strength=3)]
    assert [b["zone_key"] for b in chart.bands_from_rows(rows, 100.0)] == ["lv", "dem"]
    assert [b["zone_key"] for b in chart.bands_from_rows(rows, 100.0, family="zones")] == ["dem"]
    assert [b["zone_key"] for b in chart.bands_from_rows(rows, 100.0, family="levels", max_bands=1)] == ["lv"]


def test_labels_and_touch_counts():
    line = _cand("lv", "DYNAMIC_SR_RESISTANCE", 512.3, 512.3, "tested", 2, tf="15m", strength=3)
    band = chart.select_bands([line], 500.0)[0]
    assert band["label"] == "supply 512.30 (tested)" and band["touch_text"] == "2 touches" and band["kind"] == "line"
    assert [chart.touch_text(n) for n in (0, 1, 5)] == ["no touches", "1 touch", "5 touches"]
    assert chart.label({"low_text": "1.00", "high_text": "2.00", "state": "fresh"}, "demand") == "demand 2.00 (fresh)"


def test_strength_is_bounded_and_ordered_by_state_touches_and_timeframe():
    s = lambda **kw: chart.zone_strength(_zrow("k", kw.pop("zt", "DEMAND_ZONE"), 1.0, 2.0, **kw))  # noqa: E731
    assert s(state="fresh") > s(state="flipped") > s(state="tested", touches=1)
    assert s(state="tested", touches=1) > s(state="tested", touches=3) > s(state="tested", touches=9) == s(state="tested", touches=50)
    assert s(tf="1d") > s(tf="1h") > s(tf="5m")
    assert s(zt="SUPPORT", strength=5, touches=3) > s(zt="SUPPORT", strength=1, touches=0)
    assert s(remembered=True) == s() + chart.REMEMBERED_BONUS
    values = [s(zt=zt, tf=tf, state=st, touches=t, strength=k, remembered=r) for zt in ("DEMAND_ZONE", "BEARISH_OB", "UP_GAP", "SUPPORT", "DYNAMIC_SR_SUPPORT", "X")
              for tf in ("1m", "1h", "1w") for st in ("fresh", "tested", "flipped") for t in (0, 9) for k in (None, 9) for r in (False, True)]
    assert min(values) >= 0 and max(values) <= 100


def test_nearest_zone_by_tolerance_role_family_and_state():
    rows = [_zrow("dem", "DEMAND_ZONE", 99.0, 99.6), _zrow("sup", "SUPPLY_ZONE", 100.5, 101.0, "tested", 1),
            _zrow("dead", "DEMAND_ZONE", 99.8, 99.9, "invalidated"), _zrow("lv", "RESISTANCE", 100.3, 100.3, strength=2)]
    z = chart.nearest_zone(rows, 100.0, "any", 0.5)
    assert z["zone_key"] == "lv" and z["role"] == "supply"                                   # 0.3 % away; the invalidated one is ignored
    assert chart.nearest_zone(rows, 100.0, "any", 0.5, "zones")["zone_key"] == "dem"           # 0.4 % (the supply band is 0.5 %)
    assert chart.nearest_zone(rows, 100.0, "demand", 0.39) is None
    assert chart.nearest_zone(rows, 100.0, "demand", 0.41)["zone_key"] == "dem"
    assert chart.nearest_zone(rows, 100.0, "supply", 0.2) is None and chart.nearest_zone(rows, 100.0, "any", -1) is None
    assert chart.nearest_zone(rows, 0.0) is None


def test_band_cases_file_matches_the_rule_shared_with_the_browser():
    """The file the TypeScript port is tested against is exactly what the Python rule gives (rebuild: python -m
    tests.zones_chart_cases --write)."""
    stored = json.loads(CASES_PATH.read_text())
    assert stored == json.loads(json.dumps(build_cases()))
    assert len(stored["cases"]) >= 20 and len(CASES_PATH.read_text().splitlines()) < 1000
    # the browser's copy (inside the frontend build context) is byte-identical, so the two rules test the same cases
    assert CASES_FRONTEND_PATH.read_bytes() == CASES_PATH.read_bytes()
    for case in stored["cases"]:
        got = chart.select_bands(case["candidates"], case["price"], max_bands=case["max_bands"], max_distance_pct=case["max_distance_pct"])
        assert [(b["zone_key"], b["label"], b["touch_text"]) for b in got] == [(e["zone_key"], e["label"], e["touch_text"]) for e in case["expected"]]
        assert len(got) <= 6


# --------------------------------------------------------------------------------------------------- as-of reads (DB)
def test_read_as_of_picks_the_latest_snapshot_at_or_before_the_time():
    ids = _snapshots()
    assert _read(KEY, ist(D0, 15, 29), None) is None                                    # nothing known before the first close
    assert _read(KEY, ist(D0, 15, 30), None).run_id == ids[0]                           # as_of == at: known
    assert _read(KEY, ist(D1, 12, 0), None).run_id == ids[0]
    snap = _read(KEY, ist(D2, 15, 30), None)
    assert snap.run_id == ids[2] and [r["zone_key"] for r in snap.rows] == ["s2"]          # live rows only
    with pytest.raises(ValueError):
        _read(KEY, datetime(2026, 3, 11, 12, 0), None)                                   # a naive time is ambiguous


def test_timeline_equals_zones_as_of_at_every_bar_close_per_tenant():
    _, a = _owner("z7a3-tl-a@example.com")
    _, b = _owner("z7a3-tl-b@example.com")
    _snapshots()
    own = _run(_tenant_settings(a["tenant_id"]))
    async def own_runs():
        return [await _write_run(KEY, ist(D0, 15, 30), [_zrow("A0", "DEMAND_ZONE", 90.0, 91.0)], tenant_id=a["tenant_id"], settings=own),
                await _write_run(KEY, ist(D1, 15, 30), [_zrow("A1", "SUPPLY_ZONE", 100.1, 100.2)], tenant_id=a["tenant_id"], settings=own),
                await _write_run(KEY, ist(D2, 15, 30), [_zrow("A2", "SUPPLY_ZONE", 100.3, 100.4)], tenant_id=a["tenant_id"], settings=own)]
    a_ids = _run(own_runs())
    frame = _bars([D1, D2, D3])
    closes = bar_close(frame.index, "5m")
    for tenant, expect_ids in ((a["tenant_id"], set(a_ids)), (b["tenant_id"], None), (None, None)):
        tl = _timeline(KEY, tenant, ist(D1, 9, 0), ist(D3, 15, 30))
        seen = set()
        for t in closes[::7].append(closes[-1:]):
            snap, want = tl.at(t), _read(KEY, t, tenant)
            assert (snap and snap.run_id) == (want and want.run_id), (tenant, t)
            seen.add(snap.run_id if snap else None)
        if expect_ids is not None:
            assert seen <= expect_ids                                                   # tenant A reads its own scope only
        else:
            platform = {r.id for r in _run(_runs(store.PLATFORM_SCOPE))}
            assert seen <= platform                                                      # never tenant A's rows


async def _runs(scope):
    async with _session_factory() as session:
        return list(await session.scalars(select(MarketZoneRunRecord).where(MarketZoneRunRecord.scope == scope)))


def test_timeline_reads_the_latest_runs_only_up_to_its_cap():
    async def many():
        for i in range(10):
            await _write_run(KEY, ist(D0, 15, 30) + timedelta(days=i), [_zrow(f"k{i}", "DEMAND_ZONE", 90.0, 91.0)])
    _run(many())
    tl = _timeline(KEY, None, ist(D0, 9, 0), ist(D0, 9, 0) + timedelta(days=12))
    assert len(tl.snapshots) == 11 and tl.snapshots[0] is None and not tl.empty
    capped = _run(_capped(4))                                                            # the latest four only
    assert len(capped.snapshots) == 4 and [s.run_id for s in capped.snapshots] == [s.run_id for s in tl.snapshots[-4:]]
    assert capped.at(ist(D0, 15, 30) + timedelta(days=2)) is None                        # before its window: nothing read
    assert _timeline(OTHER, None, ist(D0, 9, 0), ist(D3, 9, 0)).empty


async def _capped(n):
    async with _session_factory() as session:
        return await read.timeline(session, KEY, None, ist(D0, 9, 0), ist(D0, 9, 0) + timedelta(days=12), max_runs=n)


def test_window_start_covers_the_lookback_in_calendar_days():
    end = ist(D3, 15, 30)
    assert end - timedelta(days=4.1) < read.window_start({"5m": 1}, end) < end - timedelta(days=4)
    assert read.window_start({}, end) == end - timedelta(days=3)
    assert read.window_start({"1d": 20}, end) == end - timedelta(days=21 * 7 / 5 + 4)
    assert read.window_start({"1d": 20, "5m": 3}, end) == read.window_start({"1d": 20}, end)
    with pytest.raises(ValueError):
        read.window_start({"1d": 1}, datetime(2026, 1, 1))


# ------------------------------------------------------------------------------------------------- ScreenQL (engine)
VARIANTS = [('NearZone("demand", 0.3)', "near", "demand", 0.3, "all"), ("NearZone()", "near", "any", 0.5, "all"),
            ('ZoneRole(0.5)', "role", "any", 0.5, "all"), ('ZoneState("any", 1.0, "zones")', "state", "any", 1.0, "zones"),
            ('ZoneStrength("supply", 2.0)', "strength", "supply", 2.0, "all"), ('ZoneState("demand", 0.6, "levels")', "state", "demand", 0.6, "levels")]


def _engine_value(snap, close, what, role, tol, family):
    z = chart.nearest_zone(snap.rows, close, role, tol, family) if snap is not None else None
    if what == "near":
        return z is not None
    if z is None:
        return None if what != "strength" else np.nan
    return {"role": str(z["role"]).upper(), "state": str(z["state"]).upper(), "strength": float(z["strength"])}[what]


def _same(a, b):
    if isinstance(a, float) and isinstance(b, float) and np.isnan(a) and np.isnan(b):
        return True
    if (a is None or (isinstance(a, float) and np.isnan(a))) and b is None:
        return True
    return a == b


def test_screenql_text_matches_the_engine_bar_by_bar():
    _snapshots()
    frame = _bars([D1, D2, D3])
    tl = _timeline(KEY, None, ist(D1, 9, 0), ist(D3, 15, 30))
    closes = bar_close(frame.index, "5m")
    for text, what, role, tol, family in VARIANTS:
        got = _series(text, frame, tl)
        for j in range(0, len(frame), 3):
            want = _engine_value(_read(KEY, closes[j], None), float(frame["close"].iloc[j]), what, role, tol, family)
            assert _same(got.iloc[j], want), (text, frame.index[j], got.iloc[j], want)
        # the builder's JSON tree gives the same series as the text
        tree = json.loads(json.dumps(nodes.to_json(parse(text))))
        again = evaluate(nodes.from_json(tree), SymbolData("ZCHARTA", {"5m": frame}, zones=tl), base_tf="5m")
        assert got.equals(again)
    near = _series("NearZone()", frame, tl)
    assert near.any() and not near.all()                                                  # a parity check on all-False proves nothing
    states = set(_series('ZoneState("any", 1.0)', frame, tl).dropna())
    assert {"FRESH", "TESTED", "FLIPPED"} & states and "INVALIDATED" not in states


def test_a_snapshot_is_seen_from_the_bar_that_closes_at_its_as_of_never_earlier():
    _snapshots()
    frame = _bars([D2])
    tl = _timeline(KEY, None, ist(D2, 9, 0), ist(D3, 9, 0))
    strength = _series('ZoneStrength("supply", 2.0)', frame, tl)
    last, before = frame.index[-1], frame.index[-2]                    # 15:25 bar closes at 15:30 = SNAP2's as_of
    assert bar_close(pd.DatetimeIndex([last]), "5m")[0] == pd.Timestamp(ist(D2, 15, 30))
    assert strength[last] == chart.zone_strength(SNAP2[1])            # the flipped 15m supply band of SNAP2
    assert strength[before] == chart.zone_strength(SNAP1[0]) != strength[last]   # SNAP1's demand base, above the close: supply now


def test_no_look_ahead_appending_and_mutating_later_bars_and_snapshots():
    _snapshots(with_last=False)
    frame = _bars([D1, D2]).loc[lambda f: f.index < pd.Timestamp(ist(D2, 15, 0))]          # up to the 14:55 bar
    end = frame.index[-1] + pd.Timedelta(minutes=5)
    texts = [v[0] for v in VARIANTS] + ["NearZone()@1h", "Count(6, NearZone())", 'ZoneRole()@1d == "DEMAND"']
    before = {t: _series(t, frame, _timeline(KEY, None, ist(D1, 9, 0), end)) for t in texts}
    # the future: a later snapshot, more bars, and wild changes to those bars
    _run(_write_run(KEY, ist(D2, 15, 30), SNAP2))
    _run(_write_run(KEY, ist(D3, 15, 30), [_zrow("late", "DEMAND_ZONE", 99.0, 101.0, tf="1d")]))
    longer = pd.concat([frame, _bars([D2, D3]).loc[lambda f: f.index >= pd.Timestamp(ist(D2, 15, 0))]])
    longer.loc[longer.index > frame.index[-1], ["open", "high", "low", "close"]] *= 1.07
    tl_late = _timeline(KEY, None, ist(D1, 9, 0), ist(D3, 15, 30))
    for t in texts:
        after = _series(t, longer, tl_late).loc[frame.index]
        pd.testing.assert_series_equal(before[t], after, check_names=False, obj=t)
    # and a later snapshot that is rewritten changes nothing earlier either
    async def mutate():
        async with _session_factory() as session:
            run = await store.latest_run(session, store.PLATFORM_SCOPE, KEY, ist(D3, 15, 30))
            for rec in await session.scalars(select(MarketZoneRecord).where(MarketZoneRecord.run_id == run.id)):
                rec.zone_low, rec.zone_high = 99.9, 100.1
            await session.commit()
    _run(mutate())
    tl_mut = _timeline(KEY, None, ist(D1, 9, 0), ist(D3, 15, 30))
    for t in texts:
        pd.testing.assert_series_equal(before[t], _series(t, longer, tl_mut).loc[frame.index], check_names=False, obj=t)


def test_zone_functions_explain_missing_data_and_never_match_it():
    frame = _bars([D1])
    ast, v = compile_screen("NearZone()", base_tf="5m")
    with pytest.raises(ScreenRuntimeError, match="not loaded"):
        evaluate(ast, SymbolData("X", {"5m": frame}), base_tf="5m")
    empty = read.ZoneTimeline("X")
    out = run_screen(ast, v, [SymbolData("X", {"5m": frame}, zones=empty)], base_tf="5m")
    assert not out[0].matched and "no stored market zones" in out[0].reason
    ast, v = compile_screen("NOT NearZone()", base_tf="5m")
    assert "no stored market zones" in run_screen(ast, v, [SymbolData("X", {"5m": frame}, zones=empty)], base_tf="5m")[0].reason
    _snapshots()
    tl = _timeline(KEY, None, ist(D1, 9, 0), ist(D1, 15, 30))
    assert _series('ZoneRole() == "SUPPLY"', frame, tl).dtype == bool
    for bad in ('NearZone("any", -1)', 'NearZone("any", 25)'):
        ast, v = compile_screen(bad, base_tf="5m")
        assert v.ok
        assert "tolerance_pct" in run_screen(ast, v, [SymbolData("X", {"5m": frame}, zones=tl)], base_tf="5m")[0].reason


def test_registry_entries_flag_and_validation():
    from app.platform.controls import DEFAULT_OFF_FLAGS, FEATURE_FLAGS
    from app.price_action.zones import job
    from app.screener.registry import FUNCTIONS, ZONE_FUNCTIONS, ZONES_FLAG
    assert ZONE_FUNCTIONS == ("NearZone", "ZoneRole", "ZoneState", "ZoneStrength")
    assert ZONES_FLAG == job.FLAG == "market_zones" and ZONES_FLAG in DEFAULT_OFF_FLAGS and "places no orders" in FEATURE_FLAGS[ZONES_FLAG]
    assert all(FUNCTIONS[n].needs == ("zones",) for n in ZONE_FUNCTIONS)
    for text, ok in (('ZoneState() == "FRESH"', True), ('ZoneState() == "INVALIDATED"', False), ('ZoneRole() == "DEMAND"', True),
                     ('NearZone("long")', False), ('NearZone("any", 0.5, "lines")', False), ("ZoneStrength() > 60", True),
                     ("ZoneStrength() > close", False)):
        assert compile_screen(text, base_tf="15m")[1].ok is ok, text


# ---------------------------------------------------------------------------------------------- API: flag and gating
@pytest.fixture
def screener_on():
    _set(screener_v2=True, market_zones=False)
    yield


def test_zone_functions_are_hidden_and_refused_while_off(screener_on):
    headers, _ = _owner("z7a3-off@example.com")
    reg = client.get("/api/screener/registry", headers=headers).json()["entries"]
    assert "NearZone" not in reg and "ZoneStrength" not in reg and "SwingHigh" in reg
    v = client.post("/api/screener/validate", json={"source": 'NearZone("demand") AND close > 1', "base_tf": "1d"}, headers=headers).json()
    assert not v["ok"] and "market_zones" in v["problems"][-1]["message"] and "NearZone()" in v["problems"][-1]["message"]
    assert client.post("/api/screener/screens", json={"name": "z", "source": "ZoneStrength() > 50"}, headers=headers).status_code == 422
    run = client.post("/api/screener/run", json={"source": 'ZoneRole() == "DEMAND"', "symbols": ["X"]}, headers=headers)
    assert run.status_code == 422 and "market_zones" in str(run.json())
    assert client.get("/api/charts/layers/market-zones", params={"instrument": KEY}, headers=headers).status_code == 503
    assert client.post("/api/screener/validate", json={"source": "close > 1"}, headers=headers).json()["ok"]


def _daily_frame(days=30):
    end = pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta(days=1)
    idx = pd.DatetimeIndex([end - pd.Timedelta(days=days - 1 - i) for i in range(days)])
    c = np.full(days, 100.0)
    return pd.DataFrame({"open": c, "high": c + 0.5, "low": c - 0.5, "close": c, "volume": 1000.0}, index=idx)


def test_screen_runs_on_stored_zones_per_tenant_when_on(screener_on, monkeypatch):
    _set(market_zones=True)
    headers, me = _owner("z7a3-on@example.com")
    other_headers, other = _owner("z7a3-other@example.com")
    reg = client.get("/api/screener/registry", headers=headers).json()["entries"]
    assert reg["NearZone"]["choices"]["role"] == ["any", "demand", "supply"] and reg["ZoneState"]["values"] == ["FRESH", "TESTED", "FLIPPED"]
    old = datetime.now(timezone.utc) - timedelta(days=10)
    _run(_write_run(KEY, old, [_zrow("p", "DEMAND_ZONE", 99.6, 99.9)]))            # platform: demand just under 100
    own = _run(_tenant_settings(other["tenant_id"]))
    _run(_write_run(KEY, old, [_zrow("o", "SUPPLY_ZONE", 100.2, 100.4, "tested", 1)], tenant_id=other["tenant_id"], settings=own))
    frame = _daily_frame()

    async def fake_fetch(session, tenant_id, symbols, exchange, base_tf, lookback, now=None, include_forming=False, **_kw):
        return [SymbolData(s, {base_tf: frame}) for s in symbols if s != "NOZONES"], {}, "broker:fake"
    from app.screener import routes as screener_routes
    monkeypatch.setattr(screener_routes, "fetch_frames", fake_fetch)
    body = {"source": 'NearZone("demand", 0.5) AND ZoneState("demand") == "FRESH"', "symbols": ["ZCHARTA", "NOZONES", "ZCHARTB"], "base_tf": "1d"}
    out = client.post("/api/screener/run", json=body, headers=headers)
    assert out.status_code == 200, out.text
    rows = {r["symbol"]: r for r in out.json()["results"]}
    assert out.json()["matched"] == ["ZCHARTA"] and "no stored market zones" in rows["ZCHARTB"]["reason"]
    # the other organisation reads its own snapshot: a supply band, no demand
    theirs = client.post("/api/screener/run", json=body, headers=other_headers).json()
    assert theirs["matched"] == []
    sup = client.post("/api/screener/run", json={**body, "source": 'ZoneRole(0.5) == "SUPPLY"'}, headers=other_headers).json()
    assert sup["matched"] == ["ZCHARTA"]
    # an organisation with its own settings but no snapshot built with them: no platform fallback in a screen
    third_headers, third = _owner("z7a3-third@example.com")
    _run(_tenant_settings(third["tenant_id"]))
    strict = {r["symbol"]: r for r in client.post("/api/screener/run", json=body, headers=third_headers).json()["results"]}
    assert not strict["ZCHARTA"]["matched"] and "no stored market zones" in strict["ZCHARTA"]["reason"]


# ----------------------------------------------------------------------------------------------- API: the chart layer
def test_chart_layer_reads_as_of_per_tenant_with_at_most_six_bands(screener_on):
    _set(market_zones=True)
    headers, me = _owner("z7a3-layer@example.com")
    other_headers, other = _owner("z7a3-layer-b@example.com")
    many = [_zrow(f"dem{i}", "DEMAND_ZONE", 99.0 - i, 99.5 - i) for i in range(5)] + \
           [_zrow(f"sup{i}", "SUPPLY_ZONE", 100.5 + i, 101.0 + i, "tested", i) for i in range(5)] + [_zrow("gone", "DEMAND_ZONE", 99.6, 99.8, "invalidated")]
    _run(_write_run(KEY, ist(D1, 15, 30), many, price=100.0))
    _run(_write_run(KEY, ist(D2, 15, 30), [_zrow("late", "SUPPLY_ZONE", 120.0, 121.0)], price=119.0))
    own = _run(_tenant_settings(other["tenant_id"]))
    _run(_write_run(KEY, ist(D1, 15, 30), [_zrow("mine", "DEMAND_ZONE", 95.0, 96.0)], tenant_id=other["tenant_id"], settings=own))
    url = "/api/charts/layers/market-zones"
    r = client.get(url, params={"instrument": "nse:zcharta", "at": ist(D2, 12, 0).isoformat()}, headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["as_of"] == ist(D1, 15, 30).isoformat() and body["scope"] == "platform" and body["price"] == 100.0
    assert body["price_source"] == "snapshot" and body["live_count"] == 10 and len(body["bands"]) == 6
    assert [b["role"] for b in body["bands"]] == ["supply"] * 3 + ["demand"] * 3
    assert body["bands"][2]["label"] == "supply 100.50 (tested)" and body["bands"][2]["touch_text"] == "no touches"
    assert body["bands"][3]["label"] == "demand 99.50 (fresh)" and "gone" not in {z["zone_key"] for z in body["zones"]}
    assert "not signals" in body["note"]
    # the reference price moves the roles; `at` after the next snapshot reads it
    moved = client.get(url, params={"instrument": KEY, "at": ist(D2, 12, 0).isoformat(), "price": 102.2, "max_bands": 2}, headers=headers).json()
    assert moved["price_source"] == "request" and len(moved["bands"]) == 2 and {b["zone_key"] for b in moved["bands"]} == {"sup1", "sup2"}
    later = client.get(url, params={"instrument": KEY, "at": ist(D3, 9, 0).isoformat()}, headers=headers).json()
    assert later["as_of"] == ist(D2, 15, 30).isoformat() and [b["zone_key"] for b in later["bands"]] == ["late"]
    assert client.get(url, params={"instrument": KEY, "at": ist(D0, 9, 0).isoformat()}, headers=headers).json()["bands"] == []
    # the other organisation sees its own snapshot only, never the platform rows mixed in, and no tenant id leaks
    theirs = client.get(url, params={"instrument": KEY, "at": ist(D2, 12, 0).isoformat()}, headers=other_headers).json()
    assert [z["zone_key"] for z in theirs["zones"]] == ["mine"] and theirs["scope"] == "organisation"
    assert "tenant:" not in json.dumps(theirs)
    assert body["fallback"] is False and body["settings_note"] is None and theirs["fallback"] is False
    # settings-hash rule: an organisation with its own settings but no snapshot built with them gets the platform's on
    # the chart only, marked as such (ScreenQL never falls back - test_timeline_* / test_screen_runs_*)
    third_headers, third = _owner("z7a3-layer-c@example.com")
    _run(_tenant_settings(third["tenant_id"]))
    fb = client.get(url, params={"instrument": KEY, "at": ist(D2, 12, 0).isoformat()}, headers=third_headers).json()
    assert fb["fallback"] is True and "default settings" in fb["settings_note"] and fb["scope"] == "platform" and len(fb["bands"]) == 6
    assert _read(KEY, ist(D2, 12, 0), third["tenant_id"]) is None                                   # the strict read sees nothing
    # input checks
    assert client.get(url, params={"instrument": KEY, "at": "2026-03-11T12:00:00"}, headers=headers).status_code == 422
    assert client.get(url, params={"instrument": "bad key!"}, headers=headers).status_code == 422
    assert client.get(url, params={"instrument": "*"}, headers=headers).status_code == 422
    assert client.get(url, params={"instrument": KEY, "max_bands": 7}, headers=headers).status_code == 422
    assert client.get(url, params={"instrument": KEY, "price": -1}, headers=headers).status_code == 422
    assert client.get(url, params={"instrument": OTHER}, headers=headers).json()["bands"] == []
    assert client.get(url, params={"instrument": KEY}).status_code == 401


# ------------------------------------------------------------------------------------------- alerts: gating like BK1
def _save_zone_screen(headers, source="NearZone()", tf="5m"):
    _set(market_zones=True)
    made = client.post("/api/screener/screens", json={"name": "zones", "source": source, "base_tf": tf}, headers=headers)
    assert made.status_code == 201, made.text
    _set(market_zones=False)
    return made.json()["id"]


def test_alert_rules_with_zone_functions_are_refused_while_off_and_never_intrabar(screener_on):
    headers, _ = _owner("z7a3-alert@example.com")
    r = client.post("/api/alerts/rules", json={"name": "z", "kind": "instrument", "symbol": "ABC", "condition": 'NearZone("demand")',
                                               "base_tf": "15m"}, headers=headers)
    assert r.status_code == 422 and "market_zones" in str(r.json()), r.text
    sid = _save_zone_screen(headers)
    r = client.post("/api/alerts/rules", json={"name": "s", "kind": "screen", "screen_id": sid, "symbols": ["ABC"]}, headers=headers)
    assert r.status_code == 422 and "market_zones" in str(r.json())
    _set(market_zones=True, screener_intrabar=True)
    for body in ({"name": "s", "kind": "screen", "screen_id": sid, "symbols": ["ABC"], "fire_on": "intrabar"},
                 {"name": "i", "kind": "instrument", "symbol": "ABC", "condition": "ZoneStrength() > 40", "base_tf": "15m", "fire_on": "intrabar"}):
        r = client.post("/api/alerts/rules", json=body, headers=headers)
        assert r.status_code == 422 and "bar close only" in str(r.json()), r.text
    ok = client.post("/api/alerts/rules", json={"name": "s", "kind": "screen", "screen_id": sid, "symbols": ["ABC"]}, headers=headers)
    assert ok.status_code == 201, ok.text
    plain = client.post("/api/alerts/rules", json={"name": "p", "kind": "instrument", "symbol": "ABC", "condition": "close > 1", "base_tf": "15m",
                                                   "fire_on": "intrabar"}, headers=headers)
    assert plain.status_code in (200, 201), plain.text


def test_zone_rules_pause_while_off_resume_at_bar_close_and_skip_intrabar(screener_on):
    from app.alerts import barclose
    from app.db.models import AlertEventRecord, AlertRuleRecord
    from tests.test_s4a_barclose import TUE, FakeFetch, _bars as s4_bars, _rule, ist as s4_ist
    _, me = _owner("z7a3-pause@example.com")
    rid = _rule(me["tenant_id"], condition='NearZone("demand", 0.5)', tf="5m", symbol="ZCHARTA")
    ib = _rule(me["tenant_id"], condition='NearZone("demand", 0.5)', tf="5m", symbol="ZCHARTA", fire_on="intrabar")
    _run(_write_run(KEY, s4_ist(TUE, 9, 0) - timedelta(hours=18), [_zrow("p", "DEMAND_ZONE", 99.7, 99.9)]))
    fetch = FakeFetch({"ZCHARTA": s4_bars(s4_ist(TUE, 9, 15), [100.0] * 61)})

    def cycle(now, rule_id=rid):
        async def go():
            async with _session_factory() as session:
                await barclose.run_due(session, now=now, fetch=fetch, holidays=set())
                rule = await session.get(AlertRuleRecord, rule_id)
                events = list(await session.scalars(select(AlertEventRecord).where(AlertEventRecord.rule_id == rule_id)))
                return rule.status, rule.last_problem, len(events)
        return _run(go())
    now = s4_ist(TUE, 14, 21)
    status, problem, fired = cycle(now)
    assert status == "active" and "market_zones" in problem and fired == 0                      # paused, not dropped
    _set(market_zones=True, screener_intrabar=True)
    status, problem, fired = cycle(now + timedelta(minutes=2))
    assert status == "active" and problem is None and fired == 1                                # resumes on the same closed bar
    status, problem, fired = cycle(now + timedelta(minutes=4), ib)
    assert status == "active" and "bar close only" in problem and fired == 0                    # never intrabar


def test_breakout_and_zone_gates_only_see_their_own_family():
    """Z7a3-7: BK1's gate takes breakout functions only, the zone gate zone functions only."""
    from app.screener import parse
    from app.screener import zones as zone_gate
    from app.screener.breakouts import gate as breakout_gate
    ast = parse('NearZone() AND RangeBreakout("up") AND ZoneStrength() > 50')
    assert breakout_gate.flagged_calls(ast) == ["RangeBreakout"]
    assert zone_gate.flagged_calls(ast) == ["NearZone", "ZoneStrength"]


def test_registry_hides_each_flagged_family_by_its_own_flag(screener_on):
    headers, _ = _owner("z7a3-families@example.com")
    _set(market_zones=True, screener_breakouts=False)
    reg = client.get("/api/screener/registry", headers=headers).json()["entries"]
    assert "NearZone" in reg and "RangeBreakout" not in reg
    v = client.post("/api/screener/validate", json={"source": 'NearZone() AND RangeBreakout("up")', "base_tf": "1d"}, headers=headers).json()
    assert not v["ok"] and any("screener_breakouts" in p["message"] for p in v["problems"])
    assert not any("market_zones" in p["message"] for p in v["problems"])
    _set(market_zones=False, screener_breakouts=True)
    reg = client.get("/api/screener/registry", headers=headers).json()["entries"]
    assert "NearZone" not in reg and "RangeBreakout" in reg
    _set(screener_breakouts=False)
