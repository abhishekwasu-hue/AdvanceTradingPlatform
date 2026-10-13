"""N7 C6: distributed backtest runs - a sweep (a parameter grid x symbols on the server's lake bars) split into tasks
that any number of workers run in parallel, merged into one deterministic result.

Flag `backtest_distributed` (operator, off by default; lake reads also need C5's `backtest_ledger`). No new
infrastructure in Phase 0: the queue is two Postgres tables (`backtest_sweeps`, `backtest_sweep_tasks`); a worker is
any process that calls `run_one` - the optional `app/workers/backtest_worker.py` (several can run at once), or, while
BACKTEST_SWEEP_INLINE is on (the default), the API process itself after it answers the request (one task at a time,
off the event loop). A dedicated worker fleet is an infrastructure decision (OPEN_QUESTIONS C6-1).

* Claiming is a conditional UPDATE (queued -> running only if still queued): of two workers that saw the same task, one
  wins. A running task carries a heartbeat; one whose worker stopped is queued again (MAX_ATTEMPTS), then failed.
* A task is pure: lake bars of the window (the sealed holdout checked again when it runs), the strategy with its
  combination's parameters, the single engine with its default models. Its result is stored on the task row.
* The worker that finishes the last task finalises (a conditional UPDATE again): every combination becomes a trial in
  the research ledger (kind `sweep_backtest`, study_id = sweep id; daily returns = the equal-weight mean over its
  symbols), the best by the chosen metric is deflated against this organisation's sweep trials of the last 90 days
  (this sweep's included - the grid IS the search), and the combinations' PBO (CSCV) is reported. The merged result is
  ordered by combination and symbol, never by which worker ran what - the same sweep gives the same `result_hash`
  however it was split (tested).

Research only: no order path; a sweep never changes a strategy.
"""
from __future__ import annotations

import itertools
import json
import logging
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.backtest.ledger import LakeWindow
from app.core.models import RiskConfig
from app.db.models import BacktestSweepRecord, BacktestSweepTaskRecord, User

logger = logging.getLogger(__name__)

FLAG = "backtest_distributed"
KIND = "sweep_backtest"
MAX_COMBOS = 50
MAX_SYMBOLS = 20
MAX_TASKS = 200
MAX_SWEEP_BARS = 400_000               # tasks x sessions in the window (estimated from weekdays before anything runs)
MAX_ATTEMPTS = 3
STALE_SECONDS = 600                    # a running task with no heartbeat for this long: its worker stopped
METRICS = ("sharpe", "net_pnl", "expectancy", "profit_factor", "win_rate")
ACTIVE = ("queued", "running", "finalizing")


class SweepError(ValueError):
    """A sweep the platform does not accept (the reason is shown as is)."""


class SweepBusy(SweepError):
    """This organisation already has a sweep queued or running."""


class SweepSpec(BaseModel):
    strategy_id: str
    symbols: List[str] = Field(min_length=1, max_length=MAX_SYMBOLS)
    base_timeframe: str = "day"
    lake: LakeWindow
    param_grid: Dict[str, List[Any]] = Field(default_factory=dict)
    metric: str = Field(default="sharpe", pattern=r"^(sharpe|net_pnl|expectancy|profit_factor|win_rate)$")
    risk_config: Optional[RiskConfig] = None

    @model_validator(mode="after")
    def _check(self) -> "SweepSpec":
        self.symbols = list(dict.fromkeys(s.strip().upper() for s in self.symbols if s.strip()))
        if not self.symbols:
            raise ValueError("symbols: at least one")
        for key, values in self.param_grid.items():
            if not isinstance(values, list) or not values:
                raise ValueError(f"param_grid.{key}: a non-empty list of values")
        return self

    def combos(self) -> List[Dict[str, Any]]:
        keys = sorted(self.param_grid)
        return [dict(zip(keys, values)) for values in itertools.product(*(self.param_grid[k] for k in keys))] or [{}]


