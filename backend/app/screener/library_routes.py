"""U9 TX2: the library's confluence view, mirror screens and watch-then-trigger lists (`app/screener/library.py`).

* `GET  /api/screener/library/mirrors`      - every long pre-built screen's short mirror, or why it has none (member).
* `GET  /api/screener/library/watch-lists`  - the pre-built watch-then-trigger lists (member).
* `POST /api/screener/library/confluence`   - run 2-6 library or saved screens over up to 50 symbols and rank the symbols
  by how many methods flag them (trader, like `/run`: it reads bars through the organisation's broker session).
* `POST /api/screener/library/watch-trigger` - a pre-built or own ready / trigger pair over up to 50 symbols -> the
  "triggered" and "ready" lists (trader).

All four need `screener_v2`, `screener_gallery` and `screener_tx2` (off by default: 503 while off). The runs read closed
bars only, through the caller's own session when the BT1b data tier is on, store nothing, and never place an order.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_trader
from app.core.rate_limit import user_rate_limit
from app.db.models import User
from app.db.session import get_session
from app.platform.controls import flag_enabled, require_flag
from app.screener import compile_screen, nodes
from app.screener import library as lib
from app.screener import taxonomy as tx
from app.screener import zones as zone_gate
from app.screener.panel import FLAG as VECTOR_FLAG
from app.screener.parser import ScreenQLSyntaxError, parse
from app.screener.routes import DISCLAIMER, FLAG, GALLERY_FLAG, MAX_SYMBOLS, _cost_cap, _gate, _owned, charge_runs, fetch_frames, missing_data
from app.screener.runtime import SymbolData, run_screen

router = APIRouter(prefix="/api/screener/library", tags=["screener"])
library_rate_limit = user_rate_limit("screener_library", limit=20, window_seconds=60)
_TF = r"^(1m|3m|5m|15m|30m|1h|1d|1w|1M)$"


class ConfluenceBody(BaseModel):
    screens: List[str] = Field(min_length=lib.MIN_CONFLUENCE_SCREENS, max_length=lib.MAX_CONFLUENCE_SCREENS,
                               description="library ids, their `.short` mirrors, or `saved:<id>`")
    symbols: List[str] = Field(min_length=1, max_length=MAX_SYMBOLS)
    exchange: str = Field(default="NSE", pattern=r"^(NSE|BSE|NFO|BFO|MCX|CDS)$")


class WatchBody(BaseModel):
    list_id: Optional[str] = None
    ready: Optional[str] = Field(default=None, max_length=4000)
    trigger: Optional[str] = Field(default=None, max_length=4000)
    base_tf: str = Field(default="1d", pattern=_TF)
    params: Dict[str, Union[float, int, str, bool]] = Field(default_factory=dict)
    symbols: List[str] = Field(min_length=1, max_length=MAX_SYMBOLS)
    exchange: str = Field(default="NSE", pattern=r"^(NSE|BSE|NFO|BFO|MCX|CDS)$")


async def _flags(session: AsyncSession, user: User) -> None:
    for name in (FLAG, GALLERY_FLAG, lib.FLAG):
        await require_flag(session, name, user.tenant_id)


async def _hidden(session: AsyncSession, user: User) -> List[str]:
    return [name for name, src in tx.sources().items() if src.flag and not await flag_enabled(session, src.flag, user.tenant_id)]


@router.get("/mirrors")
async def mirrors(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _flags(session, user)
    return {"mirrors": lib.mirrors(tx.load(), await _hidden(session, user)), "note": lib.MIRROR_NOTE}


@router.get("/watch-lists")
async def watch_lists(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _flags(session, user)
    return {"lists": [w.as_dict() for w in lib.WATCH_LISTS], "note": lib.WATCH_NOTE}


def _symbols(raw: List[str]) -> List[str]:
    return list(dict.fromkeys(s.strip().upper() for s in raw if s.strip()))


async def _compile(session: AsyncSession, user: User, label: str, ast: Any, base_tf: str, exchange: Optional[str] = None) -> Any:
    """A screen's AST (parameters already inlined) -> its validation, refused with the reason like `/run` refuses it
    (N3 S8c: with the venue's field check, as `/run` has it)."""
    _, validated = compile_screen(nodes.to_json(ast), base_tf=base_tf, cost_cap=_cost_cap())
    await _gate(session, user, ast, validated, exchange=exchange)
    if not validated.ok:
        raise HTTPException(status_code=422, detail={"message": f"{label} did not pass validation",
                                                     "problems": [p.as_dict() for p in validated.problems]})
    from app.screener import ownership as ownership_gate
    from app.screener import ranking as ranking_gate
    owned = ownership_gate.flagged_calls(ast)
    if owned:                 # N3 S8a: as RankScore below - missing_data counts ownership as loaded by /run, not here
        raise HTTPException(status_code=422, detail=f"{label}: " + ownership_gate.elsewhere_refusal(owned, "confluence and watch lists"))
    if ranking_gate.uses_rankscore(ast):
        # N3 S7a: missing_data counts RankScore's systems as loaded (by /run); the library's runs do not load them, so
        # every symbol would rank NaN - refused with the reason, as screen history refuses it
        raise HTTPException(status_code=422, detail=f"{label}: RankScore() is not run in confluence or watch lists yet - "
                                                    "run the screen from the Screener")
    missing = missing_data(ast)
    if missing:
        raise HTTPException(status_code=422, detail=f"{label}: {', '.join(m + '()' for m in missing)} need data a run does not load yet")
    return validated


async def _resolve(session: AsyncSession, user: User, ref: str, taxonomy: tx.Taxonomy, hidden: List[str]) -> lib.Resolved:
    if ref.startswith(lib.SAVED_PREFIX):
        try:
            screen_id = int(ref[len(lib.SAVED_PREFIX):])
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"{ref}: not a saved screen id") from exc
        row = await _owned(session, user, screen_id)          # the organisation's own saved screens only (404 otherwise)
        params = json.loads(row.params_json or "{}")
        return lib.Resolved(ref, row.name, "my_screens", "", "", "saved", row.source_text, row.base_tf, params)
    resolved, why = lib.resolve_ref(ref, taxonomy, hidden)
    if resolved is None:
        raise HTTPException(status_code=422, detail=why)
    return resolved


