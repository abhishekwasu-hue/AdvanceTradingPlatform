"""Settings redesign part A: "Connect AI in one paste" - the routes of the Connect AI panel (`/api/ai/byok/...`).

Behind the `ai_one_key` flag (which needs `ui_v2`): 404 while it is off. Every route works on the caller's own rows only
(user id AND tenant id from the session, never from the request; bodies refuse unknown fields). The key goes in once and
never comes back: responses carry its last four characters only. Errors are one plain sentence.

* `GET    /api/ai/byok/connection`      - the panel's summary (connected, provider, key end, test result, consent line,
                                           the free-tier warning, the rupee limits line, the "use the organisation's key" offer).
* `POST   /api/ai/byok/connect`         - paste -> detect -> consent -> save (encrypted) -> test (text + a tiny image) -> summary.
                                           A Cloudflare token also needs its Account ID (in the paste or `account_id`).
* `DELETE /api/ai/byok/connection`      - Disconnect (the key row is deleted; nothing else is).
* `PUT    /api/ai/byok/limits`          - the daily / monthly limits, typed in rupees (stored in USD).
* `POST   /api/ai/byok/acknowledge`     - the consent line for a key saved before it existed, or "My key is on a paid plan".
* `POST   /api/ai/byok/copy-tenant-key` - "Use this key for your account too": OWNER only, and only the owner who entered
                                           the organisation's key (re-sealed for the user, audited).
Nothing here places, blocks or changes an order (ADR-0006).
"""
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import byok, resolve
from app.ai.rate_limit import ai_rate_limit
from app.auth.dependencies import get_current_user, require_owner
from app.db.models import User
from app.db.session import get_session

router = APIRouter(prefix="/api/ai/byok", tags=["ai-connect"], dependencies=[Depends(ai_rate_limit)])


async def _flag_on(session: AsyncSession, user: User) -> None:
    if not await resolve.one_key_on(session, user.tenant_id):
        raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": resolve.FLAG})


async def connect_user(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> User:
    await _flag_on(session, user)
    return user


async def connect_owner(user: User = Depends(require_owner), session: AsyncSession = Depends(get_session)) -> User:
    await _flag_on(session, user)
    return user


class ConnectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: str = Field(min_length=1, max_length=512, repr=False)
    provider: Optional[str] = Field(default=None, pattern=r"^(anthropic|openai|gemini|openrouter|groq|mistral|zai|cloudflare)$",
                                    description="only when the key's format did not say (the small picker)")
    consent: bool = False
    # MO2A-2: Cloudflare Workers AI's Account ID, when it was not pasted next to the token (the panel's second box).
    account_id: Optional[str] = Field(default=None, max_length=64, repr=False)


class LimitsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    daily_inr: Optional[float] = Field(default=None, ge=0)
    monthly_inr: Optional[float] = Field(default=None, ge=0)


class AcknowledgeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["consent", "paid_plan"]


class CopyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consent: bool = False


def _plain(status: int, message: str, code: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


@router.get("/connection")
async def get_connection(user: User = Depends(connect_user), session: AsyncSession = Depends(get_session)) -> dict:
    return await byok.connection(session, user)


@router.post("/connect")
async def post_connect(body: ConnectBody, request: Request, user: User = Depends(connect_user),
                       session: AsyncSession = Depends(get_session)) -> dict:
    try:
        return await byok.connect(session, user, api_key=body.api_key, provider=body.provider, consent=body.consent, request=request,
                                  account_id=body.account_id)
    except byok.ConsentRequired as exc:
        raise _plain(400, str(exc), "consent_required") from None       # `from None`: no traceback carries the body
    except byok.ProviderNeeded as exc:
        raise _plain(400, str(exc), "provider_needed") from None
    except byok.AccountIdNeeded as exc:
        raise _plain(400, str(exc), "account_id_needed") from None
    except byok.ByokError as exc:
        raise _plain(400, str(exc), "key_refused") from None


@router.delete("/connection")
async def delete_connection(user: User = Depends(connect_user), session: AsyncSession = Depends(get_session)) -> dict:
    await byok.disconnect(session, user)
    return await byok.connection(session, user)


@router.put("/limits")
async def put_limits(body: LimitsBody, user: User = Depends(connect_user), session: AsyncSession = Depends(get_session)) -> dict:
    try:
        await byok.save_limits_inr(session, user, daily_inr=body.daily_inr, monthly_inr=body.monthly_inr)
    except byok.ByokError as exc:
        raise _plain(400, str(exc), "limits_refused") from None
    return await byok.connection(session, user)


@router.post("/acknowledge")
async def post_acknowledge(body: AcknowledgeBody, request: Request, user: User = Depends(connect_user),
                           session: AsyncSession = Depends(get_session)) -> dict:
    record = await byok.connected_key(session, user)
    if record is None:
        raise _plain(404, "Connect a key first.", "not_connected")
    if body.kind == "consent":
        await byok.accept_consent(session, user, record.provider, request=request)
        await session.commit()
    else:
        await byok.confirm_paid_plan(session, user, request=request)
    return await byok.connection(session, user)


@router.post("/copy-tenant-key")
async def post_copy_tenant_key(body: CopyBody, request: Request, user: User = Depends(connect_owner),
                               session: AsyncSession = Depends(get_session)) -> dict:
    try:
        await byok.copy_tenant_key(session, user, consent=body.consent, request=request)
    except byok.ConsentRequired as exc:
        raise _plain(400, str(exc), "consent_required") from None
    except LookupError:
        raise _plain(404, "There is no organisation key you can use here.", "nothing_to_copy") from None
    except byok.ByokError as exc:
        raise _plain(400, str(exc), "key_refused") from None
    record = await byok.connected_key(session, user)
    if record is not None:
        try:
            await byok.test_key(session, user, record.provider, with_image=True, plain=True)
        except byok.CapReached:
            pass                                          # nothing more may be spent today: the key stays, untested
    return await byok.connection(session, user)
