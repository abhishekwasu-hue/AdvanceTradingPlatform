"""BYOK-V1: `/api/ai/byok/*` - the logged-in user's own AI provider keys, key test, spend caps and Vision preferences.
BYOK-V2: `/reports` - the user's own Vision reports on proposals (read and delete).
BYOK-V3: the same `/reports` for scan-alert and daily-review reports (`?kind=`, `?notification_id=`).

Every route works on the caller's own rows only (user id AND tenant id from the session, never from the request; bodies
refuse unknown fields, so a `user_id` cannot be smuggled in). With the `vision_byok` flag off every route answers 404.
The key goes in once (PUT) and never comes back: responses carry the hint (first 4 + last 4 characters) only.
"""
from typing import Dict, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import byok, vision_audit
from app.ai.rate_limit import ai_rate_limit
from app.auth.dependencies import get_current_user
from app.db.models import User
from app.db.session import get_session
from app.platform.controls import flag_enabled

router = APIRouter(prefix="/api/ai/byok", tags=["ai-byok"], dependencies=[Depends(ai_rate_limit)])

_PROVIDER = r"^(anthropic|openai|gemini)$"


async def byok_user(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> User:
    """The caller, when the operator has turned `vision_byok` on for their organisation; 404 otherwise."""
    if not await flag_enabled(session, byok.FLAG, user.tenant_id):
        raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": byok.FLAG})
    return user


class KeyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(pattern=_PROVIDER)
    api_key: str = Field(min_length=1, max_length=512, repr=False)
    model: Optional[str] = Field(default=None, max_length=80)


class AuditScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposals: Optional[bool] = None
    scan_alerts: Optional[bool] = None
    daily_review: Optional[bool] = None


class Channels(BaseModel):
    model_config = ConfigDict(extra="forbid")
    telegram: Optional[bool] = None
    in_app: Optional[bool] = None


class PrefsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: Optional[bool] = None
    provider: Optional[str] = Field(default=None, pattern=r"^(anthropic|openai|gemini|auto)$",
                                    description="where charts go; 'auto' = the first provider with a key")
    daily_cap_usd: Optional[float] = Field(default=None, ge=0)
    monthly_cap_usd: Optional[float] = Field(default=None, ge=0)
    audit: Optional[AuditScope] = None
    channels: Optional[Channels] = None
    models: Optional[Dict[str, str]] = Field(default=None, description="model per provider; '' = the configured default")


def _bad(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/status")
async def get_status(user: User = Depends(byok_user), session: AsyncSession = Depends(get_session)) -> dict:
    return await byok.status(session, user)


@router.put("/key")
async def put_key(body: KeyBody, user: User = Depends(byok_user), session: AsyncSession = Depends(get_session)) -> dict:
    try:
        await byok.save_key(session, user, provider=body.provider, api_key=body.api_key, model=body.model)
    except byok.ByokError as exc:
        raise _bad(exc) from None          # `from None`: the traceback never carries the request body
    return await byok.status(session, user)


@router.delete("/key/{provider}")
async def delete_key(provider: str = Path(pattern=_PROVIDER), user: User = Depends(byok_user),
                     session: AsyncSession = Depends(get_session)) -> dict:
    if not await byok.delete_key(session, user, provider):
        raise HTTPException(status_code=404, detail="No key stored for this provider")
    return await byok.status(session, user)


@router.post("/test/{provider}")
async def test_key(provider: str = Path(pattern=_PROVIDER), user: User = Depends(byok_user),
                   session: AsyncSession = Depends(get_session)) -> dict:
    try:
        result = await byok.test_key(session, user, provider)
    except LookupError:
        raise HTTPException(status_code=404, detail="No key stored for this provider") from None
    except byok.CapReached as exc:
        raise HTTPException(status_code=409, detail=f"Not tested: {exc.reason}. Raise the cap or wait until it resets.") from None
    return {"result": result, "status": await byok.status(session, user)}


@router.put("/prefs")
async def put_prefs(body: PrefsBody, user: User = Depends(byok_user), session: AsyncSession = Depends(get_session)) -> dict:
    try:
        await byok.save_prefs(session, user, enabled=body.enabled,
                              provider=None if body.provider in (None, "auto") else body.provider, clear_provider=body.provider == "auto",
                              daily_cap_usd=body.daily_cap_usd, monthly_cap_usd=body.monthly_cap_usd,
                              audit=body.audit.model_dump(exclude_none=True) if body.audit else None,
                              channels=body.channels.model_dump(exclude_none=True) if body.channels else None,
                              models=body.models)
    except byok.ByokError as exc:
        raise _bad(exc) from None
    return await byok.status(session, user)


# --- BYOK-V2: the caller's own Vision reports on proposals ----------------------------------------------------------------

@router.get("/reports")
async def list_reports(action_id: Optional[int] = Query(default=None, ge=1), notification_id: Optional[int] = Query(default=None, ge=1),
                       kind: Optional[Literal["proposal", "scan_alert", "daily_review"]] = Query(default=None),
                       user: User = Depends(byok_user), session: AsyncSession = Depends(get_session)) -> dict:
    """The caller's own Vision reports (never another user's): for one proposal, one scan-alert notification, one kind
    (BYOK-V3: `scan_alert`, `daily_review`) or the latest ones, plus the line shown when there is none ("Vision off - add
    your API key in Settings", ...)."""
    return await vision_audit.reports_for(session, user, action_id, kind=kind, notification_id=notification_id)


@router.delete("/reports/{report_id}")
async def delete_report(report_id: int = Path(ge=1), user: User = Depends(byok_user), session: AsyncSession = Depends(get_session)) -> dict:
    """Deletes one of the caller's own reports; 404 for anybody else's (the same answer as for a missing one)."""
    if not await vision_audit.delete_report(session, user, report_id):
        raise HTTPException(status_code=404, detail="Report not found")
    return {"deleted": report_id}
