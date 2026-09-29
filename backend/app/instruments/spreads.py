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

Phase U - payoff-priced structures. Ratio spreads, the long butterfly and the free-form leg
builder (`CUSTOM`, legs from the deployment's `custom_legs`) have no hand rule for their
economics; `app/instruments/payoff.py` derives max profit, max loss, breakevens and whether
either side is unbounded from the expiry payoff. Their legs carry a `ratio` (1:2 for a ratio
spread, 1:2:1 for a butterfly), which the executor multiplies the lot quantity by. Their exits
are judged on the structure's mark-to-market **P&L per unit** (`pnl_target` / `pnl_stop` in the
metrics) rather than on value-vs-credit, because a ratio spread can be entered for almost
nothing, where a percentage of the credit means nothing. The stop and target are percentages
of the *risk basis*: the max loss when it is defined, otherwise the finite peak profit (so the
default 100% stop risks what the structure can make at best). An undefined side also gets an
underlying exit at that side's breakeven.
"""
import json
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import InstrumentKind, OptionPosition, OptionStrategy, OrderSide, SignalDirection, StrikeRule
from app.instruments import master
from app.instruments.contracts import (
    ChainProvider, ContractResolutionError, ContractRules, ResolvedContract, _apply_strike_filters, _resolved,
    select_expiry, select_strike,
)
from app.instruments.payoff import PayoffLeg, analyse

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
# Phase U: economics from the expiry payoff, legs with ratios, P&L-based exits.
RATIO_SPREADS = frozenset({OptionStrategy.CALL_RATIO_SPREAD, OptionStrategy.PUT_RATIO_SPREAD})
PAYOFF_STRUCTURES = RATIO_SPREADS | frozenset({OptionStrategy.LONG_BUTTERFLY, OptionStrategy.CUSTOM})
WIDTH_STRUCTURES = WINGED | RATIO_SPREADS | frozenset({OptionStrategy.LONG_BUTTERFLY})   # take spread_width
RATIO_SHORTS = 2                    # a ratio spread sells this many per bought leg
DEFAULT_TARGET_PAYOFF_PCT = 50.0    # payoff structures: take profit at half the max (or peak) profit
DEFAULT_STOP_DEFINED_PCT = 50.0     # ... stop at half the max loss when the risk is defined
DEFAULT_STOP_UNDEFINED_PCT = 100.0  # ... or at a loss equal to the peak profit when it is not
MAX_CUSTOM_LEGS = 6
MAX_LEG_RATIO = 4
NEUTRAL = frozenset({OptionStrategy.IRON_CONDOR, OptionStrategy.IRON_BUTTERFLY, OptionStrategy.SHORT_STRADDLE,
                     OptionStrategy.SHORT_STRANGLE, OptionStrategy.LONG_STRADDLE, OptionStrategy.LONG_STRANGLE})


def is_debit(strategy: OptionStrategy) -> bool:
    """Structures that are always a net debit (the long butterfly included). A ratio spread or a
    custom leg set can be either; `StructureMetrics.debit` says which at quote time."""
    return strategy in DEBIT_STRUCTURES or strategy == OptionStrategy.LONG_BUTTERFLY


def uses_wings(strategy: OptionStrategy) -> bool:
    return strategy in WINGED


def is_payoff_priced(strategy: OptionStrategy) -> bool:
    return strategy in PAYOFF_STRUCTURES


@dataclass(frozen=True)
class CustomLeg:
    """One leg of a free-form structure as the deployment stores it (Phase U). The expiry is the
    deployment's expiry rule, shared by every leg."""
    right: str                      # CE / PE
    role: str                       # SHORT / LONG
    strike_rule: StrikeRule = StrikeRule.ATM
    strike_offset: int = 0
    ratio: int = 1

    def as_dict(self) -> dict:
        return {"right": self.right, "role": self.role, "strike_rule": self.strike_rule.value,
                "strike_offset": self.strike_offset, "ratio": self.ratio}

    @classmethod
    def from_dict(cls, raw: dict) -> "CustomLeg":
        right = str(raw.get("right", "")).upper()
        role = str(raw.get("role", "")).upper()
        if right not in ("CE", "PE"):
            raise ValueError(f"leg right must be CE or PE, not {raw.get('right')!r}")
        if role not in ("SHORT", "LONG"):
            raise ValueError(f"leg role must be SHORT or LONG, not {raw.get('role')!r}")
        rule_raw = raw.get("strike_rule") or "ATM"
        rule = rule_raw if isinstance(rule_raw, StrikeRule) else StrikeRule(str(rule_raw).upper())
        offset = int(raw.get("strike_offset") or 0)
        if rule == StrikeRule.ATM:
            offset = 0
        elif offset < 1:
            offset = 1
        ratio = int(raw.get("ratio") or 1)
        if ratio < 1 or ratio > MAX_LEG_RATIO:
            raise ValueError(f"leg ratio must be between 1 and {MAX_LEG_RATIO}")
        return cls(right=right, role=role, strike_rule=rule, strike_offset=offset, ratio=ratio)

    def describe(self) -> str:
        strike = "ATM" if self.strike_rule == StrikeRule.ATM else f"{self.strike_rule.value}{self.strike_offset}"
        return f"{'sell' if self.role == 'SHORT' else 'buy'} {self.ratio}x {strike} {self.right}"


