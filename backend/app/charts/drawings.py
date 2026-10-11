"""CH1 (ADR-0023): chart drawings - our own engine-neutral schema (`drawing/1`) and its storage API.

* A drawing is anchored by time and price, never by pixels or bar index, so the same drawing shows on every timeframe
  and survives a chart-engine switch (ProChart today, lightweight-charts v5 primitives in CH2, TradingView in CH7).
* Each kind declares how many anchors it needs and which coordinates (`t` time, `p` price) each one must carry.
* Storage is per user and symbol, versioned (optimistic concurrency: an update names the version it edits; a stale
  edit is refused with 409, and undo/redo stay client-side over versions), lockable and soft-deleted.
* Export / import is a JSON document (`atp-drawings/1`); an import validates every drawing first and is all or nothing.
* A drawing is a picture: nothing here places, changes or proposes an order (a "propose from drawing" action is a
  separate PAPER proposal, CH4+).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.models import ChartDrawingRecord, User
from app.db.session import get_session

SCHEMA = "drawing/1"
EXPORT_FORMAT = "atp-drawings/1"
MAX_PER_SYMBOL = 500
SYMBOL_RE = r"^[A-Za-z0-9 &._:-]{1,40}$"
EXCHANGE_RE = r"^(NSE|BSE|NFO|BFO|MCX|CDS|CRYPTO|NYSE|NASDAQ)$"

# kind -> per-anchor required coordinates ("tp" both, "p" price only, "t" time only)
ANCHORS: Dict[str, List[str]] = {
    "trendline": ["tp", "tp"], "ray": ["tp", "tp"], "measure": ["tp", "tp"], "rectangle": ["tp", "tp"],
    "fib_retracement": ["tp", "tp"], "fib_extension": ["tp", "tp", "tp"], "channel": ["tp", "tp", "tp"],
    "hline": ["p"], "vline": ["t"], "text": ["tp"],
    "long_position": ["tp", "p", "p"], "short_position": ["tp", "p", "p"],          # entry, stop, target
}
Kind = Literal["trendline", "ray", "measure", "rectangle", "fib_retracement", "fib_extension", "channel", "hline", "vline", "text",
               "long_position", "short_position"]


class Anchor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    t: Optional[datetime] = None
    p: Optional[float] = None

    @field_validator("t")
    @classmethod
    def _utc(cls, v: Optional[datetime]) -> Optional[datetime]:
        if v is None:
            return v
        return v.astimezone(timezone.utc) if v.tzinfo else v.replace(tzinfo=timezone.utc)

    @field_validator("p")
    @classmethod
    def _finite(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and (v != v or v in (float("inf"), float("-inf"))):
            raise ValueError("price must be a finite number")
        return v


class Style(BaseModel):
    model_config = ConfigDict(extra="forbid")
    color: str = Field(default="#2962ff", pattern=r"^#[0-9a-fA-F]{6}$")
    width: int = Field(default=1, ge=1, le=6)
    line: Literal["solid", "dashed", "dotted"] = "solid"
    extend: Literal["none", "left", "right", "both"] = "none"


class Drawing(BaseModel):
    """`drawing/1`: the engine-neutral shape every engine maps in and out."""
    model_config = ConfigDict(extra="forbid")
    schema_: Literal["drawing/1"] = Field(default="drawing/1", alias="schema")
    kind: Kind
    anchors: List[Anchor] = Field(min_length=1, max_length=3)
    style: Style = Field(default_factory=Style)
    text: Optional[str] = Field(default=None, max_length=200)
    levels: Optional[List[float]] = Field(default=None, max_length=20, description="Fibonacci levels as ratios")

    @model_validator(mode="after")
    def _anchors_fit_the_kind(self) -> "Drawing":
        need = ANCHORS[self.kind]
        if len(self.anchors) != len(need):
            raise ValueError(f"a {self.kind} needs {len(need)} anchor(s), got {len(self.anchors)}")
        for i, (anchor, coords) in enumerate(zip(self.anchors, need)):
            if "t" in coords and anchor.t is None:
                raise ValueError(f"{self.kind} anchor {i + 1} needs a time")
            if "p" in coords and anchor.p is None:
                raise ValueError(f"{self.kind} anchor {i + 1} needs a price")
        if self.levels is not None and not self.kind.startswith("fib_"):
            raise ValueError("levels apply to Fibonacci drawings only")
        if self.kind == "text" and not (self.text or "").strip():
            raise ValueError("a text drawing needs text")
        if self.text is not None and re.search(r"[<>]", self.text):
            raise ValueError("text may not contain < or >")
        return self

    def to_json(self) -> Dict[str, Any]:
        return json.loads(self.model_dump_json(by_alias=True, exclude_none=True))


class CreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(pattern=SYMBOL_RE)
    exchange: str = Field(default="NSE", pattern=EXCHANGE_RE)
    drawing: Drawing


class UpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1, description="the version this edit was made on")
    drawing: Drawing


class LockBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    locked: bool


class ExportDoc(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["atp-drawings/1"] = "atp-drawings/1"
    symbol: str = Field(pattern=SYMBOL_RE)
    exchange: str = Field(default="NSE", pattern=EXCHANGE_RE)
    drawings: List[Drawing] = Field(max_length=MAX_PER_SYMBOL)


router = APIRouter(prefix="/api/charts/drawings", tags=["charts"])


def _as_dict(row: ChartDrawingRecord) -> Dict[str, Any]:
    return {"id": row.id, "symbol": row.symbol, "exchange": row.exchange, "kind": row.kind, "drawing": json.loads(row.drawing_json),
            "version": row.version, "locked": row.locked, "updated_at": row.updated_at.isoformat() if row.updated_at else None}


def _norm(symbol: str) -> str:
    return symbol.strip().upper()


async def _mine(session: AsyncSession, user: User, drawing_id: int) -> ChartDrawingRecord:
    row = await session.scalar(select(ChartDrawingRecord).where(ChartDrawingRecord.id == drawing_id, ChartDrawingRecord.user_id == user.id,
                                                              ChartDrawingRecord.tenant_id == user.tenant_id, ChartDrawingRecord.deleted_at.is_(None)))
    if row is None:
        raise HTTPException(status_code=404, detail="Drawing not found")
    return row


async def _count(session: AsyncSession, user: User, symbol: str, exchange: str) -> int:
    return int(await session.scalar(select(func.count()).select_from(ChartDrawingRecord).where(
        ChartDrawingRecord.user_id == user.id, ChartDrawingRecord.symbol == symbol, ChartDrawingRecord.exchange == exchange,
        ChartDrawingRecord.deleted_at.is_(None))) or 0)


def _new(user: User, symbol: str, exchange: str, drawing: Drawing) -> ChartDrawingRecord:
    now = datetime.now(timezone.utc)
    return ChartDrawingRecord(tenant_id=user.tenant_id, user_id=user.id, symbol=symbol, exchange=exchange, kind=drawing.kind,
                              drawing_json=json.dumps(drawing.to_json()), version=1, locked=False, created_at=now, updated_at=now)


@router.get("")
async def list_drawings(symbol: str, exchange: str = "NSE", user: User = Depends(get_current_user),
                        session: AsyncSession = Depends(get_session)) -> List[Dict[str, Any]]:
    """The caller's drawings for one symbol (every timeframe shows the same ones)."""
    rows = await session.scalars(select(ChartDrawingRecord).where(
        ChartDrawingRecord.user_id == user.id, ChartDrawingRecord.tenant_id == user.tenant_id, ChartDrawingRecord.symbol == _norm(symbol),
        ChartDrawingRecord.exchange == exchange.upper(), ChartDrawingRecord.deleted_at.is_(None)).order_by(ChartDrawingRecord.id))
    return [_as_dict(r) for r in rows]


