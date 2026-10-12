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
    from app.core import rate_limit
    rate_limit.reset("chart_studies")
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
    later = F[F["bar_end"] > at]
    tail = F.tail(40).assign(timestamp=F.tail(40)["timestamp"] + pd.Timedelta(days=60), bar_end=F.tail(40)["bar_end"] + pd.Timedelta(days=60))
    _run(_put(_rows(later.assign(high=later["high"] * 1.05, close=later["close"] * 1.02), version=2) + _rows(later.assign(low=later["low"] * 0.9), source="r2:other")
              + _rows(tail) + _rows(later.assign(close=later["close"] * 2), source="stream:upstox", version=9)))
    try:
        assert _get(headers, exprs, **params) == before
    finally:
        _run(_drop((MdCandleRecord.version > 1) | (MdCandleRecord.source != "vendor:test") | (MdCandleRecord.ts > F["bar_end"].iloc[-1].to_pydatetime())))
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
