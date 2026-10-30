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
from app.risk_engine import loss_streak, risk_limits

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


def _record_to_config(record: RiskSettingsRecord, *, switched: bool = True) -> RiskConfig:
    """The organisation's limits as the engine checks them: M64 - a check switched off on Settings > Risk limits reads
    as not set (`switched`; app/risk_engine/risk_limits.py). `switched=False` is the stored values, switches ignored."""
    cfg = _stored_config(record)
    return risk_limits.apply_switches(cfg, risk_limits.switches_of(record)["enabled"]) if switched else cfg


def _stored_config(record: RiskSettingsRecord) -> RiskConfig:
    return RiskConfig(
        capital=record.capital,
        risk_per_trade_pct=record.risk_per_trade_pct,
        max_daily_loss_pct=record.max_daily_loss_pct,
        max_trades_per_day=record.max_trades_per_day,
        max_open_positions=record.max_open_positions,
        max_consecutive_losses=record.max_consecutive_losses,
        min_risk_reward=org_min_rr_read(record.min_risk_reward),
        lot_size=record.lot_size,
        # DEF-0: a NULL column is a limit the organisation did not set - off, never a built-in value.
        max_portfolio_risk_pct=record.max_portfolio_risk_pct,
        stop_cooldown_minutes=record.stop_cooldown_minutes,
        dd_level_1_pct=record.dd_level_1_pct,
        dd_level_2_pct=record.dd_level_2_pct,
        event_size_cut_pct=record.event_size_cut_pct,
        loss_streak_pause=record.loss_streak_pause if record.loss_streak_pause in loss_streak.PAUSE_MODES else loss_streak.NEXT_SESSION,
        max_lots_per_order=record.max_lots_per_order, daily_loss_rupees=record.daily_loss_rupees,
        risk_per_trade_rupees=record.risk_per_trade_rupees, loss_cooldown_minutes=record.loss_cooldown_minutes,
        margin_safety_pct=record.margin_safety_pct,
    )


