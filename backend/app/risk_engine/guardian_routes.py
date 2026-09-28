"""Phase V1: the Risk Guardian's status and the market-events calendar (rule M8)."""
from datetime import date, datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.auth.dependencies import get_current_user, require_trader
from app.core.enums import UserRole
from app.core.models import RiskConfig
from app.db.models import MarketEventRecord, User
from app.db.session import get_session
from app.platform import controls
from app.risk_engine import guardian
from app.risk_engine.routes import get_tenant_risk_config

router = APIRouter(prefix="/api/risk-guardian", tags=["risk"])


class MarketEventBody(BaseModel):
    event_date: date
    underlying: Optional[str] = Field(default=None, max_length=50, description='one underlying, "INDEX" for the index bucket, empty = all')
    start_time: Optional[str] = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    end_time: Optional[str] = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    kind: str = Field(default="OTHER", max_length=30)
    action: str = Field(default="SIZE_CUT", pattern="^(BLOCK|SIZE_CUT|block|size_cut)$")
    size_cut_pct: Optional[float] = Field(default=None, ge=0, le=100)
    description: str = Field(default="", max_length=200)
    global_event: bool = Field(default=False, description="platform-wide (SUPER_ADMIN only)")


async def _effective_config(session: AsyncSession, tenant_id: int) -> RiskConfig:
    cfg = await get_tenant_risk_config(tenant_id, session) or RiskConfig()
    cfg, _ = controls.clamp_config(cfg, await controls.risk_ceilings(session))
    return cfg


@router.get("/status")
async def guardian_status(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """Drawdown per mode, open risk by correlation bucket, active cool-downs, today's events,
    the effective (ceiling-clamped) settings and the ceilings themselves."""
    cfg = await _effective_config(session, user.tenant_id)
    result = await guardian.status(session, user.tenant_id, cfg)
    result["ceilings"] = await controls.risk_ceilings(session)
    return result


@router.get("/events")
async def list_events(
    from_date: Optional[date] = Query(default=None, alias="from"), to_date: Optional[date] = Query(default=None, alias="to"),
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> List[dict]:
    """Global events plus this tenant's own, default the next 30 days."""
    from sqlalchemy import select
    start = from_date or datetime.now(guardian.IST).date()
    end = to_date or (start + timedelta(days=30))
    rows = await session.scalars(select(MarketEventRecord).where(
        MarketEventRecord.event_date >= start, MarketEventRecord.event_date <= end,
        (MarketEventRecord.tenant_id.is_(None)) | (MarketEventRecord.tenant_id == user.tenant_id),
    ).order_by(MarketEventRecord.event_date, MarketEventRecord.id))
    return [guardian.event_as_dict(r) for r in rows]


@router.post("/events", status_code=201)
async def create_event(body: MarketEventBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> dict:
    if body.global_event and user.role != UserRole.SUPER_ADMIN.value:
        raise HTTPException(status_code=403, detail="Only the platform operator can add a global market event")
    if body.start_time and body.end_time and body.start_time > body.end_time:
        raise HTTPException(status_code=400, detail="start_time must not be after end_time")
    kind = body.kind.strip().upper() or "OTHER"
    if kind not in guardian.EVENT_KINDS:
        raise HTTPException(status_code=400, detail=f"kind must be one of {list(guardian.EVENT_KINDS)}")
    row = MarketEventRecord(
        tenant_id=None if body.global_event else user.tenant_id, underlying=(body.underlying or "").strip().upper() or None,
        event_date=body.event_date, start_time=body.start_time, end_time=body.end_time, kind=kind,
        action=body.action.upper(), size_cut_pct=body.size_cut_pct, description=body.description.strip(), created_by=user.id,
    )
    session.add(row)
    await write_audit_log(session, None if body.global_event else user.tenant_id, user.id, "market_event_added",
                          f"{row.event_date} {row.kind} {row.action} {row.underlying or '*'}")
    await session.commit()
    await session.refresh(row)
    return guardian.event_as_dict(row)


@router.delete("/events/{event_id}", status_code=204)
async def delete_event(event_id: int, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> None:
    row = await session.get(MarketEventRecord, event_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such event")
    if row.tenant_id is None and user.role != UserRole.SUPER_ADMIN.value:
        raise HTTPException(status_code=403, detail="Only the platform operator can remove a global market event")
    if row.tenant_id is not None and row.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="No such event")
    await session.delete(row)
    await write_audit_log(session, row.tenant_id, user.id, "market_event_removed", f"{row.event_date} {row.kind} {row.underlying or '*'}")
    await session.commit()
