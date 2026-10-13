"""N3 S6a: the screen history API (flag `screener_history`, on top of `screener_v2`; off -> 503).

- `POST /api/screener/history/asof`: who passed a screen on a past date (point in time; daily screens).
- `POST /api/screener/history/fitness`: the screen's fitness over a window (forward returns of its matches against the
  universe, per horizon), recorded as a trial in the research ledger and deflated against the organisation's other
  screen fitness trials of the last 30 days.

Data: the shared lake's end-of-day bars only (`history.py`). Nothing here places, proposes or changes anything.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_trader
from app.db.models import ResearchTrialRecord, User
from app.db.session import get_session
from app.platform.controls import require_flag
from app.screener import history as H
from app.screener import nodes
from app.screener.routes import _compiled, _gate, _owned, missing_data, run_rate_limit
from app.screener.routes import FLAG as SCREENER_FLAG

router = APIRouter(prefix="/api/screener/history", tags=["screener"])

LEDGER_DAYS = 30
MAX_INDEX_MEMBERS_READ = H.MAX_SYMBOLS


class Universe(BaseModel):
    index: Optional[str] = Field(default=None, pattern=r"^[A-Z0-9]{2,40}$", description="an index code (U1-b catalogue, e.g. NIFTY50): members as of each date")
    symbols: List[str] = Field(default_factory=list, max_length=H.MAX_SYMBOLS)

    @model_validator(mode="after")
    def one_of(self) -> "Universe":
        if bool(self.index) == bool(self.symbols):
            raise ValueError("give an index code or a symbol list")
        return self


class Screen(BaseModel):
    screen_id: Optional[int] = None
    source: Optional[Union[str, Dict[str, Any]]] = None
    params: Dict[str, Union[float, int, str, bool]] = Field(default_factory=dict)
    universe: Universe


class AsOfBody(Screen):
    date: date


class FitnessBody(Screen):
    start: date
    end: date
    every: str = Field(default="weekly", pattern=r"^(daily|weekly|4-weekly|monthly)$")
    horizons: List[int] = Field(default_factory=lambda: list(H.HORIZONS), min_length=1, max_length=5)

    @model_validator(mode="after")
    def window(self) -> "FitnessBody":
        if self.end < self.start:
            raise ValueError("end must not be before start")
        if any(h < 1 or h > 60 for h in self.horizons):
            raise ValueError("horizons are 1 to 60 sessions")
        return self


async def _flags(session: AsyncSession, user: User) -> None:
    await require_flag(session, SCREENER_FLAG, user.tenant_id)
    await require_flag(session, H.FLAG, user.tenant_id)


async def _screen(session: AsyncSession, user: User, body: Screen) -> Tuple[Any, Any, Dict[str, Any]]:
    """The compiled daily screen, refused with the reason when history cannot run it."""
    if body.screen_id is not None:
        row = await _owned(session, user, body.screen_id)
        if row.base_tf != H.BASE_TF:
            raise HTTPException(status_code=422, detail="screen history runs daily screens only (base timeframe 1d)")
        source: Union[str, Dict[str, Any]] = json.loads(row.ast_json)
        params = {**json.loads(row.params_json or "{}"), **body.params}
    elif body.source is not None:
        source, params = body.source, dict(body.params)
    else:
        raise HTTPException(status_code=422, detail="give a screen_id or a source")
    ast, validated = _compiled(source, H.BASE_TF, params)
    await _gate(session, user, ast, validated)
    if not validated.ok:
        raise HTTPException(status_code=422, detail={"message": "the screen did not pass validation",
                                                     "problems": [p.as_dict() for p in validated.problems]})
    from app.screener import zones as zone_gate
    extra = missing_data(ast) + [c for c in zone_gate.flagged_calls(ast)]
    if extra or any(tf != H.BASE_TF for tf in (validated.lookback or {})):
        raise HTTPException(status_code=422, detail="screen history runs daily screens on end-of-day bars only: "
                                                    + (", ".join(sorted(set(f"{m}()" for m in extra))) + " need data it does not load"
                                                       if extra else "this screen reads another timeframe"))
    return ast, validated, params


def _holdout(start: date, end: date) -> Optional[str]:
    from app.backtest.data_policy import HoldoutError, check_range, holdout_start
    try:
        check_range(pd.Timestamp(start), pd.Timestamp(end), holdout_start())
    except HoldoutError as exc:
        return str(exc)
    return None


# --- the lake (shared end-of-day data) ----------------------------------------------------------------------------------------

def _frame(rows: List[Tuple[datetime, float, float, float, float, float]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    days = [ts.astimezone(H.IST).date() if ts.tzinfo else ts.date() for ts, *_ in rows]
    df = pd.DataFrame({"open": [r[1] for r in rows], "high": [r[2] for r in rows], "low": [r[3] for r in rows],
                       "close": [r[4] for r in rows], "volume": [r[5] for r in rows]}, index=H.bar_index(days))
    return df[~df.index.duplicated(keep="last")].sort_index()


async def _bars_by_isin(session: AsyncSession, isin: str, start: date, end: date, scale_out: Optional[Dict[date, H.Scale]] = None
                        ) -> pd.DataFrame:
    """Adjusted bars by ISIN. `scale_out` (review of S6a, MED) receives, per session date, the (price divisor, volume
    multiplier) that turns the adjusted bars up to that date into the bars as known then - the adjustment of the actions
    after the date, read off that date's adjusted and raw bar."""
    from app.market_lake.by_isin import isin_candles
    bars = await isin_candles(session, isin, start, end, adjusted=True)
    frame = _frame([(b.ts, b.open, b.high, b.low, b.close, float(b.volume or 0)) for b in bars])
    if scale_out is not None and not frame.empty:
        raw = _frame([(b.ts, b.open, b.high, b.low, b.close, float(b.volume or 0))
                      for b in await isin_candles(session, isin, start, end, adjusted=False)])
        both = frame.join(raw, rsuffix="_raw", how="inner")
        for ts, row in both.iterrows():
            scale_out[H.session_dates(pd.DatetimeIndex([ts]))[0]] = H.scale_of(row["close"], row["close_raw"], row["volume"], row["volume_raw"])
    return frame


