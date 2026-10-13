"""Realism C2: pluggable execution models for backtests (docs/design/C2_MODELS.md).

One protocol per concern - fill, slippage, latency, margin, options pricing, settlement - with a registry of named
implementations. `ModelSet()` (every default) reproduces the engine's results before C2 byte for byte (golden test
tests/golden/c2_default_models.json). A non-default set is recorded in the reproducibility fingerprint (C4), so a run
using other models can never be mistaken for a default one. Costs are not a model: `india_costs` prices every fill.
PR 1 wired fill (exits) and slippage into the single-leg engine. PR 2 (N7 C2b) adds the `next_open` fill, the
`spread_half` and `volume_share` slippage and the `fixed_ms` latency; a non-default choice of those needs the operator
flag `backtest_models` at the API (the library takes them as asked). Margin, pricing and settlement keep their
defaults until C2 PRs 3-4.

No look-ahead: an entry deferred by the fill or the latency fills at the OPEN of a later bar - the only number of that
bar it reads - and `volume_share` sizes its impact on the signal bar's volume (the last closed bar), never the fill
bar's.
"""
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from typing import Any, Callable, Dict, Optional, Protocol, Tuple

from app.core.enums import SignalDirection
from app.trading.exit_logic import determine_exit_price


class SlippageModel(Protocol):
    name: str

    def apply(self, price: float, direction: SignalDirection, *, qty: Optional[float] = None, volume: Optional[float] = None) -> float: ...


class FillModel(Protocol):
    name: str
    defers_entry: bool              # True: an entry fills at a later bar's open, not at the signal's price on its bar

    def exit(self, direction: str, stop: float, target1: Optional[float], target2: Optional[float], bar: Dict[str, float]
             ) -> Optional[Tuple[str, float]]: ...


@dataclass(frozen=True)
class FixedPctSlippage:
    """Today's slippage: a fixed percentage against the trade (PaperBroker's default)."""
    pct: float = 0.02
    name: str = "fixed_pct"

    def apply(self, price: float, direction: SignalDirection, *, qty: Optional[float] = None, volume: Optional[float] = None) -> float:
        return _against(price, self.pct, direction)


def _against(price: float, pct: float, direction: SignalDirection) -> float:
    slip = price * pct / 100
    return price + slip if direction == SignalDirection.LONG else price - slip


def _positive(name: str, value: float, *, zero_ok: bool = True) -> float:
    v = float(value)
    if v != v or v < 0 or (v == 0 and not zero_ok):
        raise ModelError(f"{name} must be {'zero or more' if zero_ok else 'above zero'}")
    return v


@dataclass(frozen=True)
class SpreadHalfSlippage:
    """Half the bid-ask spread against the trade. The bars carry no spread, so the spread is a setting (% of price)
    until recorded spreads reach the backtest (OPEN_QUESTIONS C2-3)."""
    spread_pct: float = 0.05
    name: str = "spread_half"

    def __post_init__(self) -> None:
        _positive("spread_pct", self.spread_pct)

    def apply(self, price: float, direction: SignalDirection, *, qty: Optional[float] = None, volume: Optional[float] = None) -> float:
        return _against(price, self.spread_pct / 2, direction)


@dataclass(frozen=True)
class VolumeShareSlippage:
    """Impact that grows with the order's share of the signal bar's volume: base_pct + impact_pct x (qty / volume x 100),
    capped at max_pct. No volume (or a zero bar) -> max_pct: an order the bar could not absorb is priced at the cap."""
    base_pct: float = 0.02
    impact_pct: float = 0.1             # extra % of price per 1 % of the bar's volume
    max_pct: float = 1.0
    name: str = "volume_share"

    def __post_init__(self) -> None:
        _positive("base_pct", self.base_pct)
        _positive("impact_pct", self.impact_pct)
        if _positive("max_pct", self.max_pct, zero_ok=False) < self.base_pct:
            raise ModelError("max_pct must be at least base_pct")

    def pct_for(self, qty: Optional[float], volume: Optional[float]) -> float:
        if qty is None:
            return self.base_pct
        if not volume or volume <= 0:
            return self.max_pct
        return min(self.max_pct, self.base_pct + self.impact_pct * (abs(float(qty)) / float(volume) * 100.0))

    def apply(self, price: float, direction: SignalDirection, *, qty: Optional[float] = None, volume: Optional[float] = None) -> float:
        return _against(price, self.pct_for(qty, volume), direction)


