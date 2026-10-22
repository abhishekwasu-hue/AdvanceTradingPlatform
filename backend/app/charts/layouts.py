"""CH5: saved multi-chart workspace layouts - our own JSON schema (`layout/1`) and its storage API (flag `chart_workspace`,
off by default -> 503).

A layout is a grid of 1, 2, 4, 6 or 8 charts. Each chart (cell) has its symbol, exchange, timeframe, chart type and
an optional link group (A-D); each group says whether its charts share the symbol, the interval and the crosshair.
Layouts are per user (and tenant), named, versioned for optimistic concurrency (an update names the version it edits;
a stale one gets 409 with the current layout) and capped at MAX_PER_USER. A layout is a picture of the screen:
nothing here places, changes or proposes an order.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.charts.drawings import EXCHANGE_RE, SYMBOL_RE
from app.db.models import ChartLayoutRecord, User
from app.db.session import get_session
from app.platform.controls import require_flag

FLAG = "chart_workspace"
SCHEMA = "layout/1"
GRIDS = (1, 2, 4, 6, 8)
GROUPS = ("A", "B", "C", "D")
# UI2-4a2-pre: week, month and the "bars" (OHLC) chart type are accepted so the Charts page can save them; the W/M candles
# themselves come with UI2-4a2. The short labels W, M and D are stored as their ids. Older layouts stay valid.
TIMEFRAMES = ("1min", "3min", "5min", "15min", "30min", "60min", "day", "week", "month")
TIMEFRAME_ALIASES = {"D": "day", "W": "week", "M": "month"}
CHART_TYPES = ("candles", "hollow", "heikin_ashi", "renko", "range", "line", "area", "bars")
MAX_PER_USER = 50
Group = Literal["A", "B", "C", "D"]


class Cell(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(pattern=SYMBOL_RE)
    exchange: str = Field(default="NSE", pattern=EXCHANGE_RE)
    timeframe: Literal["1min", "3min", "5min", "15min", "30min", "60min", "day", "week", "month"] = "5min"
    chart_type: Literal["candles", "hollow", "heikin_ashi", "renko", "range", "line", "area", "bars"] = "candles"
    group: Optional[Group] = None

    @field_validator("timeframe", mode="before")
    @classmethod
    def _timeframe_id(cls, v: Any) -> Any:
        return TIMEFRAME_ALIASES.get(v, v) if isinstance(v, str) else v

    @field_validator("symbol")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()


class GroupSync(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: bool = True
    interval: bool = False
    crosshair: bool = True


class Layout(BaseModel):
    """`layout/1`: the engine-neutral workspace shape the frontend maps in and out."""
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    schema_: Literal["layout/1"] = Field(default="layout/1", alias="schema")
    grid: Literal[1, 2, 4, 6, 8]
    cells: List[Cell] = Field(min_length=1, max_length=8)
    groups: Dict[Group, GroupSync] = Field(default_factory=dict)
    active: int = Field(default=0, ge=0, le=7)

    @model_validator(mode="after")
    def _fits(self) -> "Layout":
        if len(self.cells) != self.grid:
            raise ValueError(f"a {self.grid}-chart grid needs {self.grid} cells, got {len(self.cells)}")
        if self.active >= self.grid:
            raise ValueError("the active chart must be one of the grid's cells")
        return self

    def to_json(self) -> Dict[str, Any]:
        return self.model_dump(by_alias=True, mode="json")


def _name(v: str) -> str:
    v = " ".join(v.split())
    if not v or "<" in v or ">" in v:
        raise ValueError("a layout name is 1-60 characters without < or >")
    return v


class CreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=60)
    layout: Layout

    @field_validator("name")
    @classmethod
    def _clean(cls, v: str) -> str:
        return _name(v)


class UpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    name: Optional[str] = Field(default=None, min_length=1, max_length=60)
    layout: Layout

    @field_validator("name")
    @classmethod
    def _clean(cls, v: Optional[str]) -> Optional[str]:
        return v if v is None else _name(v)


router = APIRouter(prefix="/api/charts/layouts", tags=["charts"])


def _as_dict(row: ChartLayoutRecord) -> Dict[str, Any]:
    return {"id": row.id, "name": row.name, "layout": json.loads(row.layout_json), "version": row.version,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None}


async def _mine(session: AsyncSession, user: User, layout_id: int) -> ChartLayoutRecord:
    row = await session.scalar(select(ChartLayoutRecord).where(ChartLayoutRecord.id == layout_id, ChartLayoutRecord.user_id == user.id,
                                                             ChartLayoutRecord.tenant_id == user.tenant_id))
    if row is None:
        raise HTTPException(status_code=404, detail="Layout not found")
    return row


async def _count(session: AsyncSession, user: User) -> int:
    return int(await session.scalar(select(func.count()).select_from(ChartLayoutRecord).where(ChartLayoutRecord.user_id == user.id)) or 0)


async def _commit_named(session: AsyncSession, name: str) -> None:
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail=f"you already have a layout named {name!r}") from None


@router.get("")
async def list_layouts(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> List[Dict[str, Any]]:
    """The caller's own saved layouts, most recently changed first."""
    await require_flag(session, FLAG, user.tenant_id)
    rows = await session.scalars(select(ChartLayoutRecord).where(
        ChartLayoutRecord.user_id == user.id, ChartLayoutRecord.tenant_id == user.tenant_id)
        .order_by(ChartLayoutRecord.updated_at.desc(), ChartLayoutRecord.id.desc()))
    return [_as_dict(r) for r in rows]


