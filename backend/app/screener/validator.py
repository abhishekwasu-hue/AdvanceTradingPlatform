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
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from app.screener import nodes as n
from app.screener.registry import BOOL, CAT, FIELDS, FUNCTIONS, LAST_BAR_ONLY, MAX_WINDOW, NEUTRAL_PATTERNS, NUM, READS_HISTORY, STR, Spec

MAX_OFFSET = 500
DEFAULT_COST_CAP = 200.0
ANY_UNIT = "any"                      # a literal or a parameter: takes the other side's unit


@dataclass
class Problem:
    """A refusal: the message (for the person who wrote the screen) and where. N3 S9 (round cap): also what went wrong
    as a fixed `kind`, with `names` holding only what the registry itself knows (a function or field that exists, one of
    its arguments, a timeframe) - never the screen's own words or numbers. The natural-language box shows a model's
    draft problems from `kind` + `names` alone (app/screener/nl.py); `message` may quote the screen and is for the
    screen's own author."""
    message: str
    pos: Optional[int] = None
    kind: str = ""
    names: Dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {"message": self.message, "pos": self.pos}


@dataclass
class Validated:
    ok: bool
    problems: List[Problem] = field(default_factory=list)
    timeframes: Set[str] = field(default_factory=set)
    lookback: Dict[str, int] = field(default_factory=dict)          # timeframe -> bars of history needed
    # U6 BK1: bars to FETCH per timeframe - at least `lookback`, more where a function reads better on a longer history
    # than it needs to run (a Dow trend is missing, not refused, on a short frame). Fetch sizing only; never a refusal.
    fetch_lookback: Dict[str, int] = field(default_factory=dict)
    cost: float = 0.0
    cross_sectional: bool = False
    params_used: Set[str] = field(default_factory=set)
    closed_bars_only: bool = True
    # AMW-3 review: how it was validated, so a flag gate can re-validate the same screen against another registry view
    base_tf: Optional[str] = field(default=None, repr=False, compare=False)
    cost_cap: float = field(default=DEFAULT_COST_CAP, repr=False, compare=False)
    strict_units: bool = field(default=True, repr=False, compare=False)

    def as_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "problems": [p.as_dict() for p in self.problems], "timeframes": sorted(self.timeframes, key=lambda tf: n.TF_MINUTES[tf]),
                "lookback": self.lookback, "cost": round(self.cost, 2), "cross_sectional": self.cross_sectional,
                "params_used": sorted(self.params_used), "closed_bars_only": True}


T = Tuple[str, Optional[str]]          # (type, unit)


