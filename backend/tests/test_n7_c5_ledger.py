"""N7 C5: /api/backtest runs in the research trial ledger, deflated for the search; lake runs behind the sealed holdout.

Flag `backtest_ledger` (off by default): off, /api/backtest is unchanged. On, each logged-in run is a `backtest` trial
of its organisation and the response carries `deflation` (n_trials over the organisation's last LEDGER_DAYS days only);
`lake` reads the NSE bhavcopy day bars on the server and refuses a window that reaches the sealed holdout. A lake run
is its own as-of view: bars and corporate actions added after its last day never change it."""
import asyncio
import json
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.backtest import ledger as L
from app.backtest.repro import result_hash
from app.db.models import MdCandleRecord, MdCorporateActionRecord, ResearchTrialRecord
from app.market_lake.bhavcopy_backfill import SOURCE, bar_end
from app.platform.controls import DEFAULT_OFF_FLAGS
from tests.test_auth_api import _session_factory, client
from tests.test_deployments_api import _auth
from tests.utils import make_series, noisy_uptrend
from tests.test_n1_e1_execution_core import _flag

START = date(2024, 1, 1)


def _candles(days=4):
    """One-minute bars over several sessions (a trial needs at least two daily returns)."""
    out = []
    for k in range(days):
        df = make_series(noisy_uptrend(n=300, seed=7 + k, start=100.0 + k), start=f"2024-01-{2 + k:02d} 09:15")
        out += [{"timestamp": ts.isoformat(), "open": float(r.open), "high": float(r.high), "low": float(r.low), "close": float(r.close),
                 "volume": float(r.volume)} for ts, r in df.iterrows()]
    return out


def _run(coro):
    return asyncio.run(coro)


def _me(headers):
    return client.get("/api/auth/me", headers=headers).json()


def _org(tag, on=True):
    headers = _auth(f"n7c5-{tag}-{uuid.uuid4().hex[:6]}@example.com")
    me = _me(headers)
    if on:
        _flag(L.FLAG, me["tenant_id"])
    return headers, me["tenant_id"]


def _sessions(n, start=START):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _seed(*rows):
    async def go():
        async with _session_factory() as session:
            session.add_all(rows)
            await session.commit()
    _run(go())


def _lake(symbol, days, price=lambda i: 100.0 + i * 0.5 + (3.0 if i % 7 == 0 else 0.0)):
    _seed(*[MdCandleRecord(instrument_key=f"NSE:{symbol}", timeframe="day", ts=bar_end(d), source=SOURCE["cm"], version=1,
                           open=price(i) - 0.5, high=price(i) + 1.0, low=price(i) - 1.5, close=price(i), volume=1000 + i)
            for i, d in enumerate(days)])


def _trials(tenant_id):
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(ResearchTrialRecord).where(ResearchTrialRecord.tenant_id == tenant_id)))
    return [r for r in _run(go()) if json.loads(r.dsl_json).get("kind") == L.KIND]


BODY = {"strategy_id": "ema_rsi_scalper_1m", "symbol": "nifty", "base_timeframe": "1min", "data_source": "sample"}


def test_the_flag_is_off_by_default_and_off_changes_nothing():
    assert L.FLAG in DEFAULT_OFF_FLAGS
    headers, tenant = _org("off", on=False)
    r = client.post("/api/backtest", headers=headers, json={**BODY, "candles": _candles()})
    assert r.status_code == 200, r.text
    assert r.json()["deflation"] is None and _trials(tenant) == []
    runs = client.get("/api/backtests", headers=headers).json()
    assert runs[0]["data_source"] == "sample"                                         # labels as before while off
    lake = client.post("/api/backtest", headers=headers, json={**BODY, "base_timeframe": "day", "lake": {"start": "2024-01-01", "end": "2024-03-01"}})
    assert lake.status_code == 403 and L.FLAG in lake.text
    assert client.post("/api/backtest", headers=headers, json=BODY).status_code == 422   # neither candles nor a lake window


def test_each_run_is_a_trial_deflated_for_this_organisation_s_search_only():
    headers, tenant = _org("on")
    first = client.post("/api/backtest", headers=headers, json={**BODY, "candles": _candles(), "data_source": "lake:claimed"})
    assert first.status_code == 200, first.text
    d1 = first.json()["deflation"]
    assert d1["recorded"] and d1["n_trials"] == 1 and d1["data_source"] == "uploaded" and "Deflated for 1 backtests" in d1["note"]
    second = client.post("/api/backtest", headers=headers, json={**BODY, "candles": _candles(), "strategy_params": {"fast": 5}})
    d2 = second.json()["deflation"]
    assert d2["n_trials"] == 2 and d2["sr0"] >= 0
    rows = _trials(tenant)
    assert len(rows) == 2 and {json.loads(r.dsl_json)["data_source"] for r in rows} == {"uploaded"}   # a posted label is not trusted
    assert json.loads(rows[1].dsl_json)["params"] == {"fast": 5} and json.loads(rows[0].metrics_json)["run_id"] == first.json()["run_id"]
    assert client.get("/api/backtests", headers=headers).json()[0]["data_source"] == "uploaded"
    # a strategy parameter that spells the kind is not a trial of that kind (the LIKE is only a pre-filter)
    sneaky = client.post("/api/backtest", headers=headers, json={**BODY, "candles": _candles(), "strategy_params": {"x": '"kind": "backtest"'}})
    assert sneaky.json()["deflation"]["n_trials"] == 3
    # BT1b: another organisation's search is not this one's
    other, _ = _org("other")
    assert client.post("/api/backtest", headers=other, json={**BODY, "candles": _candles()}).json()["deflation"]["n_trials"] == 1


