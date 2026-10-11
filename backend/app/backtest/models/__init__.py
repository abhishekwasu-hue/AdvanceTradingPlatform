"""Realism C2: pluggable execution models for backtests (docs/design/C2_MODELS.md).

One protocol per concern - fill, slippage, latency, margin, options pricing, settlement - with a registry of named
implementations. `ModelSet()` (every default) reproduces the engine's results before C2 byte for byte (golden test
tests/golden/c2_default_models.json). A non-default set is recorded in the reproducibility fingerprint (C4), so a run
using other models can never be mistaken for a default one. Costs are not a model: `india_costs` prices every fill.
This first PR wires fill (exits) and slippage into the single-leg engine; the other kinds carry their defaults only.
"""
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, Optional, Protocol, Tuple

from app.core.enums import SignalDirection
from app.trading.exit_logic import determine_exit_price


class SlippageModel(Protocol):
    name: str

    def apply(self, price: float, direction: SignalDirection) -> float: ...


class FillModel(Protocol):
    name: str

    def exit(self, direction: str, stop: float, target1: Optional[float], target2: Optional[float], bar: Dict[str, float]
             ) -> Optional[Tuple[str, float]]: ...


@dataclass(frozen=True)
class FixedPctSlippage:
    """Today's slippage: a fixed percentage against the trade (PaperBroker's default)."""
    pct: float = 0.02
    name: str = "fixed_pct"

    def apply(self, price: float, direction: SignalDirection) -> float:
        slip = price * self.pct / 100
        return price + slip if direction == SignalDirection.LONG else price - slip


@dataclass(frozen=True)
class TouchFill:
    """Today's exits: stop / target / gap-through at the open on the bar that touches them (exit_logic)."""
    name: str = "touch"

    def exit(self, direction, stop, target1, target2, bar):
        return determine_exit_price(direction, stop, target1, target2, bar["low"], bar["high"],
                                    open_price=float(bar["open"]) if "open" in bar else None)


SLIPPAGE: Dict[str, Callable[..., Any]] = {"fixed_pct": FixedPctSlippage}
FILL: Dict[str, Callable[..., Any]] = {"touch": TouchFill}
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
        known = {"fill": FILL, "slippage": SLIPPAGE}
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
        params = {"pct": default_pct, **self.params.get("slippage", {})}
        return SLIPPAGE[self.slippage](**params)

    def fill_model(self) -> FillModel:
        return FILL[self.fill](**self.params.get("fill", {}))