class _Checker:
    def __init__(self, base_tf: str, params: Dict[str, Any], strict_units: bool = True, functions: Optional[Dict[str, Spec]] = None) -> None:
        self.base_tf, self.params, self.strict_units = base_tf, params, strict_units
        self.functions: Dict[str, Spec] = functions if functions is not None else FUNCTIONS   # AMW-3: a flag-off view
        self.out = Validated(True)
        self.last_bar_reported: Set[int] = set()

    def err(self, message: str, node: Any, kind: str = "", **names: str) -> None:
        """`kind` and `names` (registry-only: see Problem) are what the natural-language box shows."""
        self.out.ok = False
        self.out.problems.append(Problem(message, getattr(node, "pos", None), kind, dict(names)))

    def _tf(self, node: Any) -> str:
        tf = node.tf or self.base_tf
        if tf not in n.TF_MINUTES:
            self.err(f"unknown timeframe @{tf}", node, "unknown_timeframe")
            return self.base_tf
        if n.TF_MINUTES[tf] < n.TF_MINUTES[self.base_tf]:
            self.err(f"@{tf} is finer than the screen's timeframe @{self.base_tf}; a {tf} value has no single value per {self.base_tf} bar", node,
                     "timeframe_finer", tf=tf, base_tf=self.base_tf)
        return tf

    def _need(self, tf: str, bars: int) -> None:
        self.out.timeframes.add(tf)
        self.out.lookback[tf] = max(self.out.lookback.get(tf, 0), bars)
        self.out.fetch_lookback[tf] = max(self.out.fetch_lookback.get(tf, 0), bars)

    def _offset(self, node: Any) -> int:
        if node.offset < 0:
            self.err("a negative offset reads a future bar (look-ahead)", node, "look_ahead")
            return 0
        if node.offset > MAX_OFFSET:
            self.err(f"offset {node.offset} is more than {MAX_OFFSET} bars back", node, "offset_range")
        return node.offset

    def window(self, arg: Any, owner: Any) -> int:
        value: Any = None
        if isinstance(arg, n.Num):
            value = arg.value
        elif isinstance(arg, n.Param):
            self.out.params_used.add(arg.name)
            value = self.params.get(arg.name)
            if value is None:
                self.err(f"parameter ${arg.name} has no value", arg, "parameter")
                return 1
        else:
            self.err(f"{owner.name}: the window must be a whole number of bars (a literal or a $parameter)", arg, "window_not_literal",
                     function=owner.name)
            return 1
        if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) != int(value) or not 1 <= int(value) <= MAX_WINDOW:
            self.err(f"{owner.name}: the window must be a whole number from 1 to {MAX_WINDOW}, got {value}", arg, "window_range",
                     function=owner.name)
            return 1
        return int(value)

    def choice(self, arg: Any, sig: Any, owner: Any) -> None:
        """S5-A: an argument with a fixed set of values must be a literal (or a parameter) from that set."""
        if isinstance(arg, (n.Num, n.Str)):
            value: Any = arg.value
        elif isinstance(arg, n.Param):
            value = self.params.get(arg.name)
        else:
            self.err(f"{owner.name}: argument {sig.name!r} must be one of {', '.join(map(str, sig.choices))}", arg, "choice",
                     function=owner.name, arg=sig.name)
            return
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        if isinstance(value, bool) or value not in sig.choices:
            self.err(f"{owner.name}: {sig.name} {value!r} is not one of {', '.join(map(str, sig.choices))}", arg, "choice",
                     function=owner.name, arg=sig.name)

    def bounded(self, arg: Any, sig: Any, owner: Any) -> None:
        """U6 BK1: a number argument with a range must be a literal (or a parameter) inside it."""
        value: Any = arg.value if isinstance(arg, n.Num) else self.params.get(arg.name) if isinstance(arg, n.Param) else None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return                                                    # not a literal: the type checks report it
        lo, hi = sig.bounds
        if not lo <= float(value) <= hi:
            self.err(f"{owner.name}: {sig.name} must be between {lo:g} and {hi:g}, got {value:g}", arg, "out_of_range",
                     function=owner.name, arg=sig.name)

    def _value(self, arg: Any) -> Any:
        """A literal's value, or a parameter's (None for anything else)."""
        if isinstance(arg, (n.Num, n.Str, n.Bool)):
            return arg.value
        return self.params.get(arg.name) if isinstance(arg, n.Param) else None

    def form(self, arg: Any, sig: Any, owner: Any) -> None:
        """AMW-3: a text argument with a required form (TimeOfDay's HH:MM) must be a literal or a parameter that has it."""
        value = self._value(arg)
        if not isinstance(value, str) or re.fullmatch(sig.pattern, value) is None:
            shown = repr(value) if value is not None else "an expression"
            self.err(f"{owner.name}: {sig.name} must be a time as HH:MM (00:00 to 23:59), got {shown}", arg)

    def arg_value(self, node: n.Call, spec: Spec, name: str) -> Any:
        """The value an argument takes in this call: the literal / parameter given, or the default when it is left out."""
        pos = next((i for i, a in enumerate(spec.args) if a.name == name), None)
        if pos is None:
            return None
        return self._value(node.args[pos]) if pos < len(node.args) else spec.args[pos].default

    def choice_bars(self, node: n.Call, spec: Spec) -> int:
        """AMW-3: the bars an argument's value adds (Spec.choice_extra_bars)."""
        return sum(bars for name, value, bars in spec.choice_extra_bars if self.arg_value(node, spec, name) == value)

    def literal(self, arg: Any, sig: Any, owner: Any) -> None:
        """Z10-a (U3 G2): a setting of the function (Supertrend / Bollinger `k`, the zone filters' `tolerance_pct`) is
        one number for the whole series. The runtime reads it once, so an expression there (`ATR(5) / 2`) would take
        its value on the LAST bar and apply it to every earlier bar - a look-ahead inside Count, CrossAbove, Lag ...
        Only a literal (optionally negated) or a numeric $parameter is accepted."""
        node = arg.operand if isinstance(arg, n.Unary) else arg
        if isinstance(node, n.Num):
            return
        if isinstance(node, n.Param):
            value = self.params.get(node.name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
                self.err(f"{owner.name}: {sig.name} must be a number, got ${node.name} = {value!r}", arg, "literal_only",
                         function=owner.name, arg=sig.name)
            return
        self.err(f"{owner.name}: {sig.name} must be a number or a $parameter (one value for the whole series), not an expression", arg,
                 "literal_only", function=owner.name, arg=sig.name)

    def history_of_last_bar(self, owner: n.Call) -> None:
        """Z10-a (U3 G2): a function answered for the last bar only cannot be read back in time - `Count(5,
        PatternBullish())` would repeat today's answer on the four bars before and count 5 (a look-ahead into those
        bars). Reported once per inner call, by the outermost history-reading function around it."""
        for arg in (*owner.args, *owner.kwargs.values()):
            for inner in n.walk(arg):
                if isinstance(inner, n.Call) and inner.name in LAST_BAR_ONLY and id(inner) not in self.last_bar_reported:
                    self.last_bar_reported.add(id(inner))
                    self.err(f"{inner.name} is known for the last bar only, so {owner.name} cannot read its earlier values; "
                             f"use it outside {owner.name}" + (' (Pattern("name") is the bar-by-bar form)' if inner.name.startswith("Pattern") else ""),
                             inner, "last_bar_only", function=inner.name, outer=owner.name)

    def degree_bars(self, node: Any, spec: Any, table: Optional[Tuple[int, ...]] = None) -> int:
        """The bars a swing degree needs: the literal (or parameter) degree, the default when it is left out, and the
        largest when it cannot be read (the choice check reports that separately)."""
        table = table if table is not None else spec.degree_min_bars
        pos = next((i for i, a in enumerate(spec.args) if a.name == "degree"), None)
        if pos is None:
            return max(table)
        arg = node.args[pos] if pos < len(node.args) else None
        if arg is None:
            value: Any = spec.args[pos].default
        elif isinstance(arg, n.Num):
            value = arg.value
        elif isinstance(arg, n.Param):
            value = self.params.get(arg.name)
        else:
            value = None
        try:
            return table[int(value)]
        except (TypeError, ValueError, IndexError):
            return max(table)

    def sessions_needed(self, node: Any, spec: Any) -> int:
        """U6 BK2: whole sessions a function needs - its fixed `min_sessions`, or the `sessions_arg` argument (literal,
        parameter or default) plus the current session."""
        if spec.sessions_arg is None:
            return int(spec.min_sessions)
        pos = next(i for i, a in enumerate(spec.args) if a.name == spec.sessions_arg)
        arg = node.args[pos] if pos < len(node.args) else None
        value: Any = spec.args[pos].default if arg is None else arg.value if isinstance(arg, n.Num) else \
            self.params.get(arg.name) if isinstance(arg, n.Param) else None
        try:
            return max(int(spec.min_sessions), int(value) + 1)
        except (TypeError, ValueError):
            return int(spec.min_sessions)

    def argument(self, arg_node: Any, sig: Any, owner: Any, own_tf: str) -> Tuple[Optional[int], Optional[str]]:
        """One argument of a call against its signature: (its window when it is one, its unit)."""
        if sig.type == "window":
            w = self.window(arg_node, owner)
            if sig.bounds:
                self.bounded(arg_node, sig, owner)
            return w, None
        t, u = self.check(arg_node, own_tf)
        if sig.type != t and not (sig.type == STR and t == STR):
            self.err(f"{owner.name}: argument {sig.name!r} must be {sig.type}, got {t}", arg_node, "argument_type",
                     function=owner.name, arg=sig.name)
        elif sig.choices:
            self.choice(arg_node, sig, owner)
        elif sig.pattern:
            self.form(arg_node, sig, owner)
        elif sig.literal_only or sig.bounds:
            if sig.literal_only:
                self.literal(arg_node, sig, owner)
            if sig.bounds:
                self.bounded(arg_node, sig, owner)
        return None, u

    def check(self, node: Any, tf: Optional[str] = None) -> T:  # noqa: C901 - one switch over the node kinds
        tf = tf or self.base_tf
        if isinstance(node, n.Num):
            if not math.isfinite(node.value):
                self.err("numbers must be finite", node, "not_finite")
            return NUM, ANY_UNIT
        if isinstance(node, n.Str):
            return STR, None
        if isinstance(node, n.Bool):
            return BOOL, None
        if isinstance(node, n.Param):
            self.out.params_used.add(node.name)
            if node.name not in self.params:
                self.err(f"parameter ${node.name} has no value", node, "parameter")
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
                hint = " (a function needs parentheses)" if node.name in self.functions else ""
                if hint:
                    self.err(f"unknown field {node.name!r}{hint}", node, "function_without_parens", function=node.name)
                else:
                    self.err(f"unknown field {node.name!r}", node, "unknown_field")
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
                self.err("minus needs a number", node, "number_expected")
            return NUM, u
        if isinstance(node, n.Binary):
            (lt, lu), (rt, ru) = self.check(node.left, tf), self.check(node.right, tf)
            if lt != NUM or rt != NUM:
                self.err(f"'{node.op}' needs numbers on both sides", node, "number_expected")
                return NUM, ANY_UNIT
            if node.op in "+-":
                if lu != ru and ANY_UNIT not in (lu, ru) and self.strict_units:
                    self.err(f"'{node.op}' mixes units {lu} and {ru}", node, "unit_mismatch")
                return NUM, ru if lu == ANY_UNIT else lu
            if lu == ANY_UNIT or ru == ANY_UNIT:
                return NUM, ru if lu == ANY_UNIT else lu
            return NUM, "ratio"
        if isinstance(node, n.Compare):
            (lt, lu), (rt, ru) = self.check(node.left, tf), self.check(node.right, tf)
            self.comparable(node, (lt, lu), (rt, ru), node.op)
            self.domain(node.left, [node.right])
            self.domain(node.right, [node.left])
            return BOOL, None
        if isinstance(node, n.Between):
            v, lo, hi = self.check(node.value, tf), self.check(node.low, tf), self.check(node.high, tf)
            for other in (lo, hi):
                self.comparable(node, v, other, "BETWEEN")
            if v[0] != NUM:
                self.err("BETWEEN needs numbers", node, "number_expected")
            return BOOL, None
        if isinstance(node, n.In):
            v = self.check(node.value, tf)
            if not node.items:
                self.err("IN needs at least one value", node, "in_list")
            for item in node.items:
                if not isinstance(item, (n.Num, n.Str, n.Bool, n.Param)):
                    self.err("IN takes literals only", item, "in_list")
                    continue
                self.comparable(node, v, self.check(item, tf), "IN")
            self.domain(node.value, list(node.items))
            return BOOL, None
        if isinstance(node, n.Logic):
            if node.op not in ("ALL", "ANY"):
                self.err(f"unknown group {node.op!r}", node)
            if len(node.items) < 1:
                self.err(f"{node.op} needs at least one condition", node, "condition_expected")
            for item in node.items:
                if self.check(item, tf)[0] != BOOL:
                    self.err(f"{'AND' if node.op == 'ALL' else 'OR'} joins conditions (true/false), not values", item, "condition_expected")
            return BOOL, None
        if isinstance(node, n.Not):
            if self.check(node.item, tf)[0] != BOOL:
                self.err("NOT needs a condition", node, "condition_expected")
            return BOOL, None
        self.err(f"not a ScreenQL node: {type(node).__name__}", node)
        return BOOL, None

    def domain(self, cat: Any, others: List[Any]) -> None:
        """S5-A review: a classifier with a fixed set of values refuses a literal outside it (`SwingDirection() == "up"`
        would otherwise validate and never match)."""
        spec = self.functions.get(cat.name) if isinstance(cat, n.Call) else None
        if spec is None or not spec.values:
            return
        for other in others:
            value = other.value if isinstance(other, n.Str) else self.params.get(other.name) if isinstance(other, n.Param) else None
            if isinstance(value, str) and value not in spec.values:
                self.err(f"{spec.name} is one of {', '.join(v for v in spec.values if v)}; {value!r} never matches", other, "domain",
                         function=spec.name)

    def comparable(self, node: Any, left: T, right: T, op: str) -> None:
        (lt, lu), (rt, ru) = left, right
        types = {lt, rt}
        if types <= {NUM}:
            if lu != ru and ANY_UNIT not in (lu, ru) and self.strict_units:
                self.err(f"compares {lu} with {ru}", node, "unit_mismatch")
            return
        if types <= {CAT, STR} and op in ("==", "!=", "IN"):
            return
        if types <= {BOOL} and op in ("==", "!=", "IN"):
            return
        self.err(f"cannot compare {lt} with {rt} using {op}", node, "compare_types")

    def call(self, node: n.Call, tf: str) -> T:
        spec: Optional[Spec] = self.functions.get(node.name)
        if spec is None:
            hint = " (a field takes no parentheses)" if node.name in FIELDS else ""
            if hint:
                self.err(f"unknown function {node.name!r}{hint}", node, "field_called", field=node.name)
            else:
                self.err(f"unknown function {node.name!r}", node, "unknown_function")
            return NUM, ANY_UNIT
        if (node.offset or node.tf) and not spec.timeframed:
            self.err(f"{node.name} takes no [offset] or @timeframe", node, "no_offset_tf", function=spec.name)
        own_tf = self._tf(node) if node.tf else tf
        offset = self._offset(node)
        if spec.cross_sectional:
            self.out.cross_sectional = True
        # arity
        needed = [a for a in spec.args if a.required]
        if spec.varargs:
            if len(node.args) < 2:
                self.err(f"{node.name} needs at least 2 arguments", node, "arity", function=spec.name)
        elif not len(needed) <= len(node.args) <= len(spec.args):
            want = f"{len(needed)}" if len(needed) == len(spec.args) else f"{len(needed)}-{len(spec.args)}"
            self.err(f"{node.name} takes {want} argument(s), got {len(node.args)}", node, "arity", function=spec.name)
        if spec.name in READS_HISTORY:
            self.history_of_last_bar(node)
        known_kw = {a.name: a for a in spec.kwargs}
        for key in node.kwargs:
            if key not in known_kw:
                self.err(f"{node.name} has no argument {key!r}", node.kwargs[key], "unknown_argument", function=spec.name)
        window = 0
        windows: List[int] = []
        units: List[Optional[str]] = []
        if spec.timeframes and own_tf not in spec.timeframes:
            self.err(f"{node.name} runs on {' / '.join('@' + t for t in spec.timeframes)} bars only, not @{own_tf}", node,
                     "timeframe_unsupported", function=spec.name)
        sigs = [spec.args[0]] * len(node.args) if spec.varargs else list(spec.args[:len(node.args)])
        for arg_node, sig in zip(node.args, sigs):
            w, u = self.argument(arg_node, sig, node, own_tf)
            units.append(u)
            if w is not None:
                windows.append(w)
                window = max(window, w)
        # strategy templates R7: a named argument gets the same checks as a positional one (type, choices, bounds,
        # literal-only, window)
        positional = {a.name for a in spec.args[:len(node.args)]} if not spec.varargs else set()
        for key, arg_node in node.kwargs.items():
            kw_sig = known_kw.get(key)
            if kw_sig is None:
                continue
            if key in positional:
                self.err(f"{node.name}: argument {key!r} given twice", arg_node, "argument_twice", function=spec.name, arg=key)
                continue
            w, _ = self.argument(arg_node, kw_sig, node, own_tf)
            if w is not None:
                windows.append(w)
                window = max(window, w)
        for i in spec.price_args:
            if i < len(units) and units[i] not in ("price", ANY_UNIT) and self.strict_units:
                self.err(f"{node.name}: {spec.args[i].name} must be a price, got {units[i] or 'no unit'}", node.args[i],
                         "price_argument", function=spec.name, arg=spec.args[i].name)
        if spec.varargs or spec.same_unit_args:
            positions = range(len(units)) if spec.varargs else spec.same_unit_args
            seen = {units[i] for i in positions if i < len(units) and units[i] not in (None, ANY_UNIT)}
            if len(seen) > 1 and self.strict_units:
                self.err(f"{node.name} mixes units {', '.join(sorted(str(s) for s in seen))}", node, "unit_mismatch")
        for a in spec.args:
            if a.type == "window" and not a.required and len(node.args) <= spec.args.index(a):
                windows.append(a.default if isinstance(a.default, int) else 1)
                window = max(window, a.default if isinstance(a.default, int) else 1)
        if spec.window_sum:                                           # U6 BK1: e.g. ATR warm-up + percentile lookback
            window = sum(windows)
        window *= dict(spec.window_bars).get(own_tf, 1)               # U6 BK1: weeks -> daily bars
        min_bars = spec.min_bars + (self.choice_bars(node, spec) if spec.choice_extra_bars else 0)
        if spec.name == "Pattern" and self.arg_value(node, spec, "name") in NEUTRAL_PATTERNS \
                and self.arg_value(node, spec, "confirm") not in (None, "any"):
            self.err(f"Pattern: {self.arg_value(node, spec, 'name')} has no direction, so it has no confirmation status (use confirm \"any\")",
                     node)
        if spec.degree_min_bars:                                      # S5-A4: more history for a larger swing degree
            min_bars = max(min_bars, self.degree_bars(node, spec))
        for name in spec.tf_multiple_args:                            # U6 BK2: e.g. a 30-minute range on 15m bars, not on 45m ones
            pos = next(i for i, a in enumerate(spec.args) if a.name == name)
            arg = node.args[pos] if pos < len(node.args) else None
            value = (arg.value if isinstance(arg, n.Num) else self.params.get(arg.name) if isinstance(arg, n.Param)
                     else spec.args[pos].default if arg is None else None)
            step = n.TF_MINUTES.get(own_tf, 0)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < step < n.TF_MINUTES["1d"] and float(value) % step:
                self.err(f"{node.name}: {name} {value:g} is not a whole number of @{own_tf} bars ({step} minutes each)", arg or node,
                         "tf_multiple", function=spec.name, arg=name, tf=own_tf)
        for low_name, high_name, equal_ok in spec.ordered_args:      # TPL-1: e.g. MACD fast below slow
            lo_v, hi_v = self.arg_value(node, spec, low_name), self.arg_value(node, spec, high_name)
            numbers = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (lo_v, hi_v))
            if numbers and (lo_v > hi_v or (lo_v == hi_v and not equal_ok)):
                rel = "at most" if equal_ok else "below"
                self.err(f"{node.name}: {low_name} must be {rel} {high_name}, got {lo_v:g} and {hi_v:g}", node, "argument_order",
                         function=spec.name, arg=low_name, other=high_name)
        sessions = self.sessions_needed(node, spec)
        if sessions and n.TF_MINUTES[own_tf] < n.TF_MINUTES["1d"]:    # whole sessions in this timeframe's bars
            min_bars = max(min_bars, sessions * math.ceil(n.TF_MINUTES["1d"] / n.TF_MINUTES[own_tf]))
        self._need(own_tf, offset + max(window, 1, min_bars) + spec.extra_bars)
        if spec.fetch_degree_bars:                                    # U6 BK1: fetch more than the refusal needs
            fetch_need = offset + self.degree_bars(node, spec, spec.fetch_degree_bars)
            self.out.fetch_lookback[own_tf] = max(self.out.fetch_lookback.get(own_tf, 0), fetch_need)
        self.out.cost += spec.cost * (1 + window / 100)
        if spec.returns == NUM:
            unit = spec.unit
            if unit == "same":
                unit = next((u for u in units if u not in (None,)), ANY_UNIT)
            return NUM, unit
        return spec.returns, None


