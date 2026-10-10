"""S1d (ADR-0021): the screener API - the registry, validation (text or builder tree -> canonical text, AST, plan),
saved screens and runs on server data.

* Behind the `screener_v2` operator flag (off by default); the Market Scanner page is unchanged.
* A run reads candles through the organisation's own broker session (the lake once part B is merged) - never bars a
  browser posted. No session -> 409 with where to fix it.
* Results are "matches" (passed filters), never advice; every run is stored in `screen_runs` (AST hash, universe,
  data source, matches) so a list someone acted on can be reproduced.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_trader
from app.core.models import bars_to_dataframe
from app.db.models import ScreenRecord, ScreenRunRecord, User
from app.db.session import get_session
from app.platform.controls import require_flag
from app.screener import compile_screen, nodes
from app.screener.registry import describe
from app.screener.runtime import SymbolData, closed_only, resample, run_screen

router = APIRouter(prefix="/api/screener", tags=["screener"])

FLAG = "screener_v2"
MAX_SYMBOLS = 50
DISCLAIMER = "Matches are symbols that passed the screen's filters on the data shown; they are not recommendations."
_FETCH = {"1m": "1min", "3m": "1min", "5m": "5min", "15m": "15min", "30m": "30min", "1h": "60min", "1d": "day", "1w": "day", "1M": "day"}
_FETCH_TF = {"1min": "1m", "5min": "5m", "15min": "15m", "30min": "30m", "60min": "1h", "day": "1d"}


def _cost_cap() -> float:
    from app.core import config
    return float(getattr(config, "SCREENER_COST_CAP", 200.0))


class ValidateBody(BaseModel):
    source: Union[str, Dict[str, Any]] = Field(description="ScreenQL text or the builder's JSON tree")
    base_tf: str = Field(default="1d", pattern=r"^(1m|3m|5m|15m|30m|1h|1d|1w|1M)$")
    params: Dict[str, Union[float, int, str, bool]] = Field(default_factory=dict)


class ScreenBody(ValidateBody):
    name: str = Field(min_length=1, max_length=120)


class RunBody(BaseModel):
    screen_id: Optional[int] = None
    source: Optional[Union[str, Dict[str, Any]]] = None
    base_tf: str = Field(default="1d", pattern=r"^(1m|3m|5m|15m|30m|1h|1d|1w|1M)$")
    params: Dict[str, Union[float, int, str, bool]] = Field(default_factory=dict)
    symbols: List[str] = Field(min_length=1, max_length=MAX_SYMBOLS)
    exchange: str = Field(default="NSE", pattern=r"^(NSE|BSE|NFO|BFO|MCX|CDS)$")


def _compiled(source: Union[str, Dict[str, Any]], base_tf: str, params: Dict[str, Any]) -> Tuple[Any, Any]:
    ast, validated = compile_screen(source, base_tf=base_tf, params=params, cost_cap=_cost_cap())
    return ast, validated


def _view(ast: Any, validated: Any) -> Dict[str, Any]:
    return {"ok": validated.ok, "problems": [p.as_dict() for p in validated.problems], "plan": validated.as_dict(),
            "text": nodes.to_text(ast) if ast is not None else None, "ast": nodes.to_json(ast) if ast is not None else None,
            "version": nodes.VERSION}


def _screen_dict(row: ScreenRecord) -> Dict[str, Any]:
    return {"id": row.id, "name": row.name, "text": row.source_text, "ast": json.loads(row.ast_json), "version": row.ast_version,
            "base_tf": row.base_tf, "params": json.loads(row.params_json or "{}"), "archived": row.archived,
            "created_at": row.created_at.isoformat() if row.created_at else None, "updated_at": row.updated_at.isoformat() if row.updated_at else None}


async def _flag(session: AsyncSession, user: User) -> None:
    await require_flag(session, FLAG, user.tenant_id)


@router.get("/registry")
async def registry(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _flag(session, user)
    return {"version": nodes.VERSION, "timeframes": list(nodes.TIMEFRAMES), "entries": describe()}


@router.post("/validate")
async def validate_screen(body: ValidateBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Text or builder tree -> {ok, problems (with positions), canonical text, AST, plan}. Nothing runs."""
    await _flag(session, user)
    return _view(*_compiled(body.source, body.base_tf, body.params))


