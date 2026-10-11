"""Part D4: Settings > Static IP - the IPs an organisation registered with its brokers (SEBI retail-algo framework)."""
from dataclasses import asdict
from datetime import date, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.auth.dependencies import get_current_user, require_owner, require_role
from app.compliance import golive, rules, static_ip
from app.core import config
from app.core.enums import UserRole
from app.db.models import User
from app.db.session import get_session

router = APIRouter(prefix="/api/compliance", tags=["compliance"])


class StaticIpBody(BaseModel):
    broker_name: str = Field(min_length=2, max_length=50)
    role: str = Field(default="PRIMARY", pattern=r"^(PRIMARY|BACKUP|primary|backup)$")
    ip: str = Field(min_length=3, max_length=45)
    registered_at: Optional[datetime] = None


def _row(r) -> dict:
    return {"broker_name": r.broker_name, "role": r.role, "ip": r.ip,
            "registered_at": r.registered_at.isoformat() if r.registered_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None}


@router.get("/static-ips")
async def list_static_ips(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    rows = await static_ip.registered(session, user.tenant_id)
    shared = await static_ip.shared_with_others(session, user.tenant_id)
    server_ip = config.SERVER_EGRESS_IP
    warnings: List[str] = [f"{ip} is also registered by {n} other organisation(s) - brokers can refuse a shared IP" for ip, n in shared.items()]
    if server_ip and rows and server_ip not in {r.ip for r in rows}:
        warnings.append(f"This server's egress IP {server_ip} is not among your registered IPs")
    return {"server_egress_ip": server_ip, "required_for_live": config.STATIC_IP_REQUIRED_FOR_LIVE,
            "max_changes_per_week": rules.load().param(static_ip.RULE, "max_changes_per_week"),
            "ips": [_row(r) for r in rows], "warnings": warnings}


@router.put("/static-ips")
async def set_static_ip(body: StaticIpBody, user: User = Depends(require_owner), session: AsyncSession = Depends(get_session)) -> dict:
    try:
        record = await static_ip.set_ip(session, user.tenant_id, body.broker_name, body.role, body.ip, user_id=user.id,
                                        registered_at=body.registered_at)
    except static_ip.StaticIpError as exc:
        raise HTTPException(status_code=409 if "changed" in str(exc) else 422, detail=str(exc)) from exc
    await write_audit_log(session, user.tenant_id, user.id, "static_ip_set", f"{record.broker_name} {record.role} -> {record.ip}")
    await session.commit()
    return _row(record)


class EvidenceBody(BaseModel):
    item_id: str = Field(min_length=3, max_length=60)
    reference: str = Field(min_length=1, max_length=500)
    valid_until: Optional[date] = None


@router.get("/golive")
async def golive_items(user: User = Depends(require_role(UserRole.SUPER_ADMIN)), session: AsyncSession = Depends(get_session)) -> dict:
    """Part D6: the go-live items only a human can evidence, plus the two the platform checks itself (SUPER_ADMIN)."""
    return {"items": [asdict(i) for i in await golive.evaluate(session)],
            "breach_notify_hours": rules.load().param(golive.RULE, "dpdp_breach_notify_hours")}


@router.put("/golive/evidence")
async def record_golive_evidence(body: EvidenceBody, user: User = Depends(require_role(UserRole.SUPER_ADMIN)),
                                 session: AsyncSession = Depends(get_session)) -> dict:
    try:
        row = await golive.record_evidence(session, body.item_id, body.reference, body.valid_until, user.id)
    except golive.EvidenceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await write_audit_log(session, user.tenant_id, user.id, "golive_evidence", f"{row.item_id}: {row.reference}"
                          + (f" (valid until {row.valid_until.isoformat()})" if row.valid_until else ""))
    await session.commit()
    return {"item_id": row.item_id, "reference": row.reference, "valid_until": row.valid_until.isoformat() if row.valid_until else None}
