"""Z6: `/api/journal/*` - the trader's own journal, behaviour tags and discipline score (behind `discipline_engine`).

Personal: every route reads and writes only the logged-in trader's rows; another user's trade or tag answers 404,
exactly like one that does not exist. Read-only towards trading: nothing here places, changes or blocks an order.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.models import JournalTagRecord, User
from app.db.session import get_session
from app.journal import FLAG, capture, service
from app.journal import settings as journal_settings
from app.journal.review import advice_in as review_advice_in
from app.journal.settings import COMPONENT_TAGS, RANGES, TAGS, DisciplineSettings, SettingsError
from app.platform.controls import require_flag

router = APIRouter(prefix="/api/journal", tags=["journal"])

WEEK_PATTERN = r"^\d{4}-W\d{2}$"


async def _flag(session: AsyncSession, user: User) -> None:
    await require_flag(session, FLAG, user.tenant_id)


def _settings_payload(s: DisciplineSettings) -> Dict[str, Any]:
    return {"settings": s.to_dict(), "defaults": DisciplineSettings().to_dict(), "ranges": {k: list(v) for k, v in RANGES.items()},
            "components": {k: list(v) for k, v in COMPONENT_TAGS.items()}, "tags": list(TAGS)}


@router.get("/settings")
async def get_settings(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """The trader's thresholds and score weights, the documented defaults and each number's allowed range."""
    await _flag(session, user)
    return _settings_payload(await journal_settings.load(session, user))


@router.put("/settings")
async def put_settings(body: Dict[str, Any], user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Changes some settings (keys not sent keep their value). 422 names the first bad key."""
    await _flag(session, user)
    try:
        saved = await journal_settings.save(session, user, body)
    except SettingsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _settings_payload(saved)


def _week_or_422(week: Optional[str]) -> Optional[str]:
    if week is None:
        return None
    try:
        service.week_bounds(week)
    except service.WeekError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return week


@router.get("/week")
async def journal_week(week: Optional[str] = Query(default=None, pattern=WEEK_PATTERN), user: User = Depends(get_current_user),
                       session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """One IST week of the trader's journal (default: this week): every trade's plan vs execution, fills, costs,
    MAE / MFE, holding time, chart references, Greeks at entry for options, its behaviour tags - and the week's
    discipline score with its components. Detectors run on every read, so new trades and changed settings show at once."""
    await _flag(session, user)
    data = await service.refresh_week(session, user, _week_or_422(week))
    tags_by_trade: Dict[int, List[JournalTagRecord]] = {}
    for tag in data["tags"]:
        tags_by_trade.setdefault(tag.trade_id, []).append(tag)
    return {"week": data["week"], "start": data["start"].isoformat(), "end": data["end"].isoformat(),
            "entries": [service.entry_dict(t, data["entries"].get(t.id), tags_by_trade.get(t.id, [])) for t in data["trades"]],
            "score": data["score"]}


REVIEW_QUESTION = ("Explain this weekly trading review in plain language: what the numbers show about the week and which "
                   "behaviours the losses came with. Describe only; do not advise any trade, security or action.")


@router.get("/review")
async def weekly_review(http_request: Request, week: Optional[str] = Query(default=None, pattern=WEEK_PATTERN), history: int = Query(default=4, ge=0, le=12),
                        narrate: bool = Query(default=False, description="ask the organisation's AI provider to explain the numbers (checked)"),
                        user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Z6-b: the weekly review - every number from the trader's own journal (totals, setups, behaviour costs, the
    discipline score, the trader's own previous weeks), with plain-language lines that describe them. `narrate=true`
    asks the Copilot to explain the same facts: its text must use only those numbers and pass the advice filter, else
    the lines stay the explanation. Never a leaderboard: only this trader's weeks."""
    await _flag(session, user)
    review = await service.weekly_review(session, user, _week_or_422(week), history_weeks=history)
    if review["totals"]["trades"]:
        from app.growth import activation  # AUD-G2.4: the week's rows are committed by the review itself
        await activation.record_step(session, user, "first_journal_review")
    explanation: Dict[str, Any] = {"source": "rules", "text": "\n".join(review["lines"])}
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


async def _narrate(session: AsyncSession, user: User, facts: List[str]) -> Dict[str, Any]:
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
async def journal_trade(trade_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _flag(session, user)
    trade = await service.own_trade(session, user, trade_id)
    if trade is None:
        raise HTTPException(status_code=404, detail="No such trade")
    entries = await capture.ensure_entries(session, [trade])
    await session.commit()
    from app.growth import activation  # AUD-G2.4
    await activation.record_step(session, user, "first_journal_review")
    tags = list(await session.scalars(select(JournalTagRecord).where(JournalTagRecord.trade_id == trade.id, JournalTagRecord.user_id == user.id)
                                      .order_by(JournalTagRecord.tag)))
    return service.entry_dict(trade, entries.get(trade.id), tags)


class TagBody(BaseModel):
    tag: str = Field(min_length=2, max_length=30)
    note: Optional[str] = Field(default=None, max_length=300)


@router.post("/trades/{trade_id}/tags", status_code=201)
async def add_tag(trade_id: int, body: TagBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
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
async def decide_tag(tag_id: int, body: TagDecision, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Confirm or dismiss a tag (SUGGESTED puts it back to undecided). A dismissed tag never counts in the score."""
    await _flag(session, user)
    row = await service.set_tag_status(session, user, tag_id, body.status)
    if row is None:
        raise HTTPException(status_code=404, detail="No such tag")
    return service.tag_dict(row)
