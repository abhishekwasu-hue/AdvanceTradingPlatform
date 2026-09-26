"""SUPER_ADMIN-only console. Every endpoint here spans tenants, which is exactly why nothing
tenant-facing can reach it: `require_role()` with no roles is "SUPER_ADMIN only"."""
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.auth.dependencies import require_mfa_session, require_role
from app.core.config import WORKER_CYCLE_SECONDS
from app.core.enums import DeploymentStatus, KillSwitchScope, NotificationSeverity, NotificationType
from app.db.models import (
    AuditLogRecord, BrokerCredentialRecord, KillSwitchRecord, LoginEventRecord, StrategyDeploymentRecord, Tenant,
    TradeRecord, User, WorkerHeartbeatRecord,
)
from app.db.session import get_session
from app.notifications.service import notify
from app.plans.limits import TENANT_ACTIVE, TENANT_SUSPENDED, limits as plan_limits, usage as plan_usage
from app.plans.registry import PLANS, get_plan

# SUPER_ADMIN only, and the session must have passed a TOTP check (platform admins must use MFA).
router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_role()), Depends(require_mfa_session)])

TENANT_STATUSES = (TENANT_ACTIVE, TENANT_SUSPENDED)


class PlanResponse(BaseModel):
    id: str
    name: str
    description: str
    limits: Dict[str, object]


class TenantSummary(BaseModel):
    id: int
    name: str
    plan: str
    status: str
    created_at: str
    owners: List[str]
    members: int
    active_deployments: int
    live_deployments: int
    open_positions: int


class TenantDetail(TenantSummary):
    limits: Dict[str, object]
    usage: Dict[str, int]
    users: List[Dict[str, object]]
    deployments: List[Dict[str, object]]
    brokers: List[Dict[str, object]]
    tenant_kill_switch_engaged: bool


class TenantUpdateRequest(BaseModel):
    plan: Optional[str] = None
    status: Optional[str] = None
    reason: str = ""


class OverviewResponse(BaseModel):
    tenants_total: int
    tenants_by_status: Dict[str, int]
    tenants_by_plan: Dict[str, int]
    users_total: int
    active_deployments: int
    live_deployments: int
    open_positions: int
    open_live_positions: int
    global_kill_switch_engaged: bool
    global_kill_switch_reason: str
    worker_running: bool
    worker_last_seen_at: Optional[str]
    worker_last_error: Optional[str]


class PlatformAuditLog(BaseModel):
    id: int
    tenant_id: Optional[int]
    user_id: Optional[int]
    user_email: Optional[str]
    event: str
    detail: str
    created_at: str


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


async def _counts_by_tenant(session: AsyncSession):
    active = dict((await session.execute(
        select(StrategyDeploymentRecord.tenant_id, func.count())
        .where(StrategyDeploymentRecord.status != DeploymentStatus.STOPPED.value)
        .group_by(StrategyDeploymentRecord.tenant_id)
    )).all())
    live = dict((await session.execute(
        select(StrategyDeploymentRecord.tenant_id, func.count())
        .where(StrategyDeploymentRecord.status != DeploymentStatus.STOPPED.value, StrategyDeploymentRecord.mode == "LIVE")
        .group_by(StrategyDeploymentRecord.tenant_id)
    )).all())
    open_positions = dict((await session.execute(
        select(TradeRecord.tenant_id, func.count()).where(TradeRecord.exit_time.is_(None)).group_by(TradeRecord.tenant_id)
    )).all())
    members = dict((await session.execute(
        select(User.tenant_id, func.count()).where(User.is_active.is_(True)).group_by(User.tenant_id)
    )).all())
    owners: Dict[int, List[str]] = {}
    for tenant_id, email in (await session.execute(
        select(User.tenant_id, User.email).where(User.role == "OWNER", User.is_active.is_(True)).order_by(User.id)
    )).all():
        owners.setdefault(tenant_id, []).append(email)
    return active, live, open_positions, members, owners


def _summary(tenant: Tenant, active, live, open_positions, members, owners) -> TenantSummary:
    return TenantSummary(
        id=tenant.id, name=tenant.name, plan=get_plan(tenant.plan).id, status=tenant.status, created_at=tenant.created_at.isoformat(),
        owners=owners.get(tenant.id, []), members=members.get(tenant.id, 0),
        active_deployments=active.get(tenant.id, 0), live_deployments=live.get(tenant.id, 0),
        open_positions=open_positions.get(tenant.id, 0),
    )


@router.get("/plans", response_model=List[PlanResponse])
async def list_plans() -> List[PlanResponse]:
    return [PlanResponse(id=p.id, name=p.name, description=p.description, limits=plan_limits(p)) for p in PLANS.values()]


