"""N4 CH6: ScreenQL studies on charts (GET /api/charts/studies), computed by the screener's own runtime.

* parity: every value is `runtime.evaluate`'s on the same bars, and a condition study fires where a screen of the same
  condition matches (`run_screen`, the screener / alert / backtest path);
* no look-ahead: truncation invariance over every ScreenQL series expression of the Z10-a harness (the study over
  bars[:n] equals the study over all bars cut at n, at 15 cuts) and, through the API, later bars added or altered
  (higher versions, another source) leave the answer at `at` byte-identical;
* the warm-up history before `start` is read, so the first drawn value is a full one;
* each study fails on its own (syntax, cross-sectional, last-bar-only, missing data); request caps; flag per
  organisation, off by default; shared-licensed sources only (the CH3 loader);
* performance: 50,000 bars of six studies in under a second (scaled by the Z10-d CI factor in the perf job; a loose
  bound in the normal suite);
* the frontend's parity fixture is current and byte-identical.
Synthetic bars only - no broker, no network, no NIFTY data. Nothing here touches an order.
"""
import asyncio
import os
import sys
import time
import uuid
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import delete

from app.charts import studies as S
from app.db.models import MdCandleRecord
from app.platform import controls
from app.quality.causal_cases import SCREENQL_SERIES
from tests.ch3_pa_cases import frame
from tests.ch6_study_cases import FRONTEND_PATH, PATH, cases, dumps
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner

KEY = "NSE:CH6STUDYA"
URL = "/api/charts/studies"
F = frame().assign(volume=lambda d: 1000.0 + (d.index % 7) * 100.0)


def _run(coro):
    return asyncio.run(coro)


def _rows(f, source="vendor:test", version=1):
    return [MdCandleRecord(instrument_key=KEY, timeframe="15m", ts=pd.Timestamp(b.bar_end).to_pydatetime(), source=source, version=version,
                           open=float(b.open), high=float(b.high), low=float(b.low), close=float(b.close), volume=int(b.volume),
                           ingested_at=pd.Timestamp(b.bar_end).to_pydatetime() + timedelta(seconds=30)) for b in f.itertuples()]


async def _put(rows):
    async with _session_factory() as session:
        session.add_all(rows)
        await session.commit()


async def _drop(cond=None):
    async with _session_factory() as session:
        q = delete(MdCandleRecord).where(MdCandleRecord.instrument_key == KEY)
        await session.execute(q if cond is None else q.where(cond))
        await session.commit()


@pytest.fixture(scope="module", autouse=True)
def _lake():
    _run(_drop())
    _run(_put(_rows(F)))
    yield
    _run(_drop())


def _flag(on, tenants=()):
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            stored = dict(current.get("flags") or {})
            stored[S.FLAG] = {"on": on, "tenants": list(tenants)}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": stored}, None)
            await session.commit()
    _run(go())


@pytest.fixture(autouse=True)
def _fresh():
    from app.charts import price_action_layer as PAL
    from app.core import rate_limit
    rate_limit.reset("chart_studies")
    PAL.cache_clear()
    yield


@pytest.fixture
def headers():
    _flag(True)
    yield _owner(f"ch6-{uuid.uuid4().hex[:10]}@example.com")[0]
    _flag(False)


def _iso(t):
    return pd.Timestamp(t).tz_convert("UTC").isoformat()