async def _split_blackouts(session: AsyncSession, symbol: str, frame: pd.DataFrame, lookback: int, horizon: int) -> Set[date]:
    """Review of S6a (LOW): unadjusted bars (no ISIN history) across a split or bonus are not comparable - the dates
    whose lookback or forward window spans one of the symbol's ex-dates on record are left out."""
    from app.market_lake import adjust
    if frame is None or frame.empty:
        return set()
    ex = [a.ex_date for a in await adjust.actions_as_of(session, symbol, "NSE") if a.action in adjust.SHARE_ACTIONS]
    if not ex:
        return set()
    days = H.session_dates(frame.index)
    out: Set[date] = set()
    for i, d in enumerate(days):
        lo = days[max(0, i - lookback)]
        hi = days[min(len(days) - 1, i + horizon)]
        if any(lo < e <= hi for e in ex):
            out.add(d)
    return out


async def _bars_by_symbol(session: AsyncSession, symbol: str, start: date, end: date) -> pd.DataFrame:
    from app.market_lake.asof import candles_as_of
    from app.market_lake.bhavcopy_backfill import SOURCE, TIMEFRAME, bar_end
    rows = await candles_as_of(session, f"NSE:{symbol}", TIMEFRAME, bar_end(start) - timedelta(hours=1), bar_end(end), sources=[SOURCE["cm"]])
    return _frame([(r.ts if r.ts.tzinfo else r.ts.replace(tzinfo=timezone.utc), float(r.open), float(r.high), float(r.low),
                    float(r.close), float(r.volume or 0)) for r in rows])


