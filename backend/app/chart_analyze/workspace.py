"""M75 W-4 (owner order M75 CW-1 / CW-2; finding W8; M75Q-5 default confirmed by the owner on 1 Nov): the AI card of the
Copilot's chart workspace - Analyze a chart's hybrid mode on a chart the user did not upload.

POST /api/chart-analyze/workspace  {"symbol": "NIFTY", "timeframe": "15m"}

* The code reading always comes first and costs nothing (W-3, `GET /api/charts/read`); this route adds the AI card.
* It is Analyze a chart's hybrid run, reused unchanged (`service.analyze` -> `_run_hybrid`): ATP's own reading of the
  stored candles, drawn by code on ATP's own rendered chart, and - only when the private engine has the hybrid entry
  point - its AI part on those generic facts and that picture (never a price read from a picture). The run is a normal
  hybrid item of the user (source "workspace", the rendered chart as its picture), so the plan's daily / monthly
  analysis counts, the cost cap and the AI key (his own or the organisation's) apply exactly as on Analyze a chart.
* Once per (user, symbol, timeframe, last closed bar): a finished item for the same pick whose reading is as of the same
  closed bar is reused by every later open (no second model call); a run already going for it is reported, not repeated.
* Every number on the card must be one of the facts' numbers (`ai.grounding.check_numbers`); a line with any other number
  is withheld. The contract's advice filter already ran on every line (`contract.validate`).
* Any cap, missing key, missing engine part or missing data: the card is not shown and one line says why; the code
  reading stands on its own.
Never in the answer: the provider, the model, the engine's name / version, the cost or an id of the private package.
Behind `ui_v2` + `copilot_workspace` (404 while off) and Analyze a chart's `chart_analyze` + `chart_analyze_hybrid` (the card
says it is off). Read-only: nothing here places, blocks or changes an order (ADR-0006).
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import anyio
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.chart_analyze import FLAG, HYBRID_FLAG, contract, hybrid, imaging, limits, service
from app.chart_analyze import provider as engine_provider
from app.chart_analyze import settings as settings_mod
from app.db.models import ChartAnalysisRecord, Tenant, User
from app.db.session import get_session

router = APIRouter(prefix="/api/chart-analyze", tags=["chart-analyze"])
WORKSPACE_FLAG = "copilot_workspace"
SOURCE = "workspace"
OFF = "The AI part of the reading is off for your account; the code reading above is complete on its own."
NO_AI_PART = "The AI part is not available on this server yet; the code reading above is complete on its own."
NO_DATA = "The AI part needs ATP's stored candles for this chart; the code reading above is complete on its own."
RUNNING = "The AI part for this bar is being read already."
TRIED = ("The AI part could not run for this bar; it is tried again once the next bar closes. The code reading above is "
         "complete on its own.")
WITHHELD = "(A number in this line is not in ATP's reading, so the line is withheld.)"
# The short card (W8): the AI's words and the facts table's rows. Never the setup block (its trigger / invalidation
# references and setup names), the key levels or a scenario's invalidation price (M75Q-3) - the code reading draws the levels.
CARD_KEYS = ("story", "trend", "current_state", "rsi", "evidence", "scenarios", "what_to_wait_for")
SCENARIO_KEYS = ("primary", "alternative")


def short_card(ai: dict[str, Any]) -> dict[str, Any]:
    """The workspace's card from the hybrid AI block: CARD_KEYS only; scenarios as their two texts; what to watch as its
    condition text only (no alert numbers)."""
    card: dict[str, Any] = {k: ai[k] for k in CARD_KEYS if k in ai}
    if isinstance(card.get("scenarios"), dict):
        card["scenarios"] = {k: card["scenarios"][k] for k in SCENARIO_KEYS if k in card["scenarios"]}
    if isinstance(card.get("what_to_wait_for"), dict):
        card["what_to_wait_for"] = {"condition": card["what_to_wait_for"].get("condition")}
    if isinstance(card.get("evidence"), list):
        card["evidence"] = [{"name": e.get("name"), "present": bool(e.get("present"))} for e in card["evidence"] if isinstance(e, dict)]
    return card


class VisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(min_length=1, max_length=40)
    timeframe: str = Field(min_length=1, max_length=8)


# ---------------------------------------------------------------------------------------------------------------------
# Pure parts
# ---------------------------------------------------------------------------------------------------------------------
def ground(card: Any, allowed: set[str]) -> Any:
    """Every text of the card whose numbers are not all in `allowed` is withheld (the M72 grounding check, per line)."""
    from app.ai.grounding import check_numbers
    if isinstance(card, str):
        return card if check_numbers(card, allowed)[0] else WITHHELD
    if isinstance(card, dict):
        return {k: ground(v, allowed) for k, v in card.items()}
    if isinstance(card, list):
        return [ground(v, allowed) for v in card]
    return card


def allowed_numbers(result: dict[str, Any]) -> set[str]:
    """The numbers the card may use: the facts the engine received (ATP's reading of the stored candles) and ATP's own
    words for them (the timeframe labels)."""
    from app.ai.grounding import allowed_from_text, numbers_in_values
    reading: dict[str, Any] = result["reading"] if isinstance(result.get("reading"), dict) else {}
    width = int((result.get("chart") or {}).get("width") or 0)
    height = int((result.get("chart") or {}).get("height") or 0)
    facts = hybrid.facts(reading, width=width, height=height) if reading else {}
    words = " ".join([str(reading.get("timeframe_label") or ""), *[hybrid.tf_label(str(r.get("tf"))) for r in reading.get("table") or []]])
    return numbers_in_values(facts) | allowed_from_text(words)


def card_of(row: dict[str, Any], *, cached: bool) -> dict[str, Any]:
    """The answer for a finished workspace item: the AI card (grounded) or why there is none - public fields only."""
    done = row.get("hybrid") if isinstance(row.get("hybrid"), dict) else None
    if row.get("status") != service.DONE or done is None:
        return {"status": "failed", "message": "The AI part could not run this time; the code reading above is complete on its own.",
                "card": None, "notes": [], "as_of": None, "cached": cached, "disclaimer": contract.DISCLAIMER}
    ai = done.get("ai")
    card = ground(short_card(ai), allowed_numbers(done)) if isinstance(ai, dict) else None
    reading = done.get("reading") or {}
    return {"status": "ready" if card else "code_only", "message": None if card else (done.get("notes") or [NO_AI_PART])[0],
            "card": card, "notes": [n for n in done.get("notes") or [] if isinstance(n, str)][:4], "as_of": reading.get("as_of"),
            "label": done.get("label"), "cached": cached, "disclaimer": contract.DISCLAIMER}


def state(status: str, message: str, *, as_of: str | None = None) -> dict[str, Any]:
    return {"status": status, "message": message, "card": None, "notes": [], "as_of": as_of, "cached": False, "disclaimer": contract.DISCLAIMER}


def _pick_and_as_of(result_json: str | None) -> tuple[dict[str, Any] | None, str | None]:
    try:
        data = json.loads(result_json or "")
    except ValueError:
        return None, None
    if not isinstance(data, dict):
        return None, None
    if isinstance(data.get(hybrid.PICK_KEY), dict):
        return data[hybrid.PICK_KEY], None
    reading: dict[str, Any] = data["reading"] if isinstance(data.get("reading"), dict) else {}
    return (data.get("pick") if isinstance(data.get("pick"), dict) else None), reading.get("as_of")


# ---------------------------------------------------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------------------------------------------------
async def _same_bar(session: AsyncSession, user: User, pick: dict[str, str], as_of: str, since: datetime) -> ChartAnalysisRecord | None:
    """His workspace item for the same pick on the same closed bar: a finished one (reused), one still running, or one
    that could not run (a cap, a refusal) - so one bar costs at most one item of his plan. Only items made after that bar
    closed can be about it."""
    rows = (await session.execute(select(ChartAnalysisRecord).where(
        ChartAnalysisRecord.user_id == user.id, ChartAnalysisRecord.source == SOURCE, ChartAnalysisRecord.mode == "hybrid",
        ChartAnalysisRecord.status.in_((service.DONE, service.RUNNING, service.READY, service.FAILED)), ChartAnalysisRecord.created_at >= since)
        .order_by(ChartAnalysisRecord.id.desc()))).scalars().all()
    for row in rows:
        got, at = _pick_and_as_of(row.result_json)
        if got is None or got.get("symbol") != pick["symbol"] or got.get("timeframe") != pick["timeframe"]:
            continue
        if row.status != service.DONE or at == as_of:
            return row
    return None


async def run(session: AsyncSession, user: User, tenant: Tenant, settings: dict[str, Any], pick: dict[str, str], *,
              now: datetime, client: Any | None = None) -> dict[str, Any]:
    """The AI card for the pick as of its last closed bar (see the module text)."""
    from app.chart_analyze import reading_png
    engine = engine_provider.load()
    if not engine_provider.supports_hybrid(engine):
        return state("code_only", NO_AI_PART)
    try:
        reading = await hybrid.read(session, user, pick, now=now)
    except hybrid.ReadingUnavailable:
        return state("no_data", NO_DATA)
    as_of = reading.get("as_of")
    if not as_of:
        return state("no_data", NO_DATA)
    bar_end = datetime.fromisoformat(str(as_of))
    bar_end = bar_end if bar_end.tzinfo else bar_end.replace(tzinfo=UTC)
    await limits.lock_user(session, user)                    # one run per user and bar, even on two opens at once
    hit = await _same_bar(session, user, pick, str(as_of), bar_end)
    if hit is not None:
        if hit.status == service.RUNNING and not service.is_stale(hit, settings, now):
            await session.commit()
            return state("running", RUNNING, as_of=str(as_of))
        if hit.status == service.DONE:
            await session.commit()
            return card_of(service.as_dict(hit), cached=True)
        if hit.status in (service.READY, service.FAILED):
            await session.commit()
            return state("unavailable", TRIED, as_of=str(as_of))
    try:
        png = await anyio.to_thread.run_sync(reading_png.render, reading)
    except ValueError:
        return state("no_data", NO_DATA)
    clean = imaging.CleanImage(png=png, width=reading_png.WIDTH, height=reading_png.HEIGHT, sha256=hashlib.sha256(png).hexdigest())
    try:
        row = await service.add_item(session, user, tenant, settings, clean, mode="hybrid", source=SOURCE, source_host=None, now=now,
                                     pick=dict(pick))
        done = await service.analyze(session, user, tenant, settings, row.id, client=client, now=now)
    except limits.LimitReached as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        return state("limit", f"{detail.get('message') or 'Your plan limit is reached.'} The code reading above is complete on its own.",
                     as_of=str(as_of))
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        return state("unavailable", f"{detail.get('message') or 'The AI part could not run.'} The code reading above is complete on its own.",
                     as_of=str(as_of))
    return card_of(service.as_dict(done), cached=False)


# ---------------------------------------------------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------------------------------------------------
@router.post("/workspace")
async def workspace_card(body: VisionBody, user: User = Depends(get_current_user),
                         session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """The workspace chart's AI card, once per closed bar (see the module text)."""
    from app.chart_analyze.routes import _rate, _tenant
    from app.platform.controls import flag_enabled
    for name in ("ui_v2", WORKSPACE_FLAG):
        if not await flag_enabled(session, name, user.tenant_id):
            raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": name})
    if not (await flag_enabled(session, FLAG, user.tenant_id) and await flag_enabled(session, HYBRID_FLAG, user.tenant_id)):
        return state("off", OFF)
    try:
        pick = hybrid.check_pick(body.symbol, body.timeframe)
    except hybrid.PickError as exc:
        raise HTTPException(status_code=422, detail={"message": str(exc), "code": exc.code}) from None
    settings = await settings_mod.load(session)
    await _rate(user, settings)
    tenant = await _tenant(session, user)
    return await run(session, user, tenant, settings, pick, now=datetime.now(UTC))


__all__ = ["router", "run", "ground", "allowed_numbers", "card_of", "SOURCE", "WORKSPACE_FLAG"]
