from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import StrategyCategory
from app.core.models import StrategyInfo
from app.db.models import CustomStrategyRecord, User
from app.strategy_engine.base import BaseStrategy
from app.strategy_engine.declarative import CustomStrategyConfig, DeclarativeStrategy
from app.strategy_engine.registry import registry

CUSTOM_PREFIX = "custom:"
TEMPLATE_PREFIX = "template:"   # strategy templates step 2 (app/strategy_templates/ids.py PREFIX, tested equal)
LEGACY_CUSTOM_PREFIX = "custom_"   # P0.8 / A4: the spelling the AI draft / marketplace routes handed out before this release


def normalize_strategy_id(strategy_id: str) -> str:
    """`custom_12` (legacy) -> `custom:12`; everything else unchanged. Deployments and AI records written with the
    legacy spelling keep resolving (the migration rewrites stored deployments too)."""
    if strategy_id.startswith(LEGACY_CUSTOM_PREFIX) and strategy_id[len(LEGACY_CUSTOM_PREFIX):].isdigit():
        return CUSTOM_PREFIX + strategy_id[len(LEGACY_CUSTOM_PREFIX):]
    return strategy_id


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

    strategy_id = normalize_strategy_id(strategy_id)
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


async def load_template_strategy(strategy_id: str, user: Optional[User], session: AsyncSession) -> BaseStrategy:
    """Strategy templates step 2: a "template:<id>:<version>:..." id (app/strategy_templates/ids.py) as the template
    adapter with the settings the id carries. Behind the flag `strategy_templates` and the flags of the functions the
    recipe reads (e.g. the breakout screens); off - or an id the library no longer matches - reads as an unknown strategy
    (KeyError), so a deployment created while it was on stops with a plain reason rather than trading something else."""
    from app.platform.controls import flag_enabled
    from app.strategy_templates import ids
    from app.strategy_templates.adapter import TemplateStrategy
    from app.strategy_templates.compile import TemplateError
    tenant_id = user.tenant_id if user is not None else None
    if not await flag_enabled(session, "strategy_templates", tenant_id):
        raise KeyError(f"Unknown strategy id: {strategy_id}")
    template, settings = ids.decode(strategy_id)
    try:
        strategy = TemplateStrategy(template, settings)
    except (TemplateError, ValueError) as exc:
        raise KeyError(f"This strategy template cannot run with these settings: {exc}") from exc
    for flag in sorted(strategy.recipe.required_flags):
        if not await flag_enabled(session, flag, tenant_id):
            raise KeyError("This strategy template is not switched on for your account yet")
    return strategy


async def resolve_strategy(strategy_id: str, user: Optional[User], session: AsyncSession) -> BaseStrategy:
    """The single lookup path every /strategies/{id}/... route uses: a built-in strategy by id,
    or - for a "custom:<id>" id - a saved DeclarativeStrategy owned by the calling user, or - for a "template:..." id - a
    strategy template with the settings the id carries (step 2, flag `strategy_templates`).
    """
    strategy_id = normalize_strategy_id(strategy_id)
    if strategy_id.startswith(CUSTOM_PREFIX):
        return await load_custom_strategy(strategy_id, user, session)
    if strategy_id.startswith(TEMPLATE_PREFIX):
        return await load_template_strategy(strategy_id, user, session)
    return registry.get(strategy_id)
