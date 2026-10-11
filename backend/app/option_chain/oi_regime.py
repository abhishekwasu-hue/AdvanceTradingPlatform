"""OI regime: the OI banner's math, ported from the Trade repo's `oi_analysis.py` (OI Banner spec O1).

Pure functions only - no database, no broker call, no clock. The collector (O2) fetches the chain and the history, the
banner API (O3) and the alert rules (O4) read what `evaluate_snapshot` returns, and the strategy engine (O5) calls the
gates. Every threshold is a parameter; the defaults live in `OIRegimeSettings` (per tenant / per underlying, editable
on the dashboard). Strike steps are never constants here: a setting, or the chain's own strike spacing.

Where this port deliberately differs from the Trade source (each is listed in docs/design/OI_BANNER.md):
* `check_oi_confirmation` fails closed on missing data (Trade skipped the gate), per the spec's gate rule.
* Strictness "B" means "own direction, Strong": Trade compared against a label format that no longer exists, so its
  strict mode could never pass.
* A stale or untimed PCR fails closed (Trade used a value whose time it could not parse).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Literal, Mapping, Optional, Sequence, Tuple, TypeVar

from pydantic import BaseModel, Field, model_validator

from app.brokers.models import OptionChain, OptionChainRow
from app.option_chain.analysis import compute_max_pain

_EPS = 1e-9


class Direction(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"
    MIXED = "MIXED"
    SIDEWAYS = "SIDEWAYS"          # PCR's middle band only


class Strength(str, Enum):
    STRONG = "Strong"
    WEAKENING = "Weakening"


class OIClass(str, Enum):
    """What one side's (calls' or puts') OI and premium did since the previous snapshot."""
    WRITING = "Writing"
    BUYING = "Buying"
    SHORT_COVERING = "Short covering"
    LONG_UNWINDING = "Long unwinding"
    FLAT = "Flat/unclear"
    INSUFFICIENT_DATA = "Insufficient data"


class PcrBand(str, Enum):
    OVERSOLD = "OVERSOLD"
    MILD_BEARISH = "MILD_BEARISH"
    SIDEWAYS = "SIDEWAYS"
    MILD_BULLISH = "MILD_BULLISH"
    OVERBOUGHT = "OVERBOUGHT"


# ---------------------------------------------------------------- settings

class PcrBands(BaseModel):
    """The five PCR bands. Edges as in the source: < oversold_below, < mild_bearish_below, <= sideways_max,
    <= mild_bullish_max, above that overbought."""
    oversold_below: float = 0.70
    mild_bearish_below: float = 0.90
    sideways_max: float = 1.0
    mild_bullish_max: float = 1.3

    @model_validator(mode="after")
    def _ascending(self) -> "PcrBands":
        edges = [self.oversold_below, self.mild_bearish_below, self.sideways_max, self.mild_bullish_max]
        if any(e <= 0 for e in edges) or edges != sorted(edges) or len(set(edges)) != len(edges):
            raise ValueError("PCR band edges must be positive and strictly ascending")
        return self


ALERT_TYPES = ("DIRECTION_CHANGE", "STABLE_FLIP", "STRENGTH_CHANGE", "PCR_BAND", "MAX_PAIN_MOVE", "OI_WALL", "DTE", "COLLECTOR_STALE")


class OIAlertSettings(BaseModel):
    """O4: which banner changes notify, how often, and when not (all off until the tenant turns alerts on)."""
    enabled: bool = False
    types: List[str] = Field(default_factory=lambda: list(ALERT_TYPES))
    cooldown_minutes: int = Field(default=15, ge=0, le=1440)            # per alert type and underlying
    max_pain_strikes: int = Field(default=2, ge=1, le=50)               # a move of this many strikes notifies
    dte_milestones: List[int] = Field(default_factory=lambda: [1, 0])   # expiry tomorrow / today
    quiet_start: Optional[str] = Field(default=None, pattern=r"^\d{2}:\d{2}$")   # IST, e.g. "12:00"
    quiet_end: Optional[str] = Field(default=None, pattern=r"^\d{2}:\d{2}$")

    @model_validator(mode="after")
    def _known(self) -> "OIAlertSettings":
        unknown = [t for t in self.types if t not in ALERT_TYPES]
        if unknown:
            raise ValueError(f"unknown alert types {unknown}")
        return self


