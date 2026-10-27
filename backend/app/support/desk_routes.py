"""C12: the support desk's routes (app/support/desk.py has the rules).

User side, `/api/support/*`, signed in, behind `ui_v2` and `help_centre` (both OFF: 404, like a route that does not
exist):

* `GET /api/support/help` - the help articles of the Help centre (the same `content/help.md` the support bot reads;
  the entries about the legal pages only while `legal_pages` is on). How-to text, never a security name or a price.
* `GET /api/support/contact` - the WhatsApp / e-mail link, only while the market is open and the owner set them.
* `POST /api/support/report` - "Report a problem": the scrubbed message and the closed list of context fields, into
  the feedback inbox as an open ticket. Rate-limited per user. tenant_id and user_id come from the session.

Admin side, `/api/admin/support/*`, SUPER_ADMIN with a two-factor session, every change audited:

* `GET /api/admin/support/tickets` - the inbox as tickets: status, assignee, response-time targets and lateness;
* `PUT /api/admin/support/tickets/{ticket_id}` - status and / or assignee;
* `GET` / `PUT /api/admin/support/settings` - the support WhatsApp number and e-mail, and the response-time targets.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app import legal
from app.auth.dependencies import get_current_user, require_mfa_session, require_role
from app.beta.routes import FEEDBACK_MAX_CHARS, USER_AGENT_MAX_CHARS, FeedbackBody, clean_screen
from app.core.rate_limit import user_rate_limit
from app.db.models import User
from app.db.session import get_session
from app.platform.controls import flag_enabled
from app.support import desk, knowledge
from app.support.scrub import scrub


async def desk_user(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> User:
    """The caller, while `ui_v2` and `help_centre` are on for their organisation; 404 otherwise."""
    for flag in (desk.PAGE_FLAG, desk.FLAG):
        if not await flag_enabled(session, flag, user.tenant_id):
            raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": flag})
    return user


router = APIRouter(prefix="/api/support", tags=["support"], dependencies=[Depends(desk_user)])
admin_router = APIRouter(prefix="/api/admin/support", tags=["admin"], dependencies=[Depends(require_role()), Depends(require_mfa_session)])
# Module-level so tests can target it through `app.dependency_overrides`.
report_rate_limit = user_rate_limit("support_report", limit=10, window_seconds=3600)


# --- user side --------------------------------------------------------------------------------------------------------

@router.get("/help")
async def help_articles(user: User = Depends(desk_user), session: AsyncSession = Depends(get_session)) -> Dict[str, object]:
    include_legal = await legal.enabled(session, user.tenant_id)
    articles = [{"id": p.id, "title": p.title, "text": p.text, "page": p.url, "keywords": p.keywords}
                for p in knowledge.parse_help(knowledge.HELP_FILE.read_text(encoding="utf-8"))
                if include_legal or not p.requires_legal]
    return {"articles": articles}


@router.get("/contact")
async def support_contact(session: AsyncSession = Depends(get_session)) -> Dict[str, object]:
    return await desk.contact(session)


def _recent(value: Optional[datetime]) -> Optional[datetime]:
    """A time from the app: kept only when it is within a day of the server's clock (a wrong device clock reads as
    unknown rather than as a misleading time)."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    if abs(datetime.now(timezone.utc) - value) > timedelta(days=1):
        return None
    return value


class ReportBody(FeedbackBody):
    """A feedback body (message, app version, screen - validated the same way) plus the captured context. Any other
    field - a form's contents, a header, a token - is refused (extra="forbid")."""
    model_config = ConfigDict(extra="forbid")
    client_time: Optional[datetime] = None
    error_id: Optional[str] = Field(default=None, max_length=64)
    error_screen: Optional[str] = Field(default=None, max_length=2048)
    error_at: Optional[datetime] = None

    @field_validator("error_id")
    @classmethod
    def _error_id(cls, value: Optional[str]) -> Optional[str]:
        value = (value or "").strip()
        if not value:
            return None
        if not desk.ERROR_ID.fullmatch(value):
            raise ValueError("error_id must be an error reference (letters, digits, '.', '_', ':', '-')")
        return value

    @field_validator("error_screen")
    @classmethod
    def _error_screen(cls, value: Optional[str]) -> Optional[str]:
        return clean_screen(value) if value else None


@router.post("/report", status_code=status.HTTP_201_CREATED, dependencies=[Depends(report_rate_limit)])
async def report_problem(body: ReportBody, request: Request, user: User = Depends(desk_user),
                         session: AsyncSession = Depends(get_session)) -> Dict[str, object]:
    message = scrub(body.message, own_email=user.email).strip()[:FEEDBACK_MAX_CHARS]
    agent = (request.headers.get("user-agent") or "").strip()[:USER_AGENT_MAX_CHARS] or None
    row, state = await desk.report(session, user, message=message, app_version=body.app_version, screen=body.screen,
                                   user_agent=agent, client_time=_recent(body.client_time),
                                   error_id=body.error_id, error_screen=body.error_screen if body.error_id else None,
                                   error_at=_recent(body.error_at) if body.error_id else None)
    # The reporter sees their reference and what was attached - never the server's error details.
    context = state["context"] if isinstance(state["context"], dict) else {}
    return {"id": row.id, "created_at": row.created_at.isoformat() if row.created_at else None, "status": state["status"],
            "attached": {k: context.get(k) for k in ("client_time", "error_id", "error_screen", "error_at") if context.get(k)}}


# --- admin side -------------------------------------------------------------------------------------------------------

@admin_router.get("/tickets")
async def list_tickets(ticket_status: Optional[str] = None, limit: int = 200,
                       session: AsyncSession = Depends(get_session)) -> Dict[str, object]:
    if ticket_status is not None and ticket_status not in desk.STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ticket_status must be open, answered or closed")
    return await desk.list_tickets(session, status=ticket_status, limit=limit)


class TicketBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Optional[str] = None
    assignee_id: Optional[int] = None


@admin_router.put("/tickets/{ticket_id}")
async def update_ticket(ticket_id: int, body: TicketBody, admin: User = Depends(require_role()),
                        session: AsyncSession = Depends(get_session)) -> Dict[str, object]:
    kwargs: Dict[str, object] = {}
    if "assignee_id" in body.model_fields_set:
        kwargs["assignee_id"] = body.assignee_id
    try:
        out = await desk.update_ticket(session, admin, ticket_id, status=body.status, **kwargs)  # type: ignore[arg-type]
    except desk.DeskError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if out is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such ticket")
    return out


class DeskSettingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    whatsapp: Optional[str] = Field(default=None, max_length=40)
    email: Optional[str] = Field(default=None, max_length=200)
    first_response_hours: Optional[float] = None
    resolve_hours: Optional[float] = None


@admin_router.get("/settings")
async def get_desk_settings(session: AsyncSession = Depends(get_session)) -> Dict[str, object]:
    return await desk.settings(session)


@admin_router.put("/settings")
async def put_desk_settings(body: DeskSettingsBody, admin: User = Depends(require_role()),
                            session: AsyncSession = Depends(get_session)) -> Dict[str, object]:
    """Only the fields sent change; a field sent as null or "" clears it (no contact, no target)."""
    changes = {name: getattr(body, name) for name in body.model_fields_set}
    try:
        return await desk.set_settings(session, admin, changes)
    except desk.DeskError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


__all__: List[str] = ["admin_router", "report_rate_limit", "router"]
