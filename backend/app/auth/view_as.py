"""MO3-A1: the admin's read-only "View as user" (docs/OPEN_QUESTIONS.md MO3-A1, docs/design/UX_AUDIT_FULL.md section 6).

A SUPER_ADMIN with a two-factor-verified session picks a person in the admin console's Support section and gives a
reason. The server opens a `ViewSessionRecord` and hands back a short-lived access token (`create_view_token`) that
reads as that person. What keeps it read-only:

- One chokepoint, `app/auth/view_as_middleware.py`, in front of every route: a request carrying a view token that is not
  a read (anything but GET / HEAD / OPTIONS) is answered 403 before any route runs - orders, broker actions, key
  changes, settings, everything. The one exception is ending the view itself (`END_PATH`). Reads that still act or hand
  something out to keep are refused the same way (`BLOCKED_READS`: exports and downloads, broker sign-in links, the
  webhook credential, the Telegram connect poll, anything under /api/admin).
- No AI call is made for a view (`active()` is read by the AI provider resolution), so a look never spends the person's
  AI budget or key.
- Answers are scrubbed on the way out (`scrub`): any field named like a token, secret, password or key is masked, and
  e-mail and IP addresses are cut like the admin console cuts them - even where a route already masks its own.
- The token carries no login session (`sid`), so no two-factor step-up passes on it, and it is valid only while the
  view session is: not ended, not expired, the admin still a SUPER_ADMIN on the same live, two-factor-verified login
  session, the person still active and not an administrator (`stale_reason`).

Every request of a view is recorded (`view_session_requests`); its start and end are on the hash-chained audit trail
of both accounts; the person sees each view (when, how long, the reason) in Settings > Account & plan.
"""
import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qsl

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.core.config import VIEW_AS_MFA_FRESH_MINUTES, VIEW_AS_MINUTES
from app.core.enums import UserRole
from app.db.models import User, UserSessionRecord, ViewSessionRecord, ViewSessionRequestRecord

READ_ONLY_MESSAGE = "This is a read-only support view. Nothing can be changed, placed, connected or downloaded from it."
READ_ONLY_CODE = "view_read_only"
READ_ONLY_HEADER = "X-View-Read-Only"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
END_PATH = "/api/view-session/end"
REASON_MAX = 300
PATH_MAX = 300
MASK = "••••••"

# Reads a view may not make either, and why: each one acts, or hands out something to keep or reuse.
BLOCKED_READS: Tuple[Tuple["re.Pattern[str]", str], ...] = tuple((re.compile(p), why) for p, why in (
    (r"^/api/admin(/|$)", "the admin console"),
    (r"(^|/)exports?(/|$)", "an export"),
    (r"\.(csv|pdf|zip|xlsx|json)$", "a download"),
    (r"^/api/broker/[^/]+/oauth(/|$)", "a broker sign-in"),
    (r"^/api/broker/[^/]+/login-url$", "a broker sign-in link"),
    (r"^/api/auth/google(/|$)", "a sign-in"),
    (r"^/api/auth/broker/[^/]+/(start|callback)$", "a broker sign-in"),   # M74: it would also store a broker session
    (r"^/api/alerts/telegram/connect$", "the Telegram connect check (it links a chat)"),
    (r"^/api/alerts/unsubscribe$", "an unsubscribe link"),
    (r"^/api/webhooks/tradingview/token$", "the webhook address (a credential)"),
    (r"^/api/public/", "the public API"),
))

# Field names whose string values are masked in every answer a view gets.
_SECRET_FIELD = re.compile(r"(^|_)(token|secret|password|passphrase|api_?key|private_key|access_key|backup_codes?|otpauth_uri)$", re.I)
_EMAIL_FIELD = re.compile(r"(^|_)email$", re.I)
_IP_FIELD = re.compile(r"(^|_)ip(_address)?$", re.I)

_active: ContextVar[bool] = ContextVar("atp_view_as_active", default=False)


def active() -> bool:
    """True while the current request is a read-only view (set by the middleware for the request's duration)."""
    return _active.get()


def set_active(value: bool) -> Any:
    return _active.set(value)