async def _universe(session: AsyncSession, user: User, symbols: List[str], exchange: str, base_tf: str,
                    lookback: Dict[str, int]) -> Tuple[List[SymbolData], Dict[str, str], Dict[str, Any]]:
    tier: Dict[str, Any] = {}
    universe, problems, source = await fetch_frames(session, user.tenant_id, symbols, exchange, base_tf, lookback, user_id=user.id,
                                                    tier_out=tier)
    return universe, problems, {"data_source": source, **({"data_tier": tier["data_tier"]} if tier else {})}


def _merge(into: Dict[str, int], more: Dict[str, int]) -> None:
    for tf, bars in more.items():
        into[tf] = max(into.get(tf, 0), bars)


async def _run(session: AsyncSession, user: User, ast: Any, validated: Any, universe: List[SymbolData], base_tf: str,
               exchange: str) -> Dict[str, Tuple[bool, Optional[str]]]:
    if zone_gate.flagged_calls(ast):
        await zone_gate.attach_zones(session, user.tenant_id, exchange, universe, datetime.now(timezone.utc), ast, base_tf, {})
    vectorized = await flag_enabled(session, VECTOR_FLAG, user.tenant_id)
    matches = await asyncio.to_thread(run_screen, ast, validated, universe, base_tf=base_tf, params={}, vectorized=vectorized)
    return {m.symbol: (m.matched, m.reason) for m in matches}


def confluence_units(screens: int, fetches: int) -> int:
    """`/run` calls a confluence is charged as (review of TX2): one per screen evaluated, plus every fetch past the first."""
    return max(1, int(screens)) + max(0, int(fetches) - 1)


WATCH_UNITS = 2                     # watch-trigger: one fetch, the ready and the trigger condition each evaluated once


