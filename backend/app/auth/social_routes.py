"""M73 SL-1..SL-4: the sign-in provider routes, the same for every provider in the registry (app/auth/social.py).

Per provider (`<slug>` = google, facebook):
- GET  /api/auth/<slug>/start?intent=signin|signup|invite[&invite=<token>]  -> the provider (flow cookie set)
- POST /api/auth/<slug>/link   (signed in)  -> {"url"}: Settings › Account "Link"; the flow cookie names the account
- GET  /api/auth/<slug>/callback            -> back to the app (always a redirect, never a page of JSON)
Shared:
- GET  /api/auth/providers                  -> which buttons the sign-in page shows (+ the platform `ui_v2` layout)
- POST /api/auth/social/pending             -> what a pending step needs (provider, e-mail, password / code / terms)
- POST /api/auth/social/link/confirm        -> a pending link confirmed with the password (+ two-factor code)
- POST /api/auth/social/complete            -> a pending sign-up finished (terms, beta code, an e-mail Facebook did not give)
- GET  /api/auth/identities, DELETE /api/auth/identities/<provider>  (signed in) -> Settings › Account

Every callback outcome is a redirect to FRONTEND_URL (never a request value: no open redirect):
  /?social=ok|new&provider=<id>                       signed in (the refresh cookie is set, as after the password login)
  /account?social=mfa&provider=<id>#social_mfa=<t>    the account has two-factor on: the same 5-minute challenge token
  /account?social=link&provider=<id>#social_pending=<t>    an account uses this e-mail: confirm with its password
  /account?social=complete&provider=<id>#social_pending=<t> a new account needs the terms / beta code / an e-mail
  /account?social=linked&provider=<id>                Settings › Account: the provider is linked
  /account?social_error=<code>&provider=<id>          refused, said in plain words by the page
Tokens ride in the fragment (never sent to a server; the page removes them from the address bar at once).

Rate limits: start as GS-1 (20 a minute per network); the callback, the link confirmation and the sign-up completion
share the /login limit (`auth_login`, 10 a minute per network) and the same per-account lockout.
"""
import logging
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, Field, TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.bootstrap import is_configured_super_admin
from app.audit.log import write_audit_log
from app.auth import mfa, social
from app.auth.dependencies import get_current_user
from app.auth.lockout import record_login_event, refusal
from app.auth.routes import (
    TokenResponse,
    _complete_login,
    _invite_by_token,
    _invite_problem,
    _legal_versions,
    _set_refresh_cookie,
    _token_response,
    join_by_invite,
    login_rate_limit,
    self_signup_account,
)
from app.auth.security import password_usable, verify_password_async
from app.core import config
from app.core.config import ENVIRONMENT, HARDENED_ENVIRONMENTS
from app.core.rate_limit import rate_limit
from app.db.models import User
from app.db.session import get_session, release_connection
from app.legal import enabled as legal_enabled
from app.legal.acceptance import LegalAcceptance
from app.platform.controls import flag_enabled

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# GS-1's start limit (one sign-in is two requests: start and callback). The callback uses the /login limit.
social_start_rate_limit = rate_limit("auth_google", limit=20, window_seconds=60,
                                     message="Too many sign-in attempts from this network. Please wait a minute and try again.")


# --- which buttons --------------------------------------------------------------------------------------------------

