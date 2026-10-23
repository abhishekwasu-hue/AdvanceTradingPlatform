"""MO3-A1: the routes of the admin's read-only "View as user" (app/auth/view_as.py has the rules).

- `/api/admin/view-sessions` (SUPER_ADMIN with a two-factor-verified session, the admin console's router dependencies):
  start a view of one person with a reason, list the recent views, read one view's requests, end a view.
- `/api/view-session` (any login): what the banner shows while a view token is in use, and "End" - the one write a
  view token may make (it only ends the view).
- `/api/me/support-access` (any login): the person's own record of when ATP support viewed their account.
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import view_as
from app.auth.dependencies import current_session_id, get_current_user, require_mfa_session, require_role
from app.db.models import User, ViewSessionRecord, ViewSessionRequestRecord
from app.db.session import get_session

admin_router = APIRouter(prefix="/api/admin/view-sessions", tags=["admin"],
                         dependencies=[Depends(require_role()), Depends(require_mfa_session)])
router = APIRouter(tags=["auth"])
_bearer = HTTPBearer(auto_error=True)


class StartViewBody(BaseModel):
    user_id: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=view_as.REASON_MAX)


def _refused(exc: view_as.ViewRefused) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.detail)


@admin_router.post("")
async def start_view(body: StartViewBody, admin: User = Depends(require_role()), admin_session: Optional[int] = Depends(current_session_id),
                     session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Opens a read-only view of one person for VIEW_AS_MINUTES. The answer's `token` is shown once; only its hash is
    kept. Starting a view ends the admin's earlier open view."""
    try:
        token, record, target = await view_as.start(session, admin, admin_session, body.user_id, body.reason)
    except view_as.ViewRefused as exc:
        raise _refused(exc) from None
    return {"token": token, "view": view_as.describe(record, target)}


@admin_router.get("")
async def list_views(limit: int = Query(50, ge=1, le=200), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """The newest views, every admin's: who was viewed (masked), why, when, how long and how many requests."""
    rows = (await session.execute(select(ViewSessionRecord, User).join(User, User.id == ViewSessionRecord.user_id)
                                  .order_by(ViewSessionRecord.id.desc()).limit(limit))).all()
    return {"views": [{**view_as.describe(r, u), "admin_user_id": r.admin_user_id} for r, u in rows]}


@admin_router.get("/{view_id}")
async def view_detail(view_id: int, limit: int = Query(200, ge=1, le=1000), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """One view and its recorded requests, newest first (method, path, status, refused)."""
    record = await session.get(ViewSessionRecord, view_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Unknown view")
    requests: List[ViewSessionRequestRecord] = list(await session.scalars(
        select(ViewSessionRequestRecord).where(ViewSessionRequestRecord.view_session_id == record.id)
        .order_by(ViewSessionRequestRecord.id.desc()).limit(limit)))
    return {"view": {**view_as.describe(record, await session.get(User, record.user_id)), "admin_user_id": record.admin_user_id},
            "requests": [{"at": view_as._iso(q.at), "method": q.method, "path": q.path, "status": q.status, "refused": q.refused}
                         for q in requests]}


@admin_router.post("/{view_id}/end")
async def end_view(view_id: int, session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    record = await session.get(ViewSessionRecord, view_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Unknown view")
    ended = await view_as.end(session, record, "ended by support")
    return {"ended": ended, "view": view_as.describe(record, await session.get(User, record.user_id))}


async def _view_of(session: AsyncSession, token: str) -> Optional[ViewSessionRecord]:
    claims = view_as.verified_view_claims(token)
    if claims is None:
        return None
    record = await session.get(ViewSessionRecord, int(claims["vs"]))
    if record is None or record.token_hash != view_as.token_hash(token):
        return None
    return record


@router.get("/api/view-session")
async def current_view(user: User = Depends(get_current_user), credentials: HTTPAuthorizationCredentials = Depends(_bearer),
                       session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """The banner: is this a read-only support view, of whom, until when. A normal sign-in answers `active: false`."""
    record = await _view_of(session, credentials.credentials)
    if record is None:
        return {"active": False}
    return {"active": True, "read_only": True, "view": view_as.describe(record, user)}


@router.post("/api/view-session/end")
async def end_current_view(user: User = Depends(get_current_user), credentials: HTTPAuthorizationCredentials = Depends(_bearer),
                           session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """The banner's "End": ends the view behind this token (the only write a view token may make). A normal sign-in has
    no view to end."""
    record = await _view_of(session, credentials.credentials)
    if record is None:
        return {"ended": False}
    return {"ended": await view_as.end(session, record, "ended by support")}


@router.get("/api/me/support-access")
async def my_support_access(limit: int = Query(20, ge=1, le=100), user: User = Depends(get_current_user),
                            session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """When ATP support viewed this account (read-only), newest first - the caller's own rows only."""
    rows = list(await session.scalars(select(ViewSessionRecord).where(ViewSessionRecord.user_id == user.id,
                                                                      ViewSessionRecord.tenant_id == user.tenant_id)
                                      .order_by(ViewSessionRecord.id.desc()).limit(limit)))
    return {"views": [view_as.describe_for_person(r) for r in rows]}
