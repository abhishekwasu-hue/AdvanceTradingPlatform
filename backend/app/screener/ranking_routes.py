"""N3 S7a (spec §2.5): the ranking systems API - saved weighted factor trees per organisation, the platform systems, and a
run that ranks up to 50 symbols 0-100 with every component shown (flag `screener_rankings`, off by default; needs
`screener_v2`).

* A run reads server bars through the same path as a screen run (the organisation's session, or the runner's own under
  BT1b) - closed daily bars only - and ranks on the last one. Nothing is stored and nothing is placed: a rank is
  information, never advice.
* Saved systems belong to one organisation: every read and write is scoped to the caller's tenant. Archived, never
  hard-deleted.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_trader
from app.db.models import RankingSystemRecord, User
from app.db.session import get_session
from app.platform.controls import require_flag
from app.screener import ranking as RK
from app.screener.routes import FLAG as SCREENER_FLAG
from app.screener.routes import MAX_SYMBOLS, fetch_frames, run_rate_limit, store_first

router = APIRouter(prefix="/api/screener/rankings", tags=["screener"])

DISCLAIMER = ("A rank orders symbols by the factors shown, on the data shown; it is not a recommendation to buy or sell. "
              "Information only - nothing is placed.")


class SystemBody(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=300)
    tree: Dict[str, Any] = Field(description='{"nodes": [...]} - leaves {name, factor, weight, higher_is_better, neutral}, groups {name, weight, children}')


class RunBody(BaseModel):
    system_id: Optional[int] = None
    system: Optional[str] = Field(default=None, max_length=80, description='a platform system ("platform:Momentum") or a saved one by name')
    tree: Optional[Dict[str, Any]] = Field(default=None, description="an unsaved tree (tried before saving)")
    symbols: List[str] = Field(min_length=1, max_length=MAX_SYMBOLS)
    exchange: str = Field(default="NSE", pattern=r"^(NSE|BSE)$")

    @model_validator(mode="after")
    def one_system(self) -> "RunBody":
        if sum(x is not None for x in (self.system_id, self.system, self.tree)) != 1:
            raise ValueError("give one of system_id, system or tree")
        return self


async def _flags(session: AsyncSession, user: User) -> None:
    await require_flag(session, SCREENER_FLAG, user.tenant_id)
    await require_flag(session, RK.FLAG, user.tenant_id)


def _parsed(name: str, tree: Any, description: str = "") -> RK.System:
    try:
        return RK.parse(name, tree, description)
    except RK.RankingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def _gated(session: AsyncSession, user: User, system: RK.System) -> RK.System:
    """Review of S7a (MED-2): a factor from a flagged family (e.g. the breakout factors) needs that flag on."""
    off = await RK.blocked_flags(session, user.tenant_id, system)
    if off:
        raise HTTPException(status_code=422, detail=f"factors of this system are not enabled for this organisation (flag {', '.join(off)})")
    return system


def _dict(row: RankingSystemRecord) -> Dict[str, Any]:
    return {"id": row.id, "name": row.name, "description": row.description, "tree": json.loads(row.tree_json or "{}"),
            "archived": row.archived, "platform": False, "created_by": row.created_by,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None}


def _platform() -> List[Dict[str, Any]]:
    return [{"id": None, "name": f"{RK.PLATFORM_PREFIX}{name}", "description": spec["description"], "tree": spec["tree"],
             "archived": False, "platform": True} for name, spec in RK.PLATFORM.items()]


async def _owned(session: AsyncSession, user: User, system_id: int) -> RankingSystemRecord:
    row = await session.scalar(select(RankingSystemRecord).where(RankingSystemRecord.id == system_id,
                                                                 RankingSystemRecord.tenant_id == user.tenant_id))
    if row is None:
        raise HTTPException(status_code=404, detail="Ranking system not found")
    return row


def _name(raw: str) -> str:
    name = raw.strip()
    if name.lower().startswith(RK.PLATFORM_PREFIX):
        raise HTTPException(status_code=422, detail=f"names starting with {RK.PLATFORM_PREFIX!r} are the platform's")
    return name


async def _save(session: AsyncSession, row: RankingSystemRecord) -> None:
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="this organisation already has a ranking system with that name") from exc


@router.get("")
async def list_systems(include_archived: bool = False, user: User = Depends(get_current_user),
                       session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """The platform systems (documented formulas, read-only) and this organisation's own."""
    await _flags(session, user)
    q = select(RankingSystemRecord).where(RankingSystemRecord.tenant_id == user.tenant_id)
    if not include_archived:
        q = q.where(RankingSystemRecord.archived.is_(False))
    own = [_dict(r) for r in await session.scalars(q.order_by(RankingSystemRecord.id.desc()))]
    return {"platform": _platform(), "systems": own, "limits": {"max_depth": RK.MAX_DEPTH, "max_factors": RK.MAX_LEAVES},
            "disclaimer": DISCLAIMER}


