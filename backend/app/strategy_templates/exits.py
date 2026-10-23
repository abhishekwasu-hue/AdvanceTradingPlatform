"""Strategy templates section 5: EXITS AND TARGETS ARE THE USER'S SETTINGS (owner order; R4, R5; WAVE templates section 4).

A template only generates signals: its invalidation is where the idea is wrong, and its optional exit signal says when
the idea has ended. Where a trade's stop comes from and whether it has a target are the deployment's own settings,
stored on the deployment row (migration d7f9b1c3e5a8):

* `stop_source` - TEMPLATE_INVALIDATION (the template's invalidation is the underlying-level stop, the default) /
  PREMIUM_PCT (a single option: only the premium stop - `premium_stop_pct` - acts, no underlying-level stop) /
  CREDIT_PCT (a structure: its credit levels - `stop_credit_pct` - as today).
* `target_rule` - NONE (no price target, the default) / R_MULTIPLE (`target_r` times the distance from the entry to the
  invalidation) / TEMPLATE_EXIT (exit on the template's exit signal at the next price, reason "Template exit") /
  CREDIT_PCT (a structure: `target_credit_pct`, as today) / WAVE_PROJECTION (family G: the running wave's start plus
  `target_ratio` x the finished `target_base_wave`, read on the signal bar; no projection there = no target, never a
  refusal).

NULL in every column is exactly the behaviour before this module (every existing deployment): the template's invalidation
as the stop and no target; a structure keeps its credit levels. The premium stop and the credit levels are independent
safety nets that stay as set (R4: the first one hit exits; ADR-0004: nothing here ever blocks or delays an exit). The
trailing / break-even / time exits (ExitRules) are unchanged.

The precedence per instrument (R4; docs/design/STRATEGY_TEMPLATES_IMPL.md, "Exits as settings"):

| instrument            | stop_source (first = default)          | target_rule (first = default)                          |
|-----------------------|-----------------------------------------|--------------------------------------------------------|
| UNDERLYING / FUTURE   | TEMPLATE_INVALIDATION only (RiskManager | NONE, R_MULTIPLE, TEMPLATE_EXIT, WAVE_PROJECTION        |
|                       | sizes from entry - stop)                |                                                        |
| OPTION, single        | TEMPLATE_INVALIDATION, PREMIUM_PCT      | NONE, R_MULTIPLE, TEMPLATE_EXIT, WAVE_PROJECTION        |
|                       | (the premium stop always stays)         | (levels on the underlying, as the strategy's targets)  |
| OPTION, structure     | CREDIT_PCT                              | CREDIT_PCT, TEMPLATE_EXIT (the credit levels stay)      |

TEMPLATE_EXIT needs a template (with the user's settings) that has an exit signal; WAVE_PROJECTION a wave template (it
counts waves on its wave timeframe). Not built here (OPEN_QUESTIONS TPL-X1..X3): stop_source NONE (on an option the
premium / credit safety net always stays, so "none" would still have a stop), target_rule NEXT_AREA (market zones are
stored snapshots behind their own flag, R10), and the template's invalidation as an exit of a structure.

The engines and the worker apply the choice through the template adapter (`TemplateStrategy.use_exits`), so a scan, both
backtest engines and a deployment get the same levels on the same bars (the section 8 parity tests). The SIGNAL TEXT
never carries any of it (policy 11l): the levels are the order / position record's.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from app.strategy_templates.language import _WAVE_NAME_WORDS

# The spec's full lists (section 5 + WAVE section 4) and what this build offers
STOP_SOURCES: Tuple[str, ...] = ("TEMPLATE_INVALIDATION", "PREMIUM_PCT", "CREDIT_PCT", "NONE")
TARGET_RULES: Tuple[str, ...] = ("NONE", "R_MULTIPLE", "NEXT_AREA", "TEMPLATE_EXIT", "CREDIT_PCT", "WAVE_PROJECTION")
NOT_BUILT: Dict[str, str] = {
    "stop:NONE": "Trading without any stop is not offered: the template's level, the premium stop or the spread's loss limit always applies",
    "target:NEXT_AREA": "Taking profit at the next area is not available yet",
}

UNDERLYING, FUTURE, OPTION_SINGLE, OPTION_STRUCTURE = "UNDERLYING", "FUTURE", "OPTION_SINGLE", "OPTION_STRUCTURE"
_LEVEL_TARGETS: Tuple[str, ...] = ("NONE", "R_MULTIPLE", "TEMPLATE_EXIT", "WAVE_PROJECTION")
# instrument class -> (the stop sources, the target rules); the first of each is what NULL means there (today's behaviour)
PRECEDENCE: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    UNDERLYING: (("TEMPLATE_INVALIDATION",), _LEVEL_TARGETS),
    FUTURE: (("TEMPLATE_INVALIDATION",), _LEVEL_TARGETS),
    OPTION_SINGLE: (("TEMPLATE_INVALIDATION", "PREMIUM_PCT"), _LEVEL_TARGETS),
    OPTION_STRUCTURE: (("CREDIT_PCT",), ("CREDIT_PCT", "TEMPLATE_EXIT")),
}
# The bounds of the user's numbers (form validation, not trading rules): the R multiple, and WaveProjection's own ratio
# bounds (the ScreenQL registry: above 0, at most 10)
TARGET_R_BOUNDS = (0.1, 20.0)
RATIO_BOUNDS = (0.0, 10.0)
EXIT_REASON = "Template exit"


def base_waves() -> Tuple[str, ...]:
    from app.price_action.waves.levels import BASE_CHOICES
    return tuple(BASE_CHOICES)


def base_wave_words(base: str) -> str:
    return "the wave before the running one" if base == "previous" else _WAVE_NAME_WORDS.get(base, base)


def instrument_class(instrument_kind: Optional[str], option_strategy: Optional[str]) -> str:
    kind = str(getattr(instrument_kind, "value", instrument_kind) or UNDERLYING).upper()
    structure = str(getattr(option_strategy, "value", option_strategy) or "SINGLE").upper()
    if kind == "OPTION":
        return OPTION_SINGLE if structure == "SINGLE" else OPTION_STRUCTURE
    return FUTURE if kind == "FUTURE" else UNDERLYING


@dataclass(frozen=True)
class Capabilities:
    """What the template (with the user's settings) can offer: an exit signal; a wave projection (family G)."""
    exit_signal: bool = False
    wave_projection: bool = False


def capabilities(recipe: Any) -> Capabilities:
    sides = recipe.settings.side_list()
    has_exit = any(recipe.part("exit_signal", side) is not None for side in sides)
    types = {p.key: p.type for p in recipe.template.params}
    waves = types.get("wave_degree") == "int" and types.get("wave_tf") == "timeframe"
    return Capabilities(exit_signal=has_exit, wave_projection=waves)


@dataclass(frozen=True)
class TemplateExits:
    """One deployment's (or backtest's) exit choice. Every field None = the behaviour before section 5."""
    stop_source: Optional[str] = None
    target_rule: Optional[str] = None
    target_r: Optional[float] = None
    target_base_wave: Optional[str] = None
    target_ratio: Optional[float] = None

    @property
    def is_default(self) -> bool:
        return all(v is None for v in asdict(self).values())

    @classmethod
    def from_record(cls, record: Any) -> "TemplateExits":
        return cls(**{k: getattr(record, k, None) for k in cls.__dataclass_fields__})

    def columns(self) -> Dict[str, Any]:
        return asdict(self)

    def effective(self, cls_: str) -> Tuple[str, str]:
        """(stop_source, target_rule) in force on this instrument class: NULL = the class's default (today's)."""
        stops, targets = PRECEDENCE[cls_]
        return self.stop_source or stops[0], self.target_rule or targets[0]

    def problems(self, cls_: str, caps: Capabilities) -> List[str]:
        """Plain messages for a choice that does not fit the instrument or the template (none: it fits)."""
        out: List[str] = []
        stops, targets = PRECEDENCE[cls_]
        if self.stop_source is not None:
            if self.stop_source not in STOP_SOURCES:
                out.append("Pick where the trade's stop comes from from the list")
            elif f"stop:{self.stop_source}" in NOT_BUILT:
                out.append(NOT_BUILT[f"stop:{self.stop_source}"])
            elif self.stop_source not in stops:
                out.append({UNDERLYING: "On shares the stop is always the template's own level",
                            FUTURE: "On a future the stop is always the template's own level",
                            OPTION_SINGLE: "A single option's stop is the template's own level or your premium stop",
                            OPTION_STRUCTURE: "A spread's stop is its own loss limit (stop % of the credit)"}[cls_])
        if self.target_rule is not None:
            if self.target_rule not in TARGET_RULES:
                out.append("Pick when to take profit from the list")
            elif f"target:{self.target_rule}" in NOT_BUILT:
                out.append(NOT_BUILT[f"target:{self.target_rule}"])
            elif self.target_rule not in targets:
                out.append("A spread takes profit at its own target % of the credit, or on the template's exit signal"
                           if cls_ == OPTION_STRUCTURE else "A spread's target % applies to spreads only")
            elif self.target_rule == "TEMPLATE_EXIT" and not caps.exit_signal:
                out.append("This template has no exit signal with these settings")
            elif self.target_rule == "WAVE_PROJECTION" and not caps.wave_projection:
                out.append("The wave projection is for the wave templates only")
        rule = self.target_rule
        if rule == "R_MULTIPLE":
            lo, hi = TARGET_R_BOUNDS
            if self.target_r is None or not math.isfinite(self.target_r) or not lo <= self.target_r <= hi:
                out.append(f"Type how many times the risk to take profit at ({lo:g} to {hi:g})")
        elif self.target_r is not None:
            out.append("The times-the-risk number is for the risk-multiple target only")
        if rule == "WAVE_PROJECTION":
            lo, hi = RATIO_BOUNDS
            if self.target_base_wave not in base_waves():
                out.append("Pick the wave the projection is measured on")
            if self.target_ratio is None or not math.isfinite(self.target_ratio) or not lo < self.target_ratio <= hi:
                out.append(f"Type the projection's size as a share of that wave (above {lo:g}, up to {hi:g})")
        elif self.target_base_wave is not None or self.target_ratio is not None:
            out.append("The wave and its share are for the wave projection target only")
        return out

    def describe(self) -> str:
        """Plain words for the deployment's summary line (empty for the default)."""
        parts: List[str] = []
        if self.stop_source == "PREMIUM_PCT":
            parts.append("stop at the premium stop only")
        elif self.stop_source == "TEMPLATE_INVALIDATION":
            parts.append("stop at the template's level")
        if self.target_rule == "R_MULTIPLE" and self.target_r is not None:
            parts.append(f"target at {self.target_r:g} times the risk")
        elif self.target_rule == "TEMPLATE_EXIT":
            parts.append("exit on the template's exit signal")
        elif self.target_rule == "WAVE_PROJECTION" and self.target_ratio is not None and self.target_base_wave:
            parts.append(f"target at the wave projection ({self.target_ratio:g} of {base_wave_words(self.target_base_wave)})")
        elif self.target_rule == "NONE":
            parts.append("no fixed target")
        return ", ".join(parts)


DEFAULT = TemplateExits()


class TemplateExitsBody(BaseModel):
    """The request shape (deployment and backtest routes). The values are checked against the instrument and the template
    with `TemplateExits.problems` (plain messages), not here."""
    model_config = ConfigDict(extra="forbid")
    stop_source: Optional[str] = Field(default=None, max_length=24)
    target_rule: Optional[str] = Field(default=None, max_length=24)
    target_r: Optional[float] = None
    target_base_wave: Optional[str] = Field(default=None, max_length=16)
    target_ratio: Optional[float] = None

    def to_exits(self) -> TemplateExits:
        return TemplateExits(**self.model_dump())


def projection_text(exits: TemplateExits) -> str:
    """The ScreenQL expression of a WAVE_PROJECTION target (compiled with the template's own wave degree and timeframe)."""
    ratio = float(exits.target_ratio or 0.0)
    return f'WaveProjection($wave_degree, "{exits.target_base_wave}", {ratio!r})@{{wave_tf}}'


def target_level(rule: Optional[str], direction: int, entry: float, invalidation: float, *, target_r: Optional[float] = None,
                 projection: Optional[float] = None) -> Tuple[Optional[float], Optional[float]]:
    """(target1, risk_reward) for a signal of `direction` (+1 long / -1 short) - one function for the scan side, both
    backtest engines and the worker (S11). No target rule, or a projection that is missing or not beyond the entry on
    the winning side: (None, None) - no target, never a refusal."""
    risk = abs(entry - invalidation)
    if not (math.isfinite(entry) and math.isfinite(invalidation)) or risk <= 0 or direction == 0:
        return None, None
    if rule == "R_MULTIPLE" and target_r is not None:
        return round(entry + direction * float(target_r) * risk, 2), float(target_r)
    if rule == "WAVE_PROJECTION" and projection is not None and math.isfinite(projection):
        if (direction > 0 and projection > entry) or (direction < 0 and projection < entry):
            return round(float(projection), 2), round(abs(projection - entry) / risk, 2)
    return None, None


def checked(strategy: Any, body: Optional["TemplateExitsBody"], instrument_kind: Optional[str],
            option_strategy: Optional[str]) -> TemplateExits:
    """The request's exit choice checked for this strategy and instrument; raises ValueError with the plain messages.
    No choice: the default (as before). A choice for a strategy that is not a template is refused (the DSL v1 / built-in
    strategies keep their own stop / target, section 5)."""
    if body is None:
        return DEFAULT
    choice = body.to_exits()
    if choice.is_default:
        return DEFAULT
    if not hasattr(strategy, "use_exits"):
        raise ValueError(["These exit choices are for strategy templates only"])
    problems = choice.problems(instrument_class(instrument_kind, option_strategy), strategy.capabilities())
    if problems:
        raise ValueError(problems)
    return choice


def underlying_stop_on(exits: TemplateExits, instrument_kind: Optional[str], option_strategy: Optional[str]) -> bool:
    """False when a single option's stop is the premium stop only (no underlying-level stop on the trade)."""
    return not (instrument_class(instrument_kind, option_strategy) == OPTION_SINGLE and exits.stop_source == "PREMIUM_PCT")


__all__ = ["TemplateExitsBody", "checked", "STOP_SOURCES", "TARGET_RULES", "NOT_BUILT", "PRECEDENCE", "TemplateExits", "Capabilities", "capabilities",
           "instrument_class", "target_level", "projection_text", "underlying_stop_on", "base_waves", "base_wave_words",
           "EXIT_REASON", "DEFAULT", "TARGET_R_BOUNDS", "RATIO_BOUNDS"]
