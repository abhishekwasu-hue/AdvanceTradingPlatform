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
from datetime import date, datetime
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.routing import RoutingPolicy
from app.accounts.service import get_account
from app.ai.regime import parse_filter, validate_filter
from app.audit.log import write_audit_log
from app.auth.dependencies import current_session_id, ensure_live_step_up, get_current_user, require_trader
from app.brokers.gates import data_sources
from app.brokers.registry import available_brokers
from app.brokers.token_lifecycle import build_adapter, get_credential_record, token_is_usable
from app.core.enums import DeploymentStatus, ExecutionMode, ExpiryRule, InstrumentKind, OptionPosition, OptionStrategy, SignalDirection, StrikeRule
from app.custom_strategies.resolver import TEMPLATE_PREFIX, normalize_strategy_id, resolve_strategy
from app.db.models import BrokerCredentialRecord, StrategyDeploymentRecord, TradeRecord, User
from app.db.session import get_session
from app.execution.contract_execution import contract_ltp
from app.instruments import master as instrument_master
from app.instruments.contracts import (
    ContractResolutionError,
    ContractRules,
    describe_rules,
    resolve_contract,
)
from app.instruments.spreads import (
    MAX_CUSTOM_LEGS,
    MAX_LEG_RATIO,
    CustomLeg,
    can_hold_for_days,
    custom_legs_json,
    describe_structure,
    is_debit,
    parse_custom_legs,
    resolve_structure,
    structure_metrics,
)
from app.instruments.strike_selection import StrikeFilters
from app.market_data.calendar import IST
from app.plans.limits import check_can_add_deployment, load_tenant
from app.strategy_templates import exits as exit_settings
from app.strategy_templates import packs
from app.strategy_templates.exits import TemplateExits, TemplateExitsBody
from app.trading.exit_rules import ExitRules

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


