"""GS-1: the Google sign-in routes (the protocol is in app/auth/google.py).

Every outcome of the callback is a redirect back to the app, never a page of JSON: success sets the same refresh cookie
as the password login (`_complete_login`: login event with reason `google`, new-device alert, audit row, session); an
account with two-factor authentication on gets the same 5-minute challenge token as the password login, in the URL
fragment (never sent to a server, removed from the address bar by the page at once); a refusal carries one short code
(`?google_error=<code>`) that the sign-in page says in plain words. The return address is always FRONTEND_URL, never a
value from the request (no open redirect).

Accounts: Google sign-in never creates an account while `self_signup` is off - it signs in the existing, active user
whose e-mail equals the Google account's verified e-mail. With `self_signup` on, an unknown e-mail gets a new
organisation under the register form's rules; when those rules need something only the form can collect (a beta
invite code, the legal documents' acceptance) the person is sent to the form instead.
"""
import secrets
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.bootstrap import is_configured_super_admin
from app.audit.log import write_audit_log
from app.auth import google, mfa, verification
from app.auth.lockout import record_login_event, refusal
from app.auth.routes import _complete_login, _set_refresh_cookie, new_owner_account
from app.auth.security import hash_password_async
from app.auth.sessions import start_session
from app.core import config
from app.core.config import ENVIRONMENT, HARDENED_ENVIRONMENTS
from app.core.rate_limit import rate_limit
from app.db.models import User
from app.db.session import get_session, release_connection
from app.legal import enabled as legal_enabled
from app.platform.controls import flag_enabled

router = APIRouter(prefix="/api/auth", tags=["auth"])

google_rate_limit = rate_limit("auth_google", limit=20, window_seconds=60)

NO_ACCOUNT_MESSAGE = "No account uses this Google e-mail. Ask your organisation owner for an invite."


@router.get("/providers")
async def providers() -> dict:
    """Which extra sign-in buttons the sign-in page shows. google is true only when both credentials are set."""
    return {"google": google.configured()}


def _require_configured() -> None:
    if not google.configured():
        raise HTTPException(status_code=404, detail="Not Found")


def _forget_flow(response: RedirectResponse) -> RedirectResponse:
    response.delete_cookie(google.FLOW_COOKIE, path=google.FLOW_COOKIE_PATH, httponly=True, samesite="lax",
                           secure=ENVIRONMENT in HARDENED_ENVIRONMENTS)
    return response


def _app_url(request: Request, path: str, params: dict) -> str:
    """FRONTEND_URL (or this server's own origin when it is relative) + a fixed app path - never a request value."""
    base = config.FRONTEND_URL if config.FRONTEND_URL.startswith("http") else str(request.base_url)
    return f"{base.rstrip('/')}{path}?{urlencode(params)}"


def _back(request: Request, *, error: Optional[str] = None, outcome: Optional[str] = None, fragment: str = "") -> RedirectResponse:
    """Signed in: the app's front page. A refusal or the authenticator step: the sign-in page (/account)."""
    if error:
        url = _app_url(request, "/account", {"google_error": error})
    else:
        url = _app_url(request, "/account" if outcome == "mfa" else "/", {"google": outcome or "ok"})
    return _forget_flow(RedirectResponse(url + fragment, status_code=302))


@router.get("/google/start", dependencies=[Depends(google_rate_limit)])
async def google_start(request: Request) -> RedirectResponse:
    _require_configured()
    flow = google.new_flow()
    response = RedirectResponse(google.authorization_url(flow, google.redirect_uri(request)), status_code=302)
    # SameSite=Lax, not Strict: the callback is a top-level navigation coming from Google's site, and a Strict cookie
    # would not be sent with it. The cookie holds only this one attempt's state/nonce/verifier, signed and short-lived.
    response.set_cookie(google.FLOW_COOKIE, google.encode_flow(flow), max_age=google.FLOW_TTL_SECONDS, httponly=True,
                        samesite="lax", secure=ENVIRONMENT in HARDENED_ENVIRONMENTS, path=google.FLOW_COOKIE_PATH)
    return response


@router.get("/google/callback", dependencies=[Depends(google_rate_limit)])
async def google_callback(request: Request, session: AsyncSession = Depends(get_session)) -> RedirectResponse:
    _require_configured()
    params = request.query_params
    flow = google.decode_flow(request.cookies.get(google.FLOW_COOKIE))
    if params.get("error"):
        return _back(request, error="cancelled" if params.get("error") == "access_denied" else "failed")
    if not google.state_matches(flow, params.get("state")):
        return _back(request, error="expired")      # no cookie, an old one, or a state from another browser (CSRF)
    code = params.get("code")
    if not code or flow is None:
        return _back(request, error="failed")
    try:
        identity = await google.identity_for_code(code[:2048], flow, google.redirect_uri(request))
    except google.GoogleSignInError as exc:
        return _back(request, error=exc.code)
    email = identity.email

    refused = await refusal(session, email, request)
    if refused:
        await record_login_event(session, email=email, user=None, success=False,
                                 reason="locked" if refused.status_code == 423 else "delayed", request=request)
        await session.commit()
        return _back(request, error="locked" if refused.status_code == 423 else "wait")

    user = await session.scalar(select(User).where(func.lower(User.email) == email))
    if user is None:
        if not await flag_enabled(session, "self_signup"):
            await record_login_event(session, email=email, user=None, success=False, reason="google_no_account", request=request)
            await session.commit()
            return _back(request, error="no_account")
        return await _sign_up(session, request, email)
    if not user.is_active:
        await record_login_event(session, email=email, user=user, success=False, reason="inactive", request=request)
        await session.commit()
        return _back(request, error="inactive")

    if user.mfa_enabled:
        # Google proved the e-mail; the session starts only once the authenticator code checks out (/auth/mfa/verify),
        # exactly as after a right password.
        await write_audit_log(session, user.tenant_id, user.id, "user_login_mfa_challenge", "google")
        await session.commit()
        return _back(request, outcome="mfa", fragment=f"#google_mfa={mfa.create_mfa_token(user.id)}")

    response = _back(request, outcome="ok")
    await _complete_login(session, user, request, "google", response)
    return response


async def _sign_up(session: AsyncSession, request: Request, email: str) -> RedirectResponse:
    """`self_signup` is on and no account uses this e-mail: the register form's rules. What only the form can collect
    (a beta invite code while `beta_cohort_gate` is on, the legal acceptance while `legal_pages` is on) sends the person
    to the form; otherwise a new organisation with this user as OWNER, the e-mail verified by Google."""
    beta_gate = not is_configured_super_admin(email) and await flag_enabled(session, "beta_cohort_gate")
    if beta_gate or await legal_enabled(session, None):
        return _back(request, error="signup_form")
    # No password was chosen: an unguessable one nobody knows. "Forgot password?" sets a real one later.
    await release_connection(session)                       # LD-7: reads only so far; no pool connection held during bcrypt
    hashed_password = await hash_password_async(secrets.token_urlsafe(32))
    user = await new_owner_account(session, email, hashed_password, source="google")
    verification.mark_verified(user)
    await record_login_event(session, email=email, user=user, success=True, reason="google", request=request)
    issued = await start_session(session, user, request)
    await session.commit()
    response = _back(request, outcome="new")
    _set_refresh_cookie(response, issued.refresh_token)
    return response
