"""PW6 endpoints.

* `/api/admin/beta/...` (SUPER_ADMIN with an MFA-verified session): create, list and revoke closed-beta invite codes,
  read the cohort size against the cap, move the rollout stage (Z10-e, audited), and read the feedback users sent.
* `POST /api/pwa/events` (no login, rate-limited per IP): one install/update event from the installed app. The body is
  two enums - which event and which platform family - and only increments a Prometheus counter. Nothing is stored:
  no user id, no IP (the rate limiter keeps a short-lived per-IP counter in memory/Redis and nothing else), no text.
* `POST /api/feedback` (logged in): the "Send feedback" dialog. The message the user typed, the app version and the
  screen path it was sent from (query string and fragment removed), and a truncated User-Agent.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.auth.dependencies import get_current_user, require_mfa_session, require_role
from app.beta import invites
from app.core.rate_limit import rate_limit, user_rate_limit
from app.db.models import BetaInviteCodeRecord, FeedbackRecord, User
from app.db.session import get_session
from app.observability.metrics import PWA_EVENTS, WEB_VITAL_CLS, WEB_VITAL_INP, WEB_VITAL_LCP
from app.platform.controls import flag_enabled

admin_router = APIRouter(prefix="/api/admin/beta", tags=["admin"], dependencies=[Depends(require_role()), Depends(require_mfa_session)])
pwa_router = APIRouter(prefix="/api/pwa", tags=["pwa"])
feedback_router = APIRouter(prefix="/api/feedback", tags=["feedback"])

# Module-level so tests can target them through `app.dependency_overrides` (see tests/test_auth_api.py).
pwa_events_rate_limit = rate_limit("pwa_events", limit=30, window_seconds=60)
feedback_rate_limit = user_rate_limit("feedback", limit=10, window_seconds=3600)

PwaEvent = Literal["installed", "launched_standalone", "update_applied", "install_prompt_shown", "install_prompt_accepted",
                   "install_prompt_dismissed"]
PwaPlatform = Literal["android", "ios", "desktop", "other"]

FEEDBACK_MAX_CHARS = 2000
SCREEN_MAX_CHARS = 200
USER_AGENT_MAX_CHARS = 200
_APP_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,39}$")
_SCREEN = re.compile(r"^/[A-Za-z0-9/_.~%:@!$&'()*+,;=-]*$")


def clean_screen(raw: str) -> str:
    """The screen a feedback was sent from, as a path only: the query string and fragment are cut off (reset, invite
    and verification links carry tokens there), then it must start with '/' and fit in 200 characters."""
    path = (raw or "").strip().split("#", 1)[0].split("?", 1)[0]
    if not path.startswith("/"):
        raise ValueError("screen must be a path starting with '/'")
    if len(path) > SCREEN_MAX_CHARS:
        raise ValueError(f"screen must be at most {SCREEN_MAX_CHARS} characters")
    if not _SCREEN.fullmatch(path):
        raise ValueError("screen contains characters a path cannot have")
    return path


# --- admin: invite codes, cohort, feedback --------------------------------------------------------------------------

class InviteCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_uses: int = Field(default=1, ge=1, le=1000)
    expires_in_days: Optional[int] = Field(default=None, ge=1, le=365)
    note: Optional[str] = Field(default=None, max_length=200)


@admin_router.post("/invite-codes", status_code=status.HTTP_201_CREATED)
async def create_invite_code(body: InviteCreateBody, admin: User = Depends(require_role()),
                             session: AsyncSession = Depends(get_session)) -> Dict[str, object]:
    """The plain code is in this response only - it is stored as a hash and cannot be shown again."""
    expires = datetime.now(timezone.utc) + timedelta(days=body.expires_in_days) if body.expires_in_days else None
    row, code = await invites.create_code(session, created_by=admin.id, max_uses=body.max_uses, expires_at=expires, note=body.note)
    await write_audit_log(session, None, admin.id, "beta_invite_created", f"code #{row.id} max_uses={row.max_uses}")
    await session.commit()
    return {**invites.describe(row), "code": code}


@admin_router.get("/invite-codes")
async def list_invite_codes(limit: int = 500, session: AsyncSession = Depends(get_session)) -> List[Dict[str, object]]:
    rows = await session.scalars(select(BetaInviteCodeRecord).order_by(BetaInviteCodeRecord.id.desc()).limit(max(1, min(limit, 1000))))
    now = datetime.now(timezone.utc)
    return [invites.describe(r, now) for r in rows]


@admin_router.post("/invite-codes/{code_id}/revoke")
async def revoke_invite_code(code_id: int, admin: User = Depends(require_role()), session: AsyncSession = Depends(get_session)) -> Dict[str, object]:
    row = await invites.revoke(session, code_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such invite code")
    await write_audit_log(session, None, admin.id, "beta_invite_revoked", f"code #{row.id}")
    await session.commit()
    return invites.describe(row)


@admin_router.get("/cohort")
async def cohort(session: AsyncSession = Depends(get_session)) -> Dict[str, object]:
    """`cap` is the effective cap the gate enforces, min(stage, BETA_COHORT_CAP); `remaining` / `headroom` the places
    left under it."""
    return await invites.cohort_view(session, gate_on=await flag_enabled(session, "beta_cohort_gate"))


class StageBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: int
    reason: Optional[str] = Field(default=None, max_length=200)


@admin_router.put("/cohort/stage")
async def set_cohort_stage(body: StageBody, admin: User = Depends(require_role()), session: AsyncSession = Depends(get_session)) -> Dict[str, object]:
    """Z10-e: move the rollout stage. Up one stage at a time with a reason; down to any lower stage. Audited."""
    try:
        await invites.set_stage(session, admin, body.stage, body.reason)
        await session.commit()
    except invites.StageChangeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="The stage was changed by another request at the same moment. Reload and try again.") from exc
    return await invites.cohort_view(session, gate_on=await flag_enabled(session, "beta_cohort_gate"))


@admin_router.get("/feedback")
async def list_feedback(tenant_id: Optional[int] = None, limit: int = 200, session: AsyncSession = Depends(get_session)) -> List[Dict[str, object]]:
    query = select(FeedbackRecord, User.email).outerjoin(User, User.id == FeedbackRecord.user_id)
    if tenant_id is not None:
        query = query.where(FeedbackRecord.tenant_id == tenant_id)
    rows = (await session.execute(query.order_by(FeedbackRecord.id.desc()).limit(max(1, min(limit, 1000))))).all()
    return [{"id": f.id, "tenant_id": f.tenant_id, "user_id": f.user_id, "email": email, "message": f.message, "app_version": f.app_version,
             "screen": f.screen, "user_agent": f.user_agent, "created_at": f.created_at.isoformat() if f.created_at else None}
            for f, email in rows]


# --- PWA install/update metrics -------------------------------------------------------------------------------------

class PwaEventBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event: PwaEvent
    platform: PwaPlatform = "other"


# AUD-G1.5: one page view's Core Web Vitals (no login, rate-limited per IP). Closed labels only: the page family and
# phone / desktop; values bounded so a forged report cannot skew a bucket beyond its top.
VitalsPage = Literal["today", "charts", "chain", "terminal", "positions", "other"]
VitalsDevice = Literal["phone", "desktop"]
pwa_vitals_rate_limit = rate_limit("pwa_vitals", limit=30, window_seconds=60)


class VitalsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: VitalsPage = "other"
    device: VitalsDevice = "desktop"
    lcp_ms: Optional[float] = Field(default=None, ge=0, le=60_000)
    inp_ms: Optional[float] = Field(default=None, ge=0, le=60_000)
    cls: Optional[float] = Field(default=None, ge=0, le=10)


@pwa_router.post("/vitals", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(pwa_vitals_rate_limit)])
async def web_vitals(body: VitalsBody) -> Response:
    if body.lcp_ms is not None:
        WEB_VITAL_LCP.labels(page=body.page, device=body.device).observe(body.lcp_ms / 1000)
    if body.inp_ms is not None:
        WEB_VITAL_INP.labels(page=body.page, device=body.device).observe(body.inp_ms / 1000)
    if body.cls is not None:
        WEB_VITAL_CLS.labels(page=body.page, device=body.device).observe(body.cls)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@pwa_router.post("/events", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(pwa_events_rate_limit)])
async def pwa_event(body: PwaEventBody) -> Response:
    PWA_EVENTS.labels(event=body.event, platform=body.platform).inc()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Send feedback ----------------------------------------------------------------------------------------------------

class FeedbackBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=FEEDBACK_MAX_CHARS)
    app_version: str = Field(min_length=1, max_length=40)
    screen: str = Field(min_length=1, max_length=2048)          # cut to the path (at most 200 characters) below

    @field_validator("message")
    @classmethod
    def _message(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message must not be empty")
        return value

    @field_validator("app_version")
    @classmethod
    def _version(cls, value: str) -> str:
        value = value.strip()
        if not _APP_VERSION.fullmatch(value):
            raise ValueError("app_version must be a short version label (letters, digits, '.', '_', '+', '-')")
        return value

    @field_validator("screen")
    @classmethod
    def _screen(cls, value: str) -> str:
        return clean_screen(value)


@feedback_router.post("", status_code=status.HTTP_201_CREATED, dependencies=[Depends(feedback_rate_limit)])
async def send_feedback(body: FeedbackBody, request: Request, user: User = Depends(get_current_user),
                        session: AsyncSession = Depends(get_session)) -> Dict[str, object]:
    """tenant_id and user_id come from the session, never from the body (extra fields are refused)."""
    agent = (request.headers.get("user-agent") or "").strip()[:USER_AGENT_MAX_CHARS] or None
    # C12: the inbox never holds a secret, a token or somebody else's e-mail address (app/support/scrub.py).
    from app.support.scrub import scrub
    message = scrub(body.message, own_email=user.email).strip()[:FEEDBACK_MAX_CHARS]
    row = FeedbackRecord(tenant_id=user.tenant_id, user_id=user.id, message=message, app_version=body.app_version,
                         screen=body.screen, user_agent=agent)
    session.add(row)
    await session.commit()
    return {"id": row.id, "created_at": row.created_at.isoformat() if row.created_at else None}
