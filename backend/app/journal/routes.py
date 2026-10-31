"""Z6: `/api/journal/*` - the trader's own journal, behaviour tags and discipline score (behind `discipline_engine`).

Personal: every route reads and writes only the logged-in trader's rows; another user's trade or tag answers 404,
exactly like one that does not exist. Read-only towards trading: nothing here places, changes or blocks an order.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.models import JournalTagRecord, User
from app.db.session import get_session
from app.journal import FLAG, entries, service
from app.journal import settings as journal_settings
from app.journal.review import advice_in as review_advice_in
from app.journal.settings import COMPONENT_TAGS, RANGES, TAGS, DisciplineSettings, SettingsError
from app.platform.controls import require_flag

router = APIRouter(prefix="/api/journal", tags=["journal"])

WEEK_PATTERN = r"^\d{4}-W\d{2}$"


async def _flag(session: AsyncSession, user: User) -> None:
    await require_flag(session, FLAG, user.tenant_id)


def _settings_payload(s: DisciplineSettings) -> dict[str, Any]:
    return {"settings": s.to_dict(), "defaults": DisciplineSettings().to_dict(), "ranges": {k: list(v) for k, v in RANGES.items()},
            "components": {k: list(v) for k, v in COMPONENT_TAGS.items()}, "tags": list(TAGS)}


@router.get("/settings")
async def get_settings(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """The trader's thresholds and score weights, the documented defaults and each number's allowed range."""
    await _flag(session, user)
    return _settings_payload(await journal_settings.load(session, user))


@router.put("/settings")
async def put_settings(body: dict[str, Any], user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Changes some settings (keys not sent keep their value). 422 names the first bad key."""
    await _flag(session, user)
    try:
        saved = await journal_settings.save(session, user, body)
    except SettingsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _settings_payload(saved)


def _week_or_422(week: str | None) -> str | None:
    if week is None:
        return None
    try:
        service.week_bounds(week)
    except service.WeekError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return week