class OIRegimeSettings(BaseModel):
    """Defaults for one tenant / underlying. Layer platform -> tenant -> underlying with `resolve_settings`."""
    strike_step: Optional[float] = Field(default=None, gt=0)      # None: the chain's own strike spacing
    atm_range: int = Field(default=6, ge=1, le=50)                 # strikes either side of ATM in the totals
    slot_minutes: int = Field(default=5, ge=1, le=60)
    history_window: int = Field(default=5, ge=1, le=50)            # earlier snapshots the hysteresis reads
    oi_threshold_pct: float = Field(default=2.0, ge=0)
    premium_threshold_pct: float = Field(default=1.0, ge=0)
    rotation_min_change_pct: float = Field(default=2.0, ge=0)
    lookback_for_strength: int = Field(default=3, ge=1)
    confirm_count: int = Field(default=3, ge=1)
    reconcile_significance_pct: float = Field(default=10.0, ge=0)
    pcr_bands: PcrBands = Field(default_factory=PcrBands)
    stale_after_minutes: float = Field(default=15.0, gt=0)
    psychological_round_to: Optional[float] = Field(default=None, gt=0)
    psychological_step_multiple: int = Field(default=10, ge=1)    # used when round_to is not set
    swing_max_opposing: int = Field(default=1, ge=0)
    oi_confirmation_strictness: Literal["A", "B"] = "A"
    pcr_bullish_min: Optional[float] = Field(default=None, gt=0)  # PCR gate limits: None = gate not configured
    pcr_bearish_max: Optional[float] = Field(default=None, gt=0)
    iv_change_max_pct: Optional[float] = None                      # IV gate limit: None = gate not configured
    iv_lookback_days: int = Field(default=10, ge=1)
    iv_max_age_minutes: float = Field(default=20.0, gt=0)
    marubozu_threshold: float = Field(default=0.8, gt=0, le=1)
    alerts: OIAlertSettings = Field(default_factory=OIAlertSettings)


def resolve_settings(*layers: Optional[Mapping[str, Any]]) -> OIRegimeSettings:
    """Platform defaults, then each override layer in order (tenant, then underlying). Unknown keys are refused."""
    merged: Dict[str, Any] = {}
    for layer in layers:
        for key, value in (layer or {}).items():
            if key not in OIRegimeSettings.model_fields:
                raise ValueError(f"unknown OI setting {key!r}")
            if key in ("pcr_bands", "alerts") and isinstance(value, Mapping):
                value = {**merged.get(key, {}), **value}
            merged[key] = value
    return OIRegimeSettings.model_validate(merged)


# ---------------------------------------------------------------- signals

@dataclass(frozen=True)
class OISignal:
    """The hysteresis-confirmed ("stable") OI-diff signal: a direction and, for BULLISH/BEARISH, a strength."""
    direction: Direction
    strength: Optional[Strength] = None

    @property
    def label(self) -> str:
        return f"{self.direction.value} ({self.strength.value})" if self.strength else self.direction.value

    @classmethod
    def parse(cls, label: Optional[str]) -> Optional["OISignal"]:
        """Reads a stored label (also the Trade repo's emoji labels, for golden fixtures)."""
        if not label:
            return None
        direction = Direction.BULLISH if "BULLISH" in label else Direction.BEARISH if "BEARISH" in label else Direction.NEUTRAL
        strength = None
        if direction != Direction.NEUTRAL:
            strength = Strength.WEAKENING if "Weakening" in label else Strength.STRONG if "Strong" in label else None
        return cls(direction, strength)


NEUTRAL_SIGNAL = OISignal(Direction.NEUTRAL)


@dataclass(frozen=True)
class SnapshotPoint:
    """One earlier snapshot as the hysteresis reads it (oldest first in a sequence)."""
    diff: float
    total_put_oi: float
    total_call_oi: float
    signal: OISignal


def find_psychological_level(price: float, direction: str, round_to: float) -> float:
    """The next round number in the trade's direction: above for BEARISH (resistance), below otherwise (support).
    A price exactly on a level steps to the next one."""
    if round_to <= 0:
        raise ValueError("round_to must be positive")
    if direction == Direction.BEARISH.value:
        level = math.ceil(price / round_to) * round_to
        return level + round_to if level == price else level
    level = math.floor(price / round_to) * round_to
    return level - round_to if level == price else level


def is_genuine_rotation(call_oi_now: float, call_oi_prev: Optional[float], put_oi_now: float, put_oi_prev: Optional[float],
                        min_change_pct: float = 2.0) -> Optional[Direction]:
    """Calls and puts moving in opposite directions, each by at least `min_change_pct`. Calls up and puts down is
    BEARISH; calls down and puts up is BULLISH; anything else (or no previous value) is None."""
    if not call_oi_prev or not put_oi_prev:
        return None
    call_change = (call_oi_now - call_oi_prev) / call_oi_prev * 100
    put_change = (put_oi_now - put_oi_prev) / put_oi_prev * 100
    if abs(call_change) < min_change_pct or abs(put_change) < min_change_pct:
        return None
    if call_change > 0 and put_change < 0:
        return Direction.BEARISH
    if call_change < 0 and put_change > 0:
        return Direction.BULLISH
    return None


