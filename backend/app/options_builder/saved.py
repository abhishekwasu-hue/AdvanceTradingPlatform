"""Z5 / P1-c: "Save strategy" - strategies saved from the Options Builder, shared within the tenant (spec §3.2 "Save/share
(tenant)"). Behind the `options_builder_saved` flag, off by default: while it is off every route answers 404 and the
page says saving is not turned on. Research data only - a saved strategy is the page's inputs and legs; nothing here
places, stages or sizes an order.

- `GET    /api/options-builder/strategies`        the tenant's saved strategies (newest first, summaries only).
- `POST   /api/options-builder/strategies`        save one (a trader); 409 past the tenant's cap.
- `GET    /api/options-builder/strategies/{id}`   one, with its inputs and legs, to load into the page.
- `DELETE /api/options-builder/strategies/{id}`   archive it (whoever saved it, or the tenant's owner).
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_trader
from app.db.models import OptionStrategyRecord, User
from app.db.session import get_session
from app.options_builder.routes import MAX_LEGS, MAX_PRICE, LegIn
from app.platform.controls import flag_enabled, require_flag

FLAG = "options_builder_saved"
MAX_PER_TENANT = 500
OWNER_ROLE = "OWNER"            # the tenant's owner may tidy up anyone's saved strategies


async def _enabled(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> None:
    """The builder's kill flag (503 when off) and this feature's own flag (404 while it is off)."""
    await require_flag(session, "options_builder", user.tenant_id)
    if not await flag_enabled(session, FLAG, user.tenant_id):
        raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": FLAG})


router = APIRouter(prefix="/api/options-builder/strategies", tags=["options-builder"], dependencies=[Depends(_enabled)])


class SavedLeg(LegIn):
    model_config = ConfigDict(extra="forbid")
    premium_source: Optional[Literal["model", "manual"]] = None


class Inputs(BaseModel):
    """What the page's instrument row held. Optional: a strategy can be saved before every input is filled."""
    model_config = ConfigDict(extra="forbid")
    spot: Optional[float] = Field(default=None, gt=0, lt=MAX_PRICE, allow_inf_nan=False)
    step: Optional[float] = Field(default=None, gt=0, lt=MAX_PRICE, allow_inf_nan=False)
    lot_size: Optional[int] = Field(default=None, ge=1, le=100_000)
    width: Optional[float] = Field(default=None, gt=0, lt=MAX_PRICE, allow_inf_nan=False)
    base_iv: Optional[float] = Field(default=None, gt=0, lt=500, allow_inf_nan=False, description="percent, as typed")
    near_expiry: Optional[date] = None
    next_expiry: Optional[date] = None


class SaveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    underlying: str = Field(default="", max_length=40)
    inputs: Inputs = Field(default_factory=Inputs)
    legs: List[SavedLeg] = Field(min_length=1, max_length=MAX_LEGS)


def _summary(row: OptionStrategyRecord) -> Dict[str, Any]:
    data = json.loads(row.strategy_json)
    return {"id": row.id, "name": row.name, "underlying": row.underlying, "legs": len(data.get("legs") or []),
            "created_by": row.created_by, "updated_at": row.updated_at.isoformat() if row.updated_at else None}


async def _get(session: AsyncSession, user: User, strategy_id: int) -> OptionStrategyRecord:
    row = await session.scalar(select(OptionStrategyRecord).where(
        OptionStrategyRecord.id == strategy_id, OptionStrategyRecord.tenant_id == user.tenant_id, OptionStrategyRecord.archived.is_(False)))
    if row is None:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return row


@router.get("")
async def list_strategies(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> List[Dict[str, Any]]:
    rows = await session.scalars(select(OptionStrategyRecord).where(
        OptionStrategyRecord.tenant_id == user.tenant_id, OptionStrategyRecord.archived.is_(False)).order_by(OptionStrategyRecord.id.desc()).limit(MAX_PER_TENANT))
    return [_summary(r) for r in rows]


@router.post("", status_code=201)
async def save_strategy(body: SaveBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    count = await session.scalar(select(func.count()).select_from(OptionStrategyRecord).where(
        OptionStrategyRecord.tenant_id == user.tenant_id, OptionStrategyRecord.archived.is_(False)))
    if (count or 0) >= MAX_PER_TENANT:
        raise HTTPException(status_code=409, detail=f"Your organisation has {MAX_PER_TENANT} saved strategies; delete one first")
    payload = {"version": 1, "inputs": body.inputs.model_dump(mode="json"), "legs": [leg.model_dump(mode="json") for leg in body.legs]}
    now = datetime.now(timezone.utc)
    row = OptionStrategyRecord(tenant_id=user.tenant_id, created_by=user.id, name=body.name.strip() or "Untitled",
                               underlying=body.underlying.strip(), strategy_json=json.dumps(payload), archived=False,
                               created_at=now, updated_at=now)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _summary(row)


@router.get("/{strategy_id}")
async def load_strategy(strategy_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    row = await _get(session, user, strategy_id)
    data = json.loads(row.strategy_json)
    return {**_summary(row), "inputs": data.get("inputs") or {}, "legs": data.get("legs") or []}


@router.delete("/{strategy_id}")
async def archive_strategy(strategy_id: int, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    row = await _get(session, user, strategy_id)
    if row.created_by != user.id and user.role != OWNER_ROLE:
        raise HTTPException(status_code=403, detail="Only whoever saved it, or the owner, can delete a saved strategy")
    row.archived = True
    row.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return {"id": row.id, "archived": True}
