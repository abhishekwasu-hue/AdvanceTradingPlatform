"""Phase M / V4.10: incident records - opened by the operator or automatically by the global
kill switch, closed with root cause, actions and measured data-loss / downtime.

A2: an incident may carry a **public note** - the one sentence the public status page shows under it ("Charts load
slowly; a fix is being rolled out."). The title, summary and cause stay in the admin console. The notes live in one
`platform_controls` row (`incident_public_notes`, {incident id: note}), so no migration; at most PUBLIC_NOTE_MAX
characters, plain text, written by the operator."""
import json
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.db.models import AuditLogRecord, IncidentRecord, PlatformControlRecord, User

STATUSES = ("OPEN", "MITIGATED", "RESOLVED")
NOTES_KEY = "incident_public_notes"
PUBLIC_NOTE_MAX = 280


def clean_note(note: Optional[str]) -> str:
    return " ".join(str(note or "").split())[:PUBLIC_NOTE_MAX]


async def public_notes(session: AsyncSession) -> Dict[str, str]:
    row = await session.get(PlatformControlRecord, NOTES_KEY)
    try:
        value = json.loads(row.value_json or "{}") if row is not None else {}
    except ValueError:
        return {}
    return {str(k): str(v) for k, v in value.items() if v} if isinstance(value, dict) else {}


async def set_public_note(session: AsyncSession, incident_id: int, note: Optional[str], user: Optional[User]) -> None:
    """Sets (or with an empty note removes) the incident's public note. The caller commits."""
    notes = await public_notes(session)
    text = clean_note(note)
    if text:
        notes[str(incident_id)] = text
    else:
        notes.pop(str(incident_id), None)
    row = await session.get(PlatformControlRecord, NOTES_KEY)
    if row is None:
        row = PlatformControlRecord(key=NOTES_KEY)
        session.add(row)
    row.value_json = json.dumps(notes, sort_keys=True)
    row.updated_by = user.id if user else None


async def _latest_audit_id(session: AsyncSession) -> Optional[int]:
    return await session.scalar(select(AuditLogRecord.id).order_by(AuditLogRecord.id.desc()).limit(1))


async def open_incident(session: AsyncSession, *, title: str, severity: str, summary: str = "", source: str = "operator",
                        tenant_id: Optional[int] = None, user: Optional[User] = None, commit: bool = True,
                        public_note: Optional[str] = None) -> IncidentRecord:
    record = IncidentRecord(title=title.strip()[:200], severity=severity, summary=summary or "", source=source, tenant_id=tenant_id,
                            opened_by=user.id if user else None, audit_log_from_id=await _latest_audit_id(session))
    session.add(record)
    await session.flush()
    await write_audit_log(session, tenant_id, user.id if user else None, "incident_opened", f"#{record.id} [{severity}] {record.title}")
    if clean_note(public_note):
        await set_public_note(session, record.id, public_note, user)
    if commit:
        await session.commit()
        await session.refresh(record)
    return record


async def update_incident(session: AsyncSession, record: IncidentRecord, user: User, *, status: Optional[str] = None, summary: Optional[str] = None,
                          root_cause: Optional[str] = None, actions_taken: Optional[str] = None, data_loss_minutes: Optional[float] = None,
                          downtime_minutes: Optional[float] = None, public_note: Optional[str] = None) -> IncidentRecord:
    now = datetime.now(timezone.utc)
    if summary is not None:
        record.summary = summary
    if root_cause is not None:
        record.root_cause = root_cause
    if actions_taken is not None:
        record.actions_taken = actions_taken
    if data_loss_minutes is not None:
        record.data_loss_minutes = data_loss_minutes
    if downtime_minutes is not None:
        record.downtime_minutes = downtime_minutes
    if public_note is not None:
        await set_public_note(session, record.id, public_note, user)
    if status is not None:
        if status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        record.status = status
        if status == "MITIGATED" and record.mitigated_at is None:
            record.mitigated_at = now
        if status == "RESOLVED":
            record.resolved_at = record.resolved_at or now
            record.mitigated_at = record.mitigated_at or now
            record.audit_log_to_id = await _latest_audit_id(session)
            if record.downtime_minutes is None:
                record.downtime_minutes = round((now - (record.started_at if record.started_at.tzinfo else record.started_at.replace(tzinfo=timezone.utc))).total_seconds() / 60.0, 1)
    await write_audit_log(session, record.tenant_id, user.id, "incident_updated", f"#{record.id} -> {record.status}")
    await session.commit()
    await session.refresh(record)
    return record


async def list_incidents(session: AsyncSession, *, status: Optional[str] = None, limit: int = 100) -> List[IncidentRecord]:
    query = select(IncidentRecord)
    if status:
        query = query.where(IncidentRecord.status == status)
    return list(await session.scalars(query.order_by(IncidentRecord.id.desc()).limit(limit)))


def as_dict(r: IncidentRecord, public_note: Optional[str] = None) -> dict:
    iso = lambda v: v.isoformat() if v else None  # noqa: E731
    return {"id": r.id, "severity": r.severity, "title": r.title, "summary": r.summary, "status": r.status, "source": r.source, "tenant_id": r.tenant_id,
            "started_at": iso(r.started_at), "mitigated_at": iso(r.mitigated_at), "resolved_at": iso(r.resolved_at), "root_cause": r.root_cause,
            "actions_taken": r.actions_taken, "audit_log_from_id": r.audit_log_from_id, "audit_log_to_id": r.audit_log_to_id,
            "data_loss_minutes": r.data_loss_minutes, "downtime_minutes": r.downtime_minutes, "opened_by": r.opened_by, "created_at": iso(r.created_at),
            "public_note": public_note or None}
