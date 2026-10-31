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
import json
import logging
import math
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Dict, List, Optional, Tuple, Union

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.compliance_terms import ai_acknowledged as _ai_acknowledged
from app.ai.rate_limit import ai_rate_limit as _ai_rate_limit
from app.auth.dependencies import get_current_user, require_trader
from app.core.models import bars_to_dataframe
from app.core.rate_limit import user_rate_limit
from app.db.models import ScreenRecord, ScreenRunRecord, User
from app.db.session import get_session, release_connection
from app.platform.controls import flag_enabled, require_flag
from app.screener import amw_functions as amw_gate
from app.screener import compile_screen, nodes, reasons
from app.screener import fundamentals as fundamentals_gate
from app.screener import legends as lg
from app.screener import ownership as ownership_gate
from app.screener import ranking as ranking_gate  # registers the `legends` template source (U7 LG1)
from app.screener import taxonomy as tx
from app.screener import venues as venue_gate
from app.screener import waves as waves_gate
from app.screener import zones as zone_gate
from app.screener.breakouts import gate as breakout_gate
from app.screener.panel import FLAG as VECTOR_FLAG
from app.screener.registry import FUNCTIONS, describe
from app.screener.runtime import Match, ScreenMemo, SymbolData, closed_only, resample, run_screen, stage_survivors
from app.screener.validator import Problem, Validated

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


async def run_allowed(session: AsyncSession, user: User) -> None:
    """N3 S9 (review MED-1): the gate a screener run passes when it is not a route call - the Copilot's run_screen and
    explain_results act for the caller, so they need the caller's trading role and draw on the same per-user run limit
    as the Screener page (`screener_run`). Raises the route's own 403 / 429."""
    from app.auth.dependencies import ensure_trader
    await ensure_trader(session, user)
    await charge_runs(user, 1)


# Data the run path loads today: bars, and the stored market-zone snapshots (Z7a-3). A screen using a function that needs
# more is refused with the reason (the U1-d universe data and the option chain join here when they are wired in).
# U7 LG3: and the point-in-time fundamentals. N3 S8a: and the ownership patterns and events known by each bar's close.
# N3 S7a: and the ranking systems a RankScore names (loaded by the route, with the sectors a sector-neutral one needs).
RUN_PROVIDES: frozenset = frozenset({"zones", "fundamentals", "ownership", "rankings"})
MAX_SYMBOLS = 50
DISCLAIMER = "Matches are symbols that passed the screen's filters on the data shown; they are not recommendations."
SESSION_MINUTES = 375              # NSE cash session 09:15-15:30
HOLIDAY_PAD_DAYS = 4               # exchange holidays inside the window
MAX_INTRADAY_FETCH_DAYS = 60      # fetched in requests of at most 30 days each (service.INTRADAY_DAYS_PER_REQUEST)
HOLIDAYS_PER_YEAR = 16             # SCR-FIX A3: exchange holidays a year of daily bars loses (NSE: about 15)
BUDGET_WAIT = "waited for the broker's limit - run again shortly"   # a batch symbol the API budget held back
FETCH_CONCURRENCY = 3     # LD-7: one screen run's broker fetches in flight at once (capped by the broker's burst)


def fetch_concurrency(broker_name: str) -> int:
    """How many of one screen run's symbol fetches may be in flight at once: the broker's burst allowance in the worker's
    rate budget (app/brokers/rate_budget.py - about a third of the published limit; 3 for a broker it does not list,
    Dhan among them), never more than FETCH_CONCURRENCY."""
    from app.brokers.rate_budget import limits_for
    return max(1, min(FETCH_CONCURRENCY, limits_for(broker_name).burst))
# SCR-FIX A1: a screen timeframe's own interval; what goes to the broker is `screen_interval` (native, or a native divisor
# resampled up). Weeks and months are built from daily bars by the runtime.
_FETCH = {"1m": "1min", "3m": "3min", "5m": "5min", "15m": "15min", "30m": "30min", "1h": "60min", "1d": "day", "1w": "day", "1M": "day"}
_FETCH_TF = {"1min": "1m", "3min": "3m", "5min": "5m", "15min": "15m", "30min": "30m", "60min": "1h", "day": "1d"}


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
    # LAB-18: the builder blocks as the page saved them ({stages, texts, summaries}) - kept with the new version so
    # "Open this version" restores the same blocks and its change line reads in plain words. Optional; never run.
    builder: Optional[Dict[str, Any]] = None


class RunBody(BaseModel):
    screen_id: Optional[int] = None
    source: Optional[Union[str, Dict[str, Any]]] = None
    base_tf: str = Field(default="1d", pattern=r"^(1m|3m|5m|15m|30m|1h|1d|1w|1M)$")
    params: Dict[str, Union[float, int, str, bool]] = Field(default_factory=dict)
    symbols: List[str] = Field(min_length=1, max_length=MAX_SYMBOLS)
    exchange: str = Field(default="NSE", pattern=r"^(NSE|BSE|NFO|BFO|MCX|CDS)$")
    preview: bool = Field(default=False, description="U5 D2 live counts while editing: the same evaluation, not stored as a run")
    batch: bool = Field(default=False, description="one batch of a larger universe (app/screener/universes.py): every broker "
                                                   "call takes a token from the credential's shared API budget")


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
        if waves_gate.FLAG in off:                # wave templates R7: RetestEntry without its wave choices
            entries = waves_gate.hide(entries)
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
    wave count functions (`screener_waves`), the AMW-3 functions, v2 candles and confirmation statuses
    (`screenql_amw_functions`; `params` resolves a `$parameter` pattern name), N3 S8c's venue matrix (`screener_venues`,
    with an exchange) and N3 S7a's RankScore (`screener_rankings`; a system it names must be a platform one or this
    organisation's own). With the AMW-3 flag off, Pattern* mistakes get main's messages (`amw_gate.flag_off_validation`).
    Every gate runs; RankScore's check is last so its early return skips none."""
    if amw_gate.uses_patterns(ast) and not await amw_gate.enabled(session, user.tenant_id):
        amw_gate.flag_off_validation(ast, validated, params or {})
    for gate in (breakout_gate, zone_gate, fundamentals_gate, ownership_gate, waves_gate):
        # the wave gate resolves a `$parameter` RetestEntry choice (R7: of wave_line / wave_end) from the screen's parameters
        blocked = await (waves_gate.blocked_calls(session, user.tenant_id, ast, params or {}) if gate is waves_gate
                         else gate.blocked_calls(session, user.tenant_id, ast))
        if blocked:
            validated.ok = False
            validated.problems.append(Problem(gate.refusal(blocked), kind="flag_off", names={"functions": ",".join(blocked)}))
    amw_blocked = await amw_gate.blocked_calls(session, user.tenant_id, ast, params or {})
    if amw_blocked:
        validated.ok = False
        # N3 S9 (round cap): registry names only for the NL box - "Name()" labels as their function name; a Pattern
        # call's text is left out (the box then shows its fixed "could not be used" text)
        validated.problems.append(Problem(amw_gate.refusal(amw_blocked), kind="flag_off",
                                          names={"functions": ",".join(b[:-2] for b in amw_blocked if b.endswith("()"))}))
    if exchange and ast is not None and await venue_gate.enabled(session, user.tenant_id):
        missing = venue_gate.unavailable(ast, exchange)          # N3 S8c: what this venue's data does not carry
        if missing:
            validated.ok = False
            validated.problems.extend(Problem(m, kind="venue") for m in missing)
    if ranking_gate.uses_rankscore(ast):
        if not await ranking_gate.enabled(session, user.tenant_id):
            validated.ok = False
            validated.problems.append(Problem(ranking_gate.refusal(), kind="flag_off", names={"functions": "RankScore"}))
            return
        found, problems = await ranking_gate.resolve(session, user.tenant_id, ranking_gate.names_in(ast, params))
        if problems:
            validated.ok = False
            validated.problems.extend(Problem(p, kind="ranking_system") for p in problems)
        if exchange and await venue_gate.enabled(session, user.tenant_id):
            # re-check of S8: a named system's factors read this venue's data too - the same venue check as the screen's
            for name, system in found.items():
                for leaf in system.leaves():
                    for m in venue_gate.unavailable(leaf.ast, exchange):
                        validated.ok = False
                        validated.problems.append(Problem(f'RankScore("{name}"), factor "{leaf.name}" - {m}', kind="venue"))


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
    await session.flush()
    await _record_version(session, user, row, body)
    await session.commit()
    return _screen_dict(row)


