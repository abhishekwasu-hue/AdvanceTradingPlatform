from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.custom_strategies.resolver import CUSTOM_PREFIX
from app.db.models import CustomStrategyRecord, User
from app.db.session import get_session
from app.strategy_engine.declarative import CustomStrategyConfig, DeclarativeStrategy

router = APIRouter(prefix="/api/custom-strategies", tags=["custom-strategies"])


class CustomStrategyResponse(BaseModel):
    id: int
    strategy_id: str
    config: CustomStrategyConfig
    created_at: str
    updated_at: str

    @classmethod
    def from_record(cls, record: CustomStrategyRecord) -> "CustomStrategyResponse":
        return cls(
            id=record.id,
            strategy_id=f"{CUSTOM_PREFIX}{record.id}",
            config=CustomStrategyConfig.model_validate_json(record.config_json),
            created_at=record.created_at.isoformat(),
            updated_at=record.updated_at.isoformat(),
        )


@router.post("", response_model=CustomStrategyResponse, status_code=201)
async def create_custom_strategy(
    config: CustomStrategyConfig,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> CustomStrategyResponse:
    """Validates the rule set by actually constructing a DeclarativeStrategy from it (catches
    e.g. an indicator/period combination that can't run), then saves it for this user only.
    """
    try:
        DeclarativeStrategy("custom:validation", config)
    except (ValueError, ValidationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    record = CustomStrategyRecord(
        tenant_id=user.tenant_id, user_id=user.id, name=config.name, config_json=config.model_dump_json()
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return CustomStrategyResponse.from_record(record)


@router.get("", response_model=List[CustomStrategyResponse])
async def list_custom_strategies(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> List[CustomStrategyResponse]:
    rows = await session.scalars(
        select(CustomStrategyRecord)
        .where(CustomStrategyRecord.tenant_id == user.tenant_id)
        .order_by(CustomStrategyRecord.created_at.desc())
    )
    return [CustomStrategyResponse.from_record(r) for r in rows]


async def _get_owned_or_404(strategy_id: int, user: User, session: AsyncSession) -> CustomStrategyRecord:
    record = await session.get(CustomStrategyRecord, strategy_id)
    if record is None or record.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Unknown custom strategy")
    return record


@router.get("/{strategy_id}", response_model=CustomStrategyResponse)
async def get_custom_strategy(
    strategy_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> CustomStrategyResponse:
    record = await _get_owned_or_404(strategy_id, user, session)
    return CustomStrategyResponse.from_record(record)


@router.delete("/{strategy_id}", status_code=204)
async def delete_custom_strategy(
    strategy_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> None:
    record = await _get_owned_or_404(strategy_id, user, session)
    await session.delete(record)
    await session.commit()
