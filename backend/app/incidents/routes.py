"""Phase M / V4.10: `/api/admin/incidents` (SUPER_ADMIN, MFA)."""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_mfa_session, require_role
from app.db.models import IncidentRecord, User
from app.db.session import get_session
from app.incidents import service

router = APIRouter(prefix="/api/admin/incidents", tags=["admin"], dependencies=[Depends(require_role()), Depends(require_mfa_session)])


class IncidentCreate(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    severity: str = Field(default="WARNING", pattern=r"^(WARNING|CRITICAL|EMERGENCY)$")
    summary: str = Field(default="", max_length=5000)
    tenant_id: Optional[int] = None


class IncidentUpdate(BaseModel):
    status: Optional[str] = Field(default=None, pattern=r"^(OPEN|MITIGATED|RESOLVED)$")
    summary: Optional[str] = Field(default=None, max_length=5000)
    root_cause: Optional[str] = Field(default=None, max_length=5000)
    actions_taken: Optional[str] = Field(default=None, max_length=5000)
    data_loss_minutes: Optional[float] = Field(default=None, ge=0)
    downtime_minutes: Optional[float] = Field(default=None, ge=0)


@router.get("")
async def list_incidents(status: Optional[str] = Query(default=None), limit: int = Query(default=100, ge=1, le=500),
                         session: AsyncSession = Depends(get_session)) -> List[dict]:
    return [service.as_dict(r) for r in await service.list_incidents(session, status=status, limit=limit)]


@router.post("", status_code=201)
async def create_incident(body: IncidentCreate, user: User = Depends(require_role()), session: AsyncSession = Depends(get_session)) -> dict:
    record = await service.open_incident(session, title=body.title, severity=body.severity, summary=body.summary, tenant_id=body.tenant_id, user=user)
    return service.as_dict(record)


@router.patch("/{incident_id}")
async def update_incident(incident_id: int, body: IncidentUpdate, user: User = Depends(require_role()), session: AsyncSession = Depends(get_session)) -> dict:
    record = await session.get(IncidentRecord, incident_id)
    if record is None:
        raise HTTPException(status_code=404, detail="No such incident")
    try:
        record = await service.update_incident(session, record, user, **body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return service.as_dict(record)
