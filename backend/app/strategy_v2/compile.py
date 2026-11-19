"""SFC P0: an `atp.strategy/2` strategy -> parsed, validated ScreenQL (the ONE evaluator's language).

Every condition becomes one ScreenQL boolean expression (a "leaf"); its operands are kept as number expressions too, so
the chart can show their values (explainability, P3) and draw them (P1). The AND / OR tree stays a tree of leaf ids: the
evaluator combines the leaves' truth on every bar, so it knows exactly which conditions held.

Comparators on closed bars ([0] = the last closed bar of the condition's timeframe):
    crosses_above  L[1] <= R[1] and L[0] > R[0]        (CrossAbove)
    crosses_below  L[1] >= R[1] and L[0] < R[0]        (CrossBelow)
    gt / lt / gte / lte                                 L[0] op R[0]
    between        low[0] <= L[0] <= high[0]           (inclusive)
    rising (n)     L[0] > L[1] > ... > L[n]
    falling (n)    L[0] < L[1] < ... < L[n]
    is_true        the yes / no function holds on [0]
    within_bars N  the condition held on at least one of the last N closed bars of its timeframe, [0] included
A missing value (warm-up, NaN) is never a match. A condition's timeframe applies to the whole condition (its crossing,
its N bars are counted on that timeframe) and its value reaches the strategy's bars only once that bar has CLOSED (the
runtime's close-time alignment).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Union

from app.screener import nodes as n
from app.screener.parser import ScreenQLSyntaxError, parse
from app.screener.registry import BOOL, FIELDS, FUNCTIONS, LAST_BAR_ONLY, NUM, Spec
from app.screener.validator import validate_study
from app.strategy_templates.compile import _on_timeframe
from app.strategy_v2.schema import BlockOperand, Condition, Group, PriceOperand, StrategyV2, ValueOperand, walk

Operand = Union[ValueOperand, PriceOperand, BlockOperand]


class StrategyError(ValueError):
    """Everything wrong with a strategy, in words a trader reads (all problems at once)."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass
class Leaf:
    """One condition, compiled."""
    id: str
    text: str                                  # the ScreenQL text (stored nowhere; for tests and debugging)
    ast: Any                                   # the boolean expression, on its timeframe
    timeframe: str
    operands: dict[str, Any] = field(default_factory=dict)       # role (left / right / low / high) -> number expression
    operand_text: dict[str, str] = field(default_factory=dict)


@dataclass
class Node:
    """The AND / OR tree over leaf ids."""
    op: str
    items: list[Union[str, "Node"]]


@dataclass
class Compiled:
    strategy: StrategyV2
    timeframe: str
    leaves: dict[str, Leaf]
    trees: dict[str, Node]                     # entry_long / entry_short / exit_long / exit_short
    lookback: dict[str, int]                   # bars needed per timeframe (the validator's)
    fetch_lookback: dict[str, int]
    params_hash: str
    extras: dict[str, Any] = field(default_factory=dict)      # exit-level series (ATR, swings) by name

    @property
    def min_bars(self) -> int:
        return int(self.lookback.get(self.timeframe, 1))


def _literal(value: Any, arg_type: str) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(int(value)) if float(value).is_integer() else repr(float(value))
    text = str(value)
    if arg_type in (NUM, BOOL) and text in FIELDS:
        return text                            # a price series as the input ("close", "high" ...)
    return json.dumps(text)


def _call(op: BlockOperand, problems: list[str], where: str) -> str | None:
    spec: Spec | None = FUNCTIONS.get(op.block)
    if spec is None:
        problems.append(f"{where}: {op.block} is not something ATP computes")
        return None
    if op.block in LAST_BAR_ONLY:
        problems.append(f"{where}: {op.block} has a value on the last bar only, so it cannot be a condition over the chart")
        return None
    known = {a.name: a for a in (*spec.args, *spec.kwargs)}
    for key in op.params:
        if key not in known:
            problems.append(f"{where}: {op.block} has no setting '{key}'")
    args: list[str] = []
    for a in spec.args:
        if a.name in op.params:
            args.append(_literal(op.params[a.name], a.type))
        elif a.required:
            problems.append(f"{where}: {op.block} needs its '{a.name}' setting")
            return None
        else:
            break                              # positional: an unset optional ends the list (the registry default applies)
    for a in spec.kwargs:
        if a.name in op.params:
            args.append(f"{a.name}={_literal(op.params[a.name], a.type)}")
    text = f"{op.block}({', '.join(args)})"
    return text + (f"[{op.offset}]" if op.offset else "")


def operand_text(op: Operand, problems: list[str], where: str) -> str | None:
    if isinstance(op, ValueOperand):
        return repr(float(op.value))
    if isinstance(op, PriceOperand):
        return op.field + (f"[{op.offset}]" if op.offset else "")
    return _call(op, problems, where)


def _shift(text: str, k: int) -> str:
    """The same series k bars earlier: `[k]` on a field / call (an existing offset adds up)."""
    if text.endswith("]") and "[" in text:
        base, _, rest = text.rpartition("[")
        return f"{base}[{int(rest[:-1]) + k}]"
    return f"{text}[{k}]"


