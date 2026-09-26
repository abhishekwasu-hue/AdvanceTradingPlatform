from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_trader
from app.custom_strategies import versioning
from app.custom_strategies.resolver import CUSTOM_PREFIX
from app.db.models import CustomStrategyRecord, StrategyVersionRecord, User
from app.db.session import get_session
from app.strategy_engine.declarative import CustomStrategyConfig, DeclarativeStrategy
from app.strategy_engine.nlu_parser import ParseResult, parse_strategy_description

router = APIRouter(prefix="/api/custom-strategies", tags=["custom-strategies"])


class ParseStrategyRequest(BaseModel):
    text: str
    name: str = "Parsed Strategy"


@router.post("/parse", response_model=ParseResult)
async def parse_strategy_text(request: ParseStrategyRequest) -> ParseResult:
    """Turns a plain-English strategy description into a pre-filled Strategy Builder config for
    the user to review and edit - a deterministic, rule-based parser (see
    app/strategy_engine/nlu_parser.py for exactly what it recognizes), not a call to an external
    AI provider (none is configured). Pure function of its input, no persistence: the caller
    still has to review the result and POST it to "" (create_custom_strategy) to actually save
    it, exactly like manually building one in the condition editor.
    """
    return parse_strategy_description(request.text, name=request.name)


def _validate_or_400(config: CustomStrategyConfig) -> None:
    """Validates the rule set by actually constructing a DeclarativeStrategy from it - catches
    e.g. an indicator/period combination that can't run, at save time rather than at first use.
    """
    try:
        DeclarativeStrategy("custom:validation", config)
    except (ValueError, ValidationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class CustomStrategyResponse(BaseModel):
    id: int
    strategy_id: str
    config: CustomStrategyConfig
    live_version_id: Optional[int]
    created_at: str
    updated_at: str

    @classmethod
    def from_record(cls, record: CustomStrategyRecord) -> "CustomStrategyResponse":
        return cls(
            id=record.id,
            strategy_id=f"{CUSTOM_PREFIX}{record.id}",
            config=CustomStrategyConfig.model_validate_json(record.config_json),
            live_version_id=record.live_version_id,
            created_at=record.created_at.isoformat(),
            updated_at=record.updated_at.isoformat(),
        )


class StrategyVersionResponse(BaseModel):
    version_number: int
    config: CustomStrategyConfig
    source: str
    status: str
    created_at: str

    @classmethod
    def from_record(cls, record: StrategyVersionRecord) -> "StrategyVersionResponse":
        return cls(
            version_number=record.version_number,
            config=CustomStrategyConfig.model_validate_json(record.config_json),
            source=record.source, status=record.status, created_at=record.created_at.isoformat(),
        )


@router.post("", response_model=CustomStrategyResponse, status_code=201)
async def create_custom_strategy(
    config: CustomStrategyConfig,
    user: User = Depends(require_trader),
    session: AsyncSession = Depends(get_session),
) -> CustomStrategyResponse:
    """Saves a new strategy for this tenant, as immutable version 1 - see PUT/rollback below for
    how later edits are versioned rather than overwritten."""
    _validate_or_400(config)

    record = CustomStrategyRecord(
        tenant_id=user.tenant_id, user_id=user.id, name=config.name, config_json=config.model_dump_json()
    )
    session.add(record)
    await session.flush()
    await versioning.create_version(session, record, config, user, source="created")
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
    strategy_id: int, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session),
) -> None:
    record = await _get_owned_or_404(strategy_id, user, session)
    await session.delete(record)
    await session.commit()


@router.put("/{strategy_id}", response_model=CustomStrategyResponse)
async def update_custom_strategy(
    strategy_id: int, config: CustomStrategyConfig,
    user: User = Depends(require_trader), session: AsyncSession = Depends(get_session),
) -> CustomStrategyResponse:
    """Edits a strategy by appending a new immutable version and repointing the live pointer at
    it - the previous version's config_json is never touched, only its `status` (LIVE ->
    ARCHIVED). See GET .../versions for the full history and POST .../versions/{n}/rollback to
    go back to an earlier one.
    """
    record = await _get_owned_or_404(strategy_id, user, session)
    _validate_or_400(config)
    await versioning.create_version(session, record, config, user, source="user_edit")
    await session.refresh(record)
    return CustomStrategyResponse.from_record(record)


@router.get("/{strategy_id}/versions", response_model=List[StrategyVersionResponse])
async def list_strategy_versions(
    strategy_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> List[StrategyVersionResponse]:
    await _get_owned_or_404(strategy_id, user, session)
    rows = await versioning.list_versions(session, strategy_id)
    return [StrategyVersionResponse.from_record(r) for r in rows]


@router.post("/{strategy_id}/versions/{version_number}/rollback", response_model=CustomStrategyResponse)
async def rollback_strategy_version(
    strategy_id: int, version_number: int,
    user: User = Depends(require_trader), session: AsyncSession = Depends(get_session),
) -> CustomStrategyResponse:
    """Makes an earlier version live again - by appending a brand-new version whose content
    matches the target one (source="rollback"), never by resurrecting or mutating the old row.
    The history keeps growing forward even when the *config* goes backward.
    """
    record = await _get_owned_or_404(strategy_id, user, session)
    target = await versioning.get_version(session, strategy_id, version_number)
    if target is None:
        raise HTTPException(status_code=404, detail=f"Unknown version {version_number} for this strategy")

    config = CustomStrategyConfig.model_validate_json(target.config_json)
    await versioning.create_version(session, record, config, user, source="rollback")
    await session.refresh(record)
    return CustomStrategyResponse.from_record(record)
