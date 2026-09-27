"""Phase H2 + R: multi-leg option structures (master prompt sections 24-25, V2.2-2.6).

Structures, chosen on the deployment (`option_strategy`) and built at signal time from the same
rules the single leg uses:

Directional credit spreads (H2)
* **BULL_PUT_SPREAD** on a LONG signal: sell a PE at the rule strike (ATM / OTM n), buy a PE
  `spread_width` listed steps lower. Credit received; max loss = width - credit.
* **BEAR_CALL_SPREAD** on a SHORT signal: sell a CE at the rule strike, buy a CE `spread_width`
  steps higher.

Neutral credit structures, entered on either signal (the direction only records the lean)
* **IRON_CONDOR** (H2): both spreads at once, each short strike `strike_offset` steps OTM.
* **IRON_BUTTERFLY** (R): sell the ATM CE and PE, buy wings `spread_width` steps out on both
  sides. Defined risk: max loss = wing width - credit.
* **SHORT_STRADDLE** (R): sell the ATM CE and PE, no wings. **Undefined risk**: there is no max
  loss, so sizing uses the loss the stop accepts (`stop_credit_pct` of the credit) and the
  metrics say so. LIVE margin is the broker's number for both shorts (multileg._live_lot_cap).
* **SHORT_STRANGLE** (R): the same with both shorts `strike_offset` steps OTM.

Debit structures, entered on either signal
* **LONG_STRADDLE** (R): buy the ATM CE and PE. Max loss = debit paid; profit unbounded.
  Breakevens strike +/- debit.
* **LONG_STRANGLE** (R): buy OTM CE and PE `strike_offset` steps out.
* **CALENDAR_SPREAD** (R): sell the rule expiry's ATM option and buy the *next* expiry's same
  strike and right (PE on a LONG signal, CE on a SHORT one). Net debit; max loss = debit.

A structure whose direction disagrees with the signal (bull put on a SHORT) is a *no trade*,
never the opposite structure: the deployment said what it sells.

`structure_metrics` turns the legs' premiums into what V2 asks to see before a structure is
placed: net credit or debit, max profit, max loss, breakeven(s), the **risk per unit the sizer
uses** and the group exit levels the position monitor judges the whole group by. Everything is
per unit (one share of the lot); the executor multiplies by lot size and lots.

Underlying exits. Spreads, condors and strangles close when the underlying trades through a
short strike (`short_strikes`). A short straddle or iron butterfly has its shorts at the money,
so that rule would fire on entry; they close beyond a breakeven instead (`underlying_exits`).

Exit levels. For a *credit* structure the group's `value` is what it costs to close it (shorts'
premiums minus longs'): take profit when value <= target_value (credit x (1 - target%)), stop
when value >= stop_value (credit x (1 + stop%)). For a *debit* structure the group's `worth` is
what selling it brings (longs minus shorts): take profit when worth >= target_value
(debit x (1 + target%)), stop when worth <= stop_value (debit x (1 - stop%), stop% capped at
100 = the whole debit). `target_credit_pct` / `stop_credit_pct` on the deployment carry both
meanings; the UI labels them by structure.
"""
import json
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import InstrumentKind, OptionPosition, OptionStrategy, OrderSide, SignalDirection, StrikeRule
from app.instruments import master
from app.instruments.contracts import (
    ChainProvider, ContractResolutionError, ContractRules, ResolvedContract, _apply_strike_filters, _resolved,
    select_expiry, select_strike,
)

DEFAULT_TARGET_CREDIT_PCT = 50.0   # credit: take profit once half the credit has been captured
DEFAULT_STOP_CREDIT_PCT = 100.0    # credit: stop once the loss equals the credit (value doubled)
DEFAULT_TARGET_DEBIT_PCT = 50.0    # debit: take profit once the position is worth 1.5x the debit
DEFAULT_STOP_DEBIT_PCT = 50.0      # debit: stop once half the debit is lost

CREDIT_SPREADS = frozenset({OptionStrategy.BULL_PUT_SPREAD, OptionStrategy.BEAR_CALL_SPREAD})
WINGED = frozenset({OptionStrategy.BULL_PUT_SPREAD, OptionStrategy.BEAR_CALL_SPREAD, OptionStrategy.IRON_CONDOR,
                    OptionStrategy.IRON_BUTTERFLY})
