from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import StrategyCategory
from app.core.models import StrategyInfo
from app.db.models import CustomStrategyRecord, User
from app.strategy_engine.base import BaseStrategy
from app.strategy_engine.declarative import CustomStrategyConfig, DeclarativeStrategy
from app.strategy_engine.registry import registry

CUSTOM_PREFIX = "custom:"


def custom_strategy_info(record: CustomStrategyRecord) -> StrategyInfo:
    """Presents a saved custom strategy the same shape as a built-in one, so the two can be
    merged into a single list for the frontend (GET /api/strategies).
    """
    config = CustomStrategyConfig.model_validate_json(record.config_json)
    return StrategyInfo(
        id=f"{CUSTOM_PREFIX}{record.id}",
        name=config.name,
        description="User-defined strategy built with the no-code Strategy Builder.",
        category=StrategyCategory.INDICATOR_BASED,
        timeframes=[config.timeframe],
        default_params={},
    )


async def load_custom_strategy(strategy_id: str, user: Optional[User], session: AsyncSession) -> DeclarativeStrategy:
    """Loads and rehydrates a saved custom strategy belonging to the caller's tenant. Raises
    PermissionError if no user is authenticated, and KeyError if the id doesn't exist or belongs
    to a different tenant - both cases look identical from the outside (a bare 404-worthy
    KeyError) except PermissionError maps to 403, since a custom strategy's config is private to
    its owning tenant.
    """
    if user is None:
        raise PermissionError("Custom strategies require authentication")

    raw_id = strategy_id[len(CUSTOM_PREFIX):]
    try:
        record_id = int(raw_id)
    except ValueError as exc:
        raise KeyError(f"Unknown strategy id: {strategy_id}") from exc

    record = await session.get(CustomStrategyRecord, record_id)
    if record is None or record.tenant_id != user.tenant_id:
        raise KeyError(f"Unknown strategy id: {strategy_id}")

    config = CustomStrategyConfig.model_validate_json(record.config_json)
    return DeclarativeStrategy(strategy_id, config)


async def resolve_strategy(strategy_id: str, user: Optional[User], session: AsyncSession) -> BaseStrategy:
    """The single lookup path every /strategies/{id}/... route uses: a built-in strategy by id,
    or - for a "custom:<id>" id - a saved DeclarativeStrategy owned by the calling user.
    """
    if strategy_id.startswith(CUSTOM_PREFIX):
        return await load_custom_strategy(strategy_id, user, session)
    return registry.get(strategy_id)