def validate_custom_legs(legs: Sequence[CustomLeg]) -> None:
    """Shape rules a leg set must meet before anything is resolved."""
    if len(legs) < 2:
        raise ValueError("a custom structure needs at least two legs (use a single option for one)")
    if len(legs) > MAX_CUSTOM_LEGS:
        raise ValueError(f"a custom structure takes at most {MAX_CUSTOM_LEGS} legs")
    seen = set()
    for leg in legs:
        key = (leg.right, leg.strike_rule, leg.strike_offset)
        if key in seen:
            raise ValueError(f"two legs name the same contract ({leg.describe()}): combine them into one leg with a ratio")
        seen.add(key)


def parse_custom_legs(raw) -> List[CustomLeg]:
    """From the deployment column (JSON text) or an already-decoded list. Raises ValueError."""
    if raw is None or raw == "":
        return []
    items = json.loads(raw) if isinstance(raw, str) else list(raw)
    legs = [item if isinstance(item, CustomLeg) else CustomLeg.from_dict(item) for item in items]
    validate_custom_legs(legs)
    return legs


def custom_legs_json(legs: Sequence[CustomLeg]) -> Optional[str]:
    return json.dumps([leg.as_dict() for leg in legs]) if legs else None


@dataclass(frozen=True)
class ResolvedLeg:
    role: str                       # SHORT (a sold leg) / LONG (a bought leg: wing, or the long side of a debit structure)
    contract: ResolvedContract
    ratio: int = 1                  # Phase U: lots of this leg per lot of the structure (1:2 ratio spread, 1:2:1 butterfly)

    @property
    def side(self) -> OrderSide:
        return OrderSide.SELL if self.role == "SHORT" else OrderSide.BUY

    def as_dict(self) -> dict:
        return {"role": self.role, "side": self.side.value, "ratio": self.ratio, **self.contract.as_dict()}


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

    @property
    def max_ratio(self) -> int:
        return max((leg.ratio for leg in self.legs), default=1)

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
    # Phase U: payoff-priced structures are judged on mark-to-market P&L per unit instead
    # (target when pnl >= pnl_target, stop when pnl <= pnl_stop); None for the older structures.
    pnl_target: Optional[float] = None
    pnl_stop: Optional[float] = None

    def as_dict(self) -> dict:
        out = {"net_credit": self.net_credit, "max_profit": self.max_profit, "max_loss": self.max_loss,
               "breakevens": self.breakevens, "target_value": self.target_value, "stop_value": self.stop_value,
               "short_strikes": self.short_strikes, "legs": self.legs, "debit": self.debit,
               "risk_per_unit": self.risk_per_unit, "defined_risk": self.defined_risk, "underlying_exits": self.underlying_exits}
        if self.pnl_stop is not None:
            out["pnl_target"] = self.pnl_target
            out["pnl_stop"] = self.pnl_stop
        return out

    def to_json(self) -> str:
        return json.dumps(self.as_dict())


