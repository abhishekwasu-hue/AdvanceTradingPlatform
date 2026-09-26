"""Phase I1: risk limits and risk events API (master prompt section 64 `Risk` endpoints).

* `GET  /api/risk/limits?scope=&scope_id=`  - this organisation's limits (GLOBAL rows shown read-only)
* `PUT  /api/risk/limits`                   - upsert one limit (OWNER; GLOBAL scope needs SUPER_ADMIN)
* `DELETE /api/risk/limits/{id}`
* `GET  /api/risk/events?strategy_id=&limit=` - the append-only check log, newest first
* `POST /api/risk/evaluate`                 - dry run: what the hierarchy would say for an order
"""
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.auth.dependencies import get_current_user, require_role
from app.core.enums import RiskLimitType, RiskScope, UserRole
from app.db.models import RiskEventRecord, RiskLimitRecord, User
from app.db.session import get_session
from app.risk_engine.hierarchy import RiskContext, evaluate
from app.risk_engine.routes import get_tenant_risk_config
from app.core.models import RiskConfig

router = APIRouter(prefix="/api/risk", tags=["risk"])


class RiskLimitRequest(BaseModel):
    scope: RiskScope
    scope_id: str = Field(default="", max_length=100)
    limit_type: RiskLimitType
    limit_value: float = Field(gt=0)
    enabled: bool = True
    note: Optional[str] = Field(default=None, max_length=200)


class RiskLimitResponse(BaseModel):
    id: int
    tenant_id: Optional[int]
    scope: str
    scope_id: str
    limit_type: str
    limit_value: float
    enabled: bool
    note: Optional[str]
    created_by: Optional[int]
    updated_at: Optional[datetime]

    @classmethod
    def from_record(cls, r: RiskLimitRecord) -> "RiskLimitResponse":
        return cls(id=r.id, tenant_id=r.tenant_id, scope=r.scope, scope_id=r.scope_id or "", limit_type=r.limit_type,
                   limit_value=r.limit_value, enabled=r.enabled, note=r.note, created_by=r.created_by, updated_at=r.updated_at)


class RiskEventResponse(BaseModel):
    id: int
    created_at: Optional[datetime]
    strategy_id: Optional[str]
    symbol: Optional[str]
    account_id: Optional[int]
    rule_type: str
    scope: str
    current_value: float
    limit_value: float
    severity: str
    action: str
    status: str
    reason: str
    order_id: Optional[int]


def _needs_scope_id(scope: RiskScope) -> bool:
    return scope in (RiskScope.USER, RiskScope.ACCOUNT, RiskScope.STRATEGY, RiskScope.INSTRUMENT)