def rotation_confirmed_for_2_snapshots(history: Sequence[Tuple[float, float]], min_change_pct: float = 2.0) -> Optional[Direction]:
    """`history` is (call_oi, put_oi) oldest first, at least three rows: both of the last two comparisons must show the
    same rotation."""
    if len(history) < 3:
        return None
    first = is_genuine_rotation(history[-2][0], history[-3][0], history[-2][1], history[-3][1], min_change_pct)
    second = is_genuine_rotation(history[-1][0], history[-2][0], history[-1][1], history[-2][1], min_change_pct)
    return first if first is not None and first == second else None


def compute_oi_signal_with_hysteresis(diff: float, put_oi: float, call_oi: float, recent: Sequence[SnapshotPoint],
                                      lookback_for_strength: int = 3, confirm_count: int = 3,
                                      rotation_min_change_pct: float = 2.0) -> OISignal:
    """The stable OI-diff signal. diff = total put OI - total call OI over the ATM window.

    * Strength: the leading side's OI against the snapshot `lookback_for_strength` back (or the oldest one held).
    * Same direction as the previous snapshot: the new reading at once.
    * A new direction needs `confirm_count` consecutive diffs of the new sign AND a call/put rotation confirmed on two
      consecutive comparisons. Until then the old direction is shown as Weakening - never as Strong.
    """
    if len(recent) >= lookback_for_strength:
        baseline: Optional[SnapshotPoint] = recent[-lookback_for_strength]
    else:
        baseline = recent[0] if recent else None

    if diff > 0:
        growing = baseline is not None and put_oi > baseline.total_put_oi
        raw = OISignal(Direction.BULLISH, Strength.STRONG if growing else Strength.WEAKENING)
    elif diff < 0:
        growing = baseline is not None and call_oi > baseline.total_call_oi
        raw = OISignal(Direction.BEARISH, Strength.STRONG if growing else Strength.WEAKENING)
    else:
        raw = NEUTRAL_SIGNAL
    if not recent:
        return raw

    previous = recent[-1].signal
    prev_direction = previous.direction if previous.direction in (Direction.BULLISH, Direction.BEARISH) else Direction.NEUTRAL
    if raw.direction == prev_direction:
        return raw
    held = OISignal(prev_direction, Strength.WEAKENING) if prev_direction != Direction.NEUTRAL else previous
    if len(recent) < confirm_count - 1:
        return held

    tail = list(recent[len(recent) - (confirm_count - 1):]) if confirm_count > 1 else []
    diffs = [p.diff for p in tail] + [diff]
    same_new_direction = all((d > 0) if raw.direction == Direction.BULLISH else (d < 0) for d in diffs)
    rotation = rotation_confirmed_for_2_snapshots(
        [(p.total_call_oi, p.total_put_oi) for p in recent[-2:]] + [(call_oi, put_oi)], rotation_min_change_pct)
    return raw if same_new_direction and rotation == raw.direction else held


def classify_oi_price_action(oi_now: Optional[float], oi_prev: Optional[float], premium_now: Optional[float],
                             premium_prev: Optional[float], oi_threshold_pct: float = 2.0,
                             premium_threshold_pct: float = 1.0) -> OIClass:
    """The OI-premium matrix for one side: OI up + premium down = writing, OI up + premium up = buying, OI down +
    premium up = short covering, OI down + premium down = long unwinding. Moves under either threshold are flat."""
    if oi_now is None or premium_now is None or not oi_prev or not premium_prev:
        return OIClass.INSUFFICIENT_DATA
    oi_change = (oi_now - oi_prev) / oi_prev * 100
    premium_change = (premium_now - premium_prev) / premium_prev * 100
    oi_up, oi_down = oi_change >= oi_threshold_pct, oi_change <= -oi_threshold_pct
    premium_up, premium_down = premium_change >= premium_threshold_pct, premium_change <= -premium_threshold_pct
    if oi_up and premium_down:
        return OIClass.WRITING
    if oi_up and premium_up:
        return OIClass.BUYING
    if oi_down and premium_up:
        return OIClass.SHORT_COVERING
    if oi_down and premium_down:
        return OIClass.LONG_UNWINDING
    return OIClass.FLAT