@router.get("/overview", response_model=OverviewResponse)
async def overview(session: AsyncSession = Depends(get_session)) -> OverviewResponse:
    tenants = list(await session.scalars(select(Tenant)))
    by_status: Dict[str, int] = {}
    by_plan: Dict[str, int] = {}
    for t in tenants:
        by_status[t.status] = by_status.get(t.status, 0) + 1
        by_plan[get_plan(t.plan).id] = by_plan.get(get_plan(t.plan).id, 0) + 1
    users_total = await session.scalar(select(func.count()).select_from(User).where(User.is_active.is_(True))) or 0
    active = await session.scalar(select(func.count()).select_from(StrategyDeploymentRecord).where(StrategyDeploymentRecord.status != "STOPPED")) or 0
    live = await session.scalar(select(func.count()).select_from(StrategyDeploymentRecord).where(StrategyDeploymentRecord.status != "STOPPED", StrategyDeploymentRecord.mode == "LIVE")) or 0
    open_positions = await session.scalar(select(func.count()).select_from(TradeRecord).where(TradeRecord.exit_time.is_(None))) or 0
    open_live = await session.scalar(select(func.count()).select_from(TradeRecord).where(TradeRecord.exit_time.is_(None), TradeRecord.mode == "LIVE")) or 0
    global_switch = await session.scalar(select(KillSwitchRecord).where(KillSwitchRecord.scope == KillSwitchScope.GLOBAL.value, KillSwitchRecord.tenant_id.is_(None)))
    heartbeat = await session.scalar(select(WorkerHeartbeatRecord).where(WorkerHeartbeatRecord.worker_name == "trading_worker"))
    running = False
    if heartbeat is not None:
        last_seen = heartbeat.last_seen_at if heartbeat.last_seen_at.tzinfo else heartbeat.last_seen_at.replace(tzinfo=timezone.utc)
        running = (datetime.now(timezone.utc) - last_seen).total_seconds() <= WORKER_CYCLE_SECONDS * 3
    return OverviewResponse(
        tenants_total=len(tenants), tenants_by_status=by_status, tenants_by_plan=by_plan, users_total=users_total,
        active_deployments=active, live_deployments=live, open_positions=open_positions, open_live_positions=open_live,
        global_kill_switch_engaged=bool(global_switch and global_switch.engaged),
        global_kill_switch_reason=global_switch.reason if global_switch and global_switch.engaged else "",
        worker_running=running, worker_last_seen_at=_iso(heartbeat.last_seen_at) if heartbeat else None,
        worker_last_error=heartbeat.last_error if heartbeat else None,
    )


@router.get("/tenants", response_model=List[TenantSummary])
async def list_tenants(q: Optional[str] = None, limit: int = 200, session: AsyncSession = Depends(get_session)) -> List[TenantSummary]:
    query = select(Tenant).order_by(Tenant.id.desc()).limit(limit)
    if q:
        pattern = f"%{q.lower()}%"
        matching_tenant_ids = select(User.tenant_id).where(func.lower(User.email).like(pattern))
        query = select(Tenant).where((func.lower(Tenant.name).like(pattern)) | (Tenant.id.in_(matching_tenant_ids))).order_by(Tenant.id.desc()).limit(limit)
    tenants = list(await session.scalars(query))
    counts = await _counts_by_tenant(session)
    return [_summary(t, *counts) for t in tenants]


async def _tenant_or_404(session: AsyncSession, tenant_id: int) -> Tenant:
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Unknown tenant")
    return tenant


@router.get("/tenants/{tenant_id}", response_model=TenantDetail)
async def tenant_detail(tenant_id: int, session: AsyncSession = Depends(get_session)) -> TenantDetail:
    tenant = await _tenant_or_404(session, tenant_id)
    counts = await _counts_by_tenant(session)
    summary = _summary(tenant, *counts)
    users = list(await session.scalars(select(User).where(User.tenant_id == tenant_id).order_by(User.id)))
    deployments = list(await session.scalars(
        select(StrategyDeploymentRecord).where(StrategyDeploymentRecord.tenant_id == tenant_id).order_by(StrategyDeploymentRecord.id.desc()).limit(50)
    ))
    brokers = list(await session.scalars(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == tenant_id)))
    switch = await session.scalar(select(KillSwitchRecord).where(KillSwitchRecord.scope == KillSwitchScope.TENANT.value, KillSwitchRecord.tenant_id == tenant_id))
    return TenantDetail(
        **summary.model_dump(), limits=plan_limits(get_plan(tenant.plan)), usage=await plan_usage(session, tenant_id),
        users=[{"id": u.id, "email": u.email, "role": u.role, "is_active": u.is_active, "created_at": u.created_at.isoformat()} for u in users],
        deployments=[{
            "id": d.id, "strategy_id": d.strategy_id, "symbol": d.symbol, "mode": d.mode, "status": d.status,
            "broker_name": d.broker_name, "last_evaluated_at": _iso(d.last_evaluated_at), "last_error": d.last_error,
        } for d in deployments],
        brokers=[{"broker_name": b.broker_name, "token_status": b.token_status, "token_expires_at": _iso(b.token_expires_at)} for b in brokers],
        tenant_kill_switch_engaged=bool(switch and switch.engaged),
    )


