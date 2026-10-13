"""N7 C6: distributed backtest runs - a sweep (a parameter grid x symbols on the server's lake bars) split into tasks
that any number of workers run in parallel, merged into one deterministic result.

Flag `backtest_distributed` (operator, off by default; lake reads also need C5's `backtest_ledger`). No new
infrastructure in Phase 0: the queue is two Postgres tables (`backtest_sweeps`, `backtest_sweep_tasks`); a worker is
any process that calls `run_one` - the optional `app/workers/backtest_worker.py` (several can run at once), or, while
BACKTEST_SWEEP_INLINE is on (the default), the API process itself after it answers the request (one task at a time,
off the event loop). A dedicated worker fleet is an infrastructure decision (OPEN_QUESTIONS C6-1).

* Claiming is a conditional UPDATE (queued -> running only if still queued): of two workers that saw the same task, one
  wins. A running task carries a heartbeat; one whose worker stopped is queued again (MAX_ATTEMPTS), then failed.
* A grid's size is counted from its list lengths before any combination is built (SweepSpec: at most MAX_GRID_KEYS
  parameters, MAX_COMBOS values each, scalar values) - an oversized grid is a 422, never an expansion.
* Heartbeats and staleness use the database's clock; a running backtest beats every HEARTBEAT_SECONDS. Only the claim that
  holds a task (worker, attempt, still running) may beat or write it.
* Nothing stays active for ever (`recover`): a dead worker's last task failed finalises its sweep, a sweep whose tasks
  all ended is finalised by whoever looks next, a sweep still active EXPIRE_HOURS after it was queued is failed, and the
  owner may cancel. A partial unique index keeps one active sweep per organisation under concurrent requests.
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

import asyncio
import itertools
import json
import logging
import math
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

import numpy as np
from pydantic import BaseModel, Field, StringConstraints, model_validator
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
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
MAX_GRID_KEYS = 8                      # parameters in one grid
MAX_GRID_VALUE_CHARS = 64              # a string value / a parameter name
MAX_ATTEMPTS = 3
STALE_SECONDS = 600                    # a running task with no heartbeat for this long: its worker stopped
HEARTBEAT_SECONDS = 120.0              # a running task beats this often while its backtest runs
EXPIRE_HOURS = 6                       # a sweep still active this long after it was queued is failed (it never blocks for ever)
METRICS = ("sharpe", "net_pnl", "expectancy", "profit_factor", "win_rate")
ACTIVE = ("queued", "running", "finalizing")

Symbol = Annotated[str, StringConstraints(max_length=40)]       # backtest_sweep_tasks.symbol is String(40)
GridValues = Annotated[List[Any], Field(min_length=1, max_length=MAX_COMBOS)]


class SweepError(ValueError):
    """A sweep the platform does not accept (the reason is shown as is)."""


class SweepBusy(SweepError):
    """This organisation already has a sweep queued or running."""


class SweepSpec(BaseModel):
    strategy_id: str
    symbols: List[Symbol] = Field(min_length=1, max_length=MAX_SYMBOLS)
    base_timeframe: str = Field(default="day", max_length=16)
    lake: LakeWindow
    param_grid: Dict[str, GridValues] = Field(default_factory=dict, max_length=MAX_GRID_KEYS)
    metric: str = Field(default="sharpe", pattern=r"^(sharpe|net_pnl|expectancy|profit_factor|win_rate)$")
    risk_config: Optional[RiskConfig] = None

    @model_validator(mode="after")
    def _check(self) -> "SweepSpec":
        self.symbols = list(dict.fromkeys(s.strip().upper() for s in self.symbols if s.strip()))
        if not self.symbols:
            raise ValueError("symbols: at least one")
        # The grid's size is checked from its list lengths BEFORE any combination is built (a product of many short lists is
        # huge): the validator is the only place a grid is ever expanded from.
        for key, values in self.param_grid.items():
            if not key or len(key) > MAX_GRID_VALUE_CHARS:
                raise ValueError(f"param_grid: a parameter name of 1 to {MAX_GRID_VALUE_CHARS} characters")
            if not isinstance(values, list) or not values:
                raise ValueError(f"param_grid.{key}: a non-empty list of values")
            for v in values:
                if not (v is None or isinstance(v, (bool, int, float)) or (isinstance(v, str) and len(v) <= MAX_GRID_VALUE_CHARS)):
                    raise ValueError(f"param_grid.{key}: values are numbers, booleans or strings of at most {MAX_GRID_VALUE_CHARS} "
                                     "characters")
        n = math.prod(len(v) for v in self.param_grid.values())
        if n > MAX_COMBOS:
            raise ValueError(f"param_grid: {n} combinations - at most {MAX_COMBOS}")
        return self

    def n_combos(self) -> int:
        return math.prod(len(v) for v in self.param_grid.values())      # 1 for an empty grid (the strategy's own parameters)

    def combos(self) -> List[Dict[str, Any]]:
        keys = sorted(self.param_grid)
        return [dict(zip(keys, values)) for values in itertools.product(*(self.param_grid[k] for k in keys))] or [{}]


def plan(spec: SweepSpec) -> List[Tuple[int, int, str]]:
    """(task index, combination, symbol) - combination-major, symbols in the order given. SweepError over the caps (sizes are
    counted, never built: SweepSpec refuses an oversized grid already)."""
    n_combos = spec.n_combos()
    if n_combos > MAX_COMBOS:
        raise SweepError(f"param_grid: {n_combos} combinations - at most {MAX_COMBOS}")
    if n_combos * len(spec.symbols) > MAX_TASKS:
        raise SweepError(f"{n_combos * len(spec.symbols)} backtests (combinations x symbols) - at most {MAX_TASKS} in one sweep")
    tasks = [(i, c, s) for i, (c, s) in enumerate((c, s) for c in range(n_combos) for s in spec.symbols)]
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


async def db_now(session: AsyncSession) -> datetime:
    """The database's clock (PostgreSQL `now()`): every worker, on any host, stamps and judges heartbeats by the same clock,
    so a host whose clock runs fast cannot requeue a live task. Other dialects (the SQLite test database) use this process's
    UTC clock."""
    if session.get_bind().dialect.name == "postgresql":
        value = await session.scalar(select(func.now()))
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


async def enqueue(session: AsyncSession, *, tenant_id: int, user_id: int, spec: SweepSpec, now: Optional[datetime] = None) -> BacktestSweepRecord:
    """One sweep and its tasks, queued (one active sweep per organisation keeps the shared workers fair). The organisation's
    sweeps nobody would move on any more (a dead worker's tasks, every task ended but never finalised, older than
    EXPIRE_HOURS) are settled first, so they never block it for ever."""
    from app.backtest import ledger
    tasks = plan(spec)
    ledger.check_holdout(spec.lake)                     # HoldoutError: refused before anything is queued
    await recover(session, tenant_id=tenant_id, now=now)
    if await active_sweep(session, tenant_id) is not None:
        raise SweepBusy("A backtest sweep is already queued or running for this organisation.")
    sid = str(uuid.uuid4())
    row = BacktestSweepRecord(sweep_id=sid, tenant_id=tenant_id, user_id=user_id, spec_json=spec.model_dump_json(), status="queued",
                              total=len(tasks), created_at=now or datetime.now(timezone.utc))
    session.add(row)
    try:
        await session.flush()                           # uq_backtest_sweeps_one_active: a concurrent request may have won
    except IntegrityError as exc:
        await session.rollback()
        raise SweepBusy("A backtest sweep is already queued or running for this organisation.") from exc
    session.add_all([BacktestSweepTaskRecord(sweep_id=sid, tenant_id=tenant_id, idx=i, combo=c, symbol=s, status="queued", attempts=0)
                     for i, c, s in tasks])
    await session.commit()
    return row


async def active_sweep(session: AsyncSession, tenant_id: int) -> Optional[str]:
    """The organisation's queued / running / finalising sweep, if any."""
    return await session.scalar(select(BacktestSweepRecord.sweep_id).where(
        BacktestSweepRecord.tenant_id == tenant_id, BacktestSweepRecord.status.in_(ACTIVE)).order_by(BacktestSweepRecord.id).limit(1))


