"""Strategy templates: binding a template to the ScreenQL registry, and the user's settings to a template.

`validate_template` is the static check a library template must pass (every allowed timeframe, the default settings);
`resolve_settings` checks the user's settings (types, bounds, enum values, the constraints) with plain messages; and
`compile_recipe` turns both into the parsed, validated expressions the engine runs.

Binding rules (owner order section 0 R1, section 1):
* every expression is parsed and validated by the screener's own validator against the REAL registry signatures
  (`validate_study`: arity, argument types, choices, bounds, literal-only settings, units, timeframes, look-ahead);
* a function in `LAST_BAR_ONLY` is refused by name - read as a series it repeats the last answer backwards
  (look-ahead) and it takes no @timeframe; so is a cross-sectional rank;
* a `$name` must be a declared setting; a timeframe setting is not a ScreenQL value and never reaches an expression as
  `$name` - it reaches one only as `@{name}` (family G, WB3), replaced by its value (`@1h`) before parsing and then
  validated like a written timeframe. A yes / no setting may be read where the registry takes a yes / no argument
  (step 3, R7: RetestEntry's `reversal` - "wait for a reversal at the level"); the validator checks its type there;
* a part with `variants` compiles only the expression of the chosen enum value (family G, WB3);
* step 5: an invalidation that is the extreme since the setup bar (`SetupExtreme`) compiles no expression - the engine
  keeps it while it walks the bars (`Recipe.invalidation_since_setup`);
* TPL-7 (family F version 2): a variant may itself be a switch, opened one level after the other (`chosen`); the signal
  timeframe may be limited per choice (`Template.timeframes_by`).
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Set, Tuple, Union

from app.screener import nodes as n
from app.screener.parser import ScreenQLSyntaxError, parse
from app.screener.registry import FUNCTIONS, LAST_BAR_ONLY, NUM, BOOL
from app.screener.validator import validate_study
from app.strategy_templates import language
from app.strategy_templates.schema import (SIGNAL_TIMEFRAME, ContextSwitch, Setup, SetupExtreme, SetupSwitch, Sided, Switch,
                                           Template)

Side = Literal["long", "short"]
SIDES: Tuple[Side, ...] = ("long", "short")
ENGINE_VERSION = "1"            # bumps when the state machine's semantics change (part of the params hash)


class TemplateError(ValueError):
    """A template or its settings cannot run; `problems` are plain messages."""

    def __init__(self, problems: List[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Settings:
    """The user's choices for one template: the signal timeframe, the sides and every parameter (defaults filled in)."""
    timeframe: str
    sides: Literal["long", "short", "both"]
    params: Dict[str, Any]

    def side_list(self) -> Tuple[Side, ...]:
        return SIDES if self.sides == "both" else (self.sides,)


@dataclass
class Part:
    """One compiled expression of one side: its AST, the settings it reads, the bars it needs per timeframe."""
    text: str
    ast: Any
    kind: str                                   # num / bool
    params: Dict[str, Any]
    lookback: Dict[str, int]


@dataclass
class Recipe:
    template: Template
    settings: Settings
    context_timeframe: Optional[str]
    context_on: bool
    parts: Dict[Tuple[str, Side], Part]        # ("setup" | "trigger" | "context" | "invalidation" | "arm_level" | "exit_signal" | "evidence:<name>", side)
    expiry_bars: Optional[int]
    lookback: Dict[str, int] = field(default_factory=dict)
    required_flags: Set[str] = field(default_factory=set)
    params_hash: str = ""
    setup: Optional[Setup] = None               # the setup in force (a SetupSwitch's chosen variant); see `chosen_setup`
    has_trigger: bool = True                    # False: the setup event itself is the signal
    # step 5 (TPL-5): the invalidation is the extreme since the setup bar (the engine keeps it; no expression is compiled)
    invalidation_since_setup: bool = False

    @property
    def chosen_setup(self) -> Setup:
        if self.setup is not None:
            return self.setup
        setup: Setup = chosen(self.template.setup, self.settings)
        return setup

    def part(self, name: str, side: Side) -> Optional[Part]:
        return self.parts.get((name, side))

    @property
    def min_bars(self) -> int:
        """Signal-timeframe bars before the recipe can say anything (S3; the engine's warm-up adds the indicators')."""
        return int(self.lookback.get(self.settings.timeframe, 1))