# (side, class) -> (the bias it implies, the phrase in the banner)
_ACTIVITY: Dict[Tuple[str, OIClass], Tuple[Direction, str]] = {
    ("Put", OIClass.WRITING): (Direction.BULLISH, "Put writing rising"),
    ("Put", OIClass.BUYING): (Direction.BEARISH, "Put buying rising"),
    ("Put", OIClass.SHORT_COVERING): (Direction.BEARISH, "Put short covering"),
    ("Put", OIClass.LONG_UNWINDING): (Direction.BULLISH, "Put unwinding"),
    ("Call", OIClass.WRITING): (Direction.BEARISH, "Call writing rising"),
    ("Call", OIClass.BUYING): (Direction.BULLISH, "Call buying rising"),
    ("Call", OIClass.SHORT_COVERING): (Direction.BULLISH, "Call short covering"),
    ("Call", OIClass.LONG_UNWINDING): (Direction.BEARISH, "Call unwinding"),
}
MIXED_MESSAGE = "Mixed signals — no clear direction, stay cautious"
NO_ACTIVITY_MESSAGE = "No clear activity"
INSUFFICIENT_HISTORY_MESSAGE = "Insufficient data — history builds through the session"


@dataclass(frozen=True)
class OIPriceSignal:
    direction: Direction
    message: str
    momentum: str                  # the activity phrases joined, e.g. "Put writing rising + Call short covering"
    put_class: OIClass
    call_class: OIClass


def generate_oi_price_signal(put_class: OIClass, call_class: OIClass, underlying: str) -> OIPriceSignal:
    """Both sides' activity combined into the banner's direction and headline."""
    bullish: List[str] = []
    bearish: List[str] = []
    for side, cls in (("Put", put_class), ("Call", call_class)):
        found = _ACTIVITY.get((side, cls))
        if found is not None:
            (bullish if found[0] == Direction.BULLISH else bearish).append(found[1])
    if bullish and not bearish:
        momentum = " + ".join(bullish)
        return OIPriceSignal(Direction.BULLISH, f"{momentum} → avoid shorting calls; {underlying} bias bullish", momentum, put_class, call_class)
    if bearish and not bullish:
        momentum = " + ".join(bearish)
        return OIPriceSignal(Direction.BEARISH, f"{momentum} → avoid shorting puts; {underlying} bias bearish", momentum, put_class, call_class)
    if bullish and bearish:
        return OIPriceSignal(Direction.MIXED, MIXED_MESSAGE, " + ".join(bullish + bearish), put_class, call_class)
    return OIPriceSignal(Direction.NEUTRAL, NO_ACTIVITY_MESSAGE, "", put_class, call_class)


def _opposite(a: Direction, b: Direction) -> bool:
    return {a, b} == {Direction.BULLISH, Direction.BEARISH}


def reconcile_with_diff_level(signal: OIPriceSignal, diff: float, total_call_oi: float, total_put_oi: float,
                              stable_signal: Optional[OISignal] = None, significance_pct: float = 10.0) -> OIPriceSignal:
    """The banner's momentum (one snapshot back) against the slower readings. Contradicting the hysteresis-confirmed
    signal, or an overall put/call imbalance of at least `significance_pct` of total OI, turns it MIXED."""
    def mixed(message: str) -> OIPriceSignal:
        return OIPriceSignal(Direction.MIXED, message, signal.momentum, signal.put_class, signal.call_class)

    if stable_signal is not None and _opposite(signal.direction, stable_signal.direction):
        return mixed(f"Mixed signals — recent momentum ({signal.momentum}) and the confirmed trend "
                     f"({stable_signal.direction.value}) disagree — stay cautious")
    total = total_call_oi + total_put_oi
    if total <= 0:
        return signal
    significance = abs(diff) / total * 100
    if significance >= significance_pct and ((signal.direction == Direction.BEARISH and diff > 0)
                                             or (signal.direction == Direction.BULLISH and diff < 0)):
        side = "Put" if diff > 0 else "Call"
        return mixed(f"Mixed signals — recent momentum ({signal.momentum}) and the overall level "
                     f"(diff: {side} OI higher, {diff:+,.0f}) disagree — stay cautious")
    return signal


# ---------------------------------------------------------------- PCR

_BAND_BIAS = {PcrBand.OVERSOLD: Direction.BULLISH, PcrBand.MILD_BEARISH: Direction.BEARISH, PcrBand.SIDEWAYS: Direction.SIDEWAYS,
              PcrBand.MILD_BULLISH: Direction.BULLISH, PcrBand.OVERBOUGHT: Direction.BEARISH}
_BAND_LABEL = {
    PcrBand.OVERSOLD: "Oversold — possible reversal (bear-trap risk)",
    PcrBand.MILD_BEARISH: "Mildly bearish (not yet oversold)",
    PcrBand.SIDEWAYS: "Sideways (range-bound)",
    PcrBand.MILD_BULLISH: "Mildly bullish (not yet overbought)",
    PcrBand.OVERBOUGHT: "Overbought — possible reversal (bull-trap risk)",
}