@dataclass(frozen=True)
class TouchFill:
    """Today's exits: stop / target / gap-through at the open on the bar that touches them (exit_logic). Entries at the
    signal's price on the signal bar."""
    name: str = "touch"
    defers_entry: bool = False

    def exit(self, direction, stop, target1, target2, bar):
        return determine_exit_price(direction, stop, target1, target2, bar["low"], bar["high"],
                                    open_price=float(bar["open"]) if "open" in bar else None)


@dataclass(frozen=True)
class NextOpenFill(TouchFill):
    """An entry fills at the NEXT bar's open (a market order sent when the signal bar closed); exits as `touch`, from
    the fill bar on (its range after the open)."""
    name: str = "next_open"
    defers_entry: bool = True


class LatencyModel(Protocol):
    name: str

    def delay(self) -> timedelta: ...


@dataclass(frozen=True)
class ZeroLatency:
    name: str = "zero"

    def delay(self) -> timedelta:
        return timedelta(0)


@dataclass(frozen=True)
class FixedMsLatency:
    """Every entry order reaches the market `ms` after the signal bar closes: it fills at the open of the bar that is
    trading then (or the next bar after a gap) - one bar later at least, more when the delay spans bars."""
    ms: float = 250.0
    name: str = "fixed_ms"

    def __post_init__(self) -> None:
        _positive("ms", self.ms, zero_ok=False)

    def delay(self) -> timedelta:
        return timedelta(milliseconds=float(self.ms))


SLIPPAGE: Dict[str, Callable[..., Any]] = {"fixed_pct": FixedPctSlippage, "spread_half": SpreadHalfSlippage, "volume_share": VolumeShareSlippage}
FILL: Dict[str, Callable[..., Any]] = {"touch": TouchFill, "next_open": NextOpenFill}
LATENCY: Dict[str, Callable[..., Any]] = {"zero": ZeroLatency, "fixed_ms": FixedMsLatency}
# N7 C2b: the models added after PR 1 - a request naming one needs the `backtest_models` flag at the API.
FLAGGED = {"fill": {"next_open"}, "slippage": {"spread_half", "volume_share"}, "latency": {"fixed_ms"}}
FLAG = "backtest_models"
# Kinds whose alternatives land in later C2 PRs: only the default exists, so a request for anything else fails loudly.
DEFAULTS = {"fill": "touch", "slippage": "fixed_pct", "latency": "zero", "margin": "none", "pricing": "recorded_only",
            "settlement": "default"}


class ModelError(ValueError):
    pass


@dataclass(frozen=True)
class ModelSet:
    fill: str = DEFAULTS["fill"]
    slippage: str = DEFAULTS["slippage"]
    latency: str = DEFAULTS["latency"]
    margin: str = DEFAULTS["margin"]
    pricing: str = DEFAULTS["pricing"]
    settlement: str = DEFAULTS["settlement"]
    params: Dict[str, Dict[str, Any]] = field(default_factory=dict)   # per kind, e.g. {"slippage": {"pct": 0.05}}

    def __post_init__(self) -> None:
        known = {"fill": FILL, "slippage": SLIPPAGE, "latency": LATENCY}
        for kind, default in DEFAULTS.items():
            chosen = getattr(self, kind)
            if kind in known and chosen not in known[kind]:
                raise ModelError(f"Unknown {kind} model {chosen!r} (available: {', '.join(sorted(known[kind]))})")
            if kind not in known and chosen != default:
                raise ModelError(f"{kind} model {chosen!r} is not available yet (only {default!r})")
        unknown = set(self.params) - set(DEFAULTS)
        if unknown:
            raise ModelError(f"Parameters for unknown model kinds: {sorted(unknown)}")

    @property
    def is_default(self) -> bool:
        return self == ModelSet()

    def describe(self) -> Dict[str, Any]:
        """What goes into the reproducibility fingerprint (only when not the default set)."""
        return asdict(self)

    def slippage_model(self, default_pct: float) -> SlippageModel:
        params = {**({"pct": default_pct} if self.slippage == "fixed_pct" else {}), **self.params.get("slippage", {})}
        return SLIPPAGE[self.slippage](**params)

    def fill_model(self) -> FillModel:
        return FILL[self.fill](**self.params.get("fill", {}))

    def latency_model(self) -> LatencyModel:
        return LATENCY[self.latency](**self.params.get("latency", {}))

    def flagged(self) -> Dict[str, str]:
        """The chosen models that need the `backtest_models` flag (kind -> name)."""
        return {kind: getattr(self, kind) for kind, names in FLAGGED.items() if getattr(self, kind) in names}

    def built(self, default_pct: float = 0.02) -> Tuple[Any, Any, Any]:
        """(fill, slippage, latency) instances - building them checks their parameters (ModelError / TypeError)."""
        return self.fill_model(), self.slippage_model(default_pct), self.latency_model()
