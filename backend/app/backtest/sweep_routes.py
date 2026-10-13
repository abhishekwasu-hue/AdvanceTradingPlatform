"""N7 C6: distributed backtest sweeps over HTTP (flag `backtest_distributed`, off by default; lake reads also need
`backtest_ledger`).

* `POST /api/backtest/sweeps`      - queue a sweep (a parameter grid x symbols on the lake's day bars); 202 with its id
* `GET  /api/backtest/sweeps`      - the caller's own sweeps (newest first)
* `GET  /api/backtest/sweeps/{id}` - one of the caller's sweeps: status, task counts and, when done, the merged result

Workers run the tasks (`app/backtest/sweeps.py`); while BACKTEST_SWEEP_INLINE is on (the Phase 0 default, no extra
process) the API runs the queued tasks itself after it has answered, one at a time and off the event loop. A
simulation only - no order path.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
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


@router.post("/api/backtest/sweeps", status_code=202, dependencies=[Depends(sweep_rate_limit)])
async def create_sweep(spec: SW.SweepSpec, background: BackgroundTasks, user: User = Depends(get_current_user),
                       session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
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
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SW.SweepError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ledger.HoldoutError as exc:
        raise HTTPException(status_code=422, detail=f"lake: {exc}; the holdout is kept for one final check") from exc
    sid = row.sweep_id
    if inline_on():
        factory = async_sessionmaker(session.bind, expire_on_commit=False)
        background.add_task(SW.drain, factory, f"api-inline:{os.getpid()}", sid)
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
