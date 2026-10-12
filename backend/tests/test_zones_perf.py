"""Z7a-3 follow-up (F1-F4, before `market_zones` is ever turned on): the zone reads are batched and bounded, the zone
functions are vectorised, a capped window refuses instead of reading False, and a non-lake symbol shows "no zones".

* F1: `read.timelines` reads a whole universe in a fixed number of queries (settings, runs, rows) whatever the number of
  symbols or snapshots; its answer equals `zones_as_of` at every time; the window comes from the zone calls' own history;
  the bar-close engine reads zones only when the cycle has not evaluated the same screen already.
* F2: the vectorised zone series equal the per-bar rule (`chart.nearest_zone`) bar by bar and are much faster.
Synthetic zones and bars only.
"""
import asyncio
import random
import time
from contextlib import contextmanager
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import event

from app.price_action.zones import chart, read, store
from app.screener import compile_screen, parse
from app.screener import zones as zone_gate
from app.screener.runtime import SymbolData, bar_close, evaluate
from tests.test_auth_api import _engine, _session_factory
from tests.test_phase_l_ai import _owner
from tests.test_zones_chart_screenql import (D0, D1, D2, D3, KEY, _bars, _clean, _read, _set, _tenant_settings, _write_run,  # noqa: F401
                                             _zrow, ist)


def _run(coro):
    return asyncio.run(coro)


@contextmanager
def count_queries():
    seen = []

    def on(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)
    event.listen(_engine.sync_engine, "before_cursor_execute", on)
    try:
        yield seen
    finally:
        event.remove(_engine.sync_engine, "before_cursor_execute", on)


def _many(keys, nights, tenant_id=None, settings=None):
    async def go():
        for key in keys:
            for n in range(nights):
                rows = [_zrow(f"{key}-d{n}", "DEMAND_ZONE", 99.5, 99.9), _zrow(f"{key}-s{n}", "SUPPLY_ZONE", 100.3 + n / 100, 100.6),
                        _zrow(f"{key}-x{n}", "SUPPLY_ZONE", 120.0, 121.0, "invalidated")]
                await _write_run(key, ist(D0, 15, 30) + timedelta(days=n), rows, tenant_id=tenant_id, settings=settings)
    _run(go())


def _timelines(keys, tenant_id, start, end, **kw):
    async def go():
        async with _session_factory() as session:
            return await read.timelines(session, keys, tenant_id, start, end, **kw)
    return _run(go())


# ------------------------------------------------------------------------------------------------------------------ F1
def test_a_universe_is_read_in_a_fixed_number_of_queries():
    big = [f"NSE:ZQ{i}" for i in range(8)]
    small = big[:1]
    _many(big, 10)
    start, end = ist(D0, 9, 0), ist(D0, 9, 0) + timedelta(days=12)
    with count_queries() as one:
        _timelines(small, None, start, end)
    with count_queries() as eight:
        got = _timelines(big, None, start, end)
    assert len(one) == len(eight) <= 3, eight                    # runs before the window + runs in it + rows (no tenant)
    _, me = _owner("z7a3perf-q@example.com")
    with count_queries() as tenant:
        _timelines(big, me["tenant_id"], start, end)
    assert len(tenant) <= 4                                      # + one settings query for every key at once
    tl = got["NSE:ZQ3"]
    assert len(tl.snapshots) == 11 and tl.snapshots[0] is None and all(len(s.rows) == 2 for s in tl.snapshots[1:])   # live rows only


def test_batched_timelines_equal_zones_as_of_at_every_time_per_tenant():
    _, a = _owner("z7a3perf-a@example.com")
    _, b = _owner("z7a3perf-b@example.com")
    keys = ["NSE:ZQA", "NSE:ZQB"]
    _many(keys, 4)
    own = _run(_tenant_settings(a["tenant_id"], keys[0]))
    _many(keys[:1], 3, tenant_id=a["tenant_id"], settings=own)
    start, end = ist(D0, 12, 0), ist(D3, 15, 30)
    times = [start + timedelta(hours=h) for h in range(0, 24 * 4, 5)] + [ist(D1, 15, 30), ist(D2, 15, 30)]
    for tenant in (a["tenant_id"], b["tenant_id"], None):
        got = _timelines(keys, tenant, start, end)
        for key in keys:
            for t in times:
                snap, want = got[key].at(t), _read(key, t, tenant)
                assert (snap and snap.run_id) == (want and want.run_id), (tenant, key, t)
                assert (snap and [r["zone_key"] for r in snap.rows]) == (want and [r["zone_key"] for r in want.rows])