@router.get("/limits", response_model=List[RiskLimitResponse])
async def list_limits(
    scope: Optional[RiskScope] = None, scope_id: Optional[str] = None,
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> List[RiskLimitResponse]:
    query = select(RiskLimitRecord).where(
        (RiskLimitRecord.tenant_id == user.tenant_id) | (RiskLimitRecord.scope == RiskScope.GLOBAL.value)
    )
    if scope is not None:
        query = query.where(RiskLimitRecord.scope == scope.value)
    if scope_id is not None:
        query = query.where(RiskLimitRecord.scope_id == scope_id)
    rows = await session.scalars(query.order_by(RiskLimitRecord.scope, RiskLimitRecord.scope_id, RiskLimitRecord.limit_type))
    return [RiskLimitResponse.from_record(r) for r in rows]


@router.put("/limits", response_model=RiskLimitResponse)
async def upsert_limit(
    request: RiskLimitRequest, user: User = Depends(require_role(UserRole.OWNER)), session: AsyncSession = Depends(get_session),
) -> RiskLimitResponse:
    if request.scope == RiskScope.GLOBAL:
        if user.role != UserRole.SUPER_ADMIN.value:
            raise HTTPException(status_code=403, detail="GLOBAL limits are set by the platform operator (SUPER_ADMIN)")
        tenant_id = None
        scope_id = ""
    else:
        tenant_id = user.tenant_id
        scope_id = request.scope_id.strip()
        if _needs_scope_id(request.scope) and not scope_id:
            raise HTTPException(status_code=400, detail=f"{request.scope.value} limits need a scope_id (user id, account id, strategy id or symbol)")
        if request.scope == RiskScope.TENANT:
            scope_id = ""
        if request.scope == RiskScope.INSTRUMENT:
            scope_id = scope_id.upper()
    record = await session.scalar(select(RiskLimitRecord).where(
        RiskLimitRecord.tenant_id == tenant_id if tenant_id is not None else RiskLimitRecord.tenant_id.is_(None),
        RiskLimitRecord.scope == request.scope.value, RiskLimitRecord.scope_id == scope_id,
        RiskLimitRecord.limit_type == request.limit_type.value,
    ))
    if record is None:
        record = RiskLimitRecord(tenant_id=tenant_id, scope=request.scope.value, scope_id=scope_id, limit_type=request.limit_type.value,
                                 limit_value=request.limit_value, created_by=user.id)
        session.add(record)
    record.limit_value = request.limit_value
    record.enabled = request.enabled
    record.note = request.note
    await write_audit_log(session, user.tenant_id, user.id, "risk_limit_set",
                          f"{request.scope.value}{(':' + scope_id) if scope_id else ''} {request.limit_type.value}={request.limit_value:g} enabled={request.enabled}")
    await session.commit()
    await session.refresh(record)
    return RiskLimitResponse.from_record(record)


@router.delete("/limits/{limit_id}", status_code=204)
async def delete_limit(
    limit_id: int, user: User = Depends(require_role(UserRole.OWNER)), session: AsyncSession = Depends(get_session),
) -> None:
    record = await session.get(RiskLimitRecord, limit_id)
    if record is None:
        raise HTTPException(status_code=404, detail="No such limit")
    if record.scope == RiskScope.GLOBAL.value:
        if user.role != UserRole.SUPER_ADMIN.value:
            raise HTTPException(status_code=403, detail="GLOBAL limits are removed by the platform operator")
    elif record.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="No such limit")
    await session.delete(record)
    await write_audit_log(session, user.tenant_id, user.id, "risk_limit_deleted", f"{record.scope}:{record.scope_id} {record.limit_type}")
    await session.commit()


@router.get("/events", response_model=List[RiskEventResponse])
async def list_events(
    strategy_id: Optional[str] = None, status: Optional[str] = None, limit: int = Query(default=100, ge=1, le=1000),
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> List[RiskEventResponse]:
    query = select(RiskEventRecord).where(RiskEventRecord.tenant_id == user.tenant_id)
    if strategy_id:
        query = query.where(RiskEventRecord.strategy_id == strategy_id)
    if status:
        query = query.where(RiskEventRecord.status == status.upper())
    rows = await session.scalars(query.order_by(RiskEventRecord.id.desc()).limit(limit))
    return [RiskEventResponse(
        id=r.id, created_at=r.created_at, strategy_id=r.strategy_id, symbol=r.symbol, account_id=r.account_id, rule_type=r.rule_type,
        scope=r.scope, current_value=r.current_value, limit_value=r.limit_value, severity=r.severity, action=r.action, status=r.status,
        reason=r.reason, order_id=r.order_id,
    ) for r in rows]


class RiskEvaluateRequest(BaseModel):
    strategy_id: str
    symbol: str
    quantity: float = Field(gt=0)
    entry: Optional[float] = None
    stop_loss: Optional[float] = None
    account_id: Optional[int] = None
    mode: str = "PAPER"


@router.post("/evaluate")
async def evaluate_dry_run(
    request: RiskEvaluateRequest, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> dict:
    """What the hierarchy would decide for this order right now. Records the checks like a real
    evaluation (they are labelled by the absent order id) but never engages a kill switch."""
    cfg = await get_tenant_risk_config(user.tenant_id, session) or RiskConfig()
    ctx = RiskContext(tenant_id=user.tenant_id, user_id=user.id, strategy_id=request.strategy_id, symbol=request.symbol.upper(),
                      quantity=request.quantity, entry=request.entry, stop_loss=request.stop_loss, capital=cfg.capital,
                      account_id=request.account_id, mode=request.mode)
    verdict = await evaluate(session, ctx, user=None)
    return {"allowed": verdict.allowed, "reasons": verdict.reasons, "notes": verdict.notes,
            "checks": [{"limit_type": c.rule.limit_type, "scope": c.rule.scope, "current": c.current, "limit": c.limit, "status": c.status,
                        "action": c.action.value, "reason": c.reason} for c in verdict.checks]}