def plan(spec: SweepSpec) -> List[Tuple[int, int, str]]:
    """(task index, combination, symbol) - combination-major, symbols in the order given. SweepError over the caps."""
    combos = spec.combos()
    if len(combos) > MAX_COMBOS:
        raise SweepError(f"param_grid: {len(combos)} combinations - at most {MAX_COMBOS}")
    tasks = [(i, c, s) for i, (c, s) in enumerate((c, s) for c in range(len(combos)) for s in spec.symbols)]
    if len(tasks) > MAX_TASKS:
        raise SweepError(f"{len(tasks)} backtests (combinations x symbols) - at most {MAX_TASKS} in one sweep")
    sessions = int(np.busday_count(spec.lake.start, spec.lake.end + timedelta(days=1)))
    if len(tasks) * sessions > MAX_SWEEP_BARS:
        raise SweepError(f"{len(tasks)} backtests x up to {sessions} sessions is over {MAX_SWEEP_BARS} bars - fewer combinations, "
                         "symbols or a shorter window")
    return tasks


async def enabled(session: AsyncSession, tenant_id: int) -> bool:
    from app.platform.controls import flag_enabled
    return await flag_enabled(session, FLAG, tenant_id)


async def enqueue(session: AsyncSession, *, tenant_id: int, user_id: int, spec: SweepSpec, now: Optional[datetime] = None) -> BacktestSweepRecord:
    """One sweep and its tasks, queued (one active sweep per organisation keeps the shared workers fair)."""
    from app.backtest import ledger
    tasks = plan(spec)
    ledger.check_holdout(spec.lake)                     # HoldoutError: refused before anything is queued
    active = await session.scalar(select(func.count()).select_from(BacktestSweepRecord).where(
        BacktestSweepRecord.tenant_id == tenant_id, BacktestSweepRecord.status.in_(ACTIVE)))
    if active:
        raise SweepBusy("A backtest sweep is already queued or running for this organisation.")
    sid = str(uuid.uuid4())
    row = BacktestSweepRecord(sweep_id=sid, tenant_id=tenant_id, user_id=user_id, spec_json=spec.model_dump_json(), status="queued",
                              total=len(tasks), created_at=now or datetime.now(timezone.utc))
    session.add(row)
    await session.flush()
    session.add_all([BacktestSweepTaskRecord(sweep_id=sid, tenant_id=tenant_id, idx=i, combo=c, symbol=s, status="queued", attempts=0)
                     for i, c, s in tasks])
    await session.commit()
    return row


async def requeue_stale(session: AsyncSession, now: datetime) -> int:
    """Running tasks whose worker stopped beating: queued again, or failed after MAX_ATTEMPTS."""
    cutoff = now - timedelta(seconds=STALE_SECONDS)
    failed = await session.execute(update(BacktestSweepTaskRecord).where(
        BacktestSweepTaskRecord.status == "running", BacktestSweepTaskRecord.heartbeat_at < cutoff,
        BacktestSweepTaskRecord.attempts >= MAX_ATTEMPTS).values(status="failed", error="its worker stopped on every attempt"))
    again = await session.execute(update(BacktestSweepTaskRecord).where(
        BacktestSweepTaskRecord.status == "running", BacktestSweepTaskRecord.heartbeat_at < cutoff).values(status="queued", worker=None))
    await session.commit()
    return int(failed.rowcount or 0) + int(again.rowcount or 0)   # type: ignore[attr-defined]


async def claim(session: AsyncSession, worker: str, now: datetime, sweep_id: Optional[str] = None) -> Optional[BacktestSweepTaskRecord]:
    """The oldest queued task (of `sweep_id`, when given), claimed atomically by `worker`."""
    for _ in range(5):                                       # another worker may win the row: try the next one
        q = select(BacktestSweepTaskRecord).where(BacktestSweepTaskRecord.status == "queued")
        if sweep_id is not None:
            q = q.where(BacktestSweepTaskRecord.sweep_id == sweep_id)
        task = await session.scalar(q.order_by(BacktestSweepTaskRecord.id).limit(1))
        if task is None:
            return None
        won = await session.execute(update(BacktestSweepTaskRecord).where(
            BacktestSweepTaskRecord.id == task.id, BacktestSweepTaskRecord.status == "queued").values(
            status="running", worker=worker[:80], heartbeat_at=now, attempts=BacktestSweepTaskRecord.attempts + 1))
        await session.commit()
        if won.rowcount:                                     # type: ignore[attr-defined]
            await session.refresh(task)
            return task
    return None


