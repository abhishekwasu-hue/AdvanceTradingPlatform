"""Handover 11c-1 (AMW-20, first slice): the Pine Script subset API, behind the `strategy_lab_pine` flag (404 while off).

* `GET  /api/lab/pine/templates` - five Pine snippets that translate cleanly (signal conditions only).
* `POST /api/lab/pine/translate` - Pine text -> ScreenQL signal conditions + line-numbered diagnostics. Nothing runs and
  nothing is stored; the Pine text is never executed (it is parsed, and ScreenQL nodes are built from a fixed table).
* `POST /api/lab/pine/run` - "Run as screen": the chosen signal's ScreenQL goes through the Screener's own run path
  (`app.screener.routes.run`: the same validation and flag gates as a user's screen, server bars only, the run stored
  under the caller's organisation, the same per-user run limit). It needs the Screener (`screener_v2`) as well.

Backtest, paper forward test and alerts from a Pine source reuse the screen's paths in later slices (handover 11d).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_trader
from app.core.rate_limit import user_rate_limit
from app.db.models import User
from app.db.session import get_session
from app.lab.pine.templates import TEMPLATES
from app.lab.pine.tokens import MAX_SOURCE
from app.lab.pine.translate import FLAG, Translation, translate

router = APIRouter(prefix="/api/lab/pine", tags=["strategy-lab"])

TRANSLATE_LIMIT, TRANSLATE_WINDOW = 30, 60
translate_rate_limit = user_rate_limit("lab_pine_translate", limit=TRANSLATE_LIMIT, window_seconds=TRANSLATE_WINDOW)
TF_PATTERN = r"^(1m|3m|5m|15m|30m|1h|1d|1w|1M)$"
NOTE = ("Translated from the Pine Script subset; the script itself is never run. Values follow ATP's own indicator code, "
        "so they match ATP's charts, screens and backtests and converge to, but are not promised to equal, TradingView's.")


async def _enabled(session: AsyncSession, user: User) -> None:
    from app.platform.controls import flag_enabled
    if not await flag_enabled(session, FLAG, user.tenant_id):
        raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": FLAG})


class TranslateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1, max_length=MAX_SOURCE, description="The Pine Script text (the subset)")
    base_tf: str = Field(default="1d", pattern=TF_PATTERN)


class RunBody(TranslateBody):
    signal: Optional[int] = Field(default=None, ge=0, le=50, description="Which signal to run (default: the primary one)")
    params: Dict[str, Union[float, int, bool]] = Field(default_factory=dict, max_length=50)
    symbols: List[str] = Field(min_length=1, max_length=50)
    exchange: str = Field(default="NSE", pattern=r"^(NSE|BSE|NFO|BFO|MCX|CDS)$")
    preview: bool = False


def _view(result: Translation) -> Dict[str, Any]:
    return {**result.as_dict(), "note": NOTE}


@router.get("/templates")
async def templates(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _enabled(session, user)
    return {"templates": TEMPLATES, "note": "Signal conditions only. Lots, stops, targets and exits come from your own settings."}


@router.post("/translate", dependencies=[Depends(translate_rate_limit)])
async def translate_pine(body: TranslateBody, user: User = Depends(get_current_user),
                         session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Pine subset text -> signals as ScreenQL, parameters and line-numbered diagnostics. Nothing runs, nothing is stored."""
    await _enabled(session, user)
    return _view(translate(body.source, base_tf=body.base_tf))


def _checked_params(result: Translation, used: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    """The run's parameter values: the script's defaults, with the caller's overrides checked against each input's type
    and minval / maxval (an unknown name or a value out of range is a 422)."""
    out = dict(used)
    problems: List[str] = []
    for name, value in overrides.items():
        spec = result.params.get(name)
        if spec is None or name not in used:
            problems.append(f"{name!r} is not an input of this signal")
            continue
        if spec.type == "bool":
            if not isinstance(value, bool):
                problems.append(f"{name!r} is true or false")
                continue
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                problems.append(f"{name!r} is a number")
                continue
            if spec.type == "int" and not float(value).is_integer():
                problems.append(f"{name!r} is a whole number")
                continue
            if (spec.min is not None and value < spec.min) or (spec.max is not None and value > spec.max):
                problems.append(f"{name!r} must be between {spec.min:g} and {spec.max:g}" if spec.min is not None and spec.max is not None
                                else f"{name!r} is out of its range")
                continue
            value = int(value) if spec.type == "int" else float(value)
        out[name] = value
    if problems:
        raise HTTPException(status_code=422, detail={"message": "the inputs are not valid", "problems": [{"message": m, "pos": None} for m in problems]})
    return out


@router.post("/run")
async def run_as_screen(body: RunBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Translate, pick the signal, then run its ScreenQL through the Screener's own run path (validation, gates, server
    bars, stored run). Charged as one Screener run on the caller's run limit."""
    await _enabled(session, user)
    from app.screener import routes as screener

    result = translate(body.source, base_tf=body.base_tf)
    if result.refused:
        raise HTTPException(status_code=422, detail={"message": "the script reads future bars (look-ahead), so it is not run",
                                                     "problems": [d.as_dict() for d in result.diagnostics if d.severity == "error"][:20]})
    index = body.signal if body.signal is not None else result.primary()
    if index is None or index >= len(result.signals):
        raise HTTPException(status_code=422, detail={"message": "no translated signal to run", "problems": []})
    chosen = result.signals[index]
    if not chosen.ok or chosen.text is None:
        raise HTTPException(status_code=422, detail={"message": "this signal did not translate", "problems": [{"message": m, "pos": None} for m in chosen.problems]})
    params = _checked_params(result, chosen.params, body.params)
    await screener.charge_runs(user, 1)
    run_body = screener.RunBody(source=chosen.text, base_tf=body.base_tf, params=params, symbols=body.symbols, exchange=body.exchange,
                                preview=body.preview)
    out = await screener.run(run_body, user=user, session=session)
    return {**out, "pine": {"signal": chosen.as_dict(index), "params": params, "note": NOTE}}


__all__ = ["router", "FLAG"]
