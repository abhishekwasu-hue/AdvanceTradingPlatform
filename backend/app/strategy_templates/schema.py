"""Strategy templates, step 1 (docs/owner-orders/ATP_SESSION_STRATEGY_TEMPLATES.md sections 1, 3, 4): the versioned,
white-box SIGNAL RECIPE schema.

A template is data, never code. Every logical part (context, setup, trigger, invalidation, exit signal, evidence) is a
ScreenQL expression bound to the registry's real signatures (`app.strategy_templates.compile`), so the same definition
runs as a scan, a backtest and a PAPER deployment. A template only generates SIGNALS: it has no field for a target,
a position size, a strike, a premium, a lot or an order (section 5 - those are the user's deployment settings).

Every number in a template is a PARAMETER with a type, a default and bounds (`ParamSpec`); the expressions read them as
`$key`. Relations between parameters (fast < slow; the context timeframe above the signal timeframe) are `Constraint`s.

Family G (wave templates, docs/design/WAVE_TEMPLATES_REVIEW.md WB3) adds, still as `atp.template/1` and without changing
any existing template:
* `variants`: a part (setup, trigger, invalidation, exit signal, context) may hold one expression per value of an enum
  setting (`{"by": "entry_style", "variants": {"aggressive": {...}, "breakout": {...}}}`); only the chosen one is
  compiled, so an unchosen variant never runs and never holds the warm-up back;
* `@{key}` in an expression: the timeframe of a timeframe setting (`WaveState(1)@{wave_tf}`), filled in at compile time
  and then validated like a written `@1h`.

Family F version 2 (TPL-7), still `atp.template/1` and without changing any existing template: a pack holds several
member templates under its `source` setting, and a member may itself switch a part on its own setting. So
* a variant may itself be a switch (nested `variants`: e.g. the pack's `source`, then D3's `trigger_bar`);
* `signal_text` may be a switch (`SignalTextSwitch`: the member's own words per `source`);
* `timeframes_by` limits the signal timeframes per choice of an enum setting (a member that does not run on Weekly).

Family G version 2 (the wave templates' companions), still `atp.template/1` and without changing any existing template:
* `confirmations`: optional confirmations of the trigger, each a switch on its own enum setting whose DEFAULT choice is
  "no confirmation" (null). A chosen confirmation is AND-ed to the chosen trigger at compile time; with every one left at
  its default the trigger compiles to exactly its own expression, so the signals are those of the template without them.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr, model_validator

from app.screener import nodes as n
from app.screener.registry import INTRADAY_SERIES_TFS

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
    # Step 2: the words the settings form shows for each choice (same order as `choices`; empty = the choices themselves)
    choice_labels: Tuple[str, ...] = ()

    @model_validator(mode="after")
    def _shape(self) -> "ParamSpec":
        if self.choice_labels and len(self.choice_labels) != len(self.choices):
            raise ValueError(f"{self.key}: one label per choice")
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


class Switch(_Strict):
    """One expression per value of the enum setting `by` (every value covered); null means "no expression" for that value
    (allowed for a trigger, a context and an exit signal, never for an invalidation). TPL-7: a variant may itself be a
    switch on another enum setting (a pack's member that switches the part on its own setting)."""
    by: str = Field(pattern=_KEY)
    variants: Dict[str, Optional[Union[Sided, "Switch"]]] = Field(min_length=1)


class Confirmation(Switch):
    """Family G version 2: an optional confirmation of the trigger per value of the enum setting `by` - an expression that
    must also hold on the trigger bar, or null (no confirmation; the setting's default must be null, so it is off by
    default). Never a nested switch."""
    variants: Dict[str, Optional[Sided]] = Field(min_length=1)     # type: ignore[assignment]


class SetupExtreme(_Strict):
    """Step 5 (TPL-5): an invalidation that is the extreme SINCE THE SETUP BAR - long: the lowest low from the setup
    instance's bar to the trigger bar (both included); short: the highest high. The engine keeps it while it walks the
    bars (a pure function of bars, R3): it starts on the bar an instance arms and restarts when a newer setup replaces
    the instance (R8), so it reads only bars up to the trigger (no look-ahead). Used for "the pullback low", "the dip's
    low", "the excursion's extreme" and "the session low since the cross-under". Allowed for an invalidation only."""
    extreme: Literal["since_setup"]


class InvalidationSwitch(Switch):
    """An invalidation per value of the enum setting `by`: an expression, or the extreme since the setup bar (TPL-7: or a
    nested invalidation switch)."""
    variants: Dict[str, Optional[Union[Sided, SetupExtreme, "InvalidationSwitch"]]] = Field(min_length=1)   # type: ignore[assignment]


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
    # Step 5 (TPL-5, E5): the instance lives within the session of its setup bar only. The session is the one the
    # exchange calendar gives each bar's own timestamp (the runtime's session map, as SessionVWAP's reset) - no time-of-day
    # rule; the first bar of a later session voids the instance. Intraday timeframes only.
    session_bound: StrictBool = False


class SetupSwitch(_Strict):
    """A whole setup per value of the enum setting `by` (e.g. the window: one opportunity per wave is a state setup,
    every pullback an event setup). TPL-7: a variant may itself be a setup switch."""
    by: str = Field(pattern=_KEY)
    variants: Dict[str, Union[Setup, "SetupSwitch"]] = Field(min_length=1)


class ContextSwitch(_Strict):
    """A context filter per value of the enum setting `by` (null: no filter for that value, e.g. trend relation "any").
    TPL-7: a variant may itself be a switch (`Switch`) on another enum setting."""
    by: str = Field(pattern=_KEY)
    variants: Dict[str, Optional[Union[Sided, Switch]]] = Field(min_length=1)
    timeframe_param: Optional[str] = Field(default=None, pattern=_KEY)
    enabled_by: Optional[str] = Field(default=None, pattern=_KEY)


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


class SignalTextSwitch(_Strict):
    """TPL-7 (family F version 2): the signal text per value of the enum setting `by` (a pack's member's own words)."""
    by: str = Field(pattern=_KEY)
    variants: Dict[str, SignalText] = Field(min_length=1)


class TimeframesBy(_Strict):
    """TPL-7 (family F version 2): the signal timeframes allowed per value of the enum setting `by` (each a subset of the
    template's allowed timeframes; a choice left out allows them all)."""
    by: str = Field(pattern=_KEY)
    allowed: Dict[str, Tuple[str, ...]] = Field(min_length=1)


def nested(part: object) -> List[object]:
    """Every switch in a part, the outer one first, then each nested one (TPL-7); empty for a part without a switch."""
    if not isinstance(part, (Switch, SetupSwitch, ContextSwitch)):
        return []
    out: List[object] = [part]
    for v in part.variants.values():
        out += nested(v)
    return out


def _repeats(part: object, outer: Tuple[str, ...]) -> bool:
    """Whether a nested switch chooses by a setting an outer switch on its path already chose by."""
    if not isinstance(part, (Switch, SetupSwitch, ContextSwitch)):
        return False
    return part.by in outer or any(_repeats(v, (*outer, part.by)) for v in part.variants.values())


def leaves(part: object) -> List[object]:
    """A part's leaf variants (every nested switch opened; None kept): the expressions / setups the settings can choose."""
    if not isinstance(part, (Switch, SetupSwitch, ContextSwitch)):
        return [part]
    return [leaf for v in part.variants.values() for leaf in leaves(v)]


class Template(_Strict):
    schema_: Literal["atp.template/1"] = Field(alias="schema")
    id: str = Field(pattern=r"^[A-G][0-9]{1,2}$")
    version: StrictInt = Field(ge=1)
    name: str = Field(min_length=1, max_length=80)
    family: Literal["A", "B", "C", "D", "E", "F", "G"]
    idea: str = Field(min_length=1, max_length=300)
    difficulty: Literal["Beginner", "Intermediate", "Advanced"]
    style: Literal["Intraday", "Swing", "Both", "Intraday (session-based)"]
    allowed_timeframes: Tuple[str, ...] = Field(min_length=1)
    params: Tuple[ParamSpec, ...] = ()
    constraints: Tuple[Constraint, ...] = ()
    sides_default: Literal["long", "short", "both"] = "both"
    context: Optional[Union[Context, ContextSwitch]] = None
    setup: Union[Setup, SetupSwitch]
    trigger: Optional[Union[Sided, Switch]] = None   # None: the setup event itself is the trigger (needs kind event + same_bar)
    # a price per side, read on the trigger bar (S6: on the losing side of the close); step 5: or the extreme since the setup
    invalidation: Union[Sided, SetupExtreme, InvalidationSwitch]
    exit_signal: Optional[Union[Sided, Switch]] = None
    evidence: Tuple[Evidence, ...] = ()
    signal_text: Union[SignalText, SignalTextSwitch]
    timeframes_by: Optional[TimeframesBy] = None            # TPL-7: the signal timeframes per choice (family F version 2)
    confirmations: Tuple[Confirmation, ...] = ()            # family G version 2: optional confirmations of the trigger

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
        refs += [(f"text part {p.token}", p.when, "bool") for p in self.text_parts()]
        texts = self.signal_text
        if isinstance(texts, SignalTextSwitch):
            spec = by_key.get(texts.by)
            if spec is None or spec.type != "enum" or set(texts.variants) != set(spec.choices):
                raise ValueError(f"signal text variants must cover exactly the choices of an enum setting, got {texts.by!r}")
        if self.timeframes_by is not None:
            spec = by_key.get(self.timeframes_by.by)
            if spec is None or spec.type != "enum" or not set(self.timeframes_by.allowed) <= set(spec.choices):
                raise ValueError(f"timeframes_by must name choices of an enum setting, got {self.timeframes_by.by!r}")
            for choice, tfs in self.timeframes_by.allowed.items():
                if not tfs or not set(tfs) <= set(self.allowed_timeframes):
                    raise ValueError(f"timeframes_by {choice}: a non-empty subset of the allowed timeframes")
        for what, key, kind in refs:
            if key is not None and (key not in by_key or by_key[key].type != kind):
                raise ValueError(f"{what} must name a {kind} setting, got {key!r}")
        switches: List[Tuple[str, Any]] = [
            (what, sw) for what, part in (("context", self.context), ("setup", self.setup), ("trigger", self.trigger),
                                          ("invalidation", self.invalidation), ("exit_signal", self.exit_signal))
            for sw in nested(part)]
        switches += [("confirmation", c) for c in self.confirmations]
        for what, sw in switches:
            spec = by_key.get(sw.by)
            if spec is None or spec.type != "enum":
                raise ValueError(f"{what} variants must be chosen by an enum setting, got {sw.by!r}")
            if set(sw.variants) != set(spec.choices):
                raise ValueError(f"{what} variants must cover exactly the choices of {sw.by}: {', '.join(spec.choices)}")
            if what == "invalidation" and any(v is None for v in sw.variants.values()):
                raise ValueError("every invalidation variant needs an expression")
        for what, part in (("context", self.context), ("setup", self.setup), ("trigger", self.trigger),
                           ("invalidation", self.invalidation), ("exit_signal", self.exit_signal)):
            if _repeats(part, ()):                               # TPL-7: a nested switch never re-chooses by an outer setting
                raise ValueError(f"{what}: a nested switch must choose by another setting")
        if len({c.by for c in self.confirmations}) != len(self.confirmations):
            raise ValueError("each confirmation needs its own setting")
        for c in self.confirmations:
            if c.variants.get(str(by_key[c.by].default)) is not None:
                raise ValueError(f"confirmation {c.by}: the default choice must be no confirmation (off by default)")
            if self.trigger is None:
                raise ValueError("a confirmation needs a trigger")
        setups = [s_ for s_ in leaves(self.setup) if isinstance(s_, Setup)]
        for setup in setups:
            if isinstance(setup.expiry_bars, str):
                key = setup.expiry_bars.removeprefix("$")
                if not setup.expiry_bars.startswith("$") or key not in by_key or by_key[key].type != "int":
                    raise ValueError("expiry_bars must be a whole number or a $setting of type int")
            elif setup.expiry_bars is not None and setup.expiry_bars < 0:
                raise ValueError("expiry_bars must not be negative")
        if any(s.session_bound for s in setups):
            if not set(self.allowed_timeframes) <= set(INTRADAY_SERIES_TFS):
                raise ValueError("a setup bound to its session needs intraday timeframes only")
        if self.trigger is None and not all(s.kind == "event" and s.same_bar for s in setups):
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

    def text_parts(self) -> List[TextPart]:
        """Every signal text part, of every signal text variant (TPL-7)."""
        texts = list(self.signal_text.variants.values()) if isinstance(self.signal_text, SignalTextSwitch) else [self.signal_text]
        return [p for t in texts for p in (*t.long, *t.short)]

    def chosen_text(self, params: Dict[str, Any]) -> SignalText:
        """The signal text the settings choose (TPL-7: a signal text switch gives its variant)."""
        texts = self.signal_text
        return texts.variants[str(params[texts.by])] if isinstance(texts, SignalTextSwitch) else texts


Switch.model_rebuild()
InvalidationSwitch.model_rebuild()
SetupSwitch.model_rebuild()
ContextSwitch.model_rebuild()
Confirmation.model_rebuild()
Template.model_rebuild()

__all__ = ["SCHEMA_VERSION", "SIGNAL_TIMEFRAME", "MA_TYPES", "SOURCES", "ParamSpec", "Constraint", "Sided", "Context", "Setup",
           "Evidence", "TextPart", "SignalText", "Template", "Switch", "SetupSwitch", "ContextSwitch", "SetupExtreme",
           "InvalidationSwitch", "SignalTextSwitch", "TimeframesBy", "Confirmation", "nested", "leaves"]