def _task_backtest(strategy: Any, frame: Any, symbol: str, base_tf: str, risk: RiskConfig) -> Dict[str, Any]:
    """CPU part of a task (off the event loop): the single engine with its default models, and the daily returns."""
    from app.ai.research import daily_returns, trading_days
    from app.backtest.engine import run_backtest
    import pandas as pd
    result = run_backtest(strategy, frame, symbol, base_tf, risk)
    days = trading_days(pd.DatetimeIndex(frame.index))
    returns = daily_returns(result.trades, risk.capital, days) if days else []
    return {"net_pnl": result.net_pnl, "total_trades": result.total_trades, "win_rate": result.win_rate,
            "expectancy": result.expectancy, "profit_factor": result.profit_factor, "max_drawdown": result.max_drawdown,
            "returns": {str(d): round(float(r), 12) for d, r in zip(days, returns)}, "bars": len(frame)}


async def run_one(session: AsyncSession, worker: str, *, sweep_id: Optional[str] = None, clock: Optional[Callable[[], datetime]] = None,
                  in_thread: bool = True) -> Optional[str]:
    """Claim one task and run it; finalise its sweep when it was the last. Returns the task's sweep id (None: no task)."""
    import copy

    from fastapi.concurrency import run_in_threadpool

    from app.backtest import ledger
    from app.core.models import bars_to_dataframe
    from app.custom_strategies.resolver import resolve_strategy
    now = clock or (lambda: datetime.now(timezone.utc))
    await requeue_stale(session, now())
    task = await claim(session, worker, now(), sweep_id)
    if task is None:
        return None
    tid, sid, combo, symbol, task_id = task.tenant_id, task.sweep_id, task.combo, task.symbol, task.id
    sweep = await session.scalar(select(BacktestSweepRecord).where(BacktestSweepRecord.sweep_id == sid))
    assert sweep is not None
    spec = SweepSpec.model_validate_json(sweep.spec_json)
    uid = sweep.user_id
    if sweep.status == "queued":
        await session.execute(update(BacktestSweepRecord).where(BacktestSweepRecord.sweep_id == sid, BacktestSweepRecord.status == "queued")
                              .values(status="running", started_at=now()))
        await session.commit()
    status, payload, error = "done", None, None
    try:
        if not await enabled(session, tid) or not await ledger.enabled(session, tid):
            raise SweepError(f"the {FLAG} / {ledger.FLAG} feature was turned off after this sweep was queued")
        user = await session.get(User, uid) if uid else None
        if user is None or user.tenant_id != tid:
            raise SweepError("the user who asked for this sweep no longer exists")
        strategy = copy.copy(await resolve_strategy(spec.strategy_id, user, session))
        strategy.params = {**getattr(strategy, "params", {}), **spec.combos()[combo]}
        frame = bars_to_dataframe(await ledger.lake_candles(session, symbol, spec.base_timeframe, spec.lake))  # holdout checked again
        risk = spec.risk_config or RiskConfig()
        args = (strategy, frame, symbol, spec.base_timeframe, risk)
        payload = await run_in_threadpool(_task_backtest, *args) if in_thread else _task_backtest(*args)
    except Exception as exc:  # noqa: BLE001 - the task fails with its reason; the sweep goes on
        await session.rollback()
        status, error = "failed", f"{type(exc).__name__}: {exc}"[:300]
        if not isinstance(exc, (SweepError, ledger.LedgerError, ledger.HoldoutError)):
            logger.exception("Backtest sweep task %s failed", task_id)
    await session.execute(update(BacktestSweepTaskRecord).where(BacktestSweepTaskRecord.id == task_id, BacktestSweepTaskRecord.worker == worker[:80])
                          .values(status=status, error=error, heartbeat_at=now(),
                                  result_json=json.dumps(payload, sort_keys=True) if payload is not None else None))
    await session.commit()
    await finalize_if_complete(session, sid, now())
    return sid


