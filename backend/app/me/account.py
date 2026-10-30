"""AUD-G1.1 (owner audit 28 Oct 2026; DPDP rights): the signed-in user's own data, without asking anyone.

* `GET /api/me/export` - a ZIP of everything stored about them: profile, preferences, orders, trades, journal, AI
  conversations and feedback; each as JSON, the tables also as CSV. Secrets (password hash, MFA secret, encrypted keys,
  tokens) are never part of it.
* `POST /api/me/erase` - "Delete my account": confirmed with the password and the word ERASE. Nothing is erased at
  once: the account keeps working for `ACCOUNT_ERASE_GRACE_DAYS` and the request can be cancelled (`DELETE`). After the
  grace period the daily retention run erases it with the same `erase_user` the team owner uses (orders and trades stay
  as an anonymous id for the regulatory retention period). Every step is in the audit log.
"""
from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.auth.dependencies import get_current_user
from app.auth.security import verify_password_async
from app.core import config
from app.db.models import (
    CopilotChatTurnRecord,
    FeedbackRecord,
    JournalEntryRecord,
    JournalTagRecord,
    LlmCallRecord,
    OrderRecord,
    TradeRecord,
    User,
    UserPrefsRecord,
)
from app.db.session import get_session
from app.platform import controls

router = APIRouter(prefix="/api/me", tags=["me"])

KEY_ERASE_REQUESTS = "account_erase_requests"          # platform_controls: {"requests": {"<user id>": "<requested at>"}}
_SECRET_WORDS = ("password", "secret", "encrypted", "token", "hash")
# Each dataset: (file name, model). Only the caller's own rows (user_id) inside their organisation.
_DATASETS = (("preferences", UserPrefsRecord), ("orders", OrderRecord), ("trades", TradeRecord), ("journal_entries", JournalEntryRecord),
             ("journal_tags", JournalTagRecord), ("ai_conversations", LlmCallRecord), ("feedback", FeedbackRecord),
             ("copilot_conversation", CopilotChatTurnRecord))      # CP-UX CU6


def _plain(value: Any) -> Any:
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _row(record: Any) -> Dict[str, Any]:
    """Every column of a row except secrets (by name: password / secret / encrypted / token / hash)."""
    return {c.name: _plain(getattr(record, c.key, None)) for c in record.__table__.columns
            if not any(w in c.name.lower() for w in _SECRET_WORDS)}


def _csv(rows: List[Dict[str, Any]]) -> str:
    out = io.StringIO()
    if rows:
        writer = csv.DictWriter(out, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return out.getvalue()


async def collect(session: AsyncSession, user: User) -> Dict[str, List[Dict[str, Any]]]:
    data: Dict[str, List[Dict[str, Any]]] = {"profile": [_row(user)]}
    for name, model in _DATASETS:
        if not hasattr(model, "user_id"):
            continue
        q = select(model).where(model.user_id == user.id)
        if hasattr(model, "tenant_id"):
            q = q.where(model.tenant_id == user.tenant_id)
        data[name] = [_row(r) for r in await session.scalars(q.order_by(model.id))]
    return data


@router.get("/export")
async def export_my_data(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Response:
    data = await collect(session, user)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", "Your data in AIAlgoDesk, as stored on the day of this export. One JSON file per kind of "
                                 "record; tables also as CSV. Passwords, two-factor secrets and keys are never included.\n")
        for name, rows in data.items():
            z.writestr(f"{name}.json", json.dumps(rows, indent=2, ensure_ascii=False))
            if name != "profile":
                z.writestr(f"{name}.csv", _csv(rows))
    await write_audit_log(session, user.tenant_id, user.id, "my_data_exported", f"user #{user.id}: {sum(len(v) for v in data.values())} rows")
    await session.commit()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="my-data-{stamp}.zip"'})


# --- Delete my account ------------------------------------------------------------------------------------------------

class EraseAsk(BaseModel):
    password: str
    confirm: str


async def _requests(session: AsyncSession) -> Dict[str, str]:
    return dict((await controls._get(session, KEY_ERASE_REQUESTS)).get("requests") or {})


def _status(requested_at: Optional[str]) -> Dict[str, Any]:
    if not requested_at:
        return {"requested": False, "requested_at": None, "erase_after": None, "grace_days": config.ACCOUNT_ERASE_GRACE_DAYS}
    after = datetime.fromisoformat(requested_at) + timedelta(days=config.ACCOUNT_ERASE_GRACE_DAYS)
    return {"requested": True, "requested_at": requested_at, "erase_after": after.isoformat(), "grace_days": config.ACCOUNT_ERASE_GRACE_DAYS}


async def _sole_owner_with_others(session: AsyncSession, user: User) -> bool:
    if user.role != "OWNER":
        return False
    owners = await session.scalar(select(func.count()).select_from(User).where(
        User.tenant_id == user.tenant_id, User.role == "OWNER", User.is_active.is_(True), User.id != user.id))
    others = await session.scalar(select(func.count()).select_from(User).where(
        User.tenant_id == user.tenant_id, User.is_active.is_(True), User.id != user.id))
    return not owners and bool(others)


@router.get("/erase")
async def erase_status(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    return _status((await _requests(session)).get(str(user.id)))


@router.post("/erase")
async def request_erase(ask: EraseAsk, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    if user.role == "SUPER_ADMIN":
        raise HTTPException(status_code=403, detail="Platform administrator accounts are closed by the platform owner")
    if ask.confirm.strip().upper() != "ERASE":
        raise HTTPException(status_code=400, detail="Type ERASE to confirm")
    if not await verify_password_async(ask.password, user.hashed_password):
        raise HTTPException(status_code=400, detail="The password is not right")
    if await _sole_owner_with_others(session, user):
        raise HTTPException(status_code=409, detail="You are the only owner of an organisation with other members: make one of them an owner first")
    requests = await _requests(session)
    if str(user.id) not in requests:
        requests[str(user.id)] = datetime.now(timezone.utc).isoformat()
        await controls._set(session, KEY_ERASE_REQUESTS, {"requests": requests}, user)
        await write_audit_log(session, user.tenant_id, user.id, "account_erase_requested",
                              f"user #{user.id}: erased after {config.ACCOUNT_ERASE_GRACE_DAYS} days unless cancelled")
        await session.commit()
    return _status(requests[str(user.id)])


@router.delete("/erase")
async def cancel_erase(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    requests = await _requests(session)
    if requests.pop(str(user.id), None) is not None:
        await controls._set(session, KEY_ERASE_REQUESTS, {"requests": requests}, user)
        await write_audit_log(session, user.tenant_id, user.id, "account_erase_cancelled", f"user #{user.id}")
        await session.commit()
    return _status(None)


async def run_due_erasures(session: AsyncSession, now: Optional[datetime] = None) -> int:
    """The daily retention run: erase every account whose grace period has passed (deactivate, then `erase_user`).
    Does not commit."""
    from app.retention.service import erase_user
    now = now or datetime.now(timezone.utc)
    requests = await _requests(session)
    done = 0
    for uid, requested_at in list(requests.items()):
        if datetime.fromisoformat(requested_at) + timedelta(days=config.ACCOUNT_ERASE_GRACE_DAYS) > now:
            continue
        user = await session.get(User, int(uid))
        if user is not None and not user.email.endswith("@erased.invalid"):
            user.is_active = False
            await erase_user(session, user, actor_id=user.id, reason="the user's own request (Delete my account)")
            done += 1
        requests.pop(uid)
    if done or len(requests) != len(await _requests(session)):
        await controls._set(session, KEY_ERASE_REQUESTS, {"requests": requests}, None)
    return done
