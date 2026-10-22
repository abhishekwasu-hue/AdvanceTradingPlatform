"""Strategy templates step 2: a template with the user's settings as ONE strategy id (no table, no migration).

A deployment, a backtest run, an order and a trade all record a `strategy_id` (at most 100 characters; the execution
intents 80). A template adopted with settings is the id

    template:<template id>:<version>:<signal timeframe>:<sides>:<value>,<value>,...

with one value per setting in the template's own order, EMPTY when it is the default (so the default settings of D2 on
15 minutes are `template:D2:1:15m:both:,,,,,,`). Yes / no is 1 / 0, numbers are written in full (floats kept to
FLOAT_DECIMALS decimals), choices as themselves. The id is CANONICAL: `decode` accepts exactly what `encode` writes for
the same settings, so the same template with the same settings is always the same id (the deployment uniqueness rule
and the provenance rely on it).

Family G (the wave templates) carries some twenty settings, so its choices are written as their POSITION in the setting's
choices (`COMPACT_FAMILIES`; "aggressive" -> "0") to stay within the 80 characters; the template version pins the order.
Every other family's id is written exactly as before.

A template with optional confirmations (family G version 2, the wave companions: some twenty-five settings) leaves out the
trailing values that are the default (`template:G1:2:15m:both:,,,,,,0` instead of `...,0,,,,,,`), so its id stays within
the 80 characters; a missing value at the end reads as the default. Every template without confirmations is written
exactly as before.

An id naming an older version of a template resolves to that version when the library keeps it (TPL-7:
`templates/archive/`), else it reads "adopt it again".

The id IS the stored provenance (open question TPL-Q2): the template id and version and every setting are on each
deployment, order and trade that carries it, and the params hash is recomputed from it (`compile.params_hash`). Only the
setup instance of a signal is not stored: it is on the Signal in memory (`Signal.provenance`, never shown to a user) and
in the worker's log line; it can be recomputed from the bars (the series is a pure function of them, R3).
"""
from __future__ import annotations

from typing import Any, List, Tuple

from app.strategy_templates import library
from app.strategy_templates.compile import Settings, TemplateError, resolve_settings
from app.strategy_templates.schema import ParamSpec, Template

PREFIX = "template:"
MAX_LENGTH = 80                 # the shortest strategy_id column (execution_intents.strategy_id)
FLOAT_DECIMALS = 6
_SEPARATORS = (":", ",")
COMPACT_FAMILIES = frozenset({"G"})   # choices written as their position (family G: many settings, see above)


def is_template_id(strategy_id: str) -> bool:
    return strategy_id.startswith(PREFIX)


def _write(spec: ParamSpec, value: Any, compact: bool = False) -> str:
    if value == spec.default and type(value) is type(spec.default) or (spec.type == "float" and float(value) == float(spec.default)):
        return ""
    if compact and spec.type == "enum":
        return str(spec.choices.index(str(value)))
    if spec.type == "bool":
        return "1" if value else "0"
    if spec.type == "int":
        return str(int(value))
    if spec.type == "float":
        return repr(round(float(value), FLOAT_DECIMALS))
    text = str(value)
    if any(sep in text for sep in _SEPARATORS):
        raise TemplateError([f"{spec.label}: this choice cannot be saved"])
    return text


def _read(spec: ParamSpec, text: str, compact: bool = False) -> Any:
    if text == "":
        return spec.default
    if compact and spec.type == "enum":
        if not text.isdigit() or int(text) >= len(spec.choices) or str(int(text)) != text:
            raise ValueError(text)
        return spec.choices[int(text)]
    if spec.type == "bool":
        if text not in ("0", "1"):
            raise ValueError(text)
        return text == "1"
    if spec.type == "int":
        return int(text)
    if spec.type == "float":
        return float(text)
    return text


def _rounded(template: Template, settings: Settings) -> Settings:
    params = {k: (round(float(v), FLOAT_DECIMALS) if template.param(k).type == "float" else v) for k, v in settings.params.items()}
    return Settings(settings.timeframe, settings.sides, params)


def encode(template: Template, settings: Settings) -> str:
    """The strategy id of `template` with `settings` (already resolved). Raises TemplateError when it would not fit."""
    compact = template.family in COMPACT_FAMILIES
    values = [_write(p, settings.params[p.key], compact) for p in template.params]
    if template.confirmations:                    # family G version 2: the trailing defaults are left out
        while values and values[-1] == "":
            values.pop()
    out = f"{PREFIX}{template.id}:{template.version}:{settings.timeframe}:{settings.sides}:{','.join(values)}"
    if len(out) > MAX_LENGTH:
        raise TemplateError(["These settings are too long to save: use fewer decimals"])
    return out


def adopt(template: Template, timeframe: str | None = None, sides: str | None = None,
          params: dict[str, Any] | None = None) -> Tuple[str, Settings]:
    """The user's settings checked (plain messages, TemplateError) and kept to FLOAT_DECIMALS decimals, with their id."""
    settings = _rounded(template, resolve_settings(template, timeframe, sides, params))
    settings = resolve_settings(template, settings.timeframe, settings.sides, settings.params)
    return encode(template, settings), settings


def decode(strategy_id: str) -> Tuple[Template, Settings]:
    """The template and settings a template strategy id names. KeyError (a plain message) for an unknown, outdated or
    non-canonical id; the settings are checked again (bounds, constraints) as if the user had just chosen them."""
    if not is_template_id(strategy_id):
        raise KeyError(f"Unknown strategy id: {strategy_id}")
    parts: List[str] = strategy_id[len(PREFIX):].split(":")
    if len(parts) != 5:
        raise KeyError(f"Unknown strategy id: {strategy_id}")
    template_id, version, timeframe, sides, values = parts
    try:
        template = library.get(template_id)
    except KeyError:
        raise KeyError(f"Unknown strategy id: {strategy_id}") from None
    if version != str(template.version):
        # TPL-7: a kept earlier version (e.g. a pack before it gained members) resolves to itself, word for word
        if not (version.isdigit() and str(int(version)) == version):
            raise KeyError(f"Unknown strategy id: {strategy_id}")
        try:
            template = library.get_version(template_id, int(version))
        except KeyError:
            raise KeyError("This strategy template has been updated since it was adopted: adopt it again") from None
    texts = values.split(",") if template.params else ([] if values == "" else [values])
    if template.confirmations and len(texts) < len(template.params):
        texts += [""] * (len(template.params) - len(texts))      # family G version 2: trailing defaults left out
    if len(texts) != len(template.params):
        raise KeyError(f"Unknown strategy id: {strategy_id}")
    try:
        compact = template.family in COMPACT_FAMILIES
        params = {p.key: _read(p, t, compact) for p, t in zip(template.params, texts)}
        settings = resolve_settings(template, timeframe, sides, params)
    except (ValueError, TemplateError):
        raise KeyError(f"Unknown strategy id: {strategy_id}") from None
    if encode(template, settings) != strategy_id:
        raise KeyError(f"Unknown strategy id: {strategy_id}")
    return template, settings


__all__ = ["PREFIX", "MAX_LENGTH", "FLOAT_DECIMALS", "COMPACT_FAMILIES", "is_template_id", "encode", "decode", "adopt"]
