"""Where plan limits and tenant status are enforced. Every check raises a 402 (plan) or 403
(suspended) with a message that says which limit, what the plan allows, and what to do - the
Team tab shows the same numbers as usage-vs-limit so nobody hits these blind."""
from typing import Dict, Optional

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import DeploymentStatus
from app.db.models import (
    AlertChannelRecord, CustomStrategyRecord, StrategyDeploymentRecord, Tenant, TenantInviteRecord, User,
)
from app.plans.registry import Plan, get_plan

TENANT_ACTIVE = "active"
TENANT_SUSPENDED = "suspended"


class PlanLimitExceeded(HTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=detail)


class TenantSuspended(HTTPException):
    def __init__(self, tenant: Tenant) -> None:
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Organisation '{tenant.name}' is {tenant.status}: trading and configuration are disabled. "
                   "Contact support.",
        )


async def load_tenant(session: AsyncSession, tenant_id: int) -> Tenant:
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Unknown organisation")
    return tenant


def tenant_is_active(tenant: Tenant) -> bool:
    return (tenant.status or TENANT_ACTIVE).lower() == TENANT_ACTIVE


def ensure_tenant_active(tenant: Tenant) -> None:
    if not tenant_is_active(tenant):
        raise TenantSuspended(tenant)


async def count_active_deployments(session: AsyncSession, tenant_id: int) -> int:
    return await session.scalar(
        select(func.count()).select_from(StrategyDeploymentRecord).where(
            StrategyDeploymentRecord.tenant_id == tenant_id,
            StrategyDeploymentRecord.status != DeploymentStatus.STOPPED.value,
        )
    ) or 0


async def count_custom_strategies(session: AsyncSession, tenant_id: int) -> int:
    return await session.scalar(
        select(func.count()).select_from(CustomStrategyRecord).where(CustomStrategyRecord.tenant_id == tenant_id)
    ) or 0


async def count_members(session: AsyncSession, tenant_id: int) -> int:
    from datetime import datetime, timezone
    users = await session.scalar(
        select(func.count()).select_from(User).where(User.tenant_id == tenant_id, User.is_active.is_(True))
    ) or 0
    invites = await session.scalar(
        select(func.count()).select_from(TenantInviteRecord).where(
            TenantInviteRecord.tenant_id == tenant_id, TenantInviteRecord.accepted_at.is_(None),
            TenantInviteRecord.expires_at > datetime.now(timezone.utc),
        )
    ) or 0
    return users + invites


async def count_alert_channels(session: AsyncSession, tenant_id: int) -> int:
    return await session.scalar(
        select(func.count()).select_from(AlertChannelRecord).where(AlertChannelRecord.tenant_id == tenant_id)
    ) or 0


async def usage(session: AsyncSession, tenant_id: int) -> Dict[str, int]:
    return {
        "active_deployments": await count_active_deployments(session, tenant_id),
        "custom_strategies": await count_custom_strategies(session, tenant_id),
        "members": await count_members(session, tenant_id),
        "alert_channels": await count_alert_channels(session, tenant_id),
    }


def limits(plan: Plan) -> Dict[str, object]:
    return {
        "active_deployments": plan.max_active_deployments,
        "live_trading": plan.live_trading,
        "custom_strategies": plan.max_custom_strategies,
        "members": plan.max_members,
        "alert_channels": plan.max_alert_channels,
    }


def _upgrade_hint(plan: Plan) -> str:
    return "" if plan.id == "business" else " Upgrade the plan to add more."


async def check_can_add_deployment(session: AsyncSession, tenant: Tenant, *, live: bool, adding: bool = True) -> None:
    ensure_tenant_active(tenant)
    plan = get_plan(tenant.plan)
    if live and not plan.live_trading:
        raise PlanLimitExceeded(f"The {plan.name} plan is paper trading only - LIVE deployments need Pro or Business.")
    if adding:
        current = await count_active_deployments(session, tenant.id)
        if current >= plan.max_active_deployments:
            raise PlanLimitExceeded(
                f"The {plan.name} plan allows {plan.max_active_deployments} active deployment(s); you have {current}. "
                f"Stop one first.{_upgrade_hint(plan)}"
            )


async def check_can_add_custom_strategy(session: AsyncSession, tenant: Tenant) -> None:
    ensure_tenant_active(tenant)
    plan = get_plan(tenant.plan)
    current = await count_custom_strategies(session, tenant.id)
    if current >= plan.max_custom_strategies:
        raise PlanLimitExceeded(
            f"The {plan.name} plan allows {plan.max_custom_strategies} custom strategies; you have {current}.{_upgrade_hint(plan)}"
        )


async def check_can_add_member(session: AsyncSession, tenant: Tenant) -> None:
    ensure_tenant_active(tenant)
    plan = get_plan(tenant.plan)
    current = await count_members(session, tenant.id)
    if current >= plan.max_members:
        raise PlanLimitExceeded(
            f"The {plan.name} plan allows {plan.max_members} team member(s) including open invites; you have {current}.{_upgrade_hint(plan)}"
        )


async def check_can_add_alert_channel(session: AsyncSession, tenant: Tenant) -> None:
    ensure_tenant_active(tenant)
    plan = get_plan(tenant.plan)
    current = await count_alert_channels(session, tenant.id)
    if current >= plan.max_alert_channels:
        raise PlanLimitExceeded(
            f"The {plan.name} plan allows {plan.max_alert_channels} alert channel(s); you have {current}.{_upgrade_hint(plan)}"
        )


def live_allowed(tenant: Optional[Tenant]) -> bool:
    return tenant is not None and tenant_is_active(tenant) and get_plan(tenant.plan).live_trading