@router.get("/providers")
async def providers(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """The sign-in buttons: `providers` lists the live ones (credentials set) in order; `google` / `facebook` are the
    GS-1 booleans. `ui_v2` is the platform-wide layout switch for the signed-out page (the per-organisation flags are
    read only after sign-in). Nothing secret: no client id, no secret."""
    listed = social.public_providers()
    ids = {p["id"] for p in listed}
    return {"google": "google" in ids, "facebook": "facebook" in ids, "providers": listed,
            "ui_v2": await flag_enabled(session, "ui_v2")}


# --- helpers ----------------------------------------------------------------------------------------------------------

def _provider_or_404(slug: str) -> social.Provider:
    provider = social.by_slug(slug)
    if provider is None or not social.is_live(provider):
        raise HTTPException(status_code=404, detail="Not Found")
    return provider


def _app_url(request: Request, path: str, params: dict[str, str]) -> str:
    """FRONTEND_URL (or this server's own origin when it is relative) + a fixed app path - never a request value."""
    base = config.FRONTEND_URL if config.FRONTEND_URL.startswith("http") else str(request.base_url)
    return f"{base.rstrip('/')}{path}?{urlencode(params)}"


def _forget_flow(provider: social.Provider, response: Response) -> Response:
    response.delete_cookie(provider.flow_cookie, path=provider.flow_cookie_path, httponly=True, samesite="lax",
                           secure=ENVIRONMENT in HARDENED_ENVIRONMENTS)
    return response


def _back(provider: social.Provider, request: Request, *, error: str | None = None, outcome: str = "ok",
          fragment: str = "") -> RedirectResponse:
    if error:
        url = _app_url(request, "/account", {"social_error": error, "provider": provider.id})
    else:
        url = _app_url(request, "/" if outcome in ("ok", "new") else "/account", {"social": outcome, "provider": provider.id})
    response = RedirectResponse(url + fragment, status_code=302)
    _forget_flow(provider, response)
    return response


def _set_flow_cookie(provider: social.Provider, response: Response, social_flow: social.SocialFlow) -> None:
    # SameSite=Lax, not Strict: the callback is a top-level navigation coming from the provider's site, and a Strict
    # cookie would not be sent with it. The cookie holds only this one attempt's state/nonce/verifier, signed, 10 min.
    response.set_cookie(provider.flow_cookie, social.encode_flow(provider, social_flow), max_age=social.FLOW_TTL_SECONDS,
                        httponly=True, samesite="lax", secure=ENVIRONMENT in HARDENED_ENVIRONMENTS,
                        path=provider.flow_cookie_path)


async def _user_by_email(session: AsyncSession, email: str) -> User | None:
    return await session.scalar(select(User).where(func.lower(User.email) == email.lower()))


async def _run_hook(provider: social.Provider, session: AsyncSession, user: User, identity: social.ProviderIdentity,
                    request: Request) -> None:
    """The generic after-sign-in hook (M74 hands a broker session over here). Never breaks a sign-in."""
    if provider.on_signed_in is None:
        return
    try:
        await provider.on_signed_in(session, user, identity, request)
    except Exception:  # noqa: BLE001 - the person is signed in; the hook's own failure is logged without any token
        logger.warning("sign-in hook of provider %s failed for user #%s", provider.id, user.id)


# --- start ------------------------------------------------------------------------------------------------------------

async def _start(slug: str, request: Request) -> RedirectResponse:
    provider = _provider_or_404(slug)
    intent = request.query_params.get("intent") or "signin"
    if intent not in ("signin", "signup", "invite"):
        intent = "signin"                                   # "link" only through the signed-in POST below
    invite = ((request.query_params.get("invite") or "")[:256] or None) if intent == "invite" else None
    social_flow = social.new_flow(intent, invite=invite)
    response = RedirectResponse(provider.authorization_url(social_flow.flow, provider.redirect_uri(request)), status_code=302)
    _set_flow_cookie(provider, response, social_flow)
    return response


class LinkStartRequest(BaseModel):
    password: str = Field(default="", max_length=256)
    code: str | None = Field(default=None, max_length=16)


async def _link_start(slug: str, request: Request, response: Response, user: User, body: LinkStartRequest,
                      session: AsyncSession) -> dict[str, str]:
    """Settings › Account "Link": the account's password (and its two-factor code when it is on) re-confirms the person
    (review 3: a stolen session alone cannot add a way in); then the flow cookie names this account (a link can never be
    started from a URL someone else made) and the page goes to the provider's address."""
    provider = _provider_or_404(slug)
    email = user.email.lower()
    refused = await refusal(session, email, request)
    if refused:
        raise HTTPException(status_code=refused.status_code, detail=refused.reason,
                            headers={"Retry-After": str(refused.retry_after)} if refused.retry_after else None)
    if not password_usable(user.hashed_password):
        raise HTTPException(status_code=409, detail="Set a password first (\"Forgot password?\" on the sign-in page), then "
                                                    "link another way to sign in.")
    await release_connection(session)                       # LD-7: no pool connection held during bcrypt
    if not await verify_password_async(body.password, user.hashed_password):
        await record_login_event(session, email=email, user=user, success=False, reason="bad_password", request=request)
        await session.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Wrong password.")
    if user.mfa_enabled:
        ok, _ = await mfa.verify_code(session, user, (body.code or "").replace(" ", ""))
        if not ok:
            await record_login_event(session, email=email, user=user, success=False, reason="mfa_failed", request=request)
            await session.commit()
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authenticator code")
    social_flow = social.new_flow("link", link_user_id=user.id)
    _set_flow_cookie(provider, response, social_flow)
    return {"url": provider.authorization_url(social_flow.flow, provider.redirect_uri(request))}


# --- callback ---------------------------------------------------------------------------------------------------------

async def _callback(slug: str, request: Request, session: AsyncSession) -> RedirectResponse:
    provider = _provider_or_404(slug)
    params = request.query_params
    social_flow = social.decode_flow(provider, request.cookies.get(provider.flow_cookie))
    try:
        code = provider.read_code(params)                   # `?error=` (or a broker's own refusal) -> a refusal code
    except social.SignInError as exc:
        return _back(provider, request, error=exc.code)
    if not social.state_matches(social_flow, params.get("state")):
        return _back(provider, request, error="expired")    # no cookie, an old one, or a state from another browser (CSRF)
    if not code or social_flow is None:
        return _back(provider, request, error="failed")
    if not await social.burn_once("state", social_flow.flow.state, social.FLOW_TTL_SECONDS + 60):
        return _back(provider, request, error="expired")    # review nit 4: a state is spent once, server-side
    try:
        identity = await provider.identity_for_code(code[:2048], social_flow.flow, provider.redirect_uri(request))
    except social.SignInError as exc:
        return _back(provider, request, error=exc.code)
    if identity.email and not identity.email_verified:
        return _back(provider, request, error="unverified")  # an unverified address never matches an account (takeover)

    if social_flow.intent == "link":
        return await _link_signed_in(provider, request, session, social_flow, identity)

    linked = await social.find_identity(session, provider.id, identity.subject)
    if linked is not None:
        user = await session.get(User, linked.user_id)
        if user is None:
            return _back(provider, request, error="failed")
        return await _sign_in(provider, request, session, user, identity)

    email = identity.email
    if email:
        refused = await _refused(provider, request, session, email)
        if refused:
            return refused
        existing = await _user_by_email(session, email)
        if existing is not None:
            if not existing.is_active:
                await record_login_event(session, email=email, user=existing, success=False, reason="inactive", request=request)
                await session.commit()
                return _back(provider, request, error="inactive")
            return await _pending_link(provider, request, session, existing, identity)
    return await _new_person(provider, request, session, social_flow, identity)


async def _refused(provider: social.Provider, request: Request, session: AsyncSession, email: str) -> RedirectResponse | None:
    refused = await refusal(session, email, request)
    if not refused:
        return None
    await record_login_event(session, email=email, user=None, success=False,
                             reason="locked" if refused.status_code == 423 else "delayed", request=request)
    await session.commit()
    return _back(provider, request, error="locked" if refused.status_code == 423 else "wait")


async def _sign_in(provider: social.Provider, request: Request, session: AsyncSession, user: User,
                   identity: social.ProviderIdentity) -> RedirectResponse:
    refused = await _refused(provider, request, session, user.email.lower())
    if refused:
        return refused
    if not user.is_active:
        await record_login_event(session, email=user.email, user=user, success=False, reason="inactive", request=request)
        await session.commit()
        return _back(provider, request, error="inactive")
    if user.mfa_enabled:
        # The provider proved the account; the session starts only once the authenticator code checks out
        # (/auth/mfa/verify), exactly as after a right password.
        await write_audit_log(session, user.tenant_id, user.id, "user_login_mfa_challenge", provider.id)
        await session.commit()
        return _back(provider, request, outcome="mfa", fragment=f"#social_mfa={mfa.create_mfa_token(user.id)}")
    response = _back(provider, request, outcome="ok")
    await _complete_login(session, user, request, provider.id, response)   # audit `user_login`, detail = the provider
    await _run_hook(provider, session, user, identity, request)
    return response


async def _link_signed_in(provider: social.Provider, request: Request, session: AsyncSession,
                          social_flow: social.SocialFlow, identity: social.ProviderIdentity) -> RedirectResponse:
    user = await session.get(User, social_flow.link_user_id) if social_flow.link_user_id is not None else None
    if user is None or not user.is_active:
        return _back(provider, request, error="failed")
    linked = await social.find_identity(session, provider.id, identity.subject)
    if linked is not None:
        if linked.user_id == user.id:
            return _back(provider, request, outcome="linked")
        return _back(provider, request, error="linked_elsewhere")
    if any(i.provider == provider.id for i in await social.identities_of(session, user.id)):
        return _back(provider, request, error="already_linked")
    await social.link_identity(session, user, identity)
    await session.commit()
    await _run_hook(provider, session, user, identity, request)     # M74: linking a broker also connects it today
    return _back(provider, request, outcome="linked")


async def _pending_link(provider: social.Provider, request: Request, session: AsyncSession, user: User,
                        identity: social.ProviderIdentity) -> RedirectResponse:
    """SL-2: an account uses this e-mail but has not linked this provider - never a silent sign-in. The person confirms
    with the account's password (and its two-factor code) at /auth/social/link/confirm."""
    if any(i.provider == provider.id for i in await social.identities_of(session, user.id)):
        return _back(provider, request, error="already_linked")      # this account linked another account of the provider
    token = social.encode_pending(social.Pending(kind="link", provider=provider.id, subject=identity.subject,
                                                 email=identity.email, email_verified=identity.email_verified,
                                                 user_id=user.id))
    await write_audit_log(session, user.tenant_id, user.id, "social_link_pending", provider.id)
    await session.commit()
    return _back(provider, request, outcome="link", fragment=f"#social_pending={token}")


def _invite_email_matches(email: str | None, verified: bool, invited: str) -> bool:
    """Review 2: Join team with a provider only when the provider verified the invited address itself."""
    return bool(email and verified and email.strip().lower() == invited.strip().lower())


async def _signup_form_needed(session: AsyncSession, email: str | None, tenant_id: int | None = None,
                              invite: bool = False) -> bool:
    if email is None:
        return True
    if await legal_enabled(session, tenant_id):
        return True
    return not invite and not is_configured_super_admin(email) and await flag_enabled(session, "beta_cohort_gate")


async def _new_person(provider: social.Provider, request: Request, session: AsyncSession, social_flow: social.SocialFlow,
                      identity: social.ProviderIdentity) -> RedirectResponse:
    """SL-3: nobody uses this provider account or its e-mail. A team invite, or a self sign-up while `self_signup` is
    on - through the very same code path as the forms. What only a form can collect (the terms, a beta code, an e-mail
    the provider did not give) is asked on a pending "finish" step first."""
    if social_flow.intent == "invite" and social_flow.invite:
        record = await _invite_by_token(session, social_flow.invite)
        if record is None or _invite_problem(record):
            return _back(provider, request, error="invite_invalid")
        if not _invite_email_matches(identity.email, identity.email_verified, record.email):
            return _back(provider, request, error="invite_email_mismatch")
        if await _user_by_email(session, record.email) is not None:
            return _back(provider, request, error="email_taken")
        if await _signup_form_needed(session, record.email, record.tenant_id, invite=True):
            return _back_pending_signup(provider, request, identity, intent="invite", invite=social_flow.invite)
        user, issued = await join_by_invite(session, request, record, password=None, legal_entries=None, identity=identity)
        await _run_hook(provider, session, user, identity, request)
        return _signed_up(provider, request, issued)

    email = identity.email
    if not await flag_enabled(session, "self_signup"):
        if email:
            await record_login_event(session, email=email, user=None, success=False, reason=f"{provider.id}_no_account",
                                     request=request)
            await session.commit()
        return _back(provider, request, error="no_account")
    if await _signup_form_needed(session, email):
        return _back_pending_signup(provider, request, identity)
    assert email is not None
    user, issued = await self_signup_account(session, request, email=email, password=None, legal_entries=None, invite_code=None,
                                          source=provider.id, email_verified=True, identity=identity)
    await _run_hook(provider, session, user, identity, request)      # M74: a new account's broker is connected at once
    return _signed_up(provider, request, issued)


def _back_pending_signup(provider: social.Provider, request: Request, identity: social.ProviderIdentity, *,
                         intent: str = "signup", invite: str | None = None) -> RedirectResponse:
    token = social.encode_pending(social.Pending(kind="signup", provider=provider.id, subject=identity.subject,
                                                 email=identity.email, email_verified=identity.email_verified,
                                                 intent=intent, invite=invite))
    return _back(provider, request, outcome="complete", fragment=f"#social_pending={token}")


def _signed_up(provider: social.Provider, request: Request, issued: Any) -> RedirectResponse:
    response = _back(provider, request, outcome="new")
    _set_refresh_cookie(response, issued.refresh_token)
    return response


# --- the pending steps ------------------------------------------------------------------------------------------------

class PendingRequest(BaseModel):
    token: str = Field(min_length=1, max_length=4096)


class LinkConfirmRequest(BaseModel):
    token: str = Field(min_length=1, max_length=4096)
    password: str = Field(min_length=1, max_length=256)
    code: str | None = Field(default=None, max_length=16)


class CompleteSignupRequest(BaseModel):
    token: str = Field(min_length=1, max_length=4096)
    email: EmailStr | None = None
    invite_code: str | None = Field(default=None, max_length=64)
    legal_acceptance: LegalAcceptance | None = Field(default=None, max_length=3)


EXPIRED = "This step took too long. Please start again."


@router.post("/social/pending", dependencies=[Depends(login_rate_limit)])
async def pending_info(request: PendingRequest, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """What the pending step shows: the provider's name, the e-mail (the person proved it at the provider), and which
    fields it asks for. 400 when the step is invalid or expired."""
    pending = social.decode_pending(request.token)
    if pending is None:
        raise HTTPException(status_code=400, detail=EXPIRED)
    provider = social.get(pending.provider)
    assert provider is not None
    body: dict[str, Any] = {"kind": pending.kind, "provider": provider.id, "label": provider.label, "email": pending.email}
    if pending.kind == "link":
        user = await session.get(User, pending.user_id) if pending.user_id is not None else None
        if user is None or not user.is_active:
            raise HTTPException(status_code=400, detail=EXPIRED)
        body.update(mfa=bool(user.mfa_enabled), has_password=password_usable(user.hashed_password))
    else:
        tenant_id = None
        if pending.intent == "invite" and pending.invite:
            record = await _invite_by_token(session, pending.invite)
            if record is None or _invite_problem(record):
                raise HTTPException(status_code=400, detail="This invitation is no longer valid. Ask the owner for a new one.")
            tenant_id = record.tenant_id
            body["email"] = record.email
        body.update(intent=pending.intent, needs_email=pending.email is None and pending.intent != "invite",
                    legal=await legal_enabled(session, tenant_id),
                    beta=pending.intent != "invite" and await flag_enabled(session, "beta_cohort_gate")
                    and not (pending.email and is_configured_super_admin(pending.email)))
    return body


@router.post("/social/link/confirm", response_model=TokenResponse, dependencies=[Depends(login_rate_limit)])
async def confirm_link(request: LinkConfirmRequest, http_request: Request, response: Response,
                       session: AsyncSession = Depends(get_session)) -> TokenResponse:
    """SL-2: the account's own password (and its two-factor code when it has one) confirms the link; then the person is
    signed in. A wrong password counts toward the account's lockout like a wrong password at /login."""
    pending = social.decode_pending(request.token, kind="link")
    user = await session.get(User, pending.user_id) if pending and pending.user_id is not None else None
    if pending is None or user is None or not user.is_active or await social.pending_used(pending):
        raise HTTPException(status_code=400, detail=EXPIRED)
    provider = social.get(pending.provider)
    assert provider is not None
    email = user.email.lower()
    refused = await refusal(session, email, http_request)
    if refused:
        raise HTTPException(status_code=refused.status_code, detail=refused.reason,
                            headers={"Retry-After": str(refused.retry_after)} if refused.retry_after else None)
    if not password_usable(user.hashed_password):
        raise HTTPException(status_code=409, detail="This account has no password yet. Use \"Forgot password?\" to set "
                                                    "one, then link again.")
    await release_connection(session)                       # LD-7: no pool connection held during bcrypt
    if not await verify_password_async(request.password, user.hashed_password):
        await record_login_event(session, email=email, user=user, success=False, reason="bad_password", request=http_request)
        await session.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Wrong password.")
    method = provider.id
    if user.mfa_enabled:
        ok, how = await mfa.verify_code(session, user, (request.code or "").replace(" ", ""))
        if not ok:
            await record_login_event(session, email=email, user=user, success=False, reason="mfa_failed", request=http_request)
            await write_audit_log(session, user.tenant_id, user.id, "mfa_login_failed", provider.id)
            await session.commit()
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authenticator code")
        method = f"mfa:{how}"
    if await social.find_identity(session, provider.id, pending.subject) is None:
        if any(i.provider == provider.id for i in await social.identities_of(session, user.id)):
            raise HTTPException(status_code=409, detail=f"This account is already linked to another {provider.label} account.")
        await social.link_identity(session, user, social.identity_of(pending))
    if not await social.burn(pending):
        raise HTTPException(status_code=400, detail=EXPIRED)               # spent by a parallel request
    return await _complete_login(session, user, http_request, method, response)


@router.post("/social/complete", status_code=status.HTTP_201_CREATED, dependencies=[Depends(login_rate_limit)])
async def complete_signup(request: CompleteSignupRequest, http_request: Request, response: Response,
                          session: AsyncSession = Depends(get_session)) -> Any:
    """SL-3: the pending sign-up finished through the same code path as the register / invite forms.

    Review blocker 1: an e-mail typed here (Facebook gave none) is NOT trusted until its owner opens the link we mail to
    it - nothing is created and nobody is signed in now (202 `check_email`); /auth/social/verify makes the account and
    links the provider once the link is opened. An address an account already uses answers 409 `link_required` with a
    link step (that account's password confirms)."""
    pending = social.decode_pending(request.token, kind="signup")
    if pending is None or await social.pending_used(pending):
        raise HTTPException(status_code=400, detail=EXPIRED)
    provider = social.get(pending.provider)
    assert provider is not None
    if await social.find_identity(session, provider.id, pending.subject) is not None:
        raise HTTPException(status_code=409, detail="This account is already set up. Sign in instead.")
    if pending.intent == "invite" and pending.invite:
        record = await _invite_by_token(session, pending.invite)
        if record is None or _invite_problem(record):
            raise HTTPException(status_code=status.HTTP_410_GONE, detail="This invitation is no longer valid. Ask the owner for a new one.")
        if not _invite_email_matches(pending.email, pending.email_verified, record.email):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={
                "code": "invite_email_mismatch", "message": f"This invitation is for another email. Sign in to {provider.label} "
                                                            "with the invited email, or use the invitation's password form."})
        if await _user_by_email(session, record.email) is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
        _, issued = await join_by_invite(session, http_request, record, password=None,
                                         legal_entries=request.legal_acceptance, identity=social.identity_of(pending))
        await social.burn(pending)
        return _token_response(issued, response)

    if not await flag_enabled(session, "self_signup"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Self sign-up is disabled - ask an existing organisation for an invite")
    email = pending.email or (str(request.email).lower() if request.email else None)
    if not email:
        raise HTTPException(status_code=400, detail={"code": "email_required", "message": "Enter your email address."})
    existing = await _user_by_email(session, email)
    if existing is not None:
        if pending.email is None and existing.is_active:
            # SL-2: the typed address is an account's - only its password can link this provider account to it.
            link = social.encode_pending(social.Pending(kind="link", provider=provider.id, subject=pending.subject,
                                                        email=email, email_verified=False, user_id=existing.id))
            raise HTTPException(status_code=409, detail={"code": "link_required", "token": link,
                                                         "message": "An account already uses this email. Confirm with its password to link it."})
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    if pending.email is None:
        # Checked now (a stale page reloads the documents), applied when the link is opened.
        await _legal_versions(session, request.legal_acceptance)
        verify = social.Pending(kind="verify", provider=provider.id, subject=pending.subject, email=email, email_verified=False,
                                extra={"legal": jsonable_encoder(request.legal_acceptance) if request.legal_acceptance else None,
                                       "invite_code": request.invite_code})
        sent = await _mail_confirmation(http_request, email, provider.label, social.encode_pending(verify))
        await social.burn(pending)
        response.status_code = status.HTTP_202_ACCEPTED
        return {"status": "check_email", "email": email, "sent": sent}

    identity = social.ProviderIdentity(provider=provider.id, subject=pending.subject, email=pending.email,
                                       email_verified=pending.email_verified)
    _, issued = await self_signup_account(session, http_request, email=email, password=None,
                                          legal_entries=request.legal_acceptance, invite_code=request.invite_code,
                                          source=provider.id, email_verified=pending.email_verified, identity=identity)
    await social.burn(pending)
    return _token_response(issued, response)


async def _mail_confirmation(request: Request, email: str, label: str, token: str) -> bool:
    """The confirmation link for an e-mail a provider did not give (review blocker 1). The link is never logged."""
    from app.notifications import mailer
    base = config.FRONTEND_URL if config.FRONTEND_URL.startswith("http") else str(request.base_url)
    link = f"{base.rstrip('/')}/account?{urlencode({'social_verify': token})}"
    sent = await mailer.send(
        email, "Confirm your email for AiAlgoDesk",
        f"Someone (hopefully you) signed up for AiAlgoDesk with {label} and gave this email address.\n\n"
        f"Open this link within 24 hours to confirm it and finish creating your account:\n{link}\n\n"
        "If this was not you, ignore this email - no account is created without this link.")
    if not sent:
        logger.warning("sign-up confirmation mail not sent (no platform mailer) - the person cannot finish this sign-up")
    return bool(sent)


class VerifyRequest(BaseModel):
    token: str = Field(min_length=1, max_length=8192)


@router.post("/social/verify", response_model=TokenResponse, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(login_rate_limit)])
async def verify_signup(request: VerifyRequest, http_request: Request, response: Response,
                        session: AsyncSession = Depends(get_session)) -> TokenResponse:
    """Review blocker 1: the mailed link was opened - the address is proven. Only now is the account made (the same
    sign-up path, the terms and beta code given on the finish step) and the provider linked; then the person is in."""
    pending = social.decode_pending(request.token, kind="verify")
    if pending is None or not pending.email or await social.pending_used(pending):
        raise HTTPException(status_code=400, detail="This link is no longer valid. Sign up again.")
    provider = social.get(pending.provider)
    assert provider is not None
    if not await flag_enabled(session, "self_signup"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Self sign-up is disabled - ask an existing organisation for an invite")
    if await social.find_identity(session, provider.id, pending.subject) is not None:
        raise HTTPException(status_code=409, detail="This account is already set up. Sign in instead.")
    if await _user_by_email(session, pending.email) is not None:
        raise HTTPException(status_code=409, detail="An account already uses this email. Sign in, then link "
                                                    f"{provider.label} in Settings › Account.")
    legal = pending.extra.get("legal")
    legal_entries = TypeAdapter(LegalAcceptance).validate_python(legal) if isinstance(legal, dict) else None
    invite_code = pending.extra.get("invite_code") if isinstance(pending.extra.get("invite_code"), str) else None
    identity = social.ProviderIdentity(provider=provider.id, subject=pending.subject, email=pending.email, email_verified=True)
    if not await social.burn(pending):
        raise HTTPException(status_code=400, detail="This link is no longer valid. Sign up again.")
    _, issued = await self_signup_account(session, http_request, email=pending.email, password=None,
                                          legal_entries=legal_entries, invite_code=invite_code, source=provider.id,
                                          email_verified=True, identity=identity)
    return _token_response(issued, response)


# --- Settings › Account ---------------------------------------------------------------------------------------------

@router.get("/identities")
async def list_identities(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """The ways this account signs in: its password (when one was chosen) and each linked provider; `available` = the
    live providers it can still link."""
    rows = await social.identities_of(session, user.id)
    linked = {r.provider for r in rows}
    return {
        "has_password": password_usable(user.hashed_password),
        "identities": [{"provider": r.provider, "label": (social.get(r.provider).label if social.get(r.provider) else r.provider),
                        "email": r.email_at_link, "linked_at": r.created_at.isoformat() if r.created_at else None} for r in rows],
        "available": [{"id": p.id, "slug": p.slug, "label": p.label, "kind": p.kind} for p in social.live() if p.id not in linked],
    }


@router.delete("/identities/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unlink_identity(provider_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> None:
    """SL-2: unlink one provider - refused (409) when it is the last way to sign in (no password and no other link)."""
    rows = await social.identities_of(session, user.id)
    target = next((r for r in rows if r.provider == provider_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="This sign-in way is not linked")
    if not password_usable(user.hashed_password) and len(rows) <= 1:
        raise HTTPException(status_code=409, detail="This is your only way to sign in. Set a password first (\"Forgot "
                                                    "password?\" on the sign-in page) or link another way, then unlink.")
    await session.delete(target)
    await write_audit_log(session, user.tenant_id, user.id, "social_unlink", provider_id)
    label = social.get(provider_id).label if social.get(provider_id) else provider_id
    await social.security_notice(session, user, f"{label} was unlinked from your account",
                                 "If this was not you, change your password and check Settings › Account.")
    await session.commit()


# --- the per-provider routes (one set per registry entry) -------------------------------------------------------------

def _add_provider_routes(provider: social.Provider) -> None:
    slug = provider.slug

    async def start(request: Request) -> RedirectResponse:
        return await _start(slug, request)

    async def link(request: Request, response: Response, body: LinkStartRequest, user: User = Depends(get_current_user),
                   session: AsyncSession = Depends(get_session)) -> dict[str, str]:
        return await _link_start(slug, request, response, user, body, session)

    async def callback(request: Request, session: AsyncSession = Depends(get_session)) -> RedirectResponse:
        return await _callback(slug, request, session)

    start.__name__ = f"{provider.id}_start"
    link.__name__ = f"{provider.id}_link"
    callback.__name__ = f"{provider.id}_callback"
    router.add_api_route(f"/{slug}/start", start, methods=["GET"], dependencies=[Depends(social_start_rate_limit)])
    router.add_api_route(f"/{slug}/link", link, methods=["POST"],
                         dependencies=[Depends(social_start_rate_limit), Depends(login_rate_limit)])
    router.add_api_route(f"/{slug}/callback", callback, methods=["GET"], dependencies=[Depends(login_rate_limit)])


for _provider in list(social.REGISTRY.values()):
    _add_provider_routes(_provider)


def add_provider(provider: social.Provider) -> social.Provider:
    """M74 extension point: register a provider and serve its start / link / callback routes (call before the app
    includes this router, i.e. at import time of the provider's module)."""
    social.register(provider)
    _add_provider_routes(provider)
    return provider