def structure_direction_ok(strategy: OptionStrategy, direction: SignalDirection) -> bool:
    if strategy in (OptionStrategy.BULL_PUT_SPREAD, OptionStrategy.CALL_RATIO_SPREAD):
        return direction == SignalDirection.LONG
    if strategy in (OptionStrategy.BEAR_CALL_SPREAD, OptionStrategy.PUT_RATIO_SPREAD):
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


def _short_leg(rules, record, underlying, underlying_symbol, right, ratio: int = 1) -> ResolvedLeg:
    return ResolvedLeg("SHORT", _resolved(_leg_rules(rules, OptionPosition.WRITE), record, underlying, underlying_symbol, right=right,
                                          entry_side=OrderSide.SELL, trade_direction=SignalDirection.SHORT), ratio)


def _long_leg(rules, record, underlying, underlying_symbol, right, ratio: int = 1) -> ResolvedLeg:
    return ResolvedLeg("LONG", _resolved(_leg_rules(rules, OptionPosition.BUY), record, underlying, underlying_symbol, right=right,
                                         entry_side=OrderSide.BUY, trade_direction=SignalDirection.LONG), ratio)


def _primary_strike_rule(strategy: OptionStrategy, rules: ContractRules):
    """(strike rule, offset) of each side's primary strike - the short strike of a credit
    structure or the bought strike of a debit one."""
    if strategy in (OptionStrategy.IRON_CONDOR, OptionStrategy.SHORT_STRANGLE, OptionStrategy.LONG_STRANGLE):
        return StrikeRule.OTM, max(1, rules.strike_offset or 1)
    if strategy in (OptionStrategy.IRON_BUTTERFLY, OptionStrategy.SHORT_STRADDLE, OptionStrategy.LONG_STRADDLE, OptionStrategy.CALENDAR_SPREAD,
                    OptionStrategy.LONG_BUTTERFLY):
        return StrikeRule.ATM, 0
    return rules.strike_rule, rules.strike_offset


@dataclass(frozen=True)
class PlannedLeg:
    """Phase W: one leg of a structure before any master lookup - the strike math shared by
    the live resolver and the option backtest engine."""
    right: str                  # CE / PE
    role: str                   # SHORT / LONG
    strike: float
    ratio: int = 1
    far_expiry: bool = False    # the calendar's long leg sits on the next expiry


def structure_sides(strategy: OptionStrategy, direction: SignalDirection) -> Tuple[List[str], str]:
    """(rights the structure is built on, role of each side's primary strike)."""
    if strategy in (OptionStrategy.CALENDAR_SPREAD, OptionStrategy.LONG_BUTTERFLY):
        # Time spread: PE on a LONG lean, CE on a SHORT one. Butterfly: the payoff is the same
        # either way (put-call parity); the lean picks the right, CE on LONG.
        lean_pe = direction == SignalDirection.LONG
        sides = ["PE" if lean_pe else "CE"] if strategy == OptionStrategy.CALENDAR_SPREAD else ["CE" if lean_pe else "PE"]
    elif strategy in (OptionStrategy.BULL_PUT_SPREAD, OptionStrategy.PUT_RATIO_SPREAD):
        sides = ["PE"]
    elif strategy in (OptionStrategy.BEAR_CALL_SPREAD, OptionStrategy.CALL_RATIO_SPREAD):
        sides = ["CE"]
    else:
        sides = ["PE", "CE"]
    primary_role = "LONG" if (is_debit(strategy) and strategy != OptionStrategy.CALENDAR_SPREAD) or strategy in RATIO_SPREADS else "SHORT"
    return sides, primary_role