@router.post("", status_code=201)
async def create_layout(body: CreateBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await require_flag(session, FLAG, user.tenant_id)
    # One create at a time per user: the user's row is locked (Postgres SELECT ... FOR UPDATE, held to the commit), so
    # two racing creates are counted one after the other. The re-count after the insert stays as a second guard.
    await session.execute(select(User.id).where(User.id == user.id).with_for_update())
    if await _count(session, user) >= MAX_PER_USER:
        raise HTTPException(status_code=409, detail=f"at most {MAX_PER_USER} saved layouts per user - delete one first")
    now = datetime.now(timezone.utc)
    row = ChartLayoutRecord(tenant_id=user.tenant_id, user_id=user.id, name=body.name, layout_json=json.dumps(body.layout.to_json()),
                            version=1, created_at=now, updated_at=now)
    session.add(row)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail=f"you already have a layout named {body.name!r}") from None
    # Two creates racing past the count above: re-counted with this row in place, the one over the cap is undone.
    if await _count(session, user) > MAX_PER_USER:
        await session.rollback()
        raise HTTPException(status_code=409, detail=f"at most {MAX_PER_USER} saved layouts per user - delete one first")
    await _commit_named(session, body.name)
    return _as_dict(row)


@router.put("/{layout_id}")
async def update_layout(layout_id: int, body: UpdateBody, user: User = Depends(get_current_user),
                        session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await require_flag(session, FLAG, user.tenant_id)
    row = await _mine(session, user, layout_id)
    if body.version != row.version:
        raise HTTPException(status_code=409, detail={"message": "the layout changed since this edit was made", "current": _as_dict(row)})
    name = body.name if body.name is not None else row.name
    uid = user.id                                              # read now: a rollback below expires the user object
    # Conditional on the version read: of two saves racing from two tabs exactly one wins; the other gets 409.
    try:
        result = await session.execute(update(ChartLayoutRecord).where(
            ChartLayoutRecord.id == layout_id, ChartLayoutRecord.user_id == user.id, ChartLayoutRecord.tenant_id == user.tenant_id,
            ChartLayoutRecord.version == body.version).values(
            name=name, layout_json=json.dumps(body.layout.to_json()), version=body.version + 1, updated_at=datetime.now(timezone.utc))
            .execution_options(synchronize_session=False))
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail=f"you already have a layout named {name!r}") from None
    if getattr(result, "rowcount", 0) != 1:
        await session.rollback()
        current = await session.scalar(select(ChartLayoutRecord).where(ChartLayoutRecord.id == layout_id, ChartLayoutRecord.user_id == uid))
        raise HTTPException(status_code=409, detail={"message": "the layout changed since this edit was made",
                                                     "current": _as_dict(current) if current is not None else None})
    await _commit_named(session, name)
    await session.refresh(row)
    return _as_dict(row)


@router.delete("/{layout_id}")
async def delete_layout(layout_id: int, version: int, user: User = Depends(get_current_user),
                        session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await require_flag(session, FLAG, user.tenant_id)
    row = await _mine(session, user, layout_id)
    if version != row.version:
        raise HTTPException(status_code=409, detail={"message": "the layout changed since it was shown", "current": _as_dict(row)})
    result = await session.execute(delete(ChartLayoutRecord).where(
        ChartLayoutRecord.id == layout_id, ChartLayoutRecord.user_id == user.id, ChartLayoutRecord.tenant_id == user.tenant_id,
        ChartLayoutRecord.version == version).execution_options(synchronize_session=False))
    if getattr(result, "rowcount", 0) != 1:                     # saved from another tab in between
        await session.rollback()
        raise HTTPException(status_code=409, detail={"message": "the layout changed since it was shown", "current": None})
    await session.commit()
    return {"deleted": layout_id}


__all__ = ["router", "Layout", "SCHEMA", "GRIDS", "GROUPS", "TIMEFRAMES", "CHART_TYPES", "MAX_PER_USER", "FLAG"]