async def requeue_stale(session: AsyncSession, now: Optional[datetime] = None, *, tenant_id: Optional[int] = None,
                        in_thread: bool = True) -> List[str]:
    """Running tasks whose worker stopped beating: queued again, or failed after MAX_ATTEMPTS. Returns the sweeps touched;
    each is finalised here when that left no task queued or running (nobody else would finalise it)."""
    now = now or await db_now(session)
    T = BacktestSweepTaskRecord
    stale = [T.status == "running", or_(T.heartbeat_at < now - timedelta(seconds=STALE_SECONDS), T.heartbeat_at.is_(None))]
    if tenant_id is not None:
        stale.append(T.tenant_id == tenant_id)
    sids = sorted(set(await session.scalars(select(T.sweep_id).where(*stale))))
    if not sids:
        return []
    sync = {"synchronize_session": False}               # no in-Python evaluation of the time comparison on loaded rows
    await session.execute(update(T).where(*stale, T.attempts >= MAX_ATTEMPTS).values(status="failed", error="its worker stopped on every attempt")
                          .execution_options(**sync))
    await session.execute(update(T).where(*stale).values(status="queued", worker=None).execution_options(**sync))
    await session.commit()
    for sid in sids:
        await finalize_if_complete(session, sid, now, in_thread=in_thread)
    return sids