async def load(session: AsyncSession, universe: Universe, start: date, end: date, dates_hint: Optional[List[date]] = None,
               scales_out: Optional[Dict[str, Dict[date, H.Scale]]] = None, excluded_out: Optional[Set[Tuple[str, date]]] = None,
               windows: Tuple[int, int] = (1, 0)) -> Tuple[Dict[str, pd.DataFrame], Optional[Dict[date, Set[str]]], List[str]]:
    """(frames by symbol, eligible symbols by date for an index universe (None for a list), notes). `dates_hint`: the
    dates the membership is needed on (every session in [start, end] when None). Review of S6a: `scales_out` receives
    the as-of-date price scales of ISIN-adjusted symbols; `excluded_out` the (symbol, date) pairs of unadjusted bars whose
    window (`windows` = lookback, horizon sessions) spans a split or bonus."""
    from app.universe.asof import isin_for, symbol_on
    from app.universe.indices import coverage_from, members_on
    notes: List[str] = []
    frames: Dict[str, pd.DataFrame] = {}
    if universe.index:
        first = await coverage_from(session, universe.index)
        if first is None:
            raise HTTPException(status_code=422, detail=f"no membership history for index {universe.index}")
        days = dates_hint or [start + timedelta(days=i) for i in range((end - start).days + 1)]
        early = [d for d in days if d < first]
        if early:
            raise HTTPException(status_code=422, detail=f"index {universe.index} membership is known from {first} only (asked from {min(early)})")
        by_date: Dict[date, Set[str]] = {}
        isins: Dict[str, str] = {}
        for d in days:
            members = await members_on(session, universe.index, d)
            for isin in members:
                if isin not in isins:
                    if len(isins) >= MAX_INDEX_MEMBERS_READ:
                        raise HTTPException(status_code=422, detail=f"more than {MAX_INDEX_MEMBERS_READ} symbols over the window - use a shorter window")
                    label = (await symbol_on(session, isin, end)) or (await symbol_on(session, isin, d)) or isin
                    if label in isins.values():          # review of S6a (LOW): a symbol reused by another company
                        label = f"{label} ({isin})"
                    isins[isin] = label
            by_date[d] = {isins[i] for i in members}
        unadjusted_members: List[str] = []
        for isin, label in isins.items():
            scale: Dict[date, H.Scale] = {}
            frame = await _bars_by_isin(session, isin, start, end, scale if scales_out is not None else None)
            if frame.empty and label != isin:
                symbol = label.split(" (")[0]
                frame = await _bars_by_symbol(session, symbol, start, end)
                unadjusted_members.append(label)
                if excluded_out is not None:
                    excluded_out.update((label, d) for d in await _split_blackouts(session, symbol, frame, *windows))
            elif scales_out is not None and any(v != (1.0, 1.0) for v in scale.values()):
                scales_out[label] = scale
            frames[label] = frame
        if unadjusted_members:
            notes.append(f"Unadjusted bars (no ISIN run history on record): {', '.join(unadjusted_members[:20])}")
        return frames, by_date, notes
    unadjusted = []
    for sym in dict.fromkeys(s.strip().upper() for s in universe.symbols if s.strip()):
        own_isin = await isin_for(session, sym, end)
        scale = {}
        frame = await _bars_by_isin(session, own_isin, start, end, scale if scales_out is not None else None) if own_isin else pd.DataFrame()
        if frame.empty:
            frame = await _bars_by_symbol(session, sym, start, end)
            unadjusted.append(sym)
            if excluded_out is not None:
                excluded_out.update((sym, d) for d in await _split_blackouts(session, sym, frame, *windows))
        elif scales_out is not None and any(v != (1.0, 1.0) for v in scale.values()):
            scales_out[sym] = scale
        frames[sym] = frame
    if unadjusted:
        notes.append(f"Unadjusted bars (no ISIN history on record): {', '.join(unadjusted[:20])}")
    return frames, None, notes


def calendar_of(frames: Dict[str, pd.DataFrame]) -> List[date]:
    days: Set[date] = set()
    for f in frames.values():
        if f is not None and not f.empty:
            days.update(H.session_dates(f.index))
    return sorted(days)


# --- the routes -------------------------------------------------------------------------------------------------------------