def plan_side(strategy: OptionStrategy, right: str, primary_strike: float, strikes: Sequence[float], spread_width: int,
              primary_role: str) -> List[PlannedLeg]:
    """The legs of one right given its primary strike and the listed strikes. Raises
    ContractResolutionError when the ladder has no strike where a wing or short leg must sit."""
    if strategy == OptionStrategy.CALENDAR_SPREAD:
        return [PlannedLeg(right, "SHORT", primary_strike), PlannedLeg(right, "LONG", primary_strike, far_expiry=True)]
    if strategy in RATIO_SPREADS:
        # Buy one at the rule strike, sell RATIO_SHORTS further out of the money: cheap or
        # free to enter, profits most at the short strike, unbounded loss beyond it.
        short_strike = _step(strikes, primary_strike, -spread_width if right == "PE" else spread_width)
        if short_strike is None:
            raise ContractResolutionError(f"No {right} strike {spread_width} steps beyond {int(primary_strike)} for the ratio's short legs")
        return [PlannedLeg(right, "LONG", primary_strike), PlannedLeg(right, "SHORT", short_strike, RATIO_SHORTS)]
    if strategy == OptionStrategy.LONG_BUTTERFLY:
        lower = _step(strikes, primary_strike, -spread_width)
        upper = _step(strikes, primary_strike, spread_width)
        if lower is None or upper is None:
            raise ContractResolutionError(f"No {right} strikes {spread_width} steps either side of {int(primary_strike)} for the butterfly wings")
        return [PlannedLeg(right, "LONG", lower), PlannedLeg(right, "SHORT", primary_strike, 2), PlannedLeg(right, "LONG", upper)]
    if uses_wings(strategy):
        wing_strike = _step(strikes, primary_strike, -spread_width if right == "PE" else spread_width)
        if wing_strike is None:
            raise ContractResolutionError(f"No {right} strike {spread_width} steps beyond {int(primary_strike)} for the wing")
        return [PlannedLeg(right, "SHORT", primary_strike), PlannedLeg(right, "LONG", wing_strike)]
    return [PlannedLeg(right, primary_role, primary_strike)]


def plan_structure(strategy: OptionStrategy, direction: SignalDirection, rules: ContractRules, *, spot: float,
                   strikes_by_right: Dict[str, Sequence[float]], spread_width: int,
                   custom_legs: Optional[Sequence[CustomLeg]] = None) -> List[PlannedLeg]:
    """Phase W: every leg of the structure from a strike ladder alone (no master, no chain
    filters) - what the option backtest engine builds positions from. The same rules as
    `resolve_structure`: direction fit, width, primary strike per right, then `plan_side`."""
    if not structure_direction_ok(strategy, direction):
        raise ContractResolutionError(f"{strategy.value} is not entered on a {direction.value} signal")
    if strategy in WIDTH_STRUCTURES and spread_width < 1:
        raise ContractResolutionError("spread_width must be at least one strike step")
    if spot <= 0:
        raise ContractResolutionError("No spot price to build the structure from")
    if strategy == OptionStrategy.CUSTOM:
        specs = list(custom_legs or [])
        if not specs:
            raise ContractResolutionError("CUSTOM structure has no legs - add them on the deployment")
        validate_custom_legs(specs)
        legs: List[PlannedLeg] = []
        seen: Dict[Tuple[str, float], str] = {}
        for spec in specs:
            strike = select_strike(strikes_by_right.get(spec.right, ()), spot, spec.right, spec.strike_rule, spec.strike_offset)
            if strike is None:
                raise ContractResolutionError(f"No {spec.right} strike for {spec.describe()}")
            if (spec.right, strike) in seen:
                raise ContractResolutionError(f"{spec.describe()} and {seen[(spec.right, strike)]} resolve to the same contract - combine or change them")
            seen[(spec.right, strike)] = spec.describe()
            legs.append(PlannedLeg(spec.right, spec.role, strike, spec.ratio))
        return legs
    sides, primary_role = structure_sides(strategy, direction)
    strike_rule, offset = _primary_strike_rule(strategy, rules)
    legs = []
    for right in sides:
        strikes = strikes_by_right.get(right, ())
        primary = select_strike(strikes, spot, right, strike_rule, offset)
        if primary is None:
            raise ContractResolutionError(f"No {right} strikes to build a {strategy.value} from")
        legs.extend(plan_side(strategy, right, primary, strikes, spread_width, primary_role))
    return legs