class ExitRulesRequest(BaseModel):
    """Phase J1: dynamic exits applied by the position monitor (and the backtest engine). M68 PR-3 (TS1-034): no ATP
    ceiling - a real number of the right sign; a trail under 100 % (at 100 % a stop would sit at zero)."""
    trailing_stop_pct: float | None = Field(default=None, gt=0, lt=100, allow_inf_nan=False)
    break_even_at_r: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    time_exit_minutes: int | None = Field(default=None, ge=1)
    time_exit_at: Optional[str] = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")

    def to_rules(self) -> ExitRules:
        return ExitRules(**self.model_dump())


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
    # Phase H2: multi-leg structures. SINGLE keeps the Phase F single leg.
    option_strategy: OptionStrategy = OptionStrategy.SINGLE
    spread_width: int = Field(default=2, ge=1, le=20, description="wing distance in listed strike steps")
    target_credit_pct: Optional[float] = Field(default=None, ge=5, le=95, description="take profit at this % of the credit captured")
    stop_credit_pct: Optional[float] = Field(default=None, ge=10, le=500, description="stop when the loss reaches this % of the credit")
    underlying_breach_exit: Optional[bool] = Field(default=None, description="DEF-0: close the structure when the underlying breaches a short strike / breakeven (off unless set)")
    # Phase U: the legs of a CUSTOM structure (2..6), each on the deployment's expiry rule.
    custom_legs: Optional[List["CustomLegRequest"]] = Field(default=None, max_length=MAX_CUSTOM_LEGS)

    def custom_leg_specs(self) -> List[CustomLeg]:
        return [leg.to_spec() for leg in (self.custom_legs or [])]

    def normalised(self) -> "ContractRulesRequest":
        """Fills the defaults the kind implies and rejects rules that make no sense for it."""
        data = self.model_dump()
        if self.instrument_kind == InstrumentKind.UNDERLYING:
            if any(data[k] for k in ("option_position", "expiry_rule", "strike_rule", "premium_stop_pct")) or self.strike_offset or self.strike_filters:
                raise HTTPException(status_code=400, detail="Option/future rules only apply when instrument_kind is OPTION or FUTURE")
            return self
        if self.strike_filters is not None and self.instrument_kind != InstrumentKind.OPTION:
            raise HTTPException(status_code=400, detail="Strike filters only apply to OPTION deployments")
        if self.option_strategy != OptionStrategy.SINGLE and self.instrument_kind != InstrumentKind.OPTION:
            raise HTTPException(status_code=400, detail="Multi-leg structures only apply to OPTION deployments")
        if self.option_strategy == OptionStrategy.SINGLE and (self.target_credit_pct is not None or self.stop_credit_pct is not None):
            raise HTTPException(status_code=400, detail="target/stop credit % only apply to multi-leg structures")
        if self.option_strategy != OptionStrategy.SINGLE and self.premium_stop_pct is not None:
            raise HTTPException(status_code=400, detail="A structure is exited on its net credit/debit (target/stop %), not a premium stop")
        if is_debit(self.option_strategy) and self.stop_credit_pct is not None and self.stop_credit_pct > 100:
            raise HTTPException(status_code=400, detail="A debit structure cannot lose more than its debit: stop % must be at most 100")
        if self.option_strategy == OptionStrategy.CUSTOM:
            try:
                specs = self.custom_leg_specs()
                parse_custom_legs([leg.as_dict() for leg in specs])
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"custom_legs: {exc}")
            if self.strike_filters is not None:
                raise HTTPException(status_code=400, detail="Strike filters do not apply to a CUSTOM structure: its legs name their strikes")
        elif self.custom_legs:
            raise HTTPException(status_code=400, detail="custom_legs only apply when option_strategy is CUSTOM")
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
        position = self.option_position or (OptionPosition.WRITE if self.option_strategy != OptionStrategy.SINGLE else OptionPosition.BUY)
        if self.option_strategy == OptionStrategy.CUSTOM:
            position = OptionPosition.WRITE if any(leg.role == "SHORT" for leg in self.custom_legs or []) else OptionPosition.BUY
        elif self.option_strategy != OptionStrategy.SINGLE:
            # Credit structures sell their primary legs (wings bought); debit structures buy theirs.
            position = OptionPosition.BUY if is_debit(self.option_strategy) else OptionPosition.WRITE
        data["option_position"] = position
        data["strike_rule"] = self.strike_rule or StrikeRule.ATM
        if data["strike_rule"] == StrikeRule.ATM and self.strike_offset:
            raise HTTPException(status_code=400, detail="strike_offset only applies to ITM/OTM strikes")
        if data["strike_rule"] != StrikeRule.ATM and not self.strike_offset:
            data["strike_offset"] = 1
        # DEF-0 D3: no built-in premium stop - blank = no such exit (the trader sets his own)
        return ContractRulesRequest(**data)

    def structure_description(self) -> str:
        return describe_structure(self.option_strategy, self.spread_width, self.to_rules(), self.custom_leg_specs())

    def to_rules(self) -> ContractRules:
        return ContractRules(
            kind=self.instrument_kind, position=self.option_position, expiry_rule=self.expiry_rule or ExpiryRule.NEAREST,
            strike_rule=self.strike_rule or StrikeRule.ATM, strike_offset=self.strike_offset,
            premium_stop_pct=self.premium_stop_pct, max_lots=self.max_lots,
            strike_filters=self.strike_filters.to_filters() if self.strike_filters else StrikeFilters(),
        )

    def filters_json(self) -> Optional[str]:
        return self.strike_filters.to_filters().to_json() if self.strike_filters else None


class CustomLegRequest(BaseModel):
    right: str = Field(pattern="^(CE|PE|ce|pe)$")
    role: str = Field(pattern="^(SHORT|LONG|short|long)$")
    strike_rule: StrikeRule = StrikeRule.ATM
    strike_offset: int = Field(default=0, ge=0, le=20)
    ratio: int = Field(default=1, ge=1, le=MAX_LEG_RATIO)

    def to_spec(self) -> CustomLeg:
        return CustomLeg.from_dict(self.model_dump())


ContractRulesRequest.model_rebuild()