@router.post("", status_code=201)
async def create_drawing(body: CreateBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    symbol = _norm(body.symbol)
    if await _count(session, user, symbol, body.exchange) >= MAX_PER_SYMBOL:
        raise HTTPException(status_code=409, detail=f"at most {MAX_PER_SYMBOL} drawings per symbol")
    row = _new(user, symbol, body.exchange, body.drawing)
    session.add(row)
    await session.commit()
    return _as_dict(row)


@router.put("/{drawing_id}")
async def update_drawing(drawing_id: int, body: UpdateBody, user: User = Depends(get_current_user),
                         session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    row = await _mine(session, user, drawing_id)
    if row.locked:
        raise HTTPException(status_code=423, detail="The drawing is locked; unlock it first")
    if body.version != row.version:
        raise HTTPException(status_code=409, detail={"message": "the drawing changed since this edit was made", "current": _as_dict(row)})
    row.kind, row.drawing_json = body.drawing.kind, json.dumps(body.drawing.to_json())
    row.version, row.updated_at = row.version + 1, datetime.now(timezone.utc)
    await session.commit()
    return _as_dict(row)


@router.post("/{drawing_id}/lock")
async def lock_drawing(drawing_id: int, body: LockBody, user: User = Depends(get_current_user),
                       session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    row = await _mine(session, user, drawing_id)
    row.locked, row.updated_at = body.locked, datetime.now(timezone.utc)
    await session.commit()
    return _as_dict(row)


@router.delete("/{drawing_id}")
async def delete_drawing(drawing_id: int, version: int, user: User = Depends(get_current_user),
                         session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    row = await _mine(session, user, drawing_id)
    if row.locked:
        raise HTTPException(status_code=423, detail="The drawing is locked; unlock it first")
    if version != row.version:
        raise HTTPException(status_code=409, detail={"message": "the drawing changed since it was shown", "current": _as_dict(row)})
    row.deleted_at = row.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return {"deleted": row.id}


@router.get("/export")
async def export_drawings(symbol: str, exchange: str = "NSE", user: User = Depends(get_current_user),
                          session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    rows = await list_drawings(symbol, exchange, user, session)
    return {"format": EXPORT_FORMAT, "symbol": _norm(symbol), "exchange": exchange.upper(), "drawings": [r["drawing"] for r in rows]}


@router.post("/import", status_code=201)
async def import_drawings(doc: ExportDoc, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """All or nothing: every drawing is validated by the schema before any is stored."""
    symbol = _norm(doc.symbol)
    if await _count(session, user, symbol, doc.exchange) + len(doc.drawings) > MAX_PER_SYMBOL:
        raise HTTPException(status_code=409, detail=f"the import would pass {MAX_PER_SYMBOL} drawings for {symbol}")
    rows = [_new(user, symbol, doc.exchange, d) for d in doc.drawings]
    session.add_all(rows)
    await session.commit()
    return {"imported": len(rows), "ids": [r.id for r in rows]}


__all__ = ["router", "Drawing", "ANCHORS", "SCHEMA", "EXPORT_FORMAT", "MAX_PER_SYMBOL"]
