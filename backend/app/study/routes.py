"""M77 SX-2: the saved study routes (`ui_v2` + `study_export`; 404 while either is off). Read-only data: nothing here
places, changes or blocks an order.

* `POST   /api/studies`            - save a study from REFERENCES only (instrument key, timeframes, visible ranges, ticked
                                     sections, his title, notes and checklist answers); the server reads every number
                                     itself (app/study/snapshot.py). Over the size bound: 413 with a plain sentence.
* `GET    /api/studies`            - My studies: his own, newest first; search by symbol and IST date range.
* `GET    /api/studies/{id}`       - one of his studies with its snapshot.
* `GET    /api/studies/{id}/json`  - the snapshot as a JSON file (same study = same bytes).
* `DELETE /api/studies/{id}`       - deletes one of his studies.
Self-scoped: the caller's own rows inside his organisation; another member's study is "not found".
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.auth.dependencies import get_current_user
from app.charts import price_action_layer as pal
from app.charts import read_chart as R
from app.core import config
from app.core.rate_limit import user_rate_limit
from app.db.models import User
from app.db.session import get_session
from app.study import snapshot as S
from app.study import store
from app.study.numfmt import format_number

FLAG = "study_export"
router = APIRouter(prefix="/api/studies", tags=["studies"])
rate_limited = user_rate_limit("study_save", config.STUDY_SAVES_PER_MINUTE, 60.0,
                               "You saved several studies just now - wait a minute, then save again.")
Timeframe = Literal["1m", "3m", "5m", "15m", "30m", "1h", "1d"]
Section = Literal["cover", "charts", "structure", "areas", "ai_card", "option_context", "trade_plan", "pre_trade_check",
                  "scenarios", "similar_setups", "notes"]


def _aware(v: datetime | None) -> datetime | None:
    if v is None:
        return None
    return v.astimezone(UTC) if v.tzinfo else v.replace(tzinfo=UTC)


class VisibleRange(BaseModel):
    model_config = ConfigDict(extra="ignore")
    start: datetime | None = None
    end: datetime | None = None

    @field_validator("start", "end")
    @classmethod
    def _utc(cls, v: datetime | None) -> datetime | None:
        return _aware(v)


class CreateBody(BaseModel):
    """References and his own inputs only. Unknown fields (a price, an area, a result) are dropped, never read."""
    model_config = ConfigDict(extra="ignore")
    instrument: str = Field(min_length=1, max_length=100)
    timeframes: list[Timeframe] = Field(min_length=1, max_length=S.MAX_TIMEFRAMES)
    sections: list[Section] | None = None                 # None = every section (PD-1: all on by default)
    title: str | None = Field(default=None, max_length=120)
    notes: str | None = None                              # his own words, kept verbatim (bound checked with a plain message)
    checklist: list[bool | None] | None = Field(default=None, max_length=20)
    visible: dict[Timeframe, VisibleRange] | None = None


async def _flags(session: AsyncSession, user: User) -> None:
    from app.platform.controls import flag_enabled
    for name in ("ui_v2", FLAG):
        if not await flag_enabled(session, name, user.tenant_id):
            raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": name})


def _check_inputs(body: CreateBody) -> None:
    if len(set(body.timeframes)) != len(body.timeframes):
        raise HTTPException(status_code=422, detail="Choose each timeframe once.")
    if body.notes is not None and len(body.notes) > config.STUDY_NOTES_MAX_CHARS:
        raise HTTPException(status_code=422, detail=f"Your notes are longer than {format_number(config.STUDY_NOTES_MAX_CHARS)} "
                                                    "characters. Shorten them, then save again.")
    if body.sections is not None and not body.sections:
        raise HTTPException(status_code=422, detail="Tick at least one section.")


@router.post("", status_code=201, dependencies=[Depends(rate_limited)])
async def create_study(body: CreateBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    await _flags(session, user)
    _check_inputs(body)
    from app.price_action.zones.store import normalise_key
    try:
        key = normalise_key(body.instrument)
    except ValueError:
        key = "*"
    if key == "*":
        raise HTTPException(status_code=422, detail="Open a chart of one symbol, then save the study.")
    exchange, _, symbol = key.partition(":")
    tenant_id, user_id = int(user.tenant_id), int(user.id)
    now = datetime.now(UTC).replace(microsecond=0)
    tfs = S.tf_order(body.timeframes)
    frames = await S.frames_for(session, user, key, tfs, now)
    last = S.last_end(frames, now)
    zones = await S.zones_at(session, user, key, last or now)
    ctx = await R._context(session, key, last) if last is not None else None
    from app.journal.settings import load as load_journal_settings
    rules = list((await load_journal_settings(session, user)).checklist_rules)
    inputs = S.StudyInputs(
        created_at=now, key=key, instrument_kind=await S.instrument_kind(session, exchange, symbol), timeframes=tfs,
        sections=list(body.sections) if body.sections is not None else list(S.SECTIONS), frames=frames, zones=zones,
        daily_context=ctx, quote=await S.live_quote(session, user, exchange, symbol),
        drawings=await S.own_drawings(session, user, exchange, symbol),
        visible={tf: (r.start, r.end) for tf, r in (body.visible or {}).items()},
        title=(body.title or "").strip() or None, notes=body.notes or None, checklist_rules=rules,
        checklist_answers=list(body.checklist or []))
    snap = await pal._in_slot(session, user_id, S.assemble, inputs)       # the reads above are done; the build runs off-loop
    data = S.canonical(snap)
    try:
        S.check_size(data)
    except S.StudyTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from None
    row = store.new_row(tenant_id, user_id, snap, data, S.READING_SOURCE)
    session.add(row)
    await session.flush()
    await write_audit_log(session, tenant_id, user_id, "study_saved", f"study #{row.id}: {len(data)} bytes")
    await session.commit()
    return store.summary(row)


@router.get("")
async def list_studies(symbol: str | None = Query(default=None, max_length=60), date_from: date | None = None,
                       date_to: date | None = None, limit: int = Query(default=50, ge=1, le=store.LIST_MAX),
                       offset: int = Query(default=0, ge=0), user: User = Depends(get_current_user),
                       session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    await _flags(session, user)
    return await store.list_own(session, user.tenant_id, user.id, symbol=symbol, date_from=date_from, date_to=date_to,
                                limit=limit, offset=offset)


@router.get("/{study_id}")
async def get_study(study_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    await _flags(session, user)
    row = await store.get_own(session, user.tenant_id, user.id, study_id)
    return {**store.summary(row), "snapshot": store.snapshot_of(row)}


@router.get("/{study_id}/json")
async def download_json(study_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Response:
    await _flags(session, user)
    row = await store.get_own(session, user.tenant_id, user.id, study_id)
    return Response(store.export_bytes(row), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{store.file_name(row, "json")}"'})


@router.delete("/{study_id}")
async def delete_study(study_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    await _flags(session, user)
    await store.delete_own(session, user.tenant_id, user.id, study_id)
    await write_audit_log(session, user.tenant_id, user.id, "study_deleted", f"study #{study_id}")
    await session.commit()
    return {"deleted": True}


__all__ = ["router", "FLAG", "CreateBody"]