class DeploymentCreateRequest(ContractRulesRequest):
    strategy_id: str = Field(min_length=1, max_length=100)
    symbol: str = Field(min_length=1, max_length=50)
    exchange: str = Field(default="NSE", min_length=1, max_length=20)
    timeframe: str = Field(default="1min", description="Base candle interval fetched from the broker")
    mode: ExecutionMode = ExecutionMode.PAPER
    broker_name: Optional[str] = None
    # Phase I2: route LIVE orders to one broker account (None = the broker's default account).
    broker_account_id: Optional[int] = None
    # Phase T: choose the account at signal time by rule (None = the organisation's default policy).
    routing_policy: Optional[RoutingPolicy] = None
    route_across_brokers: bool = False
    # Phase J1: dynamic exits.
    exit_rules: Optional[ExitRulesRequest] = None
    regime_filter: Optional[List[str]] = Field(default=None, max_length=5, description="Phase L3: regimes noted for the admin diagnostic (handover 5 C: never blocks an entry)")
    # OI Banner O5: opt-in OI entry gates (OI_DIFF, OI_CONFIRM, PCR, IV_CHANGE, SWING_OI, OI_WALL); fail-closed; exits never gated.
    oi_gates: Optional[List[str]] = Field(default=None, max_length=6)
    # Phase AS: SWING positions are held overnight (CNC / NRML) and never squared off at the close.
    # M68 PR-1 (TS1-006 / 010 / 011): no pre-chosen holding, order type or band. Not sent = the user's own Trade settings
    # (Order defaults), stored on the bot as his choice; Not set there either = the bot keeps it Not set and places no
    # entry until he sets one ("Needs your setting: ..." on the bot card). P0.5 / T5: MARKET or PROTECTED_LIMIT - a
    # marketable limit `market_protection_pct` past the signal price.
    holding: Literal["INTRADAY", "SWING"] | None = None
    order_style: Literal["MARKET", "PROTECTED_LIMIT"] | None = None
    market_protection_pct: Optional[float] = Field(default=None, ge=0.05, le=5.0)
    # Z3 / BT2: APPROVAL - every entry signal becomes a proposal and only an approved one places a PAPER order. Needs the
    # `approval_only_entries` flag; with the flag on every deployment is approval-only whatever this says.
    entry_mode: Literal["AUTO", "APPROVAL"] = "AUTO"
    # Strategy templates section 5: a template's exit choice (where the stop comes from, when to take profit); omitted =
    # as before (the template's own level as the stop, no target; a spread's credit levels).
    template_exits: Optional[TemplateExitsBody] = None


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
    option_strategy: str = "SINGLE"
    spread_width: int = 2
    target_credit_pct: Optional[float] = None
    stop_credit_pct: Optional[float] = None
    underlying_breach_exit: bool = False
    custom_legs: Optional[List[dict]] = None
    broker_account_id: Optional[int] = None
    routing_policy: Optional[str] = None
    route_across_brokers: bool = False
    last_route: Optional[str] = None
    wait_reason: Optional[str] = None      # M55 S7: why a running bot waits, in plain words ("Waiting: market closed")
    exit_rules: Optional[dict] = None
    regime_filter: Optional[List[str]] = None
    oi_gates: Optional[List[str]] = None
    contract_rules: str = "underlying"
    holding: str | None = None            # M68 PR-1: None = Not set (the bot follows his Trade settings)
    order_style: str | None = None
    market_protection_pct: Optional[float] = None
    entry_mode: str = "AUTO"
    template_exits: Optional[dict] = None

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
            option_strategy=record.option_strategy or "SINGLE", spread_width=record.spread_width or 2,
            target_credit_pct=record.target_credit_pct, stop_credit_pct=record.stop_credit_pct,
            underlying_breach_exit=bool(getattr(record, "underlying_breach_exit", None)),
            custom_legs=json.loads(record.custom_legs) if getattr(record, "custom_legs", None) else None,
            broker_account_id=record.broker_account_id, exit_rules=json.loads(record.exit_rules) if record.exit_rules else None,
            routing_policy=getattr(record, "routing_policy", None), route_across_brokers=bool(getattr(record, "route_across_brokers", False)),
            last_route=getattr(record, "last_route", None), wait_reason=getattr(record, "wait_reason", None),
            regime_filter=parse_filter(record.regime_filter) or None, contract_rules=describe_deployment(record),
            oi_gates=[g for g in (getattr(record, "oi_gates", None) or "").split(",") if g] or None,
            holding=getattr(record, "holding", None),
            order_style=getattr(record, "order_style", None), market_protection_pct=getattr(record, "market_protection_pct", None),
            entry_mode=getattr(record, "entry_mode", None) or "AUTO",
            template_exits=_exits_answer(record),
        )


def _exits_answer(record: StrategyDeploymentRecord) -> Optional[dict]:
    """Section 5: the deployment's exit choice as stored (None: none chosen - the behaviour before)."""
    choice = TemplateExits.from_record(record)
    return None if choice.is_default else {k: v for k, v in choice.columns().items() if v is not None}