UNDEFINED_RISK = frozenset({OptionStrategy.SHORT_STRADDLE, OptionStrategy.SHORT_STRANGLE})
ATM_SHORTS = frozenset({OptionStrategy.SHORT_STRADDLE, OptionStrategy.IRON_BUTTERFLY})
DEBIT_STRUCTURES = frozenset({OptionStrategy.LONG_STRADDLE, OptionStrategy.LONG_STRANGLE, OptionStrategy.CALENDAR_SPREAD})
CREDIT_STRUCTURES = WINGED | UNDEFINED_RISK
NEUTRAL = frozenset({OptionStrategy.IRON_CONDOR, OptionStrategy.IRON_BUTTERFLY, OptionStrategy.SHORT_STRADDLE,
                     OptionStrategy.SHORT_STRANGLE, OptionStrategy.LONG_STRADDLE, OptionStrategy.LONG_STRANGLE})


def is_debit(strategy: OptionStrategy) -> bool:
    return strategy in DEBIT_STRUCTURES


def uses_wings(strategy: OptionStrategy) -> bool:
    return strategy in WINGED


@dataclass(frozen=True)
class ResolvedLeg:
    role: str                       # SHORT (a sold leg) / LONG (a bought leg: wing, or the long side of a debit structure)
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
    expiry: date                     # the near expiry (a calendar's long leg is later)
    width_points: float              # strike distance of the (widest) wing; 0 without wings
    notes: List[str] = field(default_factory=list)

    @property
    def short_legs(self) -> List[ResolvedLeg]:
        return [l for l in self.legs if l.role == "SHORT"]

    @property
    def long_legs(self) -> List[ResolvedLeg]:
        return [l for l in self.legs if l.role == "LONG"]

    @property
    def debit(self) -> bool:
        return is_debit(self.strategy)

    def as_dict(self) -> dict:
        return {"strategy": self.strategy.value, "underlying_symbol": self.underlying_symbol, "lot_size": self.lot_size,
                "expiry": self.expiry.isoformat(), "width_points": self.width_points, "notes": self.notes, "debit": self.debit,
                "legs": [l.as_dict() for l in self.legs]}


@dataclass
class StructureMetrics:
    """Per-unit economics of a structure plus the group exit levels."""
    net_credit: float                 # positive for credit structures, negative (the debit) for debit ones
    max_profit: Optional[float]       # None = unbounded (long straddle/strangle, calendar)
    max_loss: Optional[float]         # None = undefined (short straddle/strangle)
    breakevens: List[float]
    target_value: float               # credit: close when value <= this; debit: when worth >= this
    stop_value: float                 # credit: close when value >= this; debit: when worth <= this
    short_strikes: Dict[str, float]   # right -> short strike, for the structure stop on the underlying
    legs: List[dict]
    debit: bool = False
    risk_per_unit: float = 0.0        # what the sizer divides risk-per-trade by: max loss, the debit, or the stop distance
    defined_risk: bool = True
    # ATM-short structures (straddle, iron butterfly): underlying levels that close the group
    # ("below"/"above" = the breakevens) in place of the short-strike breach.
    underlying_exits: Dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"net_credit": self.net_credit, "max_profit": self.max_profit, "max_loss": self.max_loss,
                "breakevens": self.breakevens, "target_value": self.target_value, "stop_value": self.stop_value,
                "short_strikes": self.short_strikes, "legs": self.legs, "debit": self.debit,
                "risk_per_unit": self.risk_per_unit, "defined_risk": self.defined_risk, "underlying_exits": self.underlying_exits}

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


def _leg_rules(rules: ContractRules, position: OptionPosition) -> ContractRules:
    return ContractRules(kind=InstrumentKind.OPTION, position=position, expiry_rule=rules.expiry_rule,
                         strike_rule=rules.strike_rule, strike_offset=rules.strike_offset)


def _short_leg(rules, record, underlying, underlying_symbol, right) -> ResolvedLeg:
    return ResolvedLeg("SHORT", _resolved(_leg_rules(rules, OptionPosition.WRITE), record, underlying, underlying_symbol, right=right,
                                          entry_side=OrderSide.SELL, trade_direction=SignalDirection.SHORT))


