"""M95 PR-3 (owner order M95, M95-Q3 approved): "Ask for an invite" while `self_signup` is OFF.

* `POST /api/auth/invite-request` - no sign-in. Accepted only while `self_signup` is OFF (while it is ON the answer is 409
  "Sign-up is open - create an account"). Per-IP limited (the shared `rate_limit` helper). The e-mail address is
  checked and stored in lower case, the optional note is at most 500 characters, and the caller's IP is stored only as
  a keyed hash (never the raw address). The same address again within a day is stored once; every accepted call gets
  the same 202 sentence, so the answer never tells whether an address was already sent (no enumeration).
* `GET /api/admin/invite-requests` and `PATCH /api/admin/invite-requests/{request_id}` - SUPER_ADMIN with a two-factor
  session, like every `/api/admin/*` route: the list, and marking one request invited or dismissed (audited). The invite
  itself goes through the existing invite flow (Team invites / beta invite codes). Nothing here sends an e-mail.
"""
import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.auth.dependencies import require_mfa_session, require_role
from app.core import config as app_config
from app.core.rate_limit import rate_limit
from app.db.models import InviteRequestRecord, User
from app.db.session import get_session
from app.platform.controls import flag_enabled

router = APIRouter(prefix="/api/auth", tags=["auth"])
admin_router = APIRouter(prefix="/api/admin/invite-requests", tags=["admin"],
                         dependencies=[Depends(require_role()), Depends(require_mfa_session)])

NOTE_MAX = 500
DEDUPE_WINDOW = timedelta(days=1)
STATUSES = ("new", "invited", "dismissed")
LIST_MAX = 500
ACCEPTED = "Thank you. Your request is with the team; you will get an invite link by e-mail when a place is free."
SIGN_UP_OPEN = "Sign-up is open - create an account"

# Module-level so tests can target this exact dependency (tests/test_m95_invite_requests.py resets its window).
invite_request_rate_limit = rate_limit("auth_invite_request", limit=5, window_seconds=3600,
                                       message="Too many invite requests from this network. Please try again in an hour.")


def _ip_key() -> bytes:
    return hashlib.sha256(b"atp-invite-request-ip|" + (app_config.JWT_SECRET_KEY or "atp").encode()).digest()


def ip_hash(ip: str) -> str:
    """A keyed hash of the caller's IP: two requests from one network can be told apart from others, the address
    itself cannot be read back (a plain sha256 of an IPv4 address is reversible in seconds)."""
    return hmac.new(_ip_key(), ip.encode("utf-8"), hashlib.sha256).hexdigest()


class InviteRequestBody(BaseModel):
    email: EmailStr
    note: str | None = Field(default=None, max_length=NOTE_MAX)

    @field_validator("note")
    @classmethod
    def _note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class InviteRequestAccepted(BaseModel):
    message: str


class InviteRequestRow(BaseModel):
    id: int
    email: str
    note: str | None
    status: str
    created_at: datetime
    handled_by: str | None
    handled_at: datetime | None


class InviteRequestList(BaseModel):
    counts: dict[str, int]
    requests: list[InviteRequestRow]


class InviteRequestStatusBody(BaseModel):
    status: Literal["invited", "dismissed"]


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


@router.post("/invite-request", status_code=status.HTTP_202_ACCEPTED, response_model=InviteRequestAccepted,
             dependencies=[Depends(invite_request_rate_limit)])
async def ask_for_invite(body: InviteRequestBody, request: Request,
                         session: AsyncSession = Depends(get_session)) -> InviteRequestAccepted:
    """M95-Q3: an "Ask for an invite" request, stored for the admin. Only while sign-up is closed."""
    if await flag_enabled(session, "self_signup"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=SIGN_UP_OPEN)
    email = str(body.email).strip().lower()
    now = datetime.now(timezone.utc)
    recent = await session.scalar(
        select(InviteRequestRecord.id).where(InviteRequestRecord.email == email,
                                             InviteRequestRecord.created_at >= now - DEDUPE_WINDOW).limit(1))
    if recent is None:
        ip = request.client.host if request.client else "unknown"
        session.add(InviteRequestRecord(email=email, note=body.note, ip_hash=ip_hash(ip), status="new", created_at=now))
        await session.commit()
    return InviteRequestAccepted(message=ACCEPTED)


async def _row(session: AsyncSession, record: InviteRequestRecord) -> InviteRequestRow:
    handler = await session.get(User, record.handled_by) if record.handled_by else None
    return InviteRequestRow(id=record.id, email=record.email, note=record.note, status=record.status,
                            created_at=_aware(record.created_at) or record.created_at,
                            handled_by=handler.email if handler else None, handled_at=_aware(record.handled_at))


@admin_router.get("", response_model=InviteRequestList)
async def list_invite_requests(status_filter: Literal["new", "invited", "dismissed", "all"] = Query("all", alias="status"),
                               session: AsyncSession = Depends(get_session)) -> InviteRequestList:
    """The requests, newest first (at most 500), and how many are in each status."""
    counts = {s: 0 for s in STATUSES}
    for name, count in (await session.execute(
            select(InviteRequestRecord.status, func.count()).group_by(InviteRequestRecord.status))).all():
        counts[name] = int(count)
    query = select(InviteRequestRecord).order_by(InviteRequestRecord.created_at.desc(), InviteRequestRecord.id.desc())
    if status_filter != "all":
        query = query.where(InviteRequestRecord.status == status_filter)
    records = (await session.scalars(query.limit(LIST_MAX))).all()
    return InviteRequestList(counts=counts, requests=[await _row(session, r) for r in records])


@admin_router.patch("/{request_id}", response_model=InviteRequestRow)
async def mark_invite_request(request_id: int, body: InviteRequestStatusBody, admin: User = Depends(require_role()),
                              session: AsyncSession = Depends(get_session)) -> InviteRequestRow:
    """Marks one request invited or dismissed (who and when are kept; written to the audit log). Sends nothing."""
    record = await session.get(InviteRequestRecord, request_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This request no longer exists")
    record.status = body.status
    record.handled_by = admin.id
    record.handled_at = datetime.now(timezone.utc)
    await write_audit_log(session, None, admin.id, "invite_request_marked", f"request {record.id}: {body.status}")
    await session.commit()
    await session.refresh(record)
    return await _row(session, record)
