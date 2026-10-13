"""N7 C6: distributed backtest sweeps (flag backtest_distributed, off by default; lake reads need backtest_ledger).

A sweep (a parameter grid x symbols on the lake's day bars) is split into tasks in two Postgres tables; any number of
workers claim them (a conditional UPDATE - one wins), a dead worker's task is queued again, and the worker that ends the
last task merges and finalises. The merged result does not depend on how the tasks were split among workers (same
`result_hash`); every combination is a ledger trial and the best one is deflated for the whole grid. The API runs the
tasks itself (after answering) while BACKTEST_SWEEP_INLINE is on - no new infrastructure in Phase 0."""
import asyncio
import json
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from app.backtest import ledger
from app.backtest import sweeps as SW
from app.db.models import BacktestSweepRecord, BacktestSweepTaskRecord, MdCandleRecord, ResearchTrialRecord
from app.market_lake.bhavcopy_backfill import SOURCE, bar_end
from app.platform.controls import DEFAULT_OFF_FLAGS
from tests.test_auth_api import _session_factory, client
from tests.test_deployments_api import _auth
from tests.test_n1_e1_execution_core import _flag
from tests.utils import noisy_uptrend

STRATEGY = "swing_ema_pullback_d"
GRID = {"ema_fast": [10, 20], "ema_slow": [30, 50]}


def _run(coro):
    return asyncio.run(coro)


def _sessions(n, start=date(2023, 1, 2)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


DAYS = _sessions(260)


def _lake(symbol, seed):
    closes = noisy_uptrend(n=len(DAYS), seed=seed, amplitude=4.0, period=25)

    async def go():
        async with _session_factory() as session:
            for i, d in enumerate(DAYS):
                c = float(closes[i])
                session.add(MdCandleRecord(instrument_key=f"NSE:{symbol}", timeframe="day", ts=bar_end(d), source=SOURCE["cm"], version=1,
                                           open=c - 0.4, high=c + 1.2, low=c - 1.3, close=c, volume=100_000 + (i % 7) * 40_000))
            await session.commit()
    _run(go())


SYMS = [f"W{uuid.uuid4().hex[:6].upper()}" for _ in range(2)]
_lake(SYMS[0], 3)
_lake(SYMS[1], 4)


def _org(tag, flags=(SW.FLAG, ledger.FLAG)):
    headers = _auth(f"n7c6-{tag}-{uuid.uuid4().hex[:6]}@example.com")
    me = client.get("/api/auth/me", headers=headers).json()
    for f in flags:
        _flag(f, me["tenant_id"])
    return headers, me["tenant_id"], me["id"]


def _spec(**over):
    return {"strategy_id": STRATEGY, "symbols": SYMS, "base_timeframe": "day", "lake": {"start": str(DAYS[0]), "end": str(DAYS[-1])},
            "param_grid": GRID, "metric": "net_pnl", **over}


def _trials(tenant_id, study_id):
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(ResearchTrialRecord).where(ResearchTrialRecord.tenant_id == tenant_id,
                                                                                ResearchTrialRecord.study_id == study_id)))
    return _run(go())


def _enqueue(tenant_id, user_id, **over):
    async def go():
        async with _session_factory() as session:
            return (await SW.enqueue(session, tenant_id=tenant_id, user_id=user_id, spec=SW.SweepSpec(**_spec(**over)))).sweep_id
    return _run(go())


def _step(worker, sweep_id):
    async def go():
        async with _session_factory() as session:
            return await SW.run_one(session, worker, sweep_id=sweep_id, in_thread=False)
    return _run(go())


def _sweep(sweep_id):
    async def go():
        async with _session_factory() as session:
            return await session.scalar(select(BacktestSweepRecord).where(BacktestSweepRecord.sweep_id == sweep_id))
    return _run(go())


# --- the route ------------------------------------------------------------------------------------------------------------------

def test_the_flags_gate_the_routes():
    assert SW.FLAG in DEFAULT_OFF_FLAGS
    off, _, _ = _org("off", flags=())
    r = client.post("/api/backtest/sweeps", headers=off, json=_spec())
    assert r.status_code == 503 and r.headers.get("X-Feature-Disabled") == SW.FLAG
    assert client.get("/api/backtest/sweeps", headers=off).status_code == 503
    no_lake, _, _ = _org("nolake", flags=(SW.FLAG,))
    refused = client.post("/api/backtest/sweeps", headers=no_lake, json=_spec())
    assert refused.status_code == 403 and ledger.FLAG in refused.text