def _long_leg(rules, record, underlying, underlying_symbol, right) -> ResolvedLeg:
    return ResolvedLeg("LONG", _resolved(_leg_rules(rules, OptionPosition.BUY), record, underlying, underlying_symbol, right=right,
                                         entry_side=OrderSide.BUY, trade_direction=SignalDirection.LONG))


def _primary_strike_rule(strategy: OptionStrategy, rules: ContractRules):
    """(strike rule, offset) of each side's primary strike - the short strike of a credit
    structure or the bought strike of a debit one."""
    if strategy in (OptionStrategy.IRON_CONDOR, OptionStrategy.SHORT_STRANGLE, OptionStrategy.LONG_STRANGLE):
        return StrikeRule.OTM, max(1, rules.strike_offset or 1)
    if strategy in (OptionStrategy.IRON_BUTTERFLY, OptionStrategy.SHORT_STRADDLE, OptionStrategy.LONG_STRADDLE, OptionStrategy.CALENDAR_SPREAD):
        return StrikeRule.ATM, 0
    return rules.strike_rule, rules.strike_offset


async def resolve_structure(
    session: AsyncSession, symbol: str, rules: ContractRules, strategy: OptionStrategy, direction: SignalDirection, *,
    spread_width: int, spot: Optional[float], today: date, broker: str = master.UPSTOX_BROKER,
    chain_provider: Optional[ChainProvider] = None,
) -> ResolvedStructure:
    if not structure_direction_ok(strategy, direction):
        raise ContractResolutionError(f"{strategy.value} is not entered on a {direction.value} signal")
    if uses_wings(strategy) and spread_width < 1:
        raise ContractResolutionError("spread_width must be at least one strike step")
    if spot is None or spot <= 0:
        raise ContractResolutionError(f"No spot price for {symbol} to build a {strategy.value} from")
    underlying = master.underlying_of(symbol)
    underlying_symbol = master.INDEX_SYMBOLS.get(underlying, symbol.upper().strip())

    if strategy == OptionStrategy.CALENDAR_SPREAD:
        sides = ["PE" if direction == SignalDirection.LONG else "CE"]
    elif strategy == OptionStrategy.BULL_PUT_SPREAD:
        sides = ["PE"]
    elif strategy == OptionStrategy.BEAR_CALL_SPREAD:
        sides = ["CE"]
    else:
        sides = ["PE", "CE"]
    primary_role = "LONG" if is_debit(strategy) and strategy != OptionStrategy.CALENDAR_SPREAD else "SHORT"
    strike_rule, offset = _primary_strike_rule(strategy, rules)

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
        # The primary strike, measured for this right - OTM for a PE is below spot, for a CE above.
        primary_strike = select_strike(strikes, spot, right, strike_rule, offset)
        if primary_strike is None:
            raise ContractResolutionError(f"No {underlying} {right} strikes for {expiry}")
        if rules.strike_filters.active:
            selection = await _apply_strike_filters(strikes, primary_strike, right, rules.strike_filters, underlying_symbol, expiry,
                                                    spot, today, chain_provider)
            primary_strike = selection.strike
            notes.extend(selection.notes)
        primary_rec = await master.find_option(session, underlying, expiry, primary_strike, right, broker=broker)
        if primary_rec is None:
            raise ContractResolutionError(f"{underlying} {right} {int(primary_strike)} {expiry} missing from the master")
        lot_size = int(primary_rec.lot_size or 1)

        if strategy == OptionStrategy.CALENDAR_SPREAD:
            far_expiry = next((e for e in sorted(expiries) if e > expiry), None)
            if far_expiry is None:
                raise ContractResolutionError(f"No {underlying} {right} expiry after {expiry} for the calendar's long leg")
            far_rec = await master.find_option(session, underlying, far_expiry, primary_strike, right, broker=broker)
            if far_rec is None:
                raise ContractResolutionError(f"{underlying} {right} {int(primary_strike)} {far_expiry} missing from the master")
            legs.append(_short_leg(rules, primary_rec, underlying, underlying_symbol, right))
            legs.append(_long_leg(rules, far_rec, underlying, underlying_symbol, right))
            notes.append(f"{right}: sell {int(primary_strike)} {expiry.isoformat()} / buy {int(primary_strike)} {far_expiry.isoformat()}")
            continue

        if uses_wings(strategy):
            wing_strike = _step(strikes, primary_strike, -spread_width if right == "PE" else spread_width)
            if wing_strike is None:
                raise ContractResolutionError(f"No {right} strike {spread_width} steps beyond {int(primary_strike)} for the wing")
            wing_rec = await master.find_option(session, underlying, expiry, wing_strike, right, broker=broker)
            if wing_rec is None:
                raise ContractResolutionError(f"{underlying} {right} {int(primary_strike)}/{int(wing_strike)} {expiry} missing from the master")
            legs.append(_short_leg(rules, primary_rec, underlying, underlying_symbol, right))
            legs.append(_long_leg(rules, wing_rec, underlying, underlying_symbol, right))
            width_points = max(width_points, abs(primary_strike - wing_strike))
            notes.append(f"{right}: sell {int(primary_strike)} / buy {int(wing_strike)} ({int(abs(primary_strike - wing_strike))} pts wide)")
            continue

        if primary_role == "SHORT":
            legs.append(_short_leg(rules, primary_rec, underlying, underlying_symbol, right))
            notes.append(f"{right}: sell {int(primary_strike)} (no wing - undefined risk, sized off the stop)")
        else:
            legs.append(_long_leg(rules, primary_rec, underlying, underlying_symbol, right))
            notes.append(f"{right}: buy {int(primary_strike)}")
    assert expiry is not None
    return ResolvedStructure(strategy=strategy, legs=legs, underlying_symbol=underlying_symbol, lot_size=lot_size,
                             expiry=expiry, width_points=width_points, notes=notes)


