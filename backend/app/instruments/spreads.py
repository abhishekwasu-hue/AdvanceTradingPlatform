"""Phase H2: multi-leg option structures (master prompt sections 24-25, V2.2-2.6).

Three defined-risk structures, chosen on the deployment (`option_strategy`) and built at signal
time from the same rules the single leg uses:

* **BULL_PUT_SPREAD** on a LONG signal: sell a PE at the rule strike (ATM / OTM n), buy a PE
  `spread_width` listed steps lower. Credit received; max loss = width - credit.
* **BEAR_CALL_SPREAD** on a SHORT signal: sell a CE at the rule strike, buy a CE `spread_width`
  steps higher.
* **IRON_CONDOR** on either signal: both of the above at once, each short strike `strike_offset`
  steps out of the money (a range trade entered on the signal bar - the direction only records
  which way the strategy leaned).

A structure whose direction disagrees with the signal (bull put on a SHORT) is a *no trade*,
never the opposite structure: the deployment said what it sells.

`structure_metrics` turns the legs' premiums into what V2 asks to see before a spread is placed:
net credit, max profit, max loss, breakeven(s) and the credit-based exit levels the position
monitor judges the whole group by. Everything is per unit (one share of the lot); the executor
multiplies by lot size and lots.
"""
import json
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import OptionPosition, OptionStrategy, OrderSide, SignalDirection
from app.instruments import master
from app.instruments.contracts import (
    ChainProvider, ContractResolutionError, ContractRules, ResolvedContract, _apply_strike_filters, _resolved,
    select_expiry, select_strike,
)
from app.core.enums import InstrumentKind, StrikeRule

DEFAULT_TARGET_CREDIT_PCT = 50.0   # take profit once half the credit has been captured
DEFAULT_STOP_CREDIT_PCT = 100.0    # stop once the loss equals the credit (spread value doubled)


@dataclass(frozen=True)
class ResolvedLeg:
    role: str                       # SHORT (the sold leg) / LONG (the protective wing)
    contract: ResolvedContract

    @property
    def side(self) -> OrderSide:
        return OrderSide.SELL if self.role == "SHORT" else OrderSide.BUY

    def as_dict(self) -> dict:
        return {"role": self.role, "side": self.side.value, **self.contract.as_dict()}


@dataclass(frozen=True)
class ResolvedStructure:
    strategy: OptionStrategy
    legs: List[ResolvedLeg]
    underlying_symbol: str
    lot_size: int
    expiry: date
    width_points: float              # strike distance of the (widest) wing
    notes: List[str] = field(default_factory=list)

    @property
    def short_legs(self) -> List[ResolvedLeg]:
        return [l for l in self.legs if l.role == "SHORT"]

    @property
    def long_legs(self) -> List[ResolvedLeg]:
        return [l for l in self.legs if l.role == "LONG"]

    def as_dict(self) -> dict:
        return {"strategy": self.strategy.value, "underlying_symbol": self.underlying_symbol, "lot_size": self.lot_size,
                "expiry": self.expiry.isoformat(), "width_points": self.width_points, "notes": self.notes,
                "legs": [l.as_dict() for l in self.legs]}


@dataclass
class StructureMetrics:
    """Per-unit economics of a credit structure plus the group exit levels."""
    net_credit: float
    max_profit: float
    max_loss: float
    breakevens: List[float]
    target_value: float     # close the group when its value (cost to close) falls to this
    stop_value: float       # ... or rises to this
    short_strikes: Dict[str, float]   # right -> short strike, for the structure stop on the underlying
    legs: List[dict]

    def as_dict(self) -> dict:
        return {"net_credit": self.net_credit, "max_profit": self.max_profit, "max_loss": self.max_loss,
                "breakevens": self.breakevens, "target_value": self.target_value, "stop_value": self.stop_value,
                "short_strikes": self.short_strikes, "legs": self.legs}

    def to_json(self) -> str:
        return json.dumps(self.as_dict())


