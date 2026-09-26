from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.core.config import WORKER_CYCLE_SECONDS
from app.db.models import User, WorkerHeartbeatRecord
from app.db.session import get_session
from app.market_data.calendar import market_session_status

router = APIRouter(prefix="/api/system", tags=["system"])

# A heartbeat older than this many cycles means the worker is down (or wedged), not just slow.
STALE_AFTER_CYCLES = 3


class WorkerStatusResponse(BaseModel):
    worker_name: str
    running: bool
    healthy: bool
    last_seen_at: Optional[str]
    seconds_since_heartbeat: Optional[int]
    cycle_count: int
    last_cycle_ms: Optional[int]
    last_error: Optional[str]
    cycle_seconds: int
    market_open: bool
    market_status: str
    next_market_open: Optional[str]


@router.get("/worker-status", response_model=WorkerStatusResponse)
async def worker_status(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> WorkerStatusResponse:
    """Is the autonomous trading engine alive? Read from the worker's heartbeat row (written once
    per cycle) plus the market calendar, so the Dashboard can show "worker last seen 12s ago /
    market open" - and ops can alert on a heartbeat that goes stale during a trading session.
    Platform-wide, not tenant-specific: one worker serves every tenant."""
    record = await session.scalar(select(WorkerHeartbeatRecord).where(WorkerHeartbeatRecord.worker_name == "trading_worker"))
    session_status = await market_session_status(session)
    now = datetime.now(timezone.utc)

    if record is None:
        return WorkerStatusResponse(
            worker_name="trading_worker", running=False, healthy=False, last_seen_at=None, seconds_since_heartbeat=None,
            cycle_count=0, last_cycle_ms=None, last_error=None, cycle_seconds=WORKER_CYCLE_SECONDS,
            market_open=session_status.is_open, market_status=session_status.reason,
            next_market_open=session_status.next_open.isoformat() if session_status.next_open else None,
        )

    last_seen = record.last_seen_at if record.last_seen_at.tzinfo else record.last_seen_at.replace(tzinfo=timezone.utc)
    age = int((now - last_seen).total_seconds())
    running = age <= WORKER_CYCLE_SECONDS * STALE_AFTER_CYCLES
    return WorkerStatusResponse(
        worker_name=record.worker_name, running=running, healthy=running and not record.last_error,
        last_seen_at=last_seen.isoformat(), seconds_since_heartbeat=age, cycle_count=record.cycle_count,
        last_cycle_ms=record.last_cycle_ms, last_error=record.last_error, cycle_seconds=WORKER_CYCLE_SECONDS,
        market_open=session_status.is_open, market_status=session_status.reason,
        next_market_open=session_status.next_open.isoformat() if session_status.next_open else None,
    )