async def _resolve_custom(
    session: AsyncSession, symbol: str, rules: ContractRules, direction: SignalDirection, legs_spec: Sequence[CustomLeg], *,
    spot: float, today: date, broker: str,
) -> ResolvedStructure:
    """Phase U: the deployment's own legs, each at its rule strike for its right, all on the
    deployment's expiry rule. Strike filters are not applied to a custom set (the legs are
    already exact); two legs resolving to one contract are refused rather than netted."""
    if not legs_spec:
        raise ContractResolutionError("CUSTOM structure has no legs - add them on the deployment")
    validate_custom_legs(legs_spec)
    underlying = master.underlying_of(symbol)
    underlying_symbol = master.INDEX_SYMBOLS.get(underlying, symbol.upper().strip())
    expiry: Optional[date] = None
    legs: List[ResolvedLeg] = []
    notes: List[str] = []
    lot_size = 0
    seen: Dict[str, str] = {}
    strikes_by_right: Dict[str, List[float]] = {}
    for spec in legs_spec:
        expiries = await master.expiries(session, underlying, broker=broker, instrument_type=spec.right, on_or_after=today)
        leg_expiry = select_expiry(expiries, rules.expiry_rule, today)
        if leg_expiry is None:
            raise ContractResolutionError(f"No {underlying} {spec.right} expiries on/after {today} in the instrument master")
        if expiry is not None and leg_expiry != expiry:
            raise ContractResolutionError(f"{underlying} CE/PE expiries differ ({expiry} vs {leg_expiry})")
        expiry = leg_expiry
        if spec.right not in strikes_by_right:
            strikes_by_right[spec.right] = await master.strikes(session, underlying, expiry, broker=broker, instrument_type=spec.right)
        strike = select_strike(strikes_by_right[spec.right], spot, spec.right, spec.strike_rule, spec.strike_offset)
        if strike is None:
            raise ContractResolutionError(f"No {underlying} {spec.right} strike for {spec.describe()} on {expiry}")
        record = await master.find_option(session, underlying, expiry, strike, spec.right, broker=broker)
        if record is None:
            raise ContractResolutionError(f"{underlying} {spec.right} {int(strike)} {expiry} missing from the master")
        if record.tradingsymbol in seen:
            raise ContractResolutionError(f"{spec.describe()} and {seen[record.tradingsymbol]} resolve to the same contract "
                                          f"{record.tradingsymbol} - combine or change them")
        seen[record.tradingsymbol] = spec.describe()
        lot_size = int(record.lot_size or 1)
        build = _short_leg if spec.role == "SHORT" else _long_leg
        legs.append(build(rules, record, underlying, underlying_symbol, spec.right, spec.ratio))
        notes.append(f"{spec.right}: {'sell' if spec.role == 'SHORT' else 'buy'} {spec.ratio}x {int(strike)} ({spec.describe()})")
    assert expiry is not None
    strikes = [float(l.contract.strike or 0.0) for l in legs]
    width = round(max(strikes) - min(strikes), 2) if len(strikes) > 1 else 0.0
    return ResolvedStructure(strategy=OptionStrategy.CUSTOM, legs=legs, underlying_symbol=underlying_symbol, lot_size=lot_size,
                             expiry=expiry, width_points=width, notes=notes)


