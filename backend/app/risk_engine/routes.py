from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.core.models import RiskConfig
from app.db.models import RiskSettingsRecord, User
from app.db.session import get_session

router = APIRouter(prefix="/api/risk-settings", tags=["risk"])


def _record_to_config(record: RiskSettingsRecord) -> RiskConfig:
    return RiskConfig(
        capital=record.capital,
        risk_per_trade_pct=record.risk_per_trade_pct,
        max_daily_loss_pct=record.max_daily_loss_pct,
        max_trades_per_day=record.max_trades_per_day,
        max_open_positions=record.max_open_positions,
        max_consecutive_losses=record.max_consecutive_losses,
        min_risk_reward=record.min_risk_reward,
        lot_size=record.lot_size,
    )


async def get_tenant_risk_config(tenant_id: int, session: AsyncSession) -> RiskConfig | None:
    """Used by /paper-execute to apply a logged-in user's tenant-wide limits when the request
    doesn't explicitly override risk_config. Returns None if the tenant has never customized
    anything - the caller then falls back to the platform default.
    """
    record = await session.scalar(select(RiskSettingsRecord).where(RiskSettingsRecord.tenant_id == tenant_id))
    return _record_to_config(record) if record is not None else None


@router.get("", response_model=RiskConfig)
async def get_risk_settings(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> RiskConfig:
    """This tenant's saved risk settings, or the platform default if it hasn't customized any."""
    config = await get_tenant_risk_config(user.tenant_id, session)
    return config if config is not None else RiskConfig()


@router.put("", response_model=RiskConfig)
async def update_risk_settings(
    config: RiskConfig, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> RiskConfig:
    record = await session.scalar(select(RiskSettingsRecord).where(RiskSettingsRecord.tenant_id == user.tenant_id))
    if record is None:
        record = RiskSettingsRecord(tenant_id=user.tenant_id, updated_by=user.id)
        session.add(record)
    else:
        record.updated_by = user.id

    record.capital = config.capital
    record.risk_per_trade_pct = config.risk_per_trade_pct
    record.max_daily_loss_pct = config.max_daily_loss_pct
    record.max_trades_per_day = config.max_trades_per_day
    record.max_open_positions = config.max_open_positions
    record.max_consecutive_losses = config.max_consecutive_losses
    record.min_risk_reward = config.min_risk_reward
    record.lot_size = config.lot_size

    await session.commit()
    await session.refresh(record)
    return _record_to_config(record)
