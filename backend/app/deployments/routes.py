"""Deployments API: what a tenant runs autonomously, on which symbol, in which mode.

A deployment is the instruction the worker (app/workers/trading_worker.py) acts on. Creating one
is deliberately strict - LIVE requires a stored broker whose token is proven VALID right now,
PAPER still needs *some* stored broker for market data, and the base timeframe must be able to
build every timeframe the strategy consumes - because the worker acting on a half-configured
deployment would only discover the problem at 09:15 with real money on the line.
"""
import json
import logging
import re
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.auth.dependencies import current_session_id, ensure_live_step_up, get_current_user, require_trader
from app.brokers.registry import available_brokers
from app.brokers.token_lifecycle import build_adapter, get_credential_record, token_is_usable
from app.market_data.calendar import IST
from datetime import datetime
from app.core.enums import DeploymentStatus, ExecutionMode, ExpiryRule, InstrumentKind, OptionPosition, SignalDirection, StrikeRule
from app.custom_strategies.resolver import resolve_strategy
from app.db.models import BrokerCredentialRecord, StrategyDeploymentRecord, TradeRecord, User
from app.instruments import master as instrument_master
from app.instruments.strike_selection import StrikeFilters
from app.instruments.contracts import (
    DEFAULT_PREMIUM_STOP_PCT, ContractResolutionError, ContractRules, describe_rules, resolve_contract,
)
from app.db.session import get_session
from app.plans.limits import check_can_add_deployment, load_tenant

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/deployments", tags=["deployments"])

# Roles allowed to change what the platform trades: OWNER/USER/STRATEGY_CREATOR. VIEWER and
# SUPPORT are read-only by design; SUPER_ADMIN always passes.
can_manage = require_trader

SUPPORTED_BASE_TIMEFRAMES = ("1min", "3min", "5min", "15min", "30min", "60min")

_TF_RE = re.compile(r"^(\d+)(min|h|d)$")


def _timeframe_minutes(label: str) -> Optional[int]:
    match = _TF_RE.match(label)
    if not match:
        return None
    value, unit = int(match.group(1)), match.group(2)
    return value * {"min": 1, "h": 60, "d": 24 * 60}[unit]


class StrikeFiltersRequest(BaseModel):
    """Phase H1: option-chain filters applied around the rule strike at resolution time."""
    min_oi: Optional[float] = Field(default=None, ge=0)
    min_volume: Optional[float] = Field(default=None, ge=0)
    max_spread_pct: Optional[float] = Field(default=None, gt=0, le=100)
    min_iv_pct: Optional[float] = Field(default=None, ge=0, le=500)
    max_iv_pct: Optional[float] = Field(default=None, ge=0, le=500)
    target_delta: Optional[float] = Field(default=None, gt=0, lt=1)
    delta_tolerance: float = Field(default=0.10, gt=0, le=0.5)
    min_premium: Optional[float] = Field(default=None, ge=0)
    max_premium: Optional[float] = Field(default=None, gt=0)
    search_steps: int = Field(default=5, ge=1, le=20)

    def to_filters(self) -> StrikeFilters:
        return StrikeFilters(**self.model_dump())