def _holds_spread(request: "DeploymentCreateRequest") -> bool:
    """M55 T1: "Hold for days" on a defined-risk credit structure (bull put, bear call, iron condor, iron butterfly)."""
    return (request.holding == "SWING" and request.instrument_kind == InstrumentKind.OPTION
            and can_hold_for_days(request.option_strategy))


async def _own_order_defaults(session: AsyncSession, user: User, request: "DeploymentCreateRequest") -> None:
    """M68 PR-1: a field the form did not send takes the user's OWN Trade settings value (never an ATP one); a field Not
    set there either stays None on the bot. PROTECTED_LIMIT with no band of his own is asked for (422 at the field)."""
    from app.execution import order_defaults
    mine = await order_defaults.load(session, user)
    request.holding = request.holding or mine.holding                                 # type: ignore[assignment]
    request.order_style = request.order_style or mine.bot_order_style                 # type: ignore[assignment]
    if request.order_style == "PROTECTED_LIMIT" and request.market_protection_pct is None:
        request.market_protection_pct = mine.price_protection_pct
        if request.market_protection_pct is None:
            raise HTTPException(status_code=422, detail=[{"loc": ["body", "market_protection_pct"], "type": "needs_setting",
                                                          "msg": "Enter the price protection % for a limit with price protection"}])


def _check_holding(request: "DeploymentCreateRequest", strategy) -> None:
    """Phase AS: what a swing (overnight) deployment may and may not be."""
    daily = list(getattr(strategy, "timeframes", [])) == ["day"]
    if _holds_spread(request):
        # M55 T1: a defined-risk credit structure held for days - entries on the user's own timeframe (intraday or daily).
        if daily and request.timeframe != "day":
            raise HTTPException(status_code=400, detail=f"{strategy.name} trades daily candles: set timeframe to 'day'")
        if not daily and request.timeframe == "day":
            raise HTTPException(status_code=400, detail=f"{strategy.name} reads intraday candles: pick an intraday timeframe")
        return
    if request.holding == "SWING":
        if request.option_strategy != OptionStrategy.SINGLE:
            raise HTTPException(status_code=400, detail="Swing (overnight) holding is for single positions; multi-leg structures stay intraday")
        if request.option_position == OptionPosition.WRITE:
            raise HTTPException(status_code=400, detail="Overnight option writing is not offered: swing options are bought, never written")
        if request.timeframe != "day":
            raise HTTPException(status_code=400, detail="A swing deployment reads daily candles: set timeframe to 'day'")
    elif daily:
        raise HTTPException(status_code=400, detail=f"{strategy.name} trades daily candles and holds overnight: deploy it with holding 'SWING'")


def describe_deployment(record: StrategyDeploymentRecord) -> str:
    rules = ContractRules.from_deployment(record)
    strategy = OptionStrategy(record.option_strategy or "SINGLE")
    if rules.kind == InstrumentKind.OPTION and strategy != OptionStrategy.SINGLE:
        try:
            legs = parse_custom_legs(getattr(record, "custom_legs", None))
        except ValueError:
            legs = []
        text = describe_structure(strategy, record.spread_width or 2, rules, legs)
        if rules.strike_filters.active:
            text += f", filters: {rules.strike_filters.describe()}"
    else:
        text = describe_rules(rules)
    exits = ExitRules.from_json(getattr(record, "exit_rules", None))
    if exits.active:
        text += f"; exits: {exits.describe()}"
    template_exits = TemplateExits.from_record(record)
    if not template_exits.is_default and template_exits.describe():
        text += f"; {template_exits.describe()}"
    regimes = parse_filter(getattr(record, "regime_filter", None))
    if regimes:
        text += f"; regimes noted (diagnostic only, never blocks): {', '.join(r.lower().replace('_', ' ') for r in regimes)}"
    return text


def _oi_gates(request) -> List[str]:
    """OI Banner O5: the requested OI gates, validated (an unknown name is a 422, never silently dropped)."""
    from app.option_chain.oi_gates import parse_gates
    try:
        return parse_gates(",".join(getattr(request, "oi_gates", None) or []))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _regime_filter(request) -> List[str]:
    values = getattr(request, "regime_filter", None) or []
    try:
        return validate_filter([v.upper() for v in values])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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