def pcr_band(pcr: Optional[float], bands: Optional[PcrBands] = None) -> Optional[PcrBand]:
    if pcr is None:
        return None
    b = bands or PcrBands()
    if pcr < b.oversold_below:
        return PcrBand.OVERSOLD
    if pcr < b.mild_bearish_below:
        return PcrBand.MILD_BEARISH
    if pcr <= b.sideways_max:
        return PcrBand.SIDEWAYS
    if pcr <= b.mild_bullish_max:
        return PcrBand.MILD_BULLISH
    return PcrBand.OVERBOUGHT


def compute_pcr_signal(total_put_oi: float, total_call_oi: float, bands: Optional[PcrBands] = None) -> Tuple[Optional[float], Direction]:
    """PCR rounded to two places and its band's bias. The extremes are contrarian (oversold reads bullish)."""
    if not total_call_oi:
        return None, Direction.NEUTRAL
    pcr = round(total_put_oi / total_call_oi, 2)
    band = pcr_band(pcr, bands)
    return pcr, _BAND_BIAS[band] if band else Direction.NEUTRAL


def compute_pcr_zone_label(pcr: Optional[float], bands: Optional[PcrBands] = None) -> str:
    band = pcr_band(pcr, bands)
    return _BAND_LABEL[band] if band else "Insufficient data"


# ---------------------------------------------------------------- positioning, max pain, rollover

@dataclass(frozen=True)
class OIPriceMatrix:
    category: str                  # LONG_BUILDUP / SHORT_BUILDUP / SHORT_COVERING / LONG_UNWINDING / INSUFFICIENT_DATA
    bias: Direction
    strength: str                  # "Strong" for new positions, "Weak/Temporary" for closing ones


def compute_oi_price_matrix(total_oi: Optional[float], prev_total_oi: Optional[float], price: Optional[float],
                            prev_price: Optional[float]) -> OIPriceMatrix:
    if total_oi is None or prev_total_oi is None or price is None or prev_price is None:
        return OIPriceMatrix("INSUFFICIENT_DATA", Direction.NEUTRAL, "-")
    price_up, oi_up = price > prev_price, total_oi > prev_total_oi
    if price_up and oi_up:
        return OIPriceMatrix("LONG_BUILDUP", Direction.BULLISH, "Strong")
    if oi_up:
        return OIPriceMatrix("SHORT_BUILDUP", Direction.BEARISH, "Strong")
    if price_up:
        return OIPriceMatrix("SHORT_COVERING", Direction.BULLISH, "Weak/Temporary")
    return OIPriceMatrix("LONG_UNWINDING", Direction.BEARISH, "Weak/Temporary")


def max_pain(rows: Sequence[OptionChainRow]) -> Optional[float]:
    """ATP's max pain (app.option_chain.analysis), over the strikes in ascending order so a tie resolves to the
    lowest strike as in the source."""
    return compute_max_pain(sorted(rows, key=lambda r: r.strike))


def _row_at(rows: Sequence[OptionChainRow], strike: float) -> Optional[OptionChainRow]:
    return next((r for r in rows if abs(r.strike - strike) < _EPS), None)


@dataclass(frozen=True)
class RolloverProxy:
    rollover_pct: Optional[float]      # share of OI already in the next expiry
    cost_of_carry: Optional[float]     # next-expiry synthetic future minus near-expiry synthetic future
    bias: Direction
    near_expiry_total_oi: float
    next_expiry_total_oi: float


def compute_rollover_proxy(near: OptionChain, next_: OptionChain, atm_strike: float) -> Optional[RolloverProxy]:
    """A rollover-like reading from options alone: the OI share in the next expiry, and the cost of carry between the
    two expiries' put-call-parity synthetic futures at the ATM strike (positive reads bullish)."""
    if not near.rows or not next_.rows:
        return None

    def total(chain: OptionChain) -> float:
        return sum((r.call_oi or 0) + (r.put_oi or 0) for r in chain.rows)

    def synthetic(chain: OptionChain) -> Optional[float]:
        row = _row_at(chain.rows, atm_strike)
        if row is None or row.call_ltp is None or row.put_ltp is None:
            return None
        return row.strike + row.call_ltp - row.put_ltp

    near_total, next_total = total(near), total(next_)
    both = near_total + next_total
    rollover_pct = round(next_total / both * 100, 1) if both > 0 else None
    near_syn, next_syn = synthetic(near), synthetic(next_)
    carry = round(next_syn - near_syn, 2) if near_syn is not None and next_syn is not None else None
    bias = Direction.NEUTRAL if not carry else Direction.BULLISH if carry > 0 else Direction.BEARISH
    return RolloverProxy(rollover_pct, carry, bias, near_total, next_total)


# ---------------------------------------------------------------- gates (fail closed)