async def finalize_if_complete(session: AsyncSession, sweep_id: str, now: datetime) -> bool:
    """When no task is left queued or running, ONE caller (conditional update) merges the results and finalises."""
    left = await session.scalar(select(func.count()).select_from(BacktestSweepTaskRecord).where(
        BacktestSweepTaskRecord.sweep_id == sweep_id, BacktestSweepTaskRecord.status.in_(("queued", "running"))))
    if left:
        return False
    won = await session.execute(update(BacktestSweepRecord).where(
        BacktestSweepRecord.sweep_id == sweep_id, BacktestSweepRecord.status.in_(("queued", "running"))).values(status="finalizing"))
    await session.commit()
    if not won.rowcount:                                     # type: ignore[attr-defined]
        return False
    sweep = await session.scalar(select(BacktestSweepRecord).where(BacktestSweepRecord.sweep_id == sweep_id))
    assert sweep is not None
    tasks = list(await session.scalars(select(BacktestSweepTaskRecord).where(BacktestSweepTaskRecord.sweep_id == sweep_id)
                                       .order_by(BacktestSweepTaskRecord.idx)))
    try:
        result = await _merge(session, sweep, tasks)
        ok = result["combinations_completed"] > 0
        sweep.status = "done" if ok else "failed"
        sweep.error = None if ok else "every backtest of this sweep failed (see the tasks' reasons)"
        sweep.result_json = json.dumps(result, sort_keys=True, default=str)
    except Exception as exc:  # noqa: BLE001 - the sweep fails with its reason
        logger.exception("Backtest sweep %s could not be finalised", sweep_id)
        await session.rollback()
        sweep = await session.scalar(select(BacktestSweepRecord).where(BacktestSweepRecord.sweep_id == sweep_id))
        assert sweep is not None
        sweep.status, sweep.error = "failed", f"{type(exc).__name__}: {exc}"[:300]
    sweep.finished_at = now
    await session.commit()
    return True


def _metric(m: Dict[str, Any], name: str) -> Optional[float]:
    v = m.get(name)
    return None if v is None or (isinstance(v, float) and not math.isfinite(v)) else float(v)


async def _merge(session: AsyncSession, sweep: BacktestSweepRecord, tasks: List[BacktestSweepTaskRecord]) -> Dict[str, Any]:
    from app.ai.research import _pbo_blocks, record_trial
    from app.backtest import ledger
    from app.backtest.repro import config_hash
    from app.backtest.validation import deflated_sharpe, pbo_cscv, sharpe
    spec = SweepSpec.model_validate_json(sweep.spec_json)
    combos = spec.combos()
    by_combo: Dict[int, List[Tuple[str, Optional[Dict[str, Any]], Optional[str]]]] = {c: [] for c in range(len(combos))}
    for t in tasks:
        by_combo[t.combo].append((t.symbol, json.loads(t.result_json) if t.result_json else None, t.error))
    days = sorted({d for rows in by_combo.values() for _, r, _ in rows if r for d in r["returns"]})
    since = datetime.now(timezone.utc) - timedelta(days=ledger.LEDGER_DAYS)
    earlier = await ledger.earlier_sharpes(session, sweep.tenant_id, since, KIND)
    out_combos: List[Dict[str, Any]] = []
    matrix: List[List[float]] = []
    for c, params in enumerate(combos):
        rows = by_combo[c]
        done = [(s, r) for s, r, _ in rows if r is not None]
        entry: Dict[str, Any] = {"combo": c, "params": params, "symbols": {s: ({k: v for k, v in r.items() if k != "returns"} if r else {"error": e})
                                                                        for s, r, e in rows}}
        if len(done) == len(rows) and done:
            returns = [float(np.mean([r["returns"].get(d, 0.0) for _, r in done])) for d in days]
            entry.update({"net_pnl": round(sum(r["net_pnl"] for _, r in done), 2), "total_trades": sum(r["total_trades"] for _, r in done),
                          "sharpe": round(sharpe(returns), 6) if len(returns) >= 2 else None,
                          "expectancy": round(float(np.mean([r["expectancy"] for _, r in done])), 4),
                          "win_rate": round(float(np.mean([r["win_rate"] for _, r in done])), 4),
                          "profit_factor": (round(float(np.mean([r["profit_factor"] for _, r in done if r["profit_factor"] is not None])), 4)
                                            if any(r["profit_factor"] is not None for _, r in done) else None),
                          "complete": True})
            entry["_returns"] = returns
        else:
            entry["complete"] = False                         # a symbol failed: the combination is not compared
        out_combos.append(entry)
    complete = [e for e in out_combos if e["complete"] and len(e["_returns"]) >= 2]
    for e in complete:                                        # every combination tried is a trial: the grid is the search
        await record_trial(session, tenant_id=sweep.tenant_id, user_id=sweep.user_id, study_id=sweep.sweep_id,
                           dsl={"kind": KIND, "sweep_id": sweep.sweep_id, "strategy_id": spec.strategy_id, "params": e["params"],
                                "symbols": spec.symbols, "base_timeframe": spec.base_timeframe, "data_source": ledger.lake_label(spec.lake)},
                           symbol=f"SWEEP:{len(spec.symbols)}", exchange=spec.lake.exchange, timeframe=spec.base_timeframe, status="ok",
                           data_from=datetime.combine(spec.lake.start, datetime.min.time(), tzinfo=timezone.utc),
                           data_to=datetime.combine(spec.lake.end, datetime.min.time(), tzinfo=timezone.utc),
                           metrics={k: e.get(k) for k in ("net_pnl", "total_trades", "sharpe")}, returns=e["_returns"])
        matrix.append(e["_returns"])
    best = None
    if complete:
        def score(e: Dict[str, Any]) -> float:
            v = _metric(e, spec.metric)
            return -math.inf if v is None else v
        ranked = sorted(complete, key=lambda e: (-score(e), e["combo"]))
        best = ranked[0]
        for rank, e in enumerate(ranked, start=1):
            e["rank"] = rank
    deflation = None
    pbo = None
    if best is not None:
        trial_srs = earlier + [sharpe(e["_returns"]) for e in complete]
        deflation = {**deflated_sharpe(best["_returns"], trial_srs), "note": ledger.NOTE.format(n=len(trial_srs), days=ledger.LEDGER_DAYS)}
        if len(complete) >= 2:
            m = np.array(matrix, dtype=float).T
            blocks = _pbo_blocks(m.shape[0])
            pbo = pbo_cscv(m, S=blocks)["pbo"] if blocks >= 2 else None
    for e in out_combos:
        e.pop("_returns", None)
    merged = {"sweep_id": sweep.sweep_id, "strategy_id": spec.strategy_id, "metric": spec.metric, "symbols": spec.symbols,
              "window": {"start": str(spec.lake.start), "end": str(spec.lake.end)}, "data_source": ledger.lake_label(spec.lake),
              "combinations": out_combos, "combinations_completed": len(complete), "tasks": len(tasks),
              "tasks_failed": sum(1 for t in tasks if t.status == "failed"),
              "best": {"combo": best["combo"], "params": best["params"]} if best else None, "deflation": deflation, "pbo": pbo}
    merged["result_hash"] = config_hash(combinations=out_combos, best=merged["best"])
    merged["disclaimer"] = ("A search over parameter combinations on past data: the best one is deflated for every combination "
                            "tried. Not a recommendation, and not a prediction.")
    return merged


