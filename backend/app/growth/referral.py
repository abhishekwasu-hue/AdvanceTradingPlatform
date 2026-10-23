"""Readiness C13: the referral flow - each person's own invite link, and the new account that came through it.

Behind `ui_v2` + `referrals` (OFF). Nothing here touches the marketplace (OFF until the last phase, handover 11l), a plan,
a price or a payment: the reward is only described, in the words the operator sets.

* The settings interface (`ReferralSettings`, `get_settings`): the reward wording, stored in the `platform_controls` row
  `referral_settings` (no migration). Payments (C11) is meant to own this row and grant the reward; until it exists the
  admin console edits the wording only and no reward is granted automatically (OPEN_QUESTIONS RDY-C13-4).
* The code: created on request (`POST /api/growth/referral/code`), eight letters and digits without look-alikes, kept in
  the internal `user_prefs` row `referral_code` (not a prefs namespace: no prefs route reads or writes it).
* Attribution (`claim`): the new account sends the code it arrived with, once, inside REFERRAL_CLAIM_DAYS of signing up.
  Refused: its own code, a code of its own organisation, an account that already named a referrer, an unknown code. The
  internal row `referred_by` records the referrer; the referrer only ever sees how many people joined, never who.
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User, UserPrefsRecord

FLAG = "referrals"
SETTINGS_KEY = "referral_settings"
CODE_ROW = "referral_code"
REFERRED_ROW = "referred_by"
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"       # no 0 / O, 1 / I
CODE_LENGTH = 8


class ReferralSettings(BaseModel):
    """What C11 (Payments) will own: the reward wording shown on the invite card. Empty = the card names no reward."""
    model_config = ConfigDict(extra="forbid")
    reward_text: str = Field("", max_length=200)

    @field_validator("reward_text")
    @classmethod
    def _plain(cls, value: str) -> str:
        text = " ".join(value.split())
        if any(c in text for c in "<>"):
            raise ValueError("Plain words only.")
        return text


async def get_settings(session: AsyncSession) -> ReferralSettings:
    from app.platform.controls import _get
    raw = await _get(session, SETTINGS_KEY)
    try:
        return ReferralSettings(reward_text=str(raw.get("reward_text") or ""))
    except ValueError:
        return ReferralSettings()


async def save_settings(session: AsyncSession, admin: User, body: ReferralSettings) -> ReferralSettings:
    from app.audit.log import write_audit_log
    from app.platform.controls import _set
    await _set(session, SETTINGS_KEY, body.model_dump(), admin)
    await write_audit_log(session, None, admin.id, "referral_settings_changed",
                          "reward wording set" if body.reward_text else "reward wording cleared")
    await session.commit()
    return body


def normalise(code: str) -> str:
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())[:CODE_LENGTH * 2]


def valid_code(code: str) -> bool:
    return len(code) == CODE_LENGTH and all(ch in ALPHABET for ch in code)


def new_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))


async def _row(session: AsyncSession, tenant_id: int, user_id: int, namespace: str) -> Tuple[Optional[UserPrefsRecord], Dict[str, Any]]:
    row = await session.scalar(select(UserPrefsRecord).where(UserPrefsRecord.tenant_id == tenant_id, UserPrefsRecord.user_id == user_id,
                                                             UserPrefsRecord.namespace == namespace))
    try:
        value = json.loads(row.value_json) if row else {}
    except ValueError:
        value = {}
    return row, value if isinstance(value, dict) else {}


async def own_code(session: AsyncSession, user: User) -> Optional[str]:
    _, value = await _row(session, user.tenant_id, user.id, CODE_ROW)
    code = str(value.get("code") or "")
    return code if valid_code(code) else None


async def _owner_of(session: AsyncSession, code: str) -> Optional[Tuple[int, int]]:
    row = await session.scalar(select(UserPrefsRecord).where(UserPrefsRecord.namespace == CODE_ROW,
                                                             UserPrefsRecord.value_json == json.dumps({"code": code})))
    return (int(row.tenant_id), int(row.user_id)) if row is not None else None


async def ensure_code(session: AsyncSession, user: User) -> str:
    """The caller's code, created once (a clash with another person's code draws again)."""
    existing = await own_code(session, user)
    if existing:
        return existing
    for _ in range(10):
        code = new_code()
        if await _owner_of(session, code) is None:
            break
    else:                                                # pragma: no cover - 32^8 codes; ten clashes in a row do not happen
        raise RuntimeError("could not draw a free referral code")
    now = datetime.now(timezone.utc)
    row, _ = await _row(session, user.tenant_id, user.id, CODE_ROW)
    if row is None:
        session.add(UserPrefsRecord(tenant_id=user.tenant_id, user_id=user.id, namespace=CODE_ROW,
                                    value_json=json.dumps({"code": code}), version=1, updated_at=now))
    else:
        row.value_json, row.version, row.updated_at = json.dumps({"code": code}), row.version + 1, now
    await session.commit()
    return code


async def joined_count(session: AsyncSession, user_id: int) -> int:
    """How many accounts named this person as their referrer."""
    count = 0
    for value_json in await session.scalars(select(UserPrefsRecord.value_json).where(UserPrefsRecord.namespace == REFERRED_ROW)):
        try:
            if int(json.loads(value_json).get("referrer_user_id")) == user_id:
                count += 1
        except (ValueError, TypeError, AttributeError):
            continue
    return count


async def totals(session: AsyncSession) -> Dict[str, int]:
    """The admin view: how many accounts came through a referral link, and from how many people."""
    referrers = set()
    joined = 0
    for value_json in await session.scalars(select(UserPrefsRecord.value_json).where(UserPrefsRecord.namespace == REFERRED_ROW)):
        try:
            referrers.add(int(json.loads(value_json).get("referrer_user_id")))
            joined += 1
        except (ValueError, TypeError, AttributeError):
            continue
    codes = int(await session.scalar(select(func.count()).select_from(UserPrefsRecord).where(UserPrefsRecord.namespace == CODE_ROW)) or 0)
    return {"joined": joined, "referrers": len(referrers), "links_created": codes}


class ClaimRefused(Exception):
    """A plain reason the claim was not recorded."""


async def claim(session: AsyncSession, user: User, raw_code: str, now: Optional[datetime] = None) -> None:
    now = now or datetime.now(timezone.utc)
    code = normalise(raw_code)
    if not valid_code(code):
        raise ClaimRefused("This invite code is not valid.")
    created = user.created_at if user.created_at.tzinfo else user.created_at.replace(tzinfo=timezone.utc)
    from app.core import config
    if now - created > timedelta(days=config.REFERRAL_CLAIM_DAYS):
        raise ClaimRefused("An invite code can only be used by a new account.")
    row, _ = await _row(session, user.tenant_id, user.id, REFERRED_ROW)
    if row is not None:
        raise ClaimRefused("This account already used an invite code.")
    owner = await _owner_of(session, code)
    if owner is None:
        raise ClaimRefused("This invite code is not valid.")
    tenant_id, referrer = owner
    if referrer == user.id or tenant_id == user.tenant_id:
        raise ClaimRefused("You cannot use your own invite code.")
    session.add(UserPrefsRecord(tenant_id=user.tenant_id, user_id=user.id, namespace=REFERRED_ROW, version=1, updated_at=now,
                                value_json=json.dumps({"referrer_user_id": referrer, "at": now.isoformat()}, sort_keys=True)))
    from app.audit.log import write_audit_log
    await write_audit_log(session, user.tenant_id, user.id, "referral_claimed", "joined through an invite link")
    try:
        await session.commit()
    except IntegrityError:                               # a second claim at the same moment: the first one counts
        await session.rollback()
        raise ClaimRefused("This account already used an invite code.") from None


__all__ = ["FLAG", "ReferralSettings", "get_settings", "save_settings", "ensure_code", "own_code", "claim", "ClaimRefused",
           "joined_count", "totals", "valid_code", "normalise"]
