"""Strategy templates step 2: the list and "Adopt as a paper strategy", behind the flag `strategy_templates` (404 while off).

* `GET /api/strategy-templates` - the built-in templates as cards: name, idea, difficulty, style, the timeframes, the
  settings the form shows (label, help, default, bounds, choices in words) and the constraints' messages. `available`
  says whether every function the template reads is switched on for the caller's organisation (flag names are never sent).
* `GET /api/strategy-templates/{template_id}/how-it-works` - M94 F15 (family H, behind `intraday_pack`): the template's
  one-screen "how it works" - short lines from its own data, a schematic drawn from its synthetic test series, its setup
  scanner, its settings and the footer (app/strategy_templates/pages.py).
* `POST /api/strategy-templates/{template_id}/overlay` - M94 F16 (family H, behind `intraday_pack`): one stock's chart
  overlay of the template with the caller's settings - each compiled part (setup / trigger / invalidation) through the
  ONE shared M91 evaluation (`watch_screen.screen_marks`), and the engine's own signals in view.
* `POST /api/strategy-templates/{template_id}/adopt` - the caller's settings checked (plain messages) and turned into
  the PRE-FILL of the existing trading bot form: the strategy id that carries the settings, the candle timeframe, the
  holding, PAPER. It creates NOTHING: the user reviews the form and saves it (the deployment route), and nothing starts
  by itself. Templates generate signals only; every execution choice (lots, exits, options) stays in the form.

Nothing is stored here and no order path is reached.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_trader
from app.db.models import User
from app.db.session import get_session
from app.strategy_templates import exits as exit_settings
from app.strategy_templates import ids, language, library, packs, pages
from app.strategy_templates.adapter import ENGINE_LABEL, TemplateStrategy
from app.strategy_templates.compile import TemplateError, compile_recipe, resolve_settings
from app.strategy_templates.schema import Template

FLAG = "strategy_templates"
FAMILY_NAMES: Dict[str, str] = {"A": "Price action", "B": "Breakout", "C": "Breakout and retest", "D": "Trend following",
                                "E": "Momentum and mean reversion", "F": "Option seller packs", "G": "Waves",
                                "H": "Intraday", "J": "Volume spread", "K": "Wyckoff", "L": "Fibonacci + Elliott", "M": "Price patterns"}
FETCH_DEPENDENT_DEGREE = 2      # swings of this degree and larger depend on how much history is loaded (registry SC-14)
# A family that is listed only while its own flag is on as well (family G, the wave templates: `screener_waves`). While
# that flag is off its templates are not shown and cannot be adopted (404), not merely shown as unavailable.
# M94 (F2): family H "Intraday" is listed only while `intraday_pack` is on.
# M99 P3: family J "Volume spread" is listed only while `vsa_pack` is on.
# M99 P4: family K "Wyckoff" is listed only while `wyckoff_pack` is on.
# M99 P5: family L "Fibonacci + Elliott" is listed only while `fib_ew_pack` is on.
# M99 P6: family M "Price patterns" is listed only while `pa_pack` is on.
FAMILY_FLAGS: Dict[str, str] = {"G": "screener_waves", "H": "intraday_pack", "J": "vsa_pack", "K": "wyckoff_pack", "L": "fib_ew_pack",
                                "M": "pa_pack"}
# M94 (F8): the label of a template whose defaults are reference values from a study (a label only; nothing is enforced)
REFERENCE_LABEL = "Reference values - edit before use"
# M94-Q7: an intraday template carries no time-of-day rule; the optional window is the user's own operational setting
WINDOW_NOTE = ("Only between: Not set - the whole session. Your own New entries from / until (Settings › Trade settings) "
               "applies if you set it.")

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


async def _listed(session: AsyncSession, user: User, template: Template) -> bool:
    """The template is shown at all: its family's own flag (FAMILY_FLAGS) is on for the organisation, or it has none."""
    from app.platform.controls import flag_enabled
    flag = FAMILY_FLAGS.get(template.family)
    return flag is None or await flag_enabled(session, flag, user.tenant_id)


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


