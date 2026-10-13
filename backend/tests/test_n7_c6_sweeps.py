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
    assert big.status_code == 422                                                   # one list longer than 50 values
    wide = client.post("/api/backtest/sweeps", headers=headers, json=_spec(param_grid={"lookback": list(range(5, 31)), "adx_min": [20, 25]}))
    assert wide.status_code == 422 and "52 combinations" in wide.text
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


# --- review fixes: grid size, stuck sweeps, claims ------------------------------------------------------------------------------

def test_an_8_by_10_grid_is_refused_from_its_list_lengths_fast():
    import time

    from pydantic import ValidationError
    headers, _, _ = _org("grid")
    grid = {f"p{k}": list(range(10)) for k in range(8)}                              # 10**8 combinations: never built
    t0 = time.monotonic()
    r = client.post("/api/backtest/sweeps", headers=headers, json=_spec(param_grid=grid))
    assert r.status_code == 422 and "100000000 combinations" in r.text
    assert time.monotonic() - t0 < 2.0
    for bad in ({f"p{k}": [1] for k in range(SW.MAX_GRID_KEYS + 1)},                 # too many parameters
                {"p": list(range(SW.MAX_COMBOS + 1))},                               # one list too long
                {"p": ["x" * (SW.MAX_GRID_VALUE_CHARS + 1)]},                        # a value too large
                {"p": [[1, 2]]}, {"p": [{"a": 1}]}):                                 # values are scalars
        with pytest.raises(ValidationError):
            SW.SweepSpec(**_spec(param_grid=bad))
    with pytest.raises(ValidationError):                                             # backtest_sweep_tasks.symbol is String(40)
        SW.SweepSpec(**_spec(symbols=["S" * 41]))
    assert client.post("/api/backtest/sweeps", headers=headers, json=_spec(symbols=["S" * 41])).status_code == 422


def test_a_requeue_that_fails_the_last_task_finalises_its_sweep(monkeypatch):
    _, tenant, user = _org("lastfail")
    monkeypatch.setenv("BACKTEST_SWEEP_INLINE", "0")
    sid = _enqueue(tenant, user, symbols=SYMS[:1], param_grid={"ema_fast": [10, 20]})
    _step("w", sid)                                                                  # task 0 done
    now = datetime.now(timezone.utc)

    async def die_on_the_last():
        async with _session_factory() as session:
            last = await SW.claim(session, "dead", now, sid)
            await session.execute(update(BacktestSweepTaskRecord).where(BacktestSweepTaskRecord.id == last.id)
                                  .values(attempts=SW.MAX_ATTEMPTS, heartbeat_at=now - timedelta(hours=1)))
            await session.commit()
            return await SW.requeue_stale(session, now, in_thread=False)
    assert _run(die_on_the_last()) == [sid]
    row = _sweep(sid)
    assert row.status == "done" and json.loads(row.result_json)["tasks_failed"] == 1


def test_after_an_api_restart_the_next_post_settles_and_runs_the_active_sweep(monkeypatch):
    headers, tenant, user = _org("restart")
    monkeypatch.setenv("BACKTEST_SWEEP_INLINE", "0")                                 # queued; the process "stops" before running it
    first = client.post("/api/backtest/sweeps", headers=headers, json=_spec(symbols=SYMS[:1])).json()["sweep_id"]
    now = datetime.now(timezone.utc)

    async def a_dead_worker_holds_one():
        async with _session_factory() as session:
            task = await SW.claim(session, "api-inline:gone", now - timedelta(seconds=SW.STALE_SECONDS + 60), first)
            return task.id
    dead = _run(a_dead_worker_holds_one())
    monkeypatch.setenv("BACKTEST_SWEEP_INLINE", "1")                                 # the restarted API runs sweeps inline
    busy = client.post("/api/backtest/sweeps", headers=headers, json=_spec())
    assert busy.status_code == 409 and busy.json()["active_sweep_id"] == first       # answered, then the sweep was run here
    got = client.get(f"/api/backtest/sweeps/{first}", headers=headers).json()
    assert got["status"] == "done" and got["tasks"] == {"done": 4}, got

    async def attempts():
        async with _session_factory() as session:
            return (await session.get(BacktestSweepTaskRecord, dead)).attempts
    assert _run(attempts()) == 2                                                     # the dead worker's task ran again
    assert client.post("/api/backtest/sweeps", headers=headers, json=_spec(symbols=SYMS[:1])).status_code == 202


def test_the_startup_hook_runs_what_a_stopped_api_left_queued(monkeypatch):
    _, tenant, user = _org("startup")
    monkeypatch.setenv("BACKTEST_SWEEP_INLINE", "0")
    sid = _enqueue(tenant, user, symbols=SYMS[:1], param_grid={"ema_fast": [10, 20]})
    assert _run(SW.resume_inline(_session_factory)) >= 2
    assert _sweep(sid).status == "done"


