"""Phase F2: from "the strategy says LONG on NIFTY 50" to "buy NIFTY 24500 CE 08 OCT 26".

A deployment carries contract *rules* (app/db/models.py::StrategyDeploymentRecord), not a
contract: the contract is chosen at signal time from the instrument master and the current spot,
so a deployment created on Monday trades Thursday's ATM strike on Thursday. This module is pure
selection logic plus one async lookup; it never places orders.

Rules
* option right: BUY position - LONG buys CE, SHORT buys PE; WRITE position - LONG sells PE,
  SHORT sells CE (the written option's trade direction is SHORT for P&L purposes).
* expiry: NEAREST = first expiry on/after today; NEXT = the one after; MONTHLY = the last expiry
  in the nearest month that has one (the exchange's monthly contract).
* strike: ATM = nearest listed strike to spot; ITM/OTM = `offset` listed steps in/out of the
  money for that right (CE in-the-money is below spot, PE in-the-money is above).
"""
from dataclasses import dataclass, field
from datetime import date
from typing import Awaitable, Callable, List, Optional, Sequence, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ExpiryRule, InstrumentKind, OptionPosition, OrderSide, SignalDirection, StrikeRule
from app.db.models import InstrumentRecord, StrategyDeploymentRecord
from app.brokers.models import OptionChain
from app.instruments import master
from app.instruments.strike_selection import SelectionResult, StrikeFilters, StrikeSelectionError, select_strike_with_chain

DEFAULT_PREMIUM_STOP_PCT = {OptionPosition.BUY: 30.0, OptionPosition.WRITE: 50.0}


class ContractResolutionError(ValueError):
    """The rules cannot be satisfied right now (no master, no expiry, no strike) - the caller
    records the reason on the deployment and takes no trade."""


@dataclass(frozen=True)
class ContractRules:
    kind: InstrumentKind = InstrumentKind.UNDERLYING
    position: Optional[OptionPosition] = None
    expiry_rule: ExpiryRule = ExpiryRule.NEAREST
    strike_rule: StrikeRule = StrikeRule.ATM
    strike_offset: int = 0
    premium_stop_pct: Optional[float] = None
    max_lots: Optional[int] = None
    # Phase H1: chain-based filters around the rule strike (None/inactive = rule strike only).
    strike_filters: StrikeFilters = StrikeFilters()

    @classmethod
    def from_deployment(cls, dep: StrategyDeploymentRecord) -> "ContractRules":
        kind = InstrumentKind(dep.instrument_kind or "UNDERLYING")
        position = OptionPosition(dep.option_position) if dep.option_position else None
        return cls(
            kind=kind, position=position,
            expiry_rule=ExpiryRule(dep.expiry_rule) if dep.expiry_rule else ExpiryRule.NEAREST,
            strike_rule=StrikeRule(dep.strike_rule) if dep.strike_rule else StrikeRule.ATM,
            strike_offset=dep.strike_offset or 0,
            premium_stop_pct=dep.premium_stop_pct if dep.premium_stop_pct is not None else (DEFAULT_PREMIUM_STOP_PCT.get(position) if position else None),
            max_lots=dep.max_lots,
            strike_filters=StrikeFilters.from_json(getattr(dep, "strike_filters", None)),
        )

    @property
    def derived(self) -> bool:
        return self.kind != InstrumentKind.UNDERLYING


@dataclass(frozen=True)
class ResolvedContract:
    kind: InstrumentKind
    underlying: str                 # master name: NIFTY
    underlying_symbol: str          # candle/LTP symbol: NIFTY 50
    tradingsymbol: str
    exchange: str                   # NFO / BFO
    instrument_key: str
    lot_size: int
    tick_size: float
    expiry: date
    strike: Optional[float]
    right: Optional[str]            # CE / PE / None for futures
    entry_side: OrderSide           # broker order side for the entry leg
    trade_direction: SignalDirection  # LONG for bought options/long futures, SHORT for written options/short futures
    position: Optional[OptionPosition]
    # Phase H1: why this strike (chain filters), for the preview and the order trail.
    selection_notes: Tuple[str, ...] = ()
    selection: Optional[dict] = None

    def as_dict(self) -> dict:
        return {
            "kind": self.kind.value, "underlying": self.underlying, "underlying_symbol": self.underlying_symbol,
            "tradingsymbol": self.tradingsymbol, "exchange": self.exchange, "instrument_key": self.instrument_key,
            "lot_size": self.lot_size, "tick_size": self.tick_size, "expiry": self.expiry.isoformat(),
            "strike": self.strike, "right": self.right, "entry_side": self.entry_side.value,
            "trade_direction": self.trade_direction.value, "position": self.position.value if self.position else None,
            "selection_notes": list(self.selection_notes), "selection": self.selection,
        }


