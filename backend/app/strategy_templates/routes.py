"""Strategy templates step 2: the list and "Adopt as a paper strategy", behind the flag `strategy_templates` (404 while off).

* `GET /api/strategy-templates` - the built-in templates as cards: name, idea, difficulty, style, the timeframes, the
  settings the form shows (label, help, default, bounds, choices in words) and the constraints' messages. `available`
  says whether every function the template reads is switched on for the caller's organisation (flag names are never sent).
* `POST /api/strategy-templates/{template_id}/adopt` - the caller's settings checked (plain messages) and turned into
  the PRE-FILL of the existing paper bot form: the strategy id that carries the settings, the candle timeframe, the
  holding, PAPER. It creates NOTHING: the user reviews the form and saves it (the deployment route), and nothing starts
  by itself. Templates generate signals only; every execution choice (lots, exits, options) stays in the form.

Nothing is stored here and no order path is reached.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.models import User
from app.db.session import get_session
from app.strategy_templates import ids, language, library
from app.strategy_templates.adapter import ENGINE_LABEL, TemplateStrategy
from app.strategy_templates.compile import TemplateError, compile_recipe, resolve_settings
from app.strategy_templates.schema import Template

FLAG = "strategy_templates"
FAMILY_NAMES: Dict[str, str] = {"A": "Price action", "B": "Breakout", "C": "Breakout and retest", "D": "Trend following",
                                "E": "Momentum and mean reversion", "F": "Option seller packs", "G": "Waves"}
FETCH_DEPENDENT_DEGREE = 2      # swings of this degree and larger depend on how much history is loaded (registry SC-14)

router = APIRouter(prefix="/api/strategy-templates", tags=["strategy-templates"])


class AdoptBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timeframe: Optional[str] = Field(default=None, max_length=4, description="the signal timeframe (one of the template's)")
    sides: Optional[Literal["long", "short", "both"]] = None
    params: Dict[str, Any] = Field(default_factory=dict, max_length=40)


async def _enabled(session: AsyncSession, user: User) -> None:
    from app.platform.controls import flag_enabled
    if not await flag_enabled(session, FLAG, user.tenant_id):
        raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": FLAG})


async def _available(session: AsyncSession, user: User, template: Template) -> bool:
    """Every flag the template's functions need (default settings, each allowed timeframe) is on for the organisation."""
    from app.platform.controls import flag_enabled
    needed: set = set()
    for tf in template.allowed_timeframes:
        try:
            needed |= compile_recipe(template, resolve_settings(template, tf)).required_flags
        except TemplateError:
            continue
    for flag in sorted(needed):
        if not await flag_enabled(session, flag, user.tenant_id):
            return False
    return True


def _param(spec: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {"key": spec.key, "label": spec.label, "help": spec.help, "type": spec.type, "default": spec.default}
    if spec.type in ("int", "float"):
        out.update(min=spec.min, max=spec.max, step=spec.step)
    if spec.choices:
        labels = spec.choice_labels or tuple(language.TIMEFRAME_WORDS.get(c, c) for c in spec.choices)
        out["choices"] = [{"value": c, "label": label} for c, label in zip(spec.choices, labels)]
    return out


def _card(template: Template, available: bool) -> Dict[str, Any]:
    runnable = [tf for tf in template.allowed_timeframes if tf in ENGINE_LABEL]      # TPL-Q7: weekly is scans only
    return {
        "id": template.id, "version": template.version, "name": template.name, "family": template.family,
        "family_name": FAMILY_NAMES[template.family], "idea": template.idea, "difficulty": template.difficulty,
        "style": template.style,
        "timeframes": [{"value": tf, "label": language.TIMEFRAME_WORDS[tf]} for tf in runnable],
        "default_timeframe": runnable[0] if runnable else None, "sides_default": template.sides_default,
        "params": [_param(p) for p in template.params],
        "rules": [c.message for c in template.constraints],
        "available": available,
    }


def _notes(template: Template, strategy: TemplateStrategy) -> List[str]:
    notes = ["Signals only: what to trade, how much and when to get out stay your own choices.",
             "Paper money only. Nothing starts until you confirm it."]
    degrees = [v for k, v in strategy.settings.params.items() if k.endswith("degree") and isinstance(v, int)]
    if any(d >= FETCH_DEPENDENT_DEGREE for d in degrees):
        notes.append("Larger swings depend on how much history is loaded, so an early signal can differ from a longer chart.")
    return notes


@router.get("")
async def list_templates(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _enabled(session, user)
    cards = [_card(t, await _available(session, user, t)) for t in sorted(library.templates(), key=lambda t: (t.family, t.id))]
    return {"templates": cards}


@router.post("/{template_id}/adopt")
async def adopt(template_id: str, body: AdoptBody, user: User = Depends(get_current_user),
                session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """The paper bot form's pre-fill for this template with the caller's settings (nothing is created)."""
    await _enabled(session, user)
    try:
        template = library.get(template_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="No such strategy template") from None
    if body.timeframe in template.allowed_timeframes and body.timeframe not in ENGINE_LABEL:
        raise HTTPException(status_code=422, detail={"problems": ["This timeframe is for scans only; pick an intraday or a Daily one"]})
    try:
        strategy_id, settings = ids.adopt(template, body.timeframe or next(
            (tf for tf in template.allowed_timeframes if tf in ENGINE_LABEL), None), body.sides, body.params)
        strategy = TemplateStrategy(template, settings)
    except TemplateError as exc:
        raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc
    if not await _available(session, user, template):
        raise HTTPException(status_code=409, detail={"problems": ["This strategy template is not switched on for your account yet"]})
    swing = strategy.label == "day"
    return {
        "template_id": template.id, "name": template.name, "strategy_id": strategy_id,
        "deployment": {"strategy_id": strategy_id, "timeframe": strategy.label, "holding": "SWING" if swing else "INTRADAY",
                       "mode": "PAPER"},
        "settings": {"timeframe": settings.timeframe, "sides": settings.sides, "params": settings.params},
        "history": {"bars": strategy.history_bars(), "days": strategy.history_days()},
        "notes": _notes(template, strategy),
    }


__all__ = ["router", "FLAG", "AdoptBody"]