NOT_A_CONDITION = "a screen must be a condition (true/false), e.g. close > SMA(close, 20)"


def validate(ast: Any, *, base_tf: str = "1d", params: Optional[Dict[str, Any]] = None, cost_cap: float = DEFAULT_COST_CAP,
             strict_units: bool = True, functions: Optional[Dict[str, Spec]] = None) -> Validated:
    """Every check, all problems at once (not only the first). `strict_units=False` is only for screens translated from
    the legacy scanner, whose filters never checked units (S1c parity); user-written screens are always strict."""
    if base_tf not in n.TF_MINUTES:
        return Validated(False, [Problem(f"unknown screen timeframe {base_tf!r}", kind="unknown_timeframe")])
    checker = _Checker(base_tf, dict(params or {}), strict_units, functions)
    checker.out.base_tf, checker.out.cost_cap, checker.out.strict_units = base_tf, cost_cap, strict_units
    kind, _ = checker.check(ast)
    if kind != BOOL:
        checker.err(NOT_A_CONDITION, ast, "not_condition")
    unused = set((params or {}).keys()) - checker.out.params_used
    if unused:
        checker.err(f"unknown parameter(s): {', '.join(sorted('$' + u for u in unused))}", None, "parameter")
    if checker.out.cost > cost_cap:
        # U5 review: about the whole screen, not a place in it (pos None), so the builder does not pin it on stage 1
        checker.err(f"screen cost {checker.out.cost:.1f} is over this organisation's cap {cost_cap:g}", None, "cost")
    if checker.out.problems:
        checker.out.ok = False
    return checker.out