def structure_metrics(structure: ResolvedStructure, premiums: Dict[str, float], *,
                      target_credit_pct: Optional[float], stop_credit_pct: Optional[float]) -> StructureMetrics:
    """`premiums` maps each leg's tradingsymbol to its current price. Per unit."""
    credit = 0.0
    legs = []
    short_strikes: Dict[str, float] = {}
    long_strikes: Dict[str, float] = {}
    for leg in structure.legs:
        price = premiums.get(leg.contract.tradingsymbol)
        if price is None or price <= 0:
            raise ContractResolutionError(f"No premium quote for {leg.contract.tradingsymbol}")
        credit += price if leg.role == "SHORT" else -price
        legs.append({"role": leg.role, "side": leg.side.value, "tradingsymbol": leg.contract.tradingsymbol,
                     "strike": leg.contract.strike, "right": leg.contract.right, "premium": price,
                     "expiry": leg.contract.expiry.isoformat()})
        if leg.contract.right:
            (short_strikes if leg.role == "SHORT" else long_strikes)[leg.contract.right] = float(leg.contract.strike or 0.0)
    credit = round(credit, 2)
    strategy = structure.strategy

    if structure.debit:
        if credit >= 0:
            raise ContractResolutionError(f"{strategy.value} would be a net credit ({credit}) - quotes inconsistent, not entered")
        debit = round(-credit, 2)
        target_pct = target_credit_pct if target_credit_pct is not None else DEFAULT_TARGET_DEBIT_PCT
        stop_pct = min(stop_credit_pct if stop_credit_pct is not None else DEFAULT_STOP_DEBIT_PCT, 100.0)
        breakevens: List[float] = []
        if strategy in (OptionStrategy.LONG_STRADDLE, OptionStrategy.LONG_STRANGLE):
            if "PE" in long_strikes:
                breakevens.append(round(long_strikes["PE"] - debit, 2))
            if "CE" in long_strikes:
                breakevens.append(round(long_strikes["CE"] + debit, 2))
        # A calendar's payoff depends on the far leg's value at the near expiry - no static breakeven.
        return StructureMetrics(
            net_credit=credit, max_profit=None, max_loss=debit, breakevens=breakevens,
            target_value=round(debit * (1 + target_pct / 100.0), 2), stop_value=round(debit * (1 - stop_pct / 100.0), 2),
            # A calendar's short strike is protected by the far leg; no underlying breach exit.
            short_strikes={}, legs=legs, debit=True, risk_per_unit=debit, defined_risk=True,
        )

    if credit <= 0:
        raise ContractResolutionError(f"{strategy.value} would be a net debit ({credit}) - not entered")
    breakevens = []
    if "PE" in short_strikes:
        breakevens.append(round(short_strikes["PE"] - credit, 2))
    if "CE" in short_strikes:
        breakevens.append(round(short_strikes["CE"] + credit, 2))
    target_pct = target_credit_pct if target_credit_pct is not None else DEFAULT_TARGET_CREDIT_PCT
    stop_pct = stop_credit_pct if stop_credit_pct is not None else DEFAULT_STOP_CREDIT_PCT
    stop_value = round(credit * (1 + stop_pct / 100.0), 2)
    underlying_exits: Dict[str, float] = {}
    if strategy in ATM_SHORTS:
        # The shorts sit at the money, so "underlying through the short strike" would fire on
        # entry. The breakevens are the levels that matter: beyond them the structure loses.
        if len(breakevens) == 2:
            underlying_exits = {"below": min(breakevens), "above": max(breakevens)}
        short_strikes = {}
    if strategy in UNDEFINED_RISK:
        # No wing: the loss is open-ended. The sizer works off the loss the stop accepts, and the
        # metrics say so; the position monitor's stop and the underlying levels are the exits.
        stop_risk = round(stop_value - credit, 2)
        return StructureMetrics(
            net_credit=credit, max_profit=credit, max_loss=None, breakevens=breakevens,
            target_value=round(credit * (1 - target_pct / 100.0), 2), stop_value=stop_value,
            short_strikes=short_strikes, legs=legs, debit=False, risk_per_unit=stop_risk, defined_risk=False,
            underlying_exits=underlying_exits,
        )
    max_loss = round(structure.width_points - credit, 2)
    return StructureMetrics(
        net_credit=credit, max_profit=credit, max_loss=max_loss, breakevens=breakevens,
        target_value=round(credit * (1 - target_pct / 100.0), 2), stop_value=stop_value,
        short_strikes=short_strikes, legs=legs, debit=False, risk_per_unit=max_loss, defined_risk=True,
        underlying_exits=underlying_exits,
    )