def _tf_minutes(tf: str) -> int:
    return n.TF_MINUTES[tf]


_TF_PLACEHOLDER = re.compile(r"@\{([a-z][a-z0-9_]{0,31})\}")


def fill_timeframes(text: str, template: Template, settings: "Settings", what: str) -> str:
    """Family G (WB3): `@{key}` -> `@<the timeframe setting's value>`. A key that is not a timeframe setting is refused."""
    problems: List[str] = []

    def sub(m: "re.Match[str]") -> str:
        key = m.group(1)
        spec = next((p for p in template.params if p.key == key), None)
        if spec is None or spec.type != "timeframe":
            problems.append(f"{what}: @{{{key}}} must name a timeframe setting of this template")
            return m.group(0)
        return "@" + str(settings.params[key])
    out = _TF_PLACEHOLDER.sub(sub, text)
    if problems:
        raise TemplateError(problems)
    return out


def chosen(part: Any, settings: "Settings") -> Any:
    """A part as the user's settings choose it: a Switch / SetupSwitch / ContextSwitch gives the variant of its enum value
    (None when that value has no expression), a nested switch opened in turn (TPL-7); anything else is itself."""
    while isinstance(part, (Switch, SetupSwitch, ContextSwitch)):
        part = part.variants[str(settings.params[part.by])]
    return part


def resolve_settings(template: Template, timeframe: Optional[str] = None, sides: Optional[str] = None,
                     params: Optional[Dict[str, Any]] = None) -> Settings:
    """The user's settings checked against the template (defaults for what is left out). Raises TemplateError with every
    problem at once, in words the settings form can show."""
    problems: List[str] = []
    tf = timeframe or template.allowed_timeframes[0]
    if tf not in template.allowed_timeframes:
        problems.append(f"the timeframe must be one of {', '.join(template.allowed_timeframes)}")
    side_choice = sides or template.sides_default
    if side_choice not in ("long", "short", "both"):
        problems.append("sides must be long, short or both")
    given = dict(params or {})
    unknown = sorted(set(given) - {p.key for p in template.params})
    if unknown:
        problems.append(f"unknown setting(s): {', '.join(unknown)}")
    values: Dict[str, Any] = {}
    for p in template.params:
        value = given.get(p.key, p.default)
        if p.type == "float" and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        if p.type == "int" and isinstance(value, float) and value.is_integer():
            value = int(value)
        problem = p.problem(value)
        if problem:
            problems.append(problem)
        values[p.key] = value
    if not problems:
        for c in template.constraints:
            if c.when is not None and not values[c.when]:
                continue
            left = tf if c.left == SIGNAL_TIMEFRAME else values[c.left]
            right = tf if c.right == SIGNAL_TIMEFRAME else values[c.right]
            if c.op == "whole_bars":                                 # TPL-8: minutes in whole bars of the timeframe
                if int(left) % _tf_minutes(str(right)):
                    problems.append(c.message)
                continue
            kinds = {template.param(k).type for k in (c.left, c.right) if k != SIGNAL_TIMEFRAME}
            if "timeframe" in kinds or SIGNAL_TIMEFRAME in (c.left, c.right):
                left, right = _tf_minutes(str(left)), _tf_minutes(str(right))
            ok = {"<": left < right, "<=": left <= right, ">": left > right, ">=": left >= right, "!=": left != right}[c.op]
            if not ok:
                problems.append(c.message)
    by_tf = template.timeframes_by
    if not problems and by_tf is not None and tf in template.allowed_timeframes:
        allowed = by_tf.allowed.get(str(values[by_tf.by]))
        if allowed is not None and tf not in allowed:                # TPL-7: e.g. a pack member that has no Weekly chart
            spec = template.param(by_tf.by)
            label = spec.choice_labels[spec.choices.index(str(values[by_tf.by]))] if spec.choice_labels else str(values[by_tf.by])
            problems.append(f"{label} runs on the {', '.join(language.TIMEFRAME_WORDS[t] for t in allowed)} timeframes only")
    if problems:
        raise TemplateError(problems)
    return Settings(tf, side_choice, values)  # type: ignore[arg-type]