@router.get("/week")
async def journal_week(week: str | None = Query(default=None, pattern=WEEK_PATTERN), user: User = Depends(get_current_user),
                       session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """One IST week of the trader's journal (default: this week): every trade's plan vs execution, fills, costs,
    MAE / MFE, holding time, chart references, Greeks at entry for options, its behaviour tags - and the week's
    discipline score with its components. Detectors run on every read, so new trades and changed settings show at once."""
    await _flag(session, user)
    data = await service.refresh_week(session, user, _week_or_422(week))
    tags_by_trade: dict[int, list[JournalTagRecord]] = {}
    for tag in data["tags"]:
        tags_by_trade.setdefault(tag.trade_id, []).append(tag)
    s: DisciplineSettings = data["settings"]
    # M62 JN-2: the trader's own layer (his tags, checklist, attachments, the plan as recorded) on every entry, and his
    # manual (outside-ATP) trades of the week beside them - those never count in the discipline score (no detector
    # reads them).
    manual = await entries.manual_in(session, user, data["start"], data["end"])
    refs = [entries.Ref(trade=t) for t in data["trades"]] + [entries.Ref(manual=m) for m in manual]
    files = await entries.attachments_of(session, user, refs)
    timeframes = await entries.deployment_timeframes(session, data["trades"])
    return {"week": data["week"], "start": data["start"].isoformat(), "end": data["end"].isoformat(),
            "entries": [entries.trade_payload(t, data["entries"].get(t.id), tags_by_trade.get(t.id, []), s,
                                              attachments=files.get(("trade", t.id), []), timeframe=timeframes.get(t.deployment_id or -1))
                        for t in data["trades"]],
            "manual_entries": [entries.manual_payload(m, s, attachments=files.get(("manual", m.id), [])) for m in manual],
            "score": data["score"]}


REVIEW_QUESTION = ("Explain this weekly trading review in plain language: what the numbers show about the week and which "
                   "behaviours the losses came with. Describe only; do not advise any trade, security or action.")


@router.get("/review")
async def weekly_review(http_request: Request, week: str | None = Query(default=None, pattern=WEEK_PATTERN), history: int = Query(default=4, ge=0, le=12),
                        narrate: bool = Query(default=False, description="ask the organisation's AI provider to explain the numbers (checked)"),
                        user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Z6-b: the weekly review - every number from the trader's own journal (totals, setups, behaviour costs, the
    discipline score, the trader's own previous weeks), with plain-language lines that describe them. `narrate=true`
    asks the Copilot to explain the same facts: its text must use only those numbers and pass the advice filter, else
    the lines stay the explanation. Never a leaderboard: only this trader's weeks."""
    await _flag(session, user)
    review = await service.weekly_review(session, user, _week_or_422(week), history_weeks=history)
    if review["totals"]["trades"]:
        from app.growth import activation  # AUD-G2.4: the week's rows are committed by the review itself
        await activation.record_step(session, user, "first_journal_review")
    explanation: dict[str, Any] = {"source": "rules", "text": "\n".join(review["lines"])}
    if narrate and review["totals"]["trades"]:
        from app.ai.rate_limit import ai_rate_limit
        try:
            await ai_rate_limit(http_request, user, session)    # A4: an AI call counts toward the plan's AI limit
        except HTTPException as exc:
            if exc.status_code != 429:
                raise
            explanation["note"] = "AI explanation not used (the AI limit for this minute is used up); the numbers are explained from the rules"
        else:
            explanation.update(await _narrate(session, user, review["lines"]))
    review["explanation"] = explanation
    return review


async def _narrate(session: AsyncSession, user: User, facts: list[str]) -> dict[str, Any]:
    from app.ai import copilot, metering
    from app.ai.compliance_terms import KIND_COPILOT, latest
    from app.db.models import Tenant
    if await latest(session, KIND_COPILOT, tenant_id=user.tenant_id, user_id=user.id) is None:
        return {"note": "AI explanation needs the Copilot acknowledgement (AI Copilot page); the numbers are explained from the rules"}
    from app.platform.controls import flag_enabled
    if not await flag_enabled(session, "ai_copilot", user.tenant_id):
        return {"note": "The AI Copilot is turned off; the numbers are explained from the rules"}
    tenant = await session.get(Tenant, user.tenant_id)
    if tenant is None:
        return {"note": "No organisation record; the numbers are explained from the rules"}
    from app.ai import resolve as ai_resolve
    provider = await ai_resolve.provider_for_user(session, user, tenant, "knowledge")
    if provider.name == "rule_based":
        reason = getattr(provider, "reason", "") or "no AI provider configured"
        return {"note": f"AI not used ({reason}); the numbers are explained from the rules"}
    text, why = await copilot.narrate(provider, "en", "weekly review of the trader's own journal", REVIEW_QUESTION, facts)
    if text:
        # Stricter than the Copilot's own filter: a review only describes a finished week. A "framed" reply (a specific
        # call kept with a disclaimer elsewhere) or any instruction / forward-looking / buy-sell wording is not shown.
        advice = review_advice_in(text)
        if why == "framed" or advice:
            text, why = None, "it gave advice: " + (", ".join(f'"{a}"' for a in advice[:3]) if advice else "a specific trade call")
    await ai_resolve.mark_used(session, user.tenant_id, provider, error=None if text else f"weekly review explanation not used ({why})")
    await session.commit()
    if not text:
        return {"note": f"AI explanation not used ({why}); the numbers are explained from the rules", "usage": metering.spent_by(provider)}
    return {"source": "ai", "text": text, "usage": metering.spent_by(provider)}


@router.get("/trades/{trade_id}")
async def journal_trade(trade_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    await _flag(session, user)
    trade = await service.own_trade(session, user, trade_id)
    if trade is None:
        raise HTTPException(status_code=404, detail="No such trade")
    s = await journal_settings.load(session, user)
    payload = await entries.trade_entry(session, user, trade, s)      # created from the trade row when missing (committed)
    from app.growth import activation  # AUD-G2.4
    await activation.record_step(session, user, "first_journal_review")
    return payload


class TagBody(BaseModel):
    tag: str = Field(min_length=2, max_length=30)
    note: str | None = Field(default=None, max_length=300)


@router.post("/trades/{trade_id}/tags", status_code=201)
async def add_tag(trade_id: int, body: TagBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """The trader tags a trade by hand (a behaviour the detectors cannot see). Stored as CONFIRMED."""
    await _flag(session, user)
    trade = await service.own_trade(session, user, trade_id)
    if trade is None:
        raise HTTPException(status_code=404, detail="No such trade")
    try:
        row = await service.add_user_tag(session, user, trade, body.tag.strip().lower(), body.note)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return service.tag_dict(row)


class TagDecision(BaseModel):
    status: str = Field(pattern=r"^(CONFIRMED|DISMISSED|SUGGESTED)$")


@router.patch("/tags/{tag_id}")
async def decide_tag(tag_id: int, body: TagDecision, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Confirm or dismiss a tag (SUGGESTED puts it back to undecided). A dismissed tag never counts in the score."""
    await _flag(session, user)
    row = await service.set_tag_status(session, user, tag_id, body.status)
    if row is None:
        raise HTTPException(status_code=404, detail="No such tag")
    return service.tag_dict(row)


# --- M62 JN-2: the trader's own layer of an entry (an ATP trade or a manual one) ---------------------------------------
# Every route below reads and writes only the logged-in trader's own entries: another user's or another organisation's
# answers 404. None of them places, changes or blocks an order; a manual trade is journal data only.

UPLOAD_CHUNK = 64 * 1024


def _http(exc: entries.EntryError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=str(exc))


async def _ref(session: AsyncSession, user: User, kind: str, ref_id: int) -> entries.Ref:
    await _flag(session, user)
    ref = await entries.own_ref(session, user, kind, ref_id)
    if ref is None:
        raise HTTPException(status_code=404, detail="No such trade" if kind == "trade" else "No such manual trade")
    return ref


class NotesBody(BaseModel):
    notes: str | None = Field(default=None, max_length=20000)


class UserTagsBody(BaseModel):
    tags: list[str] = Field(default_factory=list, max_length=30)


class ChecklistItem(BaseModel):
    rule: str = Field(min_length=1, max_length=200)
    followed: bool | None = None


class ChecklistBody(BaseModel):
    items: list[ChecklistItem] = Field(min_length=1, max_length=40)


async def _notes(kind: str, ref_id: int, body: NotesBody, user: User, session: AsyncSession) -> dict[str, Any]:
    ref = await _ref(session, user, kind, ref_id)
    return {"kind": ref.kind, "id": ref.id, "notes": await entries.set_notes(session, user, ref, body.notes)}


async def _user_tags(kind: str, ref_id: int, body: UserTagsBody, user: User, session: AsyncSession) -> dict[str, Any]:
    ref = await _ref(session, user, kind, ref_id)
    s = await journal_settings.load(session, user)
    try:
        tags = await entries.set_user_tags(session, user, ref, body.tags, s)
    except entries.EntryError as exc:
        raise _http(exc) from exc
    return {"kind": ref.kind, "id": ref.id, "user_tags": tags, "custom_tags": list(s.custom_tags)}


async def _checklist(kind: str, ref_id: int, body: ChecklistBody, user: User, session: AsyncSession) -> dict[str, Any]:
    ref = await _ref(session, user, kind, ref_id)
    s = await journal_settings.load(session, user)
    try:
        items = await entries.set_checklist(session, user, ref, [i.model_dump() for i in body.items], s)
    except entries.EntryError as exc:
        raise _http(exc) from exc
    return {"kind": ref.kind, "id": ref.id, "checklist": items}


async def _read_upload(file: UploadFile, limit: int) -> bytes:
    """At most the size limit plus one byte is read, so an oversize upload is refused without holding all of it."""
    data = bytearray()
    while True:
        chunk = await file.read(UPLOAD_CHUNK)
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > limit:
            raise HTTPException(status_code=413, detail=f"The image is larger than {limit / (1024 * 1024):g} MB (your journal limit). "
                                                        "Crop it or save it smaller, then try again.")
    return bytes(data)


async def _attach(kind: str, ref_id: int, file: UploadFile | None, user: User, session: AsyncSession) -> dict[str, Any]:
    ref = await _ref(session, user, kind, ref_id)        # whose entry first: another organisation gets 404, never a 422
    if file is None:
        raise HTTPException(status_code=422, detail="Choose an image (PNG or JPEG) to attach.")
    s = await journal_settings.load(session, user)
    data = await _read_upload(file, entries.size_limit_bytes(s))
    try:
        row = await entries.add_attachment(session, user, ref, data, s)
    except entries.EntryError as exc:
        raise _http(exc) from exc
    return entries.attachment_dict(row)


async def _chart(kind: str, ref_id: int, at: str, user: User, session: AsyncSession) -> Response:
    ref = await _ref(session, user, kind, ref_id)
    try:
        png = await entries.chart_png(session, user, ref, at)
    except entries.EntryError as exc:
        raise _http(exc) from exc
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "private, max-age=300"})


@router.patch("/trades/{trade_id}/notes")
async def trade_notes(trade_id: int, body: NotesBody, user: User = Depends(get_current_user),
                      session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """The trader's notes on an ATP trade (empty text clears them); editable any time, open or closed."""
    return await _notes("trade", trade_id, body, user, session)


@router.patch("/manual/{manual_id}/notes")
async def manual_notes(manual_id: int, body: NotesBody, user: User = Depends(get_current_user),
                       session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    return await _notes("manual", manual_id, body, user, session)


@router.patch("/trades/{trade_id}/user-tags")
async def trade_user_tags(trade_id: int, body: UserTagsBody, user: User = Depends(get_current_user),
                          session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Replaces the trade's own mistake / emotion tags, from the trader's own tag list (journal settings)."""
    return await _user_tags("trade", trade_id, body, user, session)


@router.patch("/manual/{manual_id}/user-tags")
async def manual_user_tags(manual_id: int, body: UserTagsBody, user: User = Depends(get_current_user),
                           session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    return await _user_tags("manual", manual_id, body, user, session)


@router.patch("/trades/{trade_id}/checklist")
async def trade_checklist(trade_id: int, body: ChecklistBody, user: User = Depends(get_current_user),
                          session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Answers rules of the trader's own checklist (yes / no; null clears an answer)."""
    return await _checklist("trade", trade_id, body, user, session)


@router.patch("/manual/{manual_id}/checklist")
async def manual_checklist(manual_id: int, body: ChecklistBody, user: User = Depends(get_current_user),
                           session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    return await _checklist("manual", manual_id, body, user, session)


@router.post("/trades/{trade_id}/attachments", status_code=201)
async def trade_attachment(trade_id: int, file: UploadFile | None = File(default=None), user: User = Depends(get_current_user),
                           session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Attaches one PNG or JPEG image (re-encoded, metadata removed, stored privately for this organisation); at most
    the trader's own size limit (journal settings, default 5 MB)."""
    return await _attach("trade", trade_id, file, user, session)


@router.post("/manual/{manual_id}/attachments", status_code=201)
async def manual_attachment(manual_id: int, file: UploadFile | None = File(default=None), user: User = Depends(get_current_user),
                            session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    return await _attach("manual", manual_id, file, user, session)


@router.get("/attachments/{attachment_id}")
async def get_attachment(attachment_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Response:
    await _flag(session, user)
    row = await entries.own_attachment(session, user, attachment_id)
    data = entries.read_attachment(row) if row is not None else None
    if data is None:
        raise HTTPException(status_code=404, detail="No such image")
    return Response(content=data, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


@router.delete("/attachments/{attachment_id}")
async def delete_attachment(attachment_id: int, user: User = Depends(get_current_user),
                            session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    await _flag(session, user)
    row = await entries.own_attachment(session, user, attachment_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such image")
    await entries.delete_attachment(session, row)
    return {"deleted": attachment_id}


@router.get("/trades/{trade_id}/chart")
async def trade_chart(trade_id: int, at: str = Query(default="entry", pattern=r"^(entry|exit)$"), user: User = Depends(get_current_user),
                      session: AsyncSession = Depends(get_session)) -> Response:
    """The chart snapshot at entry or exit, drawn on the server from the stored candles up to that instant, with the
    entry, exit and stop marked (PNG; nothing stored)."""
    return await _chart("trade", trade_id, at, user, session)


@router.get("/manual/{manual_id}/chart")
async def manual_chart(manual_id: int, at: str = Query(default="entry", pattern=r"^(entry|exit)$"), user: User = Depends(get_current_user),
                       session: AsyncSession = Depends(get_session)) -> Response:
    return await _chart("manual", manual_id, at, user, session)


class ManualTradeBody(BaseModel):
    """A trade done outside ATP, as the trader records it. Only symbol, direction, quantity, entry time and entry price
    are required; every other value is his own and stays empty when he leaves it out."""
    symbol: str = Field(min_length=1, max_length=50)
    exchange: str | None = Field(default=None, max_length=20)
    instrument_kind: str | None = Field(default=None, max_length=12)
    direction: str = Field(pattern=r"^(LONG|SHORT|long|short)$")
    quantity: float
    entry_time: datetime
    entry_price: float
    stop_loss: float | None = None
    target: float | None = None
    exit_time: datetime | None = None
    exit_price: float | None = None
    exit_reason: str | None = Field(default=None, max_length=100)
    charges: float | None = None
    setup: str | None = Field(default=None, max_length=100)
    timeframe: str | None = Field(default=None, max_length=10)
    area: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=20000)


@router.post("/manual", status_code=201)
async def add_manual_trade(body: ManualTradeBody, user: User = Depends(get_current_user),
                           session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Records a trade done OUTSIDE ATP in the trader's journal, marked as manual. Journal data only: it never creates
    an order or a position, never calls a broker and never enters the P&L books, risk counters or discipline score."""
    await _flag(session, user)
    try:
        row = await entries.add_manual(session, user, body.model_dump())
    except entries.EntryError as exc:
        raise _http(exc) from exc
    return await entries.manual_entry(session, user, row, await journal_settings.load(session, user))


@router.get("/manual")
async def list_manual_trades(limit: int = Query(default=100, ge=1, le=500), user: User = Depends(get_current_user),
                             session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """The trader's manual (outside-ATP) trades, newest first."""
    await _flag(session, user)
    s = await journal_settings.load(session, user)
    rows = await entries.list_manual(session, user, limit=limit)
    files = await entries.attachments_of(session, user, [entries.Ref(manual=r) for r in rows])
    return {"entries": [entries.manual_payload(r, s, attachments=files.get(("manual", r.id), [])) for r in rows]}


@router.get("/manual/{manual_id}")
async def get_manual_trade(manual_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    ref = await _ref(session, user, "manual", manual_id)
    assert ref.manual is not None
    return await entries.manual_entry(session, user, ref.manual, await journal_settings.load(session, user))


@router.delete("/manual/{manual_id}")
async def delete_manual_trade(manual_id: int, user: User = Depends(get_current_user),
                              session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Removes a manual entry the trader added (with its images). ATP trades cannot be removed from the journal."""
    ref = await _ref(session, user, "manual", manual_id)
    assert ref.manual is not None
    await entries.delete_manual(session, user, ref.manual)
    return {"deleted": manual_id}