def _reads(template: Template, default_timeframe: Optional[str]) -> Dict[str, Optional[str]]:
    """The sentence a signal of this template says, per side, built from the template's own tokens with the default
    settings. None for a side the template does not use, when no timeframe can run, or when the sentence fails the guard."""
    out: Dict[str, Optional[str]] = {"long": None, "short": None}
    if default_timeframe is None:
        return out
    try:
        _, settings = ids.adopt(template, default_timeframe, template.sides_default, {})
    except TemplateError:
        return out
    sides = ("long", "short") if template.sides_default == "both" else (template.sides_default,)
    for side in sides:
        text = library.signal_text(template, settings, side)
        out[side] = text if text and not language.guard(text) else None
    return out


def _card(template: Template, available: bool) -> Dict[str, Any]:
    runnable = [tf for tf in template.allowed_timeframes if tf in ENGINE_LABEL]      # TPL-Q7: weekly is scans only
    return {
        "id": template.id, "version": template.version, "name": template.name, "family": template.family,
        "family_name": FAMILY_NAMES[template.family], "idea": template.idea, "difficulty": template.difficulty,
        "style": template.style,
        "timeframes": [{"value": tf, "label": language.TIMEFRAME_WORDS[tf]} for tf in runnable],
        "default_timeframe": runnable[0] if runnable else None, "sides_default": template.sides_default,
        "reads": _reads(template, runnable[0] if runnable else None),
        "params": [_param(p) for p in template.params],
        "rules": [c.message for c in template.constraints],
        "available": available,
        # M94 (F8): shown as REFERENCE_LABEL in the picker and the form; family H is listed only with `intraday_pack` on
        "reference_values": template.reference_values,
    }


def _notes(template: Template, strategy: TemplateStrategy) -> List[str]:
    notes = ["Signals only: what to trade, how much and when to get out stay your own choices.",
             "Paper money only. Nothing starts until you confirm it."]
    if packs.is_pack(template):
        notes.append(packs.STRUCTURE_NOTE)
    if template.reference_values:
        notes.append(f"{REFERENCE_LABEL}.")
    if template.family == "H":
        notes.append(WINDOW_NOTE)
    degrees = [v for k, v in strategy.settings.params.items() if k.endswith("degree") and isinstance(v, int)]
    if any(d >= FETCH_DEPENDENT_DEGREE for d in degrees):
        notes.append("Larger swings depend on how much history is loaded, so an early signal can differ from a longer chart.")
    return notes


