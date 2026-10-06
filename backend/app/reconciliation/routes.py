import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.brokers.models import BrokerCredentials
from app.brokers.registry import available_brokers, get_broker_adapter
from app.db.models import BrokerCredentialRecord, Tenant, User
from app.db.session import get_session
from app.reconciliation.models import ReconciliationReport
from app.accounts.service import ensure_account_for_credential
from app.reconciliation.service import merge_reports, open_trades, reconcile_accounts, run_reconciliation
from app.secrets_store.encryption import decrypt_text
from app.secrets_store.envelope import PURPOSE_BROKER_CREDENTIAL

router = APIRouter(prefix="/api/reconciliation", tags=["reconciliation"])


@router.post("/{broker_name}", response_model=ReconciliationReport)
async def reconcile_broker_positions(
    broker_name: str, account_label: Optional[str] = Query(default=None, max_length=50),
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> ReconciliationReport:
    """Fetches this tenant's real positions from `broker_name` (using its stored, decrypted
    credentials - the same ones POST /api/broker/{name}/authenticate uses) and compares them
    against the internally open LIVE TradeRecords. Every non-MATCHED item is logged to the audit
    trail as it's found, plus one summary row for the run itself.

    Phase AL: without `account_label`, every account stored at the broker is reconciled against
    its own session and the tenant flag settles on the joint result (one merged report, items
    carrying their account label); with it, that one account alone.
    """
    if broker_name not in available_brokers():
        raise HTTPException(status_code=404, detail=f"Unknown broker '{broker_name}'. Available: {available_brokers()}")

    query = select(BrokerCredentialRecord).where(
        BrokerCredentialRecord.tenant_id == user.tenant_id, BrokerCredentialRecord.broker_name == broker_name)
    if account_label:
        query = query.where(BrokerCredentialRecord.account_label == account_label)
    records = list(await session.scalars(query.order_by(BrokerCredentialRecord.id)))
    if not records:
        detail = f"No stored credentials for broker '{broker_name}'" + (f" (account '{account_label}')" if account_label else "")
        raise HTTPException(status_code=404, detail=detail)
    tenant = await session.get(Tenant, user.tenant_id)

    pairs = []
    for record in records:
        credentials = BrokerCredentials(**json.loads(decrypt_text(record.encrypted_payload, PURPOSE_BROKER_CREDENTIAL)))
        account = await ensure_account_for_credential(session, record)
        pairs.append((account, get_broker_adapter(broker_name, credentials)))

    if len(pairs) == 1:
        account, adapter = pairs[0]
        try:
            return await run_reconciliation(session, tenant, broker_name, adapter, user_id=user.id, source="api", account=account)
        except Exception as exc:  # noqa: BLE001 - already audited, notified and flagged by the service
            raise HTTPException(status_code=502, detail=f"Failed to fetch positions from {broker_name}: {exc}") from exc
    reports = await reconcile_accounts(session, tenant, pairs, user_id=user.id, source="api")
    if len(reports) < len(pairs):
        missing = [a.account_label for a, _ in pairs if a.account_label not in {r.account_label for r in reports}]
        raise HTTPException(status_code=502, detail=f"Failed to fetch positions from {broker_name} account(s) {', '.join(missing)}")
    return merge_reports(broker_name, reports)


class ReconciliationStatusResponse(BaseModel):
    broker_uncertain: bool
    broker_uncertain_since: Optional[datetime] = None
    broker_uncertain_reason: Optional[str] = None
    last_reconciled_at: Optional[datetime] = None
    open_live_trades: int


@router.get("/status", response_model=ReconciliationStatusResponse)
async def reconciliation_status(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> ReconciliationStatusResponse:
    """Whether this organisation's LIVE entries are currently blocked pending reconciliation
    (Phase G1), when it was last reconciled, and how many LIVE positions the platform holds open."""
    tenant = await session.get(Tenant, user.tenant_id)
    live_open = len(await open_trades(session, user.tenant_id, mode="LIVE"))
    return ReconciliationStatusResponse(
        broker_uncertain=tenant.broker_uncertain_since is not None,
        broker_uncertain_since=tenant.broker_uncertain_since,
        broker_uncertain_reason=tenant.broker_uncertain_reason,
        last_reconciled_at=tenant.last_reconciled_at,
        open_live_trades=live_open,
    )