# REV-H1: every 409 on starting (or resuming) a bot names its own reason - a machine code the page maps to one plain
# sentence with the next step, plus the server's own plain sentence in `message` (shown as-is by any other client).
REFUSED_NO_INSTRUMENT_MASTER = "no_instrument_master"
REFUSED_NO_BROKER = "no_broker"
REFUSED_BROKER_LOGIN = "broker_login_needed"
REFUSED_BROKER_ACCOUNT_OFF = "broker_account_off"
REFUSED_DUPLICATE_ACTIVE_BOT = "duplicate_active_bot"


def _refused(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": code, "message": message})


def _duplicate_refusal() -> HTTPException:
    return _refused(REFUSED_DUPLICATE_ACTIVE_BOT,
                    "This strategy already runs for this symbol in your organisation. Stop that bot first, or pick another strategy or symbol.")


async def _active_duplicate(session: AsyncSession, tenant_id: int, strategy_id: str, symbol: str, mode: str, instrument_kind: str) -> bool:
    """REV-H2: a bot that is not STOPPED on the same strategy + symbol + mode + instrument kind (the partial unique index
    uq_deployment_open_tenant_strategy_symbol_mode_kind is the backstop). A stopped bot is history and blocks nothing."""
    found = await session.scalar(select(StrategyDeploymentRecord.id).where(
        StrategyDeploymentRecord.tenant_id == tenant_id, StrategyDeploymentRecord.strategy_id == strategy_id,
        StrategyDeploymentRecord.symbol == symbol, StrategyDeploymentRecord.mode == mode,
        StrategyDeploymentRecord.instrument_kind == instrument_kind,
        StrategyDeploymentRecord.status != DeploymentStatus.STOPPED.value,
    ).limit(1))
    return found is not None


async def _require_usable_broker(session: AsyncSession, tenant_id: int, broker_name: str, account_label: str = "primary") -> BrokerCredentialRecord:
    record = await get_credential_record(session, tenant_id, broker_name, account_label)
    if record is None:
        raise _refused(REFUSED_NO_BROKER, f"{broker_name} is not connected yet. Connect it on the Broker page first, then try again.")
    if not token_is_usable(record):
        raise _refused(REFUSED_BROKER_LOGIN,
                       f"Your {broker_name} login is not active ({str(record.token_status).lower()}). "
                       f"Log in to {broker_name} again on the Broker page, then try again.")
    return record


async def _live_gates(session: AsyncSession, user: User, why: str) -> None:
    """Phase N: LIVE needs the `trading:live` scope, the `live_trading` feature flag and (when the
    operator requires it) a verified email. Exits and PAPER are untouched."""
    from app.auth.scopes import has_scope, scope_denied
    from app.auth.verification import ensure_verified
    from app.platform.controls import require_flag
    if not has_scope(user, "trading:live"):
        raise scope_denied(user, "trading:live")
    await require_flag(session, "live_trading", user.tenant_id)
    ensure_verified(user, why)


@router.post("", response_model=DeploymentResponse, status_code=status.HTTP_201_CREATED)
async def create_deployment(
    request: DeploymentCreateRequest, user: User = Depends(can_manage), session: AsyncSession = Depends(get_session),
    session_id: Optional[int] = Depends(current_session_id),
) -> DeploymentResponse:
    if request.mode == ExecutionMode.LIVE:
        await _live_gates(session, user, "Creating a LIVE deployment")
        await ensure_live_step_up(session, user, session_id, "Creating a LIVE deployment")
    try:
        request.strategy_id = normalize_strategy_id(request.strategy_id)   # P0.8 / A4: legacy `custom_<id>` -> `custom:<id>`
        strategy = await resolve_strategy(request.strategy_id, user, session)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    sent_holding = request.holding
    await _own_order_defaults(session, user, request)
    try:
        _check_holding(request, strategy)
    except HTTPException:
        if sent_holding is not None or request.holding is None:
            raise
        # M68 PR-1: his saved holding does not fit this strategy (e.g. Carry forward on intraday candles): the bot keeps
        # its holding Not set (it says so on its card) rather than refusing a form that did not send one.
        request.holding = None
        _check_holding(request, strategy)
    # REV-H3: a seller pack on an option needs the user's own construction (no server-side buy-at-the-money pick)
    pack_problem = packs.contract_problem(request.strategy_id, request.instrument_kind, request.option_strategy, request.option_position)
    if pack_problem:
        raise HTTPException(status_code=400, detail=pack_problem)
    if request.strategy_id.startswith(TEMPLATE_PREFIX) and request.mode != ExecutionMode.PAPER:
        # Strategy templates step 2: signals only, PAPER only (owner order section 10) - a template never runs LIVE here.
        raise HTTPException(status_code=400, detail="Strategy templates run on paper only for now")
    # Section 5: the template's exit choice, checked against the instrument it will trade (plain messages)
    try:
        template_exits = exit_settings.checked(strategy, request.template_exits, request.instrument_kind, request.option_strategy)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"problems": exc.args[0]}) from exc
    if request.entry_mode == "APPROVAL":
        from app.execution import approval
        if not await approval.flag_on(session, user.tenant_id):
            raise HTTPException(status_code=400, detail="Approval-only entries are not enabled on this platform (flag approval_only_entries)")

    # Phase AS: a swing deployment reads daily candles (checked by _check_holding); the intraday
    # base-timeframe rules below are for everything else.
    # M55 T1: a spread held for days on intraday candles keeps these rules (only its square-off differs).
    reads_daily = request.holding == "SWING" and request.timeframe == "day"
    if not reads_daily and request.timeframe not in SUPPORTED_BASE_TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"timeframe must be one of {list(SUPPORTED_BASE_TIMEFRAMES)}")
    base_minutes = _timeframe_minutes(request.timeframe)
    for tf in ([] if reads_daily else strategy.timeframes):
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
            raise _refused(
                REFUSED_NO_INSTRUMENT_MASTER,
                f"The {underlying} {'futures' if rules.instrument_kind == InstrumentKind.FUTURE else 'options'} are not loaded yet, so the bot "
                f"cannot pick a contract. Ask your admin to sync the instrument list, or check the symbol.",
            )

    broker_name = request.broker_name
    if broker_name is not None and broker_name not in available_brokers():
        raise HTTPException(status_code=404, detail=f"Unknown broker '{broker_name}'")

    account = None
    if request.broker_account_id is not None:
        account = await get_account(session, user.tenant_id, request.broker_account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="No such broker account")
        if broker_name and account.broker_name != broker_name:
            raise HTTPException(status_code=400, detail=f"That account belongs to {account.broker_name}; pick a {broker_name} account")   # UI-28 U3.1: no ids on screen
        broker_name = account.broker_name
    if request.route_across_brokers and request.mode != ExecutionMode.LIVE:
        raise HTTPException(status_code=400, detail="route_across_brokers only applies to LIVE deployments")
    if request.routing_policy not in (None, RoutingPolicy.EXPLICIT) and request.mode != ExecutionMode.LIVE:
        raise HTTPException(status_code=400, detail="A routing policy only applies to LIVE deployments (paper trades need no account)")
    if request.mode == ExecutionMode.LIVE:
        if not broker_name:
            raise HTTPException(status_code=400, detail="LIVE deployments must name the broker to trade through")
        if account is not None and account.status != "ACTIVE":
            raise _refused(REFUSED_BROKER_ACCOUNT_OFF,
                           f"This {account.broker_name} account is switched off ({str(account.status).lower()}). Switch it on on the Broker page, then try again.")
        await _require_usable_broker(session, user.tenant_id, broker_name, account.account_label if account is not None else "primary")
    else:
        # PAPER still needs a market-data source. Fall back to the tenant's only stored broker.
        if broker_name is None:
            stored = list(await session.scalars(
                select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == user.tenant_id)
            ))
            if not stored:
                raise _refused(REFUSED_NO_BROKER,
                               "Connect your broker on the Broker page first: even a paper bot reads live prices through your broker.")
            if len(stored) > 1:
                raise HTTPException(status_code=400, detail="Several brokers stored - name the one to use for market data")
            broker_name = stored[0].broker_name
        elif await get_credential_record(session, user.tenant_id, broker_name) is None:
            raise _refused(REFUSED_NO_BROKER, f"{broker_name} is not connected yet. Connect it on the Broker page first, then try again.")

    # A bad request is a 422 before it is a duplicate (409): the last input checks run first.
    regime_filter, oi_gates = _regime_filter(request), _oi_gates(request)
    if await _active_duplicate(session, user.tenant_id, request.strategy_id, symbol, request.mode.value, rules.instrument_kind.value):
        raise _duplicate_refusal()

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
        option_strategy=rules.option_strategy.value, spread_width=rules.spread_width,
        target_credit_pct=rules.target_credit_pct, stop_credit_pct=rules.stop_credit_pct,
        underlying_breach_exit=True if rules.underlying_breach_exit else None,
        custom_legs=custom_legs_json(rules.custom_leg_specs()) if rules.option_strategy == OptionStrategy.CUSTOM else None,
        broker_account_id=account.id if account is not None else None,
        routing_policy=request.routing_policy.value if request.routing_policy is not None else None,
        route_across_brokers=bool(request.route_across_brokers),
        exit_rules=request.exit_rules.to_rules().to_json() if request.exit_rules is not None else None,
        regime_filter=",".join(regime_filter) or None,
        oi_gates=",".join(oi_gates) or None,
        holding=request.holding,
        order_style=request.order_style, market_protection_pct=request.market_protection_pct,
        entry_mode=request.entry_mode,
        **template_exits.columns(),
    )
    session.add(record)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise _duplicate_refusal() from exc      # a race past the check above: the partial unique index is the backstop
    await write_audit_log(
        session, user.tenant_id, user.id, "deployment_created",
        f"#{record.id} {record.strategy_id} {record.symbol} {record.mode} via {broker_name} ({describe_deployment(record)})",
    )
    await session.commit()
    await session.refresh(record)
    logger.info("Deployment %s created: %s %s %s", record.id, record.strategy_id, record.symbol, record.mode)
    return DeploymentResponse.from_record(record)