def check_oi_diff_entry_gate(direction: str, signal: Optional[OISignal]) -> bool:
    """Own direction Strong, or the opposite direction Weakening (an early turn), passes. Own direction Weakening,
    NEUTRAL or no data fails. Exits never consult a gate (ADR-0004)."""
    if signal is None or direction not in (Direction.BULLISH.value, Direction.BEARISH.value):
        return False
    weakening = signal.strength == Strength.WEAKENING
    if signal.direction.value == direction:
        return not weakening
    if _opposite(signal.direction, Direction(direction)):
        return weakening
    return False


def check_oi_confirmation(direction: str, signal: Optional[OISignal], strictness: str = "A") -> Tuple[bool, str]:
    """A: block only an active conflict (the opposite direction). B: pass only own direction, Strong.
    No data fails closed."""
    if signal is None:
        return False, "OI data unavailable or stale"
    if direction not in (Direction.BULLISH.value, Direction.BEARISH.value):
        return True, signal.label
    if strictness == "B":
        return signal == OISignal(Direction(direction), Strength.STRONG), signal.label
    return not _opposite(signal.direction, Direction(direction)), signal.label


@dataclass(frozen=True)
class SwingGateDetail:
    supporting: List[Tuple[str, Direction]]
    opposing: List[Tuple[str, Direction]]
    total_signals: int


def swing_oi_gate(direction: str, matrix: Optional[OIPriceMatrix], pcr_bias: Optional[Direction], max_pain_strike: Optional[float],
                  price: Optional[float], rollover: Optional[RolloverProxy], max_opposing: int = 1) -> Tuple[bool, SwingGateDetail]:
    """Counts the non-neutral readings against the trade's direction; blocks only when more than `max_opposing` oppose
    (a conflict filter, not strict confirmation). As in the source, a SIDEWAYS PCR counts against either direction."""
    signals: List[Tuple[str, Direction]] = []
    if matrix is not None and matrix.bias != Direction.NEUTRAL:
        signals.append(("OI-price matrix", matrix.bias))
    if pcr_bias is not None and pcr_bias != Direction.NEUTRAL:
        signals.append(("PCR (contrarian)", pcr_bias))
    if max_pain_strike is not None and price:
        if max_pain_strike != price:
            signals.append(("Max pain", Direction.BEARISH if max_pain_strike < price else Direction.BULLISH))
    if rollover is not None and rollover.bias != Direction.NEUTRAL:
        signals.append(("Rollover", rollover.bias))
    opposing = [s for s in signals if s[1].value != direction]
    supporting = [s for s in signals if s[1].value == direction]
    return len(opposing) <= max_opposing, SwingGateDetail(supporting, opposing, len(signals))


@dataclass(frozen=True)
class WallCheck:
    target_strike: float
    side: str
    current_oi: Optional[float] = None
    baseline_oi: Optional[float] = None
    chg_oi: Optional[float] = None
    reason: str = ""


def check_oi_wall_confirmation(rows: Sequence[OptionChainRow], level: float, direction: str, step: float,
                               baseline_oi: Optional[float]) -> Tuple[bool, WallCheck]:
    """An OI wall at the strike nearest `level`: BEARISH needs call OI added since the day's baseline (resistance
    building), BULLISH needs put OI added. `baseline_oi` is that side's first OI of the day at the strike (None: no
    baseline yet, so no change can be shown)."""
    if step <= 0:
        raise ValueError("step must be positive")
    target = round(level / step) * step
    side = "CE" if direction == Direction.BEARISH.value else "PE"
    row = _row_at(rows, target)
    if row is None:
        return False, WallCheck(target, side, reason="strike not in the chain")
    current = float((row.call_oi if side == "CE" else row.put_oi) or 0)
    baseline = current if baseline_oi is None else float(baseline_oi)
    change = current - baseline
    return change > 0, WallCheck(target, side, current, baseline, change)


def is_stale(data_time: Optional[datetime], now: datetime, max_age_minutes: float) -> bool:
    """No timestamp counts as stale: a gate must not act on data it cannot date."""
    return data_time is None or (now - data_time).total_seconds() / 60 > max_age_minutes


def check_pcr_gate(pcr: Optional[float], direction: str, bullish_min: float, bearish_max: float) -> Tuple[bool, Optional[float], str]:
    """PCR below `bullish_min` blocks a bullish entry, above `bearish_max` a bearish one. Missing (or stale, which the
    caller passes as None) data blocks."""
    if pcr is None:
        return False, None, "PCR data unavailable or stale"
    if direction == Direction.BULLISH.value and pcr < bullish_min:
        return False, pcr, f"PCR {pcr} < {bullish_min} — not enough support for a bullish entry"
    if direction == Direction.BEARISH.value and pcr > bearish_max:
        return False, pcr, f"PCR {pcr} > {bearish_max} — not enough support for a bearish entry"
    return True, pcr, "PCR gate passed"