@router.get("")
async def list_templates(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _enabled(session, user)
    cards = [_card(t, await _available(session, user, t)) for t in sorted(library.templates(), key=lambda t: (t.family, t.id))
             if await _listed(session, user, t)]
    return {"templates": cards}


@router.post("/{template_id}/adopt")
async def adopt(template_id: str, body: AdoptBody, user: User = Depends(get_current_user),
                session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """The trading bot form's pre-fill for this template with the caller's settings (nothing is created)."""
    await _enabled(session, user)
    try:
        template = library.get(template_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="No such strategy template") from None
    if not await _listed(session, user, template):
        raise HTTPException(status_code=404, detail="No such strategy template")
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
        "exits": _exit_choices(strategy),
    }


def _exit_choices(strategy: TemplateStrategy) -> Dict[str, Any]:
    """Section 5: what the trading bot form may offer as this template's exits (with these settings), per instrument
    (the first choice of each list is the default: the behaviour before section 5). The form shows them in words; the
    deployment route checks the choice again."""
    caps = strategy.capabilities()
    precedence: Dict[str, Any] = {}
    for cls_, (stops, targets) in exit_settings.PRECEDENCE.items():
        offered = [t for t in targets if not ((t == "TEMPLATE_EXIT" and not caps.exit_signal)
                                              or (t == "WAVE_PROJECTION" and not caps.wave_projection)
                                              or (t == "TEMPLATE_LEVEL" and not caps.levels))]
        precedence[cls_] = {"stops": list(stops), "targets": offered}
    return {
        "precedence": precedence, "exit_signal": caps.exit_signal, "wave_projection": caps.wave_projection,
        "base_waves": [{"value": b, "label": exit_settings.base_wave_words(b)} for b in exit_settings.base_waves()] if caps.wave_projection else [],
        "target_r": {"min": exit_settings.TARGET_R_BOUNDS[0], "max": exit_settings.TARGET_R_BOUNDS[1]},
        "ratio": {"min": exit_settings.RATIO_BOUNDS[0], "max": exit_settings.RATIO_BOUNDS[1]},
        # M94 F10: the levels a TEMPLATE_LEVEL target may sit at (empty: the rule is not offered)
        "levels": [{"value": lv, "label": exit_settings.LEVEL_WORDS[lv]} for lv in caps.levels],
        # M99 P2: the trade-management options (None: not offered by this template); all off by default
        "management": _management_choices() if caps.management else None,
    }


def _management_choices() -> Dict[str, Any]:
    from app.strategy_templates import management as mg
    return {
        "classes": sorted(exit_settings.MANAGEMENT_CLASSES),
        "trails": [{"value": "SWING", "label": "By the swings"}, {"value": "ATR", "label": "By ATR from the best price"},
                   {"value": "MA", "label": "At a moving average"}],
        "averages": list(mg.AVERAGES),
        "bounds": {"trail_degree": list(mg.DEGREE_BOUNDS),
                   "trail_n": list(mg.N_BOUNDS), "trail_k": list(mg.K_BOUNDS), "bars": list(mg.BARS_BOUNDS)},
        "reference": dict(mg.REFERENCE), "reference_label": mg.REF_LABEL,
    }


# --------------------------------------------------------------------------------------- M94 PR-6: how it works, overlay
class OverlayBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(min_length=1, max_length=60)
    exchange: str = Field(default="NSE", pattern=r"^(NSE|BSE|NFO|BFO|MCX|CDS)$")
    timeframe: str | None = Field(default=None, max_length=4, description="the signal timeframe (one of the template's)")
    sides: Literal["long", "short", "both"] | None = None
    params: dict[str, Any] = Field(default_factory=dict, max_length=40)


async def _paged(session: AsyncSession, user: User, template_id: str) -> Template:
    """A template with a "how it works" screen and an overlay (family H): listed for the caller (its flag on), else 404."""
    await _enabled(session, user)
    try:
        template = library.get(template_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="No such strategy template") from None
    if not pages.has_page(template) or not await _listed(session, user, template):
        raise HTTPException(status_code=404, detail="No such strategy template")
    return template


@router.get("/{template_id}/how-it-works")
async def how_it_works(template_id: str, user: User = Depends(get_current_user),
                       session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """F15: the template's one screen, every word from its own data (pages.py); nothing is stored or started."""
    import asyncio
    template = await _paged(session, user, template_id)
    available = await _available(session, user, template)
    card = _card(template, available)
    exits: dict[str, Any] | None = None
    if card["default_timeframe"]:
        try:
            _, settings = ids.adopt(template, card["default_timeframe"], template.sides_default, {})
            exits = _exit_choices(TemplateStrategy(template, settings))
        except (TemplateError, ValueError):
            exits = None
    drawing = await asyncio.to_thread(pages.schematic, template)
    return {"template": card, "lines": pages.lines(template, card["reads"], exits), "schematic": drawing,
            "scanner": pages.scanner(template) if available else {"long": None, "short": None},
            "reference_label": REFERENCE_LABEL if template.reference_values else None, "window_note": WINDOW_NOTE,
            "footer": pages.FOOTER}


@router.post("/{template_id}/overlay")
async def overlay(template_id: str, body: OverlayBody, user: User = Depends(require_trader),
                  session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """F16: the template drawn on one stock's chart - each part through the shared screen evaluation (pages.py)."""
    import asyncio

    from app.db.session import release_connection
    from app.screener import evidence as E
    from app.screener import ranking as ranking_gate
    from app.screener import routes as R
    from app.screener import watch_screen as W
    from app.screener.validator import validate_study
    from app.strategy_templates import engine
    template = await _paged(session, user, template_id)
    try:
        settings = resolve_settings(template, body.timeframe or next(
            (tf for tf in template.allowed_timeframes if tf in ENGINE_LABEL), None), body.sides, body.params)
        recipe = compile_recipe(template, settings)
        parts = pages.overlay_parts(recipe)
        loading = pages.loading_part(recipe, parts)
    except TemplateError as exc:
        raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc
    if not await _available(session, user, template):
        raise HTTPException(status_code=409, detail={"problems": ["This strategy template is not switched on for your account yet"]})
    tf = settings.timeframe
    validated = validate_study(loading.ast, base_tf=tf, params=loading.params).validated
    # the bars every part of the recipe reads (a context on the Daily bars included), so the engine's signals are complete
    for frame_tf, bars in {**recipe.lookback, **recipe.fetch_lookback}.items():
        validated.fetch_lookback[frame_tf] = max(validated.fetch_lookback.get(frame_tf, 0), bars, recipe.lookback.get(frame_tf, 0))
    await R._gate(session, user, loading.ast, validated, loading.params, body.exchange)
    if not validated.ok:
        raise HTTPException(status_code=422, detail={"problems": [p.message for p in validated.problems]})
    symbol = body.symbol.strip().upper()
    store = await R.store_first(session, user.tenant_id)
    universe, problems, data_source, systems = await R.load_universe(session, user, loading.ast, validated, loading.params, [symbol],
                                                                     body.exchange, tf, {}, release=True, budget={}, store=store, pending=[])
    await release_connection(session)
    head = {"template": {"id": template.id, "name": template.name}, "symbol": symbol, "exchange": body.exchange, "base_tf": tf,
            "data_source": data_source, "footer": pages.FOOTER}
    data = next((d for d in universe if d.symbol == symbol), None)
    if data is None:
        why = problems.get(symbol)
        return {**head, "available": False, "reason": str(why) if why else "No closed bars for this stock yet."}

    def build() -> dict[str, Any]:
        out = []
        for p in parts:
            entry: dict[str, Any] = {"part": p.name, "side": p.side, "label": p.label}
            if p.part is None:
                out.append({**entry, "kept": True, "matched": None, "reason": None, "rows": [], "hits": []})
                continue
            check = validate_study(p.part.ast, base_tf=tf, params=p.part.params).validated
            marks = W.screen_marks(p.part.ast, check, data, base_tf=tf, params=p.part.params, bars=W.OVERLAY_BARS)
            base = data.frames[tf]
            tail = base.index[-len(marks["hits"]):] if marks["hits"] else base.index[:0]
            out.append({**entry, "kept": False, "matched": marks["matched"], "reason": str(marks["reason"]) if marks["reason"] else None,
                        "rows": marks["rows"], "hits": [E._iso(t) for t, h in zip(tail, marks["hits"], strict=False) if h]})
        series = engine.compute(recipe, dict(data.frames), symbol)
        index = data.frames[tf].index
        signals = [{**s, "bar": E._iso(s["bar"]), "setup_bar": E._iso(s["setup_bar"])}
                   for s in pages.signals_in(series, index, W.OVERLAY_BARS)]
        return {"parts": out, "signals": signals, "as_of": E._iso(index[-1])}

    with ranking_gate.active(systems):
        got = await asyncio.to_thread(build)
    return {**head, "available": True, **got}


__all__ = ["router", "FLAG", "AdoptBody", "OverlayBody", "FAMILY_FLAGS", "REFERENCE_LABEL", "WINDOW_NOTE"]