@router.get("/ceilings")
async def get_ceilings(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """Phase V1: the platform ceilings this tenant's settings must stay under."""
    return await controls.risk_ceilings(session)


async def get_tenant_risk_config(tenant_id: int, session: AsyncSession) -> RiskConfig | None:
    """Used by /paper-execute to apply a logged-in user's tenant-wide limits when the request
    doesn't explicitly override risk_config. Returns None if the tenant has never saved any - the caller then uses
    `RiskConfig()`, where every limit is not set (DEF-0).
    """
    record = await session.scalar(select(RiskSettingsRecord).where(RiskSettingsRecord.tenant_id == tenant_id))
    return _record_to_config(record) if record is not None else None


async def user_risk_config(session: AsyncSession, user: User) -> RiskConfig:
    """DEF-0 (owner, 28 Oct): the organisation's saved limits (each not set = off) with the capital any % reads: the
    saved capital when set, else this user's PAPER account balance - never a built-in figure."""
    from app.risk_engine.by_default import capital_basis
    cfg = await get_tenant_risk_config(user.tenant_id, session) or RiskConfig()
    return cfg if cfg.capital is not None else cfg.with_capital(await capital_basis(session, user))


@router.get("", response_model=RiskConfig)
async def get_risk_settings(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> RiskConfig:
    """This tenant's saved risk settings; with none saved, every limit is not set (DEF-0: no platform default)."""
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
    if config.dd_level_1_pct is not None and config.dd_level_2_pct is not None and config.dd_level_1_pct > config.dd_level_2_pct > 0:
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
    # M64: the fields this older form does not send keep their values; each check it sends is ON when its value is set,
    # OFF when cleared, and counts as saved by the trader (app/risk_engine/risk_limits.py).
    for key in ("max_lots_per_order", "daily_loss_rupees", "risk_per_trade_rupees", "loss_cooldown_minutes", "margin_safety_pct"):
        if key in config.model_fields_set:
            setattr(record, key, getattr(config, key))
    switches = risk_limits.switches_of(record if record.id is not None else None)
    enabled, saved = set(switches["enabled"]), set(switches["saved"])
    sent = set(config.model_fields_set)
    stored = risk_limits.values_of(record)
    for check in risk_limits.CHECKS:
        if not any(f.key in sent for f in check.fields):
            continue
        saved.add(check.id)
        if any(risk_limits.is_set(f.key, stored.get(f.key)) for f in check.fields):
            enabled.add(check.id)
        else:
            enabled.discard(check.id)
    record.limits_switches_json = risk_limits.switches_json({"enabled": sorted(enabled), "saved": sorted(saved)})

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


# --- M64: Settings > Risk limits (one page; every check a switch, OFF by default) ----------------------------------------

# The checks the PAPER ticket's own limits (app/risk_engine/by_default.py, flag risk_by_default) also hold: Risk limits
# writes them there too, so the trader sees and sets ONE value (that module keeps its rule that a looser value waits for
# the next day).
PAPER_TWINS: dict[str, dict[str, str]] = {
    "risk_sizing": {"risk_per_trade_pct": "risk_per_trade_pct", "risk_per_trade_rupees": "risk_per_trade_amount"},
    "daily_loss": {"max_daily_loss_pct": "daily_loss_pct"},
    "max_entries_per_day": {"max_trades_per_day": "max_trades_per_day"},
    "min_reward_risk": {"min_risk_reward": "min_rr"},
    "max_open_positions": {"max_open_positions": "max_open_positions"},
}


async def _paper_twin_values(session: AsyncSession, user: User) -> dict:
    """The PAPER ticket's own values of the twin checks the trader set there (flag on), {check: {field: value}}."""
    from app.market_data.calendar import to_ist
    from app.risk_engine import by_default
    if not await by_default.enabled(session, user.tenant_id):
        return {}
    stored = (await by_default.user_settings(session, user)).as_dict()
    row = await by_default._account(session, user)
    keys = set(by_default.set_on(row.risk_settings_json if row is not None else None, to_ist(None).date()))
    out: dict = {}
    for check_id, pairs in PAPER_TWINS.items():
        vals = {field: stored[twin] for field, twin in pairs.items() if twin in keys and stored.get(twin)}
        if vals:
            out[check_id] = vals
    return out


@router.get("/limits")
async def get_risk_limits(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """Settings > Risk limits: every check with its switch (OFF until the trader turns it on), its values, units, bounds
    and one-line help; the PAPER ticket's own values of the same checks (flag risk_by_default) under `paper`."""
    record = await session.scalar(select(RiskSettingsRecord).where(RiskSettingsRecord.tenant_id == user.tenant_id))
    view = risk_limits.view(record, await controls.risk_ceilings(session))
    view["paper"] = await _paper_twin_values(session, user)
    return view


@router.put("/limits", dependencies=[Depends(require_scope("risk:write"))])
async def put_risk_limits(body: dict, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> dict:
    """Saves switches and values from Settings > Risk limits (`{"checks": {id: {"enabled", "values"}}, "capital",
    "loss_streak_pause"}`). A check switched on needs its value; a switched-off one keeps its value. Each check sent
    counts as saved by the trader. Audited. Places nothing; exits are never checked (ADR-0004)."""
    from app.audit.log import write_audit_log
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="The limits could not be read. Reload the page and try again.")
    ceilings = await controls.risk_ceilings(session)
    record = await session.scalar(select(RiskSettingsRecord).where(RiskSettingsRecord.tenant_id == user.tenant_id))
    values, switches, problems = risk_limits.plan_save(record, body, ceilings)
    capital = body.get("capital", getattr(record, "capital", None) if record is not None else None)
    if capital is not None and (not isinstance(capital, (int, float)) or isinstance(capital, bool) or not math.isfinite(capital) or capital <= 0):
        problems.append("Account basis: enter an amount above 0, or leave it empty to use your paper balance.")
    pause = body.get("loss_streak_pause", getattr(record, "loss_streak_pause", None) if record is not None else None) or loss_streak.NEXT_SESSION
    if pause not in loss_streak.PAUSE_MODES:
        problems.append("Cool-down after losses in a row: pick when the pause ends.")
    if problems:
        raise HTTPException(status_code=422, detail=problems[0])
    if record is None:
        record = RiskSettingsRecord(tenant_id=user.tenant_id, updated_by=user.id, min_risk_reward=0.0, lot_size=1)
        session.add(record)
    record.updated_by = user.id
    for key, value in values.items():
        setattr(record, key, value)
    record.capital = float(capital) if capital is not None else None
    record.loss_streak_pause = pause
    record.limits_switches_json = risk_limits.switches_json(switches)
    sent = sorted((body.get("checks") or {}).keys())
    await write_audit_log(session, user.tenant_id, user.id, "risk_limits_set",
                          (f"on: {', '.join(switches['enabled']) or 'none'}; changed: {', '.join(sent) or 'none'}")[:500])
    await session.commit()
    note = await _write_paper_twins(session, user, body.get("checks") or {}, switches["enabled"], values)
    from app.me import notices
    await notices.mark_seen(session, user, notices.RISK_LIMITS_OFF)
    record = await session.scalar(select(RiskSettingsRecord).where(RiskSettingsRecord.tenant_id == user.tenant_id))
    view = risk_limits.view(record, ceilings)
    view["paper"] = await _paper_twin_values(session, user)
    view["note"] = note
    return view


async def _write_paper_twins(session: AsyncSession, user: User, sent: dict, enabled: list[str], values: dict) -> str | None:
    """The PAPER ticket's own limits get the same values for the twin checks sent (flag risk_by_default on). A value its
    narrower bounds refuse is left as it was and said in the returned note."""
    from app.paper import ticket
    from app.risk_engine import by_default
    if not await by_default.enabled(session, user.tenant_id):
        return None
    out: dict = {}
    for check_id, pairs in PAPER_TWINS.items():
        if check_id not in sent:
            continue
        for field, twin in pairs.items():
            value = values.get(field) if check_id in enabled else None
            out[twin] = value if isinstance(value, (int, float)) and value > 0 else None
    if not out:
        return None
    try:
        change = await ticket.save_settings(session, user, out)
    except ValueError:
        return "Your paper ticket keeps its own earlier values for one of these limits; they are outside its range."
    if change.get("deferred"):
        return f"On the paper ticket, a looser value starts on {change.get('from')} (a limit is never widened during the day it protects)."
    return None