async def drain(factory: async_sessionmaker[AsyncSession], worker: str, sweep_id: Optional[str] = None, limit: int = MAX_TASKS) -> int:
    """Run tasks until none is left (of `sweep_id`, when given) - the in-process fallback and the worker's loop step."""
    ran = 0
    while ran < limit:
        async with factory() as session:
            if await run_one(session, worker, sweep_id=sweep_id) is None:
                break
        ran += 1
    return ran


async def progress(session: AsyncSession, sweep_id: str) -> Dict[str, int]:
    rows = await session.execute(select(BacktestSweepTaskRecord.status, func.count()).where(
        BacktestSweepTaskRecord.sweep_id == sweep_id).group_by(BacktestSweepTaskRecord.status))
    return {str(s): int(n) for s, n in rows}


def sweep_dict(row: BacktestSweepRecord, counts: Optional[Dict[str, int]] = None, full: bool = True) -> Dict[str, Any]:
    def iso(d: Optional[datetime]) -> Optional[str]:
        return None if d is None else (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).isoformat()
    out = {"sweep_id": row.sweep_id, "status": row.status, "error": row.error, "total": row.total, "tasks": counts or {},
           "spec": json.loads(row.spec_json), "created_at": iso(row.created_at), "started_at": iso(row.started_at),
           "finished_at": iso(row.finished_at)}
    if full:
        out["result"] = json.loads(row.result_json) if row.result_json else None
    return out


__all__ = ["FLAG", "KIND", "SweepSpec", "SweepError", "SweepBusy", "plan", "enqueue", "claim", "run_one", "requeue_stale",
           "finalize_if_complete", "drain", "progress", "sweep_dict", "MAX_TASKS", "MAX_COMBOS", "STALE_SECONDS", "MAX_ATTEMPTS"]
