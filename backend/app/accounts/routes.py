"""Phase I2: broker accounts API.

* `GET   /api/accounts`                 - every account with token status and last synced numbers
* `POST  /api/accounts/{id}/sync`       - pull balance / margin / P&L from the broker now
* `POST  /api/accounts/{id}/enable|disable` - a DISABLED account refuses new LIVE entries
* `POST  /api/accounts/{id}/default`    - the account a deployment on this broker uses when it names none
* `PATCH /api/accounts/{id}`            - display name
* M45 MA-1 (`ui_v2` + `multi_account`): `GET /api/accounts/mine`, `POST /api/accounts/mine/{id}/role`,
  `POST /api/accounts/mine/{id}/group` - the caller's own accounts as Primary / Secondary (app/accounts/multi.py)
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.service import as_dict, credential_for_account, get_account, list_accounts, sync_account
from app.audit.log import write_audit_log
from app.auth.dependencies import get_current_user, require_trader
from app.auth.scopes import require_scope
from app.brokers.token_lifecycle import build_adapter, token_is_usable
from app.db.models import BrokerAccountRecord, User
from app.db.session import get_session
from app.paper.fno_routes import OrderBody

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


@router.post("/{account_id}/sync", dependencies=[Depends(require_scope("brokers:write"))])
async def sync(account_id: int, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> dict:
    # Z10-c: rewrites the organisation's balance snapshot and spends the account's broker budget - a trading role.
    account = await _owned(session, user, account_id)
    record = await credential_for_account(session, account)
    if record is None:
        raise HTTPException(status_code=409, detail="No credentials stored for this account")
    if not token_is_usable(record):
        raise HTTPException(status_code=409, detail=f"{account.broker_name} session is {record.token_status} - log in on the Broker page first")
    account = await sync_account(session, account, build_adapter(record))
    if account.last_sync_error:
        raise HTTPException(status_code=502, detail=f"Sync failed: {account.last_sync_error}")
    return await _with_status(session, account)


class AccountPatch(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=100)


@router.patch("/{account_id}", dependencies=[Depends(require_scope("brokers:write"))])
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


@router.post("/{account_id}/enable", dependencies=[Depends(require_scope("brokers:write"))])
async def enable(account_id: int, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> dict:
    return await _set_status(session, user, account_id, "ACTIVE")


@router.post("/{account_id}/disable", dependencies=[Depends(require_scope("brokers:write"))])
async def disable(account_id: int, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> dict:
    """Stops new LIVE entries routed to this account (exits still run). Deployments that name
    it record the reason each cycle instead of trading."""
    return await _set_status(session, user, account_id, "DISABLED")


@router.post("/{account_id}/default", dependencies=[Depends(require_scope("brokers:write"))])
async def make_default(account_id: int, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> dict:
    account = await _owned(session, user, account_id)
    for other in await list_accounts(session, user.tenant_id):
        if other.broker_name == account.broker_name:
            other.is_default = other.id == account.id
    await write_audit_log(session, user.tenant_id, user.id, "broker_account_default", f"#{account.id} {account.broker_name}/{account.account_label}")
    await session.commit()
    return await _with_status(session, account)


# --- M45 MA-1: the caller's own accounts as Primary / Secondary (docs/design/M45_MULTI_ACCOUNT.md) -------------------
# Behind `ui_v2` + `multi_account` (OFF): 404 with X-Feature-Disabled while either is off. Registry only - no routing.

async def _require_multi(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> None:
    from app.accounts import multi
    from app.platform.controls import flag_enabled
    for name in (multi.PAGE_FLAG, multi.FLAG):
        if not await flag_enabled(session, name, user.tenant_id):
            raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": name})


def _refused(exc: Exception) -> HTTPException:
    return HTTPException(status_code=409, detail={"message": str(exc), "code": getattr(exc, "code", "refused"),
                                                  **getattr(exc, "extra", {})})


@router.get("/mine", dependencies=[Depends(_require_multi)])
async def my_accounts(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """MB-1: the caller's own accounts with role, group, enabled and broker session state; the open LIVE trades that
    hold back a role change (M45-3)."""
    from app.accounts import multi
    rows = [await multi.describe(session, a) for a in await multi.own_accounts(session, user)]
    blocking = await multi.unaccounted_live_trades(session, user)
    return {"accounts": rows, "unaccounted_live_trades": [{"id": t.id, "symbol": t.symbol} for t in blocking]}


class RoleBody(BaseModel):
    role: Optional[str] = Field(default=None, description="PRIMARY, SECONDARY or null to take the account out")
    confirm: bool = False


@router.post("/mine/{account_id}/role", dependencies=[Depends(_require_multi), Depends(require_scope("brokers:write"))])
async def set_my_role(account_id: int, body: RoleBody, user: User = Depends(require_trader),
                      session: AsyncSession = Depends(get_session)) -> dict:
    from app.accounts import multi
    account = await _owned(session, user, account_id)
    try:
        account = await multi.set_role(session, user, account, (body.role or "").upper() or None, confirm=body.confirm)
    except multi.RegistryRefused as exc:
        raise _refused(exc) from None
    return await multi.describe(session, account)


class GroupBody(BaseModel):
    group_name: Optional[str] = Field(default=None, max_length=50)


@router.post("/mine/{account_id}/group", dependencies=[Depends(_require_multi), Depends(require_scope("brokers:write"))])
async def set_my_group(account_id: int, body: GroupBody, user: User = Depends(require_trader),
                       session: AsyncSession = Depends(get_session)) -> dict:
    from app.accounts import multi
    account = await _owned(session, user, account_id)
    try:
        account = await multi.set_group(session, user, account, body.group_name)
    except multi.RegistryRefused as exc:
        raise _refused(exc) from None
    return await multi.describe(session, account)


# --- M45 MA-3: simulated PAPER accounts and multi-account PAPER ticket orders (app/accounts/paper_fanout.py) ----------
# Behind `ui_v2` + `multi_account` (OFF); the order also needs the paper ticket's own flags. PAPER only.

async def _own_sub_account(session: AsyncSession, user: User, sub_id: int):
    from app.db.models import PaperSubAccountRecord
    row = await session.get(PaperSubAccountRecord, sub_id)
    if row is None or row.user_id != user.id or row.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="No such simulated account")
    return row


@router.get("/mine/paper", dependencies=[Depends(_require_multi)])
async def my_paper_accounts(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> List[dict]:
    from app.accounts import paper_fanout
    return [paper_fanout.describe(a) for a in await paper_fanout.sub_accounts(session, user)]


class PaperAccountBody(BaseModel):
    label: str = Field(min_length=1, max_length=50)
    capital: float = Field(gt=0, lt=1e12)


@router.post("/mine/paper", dependencies=[Depends(_require_multi), Depends(require_scope("brokers:write"))])
async def add_paper_account(body: PaperAccountBody, user: User = Depends(require_trader),
                            session: AsyncSession = Depends(get_session)) -> dict:
    from app.accounts import paper_fanout
    try:
        return paper_fanout.describe(await paper_fanout.create(session, user, body.label, body.capital))
    except paper_fanout.FanoutRefused as exc:
        raise _refused(exc) from None


class PaperAccountPatch(BaseModel):
    role: Optional[str] = None
    clear_role: bool = False
    group_name: Optional[str] = Field(default=None, max_length=50)
    enabled: Optional[bool] = None
    sizing_mode: Optional[str] = None
    sizing_value: Optional[float] = Field(default=None, gt=0, lt=1e6)
    confirm: bool = False


@router.patch("/mine/paper/{sub_id}", dependencies=[Depends(_require_multi), Depends(require_scope("brokers:write"))])
async def change_paper_account(sub_id: int, body: PaperAccountPatch, user: User = Depends(require_trader),
                               session: AsyncSession = Depends(get_session)) -> dict:
    from app.accounts import paper_fanout
    row = await _own_sub_account(session, user, sub_id)
    try:
        row = await paper_fanout.update(session, user, row, role=body.role, clear_role=body.clear_role, group_name=body.group_name,
                                        enabled=body.enabled, sizing_mode=body.sizing_mode, sizing_value=body.sizing_value,
                                        confirm=body.confirm)
    except paper_fanout.FanoutRefused as exc:
        raise _refused(exc) from None
    return paper_fanout.describe(row)


class PaperFanoutBody(BaseModel):
    order: OrderBody
    run_on: Optional[str] = Field(default=None, max_length=60, description="PRIMARY, PRIMARY+<group> or ALL")


@router.post("/mine/paper/order", dependencies=[Depends(_require_multi), Depends(require_scope("brokers:write"))])
async def place_paper_fanout(body: PaperFanoutBody, user: User = Depends(require_trader),
                             session: AsyncSession = Depends(get_session)) -> dict:
    """One ticket order on the caller's simulated PAPER accounts: a result row per account (MB-5). Never a broker order."""
    from app.accounts import paper_fanout
    from app.ai.worker_bridge import price_lookup_for_user
    from app.paper.fno_routes import MANUAL, _enabled, place_rate_limit
    await place_rate_limit(user)
    await _enabled(session, user)
    lookup = await price_lookup_for_user(session, user)
    try:
        return await paper_fanout.place(session, user, body.order.ask(), override=body.run_on,
                                        price_lookup=lookup, origin=MANUAL)
    except paper_fanout.FanoutRefused as exc:
        raise _refused(exc) from None


class PaperFanoutExitBody(BaseModel):
    ref: str = Field(pattern=r"^[A-Za-z0-9-]{8,64}$")


@router.post("/mine/paper/exit", dependencies=[Depends(_require_multi), Depends(require_scope("brokers:write"))])
async def exit_paper_fanout(body: PaperFanoutExitBody, user: User = Depends(require_trader),
                            session: AsyncSession = Depends(get_session)) -> dict:
    """MA-4: close every open child of one multi-account PAPER order (sold legs first; each child's own exit also works)."""
    from app.accounts import paper_fanout
    try:
        return await paper_fanout.exit_all(session, user, body.ref)
    except paper_fanout.FanoutRefused as exc:
        raise _refused(exc) from None