async def _owned(session: AsyncSession, user: User, screen_id: int) -> ScreenRecord:
    row = await session.scalar(select(ScreenRecord).where(ScreenRecord.id == screen_id, ScreenRecord.tenant_id == user.tenant_id))
    if row is None:
        raise HTTPException(status_code=404, detail="Screen not found")
    return row


@router.get("/screens")
async def list_screens(include_archived: bool = False, user: User = Depends(get_current_user),
                       session: AsyncSession = Depends(get_session)) -> List[Dict[str, Any]]:
    await _flag(session, user)
    q = select(ScreenRecord).where(ScreenRecord.tenant_id == user.tenant_id)
    if not include_archived:
        q = q.where(ScreenRecord.archived.is_(False))
    return [_screen_dict(r) for r in await session.scalars(q.order_by(ScreenRecord.id.desc()))]


def _apply(row: ScreenRecord, body: ScreenBody) -> None:
    ast, validated = _compiled(body.source, body.base_tf, body.params)
    if not validated.ok:
        raise HTTPException(status_code=422, detail={"message": "the screen did not pass validation", "problems": [p.as_dict() for p in validated.problems]})
    row.name, row.source_text, row.ast_json = body.name.strip(), nodes.to_text(ast), json.dumps(nodes.to_json(ast))
    row.ast_version, row.base_tf, row.params_json = nodes.VERSION, body.base_tf, json.dumps(body.params)
    row.updated_at = datetime.now(timezone.utc)