async def resolve_structure(
    session: AsyncSession, symbol: str, rules: ContractRules, strategy: OptionStrategy, direction: SignalDirection, *,
    spread_width: int, spot: Optional[float], today: date, broker: str = master.UPSTOX_BROKER,
    chain_provider: Optional[ChainProvider] = None, custom_legs: Optional[Sequence[CustomLeg]] = None,
) -> ResolvedStructure:
    if not structure_direction_ok(strategy, direction):
        raise ContractResolutionError(f"{strategy.value} is not entered on a {direction.value} signal")
    if strategy in WIDTH_STRUCTURES and spread_width < 1:
        raise ContractResolutionError("spread_width must be at least one strike step")
    if spot is None or spot <= 0:
        raise ContractResolutionError(f"No spot price for {symbol} to build a {strategy.value} from")
    if strategy == OptionStrategy.CUSTOM:
        return await _resolve_custom(session, symbol, rules, direction, custom_legs or [], spot=spot, today=today, broker=broker)
    underlying = master.underlying_of(symbol)
    underlying_symbol = master.INDEX_SYMBOLS.get(underlying, symbol.upper().strip())

    sides, primary_role = structure_sides(strategy, direction)
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

        planned = plan_side(strategy, right, primary_strike, strikes, spread_width, primary_role)
        far_expiry: Optional[date] = None
        if strategy == OptionStrategy.CALENDAR_SPREAD:
            far_expiry = next((e for e in sorted(expiries) if e > expiry), None)
            if far_expiry is None:
                raise ContractResolutionError(f"No {underlying} {right} expiry after {expiry} for the calendar's long leg")
        for leg in planned:
            leg_expiry_date = far_expiry if leg.far_expiry else expiry
            rec = primary_rec if (leg.strike == primary_strike and not leg.far_expiry) else \
                await master.find_option(session, underlying, leg_expiry_date, leg.strike, right, broker=broker)
            if rec is None:
                raise ContractResolutionError(f"{underlying} {right} {int(leg.strike)} {leg_expiry_date} missing from the master")
            build = _short_leg if leg.role == "SHORT" else _long_leg
            legs.append(build(rules, rec, underlying, underlying_symbol, right, leg.ratio))
        if strategy != OptionStrategy.CALENDAR_SPREAD:
            # The (widest) wing or short-leg distance of this right; a naked leg adds nothing.
            width_points = max([width_points] + [abs(l.strike - primary_strike) for l in planned])
        if strategy == OptionStrategy.CALENDAR_SPREAD:
            notes.append(f"{right}: sell {int(primary_strike)} {expiry.isoformat()} / buy {int(primary_strike)} {far_expiry.isoformat()}")
        elif strategy in RATIO_SPREADS:
            side_word = "above" if right == "CE" else "below"
            notes.append(f"{right}: buy 1x {int(primary_strike)} / sell {RATIO_SHORTS}x {int(planned[1].strike)} ({int(width_points)} pts) - "
                         f"undefined risk {side_word} the outer breakeven")
        elif strategy == OptionStrategy.LONG_BUTTERFLY:
            notes.append(f"{right}: buy 1x {int(planned[0].strike)} / sell 2x {int(primary_strike)} / buy 1x {int(planned[2].strike)} "
                         f"({int(abs(primary_strike - planned[0].strike))} pts wings, debit)")
        elif uses_wings(strategy):
            notes.append(f"{right}: sell {int(primary_strike)} / buy {int(planned[1].strike)} ({int(abs(primary_strike - planned[1].strike))} pts wide)")
        elif primary_role == "SHORT":
            notes.append(f"{right}: sell {int(primary_strike)} (no wing - undefined risk, sized off the stop)")
        else:
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
        credit += (price if leg.role == "SHORT" else -price) * leg.ratio
        legs.append({"role": leg.role, "side": leg.side.value, "tradingsymbol": leg.contract.tradingsymbol,
                     "strike": leg.contract.strike, "right": leg.contract.right, "premium": price,
                     "expiry": leg.contract.expiry.isoformat(), "ratio": leg.ratio})
        if leg.contract.right:
            (short_strikes if leg.role == "SHORT" else long_strikes)[leg.contract.right] = float(leg.contract.strike or 0.0)
    credit = round(credit, 2)
    strategy = structure.strategy

    if is_payoff_priced(strategy):
        return _payoff_metrics(structure, legs, target_credit_pct, stop_credit_pct)

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