def test_read_scopes_match_zones_as_of():
    _, a = _owner("z7a3perf-scope@example.com")
    _run(_tenant_settings(a["tenant_id"], "NSE:ZQA"))

    async def go():
        async with _session_factory() as session:
            scopes = await store.read_scopes(session, ["NSE:ZQA", "nse:zqb"], a["tenant_id"])
            many = await store.settings_for_many(session, a["tenant_id"], ["NSE:ZQA", "NSE:ZQB"])
            one = await store.settings_for(session, a["tenant_id"], "NSE:ZQB")
            return scopes, many, one
    scopes, many, one = _run(go())
    assert scopes == {"NSE:ZQA": store.tenant_scope(a["tenant_id"]), "NSE:ZQB": store.tenant_scope(a["tenant_id"])}   # "*" row: both
    assert many["NSE:ZQB"] == one
    assert _run(_scopes_none()) == {"NSE:ZQA": store.PLATFORM_SCOPE}


async def _scopes_none():
    async with _session_factory() as session:
        return await store.read_scopes(session, ["NSE:ZQA"], None)


def test_the_window_comes_from_the_zone_calls_own_history():
    lb = zone_gate.zone_lookback
    assert lb(parse("NearZone()"), "5m") == {"5m": 1}
    assert lb(parse("Count(5, NearZone()) AND SMA(close, 200) > close"), "5m") == {"5m": 5}
    assert lb(parse("NOT (ZoneRole()@1h == \"DEMAND\") AND RSI(14) > 50"), "5m") == {"1h": 1}
    assert lb(parse("SMA(close, 20) > close"), "5m") == {"5m": 1}                  # no zone call: a nominal bar
    whole = compile_screen("Count(5, NearZone()) AND SMA(close, 200) > close", base_tf="5m")[1].lookback
    assert whole["5m"] >= 200                                                      # what the old window was sized from


def test_bar_close_reads_zones_once_for_rules_sharing_a_cached_result(monkeypatch):
    from app.alerts import barclose
    from tests.test_s4a_barclose import TUE, FakeFetch, _bars as s4_bars, _rule, ist as s4_ist
    _set(screener_v2=True, market_zones=True)
    _, me = _owner("z7a3perf-alerts@example.com")
    for name in ("r1", "r2"):
        _rule(me["tenant_id"], name=name, condition='NearZone("demand", 0.5)', tf="5m", symbol="ZCHARTA")
    _run(_write_run(KEY, s4_ist(TUE, 9, 0) - timedelta(hours=18), [_zrow("p", "DEMAND_ZONE", 99.7, 99.9)]))
    fetch = FakeFetch({"ZCHARTA": s4_bars(s4_ist(TUE, 9, 15), [100.0] * 61)})
    calls = []
    real = zone_gate.attach_zones

    async def spy(*args, **kwargs):
        calls.append(args[1])
        return await real(*args, **kwargs)
    monkeypatch.setattr(zone_gate, "attach_zones", spy)

    async def go():
        async with _session_factory() as session:
            return await barclose.run_due(session, now=s4_ist(TUE, 14, 21), fetch=fetch, holidays=set())
    out = _run(go())
    assert out.fired == 2 and out.cache.reused_results == 1 and len(calls) == 1