@router.post("/preview-contract")
async def preview_contract(
    request: ContractPreviewRequest, as_of: Optional[date] = Query(default=None, description="preview as of this trading date (default: today, IST)"),
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
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
        spot = await _spot_from_broker(session, user.tenant_id, request.symbol, user=user)
        spot_source = "broker" if spot else None
    today = as_of or datetime.now(IST).date()
    out = {"symbol": request.symbol.upper().strip(), "kind": rules.instrument_kind.value, "rules": describe_rules(rules.to_rules()), "as_of": today.isoformat(),
           "spot": spot, "spot_source": spot_source, "contracts": {}}
    chain_provider = None
    adapter = None
    if rules.strike_filters is not None or rules.option_strategy != OptionStrategy.SINGLE:
        adapter = await _usable_adapter(session, user.tenant_id, user=user, symbol=request.symbol)
        if adapter is not None:
            async def chain_provider(underlying_symbol: str, expiry):  # noqa: E306
                return await adapter.get_option_chain(underlying_symbol, expiry)
    if rules.instrument_kind == InstrumentKind.OPTION and rules.option_strategy != OptionStrategy.SINGLE:
        out["rules"] = rules.structure_description()
        out["structures"] = {}
        for direction in (SignalDirection.LONG, SignalDirection.SHORT):
            try:
                structure = await resolve_structure(
                    session, request.symbol, rules.to_rules(), rules.option_strategy, direction,
                    spread_width=rules.spread_width, spot=spot, today=today, chain_provider=chain_provider,
                    custom_legs=rules.custom_leg_specs(),
                )
            except ContractResolutionError as exc:
                out["structures"][direction.value] = {"error": str(exc)}
                continue
            entry = structure.as_dict()
            refusal = await _legs_refusal(session, user, [leg.contract.tradingsymbol for leg in structure.legs]) if adapter is not None else None
            if refusal is not None:
                entry["metrics_error"] = refusal
            elif adapter is not None:
                try:
                    premiums = {leg.contract.tradingsymbol: await contract_ltp(adapter, leg.contract) for leg in structure.legs}
                    entry["metrics"] = structure_metrics(structure, premiums, target_credit_pct=rules.target_credit_pct,
                                                         stop_credit_pct=rules.stop_credit_pct,
                                                         underlying_exit=bool(rules.underlying_breach_exit)).as_dict()
                except Exception as exc:  # noqa: BLE001 - premiums are a bonus in a preview
                    entry["metrics_error"] = str(exc)
            else:
                entry["metrics_error"] = "No usable broker session to quote the legs - log in on the Broker page to see credit, max loss and breakeven"
            out["structures"][direction.value] = entry
        return out
    for direction in (SignalDirection.LONG, SignalDirection.SHORT):
        try:
            resolved = await resolve_contract(session, request.symbol, rules.to_rules(), direction, spot=spot, today=today,
                                              chain_provider=chain_provider)
            out["contracts"][direction.value] = resolved.as_dict()
        except ContractResolutionError as exc:
            out["contracts"][direction.value] = {"error": str(exc)}
    return out


async def _legs_refusal(session: AsyncSession, user: User, symbols: List[str]) -> Optional[str]:
    """BT1b (flag `byob_data_tier`): why the legs may not be quoted through the user's own session (None = they may)."""
    from app.market_data import live_access
    if not await live_access.byob_on(session, user.tenant_id):
        return None
    try:
        own = await live_access.user_source(session, user, [(s, "NFO") for s in symbols])
    except live_access.LiveDataRefused as exc:
        return str(exc.detail)
    blocked = [s for s in symbols if not own.allows(s, "NFO")]
    return own.reason(blocked[0], "NFO") if blocked else None


async def _own_adapter(session: AsyncSession, user: Optional[User], symbol: str, exchange: str):
    """BT1b (flag `byob_data_tier`): the user's own confirmed session for this symbol, or None (preview without quotes)."""
    from app.market_data import live_access
    try:
        own = await live_access.user_source(session, user, [(symbol, exchange)])
    except live_access.LiveDataRefused:
        return None
    return own.adapter() if own.allows(symbol, exchange) else None


def _reader_records(tenant_id: int, user: Optional[User]):
    """M71Q-2: a preview's price read uses only credentials the previewing user linked himself (no user: as before)."""
    query = select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == tenant_id)
    if user is not None:
        query = query.where(BrokerCredentialRecord.user_id == user.id)
    return query