def _on_timeframe(ast: Any, tf: str) -> Any:
    """A copy of `ast` where every field and function that names no timeframe runs on `tf` (the context timeframe)."""
    out = copy.deepcopy(ast)
    for node in n.walk(out):
        if isinstance(node, n.Field) and node.tf is None:
            node.tf = tf
        elif isinstance(node, n.Call) and node.tf is None:
            spec = FUNCTIONS.get(node.name)
            if spec is not None and spec.timeframed:
                node.tf = tf
    return out


def _calls(ast: Any) -> List[n.Call]:
    return [node for node in n.walk(ast) if isinstance(node, n.Call)]


def _param_names(ast: Any) -> Set[str]:
    return {node.name for node in n.walk(ast) if isinstance(node, n.Param)}


def compile_part(text: str, *, what: str, want: str, template: Template, settings: Settings, on_tf: Optional[str] = None) -> Part:
    """Parse, bind and validate one expression. `want` is "bool" (a condition) or "num" (a price level)."""
    text = fill_timeframes(text, template, settings, what)
    try:
        ast = parse(text)
    except ScreenQLSyntaxError as exc:
        raise TemplateError([f"{what}: {exc}"]) from exc
    problems: List[str] = []
    for call in _calls(ast):
        if call.name in LAST_BAR_ONLY:
            problems.append(f"{what}: {call.name} is answered for the last bar only, so it has no causal series (R1); "
                            "use its series form (Pattern, ReversalAt, DowTrend, SwingBreakType)")
        elif call.name not in FUNCTIONS:
            problems.append(f"{what}: unknown function {call.name!r}")
    declared = {p.key: p for p in template.params}
    names = _param_names(ast)
    for name in sorted(names):
        if name not in declared:
            problems.append(f"{what}: ${name} is not a setting of this template")
        elif declared[name].type == "timeframe":
            problems.append(f"{what}: ${name} is a {declared[name].type} setting and cannot be read inside an expression")
    if problems:
        raise TemplateError(problems)
    if on_tf is not None:
        ast = _on_timeframe(ast, on_tf)
    params = {k: settings.params[k] for k in names}
    check = validate_study(ast, base_tf=settings.timeframe, params=params)
    if not check.validated.ok:
        raise TemplateError([f"{what}: {p.message}" for p in check.validated.problems])
    kind = check.kind
    if (want == "bool" and kind != BOOL) or (want == "num" and kind != NUM):
        raise TemplateError([f"{what}: must be {'a condition (true / false)' if want == 'bool' else 'a number'}, got {kind}"])
    if want == "num" and check.unit not in ("price", None):
        raise TemplateError([f"{what}: must be a price, got a {check.unit} value"])
    return Part(text, ast, str(kind), params, dict(check.validated.lookback))


def _pa_settings_fingerprint() -> str:
    """R9: the price-action settings the swing / reversal / retest functions read (there is no version number in
    app/price_action/pa_settings.py, so the hash covers their effective content)."""
    from app.price_action import pa_settings
    return hashlib.sha256(json.dumps(pa_settings.settings(), sort_keys=True, default=str).encode()).hexdigest()[:16]