@router.post("/confluence")
async def confluence(body: ConfluenceBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _flags(session, user)
    refs = list(dict.fromkeys(body.screens))
    if len(refs) < lib.MIN_CONFLUENCE_SCREENS:
        raise HTTPException(status_code=422, detail=f"pick at least {lib.MIN_CONFLUENCE_SCREENS} different screens")
    taxonomy, hidden = tx.load(), await _hidden(session, user)
    screens = [await _resolve(session, user, ref, taxonomy, hidden) for ref in refs]
    compiled: List[Tuple[lib.Resolved, Any, Any]] = []
    for s in screens:
        ast = lib.inline_params(parse(s.text), s.params)
        compiled.append((s, ast, await _compile(session, user, s.name, ast, s.base_tf, body.exchange)))
    symbols = _symbols(body.symbols)
    by_tf: Dict[str, Dict[str, int]] = {}
    for s, _, validated in compiled:
        _merge(by_tf.setdefault(s.base_tf, {}), validated.fetch_lookback)
    # Review of TX2 (MED): up to six screens over up to six fetches is several `/run`s of work - charged as such on the
    # shared per-user `/run` bucket (before any bar is fetched), not on a bucket of its own.
    await charge_runs(user, confluence_units(len(compiled), len(by_tf)))
    results: Dict[str, Dict[str, Tuple[bool, Optional[str]]]] = {}
    fetch_problems: Dict[str, Dict[str, str]] = {}
    sources: Dict[str, Dict[str, Any]] = {}
    for base_tf, lookback in by_tf.items():               # one fetch per timeframe, shared by the screens on it
        universe, problems, sources[base_tf] = await _universe(session, user, symbols, body.exchange, base_tf, lookback)
        fetch_problems[base_tf] = problems
        for s, ast, validated in compiled:
            if s.base_tf == base_tf:
                got = await _run(session, user, ast, validated, universe, base_tf, body.exchange)
                results[s.ref] = {**{sym: (False, why) for sym, why in problems.items()}, **got}
    out = lib.confluence([s for s, _, _ in compiled], results, symbols)
    return {**out, "screens": [{**s.as_dict(), "text": nodes.to_text(ast), "matched": sum(1 for ok, _ in results.get(s.ref, {}).values() if ok)}
                               for s, ast, _ in compiled],
            "scanned": len(symbols), "sources": sources, "fetch_problems": fetch_problems, "disclaimer": DISCLAIMER}


def _parsed(label: str, text: str) -> Any:
    try:
        return parse(text)
    except ScreenQLSyntaxError as exc:
        raise HTTPException(status_code=422, detail={"message": f"{label}: {exc.message}", "problems": [{"message": exc.message, "pos": exc.pos}]}) from exc


def watch_eval(ready: Any, trigger: Any, ready_v: Any, transition_v: Any, universe: List[SymbolData], base_tf: str,
               transition: Any = None) -> Tuple[Dict[str, Tuple[bool, Optional[str]]], Dict[str, Tuple[bool, Optional[str]]]]:
    """(ready now, triggered) per symbol, evaluating the ready and the trigger condition ONCE each (review of TX2): the
    transition `Count(1, ready)[1] >= 1 AND trigger` is read off the ready series' bar before the last and the trigger's
    last bar - the same answer as running the transition screen (tested), without evaluating `ready` a second time.
    History is checked like `run_screen`: the ready condition against its own lookback, the transition against its."""
    import math

    from app.screener import nodes as nn
    from app.screener.runtime import ScreenRuntimeError, _cross_sectional, _Eval, _last

    def truthy(v: Any) -> bool:
        return v is not None and not (isinstance(v, float) and math.isnan(v)) and bool(v)

    def short(ev: Any, d: SymbolData, validated: Any) -> List[str]:
        return [tf for tf, bars in validated.lookback.items()
                if (tf in d.frames or nn.TF_MINUTES.get(tf, 0) > nn.TF_MINUTES[base_tf]) and len(ev.frame(tf)) < bars]

    def evaluated(ev: Any, node: Any) -> Tuple[Any, Optional[str]]:
        try:
            return ev.ev(node, base_tf), None
        except ScreenRuntimeError as exc:
            return None, str(exc)
        except Exception as exc:  # noqa: BLE001 - one symbol's bad data never aborts the list (as run_screen)
            return None, f"could not be evaluated ({type(exc).__name__})"

    # A trigger that ranks across the list (Rank / PercentileRank) reads the universe's values, as run_screen computes them
    # for the transition screen (the trigger's nodes are the transition's own) - review of TX2 re-check.
    if transition_v.cross_sectional:
        tree = transition if transition is not None else lib.transition_ast(ready, trigger)     # shares the trigger's nodes
        constants = _cross_sectional(tree, universe, base_tf, {})
    else:
        constants = {d.symbol: {} for d in universe}
    ready_out: Dict[str, Tuple[bool, Optional[str]]] = {}
    hit_out: Dict[str, Tuple[bool, Optional[str]]] = {}
    for d in universe:
        frame = d.frames.get(base_tf)
        if frame is None or frame.empty:
            ready_out[d.symbol] = hit_out[d.symbol] = (False, f"no {base_tf} bars")
            continue
        ev = _Eval(d, base_tf, {}, constants[d.symbol])
        ready_short, hit_short = short(ev, d, ready_v), short(ev, d, transition_v)
        r, ready_err = evaluated(ev, ready)
        if ready_err is not None:                         # the transition reads `ready` too: neither has an answer
            ready_out[d.symbol] = hit_out[d.symbol] = (False, ready_err)
            continue
        ready_out[d.symbol] = (False, f"not enough history on {', '.join(sorted(ready_short))}") if ready_short else (truthy(_last(r)), None)
        if hit_short:
            hit_out[d.symbol] = (False, f"not enough history on {', '.join(sorted(hit_short))}")
            continue
        t, trigger_err = evaluated(ev, trigger)
        if trigger_err is not None:                       # only the trigger failed: the ready answer stands
            hit_out[d.symbol] = (False, trigger_err)
            continue
        before = r.iloc[-2] if isinstance(r, pd.Series) and len(r) >= 2 else None
        hit_out[d.symbol] = (truthy(before) and truthy(_last(t)), None)
    return ready_out, hit_out


@router.post("/watch-trigger")
async def watch_trigger(body: WatchBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _flags(session, user)
    if body.list_id is not None:
        w = lib.WATCH_BY_ID.get(body.list_id)
        if w is None:
            raise HTTPException(status_code=404, detail="no such watch list")
        ready_text, trigger_text, base_tf, params, name = w.ready, w.trigger, w.base_tf, {**w.params, **body.params}, w.name
    elif body.ready and body.trigger:
        ready_text, trigger_text, base_tf, params, name = body.ready, body.trigger, body.base_tf, dict(body.params), "Your watch list"
    else:
        raise HTTPException(status_code=422, detail="give a list_id, or both a ready and a trigger condition")
    ready = lib.inline_params(_parsed("ready", ready_text), params)
    trigger = lib.inline_params(_parsed("trigger", trigger_text), params)
    transition = lib.transition_ast(ready, trigger)
    ready_v = await _compile(session, user, "the ready condition", ready, base_tf, body.exchange)
    await _compile(session, user, "the trigger condition", trigger, base_tf, body.exchange)
    transition_v = await _compile(session, user, "the transition", transition, base_tf, body.exchange)
    lookback: Dict[str, int] = {}
    _merge(lookback, ready_v.fetch_lookback)
    _merge(lookback, transition_v.fetch_lookback)
    symbols = _symbols(body.symbols)
    await charge_runs(user, WATCH_UNITS)                   # review of TX2 (MED): the shared `/run` bucket
    universe, problems, source = await _universe(session, user, symbols, body.exchange, base_tf, lookback)
    if zone_gate.flagged_calls(transition):
        await zone_gate.attach_zones(session, user.tenant_id, body.exchange, universe, datetime.now(timezone.utc), transition, base_tf, {})
    ready_got, hits = await asyncio.to_thread(watch_eval, ready, trigger, ready_v, transition_v, universe, base_tf, transition)
    ready_hits = {**{s: (False, why) for s, why in problems.items()}, **ready_got}
    return {**lib.watch_split(ready_hits, hits, symbols), "name": name, "base_tf": base_tf, "ready_text": nodes.to_text(ready),
            "trigger_text": nodes.to_text(trigger), "transition_text": nodes.to_text(transition), "scanned": len(symbols), **source,
            "disclaimer": DISCLAIMER}


__all__ = ["router"]