class ContractRulesRequest(BaseModel):
    """Phase F2: what to trade when the strategy signals on `symbol`. Defaults reproduce the
    original behaviour (trade the underlying itself)."""
    instrument_kind: InstrumentKind = InstrumentKind.UNDERLYING
    option_position: Optional[OptionPosition] = None
    expiry_rule: Optional[ExpiryRule] = None
    strike_rule: Optional[StrikeRule] = None
    strike_offset: int = Field(default=0, ge=0, le=10)
    premium_stop_pct: Optional[float] = Field(default=None, ge=5, le=95)
    max_lots: Optional[int] = Field(default=None, ge=1, le=500)
    strike_filters: Optional[StrikeFiltersRequest] = None

    def normalised(self) -> "ContractRulesRequest":
        """Fills the defaults the kind implies and rejects rules that make no sense for it."""
        data = self.model_dump()
        if self.instrument_kind == InstrumentKind.UNDERLYING:
            if any(data[k] for k in ("option_position", "expiry_rule", "strike_rule", "premium_stop_pct")) or self.strike_offset or self.strike_filters:
                raise HTTPException(status_code=400, detail="Option/future rules only apply when instrument_kind is OPTION or FUTURE")
            return self
        if self.strike_filters is not None and self.instrument_kind != InstrumentKind.OPTION:
            raise HTTPException(status_code=400, detail="Strike filters only apply to OPTION deployments")
        if self.strike_filters is not None and self.strike_filters.min_iv_pct is not None and self.strike_filters.max_iv_pct is not None \
                and self.strike_filters.min_iv_pct > self.strike_filters.max_iv_pct:
            raise HTTPException(status_code=400, detail="min_iv_pct must not exceed max_iv_pct")
        if self.strike_filters is not None and self.strike_filters.min_premium is not None and self.strike_filters.max_premium is not None \
                and self.strike_filters.min_premium > self.strike_filters.max_premium:
            raise HTTPException(status_code=400, detail="min_premium must not exceed max_premium")
        data["expiry_rule"] = self.expiry_rule or ExpiryRule.NEAREST
        if self.instrument_kind == InstrumentKind.FUTURE:
            if self.option_position or self.strike_rule or self.strike_offset or self.premium_stop_pct is not None:
                raise HTTPException(status_code=400, detail="Futures take only an expiry rule (and max_lots)")
            return ContractRulesRequest(**data)
        position = self.option_position or OptionPosition.BUY
        data["option_position"] = position
        data["strike_rule"] = self.strike_rule or StrikeRule.ATM
        if data["strike_rule"] == StrikeRule.ATM and self.strike_offset:
            raise HTTPException(status_code=400, detail="strike_offset only applies to ITM/OTM strikes")
        if data["strike_rule"] != StrikeRule.ATM and not self.strike_offset:
            data["strike_offset"] = 1
        if self.premium_stop_pct is None:
            data["premium_stop_pct"] = DEFAULT_PREMIUM_STOP_PCT[position]
        return ContractRulesRequest(**data)

    def to_rules(self) -> ContractRules:
        return ContractRules(
            kind=self.instrument_kind, position=self.option_position, expiry_rule=self.expiry_rule or ExpiryRule.NEAREST,
            strike_rule=self.strike_rule or StrikeRule.ATM, strike_offset=self.strike_offset,
            premium_stop_pct=self.premium_stop_pct, max_lots=self.max_lots,
            strike_filters=self.strike_filters.to_filters() if self.strike_filters else StrikeFilters(),
        )

    def filters_json(self) -> Optional[str]:
        return self.strike_filters.to_filters().to_json() if self.strike_filters else None


class DeploymentCreateRequest(ContractRulesRequest):
    strategy_id: str = Field(min_length=1, max_length=100)
    symbol: str = Field(min_length=1, max_length=50)
    exchange: str = Field(default="NSE", min_length=1, max_length=20)
    timeframe: str = Field(default="1min", description="Base candle interval fetched from the broker")
    mode: ExecutionMode = ExecutionMode.PAPER
    broker_name: Optional[str] = None


class ContractPreviewRequest(ContractRulesRequest):
    symbol: str = Field(min_length=1, max_length=50)
    spot: Optional[float] = Field(default=None, gt=0, description="Underlying price to pick the strike from; looked up from the broker when omitted")


class DeploymentActionRequest(BaseModel):
    reason: str = ""


