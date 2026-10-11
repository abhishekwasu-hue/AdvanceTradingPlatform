"""H-C3c: research studies as jobs. The API queues a study (`enqueue`); the research worker (`app/workers/
research_worker.py`, a separate process) claims and runs one at a time (`run_next`). A study's model calls and backtests
take minutes, so they never run inside an API request or the trading worker's loop (exits are never delayed by research,
ADR-0004). Progress is the trial ledger itself: every draft is committed as it is tried.

Job states: queued -> running -> done | failed; a running study whose worker stopped beating is marked `interrupted`
(its trials so far stay in the ledger and still count against later studies).
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ResearchStudyRecord, ResearchTrialRecord, Tenant, User

logger = logging.getLogger(__name__)

STALE_MINUTES = 20            # no heartbeat for this long: the worker stopped (restart, crash) - the study is interrupted
ACTIVE = ("queued", "running")


class StudyBusy(Exception):
    """This organisation already has a study queued or running (one at a time keeps model spend predictable)."""


def _utc(dt: Optional[datetime]) -> Optional[datetime]:
    return None if dt is None else (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc))


async def enqueue(session: AsyncSession, *, tenant_id: int, user_id: int, idea: str, symbol: str, exchange: str, timeframe: str,
                  days: int, max_drafts: int, now: Optional[datetime] = None) -> ResearchStudyRecord:
    active = await session.scalar(select(func.count()).select_from(ResearchStudyRecord).where(
        ResearchStudyRecord.tenant_id == tenant_id, ResearchStudyRecord.status.in_(ACTIVE)))
    if active:
        raise StudyBusy("A research study is already queued or running for this organisation.")
    row = ResearchStudyRecord(study_id=str(uuid.uuid4()), tenant_id=tenant_id, user_id=user_id, idea=idea, symbol=symbol.upper(),
                              exchange=exchange, timeframe=timeframe, days=days, max_drafts=max_drafts, status="queued",
                              usage_json="{}", created_at=now or datetime.now(timezone.utc))
    session.add(row)
    await session.commit()
    return row


async def mark_stale(session: AsyncSession, now: datetime) -> int:
    """Running studies with no heartbeat for STALE_MINUTES become `interrupted`."""
    cutoff = now - timedelta(minutes=STALE_MINUTES)
    result = await session.execute(update(ResearchStudyRecord)
                                   .where(ResearchStudyRecord.status == "running", ResearchStudyRecord.heartbeat_at < cutoff)
                                   .values(status="interrupted", finished_at=now,
                                           error="The research worker stopped during this study; the drafts tried so far are kept."))
    await session.commit()
    return int(result.rowcount or 0)    # type: ignore[attr-defined]


async def claim_next(session: AsyncSession, now: datetime) -> Optional[ResearchStudyRecord]:
    """The oldest queued study, claimed atomically (a second worker's claim of the same row changes nothing)."""
    candidate = await session.scalar(select(ResearchStudyRecord).where(ResearchStudyRecord.status == "queued")
                                     .order_by(ResearchStudyRecord.created_at, ResearchStudyRecord.id).limit(1))
    if candidate is None or not await _try_claim(session, candidate.id, now):
        return None
    await session.refresh(candidate)
    return candidate


async def _try_claim(session: AsyncSession, row_id: int, now: datetime) -> bool:
    """queued -> running only if it is still queued: of two workers that saw the same row, one wins."""
    claimed = await session.execute(update(ResearchStudyRecord)
                                    .where(ResearchStudyRecord.id == row_id, ResearchStudyRecord.status == "queued")
                                    .values(status="running", started_at=now, heartbeat_at=now))
    await session.commit()
    return bool(claimed.rowcount)    # type: ignore[attr-defined]


async def _finish(session: AsyncSession, row: ResearchStudyRecord, status: str, now: datetime, error: Optional[str] = None,
                  **values: Any) -> None:
    row.status, row.finished_at, row.error = status, now, (error or None) and str(error)[:300]
    for k, v in values.items():
        setattr(row, k, v)
    await session.commit()


Clock = Callable[[], datetime]


async def run_next(session: AsyncSession, *, now: Optional[Clock] = None) -> Optional[str]:
    """Runs the next queued study to its end. Returns its study_id (None when the queue is empty)."""
    from app.ai import metering, research_loop
    from app.ai import settings as ai_settings
    from app.ai.tools import ToolContext
    from app.ai.tools import market
    from app.backtest.data_policy import filter_allowed, holdout_start
    from app.core.models import RiskConfig
    from app.risk_engine.routes import get_tenant_risk_config
    clock = now or (lambda: datetime.now(timezone.utc))
    await mark_stale(session, clock())
    row = await claim_next(session, clock())
    if row is None:
        return None
    # read everything up front: commits below expire the row, and an async session cannot lazy-load
    sid, tid, uid = row.study_id, row.tenant_id, row.user_id
    spec = research_loop.StudyInput(row.idea, row.symbol, row.exchange, row.timeframe, row.max_drafts)
    days = row.days
    try:
        from app.platform.controls import flag_enabled
        for flag in ("ai_copilot", "ai_research"):                   # turned off after queueing: the study does not run
            if not await flag_enabled(session, flag, tid):
                await _finish(session, row, "failed", clock(), f"The {flag} feature was turned off before this study ran.")
                return sid
        user = await session.get(User, uid) if uid else None
        tenant = await session.get(Tenant, tid)
        if user is None or tenant is None or user.tenant_id != tid:
            await _finish(session, row, "failed", clock(), "The user who asked for this study no longer exists.")
            return sid
        provider = await ai_settings.provider_for(session, tenant, task="generation", user_id=uid)
        if getattr(provider, "name", "") == "rule_based":
            await _finish(session, row, "failed", clock(), "The research loop needs an AI provider (Settings > AI provider).")
            return sid
        frame, source = await market._frame(ToolContext(session, tid, user), spec.symbol, spec.exchange, spec.timeframe, days)
        frame = filter_allowed(frame, holdout_start())                # the sealed holdout is cut off, never searched
        if len(frame) < 50:
            await _finish(session, row, "failed", clock(), f"Only {len(frame)} bars before the sealed holdout; a study needs at least 50.",
                          data_source=source)
            return sid
        risk = await get_tenant_risk_config(tid, session) or RiskConfig()
        inner = research_loop.llm_proposer(provider)

        async def propose(idea: str, history: Any) -> Any:           # each draft is a heartbeat: the study is alive
            row.heartbeat_at = clock()
            await session.commit()
            return await inner(idea, history)

        await research_loop.run_study(session, tenant_id=tid, user_id=uid, spec=spec, frame=frame, risk=risk, propose=propose, study_id=sid)
        await _finish(session, row, "done", clock(), data_source=source, usage_json=json.dumps(metering.spent_by(provider), default=str))
    except research_loop.HoldoutError as exc:
        await session.rollback()
        await _finish(session, row, "failed", clock(), f"The window reaches the sealed holdout: {exc}")
    except Exception as exc:  # noqa: BLE001 - the study fails with its reason; the worker goes on to the next
        logger.exception("Research study %s failed", sid)
        await session.rollback()
        await _finish(session, row, "failed", clock(), f"{type(exc).__name__}: {exc}")
    return sid


async def progress(session: AsyncSession, row: ResearchStudyRecord) -> dict:
    tried = await session.scalar(select(func.count()).select_from(ResearchTrialRecord).where(
        ResearchTrialRecord.tenant_id == row.tenant_id, ResearchTrialRecord.study_id == row.study_id,
        ResearchTrialRecord.status != "oos"))
    return {"drafts_tried": int(tried or 0), "max_drafts": row.max_drafts}


def study_dict(row: ResearchStudyRecord) -> dict:
    def iso(d: Optional[datetime]) -> Optional[str]:
        u = _utc(d)
        return u.isoformat() if u else None
    return {"study_id": row.study_id, "status": row.status, "error": row.error, "idea": row.idea, "symbol": row.symbol,
            "exchange": row.exchange, "timeframe": row.timeframe, "days": row.days, "data_source": row.data_source,
            "usage": json.loads(row.usage_json or "{}"), "created_at": iso(row.created_at), "started_at": iso(row.started_at),
            "finished_at": iso(row.finished_at)}


__all__ = ["enqueue", "run_next", "claim_next", "mark_stale", "progress", "study_dict", "StudyBusy", "STALE_MINUTES"]
