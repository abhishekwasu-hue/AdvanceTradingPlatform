"""Phase I2: broker accounts (master prompt V3.14 rule 3, V3.1-3.5 routing).

A credential row is *how* the platform talks to a broker; a `BrokerAccountRecord` is the trading
account behind it - the broker's own identifier, its status (ACTIVE/DISABLED), and the last
synced balance, margin and P&L. Several accounts at one broker are several credential rows told
apart by `account_label` ("primary" is the one every pre-Phase-I caller means).

Routing: a deployment names an account (`broker_account_id`) or gets the broker's default one;
the worker builds one adapter per (broker, label) and refuses LIVE entries to a DISABLED account.
"""
import logging
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.base import BrokerInterface
from app.db.models import BrokerAccountRecord, BrokerCredentialRecord

logger = logging.getLogger(__name__)

PRIMARY = "primary"


async def ensure_account_for_credential(session: AsyncSession, record: BrokerCredentialRecord) -> BrokerAccountRecord:
    """The account row for a credential row, created on first sight. The first account at a
    broker becomes that broker's default."""
    account = await session.scalar(select(BrokerAccountRecord).where(
        BrokerAccountRecord.tenant_id == record.tenant_id, BrokerAccountRecord.broker_name == record.broker_name,
        BrokerAccountRecord.account_label == (record.account_label or PRIMARY),
    ))
    if account is None:
        others = await session.scalar(select(BrokerAccountRecord.id).where(
            BrokerAccountRecord.tenant_id == record.tenant_id, BrokerAccountRecord.broker_name == record.broker_name).limit(1))
        account = BrokerAccountRecord(
            tenant_id=record.tenant_id, user_id=record.user_id, credential_id=record.id, broker_name=record.broker_name,
            account_label=record.account_label or PRIMARY, display_name=f"{record.broker_name} {record.account_label or PRIMARY}",
            is_default=others is None,
        )
        session.add(account)
        await session.flush()
    elif account.credential_id != record.id:
        account.credential_id = record.id
    return account


async def list_accounts(session: AsyncSession, tenant_id: int) -> List[BrokerAccountRecord]:
    """Every account, creating rows for credentials stored before Phase I2."""
    credentials = list(await session.scalars(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == tenant_id)))
    for record in credentials:
        await ensure_account_for_credential(session, record)
    await session.commit()
    return list(await session.scalars(select(BrokerAccountRecord).where(BrokerAccountRecord.tenant_id == tenant_id)
                                      .order_by(BrokerAccountRecord.broker_name, BrokerAccountRecord.is_default.desc(), BrokerAccountRecord.id)))


async def get_account(session: AsyncSession, tenant_id: int, account_id: int) -> Optional[BrokerAccountRecord]:
    account = await session.get(BrokerAccountRecord, account_id)
    return account if account is not None and account.tenant_id == tenant_id else None


async def default_account(session: AsyncSession, tenant_id: int, broker_name: str) -> Optional[BrokerAccountRecord]:
    rows = list(await session.scalars(select(BrokerAccountRecord).where(
        BrokerAccountRecord.tenant_id == tenant_id, BrokerAccountRecord.broker_name == broker_name)
        .order_by(BrokerAccountRecord.is_default.desc(), BrokerAccountRecord.id)))
    return rows[0] if rows else None


async def credential_for_account(session: AsyncSession, account: BrokerAccountRecord) -> Optional[BrokerCredentialRecord]:
    if account.credential_id is not None:
        record = await session.get(BrokerCredentialRecord, account.credential_id)
        if record is not None:
            return record
    return await session.scalar(select(BrokerCredentialRecord).where(
        BrokerCredentialRecord.tenant_id == account.tenant_id, BrokerCredentialRecord.broker_name == account.broker_name,
        BrokerCredentialRecord.account_label == account.account_label))


async def routing_for_deployment(
    session: AsyncSession, tenant_id: int, broker_name: Optional[str], account_id: Optional[int],
) -> Tuple[Optional[BrokerAccountRecord], str]:
    """(account, credential label) a deployment's LIVE orders go to. No account rows yet ->
    the primary credential, as before Phase I2."""
    if account_id is not None:
        account = await get_account(session, tenant_id, account_id)
        if account is not None:
            return account, account.account_label
    if broker_name:
        account = await default_account(session, tenant_id, broker_name)
        if account is not None:
            return account, account.account_label
    return None, PRIMARY


async def sync_account(session: AsyncSession, account: BrokerAccountRecord, adapter: BrokerInterface) -> BrokerAccountRecord:
    """Pull balance/margin from the broker and the P&L of the positions it reports. Commits.
    A failed pull is recorded on the row (last_sync_error), never raised past here."""
    try:
        balance = await adapter.get_balance()
        account.available_balance = float(balance.available_margin or balance.available_cash or 0.0)
        account.used_margin = float(balance.used_margin or 0.0)
        try:
            profile = await adapter.get_profile()
            account.broker_account_identifier = getattr(profile, "user_id", None) or account.broker_account_identifier
        except Exception:  # noqa: BLE001 - identifier is a nicety
            pass
        positions = await adapter.get_positions()
        account.unrealized_pnl = round(sum(float(p.pnl or 0.0) for p in positions if p.quantity != 0), 2)
        account.realized_pnl = round(sum(float(p.pnl or 0.0) for p in positions if p.quantity == 0), 2)
        account.last_sync_at = datetime.now(timezone.utc)
        account.last_sync_error = None
    except Exception as exc:  # noqa: BLE001
        account.last_sync_error = f"{type(exc).__name__}: {exc}"[:300]
        logger.warning("Account %s sync failed: %s", account.id, exc)
    await session.commit()
    await session.refresh(account)
    return account


def as_dict(account: BrokerAccountRecord, token_status: Optional[str] = None) -> dict:
    iso = lambda v: v.isoformat() if v else None  # noqa: E731
    return {
        "id": account.id, "broker_name": account.broker_name, "account_label": account.account_label,
        "broker_account_identifier": account.broker_account_identifier, "display_name": account.display_name,
        "status": account.status, "is_default": account.is_default, "available_balance": account.available_balance,
        "used_margin": account.used_margin, "realized_pnl": account.realized_pnl, "unrealized_pnl": account.unrealized_pnl,
        "last_sync_at": iso(account.last_sync_at), "last_sync_error": account.last_sync_error, "token_status": token_status,
        "created_at": iso(account.created_at),
    }