def reset_active(token: Any) -> None:
    _active.reset(token)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    aware = _aware(value)
    return aware.isoformat() if aware else None


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# --- the token ---------------------------------------------------------------------------------------------------------

def _payload_mentions_vs(token: str) -> bool:
    """A cheap look before any JWT parsing: does the token's payload part carry a "vs" key at all? Normal sign-in tokens
    never do, so they skip the extra decode. Anything that cannot be read is not a view token."""
    parts = token.split(".")
    if len(parts) != 3:
        return False
    try:
        raw = base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
    except (ValueError, binascii.Error):
        return False
    return b'"vs"' in raw


def unverified_view_claims(token: Optional[str]) -> Optional[Dict[str, Any]]:
    """The claims of a token that SAYS it is a view token, without checking its signature - enough to refuse a write
    (refusing is always safe); never enough to read anything as anyone."""
    if not token or not _payload_mentions_vs(token):
        return None
    import jwt
    try:
        claims = jwt.decode(token, options={"verify_signature": False, "verify_exp": False, "verify_aud": False})
    except Exception:  # noqa: BLE001 - not a JWT at all: not a view token
        return None
    return claims if isinstance(claims, dict) and claims.get("vs") is not None else None


def verified_view_claims(token: Optional[str]) -> Optional[Dict[str, Any]]:
    """The claims of a genuine, unexpired view token, or None."""
    if not token:
        return None
    from app.auth.security import decode_access_token
    try:
        claims = decode_access_token(token)
    except Exception:  # noqa: BLE001 - forged, expired or malformed
        return None
    return claims if claims.get("vs") is not None else None


def is_view_payload(payload: Dict[str, Any]) -> bool:
    return payload.get("vs") is not None


def refusal(method: str, path: str, query: str = "") -> Optional[str]:
    """Why a view may not make this request, or None when it may (a read that only reads)."""
    method = method.upper()
    if method not in SAFE_METHODS:
        return None if (method == "POST" and path.rstrip("/") == END_PATH) else "a change"
    for pattern, why in BLOCKED_READS:
        if pattern.search(path):
            return why
    if query:
        for pattern, names in BLOCKED_QUERY:
            if pattern.search(path):
                for name, value in parse_qsl(query, keep_blank_values=True):
                    if name in names and value.strip().lower() not in _FALSE_VALUES:
                        return f"{name} on this read"
    return None


def allowed_content_type(content_type: str, status: int) -> bool:
    """Fail closed on what a view may receive: JSON (scrubbed), PNG images and the live event stream - or an empty
    answer (204 / 304, a HEAD). Anything else (CSV, PDF, HTML, plain text, other downloads) is refused."""
    kind = content_type.split(";", 1)[0].strip().lower()
    if not kind:
        return status in (204, 304) or status < 200
    return kind in ALLOWED_CONTENT_TYPES


# MO3-A1: the content types a view may receive (everything else is answered 403 by the middleware).
ALLOWED_CONTENT_TYPES = frozenset({"application/json", "image/png", "text/event-stream"})
# Query values that make a read act (the thesis read's fresh computation and its AI narration): refused on a view.
BLOCKED_QUERY: Tuple[Tuple["re.Pattern[str]", frozenset], ...] = (
    (re.compile(r"^/api/ai/thesis/"), frozenset({"refresh", "narrate"})),
)
_FALSE_VALUES = frozenset({"", "0", "false", "f", "no", "n", "off"})

# The current view's expiry (set with `active`), so a stream can end with the view.
_expires: ContextVar[Optional[float]] = ContextVar("atp_view_as_expires", default=None)


def set_expiry(epoch_seconds: Optional[float]) -> Any:
    return _expires.set(epoch_seconds)


def reset_expiry(token: Any) -> None:
    _expires.reset(token)


def stream_seconds(default: float) -> Optional[float]:
    """For the live stream: the seconds left in the current view when they are fewer than `default`, else None."""
    exp = _expires.get()
    if not active() or exp is None:
        return None
    import time
    left = max(0.0, exp - time.time())
    return left if left < default else None


# --- masking -----------------------------------------------------------------------------------------------------------