class DeploymentResponse(BaseModel):
    id: int
    strategy_id: str
    symbol: str
    exchange: str
    timeframe: str
    mode: str
    broker_name: Optional[str]
    status: str
    pause_reason: Optional[str]
    last_evaluated_at: Optional[str]
    last_signal_at: Optional[str]
    last_error: Optional[str]
    consecutive_failures: int
    open_positions: int
    created_by: Optional[int]
    created_at: str
    updated_at: str
    instrument_kind: str = "UNDERLYING"
    option_position: Optional[str] = None
    expiry_rule: Optional[str] = None
    strike_rule: Optional[str] = None
    strike_offset: int = 0
    premium_stop_pct: Optional[float] = None
    max_lots: Optional[int] = None
    strike_filters: Optional[dict] = None
    contract_rules: str = "underlying"

    @classmethod
    def from_record(cls, record: StrategyDeploymentRecord, open_positions: int = 0) -> "DeploymentResponse":
        iso = lambda value: value.isoformat() if value else None  # noqa: E731
        return cls(
            id=record.id, strategy_id=record.strategy_id, symbol=record.symbol, exchange=record.exchange,
            timeframe=record.timeframe, mode=record.mode, broker_name=record.broker_name, status=record.status,
            pause_reason=record.pause_reason, last_evaluated_at=iso(record.last_evaluated_at),
            last_signal_at=iso(record.last_signal_at), last_error=record.last_error,
            consecutive_failures=record.consecutive_failures or 0, open_positions=open_positions,
            created_by=record.created_by, created_at=iso(record.created_at) or "", updated_at=iso(record.updated_at) or "",
            instrument_kind=record.instrument_kind or "UNDERLYING", option_position=record.option_position,
            expiry_rule=record.expiry_rule, strike_rule=record.strike_rule, strike_offset=record.strike_offset or 0,
            premium_stop_pct=record.premium_stop_pct, max_lots=record.max_lots,
            strike_filters=json.loads(record.strike_filters) if record.strike_filters else None,
            contract_rules=describe_rules(ContractRules.from_deployment(record)),
        )


async def _open_position_counts(session: AsyncSession, tenant_id: int) -> dict:
    rows = await session.execute(
        select(TradeRecord.deployment_id, func.count()).where(
            TradeRecord.tenant_id == tenant_id, TradeRecord.exit_time.is_(None), TradeRecord.deployment_id.is_not(None),
        ).group_by(TradeRecord.deployment_id)
    )
    return {dep_id: count for dep_id, count in rows}


async def _get_owned_or_404(deployment_id: int, user: User, session: AsyncSession) -> StrategyDeploymentRecord:
    record = await session.get(StrategyDeploymentRecord, deployment_id)
    if record is None or record.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Unknown deployment")
    return record


async def _require_usable_broker(session: AsyncSession, tenant_id: int, broker_name: str) -> BrokerCredentialRecord:
    record = await get_credential_record(session, tenant_id, broker_name)
    if record is None:
        raise HTTPException(status_code=409, detail=f"No stored credentials for broker '{broker_name}' - add them in Settings first")
    if not token_is_usable(record):
        raise HTTPException(
            status_code=409,
            detail=f"{broker_name} session is {record.token_status} - log in to {broker_name} from Settings before going LIVE",
        )
    return record


