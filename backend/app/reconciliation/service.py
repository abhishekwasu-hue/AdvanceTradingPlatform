"""Phase G1: position reconciliation as a service, and the "broker uncertain" tenant state it
governs (master prompt safety rules 8 and 18).

* `run_reconciliation` - fetch the tenant's real positions from one broker, compare them with
  every internally open TradeRecord (engine.py), write the audit rows, and *update the tenant's
  state*: zero mismatches clears `broker_uncertain_since`; mismatches (or a broker that cannot
  even be asked) set it. Used by the on-demand API route, by the worker on start-up before its
  first cycle, and by the worker every cycle while a tenant is flagged, so the block lifts by
  itself the moment the books agree.
* `mark_broker_uncertain` - called when a LIVE order FAILED (the broker call raised or timed
  out). Until reconciliation passes, `execute_signal_for_user` refuses new LIVE entries for the
  tenant; exits are never blocked (open risk is still real).
"""
import logging
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.brokers.base import BrokerInterface
from app.core.enums import NotificationSeverity, NotificationType
from app.db.models import Tenant, TradeRecord
from app.notifications.service import notify
from app.observability.metrics import RECONCILIATIONS
from app.reconciliation.engine import reconcile_positions
from app.reconciliation.models import ReconciliationReport, ReconciliationStatus

logger = logging.getLogger(__name__)


def broker_uncertain_reason(tenant: Optional[Tenant]) -> Optional[str]:
    """The refusal text for a new LIVE entry while the tenant is flagged, else None."""
    if tenant is None or tenant.broker_uncertain_since is None:
        return None
    since = tenant.broker_uncertain_since
    since = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
    return (f"Broker state uncertain since {since.isoformat(timespec='seconds')} "
            f"({tenant.broker_uncertain_reason or 'order failed'}) - LIVE entries blocked until position reconciliation passes")


async def mark_broker_uncertain(session: AsyncSession, tenant: Tenant, reason: str, *, user_id: Optional[int] = None) -> None:
    """Flag the tenant; idempotent (the first cause is kept so the operator sees what started it)."""
    if tenant.broker_uncertain_since is not None:
        return
    tenant.broker_uncertain_since = datetime.now(timezone.utc)
    tenant.broker_uncertain_reason = reason[:500]
    await write_audit_log(session, tenant.id, user_id, "broker_uncertain_set", reason[:500])
    logger.error("Tenant %s: broker state uncertain - %s", tenant.id, reason)


async def clear_broker_uncertainty(session: AsyncSession, tenant: Tenant, detail: str, *, user_id: Optional[int] = None) -> None:
    if tenant.broker_uncertain_since is None:
        return
    tenant.broker_uncertain_since = None
    tenant.broker_uncertain_reason = None
    await write_audit_log(session, tenant.id, user_id, "broker_uncertain_cleared", detail[:500])
    logger.info("Tenant %s: broker uncertainty cleared - %s", tenant.id, detail)


async def open_trades(session: AsyncSession, tenant_id: int, *, mode: Optional[str] = None) -> List[TradeRecord]:
    query = select(TradeRecord).where(TradeRecord.tenant_id == tenant_id, TradeRecord.exit_time.is_(None))
    if mode:
        query = query.where(TradeRecord.mode == mode)
    return list(await session.scalars(query))


async def run_reconciliation(
    session: AsyncSession, tenant: Tenant, broker_name: str, adapter: BrokerInterface, *,
    user_id: Optional[int] = None, source: str = "api", live_only: bool = True,
) -> ReconciliationReport:
    """One reconciliation run for `tenant` against `broker_name`; commits. Raises whatever the
    broker raised when positions cannot be fetched - after flagging the tenant, because "cannot
    ask the broker" is exactly the uncertainty the flag exists for.

    `live_only`: compare LIVE trades only (default). PAPER positions never exist at the broker,
    so counting them as MISSING_AT_BROKER would flag every paper tenant forever.
    """
    try:
        broker_positions = await adapter.get_positions()
    except Exception as exc:
        RECONCILIATIONS.labels(source=source, outcome="error").inc()
        await write_audit_log(session, tenant.id, user_id, "position_reconciliation_failed", f"{broker_name}: {exc}"[:500])
        await mark_broker_uncertain(session, tenant, f"{broker_name} positions unavailable: {exc}", user_id=user_id)
        await session.commit()
        await notify(
            session, tenant.id, NotificationType.SYSTEM_FAILURE,
            title=f"Failed to fetch positions from {broker_name}", message=str(exc)[:500],
            severity=NotificationSeverity.CRITICAL, user_id=user_id,
        )
        raise

    trades = await open_trades(session, tenant.id, mode="LIVE" if live_only else None)
    report = reconcile_positions(broker_name, trades, broker_positions)

    for item in report.items:
        if item.status != ReconciliationStatus.MATCHED:
            await write_audit_log(
                session, tenant.id, user_id, "position_reconciliation_mismatch",
                f"{item.status.value} {item.symbol}: {item.detail}"[:500],
            )
    await write_audit_log(
        session, tenant.id, user_id, "position_reconciliation_run",
        f"{broker_name} ({source}): {report.mismatched_count} mismatch(es) across {len(report.items)} symbol(s)",
    )
    tenant.last_reconciled_at = datetime.now(timezone.utc)

    if report.mismatched_count == 0:
        RECONCILIATIONS.labels(source=source, outcome="clean").inc()
        await clear_broker_uncertainty(session, tenant, f"{broker_name} reconciliation clean ({source})", user_id=user_id)
        await session.commit()
        return report

    RECONCILIATIONS.labels(source=source, outcome="mismatch").inc()
    newly_flagged = tenant.broker_uncertain_since is None
    summary = "; ".join(f"{i.status.value} {i.symbol}" for i in report.items if i.status != ReconciliationStatus.MATCHED)
    await mark_broker_uncertain(session, tenant, f"{broker_name} reconciliation: {summary}", user_id=user_id)
    await session.commit()
    if newly_flagged:
        await notify(
            session, tenant.id, NotificationType.SYSTEM_FAILURE,
            title=f"Positions at {broker_name} do not match the platform",
            message=(f"{report.mismatched_count} mismatch(es): {summary}. New LIVE entries are blocked until the books agree - "
                     "close or record the difference, then run reconciliation again.")[:1000],
            severity=NotificationSeverity.CRITICAL, user_id=user_id,
        )
    return report
