"""Strategy templates, step 1 (docs/owner-orders/ATP_SESSION_STRATEGY_TEMPLATES.md sections 1, 3, 4): the versioned,
white-box SIGNAL RECIPE schema.

A template is data, never code. Every logical part (context, setup, trigger, invalidation, exit signal, evidence) is a
ScreenQL expression bound to the registry's real signatures (`app.strategy_templates.compile`), so the same definition
runs as a scan, a backtest and a PAPER deployment. A template only generates SIGNALS: it has no field for a target,
a position size, a strike, a premium, a lot or an order (section 5 - those are the user's deployment settings).

Every number in a template is a PARAMETER with a type, a default and bounds (`ParamSpec`); the expressions read them as
`$key`. Relations between parameters (fast < slow; the context timeframe above the signal timeframe) are `Constraint`s.
"""
from __future__ import annotations

import json
from typing import List, Literal, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr, model_validator

from app.screener import nodes as n

SCHEMA_VERSION = "atp.template/1"
SIGNAL_TIMEFRAME = "signal_timeframe"        # the reserved name a constraint uses for the user's chosen signal timeframe
TIMEFRAMES: Tuple[str, ...] = n.TIMEFRAMES
MA_TYPES: Tuple[str, ...] = ("EMA", "SMA", "WMA", "HMA")
SOURCES: Tuple[str, ...] = ("close", "open", "high", "low")
_KEY = r"^[a-z][a-z0-9_]{0,31}$"

ParamType = Literal["int", "float", "enum", "bool", "timeframe", "ma_type", "source"]
Value = Union[StrictBool, StrictInt, StrictFloat, StrictStr]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ParamSpec(_Strict):
    """One user setting: what the settings form shows (label, help) and what the recipe accepts (type, bounds)."""
    key: str = Field(pattern=_KEY)
    label: str = Field(min_length=1, max_length=80)
    help: str = Field(min_length=1, max_length=400)
    type: ParamType
    default: Value
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None
    choices: Tuple[str, ...] = ()

    @model_validator(mode="after")
    def _shape(self) -> "ParamSpec":
        if self.type in ("int", "float"):
            if self.min is None or self.max is None or self.min > self.max:
                raise ValueError(f"{self.key}: a number setting needs min <= max")
            if self.choices:
                raise ValueError(f"{self.key}: a number setting has no choices")
        elif self.type == "bool":
            if self.min is not None or self.max is not None or self.choices:
                raise ValueError(f"{self.key}: a yes / no setting has no bounds or choices")
        else:
            allowed = {"timeframe": TIMEFRAMES, "ma_type": MA_TYPES, "source": SOURCES}.get(self.type)
            if not self.choices:
                raise ValueError(f"{self.key}: a {self.type} setting needs its choices")
            if allowed is not None and not set(self.choices) <= set(allowed):
                raise ValueError(f"{self.key}: choices must be among {', '.join(allowed)}")
        problem = self.problem(self.default)
        if problem:
            raise ValueError(f"default: {problem}")
        return self

    def problem(self, value: object) -> Optional[str]:
        """Why `value` is not acceptable for this setting (a plain message for the settings form), or None."""
        if self.type == "bool":
            return None if isinstance(value, bool) else f"{self.label} must be yes or no"
        if self.type in ("int", "float"):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return f"{self.label} must be a number"
            if self.type == "int" and float(value) != int(value):
                return f"{self.label} must be a whole number"
            assert self.min is not None and self.max is not None
            if not self.min <= float(value) <= self.max:
                return f"{self.label} must be between {self.min:g} and {self.max:g}"
            return None
        if not isinstance(value, str) or value not in self.choices:
            return f"{self.label} must be one of {', '.join(self.choices)}"
        return None


class Constraint(_Strict):
    """`left op right` between two settings (or a setting and the signal timeframe). Timeframes compare by length."""
    left: str = Field(pattern=_KEY)
    op: Literal["<", "<=", ">", ">=", "!="]
    right: str = Field(pattern=_KEY)
    when: Optional[str] = Field(default=None, pattern=_KEY)       # a yes / no setting: the constraint applies when it is yes
    message: str = Field(min_length=1, max_length=200)


class Sided(_Strict):
    """One expression per side. The short side is written out (white box), not derived; the mirror test checks it."""
    long: str = Field(min_length=1, max_length=2000)
    short: str = Field(min_length=1, max_length=2000)


class Context(_Strict):
    """Optional filters on closed context bars, read on the trigger bar. With `timeframe_param`, every field and function
    in the expression that names no timeframe runs on the chosen context timeframe (the ScreenQL runtime's close-time
    alignment, R9). With `enabled_by`, a yes / no setting turns the filter on."""
    long: str = Field(min_length=1, max_length=2000)
    short: str = Field(min_length=1, max_length=2000)
    timeframe_param: Optional[str] = Field(default=None, pattern=_KEY)
    enabled_by: Optional[str] = Field(default=None, pattern=_KEY)