@router.post("", response_model=DeploymentResponse, status_code=status.HTTP_201_CREATED)
async def create_deployment(
    request: DeploymentCreateRequest, user: User = Depends(can_manage), session: AsyncSession = Depends(get_session),
    session_id: Optional[int] = Depends(current_session_id),
) -> DeploymentResponse:
    if request.mode == ExecutionMode.LIVE:
        await ensure_live_step_up(session, user, session_id, "Creating a LIVE deployment")
    try:
        strategy = await resolve_strategy(request.strategy_id, user, session)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if request.timeframe not in SUPPORTED_BASE_TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"timeframe must be one of {list(SUPPORTED_BASE_TIMEFRAMES)}")
    base_minutes = _timeframe_minutes(request.timeframe)
    for tf in strategy.timeframes:
        tf_minutes = _timeframe_minutes(tf)
        if tf_minutes is None or base_minutes is None or tf_minutes < base_minutes or tf_minutes % base_minutes != 0:
            raise HTTPException(
                status_code=400,
                detail=f"Base timeframe {request.timeframe} cannot build the strategy's {tf} candles - "
                       f"pick a base that divides every strategy timeframe ({', '.join(strategy.timeframes)})",
            )

    rules = request.normalised()
    symbol = request.symbol.upper().strip()

    tenant = await load_tenant(session, user.tenant_id)
    await check_can_add_deployment(session, tenant, live=request.mode == ExecutionMode.LIVE)

    # Phase F2: an index has no cash leg - it is traded through its options or future - and a
    # derived-contract deployment needs the underlying's contracts in the master to resolve from.
    if rules.instrument_kind == InstrumentKind.UNDERLYING and instrument_master.underlying_of(symbol) in instrument_master.INDEX_SYMBOLS:
        raise HTTPException(status_code=400, detail=f"{symbol} is an index - trade it through an OPTION or FUTURE deployment")
    if rules.instrument_kind != InstrumentKind.UNDERLYING:
        underlying = instrument_master.underlying_of(symbol)
        listed = await instrument_master.expiries(
            session, underlying, instrument_type="FUT" if rules.instrument_kind == InstrumentKind.FUTURE else "CE",
        )
        if not listed:
            raise HTTPException(
                status_code=409,
                detail=f"No {underlying} {'futures' if rules.instrument_kind == InstrumentKind.FUTURE else 'options'} in the instrument master - "
                       f"sync it (Admin Console) or check the symbol",
            )

    broker_name = request.broker_name
    if broker_name is not None and broker_name not in available_brokers():
        raise HTTPException(status_code=404, detail=f"Unknown broker '{broker_name}'")

    if request.mode == ExecutionMode.LIVE:
        if not broker_name:
            raise HTTPException(status_code=400, detail="LIVE deployments must name the broker to trade through")
        await _require_usable_broker(session, user.tenant_id, broker_name)
    else:
        # PAPER still needs a market-data source. Fall back to the tenant's only stored broker.
        if broker_name is None:
            stored = list(await session.scalars(
                select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == user.tenant_id)
            ))
            if not stored:
                raise HTTPException(
                    status_code=409,
                    detail="Store broker credentials in Settings first - even paper trading needs a broker session for live candles",
                )
            if len(stored) > 1:
                raise HTTPException(status_code=400, detail="Several brokers stored - name the one to use for market data")
            broker_name = stored[0].broker_name
        elif await get_credential_record(session, user.tenant_id, broker_name) is None:
            raise HTTPException(status_code=409, detail=f"No stored credentials for broker '{broker_name}'")

    record = StrategyDeploymentRecord(
        tenant_id=user.tenant_id, strategy_id=request.strategy_id, symbol=symbol,
        exchange=request.exchange.upper(), timeframe=request.timeframe, mode=request.mode.value,
        broker_name=broker_name, status=DeploymentStatus.ACTIVE.value, created_by=user.id,
        instrument_kind=rules.instrument_kind.value,
        option_position=rules.option_position.value if rules.option_position else None,
        expiry_rule=rules.expiry_rule.value if rules.expiry_rule else None,
        strike_rule=rules.strike_rule.value if rules.strike_rule else None,
        strike_offset=rules.strike_offset, premium_stop_pct=rules.premium_stop_pct, max_lots=rules.max_lots,
        strike_filters=rules.filters_json(),
    )
    session.add(record)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail="A deployment of this strategy on this symbol in this mode (and instrument kind) already exists",
        ) from exc
    await write_audit_log(
        session, user.tenant_id, user.id, "deployment_created",
        f"#{record.id} {record.strategy_id} {record.symbol} {record.mode} via {broker_name} ({describe_rules(rules.to_rules())})",
    )
    await session.commit()
    await session.refresh(record)
    logger.info("Deployment %s created: %s %s %s", record.id, record.strategy_id, record.symbol, record.mode)
    return DeploymentResponse.from_record(record)