@router.post("/asof", dependencies=[Depends(run_rate_limit)])
async def asof(body: AsOfBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Who passed the screen on `date`, on bars closed by then; an index universe is its membership on that date."""
    await _flags(session, user)
    ast, validated, params = await _screen(session, user, body)
    sealed = _holdout(body.date, body.date)
    if sealed:
        raise HTTPException(status_code=422, detail=sealed)
    lookback = int((validated.lookback or {}).get(H.BASE_TF, 1) or 1)
    first, _ = H.window_days(body.date, body.date, lookback, 0)
    scales: Dict[str, Dict[date, H.Scale]] = {}
    excluded: Set[Tuple[str, date]] = set()
    frames, eligible, notes = await load(session, body.universe, first, body.date, dates_hint=[body.date], scales_out=scales,
                                         excluded_out=excluded, windows=(lookback, 0))
    members = eligible.get(body.date) if eligible is not None else None
    out = await asyncio.to_thread(H.passers_on, ast, validated, frames, body.date, params, members, scales, excluded)
    traded = [s for s, f in frames.items() if not H.cut(f, body.date).empty and body.date in H.session_dates(H.cut(f, body.date).index)]
    return {"date": str(body.date), "text": nodes.to_text(ast), "universe": out["universe"], "matched": out["matched"],
            "not_evaluated": out["not_evaluated"], "traded_that_day": len(traded), "notes": notes,
            "data": "Shared end-of-day bars (NSE bhavcopies), adjusted by ISIN; bars closed on or before the date only.",
            "disclaimer": "Matches are symbols that passed the screen's filters on the data shown; they are not recommendations."}


async def _fitness_trials(session: AsyncSession, tenant_id: int, since: datetime, every: Optional[str] = None,
                          horizon: Optional[int] = None) -> List[float]:
    """The organisation's screen fitness trials of the window - review of S6a (LOW): the same family only (the same
    rebalance step and main horizon), so Sharpes on different sampling are not compared."""
    from app.backtest.validation import sharpe
    rows = await session.scalars(select(ResearchTrialRecord).where(
        ResearchTrialRecord.tenant_id == tenant_id, ResearchTrialRecord.status == "ok", ResearchTrialRecord.created_at >= since,
        ResearchTrialRecord.dsl_json.like('%"kind": "screen_fitness"%')))
    out = []
    for r in rows:
        dsl = json.loads(r.dsl_json or "{}")
        if (every is None or dsl.get("every") == every) and (horizon is None or dsl.get("horizon") == horizon):
            out.append(sharpe(json.loads(r.returns_json or "[]")))
    return out


@router.post("/fitness", dependencies=[Depends(run_rate_limit)])
async def fitness(body: FitnessBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """The screen's fitness over [start, end] (spec §2.6), deflated for the screens this organisation tried."""
    from app.ai.research import record_trial
    from app.backtest.validation import deflated_sharpe
    await _flags(session, user)
    ast, validated, params = await _screen(session, user, body)
    horizon = max(body.horizons)
    lookback = int((validated.lookback or {}).get(H.BASE_TF, 1) or 1)
    first, last = H.window_days(body.start, body.end, lookback, horizon)
    sealed = _holdout(first, last)
    if sealed:
        raise HTTPException(status_code=422, detail=f"the window with its {horizon}-session horizon {sealed}")
    every = H.EVERY[body.every]
    # An index's membership is read on the weekdays of [start, end] (the rebalance dates are among them).
    weekdays = [body.start + timedelta(days=i) for i in range((body.end - body.start).days + 1)]
    scales: Dict[str, Dict[date, H.Scale]] = {}
    excluded: Set[Tuple[str, date]] = set()
    frames, eligible, notes = await load(session, body.universe, first, last, dates_hint=[d for d in weekdays if d.weekday() < 5],
                                         scales_out=scales, excluded_out=excluded, windows=(lookback, horizon))
    calendar = calendar_of(frames)
    dates = H.rebalance_dates(calendar, body.start, body.end, every)
    if not dates:
        raise HTTPException(status_code=422, detail="no trading sessions with data in the window")
    if len(dates) > H.MAX_DATES:
        raise HTTPException(status_code=422, detail=f"at most {H.MAX_DATES} rebalance dates - use a longer step or a shorter window")
    try:
        sig = await asyncio.to_thread(H.signals, ast, validated, frames, dates, params, eligible, scales, excluded)
    except H.HistoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    stats = await asyncio.to_thread(H.fitness, sig, frames, calendar, body.horizons, eligible, excluded)
    if excluded:
        notes.append(f"{len({s for s, _ in excluded})} symbol(s) on unadjusted bars: dates whose window spans a split or bonus are left out")
    main = next((s for s in stats["horizons"] if s.horizon == H.MAIN_HORIZON), stats["horizons"][0])
    label = (f"index:{body.universe.index}" if body.universe.index else f"list:{len(frames)}")[:40]
    dsl = {"kind": "screen_fitness", "text": nodes.to_text(ast), "params": params, "universe": label, "start": str(body.start),
           "end": str(body.end), "every": body.every, "horizon": main.horizon}
    since = datetime.now(timezone.utc) - timedelta(days=LEDGER_DAYS)
    earlier = await _fitness_trials(session, user.tenant_id, since, body.every, main.horizon)
    deflation: Dict[str, Any] = {"n_trials": len(earlier) + 1, "dsr": None}
    returns = main.independent_excess                       # review of S6a (MED): non-overlapping forward windows only
    if len(returns) >= 2:
        await record_trial(session, tenant_id=user.tenant_id, user_id=user.id, study_id=str(uuid.uuid4()), dsl=dsl, symbol=label,
                           exchange="NSE", timeframe=H.BASE_TF, status="ok", data_from=H.utc_close(dates[0]), data_to=H.utc_close(dates[-1]),
                           metrics=main.as_dict(), returns=returns)
        await session.commit()
        from app.backtest.validation import sharpe
        deflation = deflated_sharpe(returns, earlier + [sharpe(returns)])
    return {"text": nodes.to_text(ast), "universe": label, "start": str(body.start), "end": str(body.end), "every": body.every,
            "dates": len(dates), "symbols": len(frames), "avg_passers": stats["avg_passers"], "turnover": stats["turnover"],
            "horizons": [s.as_dict() for s in stats["horizons"]], "confidence": H.confidence_note(main),
            "regimes": stats["regimes"], "main_horizon": main.horizon,
            "deflation": {**deflation, "note": f"Deflated for {deflation.get('n_trials', 1)} screen fitness runs by this organisation in the "
                                                 f"last {LEDGER_DAYS} days (the search is part of the result)."},
            "notes": notes, "data": "Shared end-of-day bars (NSE bhavcopies), adjusted by ISIN; the screen sees bars closed by each date; "
                                     "forward returns use the closes after it.",
            "disclaimer": H.DISCLAIMER}