@router.post("/screens", status_code=201)
async def create_screen(body: ScreenBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _flag(session, user)
    row = ScreenRecord(tenant_id=user.tenant_id, created_by=user.id, name="", source_text="", ast_json="{}")
    _apply(row, body)
    session.add(row)
    await session.commit()
    return _screen_dict(row)


@router.put("/screens/{screen_id}")
async def update_screen(screen_id: int, body: ScreenBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _flag(session, user)
    row = await _owned(session, user, screen_id)
    _apply(row, body)
    await session.commit()
    return _screen_dict(row)


@router.post("/screens/{screen_id}/archive")
async def archive_screen(screen_id: int, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Archived, never hard-deleted: earlier runs keep pointing at it."""
    await _flag(session, user)
    row = await _owned(session, user, screen_id)
    row.archived, row.updated_at = True, datetime.now(timezone.utc)
    await session.commit()
    return _screen_dict(row)


async def fetch_frames(session: AsyncSession, tenant_id: int, symbols: List[str], exchange: str, base_tf: str,
                       lookback: Dict[str, int], now: Optional[datetime] = None, include_forming: bool = False) -> Tuple[List[SymbolData], Dict[str, str], str]:
    """Server bars through the organisation's broker session -> (universe, per-symbol fetch problems, data source).
    A missing session raises 409 (the fix is under Settings > Brokers); never a fallback to client data.
    `include_forming` keeps the bar still forming (S4b-2 intrabar alerts only; screens decide on closed bars)."""
    from app.brokers.token_lifecycle import build_adapter
    from app.market_data.candles_routes import _pick_record
    from app.market_data.service import MarketDataService
    record = await _pick_record(session, tenant_id, None, "primary")
    interval = _FETCH[base_tf]
    bars_needed = max([*lookback.values(), 1])
    minutes = nodes.TF_MINUTES[base_tf]
    days = min(60, max(5, int(bars_needed * minutes / 375) + 3)) if interval != "day" else 5
    service = MarketDataService(build_adapter(record), lookback_days=days)
    universe: List[SymbolData] = []
    problems: Dict[str, str] = {}
    for symbol in symbols:
        try:
            bars = await service.get_candles(symbol, exchange, interval)
        except Exception as exc:  # noqa: BLE001 - one symbol's failure is reported, the run continues
            problems[symbol] = f"{type(exc).__name__}: {str(exc)[:120]}"
            continue
        df = bars_to_dataframe(bars) if bars else pd.DataFrame()
        if df.empty:
            problems[symbol] = "no bars from the broker"
            continue
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        fetched_tf = _FETCH_TF[interval]
        if not include_forming:
            df = closed_only(df, fetched_tf, now or datetime.now(timezone.utc))  # never the bar still forming
        if df.empty:
            problems[symbol] = "no closed bars from the broker"
            continue
        frames = {base_tf: df if fetched_tf == base_tf else resample(df, fetched_tf, base_tf, keep_forming=include_forming)}
        universe.append(SymbolData(symbol, frames))
    return universe, problems, f"broker:{record.broker_name}"


@router.post("/run")
async def run(body: RunBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Runs a saved screen or ad-hoc source over up to 50 symbols, on server bars only; stores the run."""
    await _flag(session, user)
    started = time.monotonic()
    screen_id = None
    if body.screen_id is not None:
        row = await _owned(session, user, body.screen_id)
        source: Union[str, Dict[str, Any]] = json.loads(row.ast_json)
        base_tf, params, screen_id = row.base_tf, {**json.loads(row.params_json or "{}"), **body.params}, row.id
    elif body.source is not None:
        source, base_tf, params = body.source, body.base_tf, dict(body.params)
    else:
        raise HTTPException(status_code=422, detail="give a screen_id or a source")
    ast, validated = _compiled(source, base_tf, params)
    if not validated.ok:
        raise HTTPException(status_code=422, detail={"message": "the screen did not pass validation", "problems": [p.as_dict() for p in validated.problems]})
    symbols = list(dict.fromkeys(s.strip().upper() for s in body.symbols if s.strip()))
    universe, fetch_problems, data_source = await fetch_frames(session, user.tenant_id, symbols, body.exchange, base_tf, validated.lookback)
    matches = run_screen(ast, validated, universe, base_tf=base_tf, params=params)
    results = [{"symbol": m.symbol, "matched": m.matched, "reason": m.reason} for m in matches]
    results += [{"symbol": s, "matched": False, "reason": why} for s, why in fetch_problems.items()]
    ast_json = json.dumps(nodes.to_json(ast), sort_keys=True)
    run_row = ScreenRunRecord(tenant_id=user.tenant_id, user_id=user.id, screen_id=screen_id, ast_sha256=hashlib.sha256(ast_json.encode()).hexdigest(),
                              ast_version=nodes.VERSION, base_tf=base_tf, universe_json=json.dumps(symbols), data_source=data_source,
                              scanned=len(symbols), matched=sum(1 for r in results if r["matched"]), result_json=json.dumps(results),
                              duration_ms=int((time.monotonic() - started) * 1000))
    session.add(run_row)
    await session.commit()
    return {"run_id": run_row.id, "text": nodes.to_text(ast), "base_tf": base_tf, "data_source": data_source, "scanned": len(symbols),
            "matched": [r["symbol"] for r in results if r["matched"]], "results": results, "disclaimer": DISCLAIMER}


@router.get("/runs/{run_id}")
async def get_run(run_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _flag(session, user)
    row = await session.scalar(select(ScreenRunRecord).where(ScreenRunRecord.id == run_id, ScreenRunRecord.tenant_id == user.tenant_id))
    if row is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"run_id": row.id, "screen_id": row.screen_id, "ast_sha256": row.ast_sha256, "version": row.ast_version, "base_tf": row.base_tf,
            "universe": json.loads(row.universe_json), "data_source": row.data_source, "scanned": row.scanned, "matched": row.matched,
            "results": json.loads(row.result_json), "created_at": row.created_at.isoformat() if row.created_at else None, "disclaimer": DISCLAIMER}


__all__ = ["router", "FLAG", "fetch_frames"]
