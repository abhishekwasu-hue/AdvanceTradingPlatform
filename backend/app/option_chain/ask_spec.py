"""OC3 (owner's order, PART C2): the declarative option chain analysis spec behind "Ask AI" and "My analyses".

The AI never runs code and never writes a number into a result. It writes a SPEC (this module's `AnalysisSpec`), the
server validates it here and evaluates it on the real chain rows; every number in a result comes from the data.

**The spec** (JSON, strict: unknown keys and wrong types are refused):

* `version` 1; `title` (a short name);
* `window`: N strikes either side of ATM (0..20). ATM = the listed strike nearest the spot (a tie takes the lower);
  the window is by position in the sorted strike list, so an uneven strike step is handled;
* `columns`: 1..6 `{label, expr}` (numeric expressions), shown after the strike;
* `filters`: 0..4 conditions (true/false expressions); a row is kept when every one is true. A condition that reads a
  missing value is not true, so the row is left out;
* `sort`: optional `{by: expr, order: asc|desc}` (missing values last; ties by strike); default by strike, ascending;
* `limit`: optional 1..41 rows; `chart`: optional `{type: bar|line, x: "strike" | a column label, y: [1..2 labels]}`.

**Expressions** are a tiny language parsed here into an AST (never `eval`, no attributes, no names outside the lists):

* numbers; column refs (FIELDS); history refs `name@open` (the session's first stored snapshot) and `name@-15m` (the
  latest stored snapshot at or before 15 minutes ago, 1..375 minutes) for the HISTORY_FIELDS;
* `+ - * /` (division by zero gives a missing value), unary minus, `< <= > >= == !=`, `and`, `or`, parentheses;
* row functions `abs(x)`, `pct_change(now, then)` = (now - then) / then x 100; window functions over the window's
  rows (before filters) `sum(x)`, `avg(x)`, `min(x)`, `max(x)`, `rank(x)` (1 = the largest; equal values share a rank).
  A window function cannot contain another one.
* Caps: 160 characters, depth 12 and 40 nodes per expression; 200 nodes and 600 expression characters per spec.

`evaluate()` is pure and deterministic (no clock, database or network: the route passes the rows, the spot, the
history and the time in). `describe()` turns a spec into the plain sentences shown under "How this was calculated".
Nothing here suggests a trade, places or changes an order (ADR-0006).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Annotated, Any, Dict, List, Literal, Mapping, Optional, Sequence, Tuple, Union

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

SPEC_VERSION = 1
MAX_WINDOW = 20
MAX_COLUMNS = 6
MAX_FILTERS = 4
MAX_LIMIT = 2 * MAX_WINDOW + 1
MAX_EXPR_CHARS = 160
MAX_DEPTH = 12
MAX_NODES_EXPR = 40
MAX_NODES_SPEC = 200
MAX_SPEC_EXPR_CHARS = 600           # all expressions of one spec together (keeps 8 saved analyses inside the prefs cap)
MAX_HISTORY_MINUTES = 375           # a whole NSE session
HISTORY_TOLERANCE_MINUTES = 10      # a "@-Nm" snapshot older than its target by more than this is not used
ROUND_DIGITS = 6

LABEL_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9 %()+\-/.,:<>=]{0,31}$"
TITLE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9 %()+\-/.,:<>=]{0,59}$"

SIDES = {"ce": "call", "pe": "put"}
LEG_FIELDS: Dict[str, str] = {
    "oi": "OI", "oi_chg": "OI change for the day (as the broker reports it)", "ltp": "last price", "iv": "IV (%)",
    "volume": "volume", "bid": "bid", "ask": "ask", "delta": "delta", "gamma": "gamma", "theta": "theta", "vega": "vega",
}
FIELDS: Dict[str, str] = {
    "strike": "the strike",
    "atm_offset": "the strike's position from ATM (0 = ATM, -1 = one listed strike below)",
    "spot": "the underlying price",
    **{f"{side}_{key}": f"{word} {text}" for side, word in SIDES.items() for key, text in LEG_FIELDS.items()},
}
HISTORY_FIELDS = tuple(f"{side}_{key}" for side in SIDES for key in ("oi", "ltp", "iv"))
ROW_FUNCTIONS = {"abs": 1, "pct_change": 2}
WINDOW_FUNCTIONS = ("sum", "avg", "min", "max", "rank")
FUNCTIONS = {**ROW_FUNCTIONS, **{name: 1 for name in WINDOW_FUNCTIONS}}
KEYWORDS = ("and", "or")
COMPARISONS = ("<", "<=", ">", ">=", "==", "!=")

# Words a label or a title may not use: a buy / sell instruction, advice or a promise (ATP shows no advice anywhere).
_ADVICE = re.compile(r"\b(?:buy\w*|bought|sell\w*|sold|recommend\w*|advi[cs]e\w*|guarantee\w*|assured|sure|sure[- ]?shot|risk[- ]?free|"
                     r"profit\w*|target\w*|stop[- ]?loss\w*|entry|entries|enter\w*|exit\w*|best|should|must|safe\w*|hold\w*|"
                     r"accumulat\w*|avoid\w*|add more|book\w*|jackpot\w*|winner\w*|winning|tips?|will|bullish|bearish|trapped|"
                     r"write|take (?:a )?positions?|(?:go|going) (?:long|short)|"
                     r"(?:long|short) (?:ce|pe|calls?|puts?|straddles?|strangles?|futures?|\d[\d,.]*))\b", re.I)
_LINK = re.compile(r"://|\bwww\.", re.I)


class SpecError(ValueError):
    """A spec or an expression outside the whitelist. The message never repeats the rejected text (positions only)."""


# ---------------------------------------------------------------------------------------------------- the AST

@dataclass(frozen=True)
class Num:
    value: float


@dataclass(frozen=True)
class Ref:
    name: str
    at: Optional[str] = None          # None (now), "open", or "-<minutes>m"


@dataclass(frozen=True)
class Neg:
    arg: "Node"


@dataclass(frozen=True)
class Bin:
    op: str                           # + - * /
    left: "Node"
    right: "Node"


@dataclass(frozen=True)
class Cmp:
    op: str
    left: "Node"
    right: "Node"


@dataclass(frozen=True)
class Logic:
    op: str                           # and / or
    items: Tuple["Node", ...]


@dataclass(frozen=True)
class Call:
    fn: str
    args: Tuple["Node", ...]


Node = Union[Num, Ref, Neg, Bin, Cmp, Logic, Call]

_TOKEN = re.compile(r"(?P<ws>\s+)|(?P<num>\d{1,12}(?:\.\d{1,6})?)(?![\w.@])|(?P<name>[a-z_][a-z0-9_]{0,31})(?![\w])|"
                    r"(?P<at>@(?:open|-\d{1,3}m))(?![\w@])|(?P<op><=|>=|==|!=|[-+*/<>(),])")
_ALLOWED_CHARS = re.compile(r"[A-Za-z0-9_ .@()+\-*/<>=!,]*")          # used with fullmatch: no "$" before a final newline


def _tokens(text: str) -> List[Tuple[str, str, int]]:
    if len(text) > MAX_EXPR_CHARS:
        raise SpecError(f"an expression is longer than {MAX_EXPR_CHARS} characters")
    if not text.strip():
        raise SpecError("an expression is empty")
    if not _ALLOWED_CHARS.fullmatch(text):
        bad = next((i for i, ch in enumerate(text) if not _ALLOWED_CHARS.fullmatch(ch)), 0)
        raise SpecError(f"a character that is not allowed at position {bad + 1}")
    lowered = text.lower()
    out: List[Tuple[str, str, int]] = []
    i = 0
    while i < len(lowered):
        m = _TOKEN.match(lowered, i)
        if m is None:
            raise SpecError(f"cannot read the expression at position {i + 1}")
        kind = m.lastgroup or ""
        if kind != "ws":
            out.append((kind, m.group(0), i + 1))
        i = m.end()
    return out


class _Parser:
    """Recursive descent: or -> and -> comparison -> sum -> product -> unary -> primary. The nesting depth (parentheses,
    function arguments and unary minus) is capped while parsing, so the recursion stays small."""

    def __init__(self, text: str) -> None:
        self.tokens = _tokens(text)
        self.i = 0
        self.depth = 0

    def _peek(self) -> Optional[Tuple[str, str, int]]:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def _at(self) -> int:
        tok = self._peek()
        return tok[2] if tok else (self.tokens[-1][2] + len(self.tokens[-1][1]) if self.tokens else 1)

    def _take(self, value: str) -> bool:
        tok = self._peek()
        if tok is not None and tok[1] == value and tok[0] in ("op", "name"):
            self.i += 1
            return True
        return False

    def _enter(self) -> None:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise SpecError(f"an expression is nested deeper than {MAX_DEPTH} levels")

    def parse(self) -> Node:
        node = self._or()
        if self._peek() is not None:
            raise SpecError(f"unexpected text at position {self._at()}")
        return node

    def _or(self) -> Node:
        items = [self._and()]
        while self._take("or"):
            items.append(self._and())
        return items[0] if len(items) == 1 else Logic("or", tuple(items))

    def _and(self) -> Node:
        items = [self._cmp()]
        while self._take("and"):
            items.append(self._cmp())
        return items[0] if len(items) == 1 else Logic("and", tuple(items))

    def _cmp(self) -> Node:
        left = self._sum()
        tok = self._peek()
        if tok is not None and tok[0] == "op" and tok[1] in COMPARISONS:
            self.i += 1
            right = self._sum()
            nxt = self._peek()
            if nxt is not None and nxt[0] == "op" and nxt[1] in COMPARISONS:
                raise SpecError(f"chained comparisons are not allowed (position {nxt[2]}); join them with 'and'")
            return Cmp(tok[1], left, right)
        return left

    def _sum(self) -> Node:
        node = self._product()
        while True:
            tok = self._peek()
            if tok is not None and tok[0] == "op" and tok[1] in ("+", "-"):
                self.i += 1
                node = Bin(tok[1], node, self._product())
            else:
                break
        return node

    def _product(self) -> Node:
        node = self._unary()
        while True:
            tok = self._peek()
            if tok is not None and tok[0] == "op" and tok[1] in ("*", "/"):
                self.i += 1
                node = Bin(tok[1], node, self._unary())
            else:
                break
        return node

    def _unary(self) -> Node:
        if self._take("-"):
            self._enter()
            node: Node = Neg(self._unary())
            self.depth -= 1
            return node
        return self._primary()

    def _primary(self) -> Node:
        tok = self._peek()
        if tok is None:
            raise SpecError("the expression ends too early")
        kind, value, pos = tok
        if kind == "num":
            self.i += 1
            number = float(value)
            if not math.isfinite(number):
                raise SpecError(f"a number that is not allowed at position {pos}")
            return Num(number)
        if kind == "op" and value == "(":
            self.i += 1
            self._enter()
            node = self._or()
            self.depth -= 1
            if not self._take(")"):
                raise SpecError(f"a missing ')' at position {self._at()}")
            return node
        if kind == "name":
            self.i += 1
            if value in FUNCTIONS:
                if not self._take("("):
                    raise SpecError(f"a function without '(' at position {pos}")
                self._enter()
                args = [self._or()]
                while self._take(","):
                    args.append(self._or())
                self.depth -= 1
                if not self._take(")"):
                    raise SpecError(f"a missing ')' at position {self._at()}")
                if len(args) != FUNCTIONS[value]:
                    raise SpecError(f"{value} takes {FUNCTIONS[value]} argument(s) (position {pos})")
                return Call(value, tuple(args))
            if value in FIELDS:
                nxt = self._peek()
                if nxt is not None and nxt[0] == "at":
                    self.i += 1
                    at = nxt[1][1:]
                    if value not in HISTORY_FIELDS:
                        raise SpecError(f"{value} has no stored history; history refs work for {', '.join(HISTORY_FIELDS)} (position {nxt[2]})")
                    if at != "open":
                        minutes = int(at[1:-1])
                        if not 1 <= minutes <= MAX_HISTORY_MINUTES:
                            raise SpecError(f"a history ref goes back 1 to {MAX_HISTORY_MINUTES} minutes (position {nxt[2]})")
                    return Ref(value, at)
                return Ref(value)
            raise SpecError(f"an unknown name at position {pos}; use only the listed columns and functions")
        raise SpecError(f"unexpected text at position {pos}")


def _walk(node: Node):
    yield node
    if isinstance(node, Neg):
        yield from _walk(node.arg)
    elif isinstance(node, (Bin, Cmp)):
        yield from _walk(node.left)
        yield from _walk(node.right)
    elif isinstance(node, (Logic, Call)):
        for child in (node.items if isinstance(node, Logic) else node.args):
            yield from _walk(child)


def node_count(node: Node) -> int:
    return sum(1 for _ in _walk(node))


def _type(node: Node, in_window_fn: bool = False) -> str:
    """'num' or 'bool'; SpecError when an operator gets the wrong kind of value."""
    if isinstance(node, (Num, Ref)):
        return "num"
    if isinstance(node, Neg):
        if _type(node.arg, in_window_fn) != "num":
            raise SpecError("minus applies to a number, not a condition")
        return "num"
    if isinstance(node, Bin):
        if _type(node.left, in_window_fn) != "num" or _type(node.right, in_window_fn) != "num":
            raise SpecError(f"'{node.op}' joins numbers, not conditions")
        return "num"
    if isinstance(node, Cmp):
        if _type(node.left, in_window_fn) != "num" or _type(node.right, in_window_fn) != "num":
            raise SpecError(f"'{node.op}' compares numbers, not conditions")
        return "bool"
    if isinstance(node, Logic):
        if any(_type(i, in_window_fn) != "bool" for i in node.items):
            raise SpecError(f"'{node.op}' joins conditions, not numbers")
        return "bool"
    window = node.fn in WINDOW_FUNCTIONS
    if window and in_window_fn:
        raise SpecError(f"{node.fn} cannot be used inside another window function")
    if any(_type(a, in_window_fn or window) != "num" for a in node.args):
        raise SpecError(f"{node.fn} takes numbers, not conditions")
    return "num"


@lru_cache(maxsize=1024)
def parse_expression(text: str) -> Node:
    """The AST of one expression (cached; the AST is immutable). SpecError when it is outside the whitelist."""
    if not isinstance(text, str):
        raise SpecError("an expression must be text")
    node = _Parser(text).parse()
    if node_count(node) > MAX_NODES_EXPR:
        raise SpecError(f"an expression has more than {MAX_NODES_EXPR} parts")
    _type(node)
    return node


def compile_expression(text: str, want: str) -> Node:
    node = parse_expression(text)
    got = _type(node)
    if got != want:
        raise SpecError("this place needs a number (a column or a sort key), not a condition" if want == "num"
                        else "a filter needs a condition (a comparison such as 'ce_oi > 0'), not a number")
    return node


# ---------------------------------------------------------------------------------------------------- the spec model

def _num_expr(text: str) -> str:
    compile_expression(text, "num")
    return text


def _bool_expr(text: str) -> str:
    compile_expression(text, "bool")
    return text


def _clean_words(text: str) -> str:
    """A label or title only names what is shown: no buy / sell, advice, forecast or promise words (also the platform's
    banned-word list and the H-C1 c output filter: a blocked text or a specific call is refused) and no links."""
    from app.ai import output_filter, wording
    if _LINK.search(text) or _ADVICE.search(text) or wording.banned_terms(text):
        raise SpecError("a label or title may only name what is shown - no buy / sell, advice or promise words")
    screened = output_filter.screen(text, "en", where="option_chain_spec")
    if not screened.ok or screened.framed or output_filter.is_specific_call(text):
        raise SpecError("a label or title may only name what is shown - no buy / sell, advice or promise words")
    return text


NumExpr = Annotated[str, StringConstraints(min_length=1, max_length=MAX_EXPR_CHARS), AfterValidator(_num_expr)]
BoolExpr = Annotated[str, StringConstraints(min_length=1, max_length=MAX_EXPR_CHARS), AfterValidator(_bool_expr)]
Label = Annotated[str, StringConstraints(pattern=LABEL_PATTERN, max_length=32), AfterValidator(_clean_words)]
Title = Annotated[str, StringConstraints(pattern=TITLE_PATTERN, max_length=60), AfterValidator(_clean_words)]


class _Strict(BaseModel):
    # strict: "5" is not a number and true is not a window; extra keys are refused, so nothing unread is ever stored
    model_config = ConfigDict(extra="forbid", strict=True)


class Column(_Strict):
    label: Label
    expr: NumExpr


class Sort(_Strict):
    by: NumExpr
    order: Literal["asc", "desc"] = "desc"


ChartRef = Annotated[str, StringConstraints(pattern=LABEL_PATTERN, max_length=32)]


class Chart(_Strict):
    type: Literal["bar", "line"]
    x: ChartRef = Field(description='"strike" or a column label')
    y: List[ChartRef] = Field(min_length=1, max_length=2, description="one or two column labels")


class AnalysisSpec(_Strict):
    version: int = Field(default=SPEC_VERSION, ge=SPEC_VERSION, le=SPEC_VERSION)      # strict int: true is not 1
    title: Title
    window: int = Field(ge=0, le=MAX_WINDOW, description="strikes either side of ATM")
    columns: List[Column] = Field(min_length=1, max_length=MAX_COLUMNS)
    filters: List[BoolExpr] = Field(default_factory=list, max_length=MAX_FILTERS)
    sort: Optional[Sort] = None
    limit: Optional[int] = Field(default=None, ge=1, le=MAX_LIMIT)
    chart: Optional[Chart] = None

    @field_validator("columns")
    @classmethod
    def _unique_labels(cls, columns: List[Column]) -> List[Column]:
        seen = set()
        for c in columns:
            key = c.label.strip().lower()
            if key in seen or key == "strike":
                raise SpecError("column labels must be different from each other and from 'Strike'")
            seen.add(key)
        return columns

    @model_validator(mode="after")
    def _whole(self) -> "AnalysisSpec":
        if sum(len(t) for t in expressions(self)) > MAX_SPEC_EXPR_CHARS:
            raise SpecError(f"the spec's expressions together are longer than {MAX_SPEC_EXPR_CHARS} characters")
        total = sum(node_count(parse_expression(t)) for t in expressions(self))
        if total > MAX_NODES_SPEC:
            raise SpecError(f"the spec has more than {MAX_NODES_SPEC} expression parts")
        if self.chart is not None:
            labels = {c.label.strip().lower() for c in self.columns}
            if self.chart.x.strip().lower() != "strike" and self.chart.x.strip().lower() not in labels:
                raise SpecError("the chart's x must be 'strike' or one of the column labels")
            if any(y.strip().lower() not in labels for y in self.chart.y) or len({y.strip().lower() for y in self.chart.y}) != len(self.chart.y):
                raise SpecError("the chart's y must name one or two different column labels")
        return self


def expressions(spec: AnalysisSpec) -> List[str]:
    return [c.expr for c in spec.columns] + list(spec.filters) + ([spec.sort.by] if spec.sort else [])


def history_refs(spec: AnalysisSpec) -> List[str]:
    """The distinct history points the spec reads ("open", "-15m"), in order of appearance."""
    out: List[str] = []
    for text in expressions(spec):
        for node in _walk(parse_expression(text)):
            if isinstance(node, Ref) and node.at and node.at not in out:
                out.append(node.at)
    return out


# ---------------------------------------------------------------------------------------------------- evaluation

@dataclass(frozen=True)
class HistoryPoint:
    """One stored snapshot of this session (same underlying and expiry): when it was read and, per strike, the values
    of HISTORY_FIELDS it holds."""
    at: datetime
    rows: Mapping[float, Mapping[str, Optional[float]]]


def _aware(at: datetime) -> datetime:
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def resolve_history(at: str, history: Sequence[HistoryPoint], now: datetime) -> Optional[HistoryPoint]:
    """`open` = the session's first stored snapshot; `-Nm` = the latest at or before now - N minutes, and no more than
    HISTORY_TOLERANCE_MINUTES older than that; None when there is no such snapshot (the values are then missing)."""
    points = sorted((p for p in history if _aware(p.at) <= _aware(now)), key=lambda p: _aware(p.at))
    if not points:
        return None
    if at == "open":
        return points[0]
    target = _aware(now) - timedelta(minutes=int(at[1:-1]))
    before = [p for p in points if _aware(p.at) <= target]
    if not before or target - _aware(before[-1].at) > timedelta(minutes=HISTORY_TOLERANCE_MINUTES):
        return None
    return before[-1]


def _finite(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def atm_index(strikes: Sequence[float], spot: Optional[float]) -> Optional[int]:
    """Index of the strike nearest the spot in the sorted list; a tie takes the lower strike."""
    if spot is None or not strikes:
        return None
    return min(range(len(strikes)), key=lambda i: (abs(strikes[i] - spot), strikes[i]))


class _Context:
    def __init__(self, rows: List[Dict[str, Any]], points: Dict[str, Optional[HistoryPoint]]) -> None:
        self.rows = rows
        self.points = points
        self.window_cache: Dict[int, Tuple[List[Optional[float]], Optional[float]]] = {}

    def ref(self, node: Ref, row: Dict[str, Any]) -> Optional[float]:
        if node.at is None:
            return _finite(row.get(node.name))
        point = self.points.get(node.at)
        if point is None:
            return None
        strike = row.get("strike")
        values = point.rows.get(float(strike)) if strike is not None else None
        return _finite(values.get(node.name)) if values else None


def _num(node: Node, row: Dict[str, Any], ctx: _Context, index: int) -> Optional[float]:
    value = _eval(node, row, ctx, index)
    return value if isinstance(value, float) else None


def _eval(node: Node, row: Dict[str, Any], ctx: _Context, index: int) -> Union[float, bool, None]:
    if isinstance(node, Num):
        return float(node.value)
    if isinstance(node, Ref):
        return ctx.ref(node, row)
    if isinstance(node, Neg):
        v = _num(node.arg, row, ctx, index)
        return None if v is None else -v
    if isinstance(node, Bin):
        a, b = _num(node.left, row, ctx, index), _num(node.right, row, ctx, index)
        if a is None or b is None:
            return None
        if node.op == "+":
            out = a + b
        elif node.op == "-":
            out = a - b
        elif node.op == "*":
            out = a * b
        else:
            if b == 0:
                return None                      # division by zero: a missing value, never an infinity
            out = a / b
        return out if math.isfinite(out) else None
    if isinstance(node, Cmp):
        a, b = _num(node.left, row, ctx, index), _num(node.right, row, ctx, index)
        if a is None or b is None:
            return None
        return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b, "==": a == b, "!=": a != b}[node.op]
    if isinstance(node, Logic):
        values = [_eval(i, row, ctx, index) for i in node.items]
        if node.op == "and":
            if any(v is False for v in values):
                return False
            return None if any(v is None for v in values) else True
        if any(v is True for v in values):
            return True
        return None if any(v is None for v in values) else False
    # a function
    if node.fn == "abs":
        v = _num(node.args[0], row, ctx, index)
        return None if v is None else abs(v)
    if node.fn == "pct_change":
        now, then = _num(node.args[0], row, ctx, index), _num(node.args[1], row, ctx, index)
        if now is None or then is None or then == 0:
            return None
        out = (now - then) / then * 100.0
        return out if math.isfinite(out) else None
    values, scalar = _window(node, ctx)
    return values[index] if node.fn == "rank" else scalar


def _window(node: Call, ctx: _Context) -> Tuple[List[Optional[float]], Optional[float]]:
    """A window function over every row of the window (before filters), computed once per evaluation."""
    key = id(node)
    if key not in ctx.window_cache:
        values = [_num(node.args[0], r, ctx, i) for i, r in enumerate(ctx.rows)]
        present = [v for v in values if v is not None]
        scalar: Optional[float] = None
        ranks: List[Optional[float]] = []
        if node.fn == "rank":
            ranks = [None if v is None else float(1 + sum(1 for w in present if w > v)) for v in values]
        elif present:
            scalar = {"sum": sum(present), "avg": sum(present) / len(present), "min": min(present), "max": max(present)}[node.fn]
            scalar = scalar if math.isfinite(scalar) else None
        ctx.window_cache[key] = (ranks, scalar)
    return ctx.window_cache[key]


def _round(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value, ROUND_DIGITS)


def evaluate(spec: AnalysisSpec, rows: Sequence[Mapping[str, Any]], *, spot: Optional[float], now: datetime,
             history: Sequence[HistoryPoint] = ()) -> Dict[str, Any]:
    """The spec's table (and chart series) over `rows` - flat per-strike dicts with FIELDS keys (`strike` required) -
    with the ATM window around `spot`. Every number is read from `rows` / `history` or computed from them."""
    by_strike: Dict[float, Mapping[str, Any]] = {}
    for r in rows:
        strike = _finite(r.get("strike"))
        if strike is not None and strike not in by_strike:
            by_strike[strike] = r
    strikes = sorted(by_strike)
    spot_value = _finite(spot)
    columns = [{"key": "strike", "label": "Strike"}] + [{"key": f"c{i}", "label": c.label} for i, c in enumerate(spec.columns)]
    base: Dict[str, Any] = {"spot": spot_value, "atm_strike": None, "window": spec.window, "columns": columns, "rows": [],
                            "chart": None, "history": [], "rows_in_window": 0, "rows_kept": 0}
    if not strikes:
        return {**base, "state": "no_rows", "message": "The option chain has no strikes for this expiry."}
    centre = atm_index(strikes, spot_value)
    if centre is None:
        return {**base, "state": "no_spot", "message": "The underlying price is not available, so the ATM strike cannot be found."}
    lo, hi = max(0, centre - spec.window), min(len(strikes), centre + spec.window + 1)
    window: List[Dict[str, Any]] = []
    for i in range(lo, hi):
        row = {k: by_strike[strikes[i]].get(k) for k in FIELDS if k in by_strike[strikes[i]]}
        row.update(strike=strikes[i], atm_offset=float(i - centre), spot=spot_value)
        window.append(row)
    points = {at: resolve_history(at, history, now) for at in history_refs(spec)}
    ctx = _Context(window, points)
    col_nodes = [compile_expression(c.expr, "num") for c in spec.columns]
    filter_nodes = [compile_expression(f, "bool") for f in spec.filters]
    sort_node = compile_expression(spec.sort.by, "num") if spec.sort else None
    kept: List[Tuple[int, Dict[str, Any]]] = [(i, r) for i, r in enumerate(window)
                                              if all(_eval(f, r, ctx, i) is True for f in filter_nodes)]
    if sort_node is not None and spec.sort is not None:
        keyed = [(_num(sort_node, r, ctx, i), i, r) for i, r in kept]
        present = [k for k in keyed if k[0] is not None]
        missing = [k for k in keyed if k[0] is None]
        reverse = spec.sort.order == "desc"
        present.sort(key=lambda k: (-(k[0] or 0.0) if reverse else (k[0] or 0.0), k[2]["strike"]))
        kept = [(i, r) for _, i, r in present + missing]
    if spec.limit is not None:
        kept = kept[:spec.limit]
    out_rows = [{"strike": r["strike"], "atm_offset": int(r["atm_offset"]), "cells": [_round(_num(n, r, ctx, i)) for n in col_nodes]}
                for i, r in kept]
    result = {**base, "state": "ok", "message": None if out_rows else "No strike in the window meets the conditions.",
              "atm_strike": strikes[centre], "rows": out_rows, "rows_in_window": len(window), "rows_kept": len(out_rows),
              "history": [{"ref": at, "at": _aware(p.at).isoformat() if p else None} for at, p in points.items()]}
    result["chart"] = _chart(spec, columns, out_rows)
    return result


def _chart(spec: AnalysisSpec, columns: List[Dict[str, str]], rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if spec.chart is None or not rows:
        return None
    by_label = {c["label"].strip().lower(): idx for idx, c in enumerate(columns[1:])}
    x_key = spec.chart.x.strip().lower()
    ordered = sorted(rows, key=lambda r: r["strike"]) if x_key == "strike" else rows
    xs = [r["strike"] if x_key == "strike" else r["cells"][by_label[x_key]] for r in ordered]
    series = []
    for y in spec.chart.y:
        idx = by_label[y.strip().lower()]
        series.append({"label": spec.columns[idx].label, "points": [{"x": x, "y": r["cells"][idx]} for x, r in zip(xs, ordered)]})
    x_label = "Strike" if x_key == "strike" else spec.columns[by_label[x_key]].label
    return {"type": spec.chart.type, "x_label": x_label, "series": series}


# ---------------------------------------------------------------------------------------------------- plain words

def _number_text(value: float) -> str:
    if float(value).is_integer() and abs(value) < 1e15:
        return str(int(value))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _ref_words(node: Ref) -> str:
    base = FIELDS[node.name]
    if node.at is None:
        return base
    if node.at == "open":
        return f"{base} at the session's first stored snapshot"
    minutes = int(node.at[1:-1])
    return (f"{base} {minutes} minute{'s' if minutes != 1 else ''} ago (the latest stored snapshot at or before then, "
            f"no more than {HISTORY_TOLERANCE_MINUTES} minutes older)")


_OP_WORDS = {"+": "plus", "-": "minus", "*": "times", "/": "divided by"}
_CMP_WORDS = {"<": "is less than", "<=": "is at most", ">": "is greater than", ">=": "is at least", "==": "equals", "!=": "is not"}


def words(node: Node) -> str:
    """One expression in plain words."""
    if isinstance(node, Num):
        return _number_text(node.value)
    if isinstance(node, Ref):
        return _ref_words(node)
    if isinstance(node, Neg):
        return f"minus {_group(node.arg)}"
    if isinstance(node, Bin):
        return f"{_group(node.left)} {_OP_WORDS[node.op]} {_group(node.right)}"
    if isinstance(node, Cmp):
        return f"{words(node.left)} {_CMP_WORDS[node.op]} {words(node.right)}"
    if isinstance(node, Logic):
        return f" {node.op} ".join(_group(i) for i in node.items)
    a = node.args
    if node.fn == "abs":
        return f"the absolute value of {_group(a[0])}"
    if node.fn == "pct_change":
        return f"the percent change from {_group(a[1])} to {_group(a[0])}"
    if node.fn == "rank":
        return f"the rank of {_group(a[0])} among the window's strikes (1 = the largest)"
    name = {"sum": "the total", "avg": "the average", "min": "the smallest value", "max": "the largest value"}[node.fn]
    return f"{name} of {_group(a[0])} over the window's strikes"


def _group(node: Node) -> str:
    text = words(node)
    return f"({text})" if isinstance(node, (Bin, Logic, Cmp)) else text


def describe(spec: AnalysisSpec) -> List[str]:
    """"How this was calculated": the spec in plain sentences, from fixed templates and the spec's own parts."""
    n = spec.window
    lines = [f"Strikes: the ATM strike (the listed strike nearest the underlying price) and {n} listed strike{'s' if n != 1 else ''} "
             f"on each side of it." if n else "Strikes: the ATM strike only (the listed strike nearest the underlying price)."]
    for c in spec.columns:
        lines.append(f"Column \"{c.label}\": {words(parse_expression(c.expr))}.")
    for f in spec.filters:
        lines.append(f"Kept only where {words(parse_expression(f))}.")
    if spec.filters:
        lines.append("A strike with a missing value in a condition is left out.")
    if spec.sort is not None:
        lines.append(f"Sorted by {words(parse_expression(spec.sort.by))}, "
                     f"{'largest' if spec.sort.order == 'desc' else 'smallest'} first (missing values last).")
    else:
        lines.append("Sorted by strike, lowest first.")
    if spec.limit is not None:
        lines.append(f"At most {spec.limit} row{'s' if spec.limit != 1 else ''} shown.")
    if any(fn in WINDOW_FUNCTIONS for t in expressions(spec) for fn in _functions(t)):
        lines.append("Totals, averages, smallest / largest values and ranks are taken over every strike of the window, before the conditions.")
    if any(isinstance(node, Bin) and node.op == "/" for t in expressions(spec) for node in _walk(parse_expression(t))) \
            or any("pct_change" == fn for t in expressions(spec) for fn in _functions(t)):
        lines.append("A division by zero, or a percent change from zero, gives a missing value.")
    if history_refs(spec):
        lines.append("Earlier values come from the option chain snapshots stored during this session for the same expiry; "
                     "a value with no such snapshot is missing.")
    if spec.chart is not None:
        own = {c.label.strip().lower(): c.label for c in spec.columns}           # the column's own label, as the table shows it
        ys = " and ".join(f"\"{own[y.strip().lower()]}\"" for y in spec.chart.y)
        x = "strike" if spec.chart.x.strip().lower() == "strike" else f"\"{own[spec.chart.x.strip().lower()]}\""
        lines.append(f"Chart: a {spec.chart.type} chart of {ys} by {x}.")
    return lines


def _functions(text: str) -> List[str]:
    return [node.fn for node in _walk(parse_expression(text)) if isinstance(node, Call)]


__all__ = ["AnalysisSpec", "Column", "Sort", "Chart", "SpecError", "HistoryPoint", "FIELDS", "HISTORY_FIELDS", "FUNCTIONS",
           "WINDOW_FUNCTIONS", "parse_expression", "compile_expression", "evaluate", "describe", "words", "history_refs",
           "resolve_history", "atm_index", "MAX_WINDOW", "MAX_COLUMNS", "MAX_FILTERS", "MAX_LIMIT", "MAX_EXPR_CHARS",
           "MAX_DEPTH", "MAX_NODES_EXPR", "MAX_NODES_SPEC", "MAX_SPEC_EXPR_CHARS", "SPEC_VERSION", "LABEL_PATTERN", "TITLE_PATTERN"]