@router.patch("/tenants/{tenant_id}", response_model=TenantSummary)
async def update_tenant(
    tenant_id: int, request: TenantUpdateRequest,
    admin: User = Depends(require_role()), session: AsyncSession = Depends(get_session),
) -> TenantSummary:
    """Change a tenant's plan and/or status. Audited on the tenant's own trail (so its owner can
    see who changed what) and announced to the tenant as a notification. Suspending takes effect
    on the next request / worker cycle: writes refused, no new entries, exits still monitored."""
    tenant = await _tenant_or_404(session, tenant_id)
    changes: List[str] = []
    if request.plan is not None:
        plan_id = request.plan.lower()
        if plan_id not in PLANS:
            raise HTTPException(status_code=400, detail=f"Unknown plan '{request.plan}'. Plans: {list(PLANS)}")
        if plan_id != tenant.plan:
            changes.append(f"plan {tenant.plan} -> {plan_id}")
            tenant.plan = plan_id
    if request.status is not None:
        new_status = request.status.lower()
        if new_status not in TENANT_STATUSES:
            raise HTTPException(status_code=400, detail=f"Status must be one of {list(TENANT_STATUSES)}")
        if new_status != tenant.status:
            changes.append(f"status {tenant.status} -> {new_status}")
            tenant.status = new_status
    if not changes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nothing to change")
    detail = "; ".join(changes) + (f" (reason: {request.reason})" if request.reason else "") + f" by {admin.email}"
    await write_audit_log(session, tenant.id, admin.id, "tenant_updated_by_admin", detail)
    await session.commit()
    severity = NotificationSeverity.CRITICAL if tenant.status == TENANT_SUSPENDED else NotificationSeverity.INFO
    await notify(
        session, tenant.id, NotificationType.SYSTEM_FAILURE if severity == NotificationSeverity.CRITICAL else NotificationType.ENTRY,
        title="Account updated by platform administrator" if severity == NotificationSeverity.INFO else "Account suspended",
        message="; ".join(changes) + (f". Reason: {request.reason}" if request.reason else ""), severity=severity,
    )
    counts = await _counts_by_tenant(session)
    return _summary(tenant, *counts)


@router.get("/audit-logs", response_model=List[PlatformAuditLog])
async def platform_audit_logs(
    tenant_id: Optional[int] = None, event: Optional[str] = None, limit: int = 200,
    session: AsyncSession = Depends(get_session),
) -> List[PlatformAuditLog]:
    """The platform-wide audit trail (every tenant), newest first."""
    query = select(AuditLogRecord, User.email).join(User, User.id == AuditLogRecord.user_id, isouter=True)
    if tenant_id is not None:
        query = query.where(AuditLogRecord.tenant_id == tenant_id)
    if event:
        query = query.where(AuditLogRecord.event.like(f"%{event}%"))
    rows = await session.execute(query.order_by(AuditLogRecord.id.desc()).limit(limit))
    return [
        PlatformAuditLog(id=log.id, tenant_id=log.tenant_id, user_id=log.user_id, user_email=email, event=log.event,
                         detail=log.detail, created_at=log.created_at.isoformat())
        for log, email in rows
    ]


@router.get("/deployments")
async def platform_deployments(status_filter: Optional[str] = None, limit: int = 200, session: AsyncSession = Depends(get_session)) -> List[Dict[str, object]]:
    """Every deployment on the platform - the ops view of "what is the worker doing right now"."""
    query = select(StrategyDeploymentRecord, Tenant.name).join(Tenant, Tenant.id == StrategyDeploymentRecord.tenant_id)
    if status_filter:
        query = query.where(StrategyDeploymentRecord.status == status_filter.upper())
    rows = await session.execute(query.order_by(StrategyDeploymentRecord.id.desc()).limit(limit))
    return [{
        "id": d.id, "tenant_id": d.tenant_id, "tenant_name": name, "strategy_id": d.strategy_id, "symbol": d.symbol,
        "mode": d.mode, "status": d.status, "broker_name": d.broker_name, "last_evaluated_at": _iso(d.last_evaluated_at),
        "last_signal_at": _iso(d.last_signal_at), "last_error": d.last_error, "consecutive_failures": d.consecutive_failures,
    } for d, name in rows]


@router.get("/login-events")
async def platform_login_events(
    email: Optional[str] = None, failures_only: bool = False, limit: int = 200, session: AsyncSession = Depends(get_session),
) -> List[Dict[str, object]]:
    """Platform-wide login attempts - the view for spotting a credential-stuffing run."""
    query = select(LoginEventRecord)
    if email:
        query = query.where(LoginEventRecord.email.like(f"%{email.lower()}%"))
    if failures_only:
        query = query.where(LoginEventRecord.success.is_(False))
    rows = await session.scalars(query.order_by(LoginEventRecord.id.desc()).limit(limit))
    return [{
        "id": r.id, "email": r.email, "tenant_id": r.tenant_id, "success": r.success, "reason": r.reason,
        "ip_address": r.ip_address, "user_agent": r.user_agent, "created_at": r.created_at.isoformat(),
    } for r in rows]
