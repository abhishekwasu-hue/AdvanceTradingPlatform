"""U6 BK1 / BK2: the breakout screens API - the catalog (style / timeframe selector, builder blocks, templates with their ScreenQL
text, every parameter with its default and research note) and a run over server bars with the quality overlay.

Behind `screener_v2` AND `screener_breakouts` (both off by default); the U6 BK3 screens (B3 VCP, B10 / B11 / B12 / B15
indicator breakouts) also need `screener_breakouts_bk3`, and the U6 BK4 screens (B5 / B6 / B8 / B9) `screener_breakouts_bk4`
(both off by default) - left out of the catalog and refused by the run. Bars come through the organisation's broker session
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
from app.platform.controls import flag_enabled, require_flag
from app.screener.breakouts import gate, quality, style, templates
from app.screener.breakouts import params as bp
from app.screener.breakouts import session as ses
from app.screener.breakouts.screens import BK3_SCREENS, BK4_SCREENS, INTRADAY_SCREENS, SCREEN_TIMEFRAMES, SCREENS
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
    screen: Optional[str] = Field(default=None, description="B1 / B2 / B4 / B7 / B13 / B14 / B16 / B17 / B18 (BK3: B3 / B10 / B11 / B12 / B15;"
                                                            " BK4: B5 / B6 / B8 / B9) when no template is given")
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
    bk3 = await gate.bk3_enabled(session, user.tenant_id)          # U6 BK3 / BK4: their screens, templates and parameters only when on
    bk4 = await flag_enabled(session, gate.BK4_FLAG, user.tenant_id)
    hidden = (() if bk3 else BK3_SCREENS) + (() if bk4 else BK4_SCREENS)
    screens = {k: v for k, v in SCREENS.items() if k not in hidden}
    return {**templates.catalog(bk3, bk4), "screens": screens, "disclaimer": quality.DISCLAIMER}


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


def _intraday_gap(screen: str, signal_tf: str) -> bool:
    return screen == "B8" and bp.TF_MINUTES[signal_tf] < bp.TF_MINUTES["1d"]


def _windows(screen: str, params: Dict[str, Any]) -> List[int]:
    """The screen's own bar-count parameters (its group's integers), largest first - U6 BK3."""
    p, _ = bp.validate(params)
    unused = ("ma_stack_",) if not p.get("ma_stack") else ()          # the MA stack's lines are read only when it is on
    return sorted((int(p[q.name]) for q in bp.PARAMS if q.group == screen and isinstance(q.default, int)
                   and not isinstance(q.default, bool) and not q.choices and not (unused and q.name.startswith(unused))), reverse=True)


def min_signal_bars(screen: str, params: Dict[str, Any]) -> int:
    """U6 BK3 review L4: the fewest signal bars on which the screen can read anything with these parameters (its longest
    window plus the bar itself) - a shorter history is reported as such, never run to a silent "no match"."""
    if screen not in BK3_SCREENS:
        return 0
    w = _windows(screen, params)
    return (w[0] if w else 0) + 1


def history_bars(screen: str, signal_tf: str, params: Dict[str, Any]) -> int:
    """Signal bars to fetch. U6 BK2: an intraday screen needs whole sessions - the time-of-day average reads the same slot
    in the previous `tod_sessions` sessions - so it asks for that many sessions plus the current one. U6 BK4: an intraday
    gap screen needs the sessions its level reads (the previous one, or `gap_base_n`) plus the current one. U6 BK3: a BK3
    screen asks for its two longest windows end to end plus the ATR warm-up (the trend template's 200-day line read over
    its one-month slope and the 52-week range; a band-width low over the band's own length; ...), at least the default."""
    if screen in BK3_SCREENS:
        w = _windows(screen, params)
        p, _ = bp.validate(params)
        return max(SIGNAL_HISTORY_BARS, sum(w[:2]) + int(p["atr_n"]) + 1)
    if screen not in INTRADAY_SCREENS and not _intraday_gap(screen, signal_tf):
        return SIGNAL_HISTORY_BARS
    p, _ = bp.validate(params)
    per_session = -(-bp.TF_MINUTES["1d"] // bp.TF_MINUTES[signal_tf])
    if screen == "B8":
        sessions = 1 if p["gap_level"] == "prev_day" else int(p["gap_base_n"])
        return max(SIGNAL_HISTORY_BARS, (sessions + int(p["atr_n"]) + 1) * per_session)
    return max(SIGNAL_HISTORY_BARS, (int(p["tod_sessions"]) + 1) * per_session)


async def _frames(session: AsyncSession, user: User, symbols: List[str], exchange: str, signal_tf: str,
                  context: tuple, tier: Optional[Dict[str, Any]] = None, bars: int = SIGNAL_HISTORY_BARS, daily_bars: int = SIGNAL_HISTORY_BARS) -> tuple:
    """Closed bars per symbol for the signal timeframe and every context timeframe.

    * A daily-or-coarser signal: one daily fetch; the signal and every context frame are built from the daily bars (L8: a
      weekly signal's monthly context comes from daily bars, not from weekly ones).
    * An intraday signal: the signal timeframe's fetch (1h for 2h / 4h); intraday context is resampled from it, and
      daily-or-coarser context is built from a separate daily fetch (deeper than the intraday window). `bars`: signal
      bars to fetch (U6 BK2: whole sessions for the intraday screens, `history_bars`)."""
    tier = tier if tier is not None else {}                           # BT1b: the run's own user session, its data-tier badge
    daily_tf = bp.TF_MINUTES["1d"]
    frames: Dict[str, Dict[str, pd.DataFrame]] = {}
    if bp.TF_MINUTES[signal_tf] >= daily_tf:
        # U6 BK3 review L4: `daily_bars` - the daily bars the signal bars asked for need (a BK3 screen's windows)
        daily, problems, source = await fetch_frames(session, user.tenant_id, symbols, exchange, "1d", {"1d": max(SIGNAL_HISTORY_BARS, daily_bars)},
                                                     user_id=user.id, tier_out=tier)
        for d in daily:
            base = d.frames["1d"]
            frames[d.symbol] = {tf: style.resample(base, "1d", tf) for tf in (signal_tf, *context)}
        return frames, problems, source
    fetch_tf = FETCH_TF.get(signal_tf, signal_tf)
    universe, problems, source = await fetch_frames(session, user.tenant_id, symbols, exchange, fetch_tf, {fetch_tf: bars},
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
               include_htf_failed: bool, calendar: Optional[ses.SessionCalendar] = None, notes: Optional[List[str]] = None) -> tuple:
    """CPU-bound: the screen and overlay for every symbol (run in a worker thread, never on the event loop). U6 BK2:
    `calendar` is the exchange session of the intraday screens; the runs' notes are added to `notes` when given."""
    rows: List[Dict[str, Any]] = []
    reasons: Dict[str, str] = {}
    hidden = 0
    for symbol, fr in frames.items():
        try:
            result = quality.run_breakout(spec["screen"], fr, spec["signal_tf"], params=spec["params"], within=within, calendar=calendar)
        except ValueError as exc:
            reasons[symbol] = str(exc)
            continue
        if notes is not None:
            notes += [f"{symbol}: {x}" for x in result.notes]
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
    asked = templates.BY_ID[body.template_id].screen if body.template_id in templates.BY_ID else body.screen
    if asked in BK3_SCREENS:                    # U6 BK3: VCP / indicator breakouts need their own flag too - checked first (review L5)
        await require_flag(session, gate.BK3_FLAG, user.tenant_id)
    if asked in BK4_SCREENS:                    # U6 BK4: trendline / pattern / gap / retest likewise
        await require_flag(session, gate.BK4_FLAG, user.tenant_id)
    spec = _resolve(body)
    symbols = list(dict.fromkeys(body.symbols))
    if body.within * len(symbols) > MAX_WORK:
        raise HTTPException(status_code=422, detail=f"within x symbols is at most {MAX_WORK} per run ({body.within} x {len(symbols)})")
    calendar = None
    if spec["screen"] in INTRADAY_SCREENS or _intraday_gap(spec["screen"], spec["signal_tf"]):   # U6 BK2 / BK4: the exchange's clock
        try:
            calendar = ses.calendar_for(body.exchange)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    bars = history_bars(spec["screen"], spec["signal_tf"], spec["params"])
    # a daily-or-coarser BK3 signal: its signal bars in sessions (a week is 5) - the BK1 / BK2 fetch is unchanged
    per_bar = 5 if spec["signal_tf"] == "1w" else 1
    daily_bars = bars * per_bar if spec["screen"] in BK3_SCREENS else SIGNAL_HISTORY_BARS
    tier: Dict[str, Any] = {}
    frames, problems, source = await _frames(session, user, symbols, body.exchange, spec["signal_tf"], spec["context"], tier, bars,
                                             daily_bars)
    least = min_signal_bars(spec["screen"], spec["params"])
    for sym in [k for k, fr in frames.items() if len(fr.get(spec["signal_tf"], ())) < least]:
        have = len(frames.pop(sym)[spec["signal_tf"]])
        problems[sym] = (f"not enough history: {have} {spec['signal_tf']} bars, these {spec['screen']} parameters need at least {least}"
                         " (shorten a window, or use a longer-history data source)")
    notes: List[str] = []
    rows, graded, hidden = await run_in_threadpool(_grade_all, spec, frames, body.within, body.include_htf_failed, calendar, notes)
    reasons: Dict[str, str] = {**dict(problems), **graded}
    text, why = templates.screenql(spec["screen"], spec["signal_tf"], spec["params"])
    return {"screen": spec["screen"], "signal_tf": spec["signal_tf"], "context_tfs": list(spec["context"]), "style": style.style_of(spec["signal_tf"]),
            "screenql": text, "engine_only_reason": why, "data_source": source, "scanned": len(symbols), "rows": rows,
            "columns": list(templates.RESULT_COLUMNS) + (list(templates.INTRADAY_COLUMNS) if spec["screen"] in INTRADAY_SCREENS else []),
            "problems": reasons, "notes": notes[:50], "as_of": datetime.now(timezone.utc).isoformat(),
            **tier, "htf_filter": bp.validate(spec["params"])[0]["htf_filter"], "htf_failed_hidden": hidden, "history_note": HISTORY_NOTE,
            "disclaimer": quality.DISCLAIMER}


__all__ = ["router"]