def leaf_text(cond: Condition, problems: list[str]) -> tuple[str, dict[str, str]] | None:
    where = f"Condition {cond.id}"
    parts: dict[str, str] = {}
    for role in ("left", "right", "low", "high"):
        op = getattr(cond, role)
        if op is not None:
            t = operand_text(op, problems, where)
            if t is None:
                return None
            parts[role] = t
    L = parts["left"]
    c = cond.cmp
    if c == "crosses_above":
        text = f"CrossAbove({L}, {parts['right']})"
    elif c == "crosses_below":
        text = f"CrossBelow({L}, {parts['right']})"
    elif c in ("gt", "lt", "gte", "lte"):
        text = f"({L}) {dict(gt='>', lt='<', gte='>=', lte='<=')[c]} ({parts['right']})"
    elif c == "between":
        text = f"({L}) BETWEEN ({parts['low']}) AND ({parts['high']})"
    elif c in ("rising", "falling"):
        sign = ">" if c == "rising" else "<"
        steps = [f"({_shift(L, i) if i else L}) {sign} ({_shift(L, i + 1)})" for i in range(int(cond.bars or 1))]
        text = " AND ".join(steps)
    else:                                      # is_true
        text = L
    if cond.within_bars:
        text = f"Count({int(cond.within_bars)}, {text}) >= 1"
    return text, parts


def _tree(group: Group) -> Node:
    return Node(group.op, [item.id if isinstance(item, Condition) else _tree(item) for item in group.items])


def params_hash(strategy: StrategyV2) -> str:
    payload = strategy.model_dump(mode="json", by_alias=True, exclude={"name", "notes"})
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def compile_strategy(strategy: StrategyV2) -> Compiled:
    """Parse and validate every condition against the REAL registry (arity, types, units, timeframes, look-ahead). A
    condition's timeframe must be the strategy's or a higher one that the strategy's bars divide exactly."""
    problems: list[str] = []
    base = strategy.timeframe
    leaves: dict[str, Leaf] = {}
    lookback: dict[str, int] = {}
    fetch: dict[str, int] = {}
    for role, group in strategy.groups().items():
        for cond, _depth in walk(group):
            tf = cond.timeframe or base
            if tf != base and (n.TF_MINUTES[tf] < n.TF_MINUTES[base] or n.TF_MINUTES[tf] % n.TF_MINUTES[base]):
                problems.append(f"Condition {cond.id}: its timeframe {tf} must be {base} or a higher one made of whole {base} bars")
                continue
            built = leaf_text(cond, problems)
            if built is None:
                continue
            text, parts = built
            try:
                ast = parse(text)
            except ScreenQLSyntaxError as exc:
                problems.append(f"Condition {cond.id}: {exc}")
                continue
            if tf != base:
                ast = _on_timeframe(ast, tf)
            check = validate_study(ast, base_tf=base)
            if not check.validated.ok:
                problems.extend(f"Condition {cond.id}: {p.message}" for p in check.validated.problems)
                continue
            if check.kind != BOOL:
                problems.append(f"Condition {cond.id}: must be true / false (use a comparison with a value)")
                continue
            operands: dict[str, Any] = {}
            for r, t in parts.items():
                try:
                    o = parse(t)
                except ScreenQLSyntaxError:
                    continue
                o = _on_timeframe(o, tf) if tf != base else o
                if validate_study(o, base_tf=base).kind == NUM:
                    operands[r] = o
            leaves[cond.id] = Leaf(cond.id, text, ast, tf, operands, parts)
            for t, bars in check.validated.lookback.items():
                lookback[t] = max(lookback.get(t, 0), int(bars))
            for t, bars in {**check.validated.lookback, **check.validated.fetch_lookback}.items():
                fetch[t] = max(fetch.get(t, 0), int(bars), int(check.validated.lookback.get(t, 0)))
    extras: dict[str, Any] = {}
    for rule in _level_rules(strategy):
        for name, text in _level_series(rule).items():
            ast = parse(text)
            check = validate_study(ast, base_tf=base)
            if not check.validated.ok:
                problems.extend(f"Exit: {p.message}" for p in check.validated.problems)
                continue
            extras[name] = ast
            for t, bars in check.validated.lookback.items():
                lookback[t] = max(lookback.get(t, 0), int(bars))
                fetch[t] = max(fetch.get(t, 0), int(bars))
    if problems:
        raise StrategyError(problems)
    trees = {role: _tree(group) for role, group in strategy.groups().items()}
    return Compiled(strategy, base, leaves, trees, lookback, fetch, params_hash(strategy), extras)


def _level_rules(strategy: StrategyV2) -> list[Any]:
    ex = strategy.exit
    return [r for r in ((ex.stop, ex.target, ex.trailing) if ex is not None else ()) if r is not None]


def _level_series(rule: Any) -> dict[str, str]:
    """The price series an exit level reads on the strategy's own bars (an ATR distance, the last swing)."""
    if rule.kind == "atr":
        return {f"atr:{rule.atr_period}": f"ATR({int(rule.atr_period)})"}
    if rule.kind == "swing":
        d = int(rule.swing_degree)
        return {f"swing_low:{d}": f"SwingLow({d})", f"swing_high:{d}": f"SwingHigh({d})"}
    return {}


__all__ = ["StrategyError", "Leaf", "Node", "Compiled", "compile_strategy", "leaf_text", "operand_text", "params_hash"]
