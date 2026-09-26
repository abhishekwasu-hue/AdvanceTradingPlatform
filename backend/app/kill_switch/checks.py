from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import KillSwitchScope
from app.db.models import KillSwitchRecord, User


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def get_switch(
    session: AsyncSession, scope: KillSwitchScope, tenant_id: Optional[int], strategy_id: Optional[str] = None,
) -> Optional[KillSwitchRecord]:
    return await session.scalar(
        select(KillSwitchRecord).where(
            KillSwitchRecord.scope == scope.value,
            KillSwitchRecord.tenant_id == tenant_id,
            KillSwitchRecord.strategy_id == strategy_id,
        )
    )


async def engage(
    session: AsyncSession, scope: KillSwitchScope, tenant_id: Optional[int], user: User,
    reason: str, strategy_id: Optional[str] = None,
) -> KillSwitchRecord:
    """Idempotent: engaging an already-engaged switch just updates its reason/timestamp rather
    than erroring, so a retried or overlapping "stop everything" call is always safe."""
    record = await get_switch(session, scope, tenant_id, strategy_id)
    if record is None:
        record = KillSwitchRecord(tenant_id=tenant_id, scope=scope.value, strategy_id=strategy_id)
        session.add(record)
    record.engaged = True
    record.reason = reason
    record.engaged_by = user.id
    record.engaged_at = _utcnow()
    record.disengaged_at = None
    await session.commit()
    await session.refresh(record)
    return record


async def disengage(
    session: AsyncSession, scope: KillSwitchScope, tenant_id: Optional[int], strategy_id: Optional[str] = None,
) -> Optional[KillSwitchRecord]:
    record = await get_switch(session, scope, tenant_id, strategy_id)
    if record is None or not record.engaged:
        return record
    record.engaged = False
    record.disengaged_at = _utcnow()
    await session.commit()
    await session.refresh(record)
    return record


async def is_global_kill_switch_engaged(session: AsyncSession) -> Optional[str]:
    """Checked on the anonymous (no-login) paper-execute path, which has no tenant to check a
    TENANT/STRATEGY switch against - the platform-wide GLOBAL switch is the only one that
    applies to a demo call."""
    record = await get_switch(session, KillSwitchScope.GLOBAL, None)
    return record.reason if record is not None and record.engaged else None


async def active_kill_switch_reasons(session: AsyncSession, tenant_id: int, strategy_id: str) -> List[str]:
    """Checked by paper-execute (and, once wired, live execution) before a new order is allowed
    to enter: GLOBAL (platform-wide), then this tenant's TENANT switch, then this strategy's
    STRATEGY switch within the tenant. Any one of the three engaged blocks the order.
    """
    reasons: List[str] = []

    global_switch = await get_switch(session, KillSwitchScope.GLOBAL, None)
    if global_switch is not None and global_switch.engaged:
        reasons.append(f"Global kill switch engaged: {global_switch.reason}".rstrip(": "))

    tenant_switch = await get_switch(session, KillSwitchScope.TENANT, tenant_id)
    if tenant_switch is not None and tenant_switch.engaged:
        reasons.append(f"Account kill switch engaged: {tenant_switch.reason}".rstrip(": "))

    strategy_switch = await get_switch(session, KillSwitchScope.STRATEGY, tenant_id, strategy_id)
    if strategy_switch is not None and strategy_switch.engaged:
        reasons.append(f"Strategy kill switch engaged for {strategy_id}: {strategy_switch.reason}".rstrip(": "))

    return reasons
