import math

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_trader
from app.auth.scopes import require_scope
from app.core.models import RiskConfig
from app.db.models import RiskSettingsRecord, User
from app.db.session import get_session
from app.platform import controls
from app.risk_engine import loss_streak

router = APIRouter(prefix="/api/risk-settings", tags=["risk"])


# TT12-4 (owner, DECISIONS 24 Oct): the organisation's minimum reward:risk has no built-in value any more (0 = not set).
# Before then every saved row held the built-in 1.2 unless someone changed it, and the row cannot tell the two apart, so
# the TT12-1 rule applies: a stored value equal to the old built-in reads as not set. An organisation that now sets exactly
# 1.2 itself is stored one float step above it (`math.nextafter`), which reads back as 1.2 - set. No migration.
LEGACY_ORG_MIN_RR = 1.2
_OWN_LEGACY_ORG_MIN_RR = math.nextafter(LEGACY_ORG_MIN_RR, math.inf)


def org_min_rr_read(stored: float | None) -> float:
    """The organisation's minimum reward:risk in force (0 = not set) from the stored column value."""
    if stored is None or not math.isfinite(stored) or stored <= 0 or stored == LEGACY_ORG_MIN_RR:
        return 0.0
    return LEGACY_ORG_MIN_RR if stored == _OWN_LEGACY_ORG_MIN_RR else float(stored)


def org_min_rr_to_store(value: float) -> float:
    """The column value for a minimum the organisation sets itself (see LEGACY_ORG_MIN_RR)."""
    return _OWN_LEGACY_ORG_MIN_RR if value == LEGACY_ORG_MIN_RR else value


def _record_to_config(record: RiskSettingsRecord) -> RiskConfig:
    return RiskConfig(
        capital=record.capital,
        risk_per_trade_pct=record.risk_per_trade_pct,
        max_daily_loss_pct=record.max_daily_loss_pct,
        max_trades_per_day=record.max_trades_per_day,
        max_open_positions=record.max_open_positions,
        max_consecutive_losses=record.max_consecutive_losses,
        min_risk_reward=org_min_rr_read(record.min_risk_reward),
        lot_size=record.lot_size,
        max_portfolio_risk_pct=record.max_portfolio_risk_pct if record.max_portfolio_risk_pct is not None else 6.0,
        stop_cooldown_minutes=record.stop_cooldown_minutes if record.stop_cooldown_minutes is not None else 30,
        dd_level_1_pct=record.dd_level_1_pct if record.dd_level_1_pct is not None else 5.0,
        dd_level_2_pct=record.dd_level_2_pct if record.dd_level_2_pct is not None else 10.0,
        event_size_cut_pct=record.event_size_cut_pct if record.event_size_cut_pct is not None else 50.0,
        loss_streak_pause=record.loss_streak_pause if record.loss_streak_pause in loss_streak.PAUSE_MODES else loss_streak.NEXT_SESSION,
    )


@router.get("/ceilings")
async def get_ceilings(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """Phase V1: the platform ceilings this tenant's settings must stay under."""
    return await controls.risk_ceilings(session)


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


@router.put("", response_model=RiskConfig, dependencies=[Depends(require_scope("risk:write"))])
async def update_risk_settings(
    config: RiskConfig, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session),
) -> RiskConfig:
    # Phase V1: never above the platform's hard ceilings, never a shorter cool-down than its minimum.
    violations = controls.ceiling_violations(config, await controls.risk_ceilings(session))
    if violations:
        raise HTTPException(status_code=400, detail="; ".join(violations))
    if not math.isfinite(config.min_risk_reward) or not 0 <= config.min_risk_reward <= 10:
        raise HTTPException(status_code=400, detail="min_risk_reward must be between 0 (not set) and 10")
    if config.dd_level_1_pct > config.dd_level_2_pct > 0:
        raise HTTPException(status_code=400, detail="dd_level_1_pct must not exceed dd_level_2_pct")
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
    record.min_risk_reward = org_min_rr_to_store(config.min_risk_reward)
    record.lot_size = config.lot_size
    record.max_portfolio_risk_pct = config.max_portfolio_risk_pct
    record.stop_cooldown_minutes = config.stop_cooldown_minutes
    record.dd_level_1_pct = config.dd_level_1_pct
    record.dd_level_2_pct = config.dd_level_2_pct
    record.event_size_cut_pct = config.event_size_cut_pct
    record.loss_streak_pause = config.loss_streak_pause

    await session.commit()
    await session.refresh(record)
    return _record_to_config(record)


# --- CP6-15: the losses-in-a-row pause and "Resume after losses in a row" -------------------------------------------------

@router.get("/loss-streak")
async def get_loss_streak(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """The organisation's losses in a row as the risk engine counts them now, whether new entries are paused, when the
    pause ends and the last resume. Exits are never paused (ADR-0004)."""
    from app.trading.persistence import loss_streak_status
    return await loss_streak_status(session, user.tenant_id)


@router.post("/loss-streak/resume", dependencies=[Depends(require_scope("risk:write"))])
async def resume_after_loss_streak(user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> dict:
    """"Resume after losses in a row": with "Pause until I resume", only losses closed after now count from here on.
    Refused (409) under "Pause until the next session" (the next session ends that pause) and when no pause is active.
    Audited; it changes no limit and places nothing."""
    from datetime import datetime, timezone

    from app.audit.log import write_audit_log
    from app.trading.persistence import loss_streak_status
    status = await loss_streak_status(session, user.tenant_id)
    if status["mode"] != loss_streak.UNTIL_RESUME:
        raise HTTPException(status_code=409, detail="Your setting ends this pause at the next session; there is nothing to resume.")
    if not status["paused"]:
        raise HTTPException(status_code=409, detail="New entries are not paused by losses in a row; there is nothing to resume.")
    record = await session.scalar(select(RiskSettingsRecord).where(RiskSettingsRecord.tenant_id == user.tenant_id))
    if record is None:   # unreachable: "until_resume" is only ever read from a saved row
        raise HTTPException(status_code=409, detail="Save your risk limits first.")
    record.loss_streak_resumed_at = datetime.now(timezone.utc)
    record.updated_by = user.id
    await write_audit_log(session, user.tenant_id, user.id, "loss_streak_resumed",
                          f"losses_in_a_row={status['losses_in_a_row']} limit={status['limit']}")
    await session.commit()
    return await loss_streak_status(session, user.tenant_id)