def describe_structure(strategy: OptionStrategy, spread_width: int, rules: ContractRules) -> str:
    if strategy == OptionStrategy.SINGLE:
        return ""
    expiry = rules.expiry_rule.value.lower()
    otm = max(1, rules.strike_offset or 1)
    if strategy == OptionStrategy.IRON_CONDOR:
        return f"iron condor, shorts {otm} step(s) OTM, wings {spread_width} step(s)"
    if strategy == OptionStrategy.IRON_BUTTERFLY:
        return f"iron butterfly, shorts ATM, wings {spread_width} step(s), {expiry} expiry"
    if strategy == OptionStrategy.SHORT_STRADDLE:
        return f"short straddle, ATM CE + PE, no wings (undefined risk, sized off the stop), {expiry} expiry"
    if strategy == OptionStrategy.SHORT_STRANGLE:
        return f"short strangle, CE + PE {otm} step(s) OTM, no wings (undefined risk, sized off the stop), {expiry} expiry"
    if strategy == OptionStrategy.LONG_STRADDLE:
        return f"long straddle, buy ATM CE + PE (debit), {expiry} expiry"
    if strategy == OptionStrategy.LONG_STRANGLE:
        return f"long strangle, buy CE + PE {otm} step(s) OTM (debit), {expiry} expiry"
    if strategy == OptionStrategy.CALENDAR_SPREAD:
        return f"calendar spread, sell {expiry} ATM / buy the next expiry (PE on LONG, CE on SHORT; debit)"
    name = "bull put spread" if strategy == OptionStrategy.BULL_PUT_SPREAD else "bear call spread"
    strike = rules.strike_rule.value if rules.strike_rule == StrikeRule.ATM else f"{rules.strike_rule.value}{rules.strike_offset}"
    return f"{name}, short {strike}, wing {spread_width} step(s), {expiry} expiry"
