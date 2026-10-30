"""M55 S7: is the trading worker still checking the bots? Read on the API side, never inside the worker - a worker that
has stopped cannot report that it has stopped.

The worker writes its heartbeat row (worker_heartbeats: time + cycle number) at the end of every cycle
(trading_worker._heartbeat). Here the API reads it:

* `heartbeat_stale` - older than WORKER_SILENT_CYCLES_ALERT cycles (default 3) of WORKER_CYCLE_SECONDS;
* `alert_if_silent` - while it is stale, every organisation with a running (ACTIVE) bot gets ONE CRITICAL
  notification per silence: a silence is named by the heartbeat's last cycle number, so the same silence is not told
  again, and a new silence is told no sooner than WORKER_SILENCE_ALERT_COOLDOWN_SECONDS after the last one. The last
  alert is kept in runtime_counters (name `worker_silence_alert`, key the organisation id), so several API processes
  and restarts do not repeat it.

Where it runs: GET /api/system/worker-status (the bot pages and Today read it, for the caller's organisation) and the
server's scheduled check (`deploy/atp.sh ops-check` -> backend/scripts/ops_check.py, every organisation).
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.core.enums import DeploymentStatus, NotificationSeverity, NotificationType
from app.db.models import RuntimeCounterRecord, StrategyDeploymentRecord, WorkerHeartbeatRecord

logger = logging.getLogger(__name__)

WORKER_NAME = "trading_worker"
ALERT_COUNTER = "worker_silence_alert"
SILENT_TITLE = "Bots are not being checked right now"
SILENT_MESSAGE = ("The engine that checks your bots has not run for a few minutes. No new entries are taken while it is "
                  "stopped. Your open positions and settings are kept; to act now, use Exit on a position.")


def _utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def stale_after_seconds() -> int:
    return config.WORKER_CYCLE_SECONDS * config.WORKER_SILENT_CYCLES_ALERT


def heartbeat_stale(record: Optional[WorkerHeartbeatRecord], now: datetime) -> bool:
    """True when the worker has been silent beyond N cycles. No row at all: it never ran - not "stale" (a new install)."""
    if record is None:
        return False
    seen = _utc(record.last_seen_at)
    return seen is None or (now - seen).total_seconds() > stale_after_seconds()


async def alert_if_silent(session: AsyncSession, now: Optional[datetime] = None, tenant_id: Optional[int] = None) -> int:
    """While the heartbeat is stale: one CRITICAL per organisation with a running bot (only `tenant_id` when given), per
    silence and cool-down. Returns how many organisations were told. Never raises."""
    now = now or datetime.now(UTC)
    try:
        record = await session.scalar(select(WorkerHeartbeatRecord).where(WorkerHeartbeatRecord.worker_name == WORKER_NAME))
        if not heartbeat_stale(record, now):
            return 0
        query = select(StrategyDeploymentRecord.tenant_id).where(
            StrategyDeploymentRecord.status == DeploymentStatus.ACTIVE.value).distinct()
        if tenant_id is not None:
            query = query.where(StrategyDeploymentRecord.tenant_id == tenant_id)
        tenants = sorted(set(await session.scalars(query)))
        told = 0
        for tid in tenants:
            if await _tell(session, tid, int(record.cycle_count or 0), now):
                told += 1
        return told
    except Exception:  # noqa: BLE001 - a status read must never fail because of its alert
        logger.warning("Worker silence alert failed", exc_info=True)
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return 0


async def _tell(session: AsyncSession, tenant_id: int, cycle: int, now: datetime) -> bool:
    from app.notifications.service import notify
    row = await session.scalar(select(RuntimeCounterRecord).where(RuntimeCounterRecord.name == ALERT_COUNTER,
                                                                  RuntimeCounterRecord.key == str(tenant_id)))
    if row is not None:
        try:
            last = json.loads(row.value) or {}
        except ValueError:
            last = {}
        same_silence = last.get("cycle") == cycle
        at = last.get("at")
        recent = isinstance(at, (int, float)) and now.timestamp() - at < config.WORKER_SILENCE_ALERT_COOLDOWN_SECONDS
        if same_silence or recent:
            return False
    value = json.dumps({"cycle": cycle, "at": now.timestamp()})
    # Claim the alert first (unique name + key), so two API processes never both send it.
    try:
        if row is None:
            session.add(RuntimeCounterRecord(name=ALERT_COUNTER, key=str(tenant_id), value=value))
        else:
            claimed = await session.execute(update(RuntimeCounterRecord).where(
                RuntimeCounterRecord.id == row.id, RuntimeCounterRecord.value == row.value).values(value=value)
                .execution_options(synchronize_session=False))
            if claimed.rowcount != 1:
                await session.rollback()
                return False
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return False
    await notify(session, tenant_id, NotificationType.SYSTEM_FAILURE, title=SILENT_TITLE, message=SILENT_MESSAGE,
                 severity=NotificationSeverity.CRITICAL)
    logger.error("Trading worker silent beyond %d cycles (last cycle %d) - organisation %s told", config.WORKER_SILENT_CYCLES_ALERT,
                 cycle, tenant_id)
    return True
