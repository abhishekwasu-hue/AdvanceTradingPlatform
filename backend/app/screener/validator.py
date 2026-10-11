"""S1a (ADR-0021 §3): the validator that runs before every screen - types and units, registry names and arities,
timeframe compatibility, look-ahead, parameters and a per-tenant cost cap. A refused screen says why and where
(the character position, or the node path for a builder tree).

What it returns (`Validated`) is also the run plan's input: the timeframes used, the bars of history needed per
timeframe (offset + window), whether the screen is cross-sectional, and its cost. Everything is computed on finalised
bars only - the executor never reads a bar that has not closed, so `[0]@1d` inside an intraday screen means the last
*completed* day (the S1b runtime enforces it; `closed_bars_only` is always true here).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from app.screener import nodes as n
from app.screener.registry import BOOL, CAT, FIELDS, FUNCTIONS, MAX_WINDOW, NUM, STR, Spec

MAX_OFFSET = 500
DEFAULT_COST_CAP = 200.0
ANY_UNIT = "any"                      # a literal or a parameter: takes the other side's unit


@dataclass
class Problem:
    message: str
    pos: Optional[int] = None

    def as_dict(self) -> Dict[str, Any]:
        return {"message": self.message, "pos": self.pos}


@dataclass
class Validated:
    ok: bool
    problems: List[Problem] = field(default_factory=list)
    timeframes: Set[str] = field(default_factory=set)
    lookback: Dict[str, int] = field(default_factory=dict)          # timeframe -> bars of history needed
    cost: float = 0.0
    cross_sectional: bool = False
    params_used: Set[str] = field(default_factory=set)
    closed_bars_only: bool = True

    def as_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "problems": [p.as_dict() for p in self.problems], "timeframes": sorted(self.timeframes, key=lambda tf: n.TF_MINUTES[tf]),
                "lookback": self.lookback, "cost": round(self.cost, 2), "cross_sectional": self.cross_sectional,
                "params_used": sorted(self.params_used), "closed_bars_only": True}


T = Tuple[str, Optional[str]]          # (type, unit)


class _Checker:
    def __init__(self, base_tf: str, params: Dict[str, Any]) -> None:
        self.base_tf, self.params = base_tf, params
        self.out = Validated(True)

    def err(self, message: str, node: Any) -> None:
        self.out.ok = False
        self.out.problems.append(Problem(message, getattr(node, "pos", None)))

    def _tf(self, node: Any) -> str:
        tf = node.tf or self.base_tf
        if tf not in n.TF_MINUTES:
            self.err(f"unknown timeframe @{tf}", node)
            return self.base_tf
        if n.TF_MINUTES[tf] < n.TF_MINUTES[self.base_tf]:
            self.err(f"@{tf} is finer than the screen's timeframe @{self.base_tf}; a {tf} value has no single value per {self.base_tf} bar", node)
        return tf

    def _need(self, tf: str, bars: int) -> None:
        self.out.timeframes.add(tf)
        self.out.lookback[tf] = max(self.out.lookback.get(tf, 0), bars)

    def _offset(self, node: Any) -> int:
        if node.offset < 0:
            self.err("a negative offset reads a future bar (look-ahead)", node)
            return 0
        if node.offset > MAX_OFFSET:
            self.err(f"offset {node.offset} is more than {MAX_OFFSET} bars back", node)
        return node.offset

    def window(self, arg: Any, owner: Any) -> int:
        value: Any = None
        if isinstance(arg, n.Num):
            value = arg.value
        elif isinstance(arg, n.Param):
            self.out.params_used.add(arg.name)
            value = self.params.get(arg.name)
            if value is None:
                self.err(f"parameter ${arg.name} has no value", arg)
                return 1
        else:
            self.err(f"{owner.name}: the window must be a whole number of bars (a literal or a $parameter)", arg)
            return 1
        if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) != int(value) or not 1 <= int(value) <= MAX_WINDOW:
            self.err(f"{owner.name}: the window must be a whole number from 1 to {MAX_WINDOW}, got {value}", arg)
            return 1
        return int(value)

    def check(self, node: Any, tf: Optional[str] = None) -> T:  # noqa: C901 - one switch over the node kinds
        tf = tf or self.base_tf
        if isinstance(node, n.Num):
            if not math.isfinite(node.value):
                self.err("numbers must be finite", node)
            return NUM, ANY_UNIT
        if isinstance(node, n.Str):
            return STR, None
        if isinstance(node, n.Bool):
            return BOOL, None
        if isinstance(node, n.Param):
            self.out.params_used.add(node.name)
            if node.name not in self.params:
                self.err(f"parameter ${node.name} has no value", node)
                return NUM, ANY_UNIT
            v = self.params[node.name]
            if isinstance(v, bool):
                return BOOL, None
            if isinstance(v, (int, float)):
                return NUM, ANY_UNIT
            return STR, None
        if isinstance(node, n.Field):
            spec = FIELDS.get(node.name)
            if spec is None:
                hint = " (a function needs parentheses)" if node.name in FUNCTIONS else ""
                self.err(f"unknown field {node.name!r}{hint}", node)
                return NUM, ANY_UNIT
            own_tf = self._tf(node) if node.tf else tf
            self._need(own_tf, self._offset(node) + 1)
            self.out.cost += spec.cost * 0.1
            return spec.returns, spec.unit
        if isinstance(node, n.Call):
            return self.call(node, tf)
        if isinstance(node, n.Unary):
            t, u = self.check(node.operand, tf)
            if t != NUM:
                self.err("minus needs a number", node)
            return NUM, u
        if isinstance(node, n.Binary):
            (lt, lu), (rt, ru) = self.check(node.left, tf), self.check(node.right, tf)
            if lt != NUM or rt != NUM:
                self.err(f"'{node.op}' needs numbers on both sides", node)
                return NUM, ANY_UNIT
            if node.op in "+-":
                if lu != ru and ANY_UNIT not in (lu, ru):
                    self.err(f"'{node.op}' mixes units {lu} and {ru}", node)
                return NUM, ru if lu == ANY_UNIT else lu
            if lu == ANY_UNIT or ru == ANY_UNIT:
                return NUM, ru if lu == ANY_UNIT else lu
            return NUM, "ratio"
        if isinstance(node, n.Compare):
            (lt, lu), (rt, ru) = self.check(node.left, tf), self.check(node.right, tf)
            self.comparable(node, (lt, lu), (rt, ru), node.op)
            return BOOL, None
        if isinstance(node, n.Between):
            v, lo, hi = self.check(node.value, tf), self.check(node.low, tf), self.check(node.high, tf)
            for other in (lo, hi):
                self.comparable(node, v, other, "BETWEEN")
            if v[0] != NUM:
                self.err("BETWEEN needs numbers", node)
            return BOOL, None
        if isinstance(node, n.In):
            v = self.check(node.value, tf)
            if not node.items:
                self.err("IN needs at least one value", node)
            for item in node.items:
                if not isinstance(item, (n.Num, n.Str, n.Bool, n.Param)):
                    self.err("IN takes literals only", item)
                    continue
                self.comparable(node, v, self.check(item, tf), "IN")
            return BOOL, None
        if isinstance(node, n.Logic):
            if node.op not in ("ALL", "ANY"):
                self.err(f"unknown group {node.op!r}", node)
            if len(node.items) < 1:
                self.err(f"{node.op} needs at least one condition", node)
            for item in node.items:
                if self.check(item, tf)[0] != BOOL:
                    self.err(f"{'AND' if node.op == 'ALL' else 'OR'} joins conditions (true/false), not values", item)
            return BOOL, None
        if isinstance(node, n.Not):
            if self.check(node.item, tf)[0] != BOOL:
                self.err("NOT needs a condition", node)
            return BOOL, None
        self.err(f"not a ScreenQL node: {type(node).__name__}", node)
        return BOOL, None

    def comparable(self, node: Any, left: T, right: T, op: str) -> None:
        (lt, lu), (rt, ru) = left, right
        types = {lt, rt}
        if types <= {NUM}:
            if lu != ru and ANY_UNIT not in (lu, ru):
                self.err(f"compares {lu} with {ru}", node)
            return
        if types <= {CAT, STR} and op in ("==", "!=", "IN"):
            return
        if types <= {BOOL} and op in ("==", "!=", "IN"):
            return
        self.err(f"cannot compare {lt} with {rt} using {op}", node)

    def call(self, node: n.Call, tf: str) -> T:
        spec: Optional[Spec] = FUNCTIONS.get(node.name)
        if spec is None:
            hint = " (a field takes no parentheses)" if node.name in FIELDS else ""
            self.err(f"unknown function {node.name!r}{hint}", node)
            return NUM, ANY_UNIT
        if (node.offset or node.tf) and not spec.timeframed:
            self.err(f"{node.name} takes no [offset] or @timeframe", node)
        own_tf = self._tf(node) if node.tf else tf
        offset = self._offset(node)
        if spec.cross_sectional:
            self.out.cross_sectional = True
        # arity
        needed = [a for a in spec.args if a.required]
        if spec.varargs:
            if len(node.args) < 2:
                self.err(f"{node.name} needs at least 2 arguments", node)
        elif not len(needed) <= len(node.args) <= len(spec.args):
            want = f"{len(needed)}" if len(needed) == len(spec.args) else f"{len(needed)}-{len(spec.args)}"
            self.err(f"{node.name} takes {want} argument(s), got {len(node.args)}", node)
        known_kw = {a.name: a for a in spec.kwargs}
        for key in node.kwargs:
            if key not in known_kw:
                self.err(f"{node.name} has no argument {key!r}", node.kwargs[key])
        window = 0
        units: List[Optional[str]] = []
        sigs = [spec.args[0]] * len(node.args) if spec.varargs else list(spec.args[:len(node.args)])
        for arg_node, sig in zip(node.args, sigs):
            if sig.type == "window":
                window = max(window, self.window(arg_node, node))
                units.append(None)
                continue
            t, u = self.check(arg_node, own_tf)
            units.append(u)
            if sig.type != t and not (sig.type == STR and t == STR):
                self.err(f"{node.name}: argument {sig.name!r} must be {sig.type}, got {t}", arg_node)
        for key, arg_node in node.kwargs.items():
            kw_sig = known_kw.get(key)
            if kw_sig is None:
                continue
            t, _ = self.check(arg_node, own_tf)
            if t != kw_sig.type:
                self.err(f"{node.name}: argument {key!r} must be a {kw_sig.type}", arg_node)
        if spec.varargs or spec.same_unit_args:
            positions = range(len(units)) if spec.varargs else spec.same_unit_args
            seen = {units[i] for i in positions if i < len(units) and units[i] not in (None, ANY_UNIT)}
            if len(seen) > 1:
                self.err(f"{node.name} mixes units {', '.join(sorted(str(s) for s in seen))}", node)
        for a in spec.args:
            if a.type == "window" and not a.required and len(node.args) <= spec.args.index(a):
                window = max(window, a.default if isinstance(a.default, int) else 1)
        self._need(own_tf, offset + max(window, 1) + (1 if node.name in ("CrossAbove", "CrossBelow") else 0))
        self.out.cost += spec.cost * (1 + window / 100)
        if spec.returns == NUM:
            unit = spec.unit
            if unit == "same":
                unit = next((u for u in units if u not in (None,)), ANY_UNIT)
            return NUM, unit
        return spec.returns, None


def validate(ast: Any, *, base_tf: str = "1d", params: Optional[Dict[str, Any]] = None, cost_cap: float = DEFAULT_COST_CAP) -> Validated:
    """Every check, all problems at once (not only the first)."""
    if base_tf not in n.TF_MINUTES:
        return Validated(False, [Problem(f"unknown screen timeframe {base_tf!r}")])
    checker = _Checker(base_tf, dict(params or {}))
    kind, _ = checker.check(ast)
    if kind != BOOL:
        checker.err("a screen must be a condition (true/false), e.g. close > SMA(close, 20)", ast)
    unused = set((params or {}).keys()) - checker.out.params_used
    if unused:
        checker.err(f"unknown parameter(s): {', '.join(sorted('$' + u for u in unused))}", None)
    if checker.out.cost > cost_cap:
        checker.err(f"screen cost {checker.out.cost:.1f} is over this organisation's cap {cost_cap:g}", ast)
    if checker.out.problems:
        checker.out.ok = False
    return checker.out


__all__ = ["validate", "Validated", "Problem", "MAX_OFFSET", "DEFAULT_COST_CAP"]