def option_right(direction: SignalDirection, position: OptionPosition) -> str:
    if position == OptionPosition.BUY:
        return "CE" if direction == SignalDirection.LONG else "PE"
    return "PE" if direction == SignalDirection.LONG else "CE"


def select_expiry(expiries: Sequence[date], rule: ExpiryRule, today: date) -> Optional[date]:
    upcoming = sorted(e for e in expiries if e >= today)
    if not upcoming:
        return None
    if rule == ExpiryRule.NEAREST:
        return upcoming[0]
    if rule == ExpiryRule.NEXT:
        return upcoming[1] if len(upcoming) > 1 else upcoming[0]
    first = upcoming[0]
    same_month = [e for e in upcoming if (e.year, e.month) == (first.year, first.month)]
    return max(same_month)


def select_strike(strikes: Sequence[float], spot: float, right: str, rule: StrikeRule, offset: int = 0) -> Optional[float]:
    listed = sorted(set(strikes))
    if not listed or spot <= 0:
        return None
    atm_index = min(range(len(listed)), key=lambda i: (abs(listed[i] - spot), listed[i]))
    if rule == StrikeRule.ATM or offset <= 0:
        return listed[atm_index]
    # CE: in the money = lower strikes; PE: in the money = higher strikes.
    towards_lower = (rule == StrikeRule.ITM) == (right.upper() == "CE")
    index = atm_index - offset if towards_lower else atm_index + offset
    index = max(0, min(len(listed) - 1, index))
    return listed[index]


ChainProvider = Callable[[str, date], Awaitable[OptionChain]]


async def resolve_contract(
    session: AsyncSession, symbol: str, rules: ContractRules, direction: SignalDirection, *,
    spot: Optional[float], today: date, broker: str = master.UPSTOX_BROKER,
    chain_provider: Optional[ChainProvider] = None,
) -> ResolvedContract:
    """`symbol` is the deployment's (underlying) symbol - an index name like NIFTY 50 or a stock.

    `chain_provider(underlying_symbol, expiry)` fetches the live option chain; it is required
    when the rules carry active strike filters (Phase H1) - without it, or when the chain cannot
    be fetched, resolution fails rather than trading an unchecked strike."""
    if not rules.derived:
        raise ContractResolutionError("Deployment trades the underlying itself - nothing to resolve")
    if direction not in (SignalDirection.LONG, SignalDirection.SHORT):
        raise ContractResolutionError("No direction to resolve a contract for")
    underlying = master.underlying_of(symbol)
    underlying_symbol = master.INDEX_SYMBOLS.get(underlying, symbol.upper().strip())

    if rules.kind == InstrumentKind.FUTURE:
        expiries = await master.expiries(session, underlying, broker=broker, instrument_type="FUT", on_or_after=today)
        expiry = select_expiry(expiries, rules.expiry_rule, today)
        if expiry is None:
            raise ContractResolutionError(f"No {underlying} futures on/after {today} in the {broker} instrument master - is it synced?")
        record = await master.find_future(session, underlying, expiry, broker=broker)
        if record is None:
            raise ContractResolutionError(f"{underlying} future for {expiry} missing from the instrument master")
        long = direction == SignalDirection.LONG
        return _resolved(rules, record, underlying, underlying_symbol, right=None,
                         entry_side=OrderSide.BUY if long else OrderSide.SELL,
                         trade_direction=SignalDirection.LONG if long else SignalDirection.SHORT)

    position = rules.position or OptionPosition.BUY
    right = option_right(direction, position)
    expiries = await master.expiries(session, underlying, broker=broker, instrument_type=right, on_or_after=today)
    expiry = select_expiry(expiries, rules.expiry_rule, today)
    if expiry is None:
        raise ContractResolutionError(f"No {underlying} {right} expiries on/after {today} in the {broker} instrument master - is it synced?")
    if spot is None or spot <= 0:
        raise ContractResolutionError(f"No spot price for {underlying_symbol} to pick a strike from")
    strikes = await master.strikes(session, underlying, expiry, broker=broker, instrument_type=right)
    strike = select_strike(strikes, spot, right, rules.strike_rule, rules.strike_offset)
    if strike is None:
        raise ContractResolutionError(f"No {underlying} {right} strikes for {expiry}")
    selection: Optional[SelectionResult] = None
    if rules.strike_filters.active:
        selection = await _apply_strike_filters(strikes, strike, right, rules.strike_filters, underlying_symbol, expiry,
                                                spot, today, chain_provider)
        strike = selection.strike
    record = await master.find_option(session, underlying, expiry, strike, right, broker=broker)
    if record is None:
        raise ContractResolutionError(f"{underlying} {int(strike)} {right} {expiry} missing from the instrument master")
    buying = position == OptionPosition.BUY
    resolved = _resolved(rules, record, underlying, underlying_symbol, right=right,
                         entry_side=OrderSide.BUY if buying else OrderSide.SELL,
                         trade_direction=SignalDirection.LONG if buying else SignalDirection.SHORT)
    if selection is not None:
        resolved = ResolvedContract(**{**resolved.__dict__, "selection_notes": tuple(selection.notes), "selection": selection.as_dict()})
    return resolved