class Setup(_Strict):
    """What ARMS the recipe (S4). kind `event`: every bar the expression is true starts a new instance (the newest
    replaces the older one, R8). kind `state`: an instance starts on the bar the state becomes true and ends when it
    stops holding. `expiry_bars` (a number or `$setting`) counts signal-timeframe bars from the setup bar; `same_bar`
    lets the trigger fire on the setup bar itself. `arm_level` (optional) is a price fixed on the setup bar: a breach
    (`breach_on` close / wick) before the trigger voids the instance (S4, S7)."""
    kind: Literal["event", "state"]
    long: str = Field(min_length=1, max_length=2000)
    short: str = Field(min_length=1, max_length=2000)
    expiry_bars: Optional[Union[StrictInt, StrictStr]] = None
    same_bar: StrictBool = False
    arm_level: Optional[Sided] = None
    breach_on: Literal["close", "wick"] = "close"


class Evidence(_Strict):
    name: str = Field(pattern=_KEY)
    long: str = Field(min_length=1, max_length=2000)
    short: str = Field(min_length=1, max_length=2000)


class TextPart(_Strict):
    token: str = Field(pattern=_KEY)
    when: Optional[str] = Field(default=None, pattern=_KEY)       # a yes / no setting: the part shows when it is yes


class SignalText(_Strict):
    long: Tuple[TextPart, ...] = Field(min_length=1)
    short: Tuple[TextPart, ...] = Field(min_length=1)


class Template(_Strict):
    schema_: Literal["atp.template/1"] = Field(alias="schema")
    id: str = Field(pattern=r"^[A-F][0-9]{1,2}$")
    version: StrictInt = Field(ge=1)
    name: str = Field(min_length=1, max_length=80)
    family: Literal["A", "B", "C", "D", "E", "F"]
    idea: str = Field(min_length=1, max_length=300)
    difficulty: Literal["Beginner", "Intermediate", "Advanced"]
    style: Literal["Intraday", "Swing", "Both", "Intraday (session-based)"]
    allowed_timeframes: Tuple[str, ...] = Field(min_length=1)
    params: Tuple[ParamSpec, ...] = ()
    constraints: Tuple[Constraint, ...] = ()
    sides_default: Literal["long", "short", "both"] = "both"
    context: Optional[Context] = None
    setup: Setup
    trigger: Optional[Sided] = None       # None: the setup event itself is the trigger (needs kind event + same_bar)
    invalidation: Sided                   # a price per side, read on the trigger bar (S6: on the losing side of the close)
    exit_signal: Optional[Sided] = None
    evidence: Tuple[Evidence, ...] = ()
    signal_text: SignalText

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, populate_by_name=True)

    @model_validator(mode="after")
    def _consistent(self) -> "Template":
        keys = [p.key for p in self.params]
        if len(set(keys)) != len(keys):
            raise ValueError("parameter keys must be unique")
        if SIGNAL_TIMEFRAME in keys:
            raise ValueError(f"{SIGNAL_TIMEFRAME} is reserved")
        by_key = {p.key: p for p in self.params}
        bad_tf = [tf for tf in self.allowed_timeframes if tf not in TIMEFRAMES]
        if bad_tf:
            raise ValueError(f"unknown timeframe(s): {', '.join(bad_tf)}")
        for c in self.constraints:
            for side in (c.left, c.right):
                if side != SIGNAL_TIMEFRAME and side not in by_key:
                    raise ValueError(f"constraint names an unknown setting {side!r}")
            if c.when is not None and (c.when not in by_key or by_key[c.when].type != "bool"):
                raise ValueError(f"constraint 'when' must be a yes / no setting, got {c.when!r}")
        refs: List[Tuple[str, Optional[str], str]] = []          # (what, key, required type)
        if self.context is not None:
            refs += [("context timeframe", self.context.timeframe_param, "timeframe"), ("context switch", self.context.enabled_by, "bool")]
        refs += [(f"text part {p.token}", p.when, "bool") for p in (*self.signal_text.long, *self.signal_text.short)]
        for what, key, kind in refs:
            if key is not None and (key not in by_key or by_key[key].type != kind):
                raise ValueError(f"{what} must name a {kind} setting, got {key!r}")
        if isinstance(self.setup.expiry_bars, str):
            key = self.setup.expiry_bars.removeprefix("$")
            if not self.setup.expiry_bars.startswith("$") or key not in by_key or by_key[key].type != "int":
                raise ValueError("expiry_bars must be a whole number or a $setting of type int")
        elif self.setup.expiry_bars is not None and self.setup.expiry_bars < 0:
            raise ValueError("expiry_bars must not be negative")
        if self.trigger is None and not (self.setup.kind == "event" and self.setup.same_bar):
            raise ValueError("without a trigger the setup event is the signal: kind event with same_bar true")
        if len({e.name for e in self.evidence}) != len(self.evidence):
            raise ValueError("evidence names must be unique")
        return self

    @classmethod
    def from_data(cls, data: object) -> "Template":
        """A template from its JSON form (a dict as stored or posted): validated in JSON mode, where arrays are the
        tuples above and nothing is coerced (strict: "3" is not 3, 1 is not true)."""
        return cls.model_validate_json(json.dumps(data))

    def param(self, key: str) -> ParamSpec:
        return next(p for p in self.params if p.key == key)


__all__ = ["SCHEMA_VERSION", "SIGNAL_TIMEFRAME", "MA_TYPES", "SOURCES", "ParamSpec", "Constraint", "Sided", "Context", "Setup",
           "Evidence", "TextPart", "SignalText", "Template"]
