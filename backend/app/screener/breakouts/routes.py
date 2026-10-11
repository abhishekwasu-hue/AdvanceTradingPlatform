"""U6 BK1 / BK2: the breakout screens API - the catalog (style / timeframe selector, builder blocks, templates with their ScreenQL
text, every parameter with its default and research note) and a run over server bars with the quality overlay.

Behind `screener_v2` AND `screener_breakouts` (both off by default). Bars come through the organisation's broker session
(the same `fetch_frames` as a screen run; never bars a browser posted), closed bars only. Results are matches with a
grade and the reasons, never advice; nothing here places, changes or blocks an order.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_trader
from app.db.models import User
from app.db.session import get_session
from app.platform.controls import require_flag
from app.screener.breakouts import gate, quality, style, templates
from app.screener.breakouts import params as bp
from app.screener.breakouts import session as ses
from app.screener.breakouts.screens import INTRADAY_SCREENS, SCREEN_TIMEFRAMES, SCREENS
from app.screener.routes import fetch_frames, run_rate_limit

router = APIRouter(prefix="/api/screener/breakouts", tags=["screener"])

MAX_SYMBOLS = 50
SIGNAL_HISTORY_BARS = 300          # D1 swings settle by ~250 bars (registry SWING_DEGREE_MIN_BARS) plus the range window
FETCH_TF = {"2h": "1h", "4h": "1h"}  # screenql/1 has no 2h / 4h: fetched as 1h and resampled (09:15-anchored)


class RunBody(BaseModel):
    template_id: Optional[str] = None
    screen: Optional[str] = Field(default=None, description="B1 / B2 / B4 / B7 / B13 / B14 / B16 / B17 / B18 when no template is given")
    signal_tf: Optional[str] = Field(default=None, pattern=r"^(1m|3m|5m|15m|30m|1h|2h|4h|1d|1w)$")
    params: Dict[str, Union[float, int, str, bool, Dict[str, List[str]]]] = Field(default_factory=dict)
    symbols: List[str] = Field(min_length=1, max_length=MAX_SYMBOLS)
    exchange: str = Field(default="NSE", pattern=r"^(NSE|BSE|NFO|BFO|MCX|CDS)$")
    within: int = Field(default=1, ge=1, le=500, description="signals on the last N closed bars (1 = the last bar, a screen run)")


async def _flags(session: AsyncSession, user: User) -> None:
    await require_flag(session, "screener_v2", user.tenant_id)
    await require_flag(session, gate.FLAG, user.tenant_id)


@router.get("/catalog")
async def catalog(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _flags(session, user)
    return {**templates.catalog(), "screens": dict(SCREENS), "disclaimer": quality.DISCLAIMER}


def _resolve(body: RunBody) -> Dict[str, Any]:
    if body.template_id is not None:
        t = templates.BY_ID.get(body.template_id)
        if t is None:
            raise HTTPException(status_code=404, detail="Template not found")
        screen, signal_tf, overrides = t.screen, body.signal_tf or t.signal_tf, templates.template_params(t, body.params)
    elif body.screen is not None and body.signal_tf is not None:
        screen, signal_tf, overrides = body.screen, body.signal_tf, dict(body.params)
    else:
        raise HTTPException(status_code=422, detail="give a template_id, or a screen and a signal_tf")
    if screen not in SCREENS:
        raise HTTPException(status_code=422, detail=f"unknown screen {screen!r}; one of {', '.join(SCREENS)}")
    allowed = SCREEN_TIMEFRAMES.get(screen)
    if allowed and signal_tf not in allowed:
        raise HTTPException(status_code=422, detail=f"{screen} runs on {' / '.join(allowed)} bars, not {signal_tf}")
    clean, errors = bp.validate(overrides)
    if errors:
        raise HTTPException(status_code=422, detail={"message": "invalid breakout parameters", "problems": errors})
    try:
        context = style.context_timeframes(signal_tf, clean["context_ladder"])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"screen": screen, "signal_tf": signal_tf, "params": overrides, "context": context}


def history_bars(screen: str, signal_tf: str, params: Dict[str, Any]) -> int:
    """Signal bars to fetch. U6 BK2: an intraday screen needs whole sessions - the time-of-day average reads the same slot
    in the previous `tod_sessions` sessions - so it asks for that many sessions plus the current one."""
    if screen not in INTRADAY_SCREENS:
        return SIGNAL_HISTORY_BARS
    p, _ = bp.validate(params)
    per_session = -(-bp.TF_MINUTES["1d"] // bp.TF_MINUTES[signal_tf])
    return max(SIGNAL_HISTORY_BARS, (int(p["tod_sessions"]) + 1) * per_session)


async def _frames(session: AsyncSession, user: User, symbols: List[str], exchange: str, signal_tf: str,
                  context: tuple, bars: int = SIGNAL_HISTORY_BARS) -> tuple:
    """Signal-timeframe bars per symbol plus daily bars when a context timeframe is daily or coarser (a deep daily history
    instead of a resample of the short intraday window). Closed bars only."""
    fetch_tf = FETCH_TF.get(signal_tf, signal_tf)
    universe, problems, source = await fetch_frames(session, user.tenant_id, symbols, exchange, fetch_tf, {fetch_tf: bars})
    frames: Dict[str, Dict[str, pd.DataFrame]] = {}
    for d in universe:
        f = d.frames[fetch_tf]
        frames[d.symbol] = {signal_tf: style.resample(f, fetch_tf, signal_tf) if fetch_tf != signal_tf else f}
    daily_ctx = [tf for tf in context if bp.TF_MINUTES[tf] >= bp.TF_MINUTES["1d"]]
    if daily_ctx and bp.TF_MINUTES[signal_tf] < bp.TF_MINUTES["1d"] and frames:
        daily, _, _ = await fetch_frames(session, user.tenant_id, list(frames), exchange, "1d", {"1d": SIGNAL_HISTORY_BARS})
        for d in daily:
            for tf in daily_ctx:
                frames[d.symbol][tf] = style.resample(d.frames["1d"], "1d", tf)
    return frames, problems, source


@router.post("/run", dependencies=[Depends(run_rate_limit)])
async def run(body: RunBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """One breakout screen (a template or screen + timeframe + parameters) over up to 50 symbols, graded."""
    await _flags(session, user)
    spec = _resolve(body)
    symbols = list(dict.fromkeys(s.strip().upper() for s in body.symbols if s.strip()))
    calendar = None
    if spec["screen"] in INTRADAY_SCREENS:                                   # U6 BK2: the exchange's session clock
        try:
            calendar = ses.calendar_for(body.exchange)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    bars = history_bars(spec["screen"], spec["signal_tf"], spec["params"])
    frames, problems, source = await _frames(session, user, symbols, body.exchange, spec["signal_tf"], spec["context"], bars)
    rows: List[Dict[str, Any]] = []
    reasons: Dict[str, str] = dict(problems)
    notes: List[str] = []
    for symbol, fr in frames.items():
        try:
            result = quality.run_breakout(spec["screen"], fr, spec["signal_tf"], params=spec["params"], within=body.within, calendar=calendar)
        except ValueError as exc:
            reasons[symbol] = str(exc)
            continue
        notes += [f"{symbol}: {x}" for x in result.notes]
        rows += [{"symbol": symbol, **r} for r in result.signals]
        if result.excluded:
            reasons[symbol] = f"{result.excluded} signal(s) below the liquidity floor"
    text, why = templates.screenql(spec["screen"], spec["signal_tf"], spec["params"])
    return {"screen": spec["screen"], "signal_tf": spec["signal_tf"], "context_tfs": list(spec["context"]), "style": style.style_of(spec["signal_tf"]),
            "screenql": text, "engine_only_reason": why, "data_source": source, "scanned": len(symbols), "rows": rows,
            "columns": list(templates.RESULT_COLUMNS) + (list(templates.INTRADAY_COLUMNS) if spec["screen"] in INTRADAY_SCREENS else []),
            "problems": reasons, "notes": notes[:50], "as_of": datetime.now(timezone.utc).isoformat(),
            "disclaimer": quality.DISCLAIMER}


__all__ = ["router"]