def structure_direction_ok(strategy: OptionStrategy, direction: SignalDirection) -> bool:
    if strategy == OptionStrategy.BULL_PUT_SPREAD:
        return direction == SignalDirection.LONG
    if strategy == OptionStrategy.BEAR_CALL_SPREAD:
        return direction == SignalDirection.SHORT
    return direction in (SignalDirection.LONG, SignalDirection.SHORT)


def _step(listed: Sequence[float], strike: float, steps: int) -> Optional[float]:
    """The strike `steps` listed positions away (negative = lower). None past the ends."""
    ordered = sorted(set(listed))
    if strike not in ordered:
        return None
    index = ordered.index(strike) + steps
    if index < 0 or index >= len(ordered):
        return None
    return ordered[index]


async def resolve_structure(
    session: AsyncSession, symbol: str, rules: ContractRules, strategy: OptionStrategy, direction: SignalDirection, *,
    spread_width: int, spot: Optional[float], today: date, broker: str = master.UPSTOX_BROKER,
    chain_provider: Optional[ChainProvider] = None,
) -> ResolvedStructure:
    if not structure_direction_ok(strategy, direction):
        raise ContractResolutionError(f"{strategy.value} is not entered on a {direction.value} signal")
    if spread_width < 1:
        raise ContractResolutionError("spread_width must be at least one strike step")
    if spot is None or spot <= 0:
        raise ContractResolutionError(f"No spot price for {symbol} to build a {strategy.value} from")
    underlying = master.underlying_of(symbol)
    underlying_symbol = master.INDEX_SYMBOLS.get(underlying, symbol.upper().strip())

    sides: List[str] = []
    if strategy in (OptionStrategy.BULL_PUT_SPREAD, OptionStrategy.IRON_CONDOR):
        sides.append("PE")
    if strategy in (OptionStrategy.BEAR_CALL_SPREAD, OptionStrategy.IRON_CONDOR):
        sides.append("CE")

    legs: List[ResolvedLeg] = []
    notes: List[str] = []
    expiry: Optional[date] = None
    lot_size = 0
    width_points = 0.0
    for right in sides:
        expiries = await master.expiries(session, underlying, broker=broker, instrument_type=right, on_or_after=today)
        leg_expiry = select_expiry(expiries, rules.expiry_rule, today)
        if leg_expiry is None:
            raise ContractResolutionError(f"No {underlying} {right} expiries on/after {today} in the instrument master")
        if expiry is not None and leg_expiry != expiry:
            raise ContractResolutionError(f"{underlying} CE/PE expiries differ ({expiry} vs {leg_expiry})")
        expiry = leg_expiry
        strikes = await master.strikes(session, underlying, expiry, broker=broker, instrument_type=right)
        # The short strike: the rule strike (ATM for spreads by default; OTM n for a condor's
        # wings) measured for the *sold* right - OTM for a PE is below spot, for a CE above.
        strike_rule = rules.strike_rule if strategy != OptionStrategy.IRON_CONDOR else StrikeRule.OTM
        offset = rules.strike_offset if strategy != OptionStrategy.IRON_CONDOR else max(1, rules.strike_offset or 1)
        short_strike = select_strike(strikes, spot, right, strike_rule, offset)
        if short_strike is None:
            raise ContractResolutionError(f"No {underlying} {right} strikes for {expiry}")
        if rules.strike_filters.active:
            selection = await _apply_strike_filters(strikes, short_strike, right, rules.strike_filters, underlying_symbol, expiry,
                                                    spot, today, chain_provider)
            short_strike = selection.strike
            notes.extend(selection.notes)
        long_strike = _step(strikes, short_strike, -spread_width if right == "PE" else spread_width)
        if long_strike is None:
            raise ContractResolutionError(f"No {right} strike {spread_width} steps beyond {int(short_strike)} for the wing")
        short_rec = await master.find_option(session, underlying, expiry, short_strike, right, broker=broker)
        long_rec = await master.find_option(session, underlying, expiry, long_strike, right, broker=broker)
        if short_rec is None or long_rec is None:
            raise ContractResolutionError(f"{underlying} {right} {int(short_strike)}/{int(long_strike)} {expiry} missing from the master")
        write_rules = ContractRules(kind=InstrumentKind.OPTION, position=OptionPosition.WRITE, expiry_rule=rules.expiry_rule,
                                    strike_rule=rules.strike_rule, strike_offset=rules.strike_offset)
        buy_rules = ContractRules(kind=InstrumentKind.OPTION, position=OptionPosition.BUY, expiry_rule=rules.expiry_rule)
        legs.append(ResolvedLeg("SHORT", _resolved(write_rules, short_rec, underlying, underlying_symbol, right=right,
                                                    entry_side=OrderSide.SELL, trade_direction=SignalDirection.SHORT)))
        legs.append(ResolvedLeg("LONG", _resolved(buy_rules, long_rec, underlying, underlying_symbol, right=right,
                                                   entry_side=OrderSide.BUY, trade_direction=SignalDirection.LONG)))
        lot_size = int(short_rec.lot_size or 1)
        width_points = max(width_points, abs(short_strike - long_strike))
        notes.append(f"{right}: sell {int(short_strike)} / buy {int(long_strike)} ({int(abs(short_strike - long_strike))} pts wide)")
    assert expiry is not None
    return ResolvedStructure(strategy=strategy, legs=legs, underlying_symbol=underlying_symbol, lot_size=lot_size,
                             expiry=expiry, width_points=width_points, notes=notes)