def _get(headers, exprs, **params):
    r = client.get(URL, params={"instrument": KEY, "tf": "15m", "expr": exprs, **params}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _runtime_series(expr, f):
    from app.screener import parse
    from app.screener.runtime import SymbolData, evaluate
    return evaluate(parse(expr), SymbolData("X", {"15m": S._runtime_frame(f)}, exchange="NSE"), base_tf="15m")


def test_flag_off_by_default_and_per_organisation():
    assert S.FLAG in controls.DEFAULT_OFF_FLAGS and "places no orders" in controls.FEATURE_FLAGS[S.FLAG]
    headers, me = _owner(f"ch6-flag-{uuid.uuid4().hex[:10]}@example.com")
    other, _ = _owner(f"ch6-flag-b-{uuid.uuid4().hex[:10]}@example.com")
    params = {"instrument": KEY, "expr": "SMA(close, 20)", "at": _iso(F["bar_end"].iloc[-1]), "start": _iso(F["bar_end"].iloc[100])}
    assert client.get(URL, params=params, headers=headers).status_code == 503
    _flag(False, tenants=[me["tenant_id"]])
    try:
        assert client.get(URL, params=params, headers=headers).status_code == 200
        assert client.get(URL, params=params, headers=other).status_code == 503
    finally:
        _flag(False)


def test_values_are_the_screener_runtime_and_conditions_fire_where_screens_match(headers):
    at, start = F["bar_end"].iloc[-1], F["bar_end"].iloc[120]
    exprs = ["SMA(close, 20)", "RSI(14)", "close > SMA(close, 50)", "ATR(14)", "EMA(close, 5)@1d"]
    body = _get(headers, exprs, at=_iso(at), start=_iso(start))
    assert body["source"] == "lake:15m" and body["schema"] == "study/1" and body["reason"] is None
    shown = F[F["bar_end"] > start]
    assert body["bars"] == len(shown) and body["warmup_bars"] > 0                     # the look-back was read before `start`
    for st in body["studies"]:
        assert st["ok"], st
        want = _runtime_series(st["expr"], F).reindex(S._runtime_frame(F).index)
        want = want.iloc[len(F) - len(shown):]
        if st["kind"] == "num":
            got = st["values"]
            assert body["times"] == [int(pd.Timestamp(t).timestamp()) for t in shown["timestamp"]]
            assert got == [None if not np.isfinite(v) else float(v) for v in want.astype(float)], st["expr"]
            if "@" not in st["expr"]:
                assert got[0] is not None                                           # warmed up (the fixture has no days before it)
        else:
            flags = want.astype(bool).to_numpy()
            prev = bool(_runtime_series(st["expr"], F).iloc[len(F) - len(shown) - 1])
            fires = [int(pd.Timestamp(t).timestamp()) for k, t in enumerate(shown["timestamp"]) if flags[k] and not (flags[k - 1] if k else prev)]
            assert st["fires"] == fires and st["true_bars"] == int(flags.sum())
    panes = {st["expr"]: st["pane"] for st in body["studies"]}
    assert panes == {"SMA(close, 20)": "price", "RSI(14)": "own", "close > SMA(close, 50)": "own", "ATR(14)": "own", "EMA(close, 5)@1d": "price"}
    # the screen of the same condition, run on the bars up to each fire, matches there (screener = study)
    from app.screener import compile_screen
    from app.screener.runtime import SymbolData, run_screen
    ast, validated = compile_screen("close > SMA(close, 50)", base_tf="15m")
    cond = next(st for st in body["studies"] if st["kind"] == "bool")
    for t in cond["fires"][:3]:
        upto = F[F["timestamp"].apply(lambda x: int(pd.Timestamp(x).timestamp())) <= t]
        match = run_screen(ast, validated, [SymbolData("X", {"15m": S._runtime_frame(upto)}, exchange="NSE")], base_tf="15m")[0]
        assert match.matched, t


def test_truncation_invariance_over_the_harness_expressions():
    """Every ScreenQL series expression of the Z10-a harness, as a study: over bars[:n] it equals the study over all bars
    cut at n - nothing from a later bar reaches an earlier value."""
    exprs = sorted({e for es in SCREENQL_SERIES.values() for e in es})
    checked = [c for c in S.check(exprs, "15m") if c["ok"]]
    assert len(checked) >= 40
    full = S.compute(F, "15m", checked, None)
    usable = [i for i, st in enumerate(full["studies"]) if st["ok"]]
    assert len(usable) >= 35, [st["problems"] for st in full["studies"] if not st["ok"]][:5]
    for n in list(range(60, len(F), 53)) + [len(F) - 1]:
        part = S.compute(F.iloc[:n], "15m", checked, None)
        for i in usable:
            a, b = part["studies"][i], full["studies"][i]
            if not a["ok"]:
                continue                                                             # too few bars for this study yet
            if b["kind"] == "num":
                assert a["values"] == b["values"][:n], (b["expr"], n)
            else:
                cut = int(pd.Timestamp(F["timestamp"].iloc[n - 1]).timestamp())
                assert a["fires"] == [t for t in b["fires"] if t <= cut], (b["expr"], n)


def test_no_lookahead_through_the_api(headers):
    at = F["bar_end"].iloc[400]
    params = {"at": _iso(at), "start": _iso(F["bar_end"].iloc[200])}
    exprs = ["SMA(close, 20)", "close > EMA(close, 9)", "RSI(14)@1h", "Max(high, 30)"]
    before = _get(headers, exprs, **params)
    # review LOW-1: a one-bar over-read would show here - the last bar drawn ended by `at`, and it started before it
    assert pd.Timestamp(before["as_of"]) <= pd.Timestamp(at) and before["times"][-1] < pd.Timestamp(at).timestamp()
    assert pd.Timestamp(before["as_of"]) == pd.Timestamp(at)                       # ... and the bar that ended at `at` is read
    later = F[F["bar_end"] > at]
    tail = F.tail(40).assign(timestamp=F.tail(40)["timestamp"] + pd.Timedelta(days=60), bar_end=F.tail(40)["bar_end"] + pd.Timedelta(days=60))
    _run(_put(_rows(later.assign(high=later["high"] * 1.05, close=later["close"] * 1.02), version=2) + _rows(later.assign(low=later["low"] * 0.9), source="r2:other")
              + _rows(tail) + _rows(later.assign(close=later["close"] * 2), source="stream:upstox", version=9)))
    try:
        from app.charts import price_action_layer as PAL
        PAL.cache_clear()                                                          # recomputed, not the cached answer
        assert _get(headers, exprs, **params) == before
    finally:
        _run(_drop((MdCandleRecord.version > 1) | (MdCandleRecord.source != "vendor:test") | (MdCandleRecord.ts > F["bar_end"].iloc[-1].to_pydatetime())))
    from app.charts import price_action_layer as PAL
    PAL.cache_clear()
    assert _get(headers, exprs, **params) == before


def test_each_study_fails_on_its_own_and_requests_are_capped(headers):
    params = {"at": _iso(F["bar_end"].iloc[-1]), "start": _iso(F["bar_end"].iloc[300])}
    body = _get(headers, ["SMA(close, 20)", "SMA(close,", "Rank(close)", "Sector() == \"IT\"", "close > 1 AND", "PCR() > 1"], **params)
    ok = {st["expr"]: (st["ok"], " ".join(p["message"] for p in st["problems"])) for st in body["studies"]}
    assert ok["SMA(close, 20)"][0] and not any(v[0] for k, v in ok.items() if k != "SMA(close, 20)")
    assert "universe" in ok["Rank(close)"][1] and "last bar" in ok["Sector() == \"IT\""][1]
    assert "option chain" in ok["PCR() > 1"][1] or "last bar" in ok["PCR() > 1"][1]
    too_many = client.get(URL, params={"instrument": KEY, "expr": ["close"] * (S.MAX_STUDIES + 1), **params}, headers=headers)
    assert too_many.status_code == 422
    assert client.get(URL, params={"instrument": KEY, "expr": "close + " * 60 + "close", **params}, headers=headers).status_code == 422
    assert client.get(URL, params={"instrument": KEY, "expr": "close", "at": "1900-01-01T00:00:00+00:00"}, headers=headers).status_code == 422
    far = _iso(F["bar_end"].iloc[-1] - pd.Timedelta(days=S.STUDY_MAX_DAYS["15m"] + 1))
    assert client.get(URL, params={"instrument": KEY, "expr": "close", "at": params["at"], "start": far}, headers=headers).status_code == 422
    empty = _get(headers, ["SMA(close, 20)"], instrument="NFO:BANKNIFTY 26OCT 52000 CE", **params)
    assert empty["bars"] == 0 and empty["reason"] and empty["studies"][0].get("values") is None


def test_parity_fixture_is_current_and_byte_identical():
    assert PATH.read_text(encoding="utf-8") == dumps(cases()), "python -m tests.ch6_study_cases --write (after a deliberate change only)"
    assert FRONTEND_PATH.read_bytes() == PATH.read_bytes()


def _big(n: int) -> pd.DataFrame:
    rng = np.random.default_rng(6)
    close = 20_000 + np.cumsum(rng.normal(0, 12, n))
    start = pd.Timestamp("2020-01-01 03:45", tz="UTC") + pd.to_timedelta(np.arange(n) * 15, unit="min")
    spread = np.abs(rng.normal(0, 8, n)) + 1
    return pd.DataFrame({"timestamp": start, "bar_end": start + pd.Timedelta(minutes=15), "open": close + rng.normal(0, 3, n),
                         "high": close + spread, "low": close - spread, "close": close, "volume": rng.integers(100, 10_000, n).astype(float)})


PERF_EXPRS = ["SMA(close, 20)", "EMA(close, 50)", "RSI(14)", "ATR(14)", "close > SMA(close, 200)", "Max(high, 20) - Min(low, 20)"]


def _timed(n: int) -> float:
    f = _big(n)
    checked = S.check(PERF_EXPRS, "15m")
    best = float("inf")
    for _ in range(3):
        t0 = time.perf_counter()
        out = S.build(f, "15m", checked, None)
        S.canonical(out)
        best = min(best, time.perf_counter() - t0)
    assert all(st["ok"] for st in out["studies"]) and out["bars"] == n
    return best


def test_fifty_thousand_bars_in_reasonable_time():
    """Normal suite: a loose bound that still catches an accidental per-bar Python loop (the perf job holds 1 s)."""
    assert _timed(50_000) < 5.0


@pytest.mark.skipif(os.environ.get("ATP_PERF") != "1", reason="performance job only (ATP_PERF=1)")
def test_fifty_thousand_bars_under_a_second_perf_job():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import bench_screener as bench
    factor = bench.ci_factor()
    took = _timed(50_000)
    print(f"ch6 studies: 50,000 bars x {len(PERF_EXPRS)} studies + JSON in {took:.3f} s (budget {factor:.2f} s)")
    assert took < 1.0 * factor


# --------------------------------------------------------------------------------------------- CH6a review fixes
HEAVY = ('RealBreak(SwingHigh(1), "above", 20) OR ReversalAt(SwingLow(1), "bullish") OR RealBreak(SwingLow(2), "below", 30) '
         'OR ReversalAt(SwingHigh(2), "bearish") OR RealBreak(SwingHigh(0), "above", 10) OR ReversalAt(SwingLow(0), "bullish") '
         'OR RealBreak(SwingLow(1), "below", 15)')


def _flags(**flags):
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            stored = dict(current.get("flags") or {})
            for name, on in flags.items():
                stored[name] = {"on": on, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": stored}, None)
            await session.commit()
    _run(go())


def test_heavy_studies_are_budgeted_per_request_and_refused_on_long_windows():
    """Review HIGH: six ~290-character RealBreak / ReversalAt studies took 8 s at 5k bars and 44 s at 20k. The budget
    admits what fits (about 4 s), refuses the rest as "too heavy for this window", and refuses the bar-by-bar functions
    above HEAVY_MAX_BARS; what is admitted stays inside the bound."""
    assert 250 <= len(HEAVY) <= S.MAX_EXPR_CHARS
    checked = S.check([HEAVY] * 6, "15m")
    assert all(st["ok"] for st in checked), checked[0]["problems"]
    assert set(S.heavy_calls(checked[0]["ast"])) == {"RealBreak", "ReversalAt"}
    single = S.check(['ReversalAt(SwingLow(0), "bullish")'] * 6, "15m")
    for studies, n, bound in ((checked, 5_000, 6.0), (checked, 20_000, 2.0), (single, 20_000, 6.0), (checked, 50_000, 2.0)):
        admitted = S.admit(studies, n)
        took = [st for st in admitted if st["ok"]]
        refused = [st for st in admitted if not st["ok"]]
        assert refused and all("too heavy for this window" in st["problems"][-1]["message"] for st in refused)
        assert sum(S.weight(st["ast"]) for st in took) * n / 1000 <= S.REQUEST_BUDGET
        if n > S.HEAVY_MAX_BARS:
            assert not took and "bar by bar" in refused[0]["problems"][-1]["message"]
        elif studies is checked and n == 20_000:
            assert not took and "s of computation" in refused[0]["problems"][-1]["message"]   # one alone is over the budget
        else:
            assert 1 <= len(took) < 6
        f = _big(n)
        t0 = time.perf_counter()
        out = S.build(f, "15m", admitted, None)
        took_s = time.perf_counter() - t0
        assert [st["ok"] for st in out["studies"]] == [st["ok"] for st in admitted]
        assert took_s < bound, (n, len(took), took_s)
    # light studies are not held back by the budget at the 50k-bar target
    assert all(st["ok"] for st in S.admit(S.check(PERF_EXPRS, "15m"), 52_000))


def test_api_refuses_only_the_studies_over_the_budget(headers, monkeypatch):
    params = {"at": _iso(F["bar_end"].iloc[-1]), "start": _iso(F["bar_end"].iloc[300])}
    monkeypatch.setattr(S, "REQUEST_BUDGET", 600.0)
    body = _get(headers, ["SMA(close, 20)", HEAVY, HEAVY, "RSI(14)"], **params)
    ok = [st["ok"] for st in body["studies"]]
    assert ok == [True, True, False, True], [st["problems"] for st in body["studies"]]
    assert "too heavy for this window" in body["studies"][2]["problems"][-1]["message"]
    assert body["studies"][3]["values"] and body["studies"][1]["fires"] is not None


def test_sixty_requests_a_minute(headers):
    params = {"instrument": KEY, "tf": "15m", "expr": "SMA(close, 20)", "at": _iso(F["bar_end"].iloc[-1]), "start": _iso(F["bar_end"].iloc[600])}
    codes = [client.get(URL, params=params, headers=headers).status_code for _ in range(61)]
    assert codes[:60] == [200] * 60 and codes[60] == 429


def test_answers_are_cached_and_computed_inside_the_build_semaphore(headers, monkeypatch):
    from app.charts import price_action_layer as PAL
    calls = []
    real = S.build

    def counted(*a, **k):
        calls.append(1)
        return real(*a, **k)
    monkeypatch.setattr(S, "build", counted)
    slots = []
    real_slot = PAL._in_slot

    async def in_slot(session, owner, fn, *a):
        slots.append(fn)
        return await real_slot(session, owner, fn, *a)
    monkeypatch.setattr(PAL, "_in_slot", in_slot)
    params = {"at": _iso(F["bar_end"].iloc[-1]), "start": _iso(F["bar_end"].iloc[300])}
    a = _get(headers, ["SMA(close, 20)", "RSI(14)"], **params)
    b = _get(headers, ["SMA(close, 20)", "RSI(14)"], **params)
    assert a == b and len(calls) == 1 and slots == [counted]
    _get(headers, ["SMA(close, 21)"], **params)
    assert len(calls) == 2


def test_screener_function_flags_apply_per_study(headers):
    """Review MED: RelVolume / SessionVWAP (U6 breakouts) and the market-zone functions follow their screener flags."""
    params = {"at": _iso(F["bar_end"].iloc[-1]), "start": _iso(F["bar_end"].iloc[300])}
    _flags(screener_breakouts=False, market_zones=False)
    body = _get(headers, ["RelVolume(20)", "SessionVWAP()", "SMA(close, 20)", 'NearZone("demand", 0.5)'], **params)
    by = {st["expr"]: st for st in body["studies"]}
    for e in ("RelVolume(20)", "SessionVWAP()"):
        assert not by[e]["ok"] and "screener_breakouts" in by[e]["problems"][-1]["message"] and by[e].get("values") is None
    assert not by['NearZone("demand", 0.5)']["ok"] and "market_zones" in " ".join(p["message"] for p in by['NearZone("demand", 0.5)']["problems"])
    assert by["SMA(close, 20)"]["ok"] and by["SMA(close, 20)"]["values"]
    _flags(screener_breakouts=True)
    try:
        body = _get(headers, ["RelVolume(20)", "SessionVWAP()"], **params)
        assert all(st["ok"] for st in body["studies"]), [st["problems"] for st in body["studies"]]
    finally:
        _flags(screener_breakouts=False)


KEY_1M = "NSE:CH6STUDYM"


def test_a_warmup_over_the_rebuild_cap_refuses_that_study_only(headers):
    """Review LOW-3: 5m is rebuilt from 1-minute rows for at most REBUILD_MAX_DAYS days. A study whose own warm-up needs
    more is refused with the reason; the others still draw."""
    from app.charts import price_action_layer as PAL
    day = pd.Timestamp("2026-04-09 03:45", tz="UTC")
    starts = day + pd.to_timedelta(np.arange(375), unit="min")
    rng = np.random.default_rng(3)
    close = 500 + np.cumsum(rng.normal(0, 0.2, 375))
    rows = [MdCandleRecord(instrument_key=KEY_1M, timeframe="1m", ts=(t + pd.Timedelta(minutes=1)).to_pydatetime(), source="vendor:test", version=1,
                           open=float(c), high=float(c) + 0.3, low=float(c) - 0.3, close=float(c), volume=100,
                           ingested_at=(t + pd.Timedelta(minutes=2)).to_pydatetime()) for t, c in zip(starts, close)]
    _run(_put(rows))
    try:
        at = starts[-1] + pd.Timedelta(minutes=1)
        params = {"instrument": KEY_1M, "tf": "5m", "at": _iso(at), "start": _iso(at - pd.Timedelta(days=PAL.REBUILD_MAX_DAYS - 20))}
        r = client.get(URL, params={**params, "expr": ["SMA(close, 5)", "SMA(close, 150)@1d"]}, headers=headers)
        assert r.status_code == 200, r.text
        light, long_ = r.json()["studies"]
        assert light["ok"] and any(v is not None for v in light["values"])
        assert not long_["ok"] and "warm-up" in long_["problems"][-1]["message"] and "1-minute" in long_["problems"][-1]["message"]
        # a window too large on its own still refuses the request
        far = {**params, "start": _iso(at - pd.Timedelta(days=PAL.REBUILD_MAX_DAYS + 30))}
        assert client.get(URL, params={**far, "expr": "SMA(close, 5)"}, headers=headers).status_code == 422
    finally:
        async def drop():
            async with _session_factory() as session:
                await session.execute(delete(MdCandleRecord).where(MdCandleRecord.instrument_key == KEY_1M))
                await session.commit()
        _run(drop())


# --------------------------------------------------------------------------------------------- CH6a re-review: build slots
def _slot_engine(tmp_path):
    """A pooled engine (one connection, no overflow) so a test can count checked-out connections: CI's Postgres when
    Z5_TEST_PG_URL is set, else a SQLite file with the same queue pool."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    url = os.environ.get("Z5_TEST_PG_URL") or f"sqlite+aiosqlite:///{tmp_path / 'slots.db'}"
    if url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://"):]
    engine = create_async_engine(url, pool_size=1, max_overflow=0, pool_timeout=1)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def test_a_request_waiting_for_a_build_slot_holds_no_pool_connection(tmp_path, monkeypatch):
    from sqlalchemy import text

    from app.charts import price_action_layer as PAL
    monkeypatch.setattr(PAL, "SLOT_WAIT_SECONDS", 5.0)
    engine, factory = _slot_engine(tmp_path)

    async def go():
        sem = PAL._slots()
        for _ in range(PAL.BUILD_CONCURRENCY):                      # every build slot is taken
            await sem.acquire()
        session = factory()
        await session.execute(text("SELECT 1"))                     # the request read its bars: one connection out
        assert engine.pool.checkedout() == 1
        entered = asyncio.Event()

        async def request():
            async with PAL.build_slot(session, owner="u1"):
                entered.set()
        task = asyncio.create_task(request())
        await asyncio.sleep(0.2)
        assert not entered.is_set()                                 # still waiting for a slot ...
        assert engine.pool.checkedout() == 0                        # ... with its connection back in the pool
        async with factory() as other:                              # so another request can still use the one-connection pool
            assert (await other.execute(text("SELECT 1"))).scalar() == 1
        sem.release()
        await asyncio.wait_for(task, 2)
        assert entered.is_set()
        for _ in range(PAL.BUILD_CONCURRENCY - 1):
            sem.release()
        await engine.dispose()
    asyncio.run(go())


def test_the_wait_for_a_slot_is_bounded_and_answers_503_with_retry_after(monkeypatch):
    from fastapi import HTTPException

    from app.charts import price_action_layer as PAL
    monkeypatch.setattr(PAL, "SLOT_WAIT_SECONDS", 0.3)

    async def go():
        sem = PAL._slots()
        for _ in range(PAL.BUILD_CONCURRENCY):
            await sem.acquire()
        t0 = time.monotonic()
        with pytest.raises(HTTPException) as err:
            async with PAL.build_slot(None, owner="u2"):
                pass
        assert time.monotonic() - t0 < 1.5
        assert err.value.status_code == 503 and "busy, retry" in err.value.detail and err.value.headers["Retry-After"] == str(PAL.BUSY_RETRY_AFTER)
        for _ in range(PAL.BUILD_CONCURRENCY):
            sem.release()
        assert not PAL._USER_LOCKS[id(asyncio.get_running_loop())]   # nothing left behind for the user
    asyncio.run(go())


def test_one_build_in_flight_per_user(monkeypatch):
    from fastapi import HTTPException

    from app.charts import price_action_layer as PAL
    monkeypatch.setattr(PAL, "SLOT_WAIT_SECONDS", 0.5)

    async def go():
        sem = PAL._slots()
        release = asyncio.Event()
        order = []

        async def build(name, owner):
            async with PAL.build_slot(None, owner):
                order.append(name)
                await release.wait()
        first = asyncio.create_task(build("a1", "alice"))
        await asyncio.sleep(0.05)
        second = asyncio.create_task(build("a2", "alice"))
        bob = asyncio.create_task(build("b1", "bob"))
        await asyncio.sleep(0.1)
        assert order == ["a1", "b1"] and sem._value == PAL.BUILD_CONCURRENCY - 2      # alice holds one slot, never two
        with pytest.raises(HTTPException) as err:                    # alice's queued request gives up at the bound
            await second
        assert err.value.status_code == 503 and "previous chart computation" in err.value.detail
        release.set()
        await asyncio.gather(first, bob)
        third = asyncio.create_task(build("a3", "alice"))           # once her build is done, the next one runs
        await asyncio.wait_for(third, 1)
        assert order == ["a1", "b1", "a3"] and sem._value == PAL.BUILD_CONCURRENCY
    asyncio.run(go())


def test_both_routes_answer_busy_when_no_slot_frees_and_cached_answers_need_none(headers, monkeypatch):
    from app.charts import price_action_layer as PAL
    params = {"at": _iso(F["bar_end"].iloc[-1]), "start": _iso(F["bar_end"].iloc[300])}
    warm = _get(headers, ["SMA(close, 20)"], **params)                                  # cached now
    monkeypatch.setattr(PAL, "SLOT_WAIT_SECONDS", 0.2)
    monkeypatch.setattr(PAL, "BUILD_CONCURRENCY", 0)
    PAL._SLOTS.clear()
    try:
        busy = client.get(URL, params={"instrument": KEY, "tf": "15m", "expr": ["SMA(close, 21)"], **params}, headers=headers)
        assert busy.status_code == 503 and busy.headers["Retry-After"] == str(PAL.BUSY_RETRY_AFTER) and "busy, retry" in busy.text
        assert _get(headers, ["SMA(close, 20)"], **params) == warm                       # a cache hit takes no slot
        _flag_layer(True)
        layer = client.get("/api/charts/layers/price-action", params={"instrument": KEY, "tf": "15m", **params}, headers=headers)
        assert layer.status_code == 503 and layer.headers["Retry-After"] == str(PAL.BUSY_RETRY_AFTER) and "busy, retry" in layer.text
    finally:
        _flag_layer(False)
        PAL._SLOTS.clear()


def test_the_cache_key_carries_each_refusal_message(headers, monkeypatch):
    """Re-review LOW: two organisations whose flags refuse a study differently never share a cached answer."""
    params = {"at": _iso(F["bar_end"].iloc[-1]), "start": _iso(F["bar_end"].iloc[300])}
    keys = []
    from app.charts import price_action_layer as PAL
    real = PAL._cached

    def spy(key):
        keys.append(key)
        return real(key)
    monkeypatch.setattr(PAL, "_cached", spy)
    _get(headers, ["RelVolume(20)"], **params)
    studies_key = keys[-1][-1]
    assert studies_key[0][2] and "screener_breakouts" in studies_key[0][2][-1]         # the message itself, not a count


def _flag_layer(on):
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            stored = dict(current.get("flags") or {})
            stored["chart_price_action"] = {"on": on, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": stored}, None)
            await session.commit()
    _run(go())


# --------------------------------------------------------------------------------------------- CH6a re-review LOWs
def test_a_cancelled_request_keeps_its_slot_until_its_build_has_finished():
    """LOW-1: a worker thread cannot be stopped, so a cancelled request (the client went away) holds its slot and the
    user's lock until the thread is done - the builds running never exceed BUILD_CONCURRENCY, nor one per user."""
    import threading

    from app.charts import price_action_layer as PAL
    gate = threading.Event()
    started = threading.Event()

    def slow():
        started.set()
        gate.wait(5)
        return "done"

    async def go():
        sem = PAL._slots()
        req = asyncio.create_task(PAL._in_slot(None, "carol", slow))
        while not started.is_set():
            await asyncio.sleep(0.01)
        req.cancel()
        await asyncio.sleep(0.1)
        assert not req.done()                                            # still waiting for its thread ...
        assert sem._value == PAL.BUILD_CONCURRENCY - 1                   # ... holding its slot
        assert PAL._USER_LOCKS[id(asyncio.get_running_loop())]["carol"][0].locked()
        gate.set()
        with pytest.raises(asyncio.CancelledError):
            await req
        assert sem._value == PAL.BUILD_CONCURRENCY and "carol" not in PAL._USER_LOCKS[id(asyncio.get_running_loop())]
    asyncio.run(go())


def test_the_owner_is_required():
    """LOW-2: no shared "None" lock - every slot names its user."""
    import inspect

    from app.charts import price_action_layer as PAL
    param = inspect.signature(PAL.mtf_rows).parameters["owner"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY and param.default is inspect.Parameter.empty

    async def go():
        with pytest.raises(ValueError):
            async with PAL.build_slot(None, None):
                pass
    asyncio.run(go())