async def _close(session: AsyncSession, sweep_id: str, status: str, error: str, now: datetime) -> bool:
    """An active sweep ended without a result (cancelled / expired): its open tasks end with it. Conditional - False when
    it had already ended."""
    won = await session.execute(update(BacktestSweepRecord).where(
        BacktestSweepRecord.sweep_id == sweep_id, BacktestSweepRecord.status.in_(ACTIVE)).values(status=status, error=error[:300], finished_at=now))
    if won.rowcount:                                         # type: ignore[attr-defined]
        await session.execute(update(BacktestSweepTaskRecord).where(
            BacktestSweepTaskRecord.sweep_id == sweep_id, BacktestSweepTaskRecord.status.in_(("queued", "running")))
            .values(status=status, error=error[:300]))
    await session.commit()
    return bool(won.rowcount)                                # type: ignore[attr-defined]


async def expire_old(session: AsyncSession, now: Optional[datetime] = None, *, tenant_id: Optional[int] = None) -> List[str]:
    """Sweeps still queued / running / finalising EXPIRE_HOURS after they were queued (no worker, a crash while finalising):
    failed with the reason, so the organisation can queue another."""
    now = now or await db_now(session)
    q = select(BacktestSweepRecord.sweep_id).where(BacktestSweepRecord.status.in_(ACTIVE),
                                                   BacktestSweepRecord.created_at < now - timedelta(hours=EXPIRE_HOURS))
    if tenant_id is not None:
        q = q.where(BacktestSweepRecord.tenant_id == tenant_id)
    out = []
    for sid in list(await session.scalars(q)):
        if await _close(session, sid, "failed", f"expired: not finished {EXPIRE_HOURS} hours after it was queued", now):
            out.append(sid)
    return out


async def recover(session: AsyncSession, *, tenant_id: Optional[int] = None, now: Optional[datetime] = None,
                  in_thread: bool = True) -> List[str]:
    """Moves on the sweeps nobody else would: a dead worker's tasks are queued again (or failed), sweeps whose every task
    ended are finalised (the process that ran the last one stopped first), and sweeps past EXPIRE_HOURS are failed."""
    now = now or await db_now(session)
    touched = set(await requeue_stale(session, now, tenant_id=tenant_id, in_thread=in_thread))
    touched |= set(await expire_old(session, now, tenant_id=tenant_id))
    q = select(BacktestSweepRecord.sweep_id).where(BacktestSweepRecord.status.in_(("queued", "running")))
    if tenant_id is not None:
        q = q.where(BacktestSweepRecord.tenant_id == tenant_id)
    for sid in list(await session.scalars(q)):
        if await finalize_if_complete(session, sid, now, in_thread=in_thread):
            touched.add(sid)
    return sorted(touched)


async def cancel(session: AsyncSession, sweep_id: str, *, tenant_id: int, user_id: int,
                 now: Optional[datetime] = None) -> Tuple[Optional[BacktestSweepRecord], bool]:
    """The owner stops an active sweep (its open tasks are cancelled; a worker mid-task cannot write its result any more).
    (None, False): not this user's sweep in this organisation; (row, False): it had already ended."""
    row = await session.scalar(select(BacktestSweepRecord).where(BacktestSweepRecord.sweep_id == sweep_id,
                                                                 BacktestSweepRecord.tenant_id == tenant_id,
                                                                 BacktestSweepRecord.user_id == user_id))
    if row is None:
        return None, False
    stopped = False
    if row.status in ACTIVE:
        stopped = await _close(session, sweep_id, "cancelled", "cancelled by its owner", now or await db_now(session))
        await session.refresh(row)
    return row, stopped


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