def test_bar_close_evidence_keeps_the_zone_band_for_a_rule_reusing_a_cached_result():
    # U8 EV x Z7a-3: the second rule reuses the first's result and skips the zone read, yet its evidence pack still draws
    # the zone its condition read - a shared result key means the same cached SymbolData objects, which already hold the
    # timeline, and evidence._trim (dataclasses.replace) carries it over
    import json

    from sqlalchemy import select

    from app.alerts import barclose
    from app.db.models import ScreenEvidenceRecord
    from tests.test_s4a_barclose import TUE, FakeFetch, _bars as s4_bars, _rule, ist as s4_ist
    _set(screener_v2=True, market_zones=True, evidence_charts=True)
    _, me = _owner("z7a3perf-evidence@example.com")
    for name in ("e1", "e2"):
        _rule(me["tenant_id"], name=name, condition='NearZone("demand", 0.5)', tf="5m", symbol="ZCHARTA")
    _run(_write_run(KEY, s4_ist(TUE, 9, 0) - timedelta(hours=18), [_zrow("p", "DEMAND_ZONE", 99.7, 99.9)]))
    fetch = FakeFetch({"ZCHARTA": s4_bars(s4_ist(TUE, 9, 15), [100.0] * 61)})

    async def go():
        async with _session_factory() as session:
            out = await barclose.run_due(session, now=s4_ist(TUE, 14, 21), fetch=fetch, holidays=set())
            await session.commit()
            rows = (await session.execute(select(ScreenEvidenceRecord).where(ScreenEvidenceRecord.tenant_id == me["tenant_id"]))).scalars().all()
            return out, rows
    try:
        out, rows = _run(go())
    finally:
        _set(evidence_charts=False)                      # a platform-wide flag: never left on for later tests
    assert out.fired == 2 and out.cache.reused_results == 1 and len(rows) == 2
    for row in rows:
        assert [d for d in json.loads(row.pack_json)["drawables"] if d["kind"] == "band"], row.alert_event_id


# ------------------------------------------------------------------------------------------------------------------ F2
def _random_timeline(rng, n_bars, n_snaps, n_rows, index, tf="5m"):
    closes = bar_close(index, tf)
    cut = sorted(rng.sample(range(1, n_bars), n_snaps - 1))
    times, snaps = [], []
    for i, b in enumerate([0, *cut]):
        rows = []
        for k in range(n_rows):
            zt = rng.choice(["DEMAND_ZONE", "SUPPLY_ZONE", "BULLISH_OB", "UP_GAP", "SUPPORT", "DYNAMIC_SR_RESISTANCE"])
            mid = 100 * (1 + rng.uniform(-0.03, 0.03))
            w = 0.0 if zt in ("SUPPORT", "DYNAMIC_SR_RESISTANCE") else rng.uniform(0.02, 0.4)
            rows.append({"zone_key": f"{i}-{k:03d}", "zone_type": zt, "timeframe": rng.choice(["5m", "1h", "1d"]), "zone_low": round(mid - w, 2),
                         "zone_high": round(mid + w, 2), "state": rng.choice(["fresh", "tested", "flipped", "invalidated"]),
                         "touches": rng.randint(0, 5), "strength": rng.randint(1, 6), "remembered": rng.random() < 0.3})
        times.append(pd.Timestamp(closes[b]).value)
        snaps.append(read.Snapshot(i, pd.Timestamp(closes[b]).to_pydatetime(), "platform", 100.0, rows))
    return read.ZoneTimeline("NSE:R", np.array(times, dtype="int64"), snaps)


def _per_bar(timeline, frame, tf, what, role, tol, family):
    """The rule bar by bar (what the runtime did before F2)."""
    idx = timeline.index_at(np.asarray(pd.DatetimeIndex(bar_close(frame.index, tf)).asi8))
    out = []
    for j, close in enumerate(frame["close"].astype(float)):
        snap = timeline.snapshots[idx[j]] if idx[j] >= 0 else None
        z = chart.nearest_zone(snap.rows, close, role, tol, family) if snap is not None else None
        out.append(z is not None if what == "near" else (None if z is None else
                   {"role": str(z["role"]).upper(), "state": str(z["state"]).upper(), "strength": float(z["strength"])}[what]))
    return out


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_vectorised_zone_series_equal_the_per_bar_rule(seed):
    rng = random.Random(seed)
    frame = _bars([D1, D2, D3])
    frame = frame.assign(close=frame["close"] + np.round(np.random.default_rng(seed).normal(0, 0.8, len(frame)), 2))
    tl = _random_timeline(rng, len(frame), 6, 60, frame.index)
    data = SymbolData("R", {"5m": frame}, zones=tl)
    mixed = False
    for what, role, tol, family in (("near", "any", 0.5, "all"), ("near", "demand", 0.3, "zones"), ("role", "any", 1.0, "all"),
                                    ("state", "supply", 2.0, "levels"), ("strength", "any", 0.8, "all"), ("strength", "demand", 0.0, "all")):
        got = zone_gate.zone_series(data, frame, "5m", what, role, tol, family).tolist()
        want = _per_bar(tl, frame, "5m", what, role, tol, family)
        same = [(g == w) or (g is None and w is None) or (isinstance(g, float) and np.isnan(g) and w is None) for g, w in zip(got, want)]
        assert all(same), (what, role, tol, family, [i for i, ok in enumerate(same) if not ok][:5])
        mixed |= what == "near" and any(got) and not all(got)
    assert mixed                                                       # a parity check on all-True proves little