def _mask_field(key: str, value: Any) -> Any:
    from app.admin.console import mask_email, mask_ip
    if _SECRET_FIELD.search(key):
        if isinstance(value, str) and value:
            return MASK
        if isinstance(value, list):
            return [MASK if isinstance(v, str) else v for v in value]
        return value
    if isinstance(value, str) and value:
        if _EMAIL_FIELD.search(key):
            return mask_email(value)
        if _IP_FIELD.search(key):
            return mask_ip(value)
    return scrub(value)


def scrub(value: Any) -> Any:
    """`value` with every secret-named string masked and e-mail / IP addresses cut, at any depth."""
    if isinstance(value, dict):
        return {k: _mask_field(str(k), v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def scrub_json(body: bytes) -> bytes:
    """A JSON answer, scrubbed; a body that is not JSON comes back unchanged."""
    try:
        parsed = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return body
    return json.dumps(scrub(parsed), ensure_ascii=False, separators=(",", ":")).encode("utf-8")


# --- validity -----------------------------------------------------------------------------------------------------------

async def stale_reason(session: AsyncSession, record: ViewSessionRecord, now: Optional[datetime] = None) -> Optional[str]:
    """Why this view session can no longer be used (or None while it can): ended, expired, the admin or the admin's
    own login session gone or no longer two-factor verified, the person removed or made an administrator."""
    from app.auth.sessions import session_is_live
    now = now or _utcnow()
    if record.ended_at is not None:
        return record.end_reason or "ended"
    expires = _aware(record.expires_at)
    if expires is None or expires <= now:
        return "expired"
    admin = await session.get(User, record.admin_user_id) if record.admin_user_id is not None else None
    if admin is None or not admin.is_active or admin.role != UserRole.SUPER_ADMIN.value or not admin.mfa_enabled:
        return "the support account changed"
    admin_session = await session.get(UserSessionRecord, record.admin_session_id) if record.admin_session_id is not None else None
    if admin_session is None or not session_is_live(admin_session, now) or admin_session.mfa_verified_at is None:
        return "support signed out"
    target = await session.get(User, record.user_id)
    if target is None or not target.is_active or target.role == UserRole.SUPER_ADMIN.value:
        return "the account changed"
    if await tenant_refusal(session, record.tenant_id, admin) is not None or target.tenant_id != record.tenant_id:
        return "the account changed"
    return None


async def session_alive(session: AsyncSession, payload: Dict[str, Any], token: str) -> bool:
    """`get_current_user` for a view token: the view session it names exists, is this exact token, reads as this person
    for this admin, and is still usable."""
    try:
        record = await session.get(ViewSessionRecord, int(payload["vs"]))
        sub, imp = int(payload["sub"]), int(payload.get("imp") or 0)
    except (KeyError, TypeError, ValueError):
        return False
    if record is None or not hmac.compare_digest(record.token_hash, token_hash(token)):
        return False
    if record.user_id != sub or record.admin_user_id != imp:
        return False
    return await stale_reason(session, record) is None


# --- start, end, record -------------------------------------------------------------------------------------------------

class ViewRefused(Exception):
    """A view that may not start (the route answers 400 / 404 with the sentence)."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def clean_reason(reason: Optional[str]) -> str:
    text = " ".join((reason or "").split())
    if len(text) < 5:
        raise ViewRefused(400, "Say why you are opening this account (a few words - the person will see it).")
    return text[:REASON_MAX]


async def tenant_refusal(session: AsyncSession, tenant_id: int, admin: Optional[User] = None) -> Optional[str]:
    """Why a person of this organisation may not be viewed: it is the operator's own organisation (the admin's), or any
    member of it is a SUPER_ADMIN - a view reads organisation-wide data (team, brokers, ...), which would expose the
    platform side. None when it may."""
    if admin is not None and admin.tenant_id == tenant_id:
        return "An account in the platform's own organisation cannot be viewed."
    admins = await session.scalar(select(User.id).where(User.tenant_id == tenant_id, User.role == UserRole.SUPER_ADMIN.value).limit(1))
    if admins is not None:
        return "An account in an organisation with an administrator cannot be viewed."
    return None


async def ensure_fresh_two_factor(session: AsyncSession, admin_session_id: Optional[int], now: Optional[datetime] = None) -> None:
    """Starting a view needs a two-factor check on this login session within VIEW_AS_MFA_FRESH_MINUTES; else the
    step-up 403 (X-Step-Up: mfa) the client answers with the code prompt."""
    from app.auth.dependencies import MfaRequired
    now = now or _utcnow()
    record = await session.get(UserSessionRecord, admin_session_id) if admin_session_id is not None else None
    verified = _aware(record.mfa_verified_at) if record is not None else None
    if verified is None or now - verified > timedelta(minutes=VIEW_AS_MFA_FRESH_MINUTES):
        raise MfaRequired("Viewing an account requires a fresh two-factor check - enter your authenticator code")


async def end_expired(session: AsyncSession, now: Optional[datetime] = None) -> int:
    """Ends (and audits) every open view past its expiry - views that expired with no later request. Does not commit.
    Called on the admin reads, at every start and by the trading worker every few minutes."""
    now = now or _utcnow()
    rows = list(await session.scalars(select(ViewSessionRecord).where(ViewSessionRecord.ended_at.is_(None),
                                                                      ViewSessionRecord.expires_at <= now)))
    for record in rows:
        await _end(session, record, "expired", now)
    return len(rows)


async def start(session: AsyncSession, admin: User, admin_session_id: Optional[int], target_id: int,
                reason: Optional[str]) -> Tuple[str, ViewSessionRecord, User]:
    """Opens a view of `target_id` for `admin`: ends the admin's other open views, stores the new one (only the token's
    hash), and writes the start on both audit trails. Commits. Returns (token, record, the person)."""
    from app.auth.security import create_view_token
    why = clean_reason(reason)
    if admin_session_id is None:
        raise ViewRefused(403, "Sign in again with two-factor to view an account.")
    await ensure_fresh_two_factor(session, admin_session_id)
    target = await session.get(User, target_id)
    if target is None:
        raise ViewRefused(404, "Nobody with that number.")
    if target.id == admin.id:
        raise ViewRefused(400, "That is your own account.")
    if target.role == UserRole.SUPER_ADMIN.value:
        raise ViewRefused(400, "An administrator's account cannot be viewed.")
    if not target.is_active:
        raise ViewRefused(400, "This account was removed; there is nothing to view.")
    refused = await tenant_refusal(session, target.tenant_id, admin)
    if refused:
        raise ViewRefused(400, refused)
    now = _utcnow()
    # One open view per admin: the admin's own row is locked for the start (Postgres; SQLite serialises writers), so two
    # starts at once cannot both see "no open view"; the partial unique index (migration e8a0c2d4f6b9) is the backstop.
    await session.scalar(select(User.id).where(User.id == admin.id).with_for_update())
    await end_expired(session, now)
    for old in await session.scalars(select(ViewSessionRecord).where(
            ViewSessionRecord.admin_user_id == admin.id, ViewSessionRecord.ended_at.is_(None))):
        await _end(session, old, "a new view started", now)
    await session.flush()
    record = ViewSessionRecord(tenant_id=target.tenant_id, user_id=target.id, admin_user_id=admin.id, admin_session_id=admin_session_id,
                               reason=why, token_hash=secrets.token_hex(32), started_at=now,
                               expires_at=now + timedelta(minutes=VIEW_AS_MINUTES))
    session.add(record)
    await session.flush()
    token = create_view_token(target.id, record.id, admin.id, record.expires_at)
    record.token_hash = token_hash(token)
    until = record.expires_at.strftime("%H:%M UTC")
    await write_audit_log(session, admin.tenant_id, admin.id, "support_view_started",
                          f"read-only view #{record.id} of user {target.id} (organisation {target.tenant_id}) until {until} - reason: {why}")
    await write_audit_log(session, target.tenant_id, target.id, "support_view_started",
                          f"ATP support opened a read-only view of this account until {until} - reason: {why}")
    await session.commit()
    return token, record, target


async def _end(session: AsyncSession, record: ViewSessionRecord, why: str, now: Optional[datetime] = None,
               actor: Optional[User] = None) -> bool:
    """Marks the view ended and writes it on both audit trails - the admin side on `actor` (the admin who ended it) when
    given, else on the view's own admin. Does not commit. False when it had already ended."""
    if record.ended_at is not None:
        return False
    now = now or _utcnow()
    expires = _aware(record.expires_at)
    record.ended_at = min(now, expires) if (why == "expired" and expires is not None) else now
    record.end_reason = why[:60]
    counts = f"{record.request_count} reads, {record.refused_count} refused"
    admin = actor if actor is not None else (await session.get(User, record.admin_user_id) if record.admin_user_id is not None else None)
    if admin is not None:
        by = f"; ended by user {admin.id}" if actor is not None else ""
        await write_audit_log(session, admin.tenant_id, admin.id, "support_view_ended",
                              f"read-only view #{record.id} of user {record.user_id} ended ({why}; {counts}{by})")
    await write_audit_log(session, record.tenant_id, record.user_id, "support_view_ended",
                          f"ATP support's read-only view of this account ended ({why})")
    return True