def _owns(task_id: int, worker: str, attempts: int) -> Tuple[Any, ...]:
    """Only the worker that holds this claim (the same attempt, still running) may beat or write the task."""
    T = BacktestSweepTaskRecord
    return (T.id == task_id, T.worker == worker[:80], T.status == "running", T.attempts == attempts)


async def _beating(session: AsyncSession, work: Awaitable[Any], beat: Callable[[], Awaitable[None]]) -> Any:
    """Awaits `work` (the backtest, in a thread), beating every HEARTBEAT_SECONDS meanwhile - a long backtest is not taken
    for a dead worker. The session is free while `work` runs (only the beat uses it)."""
    fut = asyncio.ensure_future(work)
    try:
        while True:
            done, _ = await asyncio.wait({fut}, timeout=HEARTBEAT_SECONDS)
            if done:
                return fut.result()
            try:
                await beat()
            except Exception:  # noqa: BLE001 - a missed beat is retried; the backtest goes on
                logger.warning("Backtest sweep heartbeat failed", exc_info=True)
                await session.rollback()
    finally:
        if not fut.done():
            fut.cancel()


async def run_one(session: AsyncSession, worker: str, *, sweep_id: Optional[str] = None, clock: Optional[Callable[[], datetime]] = None,
                  in_thread: bool = True) -> Optional[str]:
    """Claim one task and run it; finalise its sweep when it was the last. Returns the task's sweep id (None: no task)."""
    import copy

    from fastapi.concurrency import run_in_threadpool

    from app.backtest import ledger
    from app.core.models import bars_to_dataframe
    from app.custom_strategies.resolver import resolve_strategy

    async def now() -> datetime:
        return clock() if clock is not None else await db_now(session)
    await requeue_stale(session, await now(), in_thread=in_thread)
    task = await claim(session, worker, await now(), sweep_id)
    if task is None:
        return None
    tid, sid, combo, symbol, task_id, attempts = task.tenant_id, task.sweep_id, task.combo, task.symbol, task.id, task.attempts
    owned = _owns(task_id, worker, attempts)
    sweep = await session.scalar(select(BacktestSweepRecord).where(BacktestSweepRecord.sweep_id == sid))
    assert sweep is not None
    spec = SweepSpec.model_validate_json(sweep.spec_json)
    uid = sweep.user_id
    if sweep.status == "queued":
        await session.execute(update(BacktestSweepRecord).where(BacktestSweepRecord.sweep_id == sid, BacktestSweepRecord.status == "queued")
                              .values(status="running", started_at=await now()))
        await session.commit()

    async def beat() -> None:
        await session.execute(update(BacktestSweepTaskRecord).where(*owned).values(heartbeat_at=await now()))
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
        await beat()                                          # the lake read may have taken a while
        payload = await _beating(session, run_in_threadpool(_task_backtest, *args), beat) if in_thread else _task_backtest(*args)
    except Exception as exc:  # noqa: BLE001 - the task fails with its reason; the sweep goes on
        await session.rollback()
        status, error = "failed", f"{type(exc).__name__}: {exc}"[:300]
        if not isinstance(exc, (SweepError, ledger.LedgerError, ledger.HoldoutError)):
            logger.exception("Backtest sweep task %s failed", task_id)
    # Conditional on this claim: a worker whose task was requeued (and maybe claimed again) or cancelled writes nothing.
    await session.execute(update(BacktestSweepTaskRecord).where(*owned)
                          .values(status=status, error=error, heartbeat_at=await now(),
                                  result_json=json.dumps(payload, sort_keys=True) if payload is not None else None))
    await session.commit()
    await finalize_if_complete(session, sid, await now(), in_thread=in_thread)
    return sid