# ---------------------------------------------------------------- IV change gate

def body_ratio(open_: float, high: float, low: float, close: float) -> float:
    """|close - open| / (high - low): 0 for an all-wick day, 1 for a marubozu. A day with no range is 0."""
    day_range = high - low
    return abs(close - open_) / day_range if day_range > 0 else 0.0


def is_sideways_day(open_: float, high: float, low: float, close: float, marubozu_threshold: float = 0.8) -> bool:
    return body_ratio(open_, high, low, close) < marubozu_threshold


def atm_iv(chain: OptionChain) -> Optional[float]:
    """Mean of the call and put IV at the strike nearest the underlying."""
    if not chain.rows or not chain.underlying_ltp:
        return None
    ltp = chain.underlying_ltp
    nearest = min(chain.rows, key=lambda r: abs(r.strike - ltp))
    ivs = [iv for iv in (nearest.call_iv, nearest.put_iv) if iv is not None]
    return sum(ivs) / len(ivs) if ivs else None


@dataclass(frozen=True)
class DayIv:
    """A past session: its last ATM IV and the underlying's daily candle (for the sideways test)."""
    day: date
    atm_iv: Optional[float]
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class IvChange:
    today_iv: float
    baseline_avg_iv: float
    change_pct: float
    days_in_baseline: int


def iv_change_from_average(today_iv: Optional[float], history: Sequence[DayIv], today: date, lookback_days: int = 10,
                           marubozu_threshold: float = 0.8) -> Optional[IvChange]:
    """Today's ATM IV against the average over the last `lookback_days` sideways sessions before today (trending days
    would inflate the baseline). None when there is no sideways history yet."""
    if today_iv is None:
        return None
    sideways = sorted((d for d in history if d.day < today and is_sideways_day(d.open, d.high, d.low, d.close, marubozu_threshold)),
                      key=lambda d: d.day)[-lookback_days:]
    ivs = [d.atm_iv for d in sideways if d.atm_iv is not None]
    if not ivs:
        return None
    baseline = sum(ivs) / len(ivs)
    if baseline <= 0:
        return None
    return IvChange(today_iv, baseline, (today_iv - baseline) / baseline * 100, len(ivs))


def check_iv_change_gate(change: Optional[IvChange], max_change_pct: float) -> Tuple[bool, Optional[float], str]:
    """A jump of `max_change_pct` or more over the sideways baseline blocks new entries in either direction
    (breakout risk). No data, stale data or no sideways history blocks."""
    if change is None:
        return False, None, "IV data unavailable, stale, or not enough sideways history yet"
    if change.change_pct >= max_change_pct:
        return False, change.change_pct, (
            f"ATM IV {change.today_iv:.1f}% vs {change.baseline_avg_iv:.1f}% average over {change.days_in_baseline} sideways "
            f"sessions ({change.change_pct:+.1f}%) — above the {max_change_pct:.0f}% limit, breakout risk; no new entries")
    return True, change.change_pct, "IV gate passed"


# ---------------------------------------------------------------- snapshots and the banner

def compute_dte(expiry: Optional[str], today: date) -> Tuple[Optional[date], Optional[int]]:
    """(expiry date, days to expiry) from the chain's ISO expiry; (None, None) when absent or unreadable."""
    if not expiry:
        return None, None
    try:
        day = date.fromisoformat(expiry[:10])
    except ValueError:
        return None, None
    return day, (day - today).days


def infer_strike_step(strikes: Sequence[float]) -> Optional[float]:
    """The smallest gap between listed strikes (the step near the money)."""
    ordered = sorted({round(s, 6) for s in strikes})
    gaps = [b - a for a, b in zip(ordered, ordered[1:]) if b - a > _EPS]
    return round(min(gaps), 6) if gaps else None


def resolve_strike_step(settings: OIRegimeSettings, chain: OptionChain) -> Optional[float]:
    return settings.strike_step or infer_strike_step([r.strike for r in chain.rows])


def psychological_round_to(settings: OIRegimeSettings, strike_step: float) -> float:
    return settings.psychological_round_to or strike_step * settings.psychological_step_multiple


@dataclass(frozen=True)
class ChainTotals:
    """The ATM window of one chain: totals of OI and premium, and each strike's OI."""
    underlying_price: float
    atm_strike: float
    strike_step: float
    total_call_oi: float
    total_put_oi: float
    total_call_premium: float
    total_put_premium: float
    strikes: Tuple[Tuple[float, float, float], ...]        # (strike, call OI, put OI)

    @property
    def diff(self) -> float:
        return self.total_put_oi - self.total_call_oi