def test_a_sweep_stuck_finalising_or_too_old_expires(monkeypatch):
    headers, tenant, user = _org("expire")
    monkeypatch.setenv("BACKTEST_SWEEP_INLINE", "0")
    sid = _enqueue(tenant, user)

    async def crashed_while_finalising():
        async with _session_factory() as session:
            await session.execute(update(BacktestSweepRecord).where(BacktestSweepRecord.sweep_id == sid).values(
                status="finalizing", created_at=datetime.now(timezone.utc) - timedelta(hours=SW.EXPIRE_HOURS, minutes=1)))
            await session.commit()
    _run(crashed_while_finalising())
    r = client.post("/api/backtest/sweeps", headers=headers, json=_spec())
    assert r.status_code == 202, r.text
    old = _sweep(sid)
    assert old.status == "failed" and "expired" in old.error

    async def statuses():
        async with _session_factory() as session:
            return {t.status for t in await session.scalars(select(BacktestSweepTaskRecord).where(BacktestSweepTaskRecord.sweep_id == sid))}
    assert _run(statuses()) == {"failed"}


def test_the_owner_cancels_an_active_sweep(monkeypatch):
    headers, tenant, user = _org("cancel")
    monkeypatch.setenv("BACKTEST_SWEEP_INLINE", "0")
    sid = client.post("/api/backtest/sweeps", headers=headers, json=_spec()).json()["sweep_id"]
    other, _, _ = _org("cancel-peer")
    assert client.post(f"/api/backtest/sweeps/{sid}/cancel", headers=other).status_code == 404     # another organisation's
    now = datetime.now(timezone.utc)

    async def mid_task():
        async with _session_factory() as session:
            t = await SW.claim(session, "w", now, sid)
            return t.id, t.attempts
    task_id, attempts = _run(mid_task())
    r = client.post(f"/api/backtest/sweeps/{sid}/cancel", headers=headers)
    assert r.status_code == 200 and r.json()["status"] == "cancelled" and r.json()["tasks"] == {"cancelled": 8}

    async def late_write():                                                         # the worker mid-task cannot write any more
        async with _session_factory() as session:
            res = await session.execute(update(BacktestSweepTaskRecord).where(*SW._owns(task_id, "w", attempts)).values(status="done"))
            await session.commit()
            return res.rowcount, await SW.finalize_if_complete(session, sid, now, in_thread=False)
    assert _run(late_write()) == (0, False)
    assert client.post(f"/api/backtest/sweeps/{sid}/cancel", headers=headers).status_code == 409   # already ended
    assert _step("w2", sid) is None                                                  # nothing left to run
    assert client.post("/api/backtest/sweeps", headers=headers, json=_spec()).status_code == 202


def test_a_write_needs_the_same_claim_and_inline_names_are_per_drain(monkeypatch):
    _, tenant, user = _org("reclaim")
    monkeypatch.setenv("BACKTEST_SWEEP_INLINE", "0")
    sid = _enqueue(tenant, user)
    now = datetime.now(timezone.utc)

    async def go():
        async with _session_factory() as session:
            first = await SW.claim(session, "a", now - timedelta(seconds=SW.STALE_SECONDS + 5), sid)
            await SW.requeue_stale(session, now, in_thread=False)
            again = await SW.claim(session, "a", now, sid)                           # the same worker name claims it again
            assert again.id == first.id and again.attempts == 2
            stale = await session.execute(update(BacktestSweepTaskRecord).where(*SW._owns(first.id, "a", 1)).values(status="done"))
            await session.commit()
            return stale.rowcount, (await session.get(BacktestSweepTaskRecord, first.id)).status
    assert _run(go()) == (0, "running")                                              # the first attempt's late write is refused
    assert SW.inline_worker_name() != SW.inline_worker_name()


def test_the_database_allows_one_active_sweep_per_organisation(monkeypatch):
    from sqlalchemy.exc import IntegrityError
    _, tenant, user = _org("race")
    monkeypatch.setenv("BACKTEST_SWEEP_INLINE", "0")
    _enqueue(tenant, user)

    async def no_check(session, tenant_id):                                          # the other request checked before this one wrote
        return None
    monkeypatch.setattr(SW, "active_sweep", no_check)
    with pytest.raises(SW.SweepBusy):
        _enqueue(tenant, user)

    async def raw():
        async with _session_factory() as session:
            session.add(BacktestSweepRecord(sweep_id=str(uuid.uuid4()), tenant_id=tenant, user_id=user, spec_json="{}", status="running",
                                            total=0, created_at=datetime.now(timezone.utc)))
            await session.commit()
    with pytest.raises(IntegrityError):
        _run(raw())


def test_a_long_backtest_keeps_beating(monkeypatch):
    import time
    _, tenant, user = _org("beat")
    monkeypatch.setenv("BACKTEST_SWEEP_INLINE", "0")
    monkeypatch.setattr(SW, "HEARTBEAT_SECONDS", 0.05)
    sid = _enqueue(tenant, user, symbols=SYMS[:1], param_grid={})
    window, calls, real = [], [], SW._task_backtest

    def slow(*args):
        window.append(time.monotonic())
        time.sleep(0.4)
        window.append(time.monotonic())
        return real(*args)
    monkeypatch.setattr(SW, "_task_backtest", slow)

    def clock():
        calls.append(time.monotonic())
        return datetime.now(timezone.utc)

    async def go():
        async with _session_factory() as session:
            return await SW.run_one(session, "slow", sweep_id=sid, clock=clock, in_thread=True)
    assert _run(go()) == sid
    beats = [t for t in calls if window[0] < t < window[1]]
    assert len(beats) >= 2                                                           # beats while the backtest ran
    assert _sweep(sid).status == "done"