async def end(session: AsyncSession, record: ViewSessionRecord, why: str, actor: Optional[User] = None) -> bool:
    """Ends the view (idempotent) and commits."""
    ended = await _end(session, record, why, actor=actor)
    await session.commit()
    return ended


async def record_request(session: AsyncSession, view_session_id: int, method: str, path: str, status: int, refused: bool,
                         began: Optional[datetime] = None) -> None:
    """One request of a view, recorded - unless the view had already ended before the request began (a view found
    expired, or its admin signed out, is ended here and the request that found it is not added). Commits."""
    record = await session.get(ViewSessionRecord, view_session_id)
    if record is None:
        return
    now = _utcnow()
    began = began or now
    ended = _aware(record.ended_at)
    if ended is not None and ended < began:
        return
    if ended is None:
        why = await stale_reason(session, record, now)
        if why is not None:
            await _end(session, record, why, now)
            await session.commit()
            return
    session.add(ViewSessionRequestRecord(view_session_id=record.id, method=method[:10], path=path[:PATH_MAX], status=int(status),
                                         refused=refused, at=now))
    await session.execute(update(ViewSessionRecord).where(ViewSessionRecord.id == record.id).values(
        request_count=ViewSessionRecord.request_count + 1,
        refused_count=ViewSessionRecord.refused_count + (1 if refused else 0),
        last_seen_at=now))
    await session.commit()