def _wave_settings_fingerprint(settings: Settings) -> str:
    """R9 for family G: the wave reading's settings in force (WAVE-6) - today's values, overridden by a template setting of
    the same name."""
    from app.price_action.waves.settings import WAVE_DEFAULTS
    effective = {k: settings.params.get(k, v) for k, v in WAVE_DEFAULTS.items()}
    return hashlib.sha256(json.dumps(effective, sort_keys=True, default=str).encode()).hexdigest()[:16]


def params_hash(template: Template, settings: Settings) -> str:
    """R9 / section 4: what a signal's provenance records - the same template version, settings, price-action settings
    and engine semantics give the same hash (and the same signals, S11). A family G template also records the wave
    reading's settings; every other family's hash is exactly what it was before family G."""
    body = {"template": template.id, "version": template.version, "schema": "atp.template/1", "engine": ENGINE_VERSION,
            "timeframe": settings.timeframe, "sides": settings.sides, "params": settings.params, "pa_settings": _pa_settings_fingerprint()}
    if template.family == "G":
        body["wave_settings"] = _wave_settings_fingerprint(settings)
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:20]


def compile_recipe(template: Template, settings: Settings) -> Recipe:
    problems: List[str] = []
    parts: Dict[Tuple[str, Side], Part] = {}
    ctx = template.context
    ctx_sided: Optional[Sided] = chosen(ctx, settings) if ctx is not None else None
    setup: Setup = chosen(template.setup, settings)
    trigger: Optional[Sided] = chosen(template.trigger, settings)
    invalidation: Union[Sided, SetupExtreme] = chosen(template.invalidation, settings)
    exit_signal: Optional[Sided] = chosen(template.exit_signal, settings)
    if trigger is None and not (setup.kind == "event" and setup.same_bar):
        problems.append("without a trigger the setup event is the signal: kind event with same_bar true")
    context_on = ctx is not None and ctx_sided is not None and (ctx.enabled_by is None or bool(settings.params[ctx.enabled_by]))
    context_tf = str(settings.params[ctx.timeframe_param]) if ctx is not None and ctx.timeframe_param else None
    if context_on and context_tf is not None and _tf_minutes(context_tf) < _tf_minutes(settings.timeframe):
        problems.append("the context timeframe must not be finer than the signal timeframe")

    def add(name: str, sided: Optional[Sided], want: str, on_tf: Optional[str] = None, *, short_text: Optional[str] = None,
            long_text: Optional[str] = None) -> None:
        for side in SIDES:
            text = (long_text if side == "long" else short_text) if sided is None else getattr(sided, side)
            if text is None:
                continue
            try:
                parts[(name, side)] = compile_part(text, what=f"{name} ({side})", want=want, template=template, settings=settings,
                                                   on_tf=on_tf)
            except TemplateError as exc:
                problems.extend(exc.problems)

    add("setup", None, "bool", long_text=setup.long, short_text=setup.short)
    if trigger is not None:
        add("trigger", trigger, "bool")
    since_setup = isinstance(invalidation, SetupExtreme)
    if isinstance(invalidation, Sided):
        add("invalidation", invalidation, "num")
    if setup.arm_level is not None:
        add("arm_level", setup.arm_level, "num")
    if exit_signal is not None:
        add("exit_signal", exit_signal, "bool")
    if ctx_sided is not None and context_on:
        add("context", None, "bool", context_tf, long_text=ctx_sided.long, short_text=ctx_sided.short)
    for ev in template.evidence:
        add(f"evidence:{ev.name}", None, "bool", long_text=ev.long, short_text=ev.short)
    for part in template.text_parts():
        if part.token not in language.TOKENS:
            problems.append(f"signal text: unknown token {part.token!r}")
    expiry: Optional[int] = None
    raw = setup.expiry_bars
    if isinstance(raw, str):
        expiry = int(settings.params[raw.removeprefix("$")])
    elif raw is not None:
        expiry = int(raw)
    if problems:
        raise TemplateError(problems)
    lookback: Dict[str, int] = {}
    flags: Set[str] = set()
    for (_, side), compiled_part in parts.items():
        if side not in settings.side_list():
            continue
        for tf, bars in compiled_part.lookback.items():
            lookback[tf] = max(lookback.get(tf, 0), bars)
        for call in _calls(compiled_part.ast):
            spec = FUNCTIONS[call.name]
            flags.update(f for f in (spec.flag, *spec.also_flags) if f)
    recipe = Recipe(template, settings, context_tf if context_on else None, context_on, parts, expiry, lookback, flags,
                    setup=setup, has_trigger=trigger is not None, invalidation_since_setup=since_setup)
    recipe.params_hash = params_hash(template, settings)
    return recipe


