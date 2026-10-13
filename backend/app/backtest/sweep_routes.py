"""N7 C6: distributed backtest sweeps over HTTP (flag `backtest_distributed`, off by default; lake reads also need
`backtest_ledger`).

* `POST /api/backtest/sweeps`      - queue a sweep (a parameter grid x symbols on the lake's day bars); 202 with its id
* `GET  /api/backtest/sweeps`      - the caller's own sweeps (newest first)
* `GET  /api/backtest/sweeps/{id}` - one of the caller's sweeps: status, task counts and, when done, the merged result
* `POST /api/backtest/sweeps/{id}/cancel` - the caller stops one of their own active sweeps

Workers run the tasks (`app/backtest/sweeps.py`); while BACKTEST_SWEEP_INLINE is on (the Phase 0 default, no extra
process) the API runs the queued tasks itself after it has answered, one at a time and off the event loop. A sweep never
blocks its organisation for ever: a POST that finds one active first settles what nobody would move on (`sweeps.recover`:
a dead worker's tasks, an unfinalised or expired sweep) and, inline, runs the active sweep's queued tasks again; its owner
may also cancel it. A simulation only - no order path.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import JSONResponse
from starlette.background import BackgroundTask
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.dependencies import get_current_user
from app.backtest import ledger
from app.backtest import sweeps as SW
from app.core.rate_limit import user_rate_limit
from app.db.models import BacktestSweepRecord, User
from app.db.session import get_session

router = APIRouter(tags=["backtest"])
sweep_rate_limit = user_rate_limit("analysis", limit=60, window_seconds=60)      # the /api/backtest budget


def inline_on() -> bool:
    return os.getenv("BACKTEST_SWEEP_INLINE", "1").strip().lower() not in ("0", "false", "no", "off")


def _factory(session: AsyncSession) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(session.bind, expire_on_commit=False)


@router.post("/api/backtest/sweeps", status_code=202, dependencies=[Depends(sweep_rate_limit)])
async def create_sweep(spec: SW.SweepSpec, background: BackgroundTasks, user: User = Depends(get_current_user),
                       session: AsyncSession = Depends(get_session)) -> Any:
    from app.platform.controls import require_flag
    await require_flag(session, SW.FLAG, user.tenant_id)
    if not await ledger.enabled(session, user.tenant_id):
        raise HTTPException(status_code=403, detail=f"sweeps run on the server's lake bars, which need the flag {ledger.FLAG} "
                                                    "for this organisation")
    if spec.base_timeframe.strip().lower() not in ledger.LAKE_TIMEFRAMES:
        raise HTTPException(status_code=422, detail="base_timeframe: day bars only (the lake's NSE bhavcopy bars)")
    try:
        row = await SW.enqueue(session, tenant_id=user.tenant_id, user_id=user.id, spec=spec)
    except SW.SweepBusy as exc:
        # Inline, nothing else runs the active sweep's tasks after an API restart: run its queued ones again (after the
        # answer; once per process). The 409 is sent either way - a background task on a raised exception would not run.
        active = await SW.active_sweep(session, user.tenant_id) if inline_on() else None
        task = BackgroundTask(SW.drain_inline, _factory(session), active) if active else None
        return JSONResponse(status_code=409, content={"detail": str(exc), "active_sweep_id": active}, background=task)
    except SW.SweepError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ledger.HoldoutError as exc:
        raise HTTPException(status_code=422, detail=f"lake: {exc}; the holdout is kept for one final check") from exc
    sid = row.sweep_id
    if inline_on():
        background.add_task(SW.drain_inline, _factory(session), sid)
    return {"sweep_id": sid, "status": "queued", "total": row.total, "inline": inline_on()}


@router.get("/api/backtest/sweeps")
async def list_sweeps(limit: int = 20, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> List[Dict[str, Any]]:
    from app.platform.controls import require_flag
    await require_flag(session, SW.FLAG, user.tenant_id)
    rows = await session.scalars(select(BacktestSweepRecord).where(BacktestSweepRecord.tenant_id == user.tenant_id,
                                                                   BacktestSweepRecord.user_id == user.id)
                                 .order_by(BacktestSweepRecord.id.desc()).limit(max(1, min(int(limit), 100))))
    return [SW.sweep_dict(r, full=False) for r in rows]


@router.get("/api/backtest/sweeps/{sweep_id}")
async def get_sweep(sweep_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    from app.platform.controls import require_flag
    await require_flag(session, SW.FLAG, user.tenant_id)
    row = await session.scalar(select(BacktestSweepRecord).where(BacktestSweepRecord.sweep_id == sweep_id,
                                                                 BacktestSweepRecord.tenant_id == user.tenant_id,
                                                                 BacktestSweepRecord.user_id == user.id))
    if row is None:
        raise HTTPException(status_code=404, detail="no such sweep")
    return SW.sweep_dict(row, await SW.progress(session, row.sweep_id))


@router.post("/api/backtest/sweeps/{sweep_id}/cancel")
async def cancel_sweep(sweep_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Stops one of the caller's own active sweeps (another user's or organisation's: 404). Its open tasks are cancelled; a
    worker mid-task cannot write its result any more, and the sweep is never finalised."""
    from app.platform.controls import require_flag
    await require_flag(session, SW.FLAG, user.tenant_id)
    row, stopped = await SW.cancel(session, sweep_id, tenant_id=user.tenant_id, user_id=user.id)
    if row is None:
        raise HTTPException(status_code=404, detail="no such sweep")
    if not stopped:
        raise HTTPException(status_code=409, detail=f"this sweep has already ended ({row.status})")
    return SW.sweep_dict(row, await SW.progress(session, row.sweep_id))