# --- what the API shows ---------------------------------------------------------------------------------------------------

def describe(record: ViewSessionRecord, target: Optional[User] = None) -> Dict[str, Any]:
    """The view as the admin console and the banner show it (the person's e-mail masked)."""
    from app.admin.console import mask_email
    now = _utcnow()
    expires = _aware(record.expires_at)
    open_ = record.ended_at is None and expires is not None and expires > now
    return {
        "id": record.id, "user_id": record.user_id, "tenant_id": record.tenant_id,
        "name": mask_email(target.email) if target is not None else None,
        "reason": record.reason, "started_at": _iso(record.started_at), "expires_at": _iso(record.expires_at),
        "ended_at": _iso(record.ended_at), "end_reason": record.end_reason, "active": open_,
        "seconds_left": max(0, int((expires - now).total_seconds())) if (open_ and expires is not None) else 0,
        "requests": record.request_count, "refused": record.refused_count, "last_seen_at": _iso(record.last_seen_at),
    }


def describe_for_person(record: ViewSessionRecord) -> Dict[str, Any]:
    """The view as the person sees it in their own Settings: when, until when, why - "ATP support", never the admin."""
    expires = _aware(record.expires_at)
    ended = _aware(record.ended_at)
    now = _utcnow()
    open_ = ended is None and expires is not None and expires > now
    return {"id": record.id, "by": "ATP support", "reason": record.reason, "started_at": _iso(record.started_at),
            "ended_at": _iso(ended if ended is not None else (expires if not open_ else None)), "active": open_,
            "read_only": True, "requests": record.request_count}
