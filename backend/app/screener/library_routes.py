"""U9 TX2: the library's confluence view, mirror screens and watch-then-trigger lists (`app/screener/library.py`).

* `GET  /api/screener/library/mirrors`      - every long pre-built screen's short mirror, or why it has none (member).
* `GET  /api/screener/library/watch-lists`  - the pre-built watch-then-trigger lists (member).
* `POST /api/screener/library/confluence`   - run 2-6 library or saved screens over up to 50 symbols and rank the symbols
  by how many methods flag them (trader, like `/run`: it reads bars through the organisation's broker session).
* `POST /api/screener/library/watch-trigger` - a pre-built or own ready / trigger pair over up to 50 symbols -> the
  "triggered" and "ready" lists (trader).

U9 TX3 (flag `screener_tx3`, with `screener_v2` and `screener_gallery`; not `screener_tx2`):
* `GET  /api/screener/library/regime`          - today's regime (NIFTY 50 Dow trend + breadth, India VIX) from closed
  sessions of the shared history, and whether each screen fits it (member; no organisation data).
* `GET  /api/screener/library/digest`          - the caller's own digest settings and their last digest (member).
* `PUT  /api/screener/library/digest`          - the caller's own settings (trader: the digest runs screens through
  their broker session, like `/run`).
* `POST /api/screener/library/digest/preview`  - build the digest now for the given pins and symbols, without sending
  or storing it (trader, rate limited).

U9 TX4 (flag `screener_tx4`, with `screener_v2` and `screener_gallery`):
* `GET  /api/screener/library/screens/{library_id}/learn`    - the screen's method, provenance (version, change log,
  source note, text and its SHA-256) (member).
* `POST /api/screener/library/screens/{library_id}/examples` - three past matches on the shared end-of-day history with
  their evidence (as of each bar) and what the close did after (self: computes over shared data, stores nothing).
* `POST /api/screener/library/fork`                         - copy a pre-built screen (or its mirror) into the
  organisation's saved screens with its provenance (trader, like saving a screen).

All four need `screener_v2`, `screener_gallery` and `screener_tx2` (off by default: 503 while off). The runs read closed
bars only, through the caller's own session when the BT1b data tier is on, store nothing, and never place an order.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_trader
from app.core.rate_limit import user_rate_limit
from app.db.models import User
from app.db.session import get_session
from app.platform.controls import flag_enabled, require_flag
from app.screener import compile_screen, nodes
from app.screener import digest as dg
from app.screener import learn as ln
from app.screener import library as lib
from app.screener import regime_today as rt
from app.screener import taxonomy as tx
from app.screener import zones as zone_gate
from app.screener.panel import FLAG as VECTOR_FLAG
from app.screener.parser import ScreenQLSyntaxError, parse
from app.screener.routes import DISCLAIMER, FLAG, GALLERY_FLAG, MAX_SYMBOLS, _cost_cap, _gate, _owned, fetch_frames, missing_data
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


async def _flags3(session: AsyncSession, user: User) -> None:
    for name in (FLAG, GALLERY_FLAG, dg.FLAG):
        await require_flag(session, name, user.tenant_id)


@router.get("/regime")
async def regime(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _flags3(session, user)
    today = await rt.today(session)
    return {**today.as_dict(), "fits": rt.fits(tx.load(), today, await _hidden(session, user))}


@router.get("/digest")
async def digest_settings(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _flags3(session, user)
    out = dg.settings_of(await dg.own_row(session, user))
    if out["last_digest"]:
        out["messages"] = dg.messages(out["last_digest"])
    return {**out, "limits": {"pinned": dg.MAX_PINNED, "symbols": dg.MAX_SYMBOLS}, "note": dg.NOTE}


async def _check_pins(session: AsyncSession, user: User, pinned: List[str]) -> None:
    taxonomy, hidden = tx.load(), await _hidden(session, user)
    for ref in pinned:
        await _resolve(session, user, ref, taxonomy, hidden)              # 422 / 404 with the reason


@router.put("/digest")
async def save_digest(body: dg.DigestSettings, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _flags3(session, user)
    if body.enabled and (not body.pinned or not body.symbols):
        raise HTTPException(status_code=422, detail="pin at least one screen and add symbols before turning the digest on")
    await _check_pins(session, user, body.pinned)
    return dg.settings_of(await dg.save(session, user, body))


@router.post("/digest/preview", dependencies=[Depends(library_rate_limit)])
async def preview_digest(body: dg.DigestSettings, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _flags3(session, user)
    if not body.pinned or not body.symbols:
        raise HTTPException(status_code=422, detail="pin at least one screen and add symbols")
    built = await dg.build(session, user, body.pinned, body.symbols, body.exchange)
    await session.commit()                                                 # evidence packs of the first matches, if any
    return {**built, "messages": dg.messages(built), "preview": True}


async def _flags4(session: AsyncSession, user: User) -> None:
    for name in (FLAG, GALLERY_FLAG, ln.FLAG):
        await require_flag(session, name, user.tenant_id)


async def _library_screen(session: AsyncSession, user: User, screen_id: str) -> Tuple[tx.Taxonomy, tx.Screen, lib.Resolved]:
    """A visible pre-built screen or its `.short` mirror (mirrors need `screener_tx2`), resolved to its text."""
    taxonomy, hidden = tx.load(), await _hidden(session, user)
    if screen_id.endswith(lib.MIRROR_SUFFIX) and not await flag_enabled(session, lib.FLAG, user.tenant_id):
        raise HTTPException(status_code=404, detail="mirror screens are off for this organisation")
    base = screen_id[:-len(lib.MIRROR_SUFFIX)] if screen_id.endswith(lib.MIRROR_SUFFIX) else screen_id
    screen = next((s for s in tx.visible(taxonomy, hidden) if s.id == base), None)
    if screen is None:
        raise HTTPException(status_code=404, detail="no such screen in the library (or its feature is off)")
    resolved, why = lib.resolve_ref(screen_id, taxonomy, hidden)
    if resolved is None:
        raise HTTPException(status_code=422, detail=why)
    return taxonomy, screen, resolved


@router.get("/screens/{library_id}/learn")
async def learn(library_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _flags4(session, user)
    taxonomy, screen, resolved = await _library_screen(session, user, library_id)
    text = nodes.to_text(lib.inline_params(parse(resolved.text), resolved.params))
    return {"id": library_id, "name": resolved.name, "base_tf": resolved.base_tf, "direction": resolved.direction,
            "mirror_of": resolved.mirror_of, **ln.method_of(taxonomy, screen), "provenance": ln.provenance(screen, text),
            "note": ln.NOTE}


class ExamplesBody(BaseModel):
    symbols: List[str] = Field(min_length=1, max_length=ln.MAX_SYMBOLS)


@router.post("/screens/{library_id}/examples", dependencies=[Depends(library_rate_limit)])
async def examples(library_id: str, body: ExamplesBody, user: User = Depends(get_current_user),
                   session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _flags4(session, user)
    _, _, resolved = await _library_screen(session, user, library_id)
    ast = lib.inline_params(parse(resolved.text), resolved.params)
    validated = await _compile(session, user, resolved.name, ast, resolved.base_tf)
    symbols = [s for s in _symbols(body.symbols) if s.replace("-", "").replace("&", "").isalnum()]
    out = await ln.examples(session, ast, validated, resolved.base_tf, symbols, resolved.name)
    return {"id": library_id, "name": resolved.name, "base_tf": resolved.base_tf, "text": nodes.to_text(ast), **out}


class ForkBody(BaseModel):
    screen_id: str = Field(min_length=1, max_length=120)
    name: Optional[str] = Field(default=None, max_length=120)


@router.post("/fork", status_code=201)
async def fork(body: ForkBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _flags4(session, user)
    _, screen, resolved = await _library_screen(session, user, body.screen_id)
    from app.db.models import ScreenRecord
    from app.screener.routes import ScreenBody, _apply, _screen_dict
    name = (body.name or f"{resolved.name} (fork of v{screen.version})")[:120]
    row = ScreenRecord(tenant_id=user.tenant_id, created_by=user.id, name="", source_text="", ast_json="{}",
                       forked_from=body.screen_id, forked_version=screen.version)
    await _apply(session, user, row, ScreenBody(name=name, source=resolved.text, base_tf=resolved.base_tf, params=resolved.params))
    session.add(row)
    await session.commit()
    return _screen_dict(row)


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


async def _compile(session: AsyncSession, user: User, label: str, ast: Any, base_tf: str) -> Any:
    """A screen's AST (parameters already inlined) -> its validation, refused with the reason like `/run` refuses it."""
    _, validated = compile_screen(nodes.to_json(ast), base_tf=base_tf, cost_cap=_cost_cap())
    await _gate(session, user, ast, validated)
    if not validated.ok:
        raise HTTPException(status_code=422, detail={"message": f"{label} did not pass validation",
                                                     "problems": [p.as_dict() for p in validated.problems]})
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