@router.post("", status_code=201)
async def create_system(body: SystemBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _flags(session, user)
    system = await _gated(session, user, _parsed(_name(body.name), body.tree, body.description))
    row = RankingSystemRecord(tenant_id=user.tenant_id, created_by=user.id, name=system.name, description=system.description,
                              tree_json=json.dumps(system.tree()))
    session.add(row)
    await _save(session, row)
    return _dict(row)


@router.put("/{system_id}")
async def update_system(system_id: int, body: SystemBody, user: User = Depends(require_trader),
                        session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _flags(session, user)
    row = await _owned(session, user, system_id)
    system = await _gated(session, user, _parsed(_name(body.name), body.tree, body.description))
    row.name, row.description, row.tree_json = system.name, system.description, json.dumps(system.tree())
    row.updated_at = datetime.now(timezone.utc)
    await _save(session, row)
    return _dict(row)


@router.post("/{system_id}/archive")
async def archive_system(system_id: int, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Archived, never hard-deleted (a backtest's ledger entry keeps the tree it ran)."""
    await _flags(session, user)
    row = await _owned(session, user, system_id)
    if not row.archived:
        # review of S7a (LOW): the name is freed for a new system (names are unique per organisation); a backtest's ledger
        # entry keeps the tree it ran, so nothing refers to the archived one by name
        # re-check of S7a (LOW): a name already taken (another system named like an archived one) gets a further suffix
        taken = set(await session.scalars(select(RankingSystemRecord.name).where(
            RankingSystemRecord.tenant_id == row.tenant_id, RankingSystemRecord.id != row.id)))
        for n in range(100):
            suffix = f" (archived #{row.id})" if n == 0 else f" (archived #{row.id}.{n})"
            candidate = row.name[:60 - len(suffix)] + suffix
            if candidate not in taken:
                break
        row.name = candidate
    row.archived, row.updated_at = True, datetime.now(timezone.utc)
    await _save(session, row)                                  # a race on the name: 409, never a 500
    return _dict(row)


async def _system(session: AsyncSession, user: User, body: RunBody) -> RK.System:
    if body.tree is not None:
        return await _gated(session, user, _parsed("unsaved", body.tree))
    if body.system_id is not None:
        row = await _owned(session, user, body.system_id)
        return await _gated(session, user, _parsed(row.name, json.loads(row.tree_json or "{}"), row.description))
    found, problems = await RK.resolve(session, user.tenant_id, [str(body.system)])
    if problems:
        raise HTTPException(status_code=422, detail="; ".join(problems))
    return found[str(body.system)]


def _num(v: Any) -> Optional[float]:
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else round(float(v), 2)


@router.post("/run", dependencies=[Depends(run_rate_limit)])
async def run_system(body: RunBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Ranks up to 50 symbols by a system on their last closed daily bar: 0-100 (100 = best), every component shown."""
    import asyncio
    await _flags(session, user)
    system = await _system(session, user, body)
    from app.screener import venues as venue_gate
    if await venue_gate.enabled(session, user.tenant_id):            # N3 S8 review (LOW): the factors against the venue's data
        missing = [m for leaf in system.leaves() for m in venue_gate.unavailable(leaf.ast, body.exchange)]
        if missing:
            raise HTTPException(status_code=422, detail="; ".join(dict.fromkeys(missing)))
    symbols = list(dict.fromkeys(s.strip().upper() for s in body.symbols if s.strip()))
    tier: Dict[str, Any] = {}
    store = await store_first(session, user.tenant_id)          # M71 W2-G: store-first, broker calls budgeted (flag on only)
    universe, problems, data_source = await fetch_frames(session, user.tenant_id, symbols, body.exchange, "1d", {"1d": system.lookback},
                                                         user_id=user.id, tier_out=tier, store=store, budget_out={} if store else None)
    if system.sector_neutral and universe:
        from app.market_data.calendar import IST
        today = datetime.now(timezone.utc).astimezone(IST).date()
        ranges = await RK.sector_ranges(session, [d.symbol for d in universe], today, today)
        for d in universe:
            d.sector = RK.sector_on(ranges.get(d.symbol, []), today)

    def compute() -> Any:
        values = RK.live_values(system, universe, "1d")
        rank, parts = RK.score(system, values, {d.symbol: d.sector for d in universe} if system.sector_neutral else None)
        return values, rank, parts
    values, rank, parts = await asyncio.to_thread(compute)
    rows: List[Dict[str, Any]] = []
    for d in universe:
        r = rank.get(d.symbol, np.nan)
        if isinstance(r, float) and np.isnan(r):
            problems[d.symbol] = "no factor value (too little history?)"
            continue
        frame = d.frames.get("1d")
        rows.append({"symbol": d.symbol, "rank": _num(r), "composite": _num(parts["composite"].get(d.symbol)),
                     "sector": d.sector if system.sector_neutral else None,
                     "as_of": frame.index[-1].isoformat() if frame is not None and not frame.empty else None,
                     "components": RK.components(system, values, parts, d.symbol)})
    rows.sort(key=lambda x: (-float(x["rank"] or 0.0), str(x["symbol"])))
    out = {"system": {"name": system.name, "description": system.description, "tree": system.tree()}, "ranked": rows,
           "not_ranked": problems, "scanned": len(symbols), "data_source": data_source, "disclaimer": DISCLAIMER,
           "method": "Each factor is a percentile across the symbols ranked (within the sector when sector-neutral; reversed when "
                     "lower is better); a group is the weighted mean of its parts (a missing part is left out and the rest "
                     "re-weighted); the rank is the composite's percentile, 0-100."}
    if tier:
        out["data_tier"] = tier["data_tier"]
    return out


__all__ = ["router", "DISCLAIMER"]
