"""Z10-b: the operator's view of the alert outbox across organisations - its state, the dead letters, and their replay.

SUPER_ADMIN with a two-factor session only (like every `/api/admin` router). The listing shows delivery metadata (who,
which channel type, which event type, attempts, the error) and never a notification's title or message. A replay puts
rows back in the outbox with fresh attempts and is written to each organisation's audit trail; the row keeps its
idempotency key, so a receiver that saw the first send can drop the repeat, and a screen alert whose data has gone
stale since is suppressed at send time (flag `alerts_stale_guard`). Nothing here sends anything itself, and nothing
here places, changes or blocks an order.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.auth.dependencies import require_mfa_session, require_role
from app.db.models import AlertChannelRecord, AlertDeliveryRecord, NotificationRecord, User
from app.db.session import get_session

router = APIRouter(prefix="/api/admin/alerts", tags=["admin"], dependencies=[Depends(require_role()), Depends(require_mfa_session)])
REPLAYABLE = ("DEAD", "FAILED")


class ReplayBody(BaseModel):
    ids: List[int] = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=3, max_length=200)


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


@router.get("/outbox")
async def outbox_state(session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Rows per status, rows claimed by a worker right now, and the oldest row still waiting (the outbox lag)."""
    now = datetime.now(timezone.utc)
    counts = dict((await session.execute(select(AlertDeliveryRecord.status, func.count()).group_by(AlertDeliveryRecord.status))).all())
    claimed = await session.scalar(select(func.count()).select_from(AlertDeliveryRecord).where(
        AlertDeliveryRecord.status == "PENDING", AlertDeliveryRecord.lease_until > now))
    oldest = await session.scalar(select(func.min(AlertDeliveryRecord.created_at)).where(AlertDeliveryRecord.status == "PENDING"))
    lag = None
    if oldest is not None:
        lag = max(0.0, (now - (oldest if oldest.tzinfo else oldest.replace(tzinfo=timezone.utc))).total_seconds())
    return {"by_status": {str(k): int(v) for k, v in counts.items()}, "claimed": int(claimed or 0), "oldest_pending_seconds": lag}


@router.get("/dead-letters")
async def dead_letters(tenant_id: Optional[int] = None, limit: int = Query(default=100, ge=1, le=500),
                       session: AsyncSession = Depends(get_session)) -> List[Dict[str, Any]]:
    """DEAD and FAILED deliveries, newest first, optionally for one organisation."""
    query = (select(AlertDeliveryRecord, AlertChannelRecord.channel_type, NotificationRecord.event_type, NotificationRecord.severity)
             .join(AlertChannelRecord, AlertChannelRecord.id == AlertDeliveryRecord.channel_id)
             .join(NotificationRecord, NotificationRecord.id == AlertDeliveryRecord.notification_id)
             .where(AlertDeliveryRecord.status.in_(REPLAYABLE)))
    if tenant_id is not None:
        query = query.where(AlertDeliveryRecord.tenant_id == tenant_id)
    rows = await session.execute(query.order_by(AlertDeliveryRecord.id.desc()).limit(limit))
    return [{"id": d.id, "tenant_id": d.tenant_id, "notification_id": d.notification_id, "channel": ctype, "event_type": etype,
             "severity": sev, "status": d.status, "reason": d.reason_code, "attempts": d.attempts, "last_error": d.last_error,
             "idem_key": d.idem_key, "created_at": _iso(d.created_at)}
            for d, ctype, etype, sev in rows.all()]


@router.post("/dead-letters/replay")
async def replay(body: ReplayBody, user: User = Depends(require_role()), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Puts DEAD / FAILED rows back in the outbox (fresh attempts, lease cleared); the worker sends them on its next
    cycle. Rows in any other state are skipped with the reason. Audited per organisation."""
    ids = list(dict.fromkeys(body.ids))
    rows = {r.id: r for r in await session.scalars(select(AlertDeliveryRecord).where(AlertDeliveryRecord.id.in_(ids)))}
    now = datetime.now(timezone.utc)
    replayed: List[int] = []
    skipped: Dict[str, str] = {}
    by_tenant: Dict[int, List[int]] = {}
    for delivery_id in ids:
        row = rows.get(delivery_id)
        if row is None:
            skipped[str(delivery_id)] = "not found"
            continue
        if row.status not in REPLAYABLE:
            skipped[str(delivery_id)] = f"status {row.status}: only DEAD or FAILED rows are replayed"
            continue
        row.status, row.attempts, row.next_attempt_at, row.lease_until, row.claimed_by = "PENDING", 0, now, None, None
        row.reason_code = "replayed"
        replayed.append(row.id)
        by_tenant.setdefault(row.tenant_id, []).append(row.id)
    for tenant_id, delivery_ids in by_tenant.items():
        await write_audit_log(session, tenant_id, user.id, "alert_dead_letters_replayed",
                              f"{len(delivery_ids)} alert deliveries put back in the outbox ({', '.join(map(str, delivery_ids[:50]))}): {body.reason}")
    await session.commit()
    return {"replayed": replayed, "skipped": skipped}


__all__ = ["router"]
