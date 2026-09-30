"""Phase G1: position reconciliation as a service, and the "broker uncertain" tenant state it
governs (master prompt safety rules 8 and 18).

* `run_reconciliation` - fetch the tenant's real positions from one broker, compare them with
  every internally open TradeRecord (engine.py), write the audit rows, and *update the tenant's
  state*: zero mismatches clears `broker_uncertain_since`; mismatches (or a broker that cannot
  even be asked) set it. Used by the on-demand API route, by the worker on start-up before its
  first cycle, and by the worker every cycle while a tenant is flagged, so the block lifts by
  itself the moment the books agree.
* Phase AL (V3.1-3.5): a tenant with several broker accounts reconciles *each account against its
  own session* - `run_reconciliation(..., account=...)` compares only the LIVE trades that sit in
  that account (plus, for the broker's default account, trades recorded before accounts were
  tracked), and `reconcile_accounts` runs every account and settles the tenant flag on the joint
  result, so a position in account B is never "missing" when account A is asked.
* `mark_broker_uncertain` - called when a LIVE order FAILED (the broker call raised or timed
  out). Until reconciliation passes, `execute_signal_for_user` refuses new LIVE entries for the
  tenant; exits are never blocked (open risk is still real).
"""
import logging
from datetime import datetime, timezone
from typing import Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.brokers.base import BrokerInterface
from app.core.enums import NotificationSeverity, NotificationType
from app.db.models import BrokerAccountRecord, StrategyDeploymentRecord, Tenant, TradeRecord
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


async def trades_in_account(
    session: AsyncSession, tenant_id: int, account: BrokerAccountRecord, *, mode: Optional[str] = "LIVE",
    include_unassigned: bool = True,
) -> List[TradeRecord]:
    """Phase AL: the open trades that sit in `account` - those recorded with its id, plus (when
    `include_unassigned`, meant for the broker's default account) trades recorded before accounts
    were tracked whose deployment trades through this broker, or that were entered by hand."""
    trades = await open_trades(session, tenant_id, mode=mode)
    assigned = [t for t in trades if t.broker_account_id == account.id]
    if not include_unassigned:
        return assigned
    unassigned = [t for t in trades if t.broker_account_id is None]
    dep_ids = {t.deployment_id for t in unassigned if t.deployment_id is not None}
    brokers = {}
    if dep_ids:
        rows = await session.scalars(select(StrategyDeploymentRecord).where(StrategyDeploymentRecord.id.in_(dep_ids)))
        brokers = {d.id: (d.broker_name or "").lower() for d in rows}
    for t in unassigned:
        dep_broker = brokers.get(t.deployment_id) if t.deployment_id is not None else None
        if dep_broker in (None, "", account.broker_name.lower()):
            assigned.append(t)
    return assigned


async def _is_default_for_broker(session: AsyncSession, account: BrokerAccountRecord) -> bool:
    rows = list(await session.scalars(select(BrokerAccountRecord).where(
        BrokerAccountRecord.tenant_id == account.tenant_id, BrokerAccountRecord.broker_name == account.broker_name)
        .order_by(BrokerAccountRecord.is_default.desc(), BrokerAccountRecord.id)))
    return bool(rows) and rows[0].id == account.id


async def _settle(
    session: AsyncSession, tenant: Tenant, label: str, mismatches: Sequence[str], *, user_id: Optional[int], source: str,
) -> None:
    """Apply one reconciliation outcome to the tenant flag: no mismatches clears it, otherwise it is set
    (idempotently) and, when newly set, raises the CRITICAL notification. Commits."""
    if not mismatches:
        await clear_broker_uncertainty(session, tenant, f"{label} reconciliation clean ({source})", user_id=user_id)
        await session.commit()
        return
    newly_flagged = tenant.broker_uncertain_since is None
    summary = "; ".join(mismatches)
    await mark_broker_uncertain(session, tenant, f"{label} reconciliation: {summary}", user_id=user_id)
    await session.commit()
    if newly_flagged:
        await notify(
            session, tenant.id, NotificationType.SYSTEM_FAILURE,
            title=f"Positions at {label} do not match the platform",
            message=(f"{len(mismatches)} mismatch(es): {summary}. New LIVE entries are blocked until the books agree - "
                     "close or record the difference, then run reconciliation again.")[:1000],
            severity=NotificationSeverity.CRITICAL, user_id=user_id,
        )