def test_a_lake_run_reads_the_server_s_bars_and_is_recorded_as_lake():
    headers, tenant = _org("lake")
    sym = f"C5{uuid.uuid4().hex[:6].upper()}"
    days = _sessions(120)
    _lake(sym, days)
    body = {**BODY, "symbol": sym, "base_timeframe": "day", "lake": {"start": str(days[0]), "end": str(days[-1])}}
    r = client.post("/api/backtest", headers=headers, json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["deflation"]["data_source"] == "lake:nse_bhav:cm:adjusted" and out["deflation"]["recorded"]
    run = client.get("/api/backtests", headers=headers).json()[0]
    assert run["data_source"] == "lake:nse_bhav:cm:adjusted" and run["bars"] == 120 and run["data_from"].startswith(str(days[0]))
    assert client.post("/api/backtest", headers=headers, json={**body, "candles": _candles()}).status_code == 422      # not both
    assert client.post("/api/backtest", headers=headers, json={**body, "base_timeframe": "5min"}).status_code == 422  # day bars only
    bse = client.post("/api/backtest", headers=headers, json={**body, "lake": {**body["lake"], "exchange": "BSE"}})
    assert bse.status_code == 422
    empty = client.post("/api/backtest", headers=headers, json={**body, "symbol": "NOSUCHC5"})
    assert empty.status_code == 422 and "0 NSE day bar(s)" in empty.text


def test_a_lake_window_that_reaches_the_sealed_holdout_is_refused(monkeypatch):
    headers, _ = _org("holdout")
    sym = f"H5{uuid.uuid4().hex[:6].upper()}"
    days = _sessions(80)
    _lake(sym, days)
    monkeypatch.setenv("BACKTEST_HOLDOUT_START", str(days[60]))
    body = {**BODY, "symbol": sym, "base_timeframe": "day"}
    sealed = client.post("/api/backtest", headers=headers, json={**body, "lake": {"start": str(days[0]), "end": str(days[-1])}})
    assert sealed.status_code == 422 and "sealed holdout" in sealed.text                # refused, never cut silently
    on_the_day = client.post("/api/backtest", headers=headers, json={**body, "lake": {"start": str(days[0]), "end": str(days[60])}})
    assert on_the_day.status_code == 422
    before = client.post("/api/backtest", headers=headers, json={**body, "lake": {"start": str(days[0]), "end": str(days[59])}})
    assert before.status_code == 200, before.text
    L.check_holdout(L.LakeWindow(start=days[0], end=days[59]))
    with pytest.raises(L.HoldoutError):
        L.check_holdout(L.LakeWindow(start=days[61], end=days[70]))
    # review of C3 (LOW): a boundary during the last day (at its 09:15 IST bar) refuses that day; just after it, too
    for boundary in (f"{days[59]}T09:15:00+05:30", f"{days[59]}T09:00:00+05:30"):
        monkeypatch.setenv("BACKTEST_HOLDOUT_START", boundary)
        with pytest.raises(L.HoldoutError):
            L.check_holdout(L.LakeWindow(start=days[0], end=days[59]))
    monkeypatch.setenv("BACKTEST_HOLDOUT_START", f"{days[59]}T09:16:00+05:30")
    L.check_holdout(L.LakeWindow(start=days[0], end=days[59]))


def test_a_lake_run_never_changes_when_later_bars_or_actions_arrive():
    """No look-ahead: the bars of a window are the same after the lake gains later bars and a split whose ex-date is after
    the window - the run is its own as-of view (a split inside the window is adjusted, tested too)."""
    sym = f"T5{uuid.uuid4().hex[:6].upper()}"
    days = _sessions(90)
    _lake(sym, days[:60])
    window = L.LakeWindow(start=days[0], end=days[50])

    async def bars(w):
        async with _session_factory() as session:
            return [b.model_dump() for b in await L.lake_candles(session, sym, "day", w)]
    before = _run(bars(window))
    _lake(sym, days[60:], price=lambda i: 500.0 + i)
    _seed(MdCorporateActionRecord(symbol=sym, exchange="NSE", ex_date=days[70], action="SPLIT", ratio_new=2.0, ratio_old=1.0, source="t"))
    assert _run(bars(window)) == before
    longer = _run(bars(L.LakeWindow(start=days[0], end=days[80])))
    assert longer[0]["close"] == pytest.approx(before[0]["close"] / 2)                # the split inside the longer window: adjusted
    assert _run(bars(L.LakeWindow(start=days[0], end=days[50], adjusted=False))) == before


def test_deflation_never_enters_the_result_hash():
    from app.backtest.engine import run_backtest
    from app.core.models import RiskConfig, bars_to_dataframe
    from app.core.models import OHLCVBar
    from app.strategy_engine.registry import registry
    strategy = registry.get("ema_rsi_scalper_1m")
    df = bars_to_dataframe([OHLCVBar(**c) for c in _candles()])
    result = run_backtest(strategy, df, "NIFTY", "1min", RiskConfig())
    plain = result_hash(result)
    result.deflation = {"dsr": 0.4, "n_trials": 3}
    assert result_hash(result) == plain