@router.post("/confluence", dependencies=[Depends(library_rate_limit)])
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
        compiled.append((s, ast, await _compile(session, user, s.name, ast, s.base_tf)))
    symbols = _symbols(body.symbols)
    by_tf: Dict[str, Dict[str, int]] = {}
    for s, _, validated in compiled:
        _merge(by_tf.setdefault(s.base_tf, {}), validated.fetch_lookback)
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


@router.post("/watch-trigger", dependencies=[Depends(library_rate_limit)])
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
    ready_v = await _compile(session, user, "the ready condition", ready, base_tf)
    await _compile(session, user, "the trigger condition", trigger, base_tf)
    transition_v = await _compile(session, user, "the transition", transition, base_tf)
    lookback: Dict[str, int] = {}
    _merge(lookback, ready_v.fetch_lookback)
    _merge(lookback, transition_v.fetch_lookback)
    symbols = _symbols(body.symbols)
    universe, problems, source = await _universe(session, user, symbols, body.exchange, base_tf, lookback)
    ready_hits = {**{s: (False, why) for s, why in problems.items()}, **await _run(session, user, ready, ready_v, universe, base_tf, body.exchange)}
    hits = await _run(session, user, transition, transition_v, universe, base_tf, body.exchange)
    return {**lib.watch_split(ready_hits, hits, symbols), "name": name, "base_tf": base_tf, "ready_text": nodes.to_text(ready),
            "trigger_text": nodes.to_text(trigger), "transition_text": nodes.to_text(transition), "scanned": len(symbols), **source,
            "disclaimer": DISCLAIMER}


__all__ = ["router"]