def _payoff_metrics(structure: ResolvedStructure, legs: List[dict], target_pct: Optional[float], stop_pct: Optional[float]) -> StructureMetrics:
    """Phase U: economics from the expiry payoff; exits as P&L per unit against the risk basis."""
    analysis = analyse([PayoffLeg(right=l["right"], role=l["role"], strike=float(l["strike"] or 0.0), premium=float(l["premium"]),
                                  ratio=int(l.get("ratio") or 1)) for l in legs])
    name = structure.strategy.value
    if analysis.peak <= 0:
        raise ContractResolutionError(f"{name} cannot profit at expiry at these premiums (best case {analysis.peak:g}/unit) - not entered")
    defined = analysis.defined_risk
    if defined and (analysis.max_loss or 0.0) <= 0:
        raise ContractResolutionError(f"{name} shows no loss at expiry at these premiums - quotes inconsistent, not entered")
    risk_basis = analysis.max_loss if defined else analysis.peak
    t_pct = target_pct if target_pct is not None else DEFAULT_TARGET_PAYOFF_PCT
    s_pct = stop_pct if stop_pct is not None else (DEFAULT_STOP_DEFINED_PCT if defined else DEFAULT_STOP_UNDEFINED_PCT)
    if defined:
        s_pct = min(s_pct, 100.0)
    target_ref = analysis.max_profit if analysis.max_profit is not None else analysis.peak
    pnl_target = round(target_ref * t_pct / 100.0, 2)
    pnl_stop = round(-risk_basis * s_pct / 100.0, 2)
    credit = analysis.net_credit
    debit = credit < 0
    if debit:
        paid = -credit
        target_value, stop_value = round(paid + pnl_target, 2), round(paid + pnl_stop, 2)     # worth levels
    else:
        target_value, stop_value = round(credit - pnl_target, 2), round(credit - pnl_stop, 2)  # value (cost to close) levels
    underlying_exits: Dict[str, float] = {}
    if analysis.breakevens:
        if not analysis.upside_defined:
            underlying_exits["above"] = max(analysis.breakevens)
        if not analysis.downside_defined:
            underlying_exits["below"] = min(analysis.breakevens)
    return StructureMetrics(
        net_credit=credit, max_profit=analysis.max_profit, max_loss=analysis.max_loss, breakevens=analysis.breakevens,
        target_value=target_value, stop_value=stop_value, short_strikes={}, legs=legs, debit=debit,
        risk_per_unit=analysis.max_loss if defined else -pnl_stop, defined_risk=defined,
        underlying_exits=underlying_exits, pnl_target=pnl_target, pnl_stop=pnl_stop,
    )


def describe_structure(strategy: OptionStrategy, spread_width: int, rules: ContractRules,
                       custom_legs: Optional[Sequence[CustomLeg]] = None) -> str:
    if strategy == OptionStrategy.SINGLE:
        return ""
    expiry = rules.expiry_rule.value.lower()
    otm = max(1, rules.strike_offset or 1)
    strike = rules.strike_rule.value if rules.strike_rule == StrikeRule.ATM else f"{rules.strike_rule.value}{rules.strike_offset}"
    if strategy == OptionStrategy.CALL_RATIO_SPREAD:
        return f"call ratio spread, buy 1 {strike} CE / sell {RATIO_SHORTS} {spread_width} step(s) higher (LONG; undefined risk above), {expiry} expiry"
    if strategy == OptionStrategy.PUT_RATIO_SPREAD:
        return f"put ratio spread, buy 1 {strike} PE / sell {RATIO_SHORTS} {spread_width} step(s) lower (SHORT; undefined risk below), {expiry} expiry"
    if strategy == OptionStrategy.LONG_BUTTERFLY:
        return f"long butterfly, buy wing / sell 2 ATM / buy wing, {spread_width} step(s) wings (CE on LONG, PE on SHORT; debit), {expiry} expiry"
    if strategy == OptionStrategy.CUSTOM:
        legs = ", ".join(leg.describe() for leg in (custom_legs or [])) or "no legs"
        return f"custom structure: {legs}; {expiry} expiry, priced off the expiry payoff"
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
    return f"{name}, short {strike}, wing {spread_width} step(s), {expiry} expiry"