def _switch_paths(part: Any) -> List[Dict[str, str]]:
    """The choices that reach each leaf variant of a part (TPL-7: through every nested switch), e.g.
    [{source: supertrend_flip}, {source: ma_pullback, trigger_bar: later}, {source: ma_pullback, trigger_bar: same}]."""
    if not isinstance(part, (Switch, SetupSwitch, ContextSwitch)):
        return [{}]
    return [{part.by: choice, **inner} for choice, v in part.variants.items() for inner in _switch_paths(v)]


def _switch_variants(template: Template) -> List[Dict[str, Any]]:
    """The settings that exercise every variant of every switch (family G, WB3), one path at a time, in the order the parts
    are listed; a path that only names default choices is the default settings themselves and is left out."""
    defaults = {p.key: p.default for p in template.params}
    out: List[Dict[str, Any]] = []
    for part in (template.context, template.setup, template.trigger, template.invalidation, template.exit_signal):
        for path in _switch_paths(part):
            if any(defaults[k] != v for k, v in path.items()) and path not in out:
                out.append(path)
    return out


def validate_template(template: Template) -> List[str]:
    """The static check of a library template: its text tokens, and every allowed timeframe with the default settings
    (a context timeframe default finer than the signal timeframe is skipped - the constraint refuses that pair)."""
    problems: List[str] = []
    declared = {p.key: p.type for p in template.params}
    for part in template.text_parts():
        if part.token not in language.TOKENS:
            problems.append(f"signal text: unknown token {part.token!r}")
        else:
            problems += [f"token {part.token}: {p}" for p in language.guard(language.TOKENS[part.token])]
            for placeholder, key in language.TIMEFRAME_SETTING_PLACEHOLDERS.items():
                if "{" + placeholder + "}" in language.TOKENS[part.token] and declared.get(key) != "timeframe":
                    problems.append(f"token {part.token}: {{{placeholder}}} needs the timeframe setting {key!r}")
    compiled = 0
    ctx = template.context
    for tf in template.allowed_timeframes:
        variants: List[Dict[str, Any]] = [{}]
        if ctx is not None:                           # also with the context filter on, on the first timeframe above this one
            on: Optional[Dict[str, Any]] = {ctx.enabled_by: True} if ctx.enabled_by else {}
            if ctx.timeframe_param and on is not None:
                above = [c for c in template.param(ctx.timeframe_param).choices if _tf_minutes(c) > _tf_minutes(tf)]
                on = {**on, ctx.timeframe_param: above[0]} if above else None     # no context timeframe above this one
            if on:
                variants.append(on)
        variants += _switch_variants(template)        # family G (WB3): every variant of every switch, one at a time
        for params in variants:
            try:
                settings = resolve_settings(template, tf, params=params)
            except TemplateError:
                continue                              # e.g. the default context timeframe is not above this one
            try:
                compile_recipe(template, settings)
                compiled += 1
            except TemplateError as exc:
                problems += [f"@{tf}: {p}" for p in exc.problems]
    if not compiled and not problems:
        problems.append("no allowed timeframe compiles with the default settings")
    return sorted(set(problems))


__all__ = ["TemplateError", "Settings", "Recipe", "Part", "resolve_settings", "compile_recipe", "compile_part", "validate_template",
           "fill_timeframes", "chosen", "params_hash", "SIDES", "ENGINE_VERSION"]
