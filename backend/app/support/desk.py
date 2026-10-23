"""C12: the support desk - tickets over the existing feedback inbox, the desk settings and the contact link.

No migration: every "Send feedback" / "Report a problem" message stays one `feedback` row (as PW6 stored it); what a
ticket adds lives in `platform_controls`, like the A2 alert state and the incident public notes:

* `support_ticket:<feedback id>` - {"kind": "problem" | "feedback", "status": "open" | "answered" | "closed",
  "assignee_id": int | None, "first_response_at", "closed_at", "context": {...}}. A feedback row without one is an
  open "feedback" ticket nobody has picked up (every message sent before C12).
* `support_desk_settings` - the support WhatsApp number and e-mail (empty by default; the owner fills them in the
  admin console, never in git) and the two response-time targets in hours (none by default: no target, nothing is
  ever "late" until the owner sets one).

The ticket `context` is the closed list of fields `report()` accepts: the client's time, the last server error the app
saw (its reference - the same id as `X-Request-ID` and the in-memory `recent_errors` store - the screen it happened on
and when) and, when this API process still holds that error, its route template and status. Never a header, a body,
a query string, a form's contents or anything the user did not type themselves; the message goes through `scrub`.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.db.models import FeedbackRecord, PlatformControlRecord, User
from app.observability import recent_errors

FLAG = "help_centre"
PAGE_FLAG = "ui_v2"
SETTINGS_KEY = "support_desk_settings"
TICKET_KEY_PREFIX = "support_ticket:"
STATUSES = ("open", "answered", "closed")
KINDS = ("problem", "feedback")
MAX_TARGET_HOURS = 24 * 30
ERROR_ID = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
_PHONE = re.compile(r"^\+?[0-9]{8,15}$")
_EMAIL = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")

DEFAULT_SETTINGS: Dict[str, object] = {"whatsapp": "", "email": "", "first_response_hours": None, "resolve_hours": None}


class DeskError(ValueError):
    """A refused change, with a plain sentence for the admin."""


def ticket_key(feedback_id: int) -> str:
    return f"{TICKET_KEY_PREFIX}{int(feedback_id)}"


def _loads(raw: Optional[str]) -> Dict:
    try:
        value = json.loads(raw or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _parse(value: object) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        out = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return out if out.tzinfo else out.replace(tzinfo=timezone.utc)


# --- settings ---------------------------------------------------------------------------------------------------------

def clean_phone(raw: object) -> str:
    """The WhatsApp number as digits with an optional leading '+' (spaces, dashes and brackets dropped); "" clears it."""
    text = re.sub(r"[\s()\-.]", "", str(raw or ""))
    if not text:
        return ""
    if not _PHONE.fullmatch(text):
        raise DeskError("The WhatsApp number must be 8 to 15 digits, with the country code (for example +91...).")
    return text


def clean_email(raw: object) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    if len(text) > 200 or not _EMAIL.fullmatch(text):
        raise DeskError("The support e-mail is not a valid address.")
    return text


def clean_hours(raw: object) -> Optional[float]:
    if raw is None or raw == "":
        return None
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise DeskError("A response-time target is a number of hours.") from exc
    if not (0 < value <= MAX_TARGET_HOURS):
        raise DeskError(f"A response-time target must be more than 0 and at most {MAX_TARGET_HOURS} hours.")
    return round(value, 2)


def _settings_from(value: Dict) -> Dict[str, object]:
    out = dict(DEFAULT_SETTINGS)
    for name, clean in (("whatsapp", clean_phone), ("email", clean_email), ("first_response_hours", clean_hours),
                        ("resolve_hours", clean_hours)):
        try:
            out[name] = clean(value.get(name))
        except DeskError:
            pass                                  # a malformed stored value reads as unset, never a crash
    return out


async def settings(session: AsyncSession) -> Dict[str, object]:
    row = await session.get(PlatformControlRecord, SETTINGS_KEY)
    return _settings_from(_loads(row.value_json if row is not None else None))


async def set_settings(session: AsyncSession, admin: User, changes: Dict[str, object]) -> Dict[str, object]:
    """Applies the given fields (the others stay), audited with the names of the fields that changed - never the values
    of the contact fields. Commits."""
    current = await settings(session)
    updated = dict(current)
    for name, clean in (("whatsapp", clean_phone), ("email", clean_email), ("first_response_hours", clean_hours),
                        ("resolve_hours", clean_hours)):
        if name in changes:
            updated[name] = clean(changes[name])
    changed = sorted(k for k in updated if updated[k] != current[k])
    row = await session.get(PlatformControlRecord, SETTINGS_KEY)
    if row is None:
        row = PlatformControlRecord(key=SETTINGS_KEY)
        session.add(row)
    row.value_json = json.dumps(updated, sort_keys=True)
    row.updated_by = admin.id
    detail = ", ".join(f"{k}={updated[k]}" if k.endswith("_hours") else f"{k} {'set' if updated[k] else 'cleared'}" for k in changed)
    await write_audit_log(session, None, admin.id, "support_settings_updated", detail or "no change")
    await session.commit()
    return updated


# --- the contact link -------------------------------------------------------------------------------------------------

def whatsapp_url(number: str) -> Optional[str]:
    digits = number.lstrip("+")
    return f"https://wa.me/{digits}" if digits else None


async def contact(session: AsyncSession, now: Optional[datetime] = None) -> Dict[str, object]:
    """What the app shows a user: the WhatsApp / e-mail link only while the NSE session is open (the app's own market
    calendar, holidays included) and only when the owner set them. Outside the session, `next_open` says when."""
    from app.market_data import calendar

    desk = await settings(session)
    status = await calendar.market_session_status(session, now)
    configured = bool(desk["whatsapp"] or desk["email"])
    show = configured and status.is_open
    return {
        "configured": configured,
        "market_open": status.is_open,
        "next_open": _iso(status.next_open),
        "whatsapp_url": whatsapp_url(str(desk["whatsapp"])) if show else None,
        "email": (desk["email"] or None) if show else None,
    }


# --- tickets ----------------------------------------------------------------------------------------------------------

def _state(value: Dict) -> Dict[str, object]:
    status = value.get("status") if value.get("status") in STATUSES else "open"
    kind = value.get("kind") if value.get("kind") in KINDS else "feedback"
    assignee = value.get("assignee_id")
    context = value.get("context") if isinstance(value.get("context"), dict) else {}
    return {"kind": kind, "status": status, "assignee_id": assignee if isinstance(assignee, int) else None,
            "first_response_at": value.get("first_response_at") if isinstance(value.get("first_response_at"), str) else None,
            "closed_at": value.get("closed_at") if isinstance(value.get("closed_at"), str) else None,
            "context": context}


def _owner(row: FeedbackRecord) -> str:
    """Which message a ticket row belongs to: its id, organisation and creation time (an id a database could hand out
    again after a deletion never inherits an old ticket's state)."""
    return f"{row.id}:{row.tenant_id}:{_iso(row.created_at)}"


async def _states(session: AsyncSession, feedback: Iterable[FeedbackRecord]) -> Dict[int, Dict[str, object]]:
    by_key = {ticket_key(f.id): f for f in feedback}
    if not by_key:
        return {}
    rows = await session.scalars(select(PlatformControlRecord).where(PlatformControlRecord.key.in_(list(by_key))))
    out: Dict[int, Dict[str, object]] = {}
    for r in rows:
        value = _loads(r.value_json)
        f = by_key[r.key]
        if value.get("for") == _owner(f):
            out[f.id] = _state(value)
    return out


async def _write_state(session: AsyncSession, feedback: FeedbackRecord, state: Dict[str, object], user_id: Optional[int]) -> None:
    row = await session.get(PlatformControlRecord, ticket_key(feedback.id))
    if row is None:
        row = PlatformControlRecord(key=ticket_key(feedback.id))
        session.add(row)
    row.value_json = json.dumps({**state, "for": _owner(feedback)}, sort_keys=True)
    row.updated_by = user_id


async def forget(session: AsyncSession, feedback_ids: Iterable[int]) -> None:
    """Removes the ticket rows of deleted messages (account erasure). The caller commits."""
    keys = [ticket_key(i) for i in feedback_ids]
    if keys:
        await session.execute(delete(PlatformControlRecord).where(PlatformControlRecord.key.in_(keys)))


def error_context(error_id: Optional[str]) -> Dict[str, object]:
    """The route template and status of the reported error while this process still holds it in `recent_errors`."""
    if not error_id:
        return {}
    for entry in recent_errors.recent(recent_errors.MAX_KEPT):
        if entry.get("request_id") == error_id:
            return {"error_route": entry.get("route"), "error_status": entry.get("status"), "error_found": True}
    return {"error_found": False}


async def report(session: AsyncSession, user: User, *, message: str, app_version: str, screen: str, user_agent: Optional[str],
                 client_time: Optional[datetime], error_id: Optional[str], error_screen: Optional[str],
                 error_at: Optional[datetime]) -> Tuple[FeedbackRecord, Dict[str, object]]:
    """One "Report a problem": a feedback row (the inbox) and its open ticket with the captured context. Commits."""
    row = FeedbackRecord(tenant_id=user.tenant_id, user_id=user.id, message=message, app_version=app_version, screen=screen,
                         user_agent=user_agent)
    session.add(row)
    await session.flush()
    await session.refresh(row)
    context: Dict[str, object] = {"client_time": _iso(client_time)}
    if error_id:
        context.update({"error_id": error_id, "error_screen": error_screen, "error_at": _iso(error_at)})
        context.update(error_context(error_id))
    state = _state({"kind": "problem", "status": "open", "context": context})
    await _write_state(session, row, state, user.id)
    await session.commit()
    return row, state


def _due(created: Optional[datetime], hours: Optional[float]) -> Optional[datetime]:
    if created is None or hours is None:
        return None
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return created + timedelta(hours=hours)


def timing(created: Optional[datetime], state: Dict[str, object], targets: Dict[str, object],
           now: Optional[datetime] = None) -> Dict[str, object]:
    """The ticket against the response-time targets: when each is due, and whether it was (or is) missed."""
    now = now or datetime.now(timezone.utc)
    first_due = _due(created, targets.get("first_response_hours"))  # type: ignore[arg-type]
    resolve_due = _due(created, targets.get("resolve_hours"))  # type: ignore[arg-type]
    first_at = _parse(state.get("first_response_at"))
    closed_at = _parse(state.get("closed_at"))

    def late(due: Optional[datetime], done: Optional[datetime]) -> Optional[bool]:
        if due is None:
            return None
        return (done or now) > due

    return {"first_response_due_at": _iso(first_due), "first_response_late": late(first_due, first_at),
            "resolve_due_at": _iso(resolve_due), "resolve_late": late(resolve_due, closed_at)}


async def assignees(session: AsyncSession) -> List[Dict[str, object]]:
    rows = await session.scalars(select(User).where(User.role == "SUPER_ADMIN", User.is_active.is_(True)).order_by(User.id))
    return [{"id": u.id, "email": u.email} for u in rows]


async def list_tickets(session: AsyncSession, *, status: Optional[str] = None, limit: int = 200,
                       now: Optional[datetime] = None) -> Dict[str, object]:
    """The newest `limit` inbox messages as tickets (optionally one status), the targets, the counts and who can be
    assigned. Admin only (the route is SUPER_ADMIN + two-factor)."""
    limit = max(1, min(int(limit), 1000))
    query = select(FeedbackRecord, User.email).outerjoin(User, User.id == FeedbackRecord.user_id).order_by(FeedbackRecord.id.desc()).limit(limit)
    rows = (await session.execute(query)).all()
    states = await _states(session, [f for f, _ in rows])
    targets = await settings(session)
    people = await assignees(session)
    names = {p["id"]: p["email"] for p in people}
    counts = {s: 0 for s in STATUSES}
    late = 0
    tickets: List[Dict[str, object]] = []
    for f, email in rows:
        state = states.get(f.id) or _state({})
        counts[str(state["status"])] += 1
        times = timing(f.created_at, state, targets, now)
        waiting_late = bool(times["first_response_late"]) and not state["first_response_at"]
        if state["status"] != "closed" and (waiting_late or bool(times["resolve_late"])):
            late += 1
        if status and state["status"] != status:
            continue
        tickets.append({"id": f.id, "tenant_id": f.tenant_id, "user_id": f.user_id, "email": email, "message": f.message,
                        "app_version": f.app_version, "screen": f.screen, "created_at": _iso(f.created_at), **state,
                        "assignee_email": names.get(state["assignee_id"]) if state["assignee_id"] is not None else None, **times})
    return {"tickets": tickets, "counts": counts, "late": late, "assignees": people,
            "targets": {"first_response_hours": targets["first_response_hours"], "resolve_hours": targets["resolve_hours"]}}


_UNSET = object()


async def update_ticket(session: AsyncSession, admin: User, feedback_id: int, *, status: Optional[str] = None,
                        assignee_id: object = _UNSET, now: Optional[datetime] = None) -> Optional[Dict[str, object]]:
    """Moves a ticket's status and / or assignee, audited. None when no such message. Commits."""
    row = await session.get(FeedbackRecord, feedback_id)
    if row is None:
        return None
    state = (await _states(session, [row])).get(feedback_id) or _state({})
    before = (state["status"], state["assignee_id"])
    now_iso = _iso(now or datetime.now(timezone.utc))
    if status is not None:
        if status not in STATUSES:
            raise DeskError("The status must be open, answered or closed.")
        if status != "open" and not state["first_response_at"]:
            state["first_response_at"] = now_iso
        state["closed_at"] = (state["closed_at"] or now_iso) if status == "closed" else None
        state["status"] = status
    if assignee_id is not _UNSET:
        if assignee_id is not None:
            person = await session.get(User, assignee_id)
            if person is None or person.role != "SUPER_ADMIN" or not person.is_active:
                raise DeskError("Tickets can be assigned to an active platform administrator only.")
        state["assignee_id"] = assignee_id
    await _write_state(session, row, state, admin.id)
    after = (state["status"], state["assignee_id"])
    await write_audit_log(session, None, admin.id, "support_ticket_updated",
                          f"ticket #{feedback_id} status {before[0]}->{after[0]} assignee {before[1] or '-'}->{after[1] or '-'}")
    await session.commit()
    targets = await settings(session)
    return {"id": feedback_id, **state, **timing(row.created_at, state, targets, now)}


__all__ = ["DEFAULT_SETTINGS", "DeskError", "FLAG", "KINDS", "PAGE_FLAG", "STATUSES", "assignees", "contact", "error_context", "forget",
           "list_tickets", "report", "set_settings", "settings", "timing", "update_ticket", "whatsapp_url"]