async def finalize_if_complete(session: AsyncSession, sweep_id: str, now: datetime, *, in_thread: bool = True) -> bool:
    """When no task is left queued or running, ONE caller (conditional update) merges the results and finalises."""
    from app.ai.research import record_trial
    left = await session.scalar(select(func.count()).select_from(BacktestSweepTaskRecord).where(
        BacktestSweepTaskRecord.sweep_id == sweep_id, BacktestSweepTaskRecord.status.in_(("queued", "running"))))
    if left:
        return False
    won = await session.execute(update(BacktestSweepRecord).where(
        BacktestSweepRecord.sweep_id == sweep_id, BacktestSweepRecord.status.in_(("queued", "running"))).values(status="finalizing"))
    await session.commit()
    if not won.rowcount:                                     # type: ignore[attr-defined]
        return False
    sweep = await session.scalar(select(BacktestSweepRecord).where(BacktestSweepRecord.sweep_id == sweep_id)
                                 .execution_options(populate_existing=True))
    assert sweep is not None
    tenant_id, user_id = sweep.tenant_id, sweep.user_id
    tasks = list(await session.scalars(select(BacktestSweepTaskRecord).where(BacktestSweepTaskRecord.sweep_id == sweep_id)
                                       .order_by(BacktestSweepTaskRecord.idx).execution_options(populate_existing=True)))
    finishing = (BacktestSweepRecord.sweep_id == sweep_id, BacktestSweepRecord.status == "finalizing")
    try:
        result, trials = await _merge(session, sweep, tasks, now, in_thread=in_thread)
        ok = result["combinations_completed"] > 0
        for trial in trials:                                 # every combination tried is a trial: the grid is the search
            await record_trial(session, tenant_id=tenant_id, user_id=user_id, study_id=sweep_id, status="ok", **trial)
        wrote = await session.execute(update(BacktestSweepRecord).where(*finishing).values(
            status="done" if ok else "failed", error=None if ok else "every backtest of this sweep failed (see the tasks' reasons)",
            result_json=json.dumps(result, sort_keys=True, default=str), finished_at=now))
        if not wrote.rowcount:                               # type: ignore[attr-defined]  # cancelled / expired meanwhile
            await session.rollback()                         # nothing of it is kept, its trials included
            return False
        await session.commit()
    except Exception as exc:  # noqa: BLE001 - the sweep fails with its reason
        logger.exception("Backtest sweep %s could not be finalised", sweep_id)
        await session.rollback()
        await session.execute(update(BacktestSweepRecord).where(*finishing).values(
            status="failed", error=f"{type(exc).__name__}: {exc}"[:300], finished_at=now))
        await session.commit()
    return True


def _metric(m: Dict[str, Any], name: str) -> Optional[float]:
    v = m.get(name)
    return None if v is None or (isinstance(v, float) and not math.isfinite(v)) else float(v)


async def _merge(session: AsyncSession, sweep: BacktestSweepRecord, tasks: List[BacktestSweepTaskRecord], now: datetime, *,
                 in_thread: bool = True) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """The organisation's earlier sweep trials (database), then the merge itself off the event loop."""
    from fastapi.concurrency import run_in_threadpool

    from app.backtest import ledger
    earlier = await ledger.earlier_sharpes(session, sweep.tenant_id, now - timedelta(days=ledger.LEDGER_DAYS), KIND)
    rows = [(t.combo, t.symbol, t.result_json, t.error, t.status) for t in tasks]
    args = (sweep.sweep_id, sweep.spec_json, rows, earlier)
    return await run_in_threadpool(_merge_compute, *args) if in_thread else _merge_compute(*args)