async def run_reconciliation(
    session: AsyncSession, tenant: Tenant, broker_name: str, adapter: BrokerInterface, *,
    user_id: Optional[int] = None, source: str = "api", live_only: bool = True,
    account: Optional[BrokerAccountRecord] = None, include_unassigned: Optional[bool] = None, settle: bool = True,
) -> ReconciliationReport:
    """One reconciliation run for `tenant` against `broker_name`; commits. Raises whatever the
    broker raised when positions cannot be fetched - after flagging the tenant, because "cannot
    ask the broker" is exactly the uncertainty the flag exists for.

    `live_only`: compare LIVE trades only (default). PAPER positions never exist at the broker,
    so counting them as MISSING_AT_BROKER would flag every paper tenant forever.

    Phase AL: with `account`, only the trades that sit in that account are compared (`adapter` must
    be that account's session); `include_unassigned` defaults to "this is the broker's default
    account". `settle=False` records the run without touching the tenant flag, for callers that
    settle several accounts together (`reconcile_accounts`).
    """
    label = broker_name if account is None else f"{broker_name}/{account.account_label}"
    try:
        broker_positions = await adapter.get_positions()
    except Exception as exc:
        RECONCILIATIONS.labels(source=source, outcome="error").inc()
        await write_audit_log(session, tenant.id, user_id, "position_reconciliation_failed", f"{label}: {exc}"[:500])
        await mark_broker_uncertain(session, tenant, f"{label} positions unavailable: {exc}", user_id=user_id)
        await session.commit()
        await notify(
            session, tenant.id, NotificationType.SYSTEM_FAILURE,
            title=f"Failed to fetch positions from {label}", message=str(exc)[:500],
            severity=NotificationSeverity.CRITICAL, user_id=user_id,
        )
        raise

    mode = "LIVE" if live_only else None
    if account is None:
        trades = await open_trades(session, tenant.id, mode=mode)
    else:
        if include_unassigned is None:
            include_unassigned = await _is_default_for_broker(session, account)
        trades = await trades_in_account(session, tenant.id, account, mode=mode, include_unassigned=include_unassigned)
    report = reconcile_positions(label, trades, broker_positions)
    report.broker_name = broker_name
    if account is not None:
        report.account_label = account.account_label
        report.accounts = [account.account_label]
        for item in report.items:
            item.account_label = account.account_label

    for item in report.items:
        if item.status != ReconciliationStatus.MATCHED:
            await write_audit_log(
                session, tenant.id, user_id, "position_reconciliation_mismatch",
                f"{item.status.value} {item.symbol}: {item.detail}"[:500],
            )
    await write_audit_log(
        session, tenant.id, user_id, "position_reconciliation_run",
        f"{label} ({source}): {report.mismatched_count} mismatch(es) across {len(report.items)} symbol(s)",
    )
    tenant.last_reconciled_at = datetime.now(timezone.utc)
    RECONCILIATIONS.labels(source=source, outcome="clean" if report.mismatched_count == 0 else "mismatch").inc()

    if not settle:
        await session.commit()
        return report
    await _settle(session, tenant, label, _mismatch_lines(report), user_id=user_id, source=source)
    return report


def _mismatch_lines(report: ReconciliationReport) -> List[str]:
    return [f"{i.status.value} {i.symbol}" + (f" [{i.account_label}]" if i.account_label else "")
            for i in report.items if i.status != ReconciliationStatus.MATCHED]


def merge_reports(broker_name: str, reports: Sequence[ReconciliationReport]) -> ReconciliationReport:
    """Phase AL: several accounts' reports as one (items keep their account label)."""
    items: List = []
    accounts: List[str] = []
    for r in reports:
        items.extend(r.items)
        accounts.extend(a for a in r.accounts if a not in accounts)
    return ReconciliationReport(
        broker_name=broker_name, checked_at=datetime.now(timezone.utc).isoformat(), items=items,
        mismatched_count=sum(r.mismatched_count for r in reports), account_label=None, accounts=accounts,
    )


async def reconcile_accounts(
    session: AsyncSession, tenant: Tenant, pairs: Iterable[Tuple[BrokerAccountRecord, BrokerInterface]], *,
    user_id: Optional[int] = None, source: str = "worker",
) -> List[ReconciliationReport]:
    """Phase AL: reconcile every (account, its session) pair and settle the tenant flag once on the
    joint result: clean everywhere clears it, any mismatch sets it with every account's lines. An
    account whose positions cannot be fetched has already flagged the tenant inside
    `run_reconciliation`; the flag is then left standing whatever the other accounts say."""
    reports: List[ReconciliationReport] = []
    failed: List[str] = []
    for account, adapter in pairs:
        try:
            reports.append(await run_reconciliation(session, tenant, account.broker_name, adapter, user_id=user_id, source=source,
                                                    account=account, settle=False))
        except Exception as exc:  # noqa: BLE001 - flagged, audited and notified by run_reconciliation
            failed.append(f"{account.broker_name}/{account.account_label}")
            logger.warning("Tenant %s: reconciliation of %s/%s failed: %s", tenant.id, account.broker_name, account.account_label, exc)
    if not reports and not failed:
        return reports
    if failed:
        return reports
    label = ", ".join(sorted({f"{r.broker_name}/{r.account_label}" for r in reports}))
    mismatches: List[str] = []
    for r in reports:
        mismatches.extend(_mismatch_lines(r))
    await _settle(session, tenant, label, mismatches, user_id=user_id, source=source)
    return reports