def test_an_inline_sweep_runs_every_combination_and_deflates_the_best():
    headers, tenant, _ = _org("inline")
    r = client.post("/api/backtest/sweeps", headers=headers, json=_spec())
    assert r.status_code == 202, r.text
    sid = r.json()["sweep_id"]
    assert r.json()["total"] == 8 and r.json()["inline"] is True
    got = client.get(f"/api/backtest/sweeps/{sid}", headers=headers).json()           # the API ran the tasks after answering
    assert got["status"] == "done", got
    res = got["result"]
    assert got["tasks"] == {"done": 8} and res["combinations_completed"] == 4 and res["tasks_failed"] == 0
    assert sorted(c["rank"] for c in res["combinations"]) == [1, 2, 3, 4]
    assert any(c["total_trades"] > 0 for c in res["combinations"]) and len({c["net_pnl"] for c in res["combinations"]}) > 1
    best = next(c for c in res["combinations"] if c["rank"] == 1)
    assert best["net_pnl"] == max(c["net_pnl"] for c in res["combinations"]) and res["best"]["params"] == best["params"]
    assert res["deflation"]["n_trials"] >= 4 and res["data_source"] == "lake:nse_bhav:cm:adjusted"
    rows = _trials(tenant, sid)
    assert len(rows) == 4 and {json.loads(r.dsl_json)["kind"] for r in rows} == {SW.KIND}
    assert {json.dumps(json.loads(r.dsl_json)["params"], sort_keys=True) for r in rows} == {json.dumps(c["params"], sort_keys=True) for c in res["combinations"]}
    assert [s["sweep_id"] for s in client.get("/api/backtest/sweeps", headers=headers).json()][0] == sid
    other, _, _ = _org("peer")
    assert client.get(f"/api/backtest/sweeps/{sid}", headers=other).status_code == 404       # another organisation's: not found


def test_the_caps_the_holdout_and_one_sweep_at_a_time(monkeypatch):
    headers, tenant, user = _org("caps")
    big = client.post("/api/backtest/sweeps", headers=headers, json=_spec(param_grid={"lookback": list(range(5, 61)), "adx_min": [20]}))
    assert big.status_code == 422 and "combinations" in big.text
    many = client.post("/api/backtest/sweeps", headers=headers,
                       json=_spec(symbols=[f"S{i}" for i in range(20)], param_grid={"lookback": list(range(5, 16)), "adx_min": [20]}))
    assert many.status_code == 422 and "at most 200" in many.text
    assert client.post("/api/backtest/sweeps", headers=headers, json=_spec(base_timeframe="5min")).status_code == 422
    monkeypatch.setenv("BACKTEST_HOLDOUT_START", str(DAYS[200]))
    sealed = client.post("/api/backtest/sweeps", headers=headers, json=_spec())
    assert sealed.status_code == 422 and "sealed holdout" in sealed.text
    monkeypatch.delenv("BACKTEST_HOLDOUT_START")
    monkeypatch.setenv("BACKTEST_SWEEP_INLINE", "0")                                  # queued for workers: still active
    assert client.post("/api/backtest/sweeps", headers=headers, json=_spec()).status_code == 202
    busy = client.post("/api/backtest/sweeps", headers=headers, json=_spec())
    assert busy.status_code == 409


# --- the workers ----------------------------------------------------------------------------------------------------------------

def test_the_result_does_not_depend_on_how_workers_split_the_tasks():
    _, t1, u1 = _org("split-a")
    _, t2, u2 = _org("split-b")
    one = _enqueue(t1, u1)
    while _step("solo", one):
        pass
    three = _enqueue(t2, u2)
    workers, i = ["w1", "w2", "w3"], 0
    while _step(workers[i % 3], three):
        i += 1
    a, b = json.loads(_sweep(one).result_json), json.loads(_sweep(three).result_json)
    assert _sweep(one).status == _sweep(three).status == "done"
    assert a["result_hash"] == b["result_hash"] and a["combinations"] == b["combinations"]

    async def by_worker():
        async with _session_factory() as session:
            return {t.worker for t in await session.scalars(select(BacktestSweepTaskRecord).where(BacktestSweepTaskRecord.sweep_id == three))}
    assert _run(by_worker()) == {"w1", "w2", "w3"}