def _merge_compute(sweep_id: str, spec_json: str, rows: List[Tuple[int, str, Optional[str], Optional[str], str]],
                   earlier: List[float]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """CPU part of finalising (pure): the merged result, and one ledger trial (record_trial's keywords) per complete
    combination."""
    from app.backtest import ledger
    from app.backtest.repro import config_hash
    from app.backtest.validation import deflated_sharpe, pbo_cscv, sharpe
    from app.ai.research import _pbo_blocks
    spec = SweepSpec.model_validate_json(spec_json)
    combos = spec.combos()
    by_combo: Dict[int, List[Tuple[str, Optional[Dict[str, Any]], Optional[str]]]] = {c: [] for c in range(len(combos))}
    for combo, symbol, result_json, error, _ in rows:
        by_combo[combo].append((symbol, json.loads(result_json) if result_json else None, error))
    days = sorted({d for rws in by_combo.values() for _, r, _ in rws if r for d in r["returns"]})
    out_combos: List[Dict[str, Any]] = []
    matrix: List[List[float]] = []
    trials: List[Dict[str, Any]] = []
    for c, params in enumerate(combos):
        rws = by_combo[c]
        done = [(s, r) for s, r, _ in rws if r is not None]
        entry: Dict[str, Any] = {"combo": c, "params": params, "symbols": {s: ({k: v for k, v in r.items() if k != "returns"} if r else {"error": e})
                                                                        for s, r, e in rws}}
        if len(done) == len(rws) and done:
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
        trials.append({"dsl": {"kind": KIND, "sweep_id": sweep_id, "strategy_id": spec.strategy_id, "params": e["params"],
                               "symbols": spec.symbols, "base_timeframe": spec.base_timeframe, "data_source": ledger.lake_label(spec.lake)},
                       "symbol": f"SWEEP:{len(spec.symbols)}", "exchange": spec.lake.exchange, "timeframe": spec.base_timeframe,
                       "data_from": datetime.combine(spec.lake.start, datetime.min.time(), tzinfo=timezone.utc),
                       "data_to": datetime.combine(spec.lake.end, datetime.min.time(), tzinfo=timezone.utc),
                       "metrics": {k: e.get(k) for k in ("net_pnl", "total_trades", "sharpe")}, "returns": e["_returns"]})
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
    merged = {"sweep_id": sweep_id, "strategy_id": spec.strategy_id, "metric": spec.metric, "symbols": spec.symbols,
              "window": {"start": str(spec.lake.start), "end": str(spec.lake.end)}, "data_source": ledger.lake_label(spec.lake),
              "combinations": out_combos, "combinations_completed": len(complete), "tasks": len(rows),
              "tasks_failed": sum(1 for r in rows if r[4] == "failed"),
              "best": {"combo": best["combo"], "params": best["params"]} if best else None, "deflation": deflation, "pbo": pbo}
    merged["result_hash"] = config_hash(combinations=out_combos, best=merged["best"])
    merged["disclaimer"] = ("A search over parameter combinations on past data: the best one is deflated for every combination "
                            "tried. Not a recommendation, and not a prediction.")
    return merged, trials


async def drain(factory: async_sessionmaker[AsyncSession], worker: str, sweep_id: Optional[str] = None, limit: int = MAX_TASKS) -> int:
    """Run tasks until none is left (of `sweep_id`, when given) - the in-process fallback and the worker's loop step."""
    ran = 0
    while ran < limit:
        async with factory() as session:
            if await run_one(session, worker, sweep_id=sweep_id) is None:
                break
        ran += 1
    return ran


_INLINE_DRAINS: Set[str] = set()                     # sweeps this API process is running inline right now


def inline_worker_name() -> str:
    """One name per inline drain: two drains in the same process never pass for each other's claims."""
    return f"api-inline:{os.getpid()}:{uuid.uuid4().hex[:12]}"


async def drain_inline(factory: async_sessionmaker[AsyncSession], sweep_id: str) -> int:
    """The API runs a sweep's queued tasks itself (BACKTEST_SWEEP_INLINE) - once per sweep per process."""
    if sweep_id in _INLINE_DRAINS:
        return 0
    _INLINE_DRAINS.add(sweep_id)
    try:
        return await drain(factory, inline_worker_name(), sweep_id)
    finally:
        _INLINE_DRAINS.discard(sweep_id)


async def resume_inline(factory: async_sessionmaker[AsyncSession]) -> int:
    """API start-up while BACKTEST_SWEEP_INLINE is on: the sweeps a stopped API process left behind are settled (`recover`)
    and their queued tasks run here - nothing else would run them without a worker."""
    async with factory() as session:
        await recover(session)
        sids = sorted(set(await session.scalars(select(BacktestSweepTaskRecord.sweep_id).where(BacktestSweepTaskRecord.status == "queued"))))
    ran = 0
    for sid in sids:
        ran += await drain_inline(factory, sid)
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


__all__ = ["FLAG", "KIND", "SweepSpec", "SweepError", "SweepBusy", "plan", "enqueue", "active_sweep", "claim", "run_one",
           "requeue_stale", "expire_old", "recover", "cancel", "finalize_if_complete", "drain", "drain_inline", "resume_inline",
           "inline_worker_name", "progress", "sweep_dict", "db_now", "MAX_TASKS", "MAX_COMBOS", "MAX_GRID_KEYS", "STALE_SECONDS",
           "MAX_ATTEMPTS", "EXPIRE_HOURS", "HEARTBEAT_SECONDS"]