async def _usable_adapter(session: AsyncSession, tenant_id: int, user: Optional[User] = None, symbol: Optional[str] = None):
    from app.market_data import live_access
    if await live_access.byob_on(session, tenant_id):
        underlying = instrument_master.underlying_of(symbol or "")
        return await _own_adapter(session, user, underlying or (symbol or ""), "NFO")
    stored = list(await session.scalars(_reader_records(tenant_id, user)))   # M71Q-2: the previewing user's own
    usable = [r for r in data_sources(stored) if token_is_usable(r)]    # Part 1B: no gated price source
    return build_adapter(usable[0]) if usable else None


async def _spot_from_broker(session: AsyncSession, tenant_id: int, symbol: str, user: Optional[User] = None) -> Optional[float]:
    from app.market_data import live_access
    try:
        underlying = instrument_master.underlying_of(symbol)
        candle_symbol = instrument_master.INDEX_SYMBOLS.get(underlying, symbol.upper().strip())
        exchange = instrument_master.INDEX_EXCHANGE.get(underlying, "NSE")
    except Exception as exc:  # noqa: BLE001 - a preview must not 500 on an odd symbol
        logger.warning("Spot lookup for %s failed: %s", symbol, exc)
        return None
    if await live_access.byob_on(session, tenant_id):
        adapter = await _own_adapter(session, user, candle_symbol, exchange)
        if adapter is None:
            return None
    else:
        stored = list(await session.scalars(_reader_records(tenant_id, user)))   # M71Q-2: the previewing user's own
        usable = [r for r in data_sources(stored) if token_is_usable(r)]    # Part 1B: no gated price source
        if not usable:
            return None
        adapter = None
    try:
        if adapter is None:
            adapter = build_adapter(usable[0])
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


@router.get("/{deployment_id}/why")
async def why_no_trade(
    deployment_id: int, limit: int = Query(20, ge=1, le=50),
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> dict:
    """AUD-T3: the bot's last closed bars with the reason it did or did not trade (newest first). Behind `why_no_trade`
    and `ui_v2` (404 while off); the organisation's own deployments only."""
    from app.deployments import why
    from app.platform.controls import flag_enabled
    for name in ("ui_v2", "why_no_trade"):
        if not await flag_enabled(session, name, user.tenant_id):
            raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": name})
    record = await _get_owned_or_404(deployment_id, user, session)
    return {"deployment_id": record.id, "rows": await why.recent(session, record.id, limit)}


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
        await _live_gates(session, user, "Resuming a LIVE deployment")
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
    record.data_failures = 0                # M55 S7: a fresh start for the data-failure streak too
    record.waiting_for_data_since = None
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