@router.post("/preview-contract")
async def preview_contract(
    request: ContractPreviewRequest, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> dict:
    """What the rules would trade right now, for both signal directions - the Autopilot form
    shows this before a deployment is created. Uses the supplied spot, else the tenant's broker
    LTP when a usable session exists; with neither, says so."""
    rules = request.normalised()
    if rules.instrument_kind == InstrumentKind.UNDERLYING:
        return {"symbol": request.symbol.upper().strip(), "kind": "UNDERLYING", "note": "Trades the symbol itself"}
    spot = request.spot
    spot_source = "supplied" if spot else None
    if spot is None and rules.instrument_kind == InstrumentKind.OPTION:
        spot = await _spot_from_broker(session, user.tenant_id, request.symbol)
        spot_source = "broker" if spot else None
    today = datetime.now(IST).date()
    out = {"symbol": request.symbol.upper().strip(), "kind": rules.instrument_kind.value, "rules": describe_rules(rules.to_rules()),
           "spot": spot, "spot_source": spot_source, "contracts": {}}
    chain_provider = None
    if rules.strike_filters is not None:
        adapter = await _usable_adapter(session, user.tenant_id)
        if adapter is not None:
            async def chain_provider(underlying_symbol: str, expiry):  # noqa: E306
                return await adapter.get_option_chain(underlying_symbol, expiry)
    for direction in (SignalDirection.LONG, SignalDirection.SHORT):
        try:
            resolved = await resolve_contract(session, request.symbol, rules.to_rules(), direction, spot=spot, today=today,
                                              chain_provider=chain_provider)
            out["contracts"][direction.value] = resolved.as_dict()
        except ContractResolutionError as exc:
            out["contracts"][direction.value] = {"error": str(exc)}
    return out


async def _usable_adapter(session: AsyncSession, tenant_id: int):
    stored = list(await session.scalars(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == tenant_id)))
    usable = [r for r in stored if token_is_usable(r)]
    return build_adapter(usable[0]) if usable else None


async def _spot_from_broker(session: AsyncSession, tenant_id: int, symbol: str) -> Optional[float]:
    stored = list(await session.scalars(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == tenant_id)))
    usable = [r for r in stored if token_is_usable(r)]
    if not usable:
        return None
    try:
        adapter = build_adapter(usable[0])
        underlying = instrument_master.underlying_of(symbol)
        candle_symbol = instrument_master.INDEX_SYMBOLS.get(underlying, symbol.upper().strip())
        exchange = instrument_master.INDEX_EXCHANGE.get(underlying, "NSE")
        return float(await adapter.get_ltp_for_symbol(candle_symbol, exchange))
    except Exception as exc:  # noqa: BLE001 - a preview must not 500 on a broker hiccup
        logger.warning("Spot lookup for %s failed: %s", symbol, exc)
        return None


@router.get("", response_model=List[DeploymentResponse])
async def list_deployments(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
    include_stopped: bool = False,
) -> List[DeploymentResponse]:
    query = select(StrategyDeploymentRecord).where(StrategyDeploymentRecord.tenant_id == user.tenant_id)
    if not include_stopped:
        query = query.where(StrategyDeploymentRecord.status != DeploymentStatus.STOPPED.value)
    rows = await session.scalars(query.order_by(StrategyDeploymentRecord.created_at.desc()))
    counts = await _open_position_counts(session, user.tenant_id)
    return [DeploymentResponse.from_record(r, counts.get(r.id, 0)) for r in rows]


