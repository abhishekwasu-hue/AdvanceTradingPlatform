"""Readiness C13: the first-week onboarding e-mails - connect your broker, start your first paper bot, review your first
signal - through the platform mailer (`app/notifications/mailer.py`).

* OFF unless everything is in place: the flag `onboarding_emails` for the person's organisation, the platform mailer
  configured (PLATFORM_SMTP_*) and the app's public address (PUBLIC_BASE_URL), because every e-mail carries its links and
  its unsubscribe link. With any of them missing nothing is sent and nothing is marked.
* Who: an active, non-operator account with a verified address, inside its first week (ONBOARDING_EMAIL_WINDOW_HOURS
  after sign-up). Turning the flag on later never mails people who signed up long ago.
* When: each step once, ONBOARDING_EMAIL_AFTER_HOURS after sign-up (settings, default 2 / 48 / 120 hours). A step whose
  action the person already took (a broker connected, a paper bot created) is marked skipped and never sent.
* Once: the marks live in the internal `user_prefs` row `onboarding_mail` (not a prefs namespace, so no route reads or
  writes it). A failed send is not marked, so the next run tries again while the step is still in its week.
* Unsubscribe: a signed link in every e-mail (and the List-Unsubscribe header) records an `email_opt_outs` row with the
  scope `onboarding`; account e-mails (verification, password reset) are not affected.

The text is plain English, education and practice only: the content guard of the idea library runs over every line in
the tests. Nothing here reads or places an order.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.db.models import BrokerCredentialRecord, EmailOptOutRecord, StrategyDeploymentRecord, User, UserPrefsRecord

logger = logging.getLogger(__name__)

FLAG = "onboarding_emails"
MARKS = "onboarding_mail"            # internal user_prefs row: {step: "sent:<iso>" | "skipped:<iso>"}
SCOPE = "onboarding"                 # email_opt_outs.scope
STEPS: Tuple[str, ...] = ("broker", "paper_bot", "signal_review")
DEFAULT_HOURS: Tuple[int, ...] = (2, 48, 120)

FOOTER = "Education and practice only. Nothing in this e-mail tells you what to trade."


@dataclass(frozen=True)
class Template:
    subject: str
    lines: Tuple[str, ...]
    link_label: str
    path: str


TEMPLATES: Dict[str, Template] = {
    "broker": Template(
        subject="Connect your broker to see live prices",
        lines=(
            "Welcome to AiAlgoDesk.",
            "Your first step is to connect your broker, so ATP can read prices for your charts, scans and paper bots.",
            "Your broker login stays with your broker. It takes a couple of minutes.",
        ),
        link_label="Connect a broker",
        path="/broker",
    ),
    "paper_bot": Template(
        subject="Start your first paper bot",
        lines=(
            "A paper bot practises an idea for you at real prices with pretend money. No order reaches your broker.",
            "Pick an idea from the library, choose your own settings and start it.",
            "You can pause or change it at any time.",
        ),
        link_label="Start a paper bot",
        path="/deployments",
    ),
    "signal_review": Template(
        subject="Review your first signal",
        lines=(
            "When your paper bot finds the structure you chose, a signal appears on the Signals page, described in plain words.",
            "Open it, read what the structure shows and decide for yourself.",
            "Every paper trade lands in your journal, so you can review what you planned and what happened.",
        ),
        link_label="Open Signals",
        path="/signals",
    ),
}


def after_hours() -> Tuple[int, ...]:
    """The send times from the setting; the defaults when it is malformed (one number per step, never negative)."""
    try:
        hours = tuple(int(x) for x in str(config.ONBOARDING_EMAIL_AFTER_HOURS).split(",") if x.strip())
    except ValueError:
        return DEFAULT_HOURS
    if len(hours) != len(STEPS) or any(h < 0 for h in hours):
        return DEFAULT_HOURS
    return hours


def window() -> timedelta:
    return timedelta(hours=max(1, int(config.ONBOARDING_EMAIL_WINDOW_HOURS)))


# --- the unsubscribe link -------------------------------------------------------------------------------------------

def _key() -> bytes:
    return hashlib.sha256(b"atp-onboarding-unsubscribe|" + (config.JWT_SECRET_KEY or "atp").encode()).digest()


def unsubscribe_token(tenant_id: int, address: str) -> str:
    payload = base64.urlsafe_b64encode(f"{tenant_id}:{address.strip().lower()}".encode()).decode().rstrip("=")
    mac = hmac.new(_key(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{payload}.{mac}"


def read_unsubscribe_token(token: str) -> Optional[Tuple[int, str]]:
    try:
        payload, mac = token.split(".", 1)
        if not hmac.compare_digest(hmac.new(_key(), payload.encode(), hashlib.sha256).hexdigest()[:32], mac):
            return None
        tenant, address = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode().split(":", 1)
        return int(tenant), address
    except (ValueError, UnicodeDecodeError):
        return None


def unsubscribe_url(base: str, tenant_id: int, address: str) -> str:
    return f"{base}/api/growth/onboarding/unsubscribe?t={unsubscribe_token(tenant_id, address)}"


async def opted_out(session: AsyncSession, tenant_id: int, address: str) -> bool:
    return await session.scalar(select(EmailOptOutRecord.id).where(
        EmailOptOutRecord.tenant_id == tenant_id, EmailOptOutRecord.scope == SCOPE,
        func.lower(EmailOptOutRecord.address) == address.strip().lower())) is not None


async def opt_out(session: AsyncSession, tenant_id: int, address: str, now: Optional[datetime] = None) -> bool:
    """Records the opt-out (caller commits); True when it is new."""
    if await opted_out(session, tenant_id, address):
        return False
    session.add(EmailOptOutRecord(tenant_id=tenant_id, address=address.strip().lower(), scope=SCOPE,
                                  created_at=now or datetime.now(timezone.utc)))
    return True


# --- the message ----------------------------------------------------------------------------------------------------

def render(step: str, base: str, unsubscribe: str) -> Tuple[str, str]:
    """(subject, plain-text body) of one step."""
    t = TEMPLATES[step]
    body = [*t.lines, "", f"{t.link_label}: {base}{t.path}", "", FOOTER, f"Stop these first-week e-mails: {unsubscribe}"]
    return t.subject, "\n".join(body)


def readable_lines() -> List[Tuple[str, str]]:
    """Every line a reader sees (links aside) - what the content guard checks."""
    out: List[Tuple[str, str]] = [("footer", FOOTER)]
    for step, t in TEMPLATES.items():
        out += [(f"{step}.subject", t.subject), (f"{step}.link_label", t.link_label)]
        out += [(f"{step}.lines[{i}]", line) for i, line in enumerate(t.lines)]
    return out


# --- the marks ------------------------------------------------------------------------------------------------------

async def read_marks(session: AsyncSession, tenant_id: int, user_id: int) -> Tuple[Optional[UserPrefsRecord], Dict[str, str]]:
    row = await session.scalar(select(UserPrefsRecord).where(UserPrefsRecord.tenant_id == tenant_id, UserPrefsRecord.user_id == user_id,
                                                             UserPrefsRecord.namespace == MARKS))
    try:
        marks = json.loads(row.value_json) if row else {}
    except ValueError:
        marks = {}
    return row, ({k: str(v) for k, v in marks.items() if k in STEPS} if isinstance(marks, dict) else {})


async def write_marks(session: AsyncSession, tenant_id: int, user_id: int, row: Optional[UserPrefsRecord], marks: Dict[str, str],
                      now: datetime) -> UserPrefsRecord:
    value = json.dumps(marks, sort_keys=True)
    if row is None:
        row = UserPrefsRecord(tenant_id=tenant_id, user_id=user_id, namespace=MARKS, value_json=value, version=1, updated_at=now)
        session.add(row)
    else:
        row.value_json, row.version, row.updated_at = value, row.version + 1, now
    await session.commit()
    return row


# --- the steps already taken ----------------------------------------------------------------------------------------

async def _has_broker(session: AsyncSession, tenant_id: int, user_id: int) -> bool:
    return await session.scalar(select(BrokerCredentialRecord.id).where(
        BrokerCredentialRecord.tenant_id == tenant_id, BrokerCredentialRecord.user_id == user_id).limit(1)) is not None


async def _has_paper_bot(session: AsyncSession, tenant_id: int, user_id: int) -> bool:
    return await session.scalar(select(StrategyDeploymentRecord.id).where(
        StrategyDeploymentRecord.tenant_id == tenant_id, StrategyDeploymentRecord.created_by == user_id,
        StrategyDeploymentRecord.mode == "PAPER").limit(1)) is not None


# The steps a person may already have taken; "review your first signal" has no such check and always goes.
DONE: Dict[str, Callable[[AsyncSession, int, int], Awaitable[bool]]] = {"broker": _has_broker, "paper_bot": _has_paper_bot}


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def due_steps(signed_up: datetime, now: datetime, marks: Dict[str, str]) -> List[str]:
    """The steps whose time has come and that carry no mark yet, inside the first week only."""
    if now - signed_up > window() or now < signed_up:
        return []
    return [step for step, hours in zip(STEPS, after_hours()) if step not in marks and now >= signed_up + timedelta(hours=hours)]


async def send_due(session: AsyncSession, now: Optional[datetime] = None) -> int:
    """Sends every onboarding e-mail that is due now. Returns how many were handed to the mail server."""
    from app.alerts.deeplink import app_base_url
    from app.notifications import mailer
    from app.platform.controls import flag_enabled
    now = _aware(now) or datetime.now(timezone.utc)
    base = app_base_url()
    if not mailer.configured() or not base:
        return 0
    since = now - window()
    # Plain values, read once: a rollback below expires every ORM row of this session.
    people = [(u.id, u.tenant_id) for u in await session.scalars(
        select(User).where(User.created_at >= since, User.is_active.is_(True), User.role != "SUPER_ADMIN",
                           User.email_verified_at.is_not(None)).order_by(User.id))]
    memo: Dict[int, bool] = {}
    sent = 0
    for user_id, tenant_id in people:
        if tenant_id not in memo:
            memo[tenant_id] = await flag_enabled(session, FLAG, tenant_id)
        if not memo[tenant_id]:
            continue
        try:
            sent += await _send_user(session, user_id, now, base)
        except Exception as exc:  # noqa: BLE001 - one person's mail never stops the others; retried on the next run
            logger.warning("onboarding mail for user %s not sent: %s", user_id, type(exc).__name__)
            await session.rollback()
    return sent


async def _send_user(session: AsyncSession, user_id: int, now: datetime, base: str) -> int:
    from app.notifications import mailer
    user = await session.get(User, user_id)
    if user is None:
        return 0
    tenant_id, address, signed_up = int(user.tenant_id), str(user.email), _aware(user.created_at)
    if signed_up is None:
        return 0
    row, marks = await read_marks(session, tenant_id, user_id)
    steps = due_steps(signed_up, now, marks)
    if not steps or await opted_out(session, tenant_id, address):
        return 0
    sent = 0
    for step in steps:
        done = DONE.get(step)
        if done is not None and await done(session, tenant_id, user_id):
            marks[step] = f"skipped:{now.isoformat()}"
        else:
            link = unsubscribe_url(base, tenant_id, address)
            subject, body = render(step, base, link)
            ok = await mailer.send(address, subject, body, headers={
                "List-Unsubscribe": f"<{link}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"})
            if not ok:
                break                                    # not marked: tried again on the next run
            marks[step] = f"sent:{now.isoformat()}"
            sent += 1
        row = await write_marks(session, tenant_id, user_id, row, marks, now)
    return sent


__all__ = ["FLAG", "STEPS", "TEMPLATES", "SCOPE", "send_due", "render", "readable_lines", "due_steps", "unsubscribe_token",
           "read_unsubscribe_token", "opt_out", "opted_out"]
