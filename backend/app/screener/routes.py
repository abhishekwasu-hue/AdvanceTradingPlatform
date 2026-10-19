"""S1d (ADR-0021): the screener API - the registry, validation (text or builder tree -> canonical text, AST, plan),
saved screens and runs on server data.

* Behind the `screener_v2` operator flag (off by default); the Market Scanner page is unchanged.
* A run reads candles through the organisation's own broker session (the lake once part B is merged) - never bars a
  browser posted. No session -> 409 with where to fix it.
* Results are "matches" (passed filters), never advice; every run is stored in `screen_runs` (AST hash, universe,
  data source, matches) so a list someone acted on can be reproduced.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import json
import math
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
from app.db.session import get_session, release_connection
from app.platform.controls import flag_enabled, require_flag
from app.screener import amw_functions as amw_gate
from app.screener import compile_screen, nodes
from app.screener import legends as lg
from app.screener import ranking as ranking_gate   # registers the `legends` template source (U7 LG1)
from app.screener import taxonomy as tx
from app.screener import zones as zone_gate
from app.screener import ownership as ownership_gate
from app.screener import venues as venue_gate
from app.screener import fundamentals as fundamentals_gate
from app.screener.breakouts import gate as breakout_gate
from app.core.rate_limit import user_rate_limit
from app.screener.panel import FLAG as VECTOR_FLAG
from app.screener.registry import FUNCTIONS, describe
from app.screener.validator import Problem
from app.screener.runtime import Match, ScreenMemo, SymbolData, closed_only, resample, run_screen, stage_survivors

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/screener", tags=["screener"])

FLAG = "screener_v2"
GALLERY_FLAG = "screener_gallery"   # U9 TX1: the taxonomy API and the gallery on the Screener page
LEGENDS_FLAG = lg.FLAG             # U7 LG1: the Legends cards (their screens are hidden from the taxonomy while off)
# U5 D2 review: live previews make /run chattier; a per-user window keeps them (and Runs) to a sane rate.
RUN_BUCKET, RUN_LIMIT, RUN_WINDOW = "screener_run", 60, 60
run_rate_limit = user_rate_limit(RUN_BUCKET, limit=RUN_LIMIT, window_seconds=RUN_WINDOW)


async def charge_runs(user: User, units: int) -> None:
    """A request that runs several screens (the library's confluence, watch-trigger, digest preview) is charged as that
    many `/run` calls on the same per-user bucket, so it cannot do more work than `/run` allows."""
    from app.core.rate_limit import charge_user
    await charge_user(RUN_BUCKET, user.id, RUN_LIMIT, RUN_WINDOW, units)
# Data the run path loads today: bars, and the stored market-zone snapshots (Z7a-3). A screen using a function that needs
# more is refused with the reason (the U1-d universe data and the option chain join here when they are wired in).
# U7 LG3: and the point-in-time fundamentals. N3 S8a: and the ownership patterns and events known by each bar's close.
# N3 S7a: and the ranking systems a RankScore names (loaded by the route, with the sectors a sector-neutral one needs).
RUN_PROVIDES: frozenset = frozenset({"zones", "fundamentals", "ownership", "rankings"})
MAX_SYMBOLS = 50
DISCLAIMER = "Matches are symbols that passed the screen's filters on the data shown; they are not recommendations."
SESSION_MINUTES = 375              # NSE cash session 09:15-15:30
HOLIDAY_PAD_DAYS = 4               # exchange holidays inside the window
MAX_INTRADAY_FETCH_DAYS = 60
FETCH_CONCURRENCY = 3     # LD-7: one screen run's broker fetches in flight at once (capped by the broker's burst)


def fetch_concurrency(broker_name: str) -> int:
    """How many of one screen run's symbol fetches may be in flight at once: the broker's burst allowance in the worker's
    rate budget (app/brokers/rate_budget.py - about a third of the published limit; 3 for a broker it does not list,
    Dhan among them), never more than FETCH_CONCURRENCY."""
    from app.brokers.rate_budget import limits_for
    return max(1, min(FETCH_CONCURRENCY, limits_for(broker_name).burst))
_FETCH = {"1m": "1min", "3m": "1min", "5m": "5min", "15m": "15min", "30m": "30min", "1h": "60min", "1d": "day", "1w": "day", "1M": "day"}
_FETCH_TF = {"1min": "1m", "5min": "5m", "15min": "15m", "30min": "30m", "60min": "1h", "day": "1d"}


def _cost_cap() -> float:
    from app.core import config
    return float(getattr(config, "SCREENER_COST_CAP", 200.0))


class ValidateBody(BaseModel):
    source: Union[str, Dict[str, Any]] = Field(description="ScreenQL text or the builder's JSON tree")
    base_tf: str = Field(default="1d", pattern=r"^(1m|3m|5m|15m|30m|1h|1d|1w|1M)$")
    params: Dict[str, Union[float, int, str, bool]] = Field(default_factory=dict)
    exchange: Optional[str] = Field(default=None, pattern=r"^(NSE|BSE|NFO|BFO|MCX|CDS)$",
                                    description="N3 S8c: check the screen against this venue's data (flag screener_venues)")


class ScreenBody(ValidateBody):
    name: str = Field(min_length=1, max_length=120)


class RunBody(BaseModel):
    screen_id: Optional[int] = None
    source: Optional[Union[str, Dict[str, Any]]] = None
    base_tf: str = Field(default="1d", pattern=r"^(1m|3m|5m|15m|30m|1h|1d|1w|1M)$")
    params: Dict[str, Union[float, int, str, bool]] = Field(default_factory=dict)
    symbols: List[str] = Field(min_length=1, max_length=MAX_SYMBOLS)
    exchange: str = Field(default="NSE", pattern=r"^(NSE|BSE|NFO|BFO|MCX|CDS)$")
    preview: bool = Field(default=False, description="U5 D2 live counts while editing: the same evaluation, not stored as a run")


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
    entries = describe()
    off = await _flags_off(session, user)
    if off:                                       # Z7a-3 (the U6 BK1 pattern): a flagged family stays hidden while its flag is off
        entries = {k: v for k, v in entries.items()           # U6 BK3: a second flag (Spec.also_flags) hides it too
                   if not (k in FUNCTIONS and ({FUNCTIONS[k].flag, *FUNCTIONS[k].also_flags} & off))}
        if amw_gate.FLAG in off:                  # AMW-3: the v2 candles and Pattern's `confirm` argument hide too
            entries = amw_gate.hide(entries)
    return {"version": nodes.VERSION, "timeframes": list(nodes.TIMEFRAMES), "entries": entries}


async def _flags_off(session: AsyncSession, user: User) -> set:
    """The registry's function flags that are off for the user's organisation."""
    flags = {f for spec in FUNCTIONS.values() for f in (spec.flag, *spec.also_flags) if f}
    return {f for f in flags if not await flag_enabled(session, f, user.tenant_id)}


@router.get("/venues")
async def venues(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """N3 S8c: the field-availability matrix - per venue its bar fields, data families and the functions it can run."""
    await _flag(session, user)
    await require_flag(session, venue_gate.FLAG, user.tenant_id)
    return venue_gate.matrix()


@router.get("/taxonomy")
async def taxonomy(method: Optional[str] = None, setup: Optional[str] = None, user: User = Depends(get_current_user),
                   session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """U9 TX1: the library's two axes (method x setup), the tag vocabulary, the pre-built screens filed on them and the
    full matrix of counts (empty cells are 0). `method` / `setup` narrow the screen list, never the matrix. A template
    source behind its own flag (e.g. the breakouts) is left out while that flag is off for the organisation."""
    await _flag(session, user)
    await require_flag(session, GALLERY_FLAG, user.tenant_id)
    tax = tx.load()
    if method is not None and method not in {m.id for m in tax.methods}:
        raise HTTPException(status_code=422, detail=f"unknown method {method!r}; one of {', '.join(m.id for m in tax.methods)}")
    if setup is not None and setup not in {s.id for s in tax.setups}:
        raise HTTPException(status_code=422, detail=f"unknown setup {setup!r}; one of {', '.join(s.id for s in tax.setups)}")
    hidden = [name for name, src in tx.sources().items()
              if any([not await flag_enabled(session, f, user.tenant_id) for f in (src.flag, *src.also) if f])]
    on: Dict[str, bool] = {}
    for flag in set(tx.screen_flags().values()):                  # U7 LG2: single screens behind their own flag
        on[flag] = await flag_enabled(session, flag, user.tenant_id)
    hidden_ids = [ident for ident, flag in tx.screen_flags().items() if not on[flag]]
    return tx.payload(tax, hidden_sources=hidden, method=method, setup=setup, hidden_ids=hidden_ids)


async def _legend_slices(session: AsyncSession, user: User) -> List[str]:
    """U7 LG2 / LG3: the card slices this organisation sees - LG1 with `screener_legends`, each later slice also with its
    own flag (`legends.SLICES`)."""
    out = ["lg1"]
    for part, flag in lg.SLICES.items():
        if flag and await flag_enabled(session, flag, user.tenant_id):
            out.append(part)
    return out


@router.get("/legends")
async def legends(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """U7 LG1: the Legends cards - the library header (the disclaimer), and per card its five lines with their cited
    public sources, the disclaimer naming the person, what the screen approximates, the ScreenQL source with its
    editable parameters (defaults and documented ranges) and where the screen is filed in the taxonomy."""
    await _flag(session, user)
    await require_flag(session, LEGENDS_FLAG, user.tenant_id)
    return lg.payload(lg.load(), tx.load(), slices=await _legend_slices(session, user))


@router.get("/legends/{card_id}")
async def legend_card(card_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """One Legends card (the shape of an entry in `GET /legends`, with the library header)."""
    await _flag(session, user)
    await require_flag(session, LEGENDS_FLAG, user.tenant_id)
    library = lg.load()
    card = library.card(card_id)
    if card is None or card.slice not in await _legend_slices(session, user):
        raise HTTPException(status_code=404, detail="no such Legends card")
    return {**lg.card_payload(card, library, tx.load()), "header": lg.HEADER}


class StagesBody(BaseModel):
    source: Optional[Union[str, Dict[str, Any]]] = Field(default=None, description="ScreenQL text or the builder's JSON tree")
    library_id: Optional[str] = Field(default=None, max_length=120, description="a pre-built Legends or breakout screen")
    base_tf: str = Field(default="1d", pattern=r"^(1m|3m|5m|15m|30m|1h|1d|1w|1M)$")
    params: Dict[str, Union[float, int, str, bool]] = Field(default_factory=dict)


@router.post("/stages")
async def screen_stages(body: StagesBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """U2 (flag `screener_builder_u2`): a screen -> the funnel's stages (top-level conjuncts, parameters inlined), so the
    builder can open it; validated and gated like `/validate`. Nothing runs, nothing is stored."""
    from app.screener import stages as st
    await _flag(session, user)
    await require_flag(session, st.FLAG, user.tenant_id)
    name: Optional[str] = None
    source: Union[str, Dict[str, Any]]
    if body.library_id is not None:
        src = tx.sources().get(body.library_id.split(".", 1)[0])
        if src is not None and any([not await flag_enabled(session, f, user.tenant_id) for f in (src.flag, *src.also) if f]):
            raise HTTPException(status_code=404, detail="that screen's family is turned off for this organisation")
        if body.library_id.startswith("legends."):
            # U7 LG2: a card of a slice this organisation does not see is not there (as GET /legends/{id} answers)
            card = lg.load().card(body.library_id)
            if card is not None and card.slice not in await _legend_slices(session, user):
                raise HTTPException(status_code=404, detail="no such Legends card")
        own_flag = tx.screen_flags().get(body.library_id)                 # any single screen behind a flag of its own
        if own_flag is not None and not await flag_enabled(session, own_flag, user.tenant_id):
            raise HTTPException(status_code=404, detail="no such screen")
        found, why = st.library_source(body.library_id)
        if found is None:
            raise HTTPException(status_code=404, detail=why)
        source, base_tf, params, name = found
    elif body.source is not None:
        source, base_tf, params = body.source, body.base_tf, dict(body.params)
    else:
        raise HTTPException(status_code=422, detail="give a source or a library_id")
    ast, validated = _compiled(source, base_tf, params)
    await _gate(session, user, ast, validated, params)        # main: params resolve RankScore($name) (N3 S7a)
    if ast is None or not validated.ok:
        raise HTTPException(status_code=422, detail={"message": "the screen did not pass validation", "problems": [p.as_dict() for p in validated.problems]})
    flat = st.inline(ast, params)
    return {"name": name, "base_tf": base_tf, "text": nodes.to_text(flat), "stages": st.split(flat), "version": nodes.VERSION}


@router.post("/validate")
async def validate_screen(body: ValidateBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Text or builder tree -> {ok, problems (with positions), canonical text, AST, plan}. Nothing runs."""
    await _flag(session, user)
    ast, validated = _compiled(body.source, body.base_tf, body.params)
    await _gate(session, user, ast, validated, body.params, getattr(body, "exchange", None))
    return _view(ast, validated)


async def _gate(session: AsyncSession, user: User, ast: Any, validated: Any, params: Optional[Dict[str, Any]] = None,
                exchange: Optional[str] = None) -> None:
    """A screen using a flagged function while its flag is off fails validation, with the reason: the U6 BK1 breakout
    screens (`screener_breakouts`; BK3's also `screener_breakouts_bk3`), the Z7a-3 market-zone functions (`market_zones`),
    the U7 LG3 fundamentals functions (`screener_fundamentals`), N3 S8a's ownership functions (`screener_ownership`), the
    AMW-3 functions, v2 candles and confirmation statuses (`screenql_amw_functions`; `params` resolves a `$parameter`
    pattern name), N3 S8c's venue matrix (`screener_venues`, with an exchange) and N3 S7a's RankScore
    (`screener_rankings`; a system it names must be a platform one or this organisation's own). With the AMW-3 flag off,
    Pattern* mistakes get main's messages (`amw_gate.flag_off_validation`). Every gate runs; RankScore's check is last
    so its early return skips none."""
    if amw_gate.uses_patterns(ast) and not await amw_gate.enabled(session, user.tenant_id):
        amw_gate.flag_off_validation(ast, validated, params or {})
    for gate in (breakout_gate, zone_gate, fundamentals_gate, ownership_gate):
        blocked = await gate.blocked_calls(session, user.tenant_id, ast)
        if blocked:
            validated.ok = False
            validated.problems.append(Problem(gate.refusal(blocked)))
    amw_blocked = await amw_gate.blocked_calls(session, user.tenant_id, ast, params or {})
    if amw_blocked:
        validated.ok = False
        validated.problems.append(Problem(amw_gate.refusal(amw_blocked)))
    if exchange and ast is not None and await venue_gate.enabled(session, user.tenant_id):
        missing = venue_gate.unavailable(ast, exchange)          # N3 S8c: what this venue's data does not carry
        if missing:
            validated.ok = False
            validated.problems.extend(Problem(m) for m in missing)
    if ranking_gate.uses_rankscore(ast):
        if not await ranking_gate.enabled(session, user.tenant_id):
            validated.ok = False
            validated.problems.append(Problem(ranking_gate.refusal()))
            return
        found, problems = await ranking_gate.resolve(session, user.tenant_id, ranking_gate.names_in(ast, params))
        if problems:
            validated.ok = False
            validated.problems.extend(Problem(p) for p in problems)
        if exchange and await venue_gate.enabled(session, user.tenant_id):
            # re-check of S8: a named system's factors read this venue's data too - the same venue check as the screen's
            for name, system in found.items():
                for leaf in system.leaves():
                    for m in venue_gate.unavailable(leaf.ast, exchange):
                        validated.ok = False
                        validated.problems.append(Problem(f'RankScore("{name}"), factor "{leaf.name}" - {m}'))


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


async def _apply(session: AsyncSession, user: User, row: ScreenRecord, body: ScreenBody) -> None:
    ast, validated = _compiled(body.source, body.base_tf, body.params)
    await _gate(session, user, ast, validated, body.params)
    if not validated.ok:
        raise HTTPException(status_code=422, detail={"message": "the screen did not pass validation", "problems": [p.as_dict() for p in validated.problems]})
    row.name, row.source_text, row.ast_json = body.name.strip(), nodes.to_text(ast), json.dumps(nodes.to_json(ast))
    row.ast_version, row.base_tf, row.params_json = nodes.VERSION, body.base_tf, json.dumps(body.params)
    row.updated_at = datetime.now(timezone.utc)


@router.post("/screens", status_code=201)
async def create_screen(body: ScreenBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _flag(session, user)
    row = ScreenRecord(tenant_id=user.tenant_id, created_by=user.id, name="", source_text="", ast_json="{}")
    await _apply(session, user, row, body)
    session.add(row)
    await session.commit()
    return _screen_dict(row)


@router.put("/screens/{screen_id}")
async def update_screen(screen_id: int, body: ScreenBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _flag(session, user)
    row = await _owned(session, user, screen_id)
    await _apply(session, user, row, body)
    await _rules_still_valid(session, user, row)
    await session.commit()
    return _screen_dict(row)


async def _rules_still_valid(session: AsyncSession, user: User, row: ScreenRecord) -> None:
    """N3 S7a review (LOW): the alert rules running this screen are checked against the edited screen - an edit that a
    rule could not run (RankScore, a zone function or an AMW-3 bar-close-only function on an intrabar rule...) is refused
    with the rule named."""
    from app.alerts.rules_routes import screen_rule_checks
    from app.db.models import AlertRuleRecord
    rules = [(r.id, r.fire_on, r.exchange) for r in await session.scalars(
        select(AlertRuleRecord).where(AlertRuleRecord.tenant_id == row.tenant_id, AlertRuleRecord.screen_id == row.id))]
    ast, params = json.loads(row.ast_json), json.loads(row.params_json or "{}")
    for rule_id, fire_on, exchange in rules:
        try:
            await screen_rule_checks(session, user, ast, fire_on, exchange, params)
        except HTTPException as exc:
            await session.rollback()
            detail = exc.detail.get("message") if isinstance(exc.detail, dict) else exc.detail
            raise HTTPException(status_code=422, detail=f"alert rule #{rule_id} runs this screen and could not after this change: {detail}") from exc


@router.post("/screens/{screen_id}/archive")
async def archive_screen(screen_id: int, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Archived, never hard-deleted: earlier runs keep pointing at it."""
    await _flag(session, user)
    row = await _owned(session, user, screen_id)
    row.archived, row.updated_at = True, datetime.now(timezone.utc)
    await session.commit()
    return _screen_dict(row)


def fetch_days(lookback: Dict[str, int], base_tf: str) -> int:
    """Calendar days of intraday bars to fetch for a screen's lookback. S5-A review: each timeframe's bars count in its
    own minutes (100 daily bars are 100 sessions, not 100 base bars); sessions become calendar days (weekends) with room
    for holidays; capped at the broker's intraday window (a longer need is then reported as "not enough history")."""
    sessions = max([math.ceil(bars * nodes.TF_MINUTES.get(tf, nodes.TF_MINUTES[base_tf]) / SESSION_MINUTES)
                    for tf, bars in lookback.items()] or [1])
    return min(MAX_INTRADAY_FETCH_DAYS, max(5, math.ceil(sessions * 7 / 5) + HOLIDAY_PAD_DAYS))


async def fetch_frames(session: AsyncSession, tenant_id: int, symbols: List[str], exchange: str, base_tf: str,
                       lookback: Dict[str, int], now: Optional[datetime] = None, include_forming: bool = False, *,
                       user_id: Optional[int] = None, tier_out: Optional[Dict[str, Any]] = None,
                       release: bool = False) -> Tuple[List[SymbolData], Dict[str, str], str]:
    """Server bars through the organisation's broker session -> (universe, per-symbol fetch problems, data source).
    A missing session raises 409 (the fix is under Settings > Brokers); never a fallback to client data.
    `include_forming` keeps the bar still forming (S4b-2 intrabar alerts only; screens decide on closed bars).

    BT1b (flag `byob_data_tier`): the bars come only through the session of the user the run / alert rule belongs to
    (`user_id`), at a broker whose data terms are confirmed, for the symbols inside that session's budget (the rest are
    reported as EOD-only problems), cached under that session's namespace. No such session -> 409 (Tier C).
    `tier_out` receives the data-tier badge of what was read.
    `release` (LD-7, a request whose session has only read so far): the pool connection goes back before the broker
    calls; the worker's callers keep their transaction."""
    from app.brokers.token_lifecycle import build_adapter
    from app.market_data import live_access
    from app.market_data.candles_routes import _pick_record
    from app.market_data.fanin import Priority
    from app.market_data.service import MarketDataService
    interval = _FETCH[base_tf]
    attach_daily = interval != "day" and any(nodes.TF_MINUTES.get(tf, 0) >= nodes.TF_MINUTES["1d"] for tf in lookback)
    # BK1 verification F3: with the daily history attached, @1d / @1w / @1M come from it, so only the intraday timeframes
    # size the intraday window (a 15m screen with daily context fetches days, not the 60-day cap)
    window = {tf: b for tf, b in lookback.items() if nodes.TF_MINUTES.get(tf, 0) < nodes.TF_MINUTES["1d"]} if attach_daily else lookback
    days = fetch_days(window or {base_tf: 1}, base_tf) if interval != "day" else 5
    problems: Dict[str, str] = {}
    own = None
    if await live_access.byob_on(session, tenant_id):
        user = await live_access.load_user(session, user_id)
        if user is not None and user.tenant_id != tenant_id:
            user = None                                   # never another organisation's member
        own = await live_access.user_source(session, user, [(s, exchange) for s in symbols], priority=Priority.SCREENER)
        record = own.record
        service = own.market_data(lookback_days=days)
        allowed = [s for s in symbols if own.allows(s, exchange)]
        problems.update({s: own.reason(s, exchange) for s in symbols if not own.allows(s, exchange)})
        symbols = allowed
    else:
        record = await _pick_record(session, tenant_id, None, "primary")
        service = MarketDataService(build_adapter(record), lookback_days=days)
    if release:
        await release_connection(session)
    universe: List[SymbolData] = []
    newest: List[datetime] = []
    # LD-7: the symbols are fetched a few at a time instead of one after another (a cold five-symbol run waited for five
    # broker round trips in a row). At most the broker's burst allowance at once (`fetch_concurrency`); the results keep
    # the symbols' order.
    gate = asyncio.Semaphore(fetch_concurrency(record.broker_name))

    async def fetch_one(symbol: str) -> Tuple[Any, str]:
        async with gate:
            try:
                return await (service.get_candles(symbol, exchange, interval, fresh=True) if include_forming  # no cached copy
                              else service.get_candles(symbol, exchange, interval)), ""
            except Exception as exc:  # noqa: BLE001 - one symbol's failure is reported, the run continues
                return None, f"{type(exc).__name__}: {str(exc)[:120]}"

    got = await asyncio.gather(*(fetch_one(symbol) for symbol in symbols))

    def frames_of_all() -> List[Tuple[Any, Dict[str, Any], str]]:
        return [(None, {}, failed) if failed else _symbol_frames(bars, interval, base_tf, include_forming, now) for bars, failed in got]

    # LD-7: the pandas work runs on a worker thread, so a screen does not hold the event loop - one thread for the run's
    # symbols in turn, not one each (more CPU-bound threads only take the GIL from the event loop more often).
    fetched = await asyncio.to_thread(frames_of_all)
    for symbol, (df, frames, problem) in zip(symbols, fetched):
        if problem:
            problems[symbol] = problem
            continue
        universe.append(SymbolData(symbol, frames, exchange=exchange))         # U6 BK2: the session clock of the symbol
        newest.append(df.index[-1].to_pydatetime())
    if attach_daily and universe:
        daily_service = own.market_data(lookback_days=5) if own is not None else MarketDataService(build_adapter(record), lookback_days=5)
        await _attach_daily(daily_service, universe, exchange, now)        # U6 BK1: through the same (BT1b) source
    if own is not None and tier_out is not None:
        # Timed by the data (the stalest symbol's newest bar); nothing read = age unknown = stale.
        oldest = min(newest) if newest else None
        tier_out["data_tier"] = own.badge(timeframe=_FETCH_TF[interval] if oldest is not None else None, last_bar_start=oldest,
                                          now=now or datetime.now(timezone.utc))
    return universe, problems, f"broker:{record.broker_name}"


def _symbol_frames(bars: Any, interval: str, base_tf: str, include_forming: bool, now: Optional[datetime]) -> Tuple[Any, Dict[str, Any], str]:
    """(closed bars as a frame, {base_tf: frame}, problem) for one symbol's fetched bars - `fetch_frames`' pandas step."""
    df = bars_to_dataframe(bars) if bars else pd.DataFrame()
    if df.empty:
        return df, {}, "no bars from the broker"
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    fetched_tf = _FETCH_TF[interval]
    if not include_forming:
        df = closed_only(df, fetched_tf, now or datetime.now(timezone.utc))  # never the bar still forming
    if df.empty:
        return df, {}, "no closed bars from the broker"
    return df, {base_tf: df if fetched_tf == base_tf else resample(df, fetched_tf, base_tf, keep_forming=include_forming)}, ""


async def _attach_daily(service: Any, universe: List[SymbolData], exchange: str, now: Optional[datetime]) -> None:
    """U6 BK1 verification (A): an intraday screen that reads a daily-or-coarser timeframe gets the broker's daily history
    (completed sessions, about 400 calendar days) as its "1d" frame; the runtime builds @1d / @1w / @1M from it instead of
    from the short intraday window (60 days at most), so a higher-timeframe trend can be read. Closed bars only. A symbol
    whose daily fetch fails keeps the intraday-built context (shorter; a trend there may read as unknown)."""
    from app.cache.client import cache_get, cache_set
    from app.core.models import OHLCVBar
    from app.market_data.calendar import IST
    from app.market_data.service import HISTORY_CACHE_TTL_SECONDS
    session_day = (now or datetime.now(timezone.utc)).astimezone(IST).date().isoformat()
    scope = f"{getattr(service, 'cache_namespace', None) or 'shared'}:{getattr(getattr(service, 'broker', None), 'name', 'broker')}"
    for data in universe:
        # BK1 verification F2: completed sessions do not change during the day - one fetch per symbol per session date
        # (kept HISTORY_CACHE_TTL_SECONDS), under the session's own namespace (BT1b), not every alert cycle
        key = f"md:dailyctx:{scope}:{exchange}:{data.symbol}:{session_day}"
        cached = await cache_get(key)
        if cached:
            bars = [OHLCVBar.model_validate(b) for b in json.loads(cached)]
        else:
            try:
                bars = await service.get_candles(data.symbol, exchange, "day")
            except Exception:  # noqa: BLE001 - the screen still runs on the intraday-built context
                logger.warning("daily context fetch failed for %s", data.symbol, exc_info=True)
                continue
            if bars:
                await cache_set(key, json.dumps([b.model_dump(mode="json") for b in bars]), HISTORY_CACHE_TTL_SECONDS)
        df = bars_to_dataframe(bars) if bars else pd.DataFrame()
        if df.empty:
            continue
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        df = closed_only(df, "1d", now or datetime.now(timezone.utc))
        if not df.empty:
            data.frames["1d"] = df


def screen_and_funnel(ast: Any, validated: Any, universe: List[SymbolData], *, base_tf: str, params: Dict[str, Any],
                      vectorized: bool) -> Tuple[List[Match], Dict[str, Any]]:
    """The screen's matches and its funnel (`stage_survivors`) on one worker thread. LD-7: one hop off the event loop, not
    two, and one evaluation memo for both (`runtime.ScreenMemo`): the funnel's stages are the screen's own top-level
    items, so they are read back from the full evaluation instead of computed a second time."""
    memo: ScreenMemo = {}
    matches = run_screen(ast, validated, universe, base_tf=base_tf, params=params, vectorized=vectorized, memo=memo)
    stages = stage_survivors(ast, validated, universe, base_tf=base_tf, params=params, matches=matches, vectorized=vectorized, memo=memo)
    return matches, stages


async def rank_systems(session: AsyncSession, tenant_id: int, ast: Any, params: Dict[str, Any], universe: List[SymbolData]
                       ) -> Dict[str, Any]:
    """N3 S7a: the ranking systems a screen's RankScore names (validated by `_gate`), and - when one is sector-neutral -
    each symbol's NSE sector today on its SymbolData."""
    if not ranking_gate.uses_rankscore(ast):
        return {}
    systems, _ = await ranking_gate.resolve(session, tenant_id, ranking_gate.names_in(ast, params))
    if any(s.sector_neutral for s in systems.values()) and universe:
        from app.market_data.calendar import IST
        today = datetime.now(timezone.utc).astimezone(IST).date()
        ranges = await ranking_gate.sector_ranges(session, [d.symbol for d in universe], today, today)
        for d in universe:
            d.sector = ranking_gate.sector_on(ranges.get(d.symbol, []), today)
    return systems


def missing_data(ast: Any) -> List[str]:
    """Functions in the screen whose data the run path does not load (`Spec.needs` not in RUN_PROVIDES)."""
    out: List[str] = []
    for node in nodes.walk(ast):
        spec = FUNCTIONS.get(getattr(node, "name", "")) if isinstance(node, nodes.Call) else None
        if spec and any(need not in RUN_PROVIDES for need in spec.needs) and spec.name not in out:
            out.append(spec.name)
    return out


@router.post("/run", dependencies=[Depends(run_rate_limit)])
async def run(body: RunBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Runs a saved screen or ad-hoc source over up to 50 symbols, on server bars only; stores the run."""
    await _flag(session, user)
    started = time.monotonic()
    screen_id = None
    screen_name: Optional[str] = None
    if body.screen_id is not None:
        row = await _owned(session, user, body.screen_id)
        screen_name = row.name
        source: Union[str, Dict[str, Any]] = json.loads(row.ast_json)
        base_tf, params, screen_id = row.base_tf, {**json.loads(row.params_json or "{}"), **body.params}, row.id
    elif body.source is not None:
        source, base_tf, params = body.source, body.base_tf, dict(body.params)
    else:
        raise HTTPException(status_code=422, detail="give a screen_id or a source")
    ast, validated = _compiled(source, base_tf, params)
    await _gate(session, user, ast, validated, params, body.exchange)
    if not validated.ok:
        raise HTTPException(status_code=422, detail={"message": "the screen did not pass validation", "problems": [p.as_dict() for p in validated.problems]})
    missing = missing_data(ast)
    if missing:
        raise HTTPException(status_code=422, detail={"message": f"{', '.join(m + '()' for m in missing)} need data the screener run does not load "
                                                                "yet (sector / index / F&O lists or the option chain); a run uses bars only",
                                                     "problems": [{"message": f"{m}() needs data the run does not load yet", "pos": None} for m in missing]})
    symbols = list(dict.fromkeys(s.strip().upper() for s in body.symbols if s.strip()))
    # Z10-d: same matches, less time. LD-7: read before the fetch, while this request still holds its connection (read
    # after it, the flag took a second pool checkout and a query per run).
    vectorized = await flag_enabled(session, VECTOR_FLAG, user.tenant_id)
    tier: Dict[str, Any] = {}
    universe, fetch_problems, data_source = await fetch_frames(session, user.tenant_id, symbols, body.exchange, base_tf, validated.fetch_lookback,
                                                               user_id=user.id, tier_out=tier, release=True)
    if zone_gate.flagged_calls(ast):              # Z7a-3: the tenant's zone snapshots as of each bar's close
        await zone_gate.attach_zones(session, user.tenant_id, body.exchange, universe, datetime.now(timezone.utc), ast, base_tf, params)
    if fundamentals_gate.flagged_calls(ast):      # U7 LG3: the reported figures as known by each bar's close (point in time)
        await fundamentals_gate.attach(session, universe, datetime.now(timezone.utc), tenant_id=user.tenant_id)
    if ownership_gate.flagged_calls(ast):         # N3 S8a: the shareholding patterns and events known by each bar's close
        await ownership_gate.attach(session, universe, body.exchange, datetime.now(timezone.utc), viewer=user.tenant_id)
    systems = await rank_systems(session, user.tenant_id, ast, params, universe)       # N3 S7a: RankScore's systems
    await release_connection(session)            # LD-7: reads only so far; no pool connection held while the screen runs
    # the evaluation is pure CPU (no database): off the event loop, so one heavy screen does not stall other requests
    # (RankScore's systems ride the context asyncio.to_thread copies into the worker - N3 S7a)
    with ranking_gate.active(systems):
        matches, stages = await asyncio.to_thread(screen_and_funnel, ast, validated, universe, base_tf=base_tf, params=params,
                                                  vectorized=vectorized)
    results: List[Dict[str, Any]] = [{"symbol": m.symbol, "matched": m.matched, "reason": m.reason} for m in matches]
    results += [{"symbol": s, "matched": False, "reason": why} for s, why in fetch_problems.items()]
    payload = {"text": nodes.to_text(ast), "base_tf": base_tf, "data_source": data_source, "scanned": len(symbols),
               "matched": [r["symbol"] for r in results if r["matched"]], "results": results,
               "funnel": {"universe": len(symbols), **stages}, "disclaimer": DISCLAIMER}
    if tier:
        payload["data_tier"] = tier["data_tier"]          # BT1b: the tier the run actually used (flag byob_data_tier)
    if body.preview:
        # U5 D2 review: a preview is not stored, so it returns counts only - the match list itself comes from a Run,
        # which is stored and can be reproduced.
        counts = {"universe": len(symbols), "with_data": stages["with_data"],
                  "stages": [{"text": st["text"], "survivors": st["survivors"]} for st in stages["stages"]]}
        preview = {"run_id": None, "preview": True, "text": payload["text"], "base_tf": base_tf, "data_source": data_source,
                   "scanned": len(symbols), "matched_count": sum(1 for r in results if r["matched"]), "funnel": counts, "disclaimer": DISCLAIMER}
        if tier:
            preview["data_tier"] = tier["data_tier"]
        return preview
    ast_json = json.dumps(nodes.to_json(ast), sort_keys=True)
    run_row = ScreenRunRecord(tenant_id=user.tenant_id, user_id=user.id, screen_id=screen_id, ast_sha256=hashlib.sha256(ast_json.encode()).hexdigest(),
                              ast_version=nodes.VERSION, base_tf=base_tf, universe_json=json.dumps(symbols), data_source=data_source,
                              scanned=len(symbols), matched=sum(1 for r in results if r["matched"]), result_json=json.dumps(results),
                              duration_ms=int((time.monotonic() - started) * 1000))
    session.add(run_row)
    from app.screener import evidence_store
    matched_symbols = [str(r["symbol"]) for r in results if r["matched"]]
    if matched_symbols and await evidence_store.enabled(session, user.tenant_id):
        # U8 EV1: the matched symbols' evidence packs, from the same bars, parameters and ranks the screen just ran on
        await session.flush()
        ranking_token = ranking_gate.ACTIVE.set(systems)          # N3 S7a: the packs read the same RankScore systems
        try:
            ids = await evidence_store.store_for_run(session, tenant_id=user.tenant_id, user_id=user.id, run_id=run_row.id, ast=ast,
                                                     validated=validated, universe=universe, matched=matched_symbols, base_tf=base_tf,
                                                     params=params, exchange=body.exchange, data_source=data_source,
                                                     screen_name=screen_name, per_user=bool(tier))   # BT1b: the runner's own session
        finally:
            ranking_gate.ACTIVE.reset(ranking_token)
        for r in results:
            r["evidence_id"] = ids.get(str(r["symbol"]))
        run_row.result_json = json.dumps(results)
    await session.commit()
    return {"run_id": run_row.id, "preview": False, **payload}


@router.get("/runs/{run_id}")
async def get_run(run_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _flag(session, user)
    query = select(ScreenRunRecord).where(ScreenRunRecord.id == run_id, ScreenRunRecord.tenant_id == user.tenant_id)
    from app.market_data import live_access
    if await live_access.byob_on(session, user.tenant_id):
        query = query.where(ScreenRunRecord.user_id == user.id)       # BT1b: a run read the runner's own broker feed
    row = await session.scalar(query)
    if row is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"run_id": row.id, "screen_id": row.screen_id, "ast_sha256": row.ast_sha256, "version": row.ast_version, "base_tf": row.base_tf,
            "universe": json.loads(row.universe_json), "data_source": row.data_source, "scanned": row.scanned, "matched": row.matched,
            "results": json.loads(row.result_json), "created_at": row.created_at.isoformat() if row.created_at else None, "disclaimer": DISCLAIMER}


__all__ = ["router", "FLAG", "GALLERY_FLAG", "LEGENDS_FLAG", "fetch_frames"]
