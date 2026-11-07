"""P3-4-B10 (DPDP s.14; privacy notice "nominate another person"): a user names ONE nominee - the person who may use
their rights if they die or cannot act - and can view, change or remove them.

* `GET /api/me/nominee`    - the caller's nominee, or `{"nominee": null}`.
* `PUT /api/me/nominee`    - name, relationship and an e-mail or a phone (at least one); replaces any earlier nominee.
* `DELETE /api/me/nominee` - removes it.

Only the caller's own row (user and organisation). The nominee's contact is a third person's personal data: it is never
written to a log or to the audit text (the audit row names the user id only), it is part of "Download my data" and it is
deleted with the account (`retention.service.erase_user`). Storing a nominee gives that person no access by itself: the
process that acts on a nominee's request is the grievance officer's (docs/legal runbook), after an identity check.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.auth.dependencies import get_current_user
from app.db.models import User, UserNomineeRecord
from app.db.session import get_session

router = APIRouter(prefix="/api/me/nominee", tags=["me"])

_PHONE = re.compile(r"^\+?[0-9][0-9 \-]{5,18}[0-9]$")


class NomineeIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    relationship: str = Field(min_length=1, max_length=60)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(default=None, max_length=20)

    @field_validator("name", "relationship")
    @classmethod
    def _text(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("must not be empty")
        return v

    @field_validator("email", mode="before")
    @classmethod
    def _email(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = v.strip()
            if len(v) > 254:
                raise ValueError("e-mail is too long")
        return v or None

    @field_validator("phone", mode="before")
    @classmethod
    def _phone(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = v.strip()
            if v and not _PHONE.match(v):
                raise ValueError("phone must be digits, with an optional + at the start")
        return v or None


def _out(row: Optional[UserNomineeRecord]) -> Dict[str, Any]:
    if row is None:
        return {"nominee": None}
    return {"nominee": {"name": row.name, "relationship": row.relationship_to_user, "email": row.email, "phone": row.phone,
                        "updated_at": row.updated_at.isoformat() if row.updated_at else None}}


async def _mine(session: AsyncSession, user: User) -> Optional[UserNomineeRecord]:
    return await session.scalar(select(UserNomineeRecord).where(
        UserNomineeRecord.user_id == user.id, UserNomineeRecord.tenant_id == user.tenant_id))


@router.get("")
async def get_nominee(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    return _out(await _mine(session, user))


@router.put("")
async def put_nominee(body: NomineeIn, user: User = Depends(get_current_user),
                      session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    if not body.email and not body.phone:
        raise HTTPException(status_code=422, detail="Give an e-mail or a phone number for your nominee")
    row = await _mine(session, user)
    if row is None:
        row = UserNomineeRecord(tenant_id=user.tenant_id, user_id=user.id, name=body.name,
                                relationship_to_user=body.relationship, email=body.email, phone=body.phone)
        session.add(row)
    else:
        row.name, row.relationship_to_user, row.email, row.phone = body.name, body.relationship, body.email, body.phone
        row.updated_at = datetime.now(UTC)
    await write_audit_log(session, user.tenant_id, user.id, "nominee_saved", f"user #{user.id}")   # never the nominee's details
    await session.commit()
    await session.refresh(row)
    return _out(row)


@router.delete("")
async def delete_nominee(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    result = await session.execute(delete(UserNomineeRecord).where(
        UserNomineeRecord.user_id == user.id, UserNomineeRecord.tenant_id == user.tenant_id))
    if result.rowcount:
        await write_audit_log(session, user.tenant_id, user.id, "nominee_removed", f"user #{user.id}")
        await session.commit()
    return {"nominee": None}