def test_vectorised_zone_series_are_fast():
    rng = random.Random(7)
    frame = pd.concat([_bars([D1, D2, D3]), _bars([D1 + timedelta(days=7 * k) for k in range(1, 40)])]).sort_index()
    frame = frame[~frame.index.duplicated()]
    tl = _random_timeline(rng, len(frame), 30, 300, frame.index)
    data = SymbolData("R", {"5m": frame}, zones=tl)
    started = time.perf_counter()
    for _ in range(3):
        zone_gate.zone_series(data, frame, "5m", "strength", "any", 1.0, "all")
    vectorised = (time.perf_counter() - started) / 3
    sample = frame.iloc[:300]
    started = time.perf_counter()
    _per_bar(tl, sample, "5m", "strength", "any", 1.0, "all")
    per_bar = (time.perf_counter() - started) / len(sample) * len(frame)              # the old loop, scaled to the frame
    assert len(frame) > 3000 and vectorised < 0.5, vectorised                       # ~3000 bars x 300 zones x 30 snapshots
    assert per_bar > 10 * vectorised, (per_bar, vectorised)


def test_screen_run_evaluates_off_the_event_loop(monkeypatch):
    """F2: /run hands the CPU-bound evaluation to a worker thread."""
    from app.screener import routes
    used = []
    real = asyncio.to_thread

    async def spy(fn, *a, **kw):
        used.append(fn.__name__)
        return await real(fn, *a, **kw)
    monkeypatch.setattr(routes.asyncio, "to_thread", spy)
    _set(screener_v2=True, evidence_charts=False)        # the evidence packs (U8 EV1) would use a worker thread too
    headers, _ = _owner("z7a3perf-thread@example.com")
    frame = _bars([D1])

    async def fake_fetch(session, tenant_id, symbols, exchange, base_tf, lookback, now=None, include_forming=False, **_kw):
        return [SymbolData(s, {base_tf: frame}) for s in symbols], {}, "broker:fake"
    monkeypatch.setattr(routes, "fetch_frames", fake_fetch)
    from tests.test_auth_api import client
    r = client.post("/api/screener/run", json={"source": "close > 1", "symbols": ["ABC"], "base_tf": "5m"}, headers=headers)
    assert r.status_code == 200, r.text
    assert used == ["run_screen", "stage_survivors"]


def test_chart_layer_says_why_there_are_no_bands():
    from tests.test_auth_api import client
    _set(market_zones=True)
    headers, _ = _owner("z7a3perf-layer@example.com")
    _run(_write_run(KEY, ist(D1, 15, 30), [_zrow("far", "DEMAND_ZONE", 50.0, 51.0)], price=100.0))
    url = "/api/charts/layers/market-zones"
    far = client.get(url, params={"instrument": KEY, "at": ist(D2, 9, 0).isoformat()}, headers=headers).json()
    assert far["bands"] == [] and far["live_count"] == 1 and "within 10 %" in far["reason"]
    none = client.get(url, params={"instrument": "NSE:ZNOTHING"}, headers=headers).json()
    assert none["bands"] == [] and none["reason"] == "no zones for this instrument"
    ok = client.get(url, params={"instrument": KEY, "at": ist(D2, 9, 0).isoformat(), "price": 51.2}, headers=headers).json()
    assert len(ok["bands"]) == 1 and ok["reason"] is None


def test_evaluate_uses_the_batched_timeline_end_to_end():
    _many([KEY], 4)
    frame = _bars([D1, D2])
    tl = _timelines([KEY], None, ist(D1, 9, 0), ist(D2, 15, 30))[KEY]
    got = evaluate(parse('ZoneStrength("supply", 1.0)'), SymbolData("ZCHARTA", {"5m": frame}, zones=tl), base_tf="5m")
    assert got.notna().any()