def kind_of(ast: Any, *, base_tf: str = "1d") -> str:
    """N3 S7a: the type an expression yields (NUM / BOOL / CAT / STR) - a ranking factor must be a number."""
    kind, _ = _Checker(base_tf, {}).check(ast)
    return str(kind)


@dataclass
class StudyCheck:
    """N4 CH6: a ScreenQL expression as a chart study - its validation, its kind (num: a line; bool: true / false marks)
    and its unit ("price" draws on the price pane, anything else in its own pane)."""
    validated: Validated
    kind: Optional[str] = None
    unit: Optional[str] = None


def validate_study(ast: Any, *, base_tf: str = "1d", params: Optional[Dict[str, Any]] = None, cost_cap: float = DEFAULT_COST_CAP,
                   functions: Optional[Dict[str, Spec]] = None) -> StudyCheck:
    """The screen checks (types, units, timeframes, look-ahead, cost) for an expression drawn as a series: a number or a
    condition. Refused as studies: cross-sectional functions (they rank a universe, not one chart) and functions that
    only have a value on the last bar (fundamentals, sector / index membership: no series to draw)."""
    if base_tf not in n.TF_MINUTES:
        return StudyCheck(Validated(False, [Problem(f"unknown study timeframe {base_tf!r}")]))
    checker = _Checker(base_tf, dict(params or {}), True, functions)
    kind, unit = checker.check(ast)
    if kind not in (NUM, BOOL):
        checker.err("a study must be a number (a line) or a condition (true / false marks), e.g. SMA(close, 20)", ast)
    for node in n.walk(ast):
        name = getattr(node, "name", "") if isinstance(node, n.Call) else ""
        spec = checker.functions.get(name) if name else None
        if spec is not None and spec.cross_sectional:
            checker.err(f"{name} ranks a universe of symbols - it has no series on one chart", node)
        elif spec is not None and name in LAST_BAR_ONLY:
            checker.err(f"{name} has a value on the last bar only - there is no series to draw", node)
    unused = set((params or {}).keys()) - checker.out.params_used
    if unused:
        checker.err(f"unknown parameter(s): {', '.join(sorted('$' + u for u in unused))}", None)
    if checker.out.cost > cost_cap:
        checker.err(f"study cost {checker.out.cost:.1f} is over the cap {cost_cap:g}", None)
    if checker.out.problems:
        checker.out.ok = False
    return StudyCheck(checker.out, kind, None if unit == ANY_UNIT else unit)


__all__ = ["validate", "validate_study", "kind_of", "StudyCheck", "Validated", "Problem", "MAX_OFFSET", "DEFAULT_COST_CAP"]