async def _record_version(session: AsyncSession, user: User, row: ScreenRecord, body: ScreenBody) -> None:
    """LAB-18 (AMW-7): every save that changes the screen writes an immutable version (flag `screener_copilot`)."""
    from app.screener import versions
    if await flag_enabled(session, versions.FLAG, user.tenant_id):
        await versions.record(session, row, user.id, body.builder)


@router.put("/screens/{screen_id}")
async def update_screen(screen_id: int, body: ScreenBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _flag(session, user)
    row = await _owned(session, user, screen_id)
    await _apply(session, user, row, body)
    await _rules_still_valid(session, user, row)
    await _record_version(session, user, row, body)
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
            logging.getLogger(__name__).info("screen change breaks alert rule %s: %s", rule_id, detail)   # UI-28 U3.1: the id stays in the log
            raise HTTPException(status_code=422, detail=f"One of your alerts runs this screen and could not after this change: {detail}") from exc


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


def daily_fetch_days(lookback: Dict[str, int], base_tf: str) -> int:
    """SCR-FIX A3: calendar days of daily bars for the daily-or-coarser part of a lookback - sized by the need (1w / 1M
    screens read years), as the stored-history path does (`stored_history.stored_days`), plus each year's exchange
    holidays. The service never fetches less than its default daily window (DAILY_MIN_LOOKBACK_DAYS)."""
    from app.screener.stored_history import stored_days
    days = stored_days(lookback or {base_tf: 1}, base_tf)
    return days + math.ceil(days / 365) * HOLIDAYS_PER_YEAR


def screen_interval(adapter: Any, base_tf: str) -> str:
    """SCR-FIX A1: the interval a screen at `base_tf` is fetched at through `adapter` - the timeframe's own interval when
    the broker serves it, else the largest native one that divides it (the screener resamples it, 09:15-anchored)."""
    from app.market_data.intervals import fetch_interval
    return fetch_interval(adapter, _FETCH[base_tf], among=_FETCH_TF)


async def _closed_for_the_day(session: AsyncSession, exchange: str, now: datetime) -> bool:
    """SCR-FIX A4: today is a trading day of the exchange and its session has closed - today's daily bar is final, though
    the broker's daily history has it only from tomorrow (as Charts' `_today_state`)."""
    from app.market_data.calendar import EXCHANGE_SESSIONS, IST, family_holidays, session_family
    family = session_family(exchange)
    spec = EXCHANGE_SESSIONS[family]
    if spec.always_open:
        return False
    now_ist = now.astimezone(IST)
    if (spec.weekdays_only and now_ist.weekday() >= 5) or now_ist.time() < spec.close:
        return False
    holidays = await family_holidays(session, family) if session is not None else set()   # no database: weekends only
    return now_ist.date() not in holidays


async def fetch_frames(session: AsyncSession, tenant_id: int, symbols: List[str], exchange: str, base_tf: str,
                       lookback: Dict[str, int], now: Optional[datetime] = None, include_forming: bool = False, *,
                       user_id: Optional[int] = None, tier_out: Optional[Dict[str, Any]] = None,
                       release: bool = False, budget_out: Optional[Dict[str, Any]] = None,
                       store: bool = False) -> Tuple[List[SymbolData], Dict[str, str], str]:
    """Server bars through the organisation's broker session -> (universe, per-symbol fetch problems, data source).
    M71 PL-3 (`store`, the run route under the flag `market_store_reads` - `store_first`): the bars come store-first
    (`_store_frames`, app/market_store) - a closed-bar screen only; `include_forming` (intrabar alerts) keeps the broker
    path below. Breakouts, library, ranking and the Copilot screener tool pass it too (M71 W2-G, with `budget_out` so
    every broker call left is paced); alerts keep it off (M71 wave 2).
    A missing session raises 409 (the fix is on the Broker page); never a fallback to client data.
    `include_forming` keeps the bar still forming (S4b-2 intrabar alerts only; screens decide on closed bars).

    BT1b (flag `byob_data_tier`): the bars come only through the session of the user the run / alert rule belongs to
    (`user_id`), at a broker whose data terms are confirmed, cached under that session's namespace. No such session ->
    409 (Tier C). SCR-FIX A6: symbols outside the session's live budget are still read - this is history, not a live
    slot - through the batch path (the credential's API budget, api_budget.BudgetedAdapter), or for a daily-or-coarser
    screen the stored daily bars when the API budget is used up; `tier_out` gets their count ("held_back"). An intrabar
    read (`include_forming`) keeps them as EOD-only problems. `tier_out` receives the data-tier badge of what was read.
    `release` (LD-7, a request whose session has only read so far): the pool connection goes back before the broker
    calls; the worker's callers keep their transaction.
    `budget_out` (owner order 23 Oct, a batch of a larger universe): every broker call takes a token from the
    credential's shared API budget first (api_budget.BudgetedAdapter, as a chart's); a symbol without one is reported
    "budget used". It receives the calls made and refused and the per-minute share the page paces the next batch by.

    SCR-FIX: the interval goes to the broker only when the broker serves it (A1, `screen_interval`); a daily window is
    sized by the lookback (A3); after the session's close a 1d / 1w / 1M screen gets today's finished bar (A4); an
    index takes its near-month future's volume, as Charts do (A7)."""
    from app.brokers.token_lifecycle import build_adapter
    from app.market_data import api_budget, live_access
    from app.market_data.candles_routes import _pick_record
    from app.market_data.fanin import Priority
    from app.market_data.service import MarketDataService
    from app.screener.universes import batch_wait_seconds
    if store and not include_forming:
        return await _store_frames(session, tenant_id, symbols, exchange, base_tf, lookback, now, user_id=user_id, tier_out=tier_out,
                                   release=release, budget_out=budget_out)
    now_utc = now or datetime.now(timezone.utc)
    daily_tf = nodes.TF_MINUTES["1d"]
    want = _FETCH[base_tf]
    attach_daily = want != "day" and any(nodes.TF_MINUTES.get(tf, 0) >= daily_tf for tf in lookback)
    # BK1 verification F3: with the daily history attached, @1d / @1w / @1M come from it, so only the intraday timeframes
    # size the intraday window (a 15m screen with daily context fetches days, not the 60-day cap)
    window = {tf: b for tf, b in lookback.items() if nodes.TF_MINUTES.get(tf, 0) < daily_tf} if attach_daily else lookback
    daily_window = {tf: b for tf, b in lookback.items() if nodes.TF_MINUTES.get(tf, 0) >= daily_tf}
    days = fetch_days(window or {base_tf: 1}, base_tf) if want != "day" else daily_fetch_days(lookback, base_tf)
    problems: Dict[str, str] = {}
    own = None
    user = None
    held: List[str] = []
    if await live_access.byob_on(session, tenant_id):
        user = await live_access.load_user(session, user_id)
        if user is not None and user.tenant_id != tenant_id:
            user = None                                   # never another organisation's member
        own = await live_access.user_source(session, user, [(s, exchange) for s in symbols], priority=Priority.SCREENER)
        record = own.record
        adapter: Any = own.adapter()
        outside = [s for s in symbols if not own.allows(s, exchange)]
        if include_forming:
            problems.update({s: reasons.Reason(own.reason(s, exchange), reasons.SESSION_BUDGET) for s in outside})
        else:
            held = outside                                # A6: read through the API budget below, not dropped
        symbols = [s for s in symbols if own.allows(s, exchange)]
    else:
        record = await _pick_record(session, tenant_id, None, "primary")
        adapter = build_adapter(record)
    if budget_out is not None:
        adapter = api_budget.BudgetedAdapter(adapter, record.id, record.broker_name,
                                             wait_seconds=batch_wait_seconds(record.broker_name, len(symbols) + len(held)))
    interval = screen_interval(adapter, base_tf)
    service = own.market_data(adapter, lookback_days=days) if own is not None else MarketDataService(adapter, lookback_days=days)
    held_adapter: Any = None
    held_service: Any = None
    if held and own is not None:
        held_adapter = adapter if budget_out is not None else api_budget.BudgetedAdapter(
            own.adapter(), record.id, record.broker_name, wait_seconds=batch_wait_seconds(record.broker_name, len(held)))
        held_service = own.market_data(held_adapter, lookback_days=days)
    attach_today = interval == "day" and not include_forming and await _closed_for_the_day(session, exchange, now_utc)
    futures = await _index_futures(session, user, record, [*symbols, *held], exchange, now_utc, own is not None)
    if release:
        await release_connection(session)
    universe: List[SymbolData] = []
    newest: List[datetime] = []
    # LD-7: the symbols are fetched a few at a time instead of one after another (a cold five-symbol run waited for five
    # broker round trips in a row). At most the broker's burst allowance at once (`fetch_concurrency`); the results keep
    # the symbols' order.
    gate = asyncio.Semaphore(fetch_concurrency(record.broker_name))

    async def bars_of(svc: Any, symbol: str, sym_exchange: str) -> Any:
        return await (svc.get_candles(symbol, sym_exchange, interval, fresh=True) if include_forming  # no cached copy
                      else svc.get_candles(symbol, sym_exchange, interval))

    async def fetch_one(symbol: str, svc: Any) -> Tuple[Any, Any, str]:
        async with gate:
            try:
                bars = await bars_of(svc, symbol, exchange)
            except Exception as exc:  # noqa: BLE001 - one symbol's failure is reported, the run continues
                # SCR-FIX B3: a reason code + a plain sentence; the full text (never cut) in the log; the API budget
                # (a batch's symbol with no token) caught by class, keeping the text the page offers to run again
                return None, None, reasons.from_exception(exc, symbol=symbol, wait_text=BUDGET_WAIT)
            today_bar = await _today_bar(svc, symbol, exchange, now_utc) if attach_today else None
            fut = futures.get(symbol)
            if fut is not None and bars:
                bars, today_bar = await _future_volume(svc, fut, bars, today_bar, lambda: bars_of(svc, fut.symbol, fut.exchange), now_utc)
            return bars, today_bar, ""

    pairs = [(s, service) for s in symbols] + [(s, held_service) for s in held]
    got = await asyncio.gather(*(fetch_one(symbol, svc) for symbol, svc in pairs))

    def frames_of_all() -> List[Tuple[Any, Dict[str, Any], str]]:
        return [(None, {}, failed) if failed else _symbol_frames(bars, interval, base_tf, include_forming, now, today=today_bar, exchange=exchange)
                for bars, today_bar, failed in got]

    # LD-7: the pandas work runs on a worker thread, so a screen does not hold the event loop - one thread for the run's
    # symbols in turn, not one each (more CPU-bound threads only take the GIL from the event loop more often).
    fetched = await asyncio.to_thread(frames_of_all)
    held_data: List[SymbolData] = []
    for (symbol, svc), (df, frames, problem) in zip(pairs, fetched):
        if problem:
            problems[symbol] = problem
            continue
        data = SymbolData(symbol, frames, exchange=exchange)          # U6 BK2: the session clock of the symbol
        universe.append(data)
        if held_service is not None and svc is held_service:
            held_data.append(data)
        newest.append(df.index[-1].to_pydatetime())
    if attach_daily and universe:
        live_data = [d for d in universe if all(d is not h for h in held_data)]
        daily_days = daily_fetch_days(daily_window, "1d")
        if live_data:
            daily_adapter = adapter if budget_out is not None else (own.adapter() if own is not None else build_adapter(record))
            daily_service = (own.market_data(daily_adapter, lookback_days=daily_days) if own is not None
                             else MarketDataService(daily_adapter, lookback_days=daily_days))
            await _attach_daily(daily_service, live_data, exchange, now)       # U6 BK1: through the same (BT1b) source
        if held_data and own is not None:
            await _attach_daily(own.market_data(held_adapter, lookback_days=daily_days), held_data, exchange, now)
    if held:
        await _stored_for_refused(session, tenant_id, held, problems, universe, exchange, base_tf, lookback)
    if own is not None and tier_out is not None:
        # Timed by the data (the stalest symbol's newest bar); nothing read = age unknown = stale.
        oldest = min(newest) if newest else None
        tier_out["data_tier"] = own.badge(timeframe=_FETCH_TF[interval] if oldest is not None else None, last_bar_start=oldest,
                                          now=now or datetime.now(timezone.utc))
        if held:
            tier_out["held_back"] = len(held)
    if budget_out is not None:
        from app.market_data.api_budget import api_limits
        budget_out.update({"broker_calls": int(getattr(adapter, "taken", 0)), "refused": int(getattr(adapter, "refused", 0)),
                           "per_minute": api_limits(record.broker_name).per_minute})
    return universe, problems, f"broker:{record.broker_name}"


async def store_first(session: AsyncSession, tenant_id: int) -> bool:
    """M71 PL-3: the flag `market_store_reads` - scans read the own store first (app/market_store)."""
    from app.market_store.models import FLAG as STORE_FLAG
    return await flag_enabled(session, STORE_FLAG, tenant_id)


async def _store_rest(session: AsyncSession, tenant_id: int, user: Any, symbols: list[str], exchange: str,
                      wait_seconds: float | None) -> tuple[Any, set]:
    """(the reader's OWN session as the newest bars' REST source or None, the symbols it may top up) - M71Q-2: never
    another member's session (`market_store.sources.from_record`)."""
    from app.brokers.token_lifecycle import build_adapter
    from app.market_data import live_access
    from app.market_data.candles_routes import _pick_record
    from app.market_data.fanin import Priority
    from app.market_store import sources
    if user is None or not symbols:
        return None, set()
    try:
        if await live_access.byob_on(session, tenant_id):
            own = await live_access.user_source(session, user, [(s, exchange) for s in symbols], priority=Priority.SCREENER)
            rest = sources.from_record(user, own.record, own.adapter(), wait_seconds)
            return rest, ({s for s in symbols if own.allows(s, exchange)} if rest is not None else set())
        record = await _pick_record(session, tenant_id, None, "primary")
    except HTTPException:
        return None, set()
    rest = sources.from_record(user, record, build_adapter(record), wait_seconds) if sources.may_top_up(user, record) else None
    return rest, (set(symbols) if rest is not None else set())


async def _store_frames(session: AsyncSession, tenant_id: int, symbols: list[str], exchange: str, base_tf: str,
                        lookback: dict[str, int], now: datetime | None, *, user_id: int | None,
                        tier_out: dict[str, Any] | None, release: bool,
                        budget_out: dict[str, Any] | None) -> tuple[list[SymbolData], dict[str, str], str]:
    """M71 PL-3: a closed-bar screen's universe store-first (app/market_store, read order in its reader):
    - 1d / 1w / 1M on NSE: the NSE daily files only - zero broker calls; a symbol not stored is "not stored yet".
    - intraday: the reader's own stored one-minute bars built into the screen's timeframe, plus only the newest bars
      by REST through his own session, paced by the credential's API budget; the daily context (@1d / @1w / @1M) of an
      NSE screen from the NSE files. A symbol the store holds nothing of, an index whose volume comes from its future
      (SCR-FIX A7) and another exchange read through the broker path exactly as before (`fetch_frames(store=False)`).
    Same frames as the broker path (START-stamped, closed bars only), so equal bars give equal matches."""
    from app.backtest import bar_source
    from app.market_data import api_budget, index_volume
    from app.market_data.live_access import load_user
    from app.market_store import reader as ms
    from app.screener.universes import batch_wait_seconds
    now_utc = now or datetime.now(timezone.utc)
    daily_tf = nodes.TF_MINUTES["1d"]
    want = _FETCH[base_tf]
    syms = list(dict.fromkeys(s.strip().upper() for s in symbols if s and s.strip()))
    universe: list[SymbolData] = []
    problems: dict[str, str] = {}
    if want == "day":
        if not ms.eod_served(exchange):
            return await fetch_frames(session, tenant_id, syms, exchange, base_tf, lookback, now, user_id=user_id, tier_out=tier_out,
                                      release=release, budget_out=budget_out, store=False)
        start = now_utc - timedelta(days=daily_fetch_days(lookback, base_tf))
        got = await ms.read_many(session, syms, "1d", start, now_utc, purpose="screener", exchange=exchange, now=now_utc,
                                 release=(lambda: release_connection(session)) if release else None)
        for s in syms:
            r = got[s]
            if not r.served:
                problems[s] = reasons.Reason(bar_source.not_stored(s), reasons.NOT_STORED)
                continue
            frame = r.frame if base_tf == "1d" else resample(r.frame, "1d", base_tf, keep_forming=False, exchange=exchange)
            universe.append(SymbolData(s, {base_tf: frame}, exchange=exchange))
        if budget_out is not None:
            budget_out.update({"broker_calls": 0, "refused": 0, "per_minute": api_budget.api_limits("").per_minute})
        return universe, problems, bar_source.LAKE_SOURCE
    user = await load_user(session, user_id)
    if user is not None and user.tenant_id != tenant_id:
        user = None                                       # never another organisation's member
    legacy = [s for s in syms if index_volume.index_underlying(s, exchange) is not None or ms.intraday_key(s, exchange) is None]
    window = {tf: b for tf, b in lookback.items() if nodes.TF_MINUTES.get(tf, 0) < daily_tf} or {base_tf: 1}
    start = now_utc - timedelta(days=fetch_days(window, base_tf))
    stored = {s: await ms.store_intraday(session, s, exchange, base_tf, start, now_utc, user=user, now=now_utc)
              for s in syms if s not in legacy}
    legacy += [s for s, got in stored.items() if not got.minutes]
    served = [s for s in syms if s in stored and stored[s].minutes]
    daily_window = {tf: b for tf, b in lookback.items() if nodes.TF_MINUTES.get(tf, 0) >= daily_tf}
    daily: dict[str, Any] = {}
    if daily_window and served and ms.eod_served(exchange):
        dstart = now_utc - timedelta(days=daily_fetch_days(daily_window, "1d"))
        daily = await ms.read_many(session, served, "1d", dstart, now_utc, purpose="screener-context", exchange=exchange, now=now_utc)
    holidays = await ms.holidays_for(session, exchange)
    rest, allowed = await _store_rest(session, tenant_id, user, served, exchange, None)
    if release:
        await release_connection(session)
    if rest is not None and budget_out is not None:
        rest = replace(rest, wait_seconds=batch_wait_seconds(rest.broker, len(served)))
    budgeted = None if rest is None else ms.paced_adapter(rest)
    calls = 0
    topped = False
    for s in served:
        mine = rest if s in allowed else None
        r = await ms.top_up(stored[s], rest=mine, budgeted=budgeted if mine is not None else None, now=now_utc, holidays=holidays)
        calls += r.rest_calls
        topped = topped or "+broker:" in r.source
        if not r.served:
            problems[s] = reasons.Reason("no closed bars stored for this timeframe", reasons.NO_BARS)
            continue
        data = SymbolData(s, {base_tf: r.frame}, exchange=exchange)
        got_daily = daily.get(s)
        if got_daily is not None and got_daily.served:
            data.frames["1d"] = got_daily.frame
        universe.append(data)
    source = "lake:intraday" + (f"+broker:{rest.broker}" if topped and rest is not None else "")
    if legacy:
        try:
            more, more_problems, legacy_source = await fetch_frames(session, tenant_id, legacy, exchange, base_tf, lookback, now,
                                                                    user_id=user_id, tier_out=tier_out, release=False,
                                                                    budget_out=budget_out, store=False)
        except HTTPException:
            if not universe:
                raise                                     # nothing stored and no session: the broker path's own answer
            more, more_problems, legacy_source = [], {s: reasons.Reason(ms.CONNECT_FOR_INTRADAY, reasons.NOT_STORED) for s in legacy}, ""
        universe += more
        problems.update(more_problems)
        if legacy_source and not universe[: len(universe) - len(more)]:
            source = legacy_source
        elif legacy_source and more:
            source = f"{source}+{legacy_source}" if legacy_source not in source else source
    if budget_out is not None:
        budget_out["broker_calls"] = int(budget_out.get("broker_calls", 0)) + calls
        budget_out.setdefault("refused", int(getattr(budgeted, "refused", 0)))
        budget_out.setdefault("per_minute", api_budget.api_limits(rest.broker if rest is not None else "").per_minute)
    return universe, problems, source


async def _today_bar(service: Any, symbol: str, exchange: str, now: datetime) -> Any:
    """SCR-FIX A4: today's finished daily bar from today's one-minute bars (Charts' `get_today_bar`), None when it could
    not be read - the completed sessions still make the screen."""
    try:
        return await service.get_today_bar(symbol, exchange, now)
    except Exception as exc:  # noqa: BLE001 - logged; the screen runs on the completed sessions
        logger.info("today's bar not loaded for %s:%s: %s", exchange, symbol, exc)
        return None


async def _future_volume(service: Any, fut: Any, bars: Any, today_bar: Any, fetch: Any, now: datetime) -> Tuple[Any, Any]:
    """SCR-FIX A7: the index's bars with its near-month future's volume (`index_volume.replace_volume`, 0 where the future
    has no bar), as Charts do; the index's own bars unchanged when the future's could not be read."""
    from app.market_data import index_volume
    try:
        fut_bars = await fetch()
        if today_bar is not None:
            fut_today = await _today_bar(service, fut.symbol, fut.exchange, now)
            today_bar = index_volume.replace_volume([today_bar], [fut_today] if fut_today is not None else [])[0]
        return index_volume.replace_volume(bars, fut_bars), today_bar
    except Exception as exc:  # noqa: BLE001 - the index's own bars still make the screen
        logger.info("future volume not loaded for %s: %s", fut.symbol, exc)
        return bars, today_bar


async def _index_futures(session: AsyncSession, user: Any, record: Any, symbols: List[str], exchange: str, now: datetime,
                         byob: bool) -> Dict[str, Any]:
    """SCR-FIX A7: the near-month future of each index among `symbols` whose volume replaces the index's own (brokers
    send 0 for an index) - the condition Charts apply (`candles_routes`, `volume_from_future`): an index with futures, a
    contract in the platform's master, and with bring-your-own-broker on, the future inside the same session's
    entitlement. Read before the broker calls (the database session may be given back for them)."""
    from app.market_data import entitlement, index_volume
    from app.market_data.calendar import IST
    from app.market_data.fanin import Priority
    out: Dict[str, Any] = {}
    today = now.astimezone(IST).date()
    for symbol in symbols:
        if index_volume.index_underlying(symbol, exchange) is None:
            continue
        fut = await index_volume.near_future(session, symbol, exchange, today)
        if fut is None:
            continue
        if byob:
            if user is None:
                continue
            fent = await entitlement.resolve(session, user, fut.symbol, fut.exchange, broker=record.broker_name,
                                             account_label=record.account_label, priority=Priority.SCREENER)
            if not fent.live or fent.record is None or fent.record.id != record.id:
                continue
        out[symbol] = fut
    return out


async def _stored_for_refused(session: AsyncSession, tenant_id: int, held: List[str], problems: Dict[str, str],
                              universe: List[SymbolData], exchange: str, base_tf: str, lookback: Dict[str, int]) -> None:
    """SCR-FIX A6: a held-back symbol the API budget refused, on a daily-or-coarser NSE screen, reads ATP's stored daily
    bars instead (flag `backtest_stored_history`, `stored_history.stored_universe`); anything else keeps its problem."""
    from app.screener.stored_history import stored_universe
    refused = [s for s in held if problems.get(s) == BUDGET_WAIT]
    if not refused:
        return
    stored = await stored_universe(session, tenant_id, HTTPException(status_code=409, detail=BUDGET_WAIT), refused, exchange,
                                   base_tf, lookback)
    if stored is None:
        return
    for data in stored[0]:
        problems.pop(data.symbol, None)
        universe.append(data)


def _symbol_frames(bars: Any, interval: str, base_tf: str, include_forming: bool, now: Optional[datetime], *,
                   today: Any = None, exchange: Optional[str] = None) -> Tuple[Any, Dict[str, Any], str]:
    """(closed bars as a frame, {base_tf: frame}, problem) for one symbol's fetched bars - `fetch_frames`' pandas step.
    `today` (SCR-FIX A4): today's finished daily bar, added after the closed-bar cut (its session has closed)."""
    df = bars_to_dataframe(bars) if bars else pd.DataFrame()
    if df.empty:
        return df, {}, reasons.Reason("no bars from the broker", reasons.NO_BARS)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    fetched_tf = _FETCH_TF[interval]
    if not include_forming:
        df = closed_only(df, fetched_tf, now or datetime.now(timezone.utc), exchange)  # never the bar still forming
    if today is not None and fetched_tf == "1d":
        df = _with_today(df, today)
    if df.empty:
        return df, {}, reasons.Reason("no closed bars from the broker", reasons.NO_BARS)
    return df, {base_tf: df if fetched_tf == base_tf else resample(df, fetched_tf, base_tf, keep_forming=include_forming, exchange=exchange)}, ""


def _with_today(daily: pd.DataFrame, today: Any) -> pd.DataFrame:
    """`daily` plus today's finished bar, unless its date is already there (Charts: `_base_bars`, `merge_bars`)."""
    row = bars_to_dataframe([today])
    if row.index.tz is None:
        row.index = row.index.tz_localize("UTC")
    if len(daily):
        row.index = row.index.tz_convert(daily.index.tz)
        if row.index[0].tz_convert("Asia/Kolkata").date() <= daily.index[-1].tz_convert("Asia/Kolkata").date():
            return daily
        return pd.concat([daily, row])
    return row


async def _attach_daily(service: Any, universe: List[SymbolData], exchange: str, now: Optional[datetime]) -> None:
    """U6 BK1 verification (A): an intraday screen that reads a daily-or-coarser timeframe gets the broker's daily history
    (completed sessions, sized by the screen's daily lookback - SCR-FIX A3) as its "1d" frame; the runtime builds @1d /
    @1w / @1M from it instead of from the short intraday window (60 days at most), so a higher-timeframe trend can be
    read. Closed bars only. A symbol whose daily fetch fails keeps the intraday-built context (shorter; a trend there may
    read as unknown)."""
    from app.cache.client import cache_get, cache_set
    from app.core.models import OHLCVBar
    from app.market_data.calendar import IST
    from app.market_data.service import HISTORY_CACHE_TTL_SECONDS
    session_day = (now or datetime.now(timezone.utc)).astimezone(IST).date().isoformat()
    scope = f"{getattr(service, 'cache_namespace', None) or 'shared'}:{getattr(getattr(service, 'broker', None), 'name', 'broker')}"
    span = int(getattr(service, "lookback_days", 0) or 0)
    for data in universe:
        # BK1 verification F2: completed sessions do not change during the day - one fetch per symbol per session date
        # (kept HISTORY_CACHE_TTL_SECONDS), under the session's own namespace (BT1b), not every alert cycle. SCR-FIX A3:
        # keyed by the window too (a longer need is not answered with a shorter copy).
        key = f"md:dailyctx:{scope}:{exchange}:{data.symbol}:{span}d:{session_day}"
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


def held_back_notice(count: int) -> str:
    """SCR-FIX A6: the one line a run shows when symbols were outside the live session budget (they were still read)."""
    return f"{count} held back by the session budget - read from history instead"


def screen_and_funnel(ast: Any, validated: Any, universe: List[SymbolData], *, base_tf: str, params: Dict[str, Any],
                      vectorized: bool) -> Tuple[List[Match], Dict[str, Any]]:
    """The screen's matches and its funnel (`stage_survivors`) on one worker thread. LD-7: one hop off the event loop, not
    two, and one evaluation memo for both (`runtime.ScreenMemo`): the funnel's stages are the screen's own top-level
    items, so they are read back from the full evaluation instead of computed a second time."""
    memo: ScreenMemo = {}
    matches = run_screen(ast, validated, universe, base_tf=base_tf, params=params, vectorized=vectorized, memo=memo)
    stages = stage_survivors(ast, validated, universe, base_tf=base_tf, params=params, matches=matches, vectorized=vectorized, memo=memo)
    return matches, stages


async def load_universe(session: AsyncSession, user: User, ast: Any, validated: Any, params: Dict[str, Any], symbols: List[str],
                        exchange: str, base_tf: str, tier: Optional[Dict[str, Any]] = None, *, release: bool = False,
                        budget: Optional[Dict[str, Any]] = None, store: bool = False) -> Tuple[List[SymbolData], Dict[str, str], str, Dict[str, Any]]:
    """A validated screen's universe on server bars with the data its functions read: (universe, per-symbol fetch
    problems, data source, RankScore's systems). The run route and the N3 S9 Copilot tools share it. `release` (LD-7,
    the run route only - its session has only read so far): fetch_frames gives the pool connection back before the
    broker / lake reads; the Copilot tools keep the agent's shared session as it is."""
    try:
        universe, fetch_problems, data_source = await fetch_frames(session, user.tenant_id, symbols, exchange, base_tf, validated.fetch_lookback,
                                                                   user_id=user.id, tier_out=tier, release=release, budget_out=budget,
                                                                   store=store)
    except HTTPException as exc:
        # Day-one fix 2: before the day's broker login a Daily screen reads ATP's stored end-of-day history (flag
        # `backtest_stored_history`); anything else keeps the broker path's own answer.
        from app.screener.stored_history import stored_universe
        stored = await stored_universe(session, user.tenant_id, exc, symbols, exchange, base_tf, validated.fetch_lookback)
        if stored is None:
            raise
        universe, fetch_problems, data_source = stored
    if zone_gate.flagged_calls(ast):              # Z7a-3: the tenant's zone snapshots as of each bar's close
        await zone_gate.attach_zones(session, user.tenant_id, exchange, universe, datetime.now(timezone.utc), ast, base_tf, params)
    if fundamentals_gate.flagged_calls(ast):      # U7 LG3: the reported figures as known by each bar's close (point in time)
        await fundamentals_gate.attach(session, universe, datetime.now(timezone.utc), tenant_id=user.tenant_id)
    if ownership_gate.flagged_calls(ast):         # N3 S8a: the shareholding patterns and events known by each bar's close
        await ownership_gate.attach(session, universe, exchange, datetime.now(timezone.utc), viewer=user.tenant_id)
    systems = await rank_systems(session, user.tenant_id, ast, params, universe)       # N3 S7a: RankScore's systems
    return universe, fetch_problems, data_source, systems


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


def read_counts(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """SCR-FIX B1 / B2: how many symbols could not be read or checked, the grouped reason in one line with its ONE action
    (`unread_reason`: code, text, action, count) and the first symbol's reason (`first_error`) - from the result rows,
    so a run, a preview and a stored run count the same way."""
    rows = reasons.unread_rows(results)
    out: Dict[str, Any] = {"unread": len(rows)}
    if rows:
        out["unread_reason"] = reasons.summary(rows)
        first = rows[0]
        out["first_error"] = {"symbol": first["symbol"], "text": first["reason"], "code": first.get("code") or reasons.NOT_READ,
                              "action": first.get("action") or "retry"}
    return out


def nothing_read_detail(scanned: int, grouped: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """SCR-FIX B2: the run-level error when not one symbol could be read - the existing {message, problems} error shape,
    plus the reason code and the one action."""
    reason = (grouped or {}).get("text") or "The bars could not be read."
    what = "the stock" if scanned == 1 else f"any of the {scanned} stocks"
    message = f"Could not read {what}. {reason}"
    return {"message": message, "code": reasons.NOTHING_READ, "reason_code": (grouped or {}).get("code") or reasons.NOT_READ,
            "action": (grouped or {}).get("action") or "retry", "unread": scanned, "problems": [{"message": message, "pos": None}]}


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
    budget: Optional[Dict[str, Any]] = {} if body.batch else None
    store = await store_first(session, user.tenant_id)   # M71 PL-3: the run and its batches read the own store first
    universe, fetch_problems, data_source, systems = await load_universe(session, user, ast, validated, params, symbols, body.exchange,
                                                                         base_tf, tier, release=True, budget=budget, store=store)
    await release_connection(session)            # LD-7: reads only so far; no pool connection held while the screen runs
    # the evaluation is pure CPU (no database): off the event loop, so one heavy screen does not stall other requests
    # (RankScore's systems ride the context asyncio.to_thread copies into the worker - N3 S7a)
    with ranking_gate.active(systems):
        matches, stages = await asyncio.to_thread(screen_and_funnel, ast, validated, universe, base_tf=base_tf, params=params,
                                                  vectorized=vectorized)
    # SCR-FIX B1: a symbol that could not be read (fetch problem) or checked (too little history, no value) carries its
    # reason code + action and is counted apart from the symbols that were read and did not match
    results: List[Dict[str, Any]] = [reasons.row(m.symbol, m.matched, m.reason) for m in matches]
    results += [reasons.row(s, False, reasons.as_reason(why)) for s, why in fetch_problems.items()]
    unread = read_counts(results)
    if unread["unread"] and unread["unread"] >= len(symbols) and not body.preview and not body.batch:
        # SCR-FIX B2: nothing could be read - a run-level error with the grouped reason and its one action, never
        # "200 with 0 matches" (a preview / a batch of a longer list reports the counts instead)
        raise HTTPException(status_code=422, detail=nothing_read_detail(len(symbols), unread["unread_reason"]))
    payload = {"text": nodes.to_text(ast), "base_tf": base_tf, "data_source": data_source, "scanned": len(symbols),
               "matched": [r["symbol"] for r in results if r["matched"]], "results": results,
               "funnel": {"universe": len(symbols), **stages}, **unread, "disclaimer": DISCLAIMER}
    held_back = int(tier.pop("held_back", 0) or 0)
    if tier:
        payload["data_tier"] = tier["data_tier"]          # BT1b: the tier the run actually used (flag byob_data_tier)
    if held_back:
        # SCR-FIX A6: symbols outside the live session budget, still read (API budget / stored daily bars) - said once
        payload["session_held_back"] = held_back
        payload["notice"] = held_back_notice(held_back)
    if budget is not None:
        payload["budget"] = budget                        # a batch: its broker calls, so the page paces the next one
    if body.preview:
        # U5 D2 review: a preview is not stored, so it returns counts only - the match list itself comes from a Run,
        # which is stored and can be reproduced.
        counts = {"universe": len(symbols), "with_data": stages["with_data"],
                  "stages": [{"text": st["text"], "survivors": st["survivors"]} for st in stages["stages"]]}
        preview = {"run_id": None, "preview": True, "text": payload["text"], "base_tf": base_tf, "data_source": data_source,
                   "scanned": len(symbols), "matched_count": sum(1 for r in results if r["matched"]), "funnel": counts, **unread,
                   "disclaimer": DISCLAIMER}
        if tier:
            preview["data_tier"] = tier["data_tier"]
        if held_back:
            preview["session_held_back"] = held_back
            preview["notice"] = payload["notice"]
        if budget is not None:
            preview["budget"] = budget
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


class NLBody(BaseModel):
    text: str = Field(min_length=5, max_length=1000, description="The screen in plain language")
    base_tf: str = Field(default="1d", pattern=r"^(1m|3m|5m|15m|30m|1h|1d|1w|1M)$")
    # English only (owner directive, 13 Oct): the former `language` field is gone; a client that still sends it is
    # answered in English (an unknown body field is ignored, as before)


@router.post("/copilot/nl")
async def natural_language(body: NLBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session),
                           _ack: None = Depends(_ai_acknowledged), _limit: None = Depends(_ai_rate_limit)) -> Dict[str, Any]:
    """N3 S9: plain language -> a ScreenQL draft the trader checks, edits and runs (app/screener/nl.py). Nothing runs and
    nothing is stored here; flags `screener_copilot` and `ai_copilot`."""
    from app.ai.prompt_versions import stamp
    from app.ai.providers import ProviderError, RuleBasedProvider
    from app.ai.tools.screener import free_text_in
    from app.audit.log import write_audit_log
    from app.billing.service import meter
    from app.db.models import Tenant
    from app.screener import nl
    await _flag(session, user)
    await require_flag(session, nl.FLAG, user.tenant_id)
    await require_flag(session, "ai_copilot", user.tenant_id)
    tenant = await session.get(Tenant, user.tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Organisation not found")
    from app.ai import resolve as ai_resolve
    provider = await ai_resolve.provider_for_user(session, user, tenant, "screener_nl")
    await ai_resolve.refuse_if_not_connected(session, tenant.id, provider)      # Settings redesign A1 (flag on only)
    if isinstance(provider, RuleBasedProvider):
        raise HTTPException(status_code=409, detail="No AI provider is set up for this organisation (Settings > AI); write the screen in the "
                                                    "builder or the query editor instead" + (f" ({provider.reason})" if provider.reason else ""))
    stamp(provider, nl.PROMPT_VERSION)
    system = nl.system_prompt(sorted(await _flags_off(session, user)))
    prompt = f"REQUEST:\n{body.text.strip()}"
    draft: Optional[Dict[str, Any]] = None
    problems: List[Problem] = []
    ast: Any = None
    validated: Any = None
    for attempt in range(2):                                  # one repair turn with the validator's problems
        try:
            draft = nl.parse(await provider.complete(system, prompt, max_tokens=1200))
        except (ProviderError, ValueError, json.JSONDecodeError) as exc:
            await ai_resolve.mark_used(session, user.tenant_id, provider, error=str(exc)[:200])
            raise HTTPException(status_code=502, detail="The AI did not answer usably; write the screen in the builder") from exc
        at = nl.non_ascii_at(draft["text"])
        if at is not None:
            # S9 round cap (English only): ScreenQL is ASCII - a draft with any other character is refused unread
            ast, validated = None, Validated(False, [Problem("the screen has characters outside ASCII; ScreenQL is plain ASCII text",
                                                             at, kind="non_ascii")])
        else:
            ast, validated = _compiled(draft["text"], body.base_tf, {})
            await _gate(session, user, ast, validated, {})
        problems = list(validated.problems)
        if ast is not None and await free_text_in(session, user.tenant_id, ast):
            # review of S9 (MED, ADR-0006): a string literal other than a function's fixed choices is the model's free
            # text; it would reach the page inside the canonical screen. Refused (not echoed) and repaired like any problem.
            problems.append(Problem("a drafted screen may use quoted text only for a function's fixed choices "
                                    "(\"promoter\", \"results\"...) - other quoted text is not allowed", kind="free_text"))
        if validated.ok and not problems:
            break
        # the repair turn goes back to the model only, so it carries the validator's own messages (they quote its draft)
        prompt = (f"REQUEST:\n{body.text.strip()}\n\nYour screen {draft['text']!r} has problems: "
                  + "; ".join(p.message for p in problems)[:800] + "\nAnswer again with a corrected screen, same JSON shape.")
    await ai_resolve.mark_used(session, user.tenant_id, provider)
    if draft is None or validated is None:
        raise HTTPException(status_code=502, detail="The AI did not answer; write the screen in the builder")
    ok = bool(validated.ok) and not problems
    # review of S9 (MED-1, ADR-0006; round-cap redesign): an invalid draft returns its problems only - never the model's
    # text or the canonical text of it - and each problem is a fixed template for its kind with registry names only
    # (nl.shown_problem): no quoted text, no unknown name, no number from the draft; `pos` kept
    shown = [nl.shown_problem(p) for p in problems] if not ok else []
    explanation = nl.screened(draft["explanation"])
    await write_audit_log(session, user.tenant_id, user.id, "screener_nl_drafted",
                          f"{getattr(provider, 'name', 'ai')}: {'valid' if ok else 'invalid'} screen from {len(body.text)} chars")
    await meter(session, user.tenant_id, "ai_screener_nl", 1, source="api", metadata={"provider": getattr(provider, "name", "ai")}, commit=False)
    await session.commit()
    stages = []
    if ok:
        items = list(ast.items) if isinstance(ast, nodes.Logic) and ast.op == "ALL" else [ast]
        stages = [nodes.to_text(i) for i in items]
    return {"ok": ok, "text": nodes.to_text(ast) if ok else None, "stages": stages,
            "ast": nodes.to_json(ast) if ok else None, "problems": shown, "base_tf": body.base_tf,
            "explanation": explanation, "explanation_withheld": explanation is None and bool(draft["explanation"]),
            "assumptions": [a for a in (nl.screened(x) for x in draft["assumptions"]) if a],
            "provider": getattr(provider, "name", None), "model": getattr(provider, "model", None), "prompt_version": nl.PROMPT_VERSION,
            "disclaimer": nl.DISCLAIMER}


class IdeaBody(BaseModel):
    text: str = Field(min_length=3, max_length=1000, description="The scan idea in plain words")
    base_tf: str = Field(default="1d", pattern=r"^(1m|3m|5m|15m|30m|1h|1d|1w|1M)$")
    # 11j step 6: the edits the user made by chat, in order ("drop the volume condition", "also RSI above 50"). The
    # idea is replayed with them from the start, so a version is the idea plus its first n edits; nothing is stored.
    edits: List[Annotated[str, Field(min_length=1, max_length=300)]] = Field(default_factory=list, max_length=20)


@router.post("/copilot/idea")
async def idea_to_scan(body: IdeaBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Handover 11j "idea -> scan": a plain idea -> a ScreenQL draft from registry functions only (app/screener/idea.py),
    with no AI call. Each recognised part is validated and flag-gated like a typed screen; a part with a problem comes
    back with the validator's fixed reason (nl.shown_problem), and words no rule knows come back as "not supported yet".
    The draft opens as editable blocks (`stages`, the shape of POST /stages). Nothing runs, nothing is stored. Flag
    `screener_copilot` (the plain-language screen box) on top of `screener_v2`."""
    from app.audit.log import write_audit_log
    from app.screener import idea, idea_edits, nl
    from app.screener import stages as st
    await _flag(session, user)
    await require_flag(session, nl.FLAG, user.tenant_id)
    draft, base_tf, feedback = idea_edits.replay(body.text.strip(), body.base_tf, [e.strip() for e in body.edits])
    parts: List[Dict[str, Any]] = []
    for part in draft.parts:
        ast, validated = _compiled(part.condition, base_tf, {})
        await _gate(session, user, ast, validated, {})
        problems = [nl.shown_problem(p) for p in validated.problems] if ast is None or not validated.ok else []
        parts.append({**part.as_dict(), "condition": nodes.to_text(ast) if ast is not None and not problems else part.condition,
                      "ok": not problems, "problems": problems})
    usable = [p["condition"] for p in parts if p["ok"]]
    text: Optional[str] = None
    stages: List[Dict[str, Any]] = []
    if usable:
        ast, validated = _compiled(idea.joined(usable), base_tf, {})
        await _gate(session, user, ast, validated, {})
        if ast is not None and validated.ok:
            text, stages = nodes.to_text(ast), st.split(ast)
        else:                                  # each part passed alone; together they did not (the cost cap): say so
            for p in parts:
                if p["ok"]:
                    p["ok"], p["problems"] = False, [nl.shown_problem(q) for q in validated.problems][:1] or [
                        {"kind": "unusable", "message": nl.UNUSABLE, "pos": None}]
    await write_audit_log(session, user.tenant_id, user.id, "screener_idea_drafted",
                          f"{sum(1 for p in parts if p['ok'])} of {len(parts)} parts usable, {len(draft.unsupported)} not supported, "
                          f"from {len(body.text)} chars, {len(feedback)} edits ({sum(1 for f in feedback if f.changed)} changed it)")
    await session.commit()
    return {"ok": text is not None, "text": text, "base_tf": base_tf, "stages": stages, "parts": parts,
            "not_supported": draft.unsupported, "edits": [f.as_dict() for f in feedback], "disclaimer": idea.DISCLAIMER}


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
    results = json.loads(row.result_json)
    return {"run_id": row.id, "screen_id": row.screen_id, "ast_sha256": row.ast_sha256, "version": row.ast_version, "base_tf": row.base_tf,
            "universe": json.loads(row.universe_json), "data_source": row.data_source, "scanned": row.scanned, "matched": row.matched,
            "results": results, **read_counts(results), "created_at": row.created_at.isoformat() if row.created_at else None,
            "disclaimer": DISCLAIMER}


__all__ = ["router", "FLAG", "GALLERY_FLAG", "LEGENDS_FLAG", "fetch_frames"]
