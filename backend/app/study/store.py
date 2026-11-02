"""M77 SX-2: the saved studies (`copilot_studies`) - one member's own rows only, inside his organisation.

Every read and write here is scoped by user_id AND tenant_id; a study of another member (or of another organisation)
reads as "not found", never as "forbidden" (its existence is not disclosed). The JSON download is the stored snapshot
in one fixed byte form (`export_bytes`), so the same study downloads with the same bytes every time.
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CopilotStudyRecord
from app.study import snapshot as S

EXPORT_FORMAT = "atp-study/1"
IST = timezone(timedelta(hours=5, minutes=30))
NOT_FOUND = "Study not found"
LIST_MAX = 100


def summary(row: CopilotStudyRecord) -> dict[str, Any]:
    """What My studies lists: no snapshot, no share state."""
    created = row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=UTC)
    return {"id": row.id, "title": row.title, "symbol": row.symbol, "exchange": row.exchange, "instrument_kind": row.instrument_kind,
            "timeframes": [t for t in (row.timeframes or "").split(",") if t], "sections": json.loads(row.sections_json or "[]"),
            "created_at": created.isoformat(), "fingerprint": row.snapshot_sha256[:12], "size_bytes": row.size_bytes,
            "reading_source": S.READING_SOURCE_WORDS.get(row.reading_source, S.READING_SOURCE_WORDS["atp"])}


def new_row(tenant_id: int, user_id: int, snap: dict[str, Any], data: bytes, reading_source: str) -> CopilotStudyRecord:
    inst = snap["instrument"]
    return CopilotStudyRecord(tenant_id=tenant_id, user_id=user_id, symbol=inst["symbol"], exchange=inst["exchange"],
                              instrument_kind=inst.get("kind"), timeframes=",".join(snap["timeframes"]), title=snap.get("title"),
                              sections_json=json.dumps(snap["sections"]), snapshot_json=data.decode("utf-8"),
                              snapshot_sha256=S.fingerprint(data), reading_source=reading_source, app_version=snap["app_version"],
                              size_bytes=len(data), share_json=None,
                              created_at=datetime.fromisoformat(snap["created_at"]))


def _mine(tenant_id: int, user_id: int) -> Any:
    return (CopilotStudyRecord.user_id == user_id, CopilotStudyRecord.tenant_id == tenant_id)


async def get_own(session: AsyncSession, tenant_id: int, user_id: int, study_id: int) -> CopilotStudyRecord:
    row = await session.scalar(select(CopilotStudyRecord).where(CopilotStudyRecord.id == study_id, *_mine(tenant_id, user_id)))
    if row is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return row


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


async def list_own(session: AsyncSession, tenant_id: int, user_id: int, *, symbol: str | None = None,
                   date_from: date | None = None, date_to: date | None = None, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """His studies, newest first; `symbol` matches part of the symbol (any case), the dates are IST calendar days
    (both ends included)."""
    where = list(_mine(tenant_id, user_id))
    if symbol and symbol.strip():
        where.append(func.upper(CopilotStudyRecord.symbol).like(_like(" ".join(symbol.upper().split())), escape="\\"))
    if date_from is not None:
        where.append(CopilotStudyRecord.created_at >= datetime.combine(date_from, time.min, IST).astimezone(UTC))
    if date_to is not None:
        where.append(CopilotStudyRecord.created_at < datetime.combine(date_to + timedelta(days=1), time.min, IST).astimezone(UTC))
    total = int(await session.scalar(select(func.count()).select_from(CopilotStudyRecord).where(*where)) or 0)
    rows = await session.scalars(select(CopilotStudyRecord).where(*where)
                                 .order_by(CopilotStudyRecord.created_at.desc(), CopilotStudyRecord.id.desc())
                                 .limit(max(1, min(limit, LIST_MAX))).offset(max(0, offset)))
    return {"items": [summary(r) for r in rows], "total": total}


async def delete_own(session: AsyncSession, tenant_id: int, user_id: int, study_id: int) -> None:
    await get_own(session, tenant_id, user_id, study_id)
    await session.execute(delete(CopilotStudyRecord).where(CopilotStudyRecord.id == study_id, *_mine(tenant_id, user_id)))


async def erase_user(session: AsyncSession, tenant_id: int, user_id: int) -> None:
    """Every study of the member (account erase, retention.erase_user). Does not commit."""
    await session.execute(delete(CopilotStudyRecord).where(*_mine(tenant_id, user_id)))


def snapshot_of(row: CopilotStudyRecord) -> dict[str, Any]:
    return json.loads(row.snapshot_json)


def export_doc(row: CopilotStudyRecord) -> dict[str, Any]:
    """The JSON download: the snapshot (no row id, no share state, no settings) with its fingerprint."""
    return {"format": EXPORT_FORMAT, "fingerprint": row.snapshot_sha256, "information": S.INFO_LINE, "study": snapshot_of(row)}


def export_bytes(row: CopilotStudyRecord) -> bytes:
    return (json.dumps(export_doc(row), sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def file_name(row: CopilotStudyRecord, ext: str) -> str:
    created = row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=UTC)
    safe = "".join(ch if ch.isalnum() else "-" for ch in row.symbol).strip("-").lower() or "study"
    return f"study-{safe}-{created.astimezone(IST).strftime('%Y%m%d-%H%M')}.{ext}"
