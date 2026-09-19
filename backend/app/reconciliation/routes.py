import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.brokers.models import BrokerCredentials
from app.brokers.registry import available_brokers, get_broker_adapter
from app.core.enums import NotificationSeverity, NotificationType
from app.db.models import AuditLogRecord, BrokerCredentialRecord, TradeRecord, User
from app.db.session import get_session
from app.notifications.service import notify
from app.reconciliation.engine import reconcile_positions
from app.reconciliation.models import ReconciliationReport, ReconciliationStatus
from app.secrets_store.encryption import decrypt_text

router = APIRouter(prefix="/api/reconciliation", tags=["reconciliation"])


@router.post("/{broker_name}", response_model=ReconciliationReport)
async def reconcile_broker_positions(
    broker_name: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> ReconciliationReport:
    """Fetches this tenant's real positions from `broker_name` (using its stored, decrypted
    credentials - the same ones POST /api/broker/{name}/authenticate uses) and compares them
    against every internally open TradeRecord for this tenant. Every non-MATCHED item is logged
    to the audit trail as it's found, plus one summary row for the run itself.
    """
    if broker_name not in available_brokers():
        raise HTTPException(status_code=404, detail=f"Unknown broker '{broker_name}'. Available: {available_brokers()}")

    record = await session.scalar(
        select(BrokerCredentialRecord).where(
            BrokerCredentialRecord.tenant_id == user.tenant_id, BrokerCredentialRecord.broker_name == broker_name
        )
    )
    if record is None:
        raise HTTPException(status_code=404, detail=f"No stored credentials for broker '{broker_name}'")

    credentials = BrokerCredentials(**json.loads(decrypt_text(record.encrypted_payload)))
    adapter = get_broker_adapter(broker_name, credentials)

    try:
        broker_positions = await adapter.get_positions()
    except Exception as exc:
        session.add(
            AuditLogRecord(
                tenant_id=user.tenant_id, user_id=user.id, event="position_reconciliation_failed",
                detail=f"{broker_name}: {exc}",
            )
        )
        await session.commit()
        await notify(
            session, user.tenant_id, NotificationType.SYSTEM_FAILURE,
            title=f"Failed to fetch positions from {broker_name}", message=str(exc),
            severity=NotificationSeverity.CRITICAL, user_id=user.id,
        )
        raise HTTPException(status_code=502, detail=f"Failed to fetch positions from {broker_name}: {exc}") from exc

    open_trades = list(
        await session.scalars(
            select(TradeRecord).where(TradeRecord.tenant_id == user.tenant_id, TradeRecord.exit_time.is_(None))
        )
    )

    report = reconcile_positions(broker_name, open_trades, broker_positions)

    for item in report.items:
        if item.status != ReconciliationStatus.MATCHED:
            session.add(
                AuditLogRecord(
                    tenant_id=user.tenant_id, user_id=user.id, event="position_reconciliation_mismatch",
                    detail=f"{item.status.value} {item.symbol}: {item.detail}",
                )
            )
    session.add(
        AuditLogRecord(
            tenant_id=user.tenant_id, user_id=user.id, event="position_reconciliation_run",
            detail=f"{broker_name}: {report.mismatched_count} mismatch(es) across {len(report.items)} symbol(s)",
        )
    )
    await session.commit()

    return report
