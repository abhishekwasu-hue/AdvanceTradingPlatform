"""Phase I2: broker accounts API.

* `GET   /api/accounts`                 - every account with token status and last synced numbers
* `POST  /api/accounts/{id}/sync`       - pull balance / margin / P&L from the broker now
* `POST  /api/accounts/{id}/enable|disable` - a DISABLED account refuses new LIVE entries
* `POST  /api/accounts/{id}/default`    - the account a deployment on this broker uses when it names none
* `PATCH /api/accounts/{id}`            - display name
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.service import as_dict, credential_for_account, get_account, list_accounts, sync_account
from app.audit.log import write_audit_log
from app.auth.dependencies import get_current_user, require_trader
from app.brokers.token_lifecycle import build_adapter, token_is_usable
from app.db.models import BrokerAccountRecord, User
from app.db.session import get_session

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


async def _owned(session: AsyncSession, user: User, account_id: int) -> BrokerAccountRecord:
    account = await get_account(session, user.tenant_id, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="No such account")
    return account


async def _with_status(session: AsyncSession, account: BrokerAccountRecord) -> dict:
    record = await credential_for_account(session, account)
    return as_dict(account, token_status=record.token_status if record is not None else "MISSING")


@router.get("")
async def accounts(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> List[dict]:
    return [await _with_status(session, a) for a in await list_accounts(session, user.tenant_id)]


@router.post("/{account_id}/sync")
async def sync(account_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    account = await _owned(session, user, account_id)
    record = await credential_for_account(session, account)
    if record is None:
        raise HTTPException(status_code=409, detail="No credentials stored for this account")
    if not token_is_usable(record):
        raise HTTPException(status_code=409, detail=f"{account.broker_name} session is {record.token_status} - log in from Settings first")
    account = await sync_account(session, account, build_adapter(record))
    if account.last_sync_error:
        raise HTTPException(status_code=502, detail=f"Sync failed: {account.last_sync_error}")
    return await _with_status(session, account)


class AccountPatch(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=100)


@router.patch("/{account_id}")
async def patch(account_id: int, body: AccountPatch, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> dict:
    account = await _owned(session, user, account_id)
    if body.display_name is not None:
        account.display_name = body.display_name.strip() or account.display_name
    await session.commit()
    return await _with_status(session, account)


async def _set_status(session: AsyncSession, user: User, account_id: int, status: str) -> dict:
    account = await _owned(session, user, account_id)
    account.status = status
    await write_audit_log(session, user.tenant_id, user.id, "broker_account_status", f"#{account.id} {account.broker_name}/{account.account_label} -> {status}")
    await session.commit()
    return await _with_status(session, account)


@router.post("/{account_id}/enable")
async def enable(account_id: int, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> dict:
    return await _set_status(session, user, account_id, "ACTIVE")


@router.post("/{account_id}/disable")
async def disable(account_id: int, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> dict:
    """Stops new LIVE entries routed to this account (exits still run). Deployments that name
    it record the reason each cycle instead of trading."""
    return await _set_status(session, user, account_id, "DISABLED")


@router.post("/{account_id}/default")
async def make_default(account_id: int, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> dict:
    account = await _owned(session, user, account_id)
    for other in await list_accounts(session, user.tenant_id):
        if other.broker_name == account.broker_name:
            other.is_default = other.id == account.id
    await write_audit_log(session, user.tenant_id, user.id, "broker_account_default", f"#{account.id} {account.broker_name}/{account.account_label}")
    await session.commit()
    return await _with_status(session, account)