@router.get("/{deployment_id}", response_model=DeploymentResponse)
async def get_deployment(
    deployment_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> DeploymentResponse:
    record = await _get_owned_or_404(deployment_id, user, session)
    counts = await _open_position_counts(session, user.tenant_id)
    return DeploymentResponse.from_record(record, counts.get(record.id, 0))


@router.post("/{deployment_id}/pause", response_model=DeploymentResponse)
async def pause_deployment(
    deployment_id: int, request: DeploymentActionRequest = DeploymentActionRequest(),
    user: User = Depends(can_manage), session: AsyncSession = Depends(get_session),
) -> DeploymentResponse:
    """No new entries; open positions opened by it stay monitored (the position sweep is
    tenant-wide, not deployment-scoped) and still exit at their stop/target."""
    record = await _get_owned_or_404(deployment_id, user, session)
    if record.status == DeploymentStatus.STOPPED.value:
        raise HTTPException(status_code=409, detail="Deployment is stopped")
    record.status = DeploymentStatus.PAUSED.value
    record.pause_reason = f"Paused by user: {request.reason}".rstrip(": ")
    await write_audit_log(session, user.tenant_id, user.id, "deployment_paused", f"#{record.id} {request.reason}")
    await session.commit()
    await session.refresh(record)
    return DeploymentResponse.from_record(record)


@router.post("/{deployment_id}/resume", response_model=DeploymentResponse)
async def resume_deployment(
    deployment_id: int, user: User = Depends(can_manage), session: AsyncSession = Depends(get_session),
    session_id: Optional[int] = Depends(current_session_id),
) -> DeploymentResponse:
    record = await _get_owned_or_404(deployment_id, user, session)
    if record.mode == ExecutionMode.LIVE.value:
        await ensure_live_step_up(session, user, session_id, "Resuming a LIVE deployment")
    if record.status == DeploymentStatus.STOPPED.value:
        raise HTTPException(status_code=409, detail="A stopped deployment cannot be resumed - create a new one")
    # A PAUSED row already counts against the plan, so only the LIVE entitlement is re-checked.
    await check_can_add_deployment(session, await load_tenant(session, user.tenant_id), live=record.mode == ExecutionMode.LIVE.value, adding=False)
    if record.mode == ExecutionMode.LIVE.value and record.broker_name:
        await _require_usable_broker(session, user.tenant_id, record.broker_name)
    record.status = DeploymentStatus.ACTIVE.value
    record.pause_reason = None
    record.consecutive_failures = 0
    record.last_error = None
    await write_audit_log(session, user.tenant_id, user.id, "deployment_resumed", f"#{record.id}")
    await session.commit()
    await session.refresh(record)
    return DeploymentResponse.from_record(record)


@router.post("/{deployment_id}/stop", response_model=DeploymentResponse)
async def stop_deployment(
    deployment_id: int, request: DeploymentActionRequest = DeploymentActionRequest(),
    user: User = Depends(can_manage), session: AsyncSession = Depends(get_session),
) -> DeploymentResponse:
    """Terminal. Positions it opened are left to the monitor (or emergency exit) - stopping a
    deployment is "stop taking new trades", not "dump everything at market"."""
    record = await _get_owned_or_404(deployment_id, user, session)
    record.status = DeploymentStatus.STOPPED.value
    record.pause_reason = f"Stopped by user: {request.reason}".rstrip(": ")
    await write_audit_log(session, user.tenant_id, user.id, "deployment_stopped", f"#{record.id} {request.reason}")
    await session.commit()
    await session.refresh(record)
    return DeploymentResponse.from_record(record)


@router.delete("/{deployment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_deployment(
    deployment_id: int, user: User = Depends(can_manage), session: AsyncSession = Depends(get_session),
) -> None:
    record = await _get_owned_or_404(deployment_id, user, session)
    if record.status != DeploymentStatus.STOPPED.value:
        raise HTTPException(status_code=409, detail="Stop the deployment before deleting it")
    await session.delete(record)
    await write_audit_log(session, user.tenant_id, user.id, "deployment_deleted", f"#{deployment_id}")
    await session.commit()