def summarise_chain(chain: OptionChain, strike_step: float, atm_range: int) -> Optional[ChainTotals]:
    """ATM = the underlying rounded to the step; the window is `atm_range` steps either side."""
    if chain.underlying_ltp is None or strike_step <= 0:
        return None
    atm = round(chain.underlying_ltp / strike_step) * strike_step
    window = [r for r in chain.rows if abs(r.strike - atm) <= atm_range * strike_step + _EPS]
    return ChainTotals(
        underlying_price=chain.underlying_ltp, atm_strike=atm, strike_step=strike_step,
        total_call_oi=sum(r.call_oi or 0 for r in window), total_put_oi=sum(r.put_oi or 0 for r in window),
        total_call_premium=sum(r.call_ltp or 0 for r in window), total_put_premium=sum(r.put_ltp or 0 for r in window),
        strikes=tuple((r.strike, r.call_oi or 0, r.put_oi or 0) for r in sorted(window, key=lambda r: r.strike)))


def slot_start(moment: datetime, slot_minutes: int = 5) -> datetime:
    """The start of the collector slot containing `moment` (one snapshot per slot)."""
    return moment.replace(minute=(moment.minute // slot_minutes) * slot_minutes, second=0, microsecond=0)


T = TypeVar("T")


def aggregate_history(points: Sequence[T], interval_minutes: int, slot_of: Callable[[T], datetime]) -> List[Tuple[datetime, T]]:
    """The history in `interval_minutes` buckets, newest first. OI is a level, not a flow: each bucket keeps its last
    snapshot (like a candle's close), never a sum."""
    if interval_minutes <= 0:
        raise ValueError("interval_minutes must be positive")
    buckets: Dict[datetime, T] = {}
    for point in sorted(points, key=slot_of):
        moment = slot_of(point)
        floor = moment - timedelta(minutes=moment.minute % interval_minutes, seconds=moment.second, microseconds=moment.microsecond)
        buckets[floor] = point
    return sorted(buckets.items(), key=lambda kv: kv[0], reverse=True)


@dataclass(frozen=True)
class BannerState:
    underlying: str
    direction: Direction
    message: str
    put_class: OIClass
    call_class: OIClass
    stable_signal: OISignal
    diff: float
    delta_diff: float
    pcr: Optional[float]
    pcr_band: Optional[PcrBand]
    pcr_label: str
    atm_strike: float
    underlying_price: float
    max_pain: Optional[float]
    expiry: Optional[date]
    dte: Optional[int]
    first_of_day: bool


def evaluate_snapshot(underlying: str, current: ChainTotals, previous: Optional[ChainTotals], history: Sequence[SnapshotPoint],
                      settings: OIRegimeSettings, *, max_pain_strike: Optional[float] = None, expiry: Optional[str] = None,
                      today: Optional[date] = None) -> BannerState:
    """One snapshot's banner. `previous` is the day's previous snapshot (None for the first of the day); `history` is
    the day's earlier snapshots, oldest first."""
    recent = list(history)[-settings.history_window:]
    stable = compute_oi_signal_with_hysteresis(current.diff, current.total_put_oi, current.total_call_oi, recent,
                                               settings.lookback_for_strength, settings.confirm_count,
                                               settings.rotation_min_change_pct)
    prev = previous
    put_class = classify_oi_price_action(current.total_put_oi, prev.total_put_oi if prev else None, current.total_put_premium,
                                         prev.total_put_premium if prev else None, settings.oi_threshold_pct, settings.premium_threshold_pct)
    call_class = classify_oi_price_action(current.total_call_oi, prev.total_call_oi if prev else None, current.total_call_premium,
                                          prev.total_call_premium if prev else None, settings.oi_threshold_pct, settings.premium_threshold_pct)
    signal = reconcile_with_diff_level(generate_oi_price_signal(put_class, call_class, underlying), current.diff,
                                       current.total_call_oi, current.total_put_oi, stable, settings.reconcile_significance_pct)
    message = INSUFFICIENT_HISTORY_MESSAGE if prev is None else signal.message
    pcr, _ = compute_pcr_signal(current.total_put_oi, current.total_call_oi, settings.pcr_bands)
    expiry_day, dte = compute_dte(expiry, today) if today is not None else (None, None)
    return BannerState(
        underlying=underlying, direction=signal.direction, message=message, put_class=put_class, call_class=call_class,
        stable_signal=stable, diff=current.diff, delta_diff=current.diff - recent[-1].diff if recent else 0.0,
        pcr=pcr, pcr_band=pcr_band(pcr, settings.pcr_bands), pcr_label=compute_pcr_zone_label(pcr, settings.pcr_bands),
        atm_strike=current.atm_strike, underlying_price=current.underlying_price, max_pain=max_pain_strike,
        expiry=expiry_day, dte=dte, first_of_day=prev is None)