async def _apply_strike_filters(
    strikes: Sequence[float], rule_strike: float, right: str, filters: StrikeFilters, underlying_symbol: str,
    expiry: date, spot: float, today: date, chain_provider: Optional[ChainProvider],
) -> SelectionResult:
    if chain_provider is None:
        raise ContractResolutionError(f"Strike filters ({filters.describe()}) need the option chain, and no broker session can fetch it")
    try:
        chain = await chain_provider(underlying_symbol, expiry)
    except Exception as exc:  # noqa: BLE001 - the reason goes on the deployment
        raise ContractResolutionError(f"Option chain for {underlying_symbol} {expiry} unavailable: {exc}") from exc
    if not chain.rows:
        raise ContractResolutionError(f"Option chain for {underlying_symbol} {expiry} is empty")
    try:
        return select_strike_with_chain(strikes, rule_strike, right, chain, filters, spot=spot, expiry=expiry, as_of=today)
    except StrikeSelectionError as exc:
        raise ContractResolutionError(str(exc)) from exc


def _resolved(rules: ContractRules, record: InstrumentRecord, underlying: str, underlying_symbol: str, *,
              right: Optional[str], entry_side: OrderSide, trade_direction: SignalDirection) -> ResolvedContract:
    return ResolvedContract(
        kind=rules.kind, underlying=underlying, underlying_symbol=underlying_symbol, tradingsymbol=record.tradingsymbol,
        exchange=record.exchange, instrument_key=record.instrument_key, lot_size=int(record.lot_size or 1),
        tick_size=float(record.tick_size or 0.05), expiry=record.expiry, strike=record.strike, right=right,
        entry_side=entry_side, trade_direction=trade_direction, position=rules.position if rules.kind == InstrumentKind.OPTION else None,
    )


def describe_rules(rules: ContractRules) -> str:
    if rules.kind == InstrumentKind.UNDERLYING:
        return "underlying"
    if rules.kind == InstrumentKind.FUTURE:
        return f"future, {rules.expiry_rule.value.lower()} expiry"
    strike = rules.strike_rule.value if rules.strike_rule == StrikeRule.ATM else f"{rules.strike_rule.value}{rules.strike_offset}"
    stop = f", premium stop {rules.premium_stop_pct:g}%" if rules.premium_stop_pct else ""
    filters = f", filters: {rules.strike_filters.describe()}" if rules.strike_filters.active else ""
    return f"{(rules.position or OptionPosition.BUY).value.lower()} option, {rules.expiry_rule.value.lower()} expiry, {strike}{stop}{filters}"
