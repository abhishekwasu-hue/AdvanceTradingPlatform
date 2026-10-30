"""`/api/growth/*` - the user's plan page and his discipline share card (behind `ui_v2` + `growth_v2`; 404 while either
is off, X-Feature-Disabled names the flag).

* `GET /api/growth/plan` - what his plan includes and his usage meter (app/growth/plan.py).
* `GET /api/growth/dish/{page}` - MO4 slice 4: the "ready-made dish" at the top of Signals, My positions, the Journal,
  Charts (`?symbol=`, `&exchange=`) and Find setups, from his own records (app/growth/dishes.py).
* `GET /api/growth/share/discipline-week` - the "my discipline this week" card from his own PAPER record, guarded for a
  public surface: no security names, no prices, no money, no call words (app/growth/share.py).

Per user: every read is the caller's own (the organisation's trading bots and backtests are counted for the team, and the
meter says so). Read-only towards billing and trading: nothing here changes a plan, starts a trial or touches an order.

Readiness C13 adds (each behind its own flag, OFF):
* `GET /api/growth/referral`, `POST /api/growth/referral/code`, `POST /api/growth/referral/claim` - his own invite link and
  the new account's one claim (`ui_v2` + `referrals`; app/growth/referral.py);
* `GET` / `POST /api/growth/onboarding/unsubscribe?t=` - the first-week e-mails' signed unsubscribe link (no sign-in);
* `GET` / `PUT /api/admin/growth/referral-settings` - the reward wording (SUPER_ADMIN + two-factor, audited).
"""
from __future__ import annotations

import html
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_mfa_session, require_role
from app.db.models import User
from app.db.session import get_session
from app.growth import FLAG, PAGE_FLAG, dishes, onboarding, plan, referral, share

router = APIRouter(prefix="/api/growth", tags=["growth"])


async def require_flags(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> None:
    from app.platform.controls import flag_enabled
    for name in (PAGE_FLAG, FLAG):
        if not await flag_enabled(session, name, user.tenant_id):
            raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": name})


@router.get("/plan", dependencies=[Depends(require_flags)])
async def plan_page(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """His plan in plain words and how much of it he used (this month / today / now)."""
    return await plan.plan_page(session, user)


@router.get("/share/discipline-week", dependencies=[Depends(require_flags)])
async def discipline_week(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """The share card's facts: discipline only, never a name, a price or a call."""
    return await share.discipline_week(session, user)


@router.get("/dish/{page}", dependencies=[Depends(require_flags)])
async def dish(page: str, symbol: Optional[str] = Query(None, max_length=50), exchange: str = Query("NSE", max_length=20),
               user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """The page's dish: a few plain lines from his own records and the market's data, in structure words; never advice."""
    if page not in dishes.PAGES:
        raise HTTPException(status_code=404, detail="Not found")
    if page == "charts" and not (symbol or "").strip():
        raise HTTPException(status_code=422, detail="Pick a symbol first.")
    return await dishes.build(session, user, page, symbol=symbol, exchange=exchange)


# --- Readiness C13: the referral flow (flags ui_v2 + referrals) ------------------------------------------------------------

async def require_referral_flags(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> None:
    from app.platform.controls import flag_enabled
    for name in (PAGE_FLAG, referral.FLAG):
        if not await flag_enabled(session, name, user.tenant_id):
            raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": name})


async def _referral_view(session: AsyncSession, user: User) -> Dict[str, Any]:
    from app.alerts.deeplink import app_base_url
    code = await referral.own_code(session, user)
    base = app_base_url()
    settings = await referral.get_settings(session)
    return {"code": code, "link": f"{base}/?ref={code}" if code and base else None,
            "reward_text": settings.reward_text or None, "joined": await referral.joined_count(session, user.id)}


@router.get("/referral", dependencies=[Depends(require_referral_flags)])
async def referral_card(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """His invite code and link (null until he asks for one), the reward wording and how many people joined."""
    return await _referral_view(session, user)


@router.post("/referral/code", dependencies=[Depends(require_referral_flags)])
async def referral_code(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Creates his invite code once (the same code on every later call)."""
    await referral.ensure_code(session, user)
    return await _referral_view(session, user)


class ClaimIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=32)


@router.post("/referral/claim", dependencies=[Depends(require_referral_flags)])
async def referral_claim(body: ClaimIn, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """A new account names the invite code it arrived with, once."""
    try:
        await referral.claim(session, user, body.code)
    except referral.ClaimRefused as refused:
        raise HTTPException(status_code=409, detail=str(refused)) from None
    return {"claimed": True}


# --- Readiness C13: the onboarding e-mails' unsubscribe link (no sign-in; the signed token is the authority) ---------------

def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f"<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>{html.escape(title)}</title>"
                        f"<body><h1>{html.escape(title)}</h1>{body}</body>")


@router.get("/onboarding/unsubscribe", response_class=HTMLResponse)
async def onboarding_unsubscribe_page(t: str = Query("", max_length=600)) -> HTMLResponse:
    """A page with one button, so a mail scanner that follows the link unsubscribes nobody."""
    who = onboarding.read_unsubscribe_token(t)
    if who is None:
        return _page("Link not valid", "<p>This unsubscribe link is not valid.</p>")
    return _page("Stop the first-week e-mails", f"<p>Stop the first-week getting-started e-mails to <b>{html.escape(who[1])}</b>? "
                 "Account e-mails such as password resets are not affected.</p>"
                 f"<form method='post' action='?t={html.escape(t)}'><button type='submit'>Unsubscribe</button></form>")


@router.post("/onboarding/unsubscribe", response_class=HTMLResponse)
async def onboarding_unsubscribe(t: str = Query("", max_length=600), session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    """The button's target and the RFC 8058 one-click target."""
    who = onboarding.read_unsubscribe_token(t)
    if who is None:
        raise HTTPException(status_code=400, detail="This unsubscribe link is not valid.")
    tenant_id, address = who
    if await onboarding.opt_out(session, tenant_id, address):
        from app.audit.log import write_audit_log
        await write_audit_log(session, tenant_id, None, "onboarding_email_unsubscribed", "first-week e-mails stopped")
    await session.commit()
    return _page("Unsubscribed", f"<p>{html.escape(address)} will get no more first-week e-mails.</p>")


# --- Readiness C13: the referral settings for the operator (SUPER_ADMIN + two-factor) -------------------------------------

admin_router = APIRouter(prefix="/api/admin/growth", tags=["admin"], dependencies=[Depends(require_role()), Depends(require_mfa_session)])


@admin_router.get("/referral-settings")
async def admin_referral_settings(session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """The reward wording and how many accounts came through an invite link (counts only)."""
    return {**(await referral.get_settings(session)).model_dump(), "totals": await referral.totals(session)}


@admin_router.put("/referral-settings")
async def admin_save_referral_settings(body: referral.ReferralSettings, admin: User = Depends(require_role()),
                                       session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    saved = await referral.save_settings(session, admin, body)
    return {**saved.model_dump(), "totals": await referral.totals(session)}
