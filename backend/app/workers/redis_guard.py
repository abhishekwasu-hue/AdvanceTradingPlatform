"""H-1 (OPEN_QUESTIONS, answered 2026-10-10): Redis at its memory cap.

Redis runs capped with `volatile-lru` (docker-compose.hostinger.yml): at the cap it evicts keys that carry a TTL.
The worker's replica lock carries one (ADR-0010: twice the cycle), so eviction can take it mid-cycle and a second
replica could then take it. What lives where:
- the replica lock: Redis, with a TTL - its loss is detected at every renewal and the worker fails closed
  (`TradingWorker._keep_lock`): no new entries for the rest of the cycle, and if another holder has it, the cycle
  stops there (that holder runs the exits); one CRITICAL to the operators, metric `atp_worker_lock_lost_total`;
- the alert outbox and order idempotency keys: Postgres rows (AlertDeliveryRecord, OrderRecord.idempotency_key) -
  eviction cannot touch them;
- caches, quote ticks, rate windows: Redis with short TTLs (cache_set caps them at CACHE_MAX_TTL_SECONDS).
The worker reads `used_memory / maxmemory` every REDIS_MEMORY_CHECK_SECONDS and warns the operators at
REDIS_MEMORY_WARN_RATIO (70 %), before eviction starts. One worker replica runs (the Hostinger overlay pins it).
"""
import logging
import time
from typing import Callable, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.client import cache_memory_ratio
from app.core import config as app_config
from app.core.enums import DeploymentStatus, NotificationSeverity, NotificationType, UserRole
from app.db.models import StrategyDeploymentRecord, User
from app.notifications.service import notify
from app.observability.metrics import REDIS_MEMORY_RATIO, WORKER_LOCK_LOST

logger = logging.getLogger(__name__)

LOCK_LOST_ALERT_COOLDOWN_SECONDS = 1800
MEMORY_ALERT_COOLDOWN_SECONDS = 3600


async def operator_tenants(session: AsyncSession) -> List[int]:
    """Organisations told about platform faults: those of the platform operators (SUPER_ADMIN users); with no
    operator account yet, every organisation with an ACTIVE deployment (the people whose trading it affects)."""
    ids = set(await session.scalars(select(User.tenant_id).where(User.role == UserRole.SUPER_ADMIN.value)))
    if not ids:
        ids = set(await session.scalars(select(StrategyDeploymentRecord.tenant_id).where(
            StrategyDeploymentRecord.status == DeploymentStatus.ACTIVE.value).distinct()))
    return sorted(i for i in ids if i is not None)


async def alert_operators(session: AsyncSession, title: str, message: str, severity: NotificationSeverity) -> int:
    sent = 0
    for tenant_id in await operator_tenants(session):
        await notify(session, tenant_id, NotificationType.SYSTEM_FAILURE, title, message, severity=severity)
        sent += 1
    return sent


class RedisGuard:
    """Per-process alert state (cooldowns) for the two H-1 alerts."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._last_lock_alert: Optional[float] = None
        self._last_memory_check: Optional[float] = None
        self._last_memory_alert: Optional[float] = None

    def _due(self, last: Optional[float], every: float) -> bool:
        return last is None or self._clock() - last >= every

    async def lock_lost(self, session: AsyncSession, *, retaken: bool, holder: Optional[str], where: str) -> bool:
        """Records a lost lock; True when the CRITICAL went out (once per LOCK_LOST_ALERT_COOLDOWN_SECONDS)."""
        WORKER_LOCK_LOST.labels(outcome="retaken" if retaken else "held_elsewhere").inc()
        if retaken:
            what = ("The trading worker's replica lock disappeared from Redis mid-cycle (most likely evicted at the "
                    "memory cap) and was taken back. No new entries were placed for the rest of that cycle; exits continued.")
        else:
            what = (f"The trading worker's replica lock is now held by another worker ({holder}). This worker stopped "
                    "its cycle (no entries, no exits - the holder runs them). Two workers must never run: check the replicas.")
        logger.critical("Replica lock lost %s (%s)", where, "retaken" if retaken else f"held by {holder}")
        if not self._due(self._last_lock_alert, LOCK_LOST_ALERT_COOLDOWN_SECONDS):
            return False
        self._last_lock_alert = self._clock()
        try:
            await alert_operators(session, "Worker lock lost - failed closed", f"{what} Where: {where}.", NotificationSeverity.CRITICAL)
        except Exception:  # noqa: BLE001 - the alert never breaks the cycle; the log line and metric remain
            logger.exception("Lock-lost alert failed")
            return False
        return True

    async def check_memory(self, session: AsyncSession) -> Optional[float]:
        """Every REDIS_MEMORY_CHECK_SECONDS: the used share of maxmemory, a WARNING at REDIS_MEMORY_WARN_RATIO."""
        if not self._due(self._last_memory_check, app_config.REDIS_MEMORY_CHECK_SECONDS):
            return None
        self._last_memory_check = self._clock()
        ratio = await cache_memory_ratio()
        if ratio is None:
            return None
        REDIS_MEMORY_RATIO.set(ratio)
        if ratio >= app_config.REDIS_MEMORY_WARN_RATIO and self._due(self._last_memory_alert, MEMORY_ALERT_COOLDOWN_SECONDS):
            self._last_memory_alert = self._clock()
            await alert_operators(
                session, f"Redis memory at {ratio:.0%} of its cap",
                f"Redis uses {ratio:.0%} of maxmemory (warning at {app_config.REDIS_MEMORY_WARN_RATIO:.0%}). At the cap it evicts "
                "keys with a TTL - caches first, but also the worker's lock (the worker then fails closed). Raise "
                "REDIS_MAXMEMORY / MEM_REDIS in .env or find what grew (redis-cli --bigkeys).",
                NotificationSeverity.WARNING)
        return ratio