def test_a_task_is_claimed_once_and_a_dead_worker_s_task_runs_again(monkeypatch):
    _, tenant, user = _org("claims")
    monkeypatch.setenv("BACKTEST_SWEEP_INLINE", "0")
    sid = _enqueue(tenant, user)
    now = datetime.now(timezone.utc)

    async def two_claims():
        async with _session_factory() as session:
            a = await SW.claim(session, "a", now, sid)
            b = await SW.claim(session, "b", now, sid)
            return a.id, b.id
    a_id, b_id = _run(two_claims())
    assert a_id != b_id

    async def age_and_requeue(task_id, at):
        async with _session_factory() as session:
            await session.execute(update(BacktestSweepTaskRecord).where(BacktestSweepTaskRecord.id == task_id)
                                  .values(heartbeat_at=at - timedelta(seconds=SW.STALE_SECONDS + 5)))
            await session.commit()
            await SW.requeue_stale(session, at)
            return await session.get(BacktestSweepTaskRecord, task_id)
    task = _run(age_and_requeue(a_id, now))
    assert task.status == "queued" and task.attempts == 1 and task.worker is None

    async def exhausted():                                                          # "b" died on its last attempt: failed
        async with _session_factory() as session:
            await session.execute(update(BacktestSweepTaskRecord).where(BacktestSweepTaskRecord.id == b_id)
                                  .values(status="running", attempts=SW.MAX_ATTEMPTS, heartbeat_at=now - timedelta(hours=1)))
            await session.commit()
            await SW.requeue_stale(session, now)
            return (await session.get(BacktestSweepTaskRecord, b_id)).status
    assert _run(exhausted()) == "failed"
    while _step("c", sid):                                                          # "a" is gone; "c" finishes the sweep
        pass
    row = _sweep(sid)
    res = json.loads(row.result_json)
    assert row.status == "done" and res["tasks_failed"] == 1 and res["combinations_completed"] == 3   # b's combination is not compared

    async def late_write():                                                         # "a" comes back with a stale result
        async with _session_factory() as session:
            before = (await session.get(BacktestSweepTaskRecord, a_id)).result_json
            await session.execute(update(BacktestSweepTaskRecord).where(BacktestSweepTaskRecord.id == a_id, BacktestSweepTaskRecord.worker == "a")
                                  .values(result_json="{}"))
            await session.commit()
            return before, (await session.get(BacktestSweepTaskRecord, a_id)).result_json
    before, after = _run(late_write())
    assert before == after                                                          # only the task's current worker may write


def test_a_flag_turned_off_after_queueing_fails_the_tasks_with_the_reason(monkeypatch):
    _, tenant, user = _org("flagoff")
    monkeypatch.setenv("BACKTEST_SWEEP_INLINE", "0")
    sid = _enqueue(tenant, user)
    _flag(SW.FLAG, tenant, on=False)
    while _step("w", sid):
        pass
    row = _sweep(sid)
    assert row.status == "failed" and "every backtest" in row.error

    async def reasons():
        async with _session_factory() as session:
            return {t.error for t in await session.scalars(select(BacktestSweepTaskRecord).where(BacktestSweepTaskRecord.sweep_id == sid))}
    assert all("turned off" in r for r in _run(reasons()))
    assert _trials(tenant, sid) == []


def test_the_plan_is_combination_major_and_capped():
    spec = SW.SweepSpec(**_spec())
    tasks = SW.plan(spec)
    assert [(c, s) for _, c, s in tasks] == [(c, s) for c in range(4) for s in SYMS] and [i for i, _, _ in tasks] == list(range(8))
    assert spec.combos()[0] == {"ema_fast": 10, "ema_slow": 30}
    with pytest.raises(SW.SweepError):
        SW.plan(SW.SweepSpec(**_spec(lake={"start": "2014-01-01", "end": "2024-01-01"}, symbols=[f"S{i}" for i in range(20)],
                                     param_grid={"lookback": list(range(5, 15))})))
