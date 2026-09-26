from typing import List

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CustomStrategyRecord, StrategyVersionRecord, User
from app.strategy_engine.declarative import CustomStrategyConfig


async def _next_version_number(session: AsyncSession, custom_strategy_id: int) -> int:
    current_max = await session.scalar(
        select(func.max(StrategyVersionRecord.version_number))
        .where(StrategyVersionRecord.custom_strategy_id == custom_strategy_id)
    )
    return (current_max or 0) + 1


async def _archive_current_live_version(session: AsyncSession, custom_strategy_id: int) -> None:
    live = await session.scalar(
        select(StrategyVersionRecord).where(
            StrategyVersionRecord.custom_strategy_id == custom_strategy_id,
            StrategyVersionRecord.status == "LIVE",
        )
    )
    if live is not None:
        live.status = "ARCHIVED"


async def create_version(
    session: AsyncSession, record: CustomStrategyRecord, config: CustomStrategyConfig, user: User,
    source: str = "created",
) -> StrategyVersionRecord:
    """Appends a new immutable version, archives whichever version was previously live, and
    repoints the parent CustomStrategyRecord's denormalized name/config_json/live_version_id at
    it - the single write path every create/update/rollback goes through.
    """
    await _archive_current_live_version(session, record.id)

    version = StrategyVersionRecord(
        tenant_id=record.tenant_id, custom_strategy_id=record.id,
        version_number=await _next_version_number(session, record.id),
        config_json=config.model_dump_json(), source=source, status="LIVE", created_by=user.id,
    )
    session.add(version)
    await session.flush()

    record.name = config.name
    record.config_json = version.config_json
    record.live_version_id = version.id
    await session.commit()
    await session.refresh(version)
    return version


async def list_versions(session: AsyncSession, custom_strategy_id: int) -> List[StrategyVersionRecord]:
    rows = await session.scalars(
        select(StrategyVersionRecord)
        .where(StrategyVersionRecord.custom_strategy_id == custom_strategy_id)
        .order_by(StrategyVersionRecord.version_number.desc())
    )
    return list(rows)


async def get_version(
    session: AsyncSession, custom_strategy_id: int, version_number: int,
) -> StrategyVersionRecord | None:
    return await session.scalar(
        select(StrategyVersionRecord).where(
            StrategyVersionRecord.custom_strategy_id == custom_strategy_id,
            StrategyVersionRecord.version_number == version_number,
        )
    )