def structure_metrics(structure: ResolvedStructure, premiums: Dict[str, float], *,
                      target_credit_pct: Optional[float], stop_credit_pct: Optional[float]) -> StructureMetrics:
    """`premiums` maps each leg's tradingsymbol to its current price. Per unit."""
    credit = 0.0
    legs = []
    short_strikes: Dict[str, float] = {}
    for leg in structure.legs:
        price = premiums.get(leg.contract.tradingsymbol)
        if price is None or price <= 0:
            raise ContractResolutionError(f"No premium quote for {leg.contract.tradingsymbol}")
        credit += price if leg.role == "SHORT" else -price
        legs.append({"role": leg.role, "side": leg.side.value, "tradingsymbol": leg.contract.tradingsymbol,
                     "strike": leg.contract.strike, "right": leg.contract.right, "premium": price})
        if leg.role == "SHORT" and leg.contract.right:
            short_strikes[leg.contract.right] = float(leg.contract.strike or 0.0)
    credit = round(credit, 2)
    if credit <= 0:
        raise ContractResolutionError(f"{structure.strategy.value} would be a net debit ({credit}) - not entered")
    breakevens: List[float] = []
    if "PE" in short_strikes:
        breakevens.append(round(short_strikes["PE"] - credit, 2))
    if "CE" in short_strikes:
        breakevens.append(round(short_strikes["CE"] + credit, 2))
    max_loss = round(structure.width_points - credit, 2)
    target_pct = target_credit_pct if target_credit_pct is not None else DEFAULT_TARGET_CREDIT_PCT
    stop_pct = stop_credit_pct if stop_credit_pct is not None else DEFAULT_STOP_CREDIT_PCT
    return StructureMetrics(
        net_credit=credit, max_profit=credit, max_loss=max_loss, breakevens=breakevens,
        target_value=round(credit * (1 - target_pct / 100.0), 2), stop_value=round(credit * (1 + stop_pct / 100.0), 2),
        short_strikes=short_strikes, legs=legs,
    )


def describe_structure(strategy: OptionStrategy, spread_width: int, rules: ContractRules) -> str:
    if strategy == OptionStrategy.SINGLE:
        return ""
    if strategy == OptionStrategy.IRON_CONDOR:
        return f"iron condor, shorts {max(1, rules.strike_offset or 1)} step(s) OTM, wings {spread_width} step(s)"
    name = "bull put spread" if strategy == OptionStrategy.BULL_PUT_SPREAD else "bear call spread"
    strike = rules.strike_rule.value if rules.strike_rule == StrikeRule.ATM else f"{rules.strike_rule.value}{rules.strike_offset}"
    return f"{name}, short {strike}, wing {spread_width} step(s), {rules.expiry_rule.value.lower()} expiry"
