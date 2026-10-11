"""U6 BK1: the breakout screens API - the catalog (style / timeframe selector, builder blocks, templates with their ScreenQL
text, every parameter with its default and research note) and a run over server bars with the quality overlay.

Behind `screener_v2` AND `screener_breakouts` (both off by default). Bars come through the organisation's broker session
(the same `fetch_frames` as a screen run; never bars a browser posted), closed bars only. Results are matches with a
grade and the reasons, never advice; nothing here places, changes or blocks an order.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_trader
from app.db.models import User
from app.db.session import get_session
from app.platform.controls import require_flag
from app.screener.breakouts import gate, quality, style, templates
from app.screener.breakouts import params as bp
from app.screener.breakouts.screens import SCREEN_TIMEFRAMES, SCREENS
from app.screener.routes import fetch_frames, run_rate_limit

router = APIRouter(prefix="/api/screener/breakouts", tags=["screener"])

MAX_SYMBOLS = 50
MAX_WORK = 2500                    # within x symbols: bars the overlay may grade per request (M8 - CPU, run off the event loop)
SYMBOL = re.compile(r"^[A-Z0-9&._:-]{1,40}$")   # the alert rules' symbol shape
HISTORY_NOTE = ("The run grades signals on the signal timeframe's fetch plus daily history for daily-or-coarser context; a ScreenQL"
                " screen or alert resamples its context from the screen's own fetch, so on a short intraday history its context"
                " trend can be unknown where the run reads one (open question BK1-5).")
SIGNAL_HISTORY_BARS = 300          # D1 swings settle by ~250 bars (registry SWING_DEGREE_MIN_BARS) plus the range window
FETCH_TF = {"2h": "1h", "4h": "1h"}  # screenql/1 has no 2h / 4h: fetched as 1h and resampled (09:15-anchored)


class RunBody(BaseModel):
    template_id: Optional[str] = None
    screen: Optional[str] = Field(default=None, description="B1 / B2 / B4 / B7 / B13 / B14 when no template is given")
    signal_tf: Optional[str] = Field(default=None, pattern=r"^(1m|3m|5m|15m|30m|1h|2h|4h|1d|1w)$")
    params: Dict[str, Union[float, int, str, bool, Dict[str, List[str]]]] = Field(default_factory=dict)
    symbols: List[str] = Field(min_length=1, max_length=MAX_SYMBOLS)
    exchange: str = Field(default="NSE", pattern=r"^(NSE|BSE|NFO|BFO|MCX|CDS)$")
    within: int = Field(default=1, ge=1, le=500, description="signals on the last N closed bars (1 = the last bar, a screen run)")
    include_htf_failed: bool = Field(default=False, description="also list signals that fail the template's higher-timeframe filter")

    @field_validator("symbols")
    @classmethod
    def _symbols(cls, v: List[str]) -> List[str]:
        out = [s.strip().upper() for s in v if s.strip()]
        bad = [s for s in out if not SYMBOL.fullmatch(s)]
        if bad or not out:
            raise ValueError(f"invalid symbol(s): {', '.join(bad[:5]) or 'none given'}")
        return out


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
    if allowed and signal_tf not in allowed:                         # L6: refused before any broker fetch
        raise HTTPException(status_code=422, detail=f"{screen} runs on {' / '.join(allowed)} bars, not {signal_tf}")
    clean, errors = bp.validate(overrides)
    if errors:
        raise HTTPException(status_code=422, detail={"message": "invalid breakout parameters", "problems": errors})
    try:
        context = style.context_timeframes(signal_tf, clean["context_ladder"])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"screen": screen, "signal_tf": signal_tf, "params": overrides, "context": context}


async def _frames(session: AsyncSession, user: User, symbols: List[str], exchange: str, signal_tf: str,
                  context: tuple, tier: Optional[Dict[str, Any]] = None) -> tuple:
    """Closed bars per symbol for the signal timeframe and every context timeframe.

    * A daily-or-coarser signal: one daily fetch; the signal and every context frame are built from the daily bars (L8: a
      weekly signal's monthly context comes from daily bars, not from weekly ones).
    * An intraday signal: the signal timeframe's fetch (1h for 2h / 4h); intraday context is resampled from it, and
      daily-or-coarser context is built from a separate daily fetch (deeper than the intraday window)."""
    tier = tier if tier is not None else {}                           # BT1b: the run's own user session, its data-tier badge
    daily_tf = bp.TF_MINUTES["1d"]
    frames: Dict[str, Dict[str, pd.DataFrame]] = {}
    if bp.TF_MINUTES[signal_tf] >= daily_tf:
        daily, problems, source = await fetch_frames(session, user.tenant_id, symbols, exchange, "1d", {"1d": SIGNAL_HISTORY_BARS},
                                                     user_id=user.id, tier_out=tier)
        for d in daily:
            base = d.frames["1d"]
            frames[d.symbol] = {tf: style.resample(base, "1d", tf) for tf in (signal_tf, *context)}
        return frames, problems, source
    fetch_tf = FETCH_TF.get(signal_tf, signal_tf)
    universe, problems, source = await fetch_frames(session, user.tenant_id, symbols, exchange, fetch_tf, {fetch_tf: SIGNAL_HISTORY_BARS},
                                                    user_id=user.id, tier_out=tier)
    for d in universe:
        f = d.frames[fetch_tf]
        frames[d.symbol] = {signal_tf: style.resample(f, fetch_tf, signal_tf) if fetch_tf != signal_tf else f}
    daily_ctx = [tf for tf in context if bp.TF_MINUTES[tf] >= daily_tf]
    if daily_ctx and frames:
        daily, daily_problems, _ = await fetch_frames(session, user.tenant_id, list(frames), exchange, "1d", {"1d": SIGNAL_HISTORY_BARS},
                                                      user_id=user.id)
        problems.update({s: f"daily context: {why}" for s, why in daily_problems.items() if s not in problems})
        for d in daily:
            for tf in daily_ctx:
                frames[d.symbol][tf] = style.resample(d.frames["1d"], "1d", tf)
    return frames, problems, source


def _grade_all(spec: Dict[str, Any], frames: Dict[str, Dict[str, pd.DataFrame]], within: int,
               include_htf_failed: bool) -> tuple:
    """CPU-bound: the screen and overlay for every symbol (run in a worker thread, never on the event loop)."""
    rows: List[Dict[str, Any]] = []
    reasons: Dict[str, str] = {}
    hidden = 0
    for symbol, fr in frames.items():
        try:
            result = quality.run_breakout(spec["screen"], fr, spec["signal_tf"], params=spec["params"], within=within)
        except ValueError as exc:
            reasons[symbol] = str(exc)
            continue
        for r in result.signals:
            if r["htf_pass"] or include_htf_failed:
                rows.append({"symbol": symbol, **r})
            else:
                hidden += 1
        if result.excluded:
            reasons[symbol] = f"{result.excluded} signal(s) below the liquidity floor"
    return rows, reasons, hidden


@router.post("/run", dependencies=[Depends(run_rate_limit)])
async def run(body: RunBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """One breakout screen (a template or screen + timeframe + parameters) over up to 50 symbols, graded."""
    await _flags(session, user)
    spec = _resolve(body)
    symbols = list(dict.fromkeys(body.symbols))
    if body.within * len(symbols) > MAX_WORK:
        raise HTTPException(status_code=422, detail=f"within x symbols is at most {MAX_WORK} per run ({body.within} x {len(symbols)})")
    tier: Dict[str, Any] = {}
    frames, problems, source = await _frames(session, user, symbols, body.exchange, spec["signal_tf"], spec["context"], tier)
    rows, graded, hidden = await run_in_threadpool(_grade_all, spec, frames, body.within, body.include_htf_failed)
    reasons: Dict[str, str] = {**dict(problems), **graded}
    text, why = templates.screenql(spec["screen"], spec["signal_tf"], spec["params"])
    return {"screen": spec["screen"], "signal_tf": spec["signal_tf"], "context_tfs": list(spec["context"]), "style": style.style_of(spec["signal_tf"]),
            "screenql": text, "engine_only_reason": why, "data_source": source, "scanned": len(symbols), "rows": rows,
            "columns": list(templates.RESULT_COLUMNS), "problems": reasons, "as_of": datetime.now(timezone.utc).isoformat(),
            **tier, "htf_filter": bp.validate(spec["params"])[0]["htf_filter"], "htf_failed_hidden": hidden, "history_note": HISTORY_NOTE,
            "disclaimer": quality.DISCLAIMER}


__all__ = ["router"]
