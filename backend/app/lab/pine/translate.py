"""AMW-20 / handover 11c-1: Pine Script subset -> ScreenQL. Translated, never executed.

The translator walks the Pine syntax tree (`parser.py`) and builds ScreenQL nodes (`app.screener.nodes`) from a fixed
table of supported Pine names. User text never becomes ScreenQL text directly: names come from the table, a user's
variable is inlined as the nodes it was built from, an input becomes a `$parameter` whose name is a plain identifier,
and every number is a parsed float. The canonical ScreenQL text is then validated by the same `compile_screen` a
user's own screen goes through (types, units, windows, look-ahead, cost).

What it returns (`Translation`):
- `signals`: every signal condition the script states (strategy.entry / strategy.close inside `if` or with `when=`,
  alertcondition, plotshape / plotchar), each as one ScreenQL condition with its parameters, its plain-words reading
  and its problems;
- `params`: the script's inputs (type, default, bounds, title);
- `diagnostics`: line-numbered findings - unsupported (with the closest supported alternative), ignored (order sizing,
  drawing) and notes;
- `constructs`: what was found on each line and what it maps to.

Look-ahead is refused for the whole script (no signal is offered): `barmerge.lookahead_on` anywhere, a negative
history offset `x[-1]`, a negative `offset=` and `request.security(..., lookahead=...)` other than `lookahead_off`.

Order sizing, stops and targets in the script (`qty`, `default_qty_*`, `strategy.exit`, `stop=` / `limit=`) are reported
and ignored: the signal is ATP's; lots, exits and the number of trades come from the user's own settings (CLAUDE.md §3).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from app.lab.pine import parser as p
from app.lab.pine.tokens import PineSyntaxError, Tok
from app.screener import compile_screen
from app.screener import nodes as n

FLAG = "strategy_lab_pine"
MAX_NODES = 1_500            # ScreenQL nodes in one translated value (stops exponential inlining early)
MAX_OUT_DEPTH = 40           # nesting of one translated value (ScreenQL's own parser allows 60)
MAX_OFFSET = 500             # bars back, as the ScreenQL validator
MAX_SIGNALS = 20
SUPPORTED_VERSIONS = ("5", "6")
FIELDS = ("open", "high", "low", "close", "volume")


# --- results -------------------------------------------------------------------------------------------------------------

@dataclass
class Diagnostic:
    line: int
    col: int
    severity: str            # error / warning / info
    code: str
    message: str
    alternative: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {"line": self.line, "col": self.col, "severity": self.severity, "code": self.code, "message": self.message,
                "alternative": self.alternative}


@dataclass
class Construct:
    line: int
    construct: str
    status: str              # supported / ignored / unsupported
    maps_to: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {"line": self.line, "construct": self.construct, "status": self.status, "maps_to": self.maps_to}


@dataclass
class ParamSpec:
    name: str
    type: str                # int / float / bool
    default: Any
    min: Optional[float] = None
    max: Optional[float] = None
    title: str = ""
    line: int = 0
    options: Tuple[Any, ...] = ()

    def as_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "type": self.type, "default": self.default, "min": self.min, "max": self.max,
                "title": self.title, "line": self.line, "options": list(self.options)}


@dataclass
class Signal:
    kind: str                # long_entry / short_entry / exit / alert / marker
    label: str
    line: int
    ok: bool
    text: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)
    words: str = ""
    problems: List[str] = field(default_factory=list)

    def as_dict(self, index: int) -> Dict[str, Any]:
        return {"index": index, "kind": self.kind, "label": self.label, "line": self.line, "ok": self.ok, "text": self.text,
                "params": self.params, "words": self.words, "problems": self.problems}


@dataclass
class Translation:
    ok: bool                 # at least one signal translated and nothing refused
    complete: bool           # and no line was left untranslated
    refused: bool            # look-ahead: nothing is offered
    version: Optional[str]
    script_kind: Optional[str]
    title: str
    signals: List[Signal]
    params: Dict[str, ParamSpec]
    diagnostics: List[Diagnostic]
    constructs: List[Construct]

    def primary(self) -> Optional[int]:
        """The signal "Run as screen" uses by default: the first translated long-side entry, else the first translated."""
        for kinds in (("long_entry",), ("long_entry", "short_entry", "exit", "alert", "marker")):
            for i, s in enumerate(self.signals):
                if s.ok and s.kind in kinds:
                    return i
        return None

    def as_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "complete": self.complete, "refused": self.refused, "version": self.version,
                "script_kind": self.script_kind, "title": self.title, "primary": self.primary(),
                "signals": [s.as_dict(i) for i, s in enumerate(self.signals)],
                "params": {k: v.as_dict() for k, v in self.params.items()},
                "diagnostics": [d.as_dict() for d in sorted(self.diagnostics, key=lambda d: (d.line, d.col))],
                "constructs": [c.as_dict() for c in self.constructs]}


# --- translated values ---------------------------------------------------------------------------------------------------

@dataclass
class V:
    kind: str                       # num / bool / dir / dirchange / tuple / str / cosmetic / side / const
    node: Any = None                # the ScreenQL node (num / bool); the Supertrend call (dir / dirchange)
    size: int = 1
    depth: int = 1
    offset: int = 0                 # dir / dirchange: bars back
    items: Tuple["V", ...] = ()
    text: str = ""
    literal: Optional[float] = None  # num: a constant number
    is_int: bool = False
    param: Optional[str] = None     # num / bool: this value is the input $param


class _Refuse(Exception):
    def __init__(self, code: str, message: str, alternative: str, line: int, col: int, *, look_ahead: bool = False) -> None:
        super().__init__(message)
        self.code, self.message, self.alternative, self.line, self.col, self.look_ahead = code, message, alternative, line, col, look_ahead


class _Depends(Exception):
    """A value built on a line that did not translate (its own diagnostic is already there)."""

    def __init__(self, name: str, line: int) -> None:
        super().__init__(name)
        self.name, self.line = name, line


def _num(node: Any, size: int = 1, depth: int = 1, **kw: Any) -> V:
    return V("num", node, size, depth, **kw)


def _bool(node: Any, size: int = 1, depth: int = 1) -> V:
    return V("bool", node, size, depth)


def _grow(line: int, col: int, *parts: V) -> Tuple[int, int]:
    size = 1 + sum(v.size for v in parts)
    depth = 1 + max((v.depth for v in parts), default=0)
    if size > MAX_NODES:
        raise _Refuse("limit", f"the translated condition would have more than {MAX_NODES:,} parts",
                      "split it into smaller conditions or reuse fewer derived values", line, col)
    if depth > MAX_OUT_DEPTH:
        raise _Refuse("limit", f"the translated condition would be nested more than {MAX_OUT_DEPTH} levels deep",
                      "simplify the expression", line, col)
    return size, depth


def shift(node: Any, k: int) -> Any:
    """The same ScreenQL value `k` bars back: offsets added to every field and call (a call's offset shifts its whole
    result, so its arguments stay as they are)."""
    if k == 0:
        return node
    if isinstance(node, n.Field):
        return n.Field(node.name, node.offset + k, node.tf)
    if isinstance(node, n.Call):
        return n.Call(node.name, list(node.args), dict(node.kwargs), node.offset + k, node.tf)
    if isinstance(node, (n.Num, n.Param, n.Bool, n.Str)):
        return node
    if isinstance(node, n.Unary):
        return n.Unary(node.op, shift(node.operand, k))
    if isinstance(node, n.Binary):
        return n.Binary(node.op, shift(node.left, k), shift(node.right, k))
    if isinstance(node, n.Compare):
        return n.Compare(node.op, shift(node.left, k), shift(node.right, k))
    if isinstance(node, n.Logic):
        return n.Logic(node.op, [shift(i, k) for i in node.items])
    if isinstance(node, n.Not):
        return n.Not(shift(node.item, k))
    raise TypeError(f"cannot shift {type(node).__name__}")


def _max_offset(node: Any) -> int:
    best = 0
    for x in n.walk(node):
        if isinstance(x, (n.Field, n.Call)):
            best = max(best, x.offset)
    return best


# --- the tables ----------------------------------------------------------------------------------------------------------

COSMETIC_PREFIXES = ("color.", "shape.", "location.", "size.", "display.", "plot.style", "position.", "text.", "xloc.",
                     "yloc.", "extend.", "line.style", "hline.style", "label.style", "format.", "font.", "order.",
                     "scale.", "currency.", "alert.freq", "strategy.commission", "strategy.percent_of_equity",
                     "strategy.cash", "strategy.fixed", "strategy.oca", "strategy.direction", "dividends.", "earnings.",
                     "splits.", "adjustment.", "session.", "barmerge.gaps", "math.pi", "math.e")
COSMETIC_CALLS = {"plot", "hline", "fill", "bgcolor", "barcolor", "plotcandle", "plotbar", "plotarrow", "color.new",
                  "color.rgb", "color.from_gradient", "label.new", "label.delete", "label.set_text", "label.set_xy",
                  "line.new", "line.delete", "line.set_xy1", "line.set_xy2", "box.new", "box.delete", "table.new",
                  "table.cell", "runtime.error", "log.info", "log.warning", "log.error", "str.tostring", "str.format",
                  "linefill.new", "polyline.new", "max_bars_back"}
IGNORED_ORDER_CALLS = {"strategy.exit", "strategy.cancel", "strategy.cancel_all", "strategy.risk.allow_entry_in",
                       "strategy.risk.max_drawdown", "strategy.risk.max_intraday_loss", "strategy.risk.max_cons_loss_days",
                       "strategy.risk.max_intraday_filled_orders", "strategy.risk.max_position_size"}
SIZING_KWARGS = {"qty", "qty_percent", "default_qty_type", "default_qty_value", "initial_capital", "pyramiding",
                 "commission_type", "commission_value", "slippage", "currency", "margin_long", "margin_short",
                 "risk_free_rate", "use_bar_magnifier", "close_entries_rule", "backtest_fill_limits_assumption",
                 "max_lines_count", "max_labels_count", "max_boxes_count", "max_bars_back", "max_polylines_count",
                 "fill_orders_on_standard_ohlc", "calc_on_order_fills", "process_orders_on_close", "dynamic_requests"}
ORDER_PRICE_KWARGS = {"limit", "stop", "oca_name", "oca_type", "comment", "alert_message", "disable_alert", "immediately"}

_AVG_ALT = "ta.ema(src, len) or ta.sma(src, len) is the nearest supported moving average"
UNSUPPORTED_FUNCTIONS: Dict[str, str] = {
    "ta.wma": _AVG_ALT,
    "ta.hma": _AVG_ALT,
    "ta.vwma": _AVG_ALT,
    "ta.alma": _AVG_ALT,
    "ta.swma": _AVG_ALT,
    "ta.linreg": _AVG_ALT,
    "ta.rma": "ta.ema(src, 2 * len - 1) is the same smoothing as Wilder's average (write the length as a number)",
    "ta.pivothigh": "SwingHigh() in a ScreenQL screen reads confirmed swing highs",
    "ta.pivotlow": "SwingLow() in a ScreenQL screen reads confirmed swing lows",
    "ta.valuewhen": "compare the values directly, or use ta.highest / ta.lowest over a window",
    "ta.barssince": "count bars with a ScreenQL screen: Count(n, cond) or CountStreak(cond)",
    "ta.vwap": "VWAP() in a ScreenQL screen (session VWAP)",
    "ta.sar": "ta.supertrend(factor, atrLength) is the supported trailing trend line",
    "ta.cci": "ta.rsi(close, len) is the supported oscillator",
    "ta.mfi": "ta.rsi(close, len) is the supported oscillator",
    "ta.wpr": "ta.stoch(close, high, low, len) (Williams %R is the stochastic minus 100)",
    "ta.cmo": "ta.rsi(close, len) is the supported oscillator",
    "ta.kc": "ta.bb(close, len, mult) (Bollinger bands) or ta.ema +/- a multiple of ta.atr(len)",
    "ta.obv": "volume compared with ta.sma(volume, len)",
    "ta.tr": "ta.atr(len) (the average true range)",
    "ta.percentrank": "PercentileRank() in a ScreenQL screen ranks across symbols",
    "ta.correlation": "not available yet",
    "ta.median": "ta.sma(src, len)",
    "ta.mode": "ta.sma(src, len)",
    "ta.range": "ta.highest(src, len) - ta.lowest(src, len)",
    "ta.cum": "not available: ATP does not sum the whole history",
    "ta.dev": "ta.stdev(src, len)",
    "ta.variance": "ta.stdev(src, len)",
    "ta.cog": "ta.rsi(close, len) is the supported oscillator",
    "ta.tsi": "ta.rsi(close, len) is the supported oscillator",
    "ta.bbw": "(upper - lower) / middle from [middle, upper, lower] = ta.bb(close, len, mult)",
    "ta.pivot_point_levels": "PDH() / PDL() / PDC() in a ScreenQL screen",
    "math.abs": "write both sides: x > k or x < -k",
    "math.round": "compare the value directly",
    "math.floor": "compare the value directly",
    "math.ceil": "compare the value directly",
    "math.sqrt": "not available",
    "math.pow": "write the product out: x * x",
    "math.log": "ta.roc(src, len) for a rate of change",
    "math.sum": "ta.sma(src, len) * len",
    "math.avg": "(a + b) / 2",
    "math.sign": "compare with 0",
    "nz": "not needed: ATP never matches a bar where a value is missing",
    "na": "not needed: ATP never matches a bar where a value is missing",
    "fixnan": "not needed: ATP never matches a bar where a value is missing",
    "request.security": "the Pine subset reads the chart's own timeframe; a ScreenQL screen can read a higher one with @1w",
    "request.security_lower_tf": "the Pine subset reads the chart's own timeframe",
    "request.financial": "the fundamentals functions in a ScreenQL screen (EPSGrowthYoY, ReturnOnCapital...)",
    "request.dividends": "DaysToEvent() in a ScreenQL screen",
    "request.earnings": "DaysToEvent() in a ScreenQL screen",
    "request.quandl": "not available",
    "request.seed": "not available",
    "request.economic": "not available",
    "strategy.order": "strategy.entry(id, strategy.long) inside `if cond`",
    "timestamp": "not available: the subset has no clock-time rules",
    "time": "TimeOfDay(start, end) in a ScreenQL screen",
    "array.new_float": "the subset has no arrays: use ta.highest / ta.lowest over a window",
    "array.from": "the subset has no arrays",
    "str.contains": "the subset has no text rules",
}
UNSUPPORTED_NAMES: Dict[str, str] = {
    "na": "not needed: ATP never matches a bar where a value is missing",
    "bar_index": "the subset has no bar counter; use a history reference (x[1]) instead",
    "last_bar_index": "the subset has no bar counter",
    "time": "TimeOfDay(start, end) in a ScreenQL screen",
    "time_close": "TimeOfDay(start, end) in a ScreenQL screen",
    "timenow": "the subset reads closed bars only, never the clock",
    "hour": "TimeOfDay(start, end) in a ScreenQL screen",
    "minute": "TimeOfDay(start, end) in a ScreenQL screen",
    "dayofweek": "the subset has no calendar rules",
    "dayofmonth": "the subset has no calendar rules",
    "month": "the subset has no calendar rules",
    "year": "the subset has no calendar rules",
    "ohlc4": "",
    "ta.tr": "ta.atr(len) (the average true range)",
    "ta.vwap": "VWAP() in a ScreenQL screen (session VWAP)",
    "ta.obv": "volume compared with ta.sma(volume, len)",
    "barstate.islast": "ATP reads closed bars only; barstate.isconfirmed is accepted",
    "barstate.isrealtime": "ATP reads closed bars only; barstate.isconfirmed is accepted",
    "barstate.isnew": "ATP reads closed bars only; barstate.isconfirmed is accepted",
    "barstate.ishistory": "ATP reads closed bars only; barstate.isconfirmed is accepted",
    "barstate.isfirst": "ATP reads closed bars only",
    "syminfo.tickerid": "the screen runs on the symbols you pick",
    "syminfo.ticker": "the screen runs on the symbols you pick",
    "timeframe.period": "the screen's timeframe is picked when it runs",
    "strategy.position_size": "the signal does not read positions; sizing and exits are your own settings",
    "strategy.equity": "the signal does not read the account",
    "strategy.opentrades": "the signal does not read positions",
}
SUPPORTED_LIST = ("ta.ema, ta.sma, ta.rsi, ta.atr, ta.supertrend, ta.crossover, ta.crossunder, ta.cross, ta.highest, "
                  "ta.lowest, ta.change, ta.mom, ta.macd, ta.stoch, ta.bb, ta.stdev, ta.roc, ta.rising, ta.falling, ta.dmi, "
                  "math.max, math.min")


# --- the translator ------------------------------------------------------------------------------------------------------

class _Translator:  # noqa: PLR0904 - one method per construct family
    def __init__(self, base_tf: str) -> None:
        self.base_tf = base_tf
        self.env: Dict[str, V] = {}
        self.bad: Dict[str, int] = {}              # name -> line where it failed
        self.declared: Dict[str, int] = {}
        self.params: Dict[str, ParamSpec] = {}
        self.diags: List[Diagnostic] = []
        self.constructs: List[Construct] = []
        self._seen: set = set()
        self.raw_signals: List[Tuple[str, str, int, int, Optional[V], Optional[Tuple[str, int]]]] = []
        self.script_kind: Optional[str] = None
        self.title = ""
        self.header_line = 0

    # bookkeeping
    def diag(self, line: int, col: int, severity: str, code: str, message: str, alternative: str = "") -> None:
        if code == "look_ahead" and any(d.code == "look_ahead" and d.line == line for d in self.diags):
            return                        # the text scan already named this line
        self.diags.append(Diagnostic(line, col, severity, code, message, alternative))

    def construct(self, line: int, name: str, status: str, maps_to: str = "") -> None:
        key = (line, name, status)
        if key not in self._seen:
            self._seen.add(key)
            self.constructs.append(Construct(line, name, status, maps_to))

    def refuse(self, code: str, message: str, alternative: str, node: Any, construct: Optional[str] = None) -> _Refuse:
        line, col = getattr(node, "line", 0), getattr(node, "col", 0)
        if construct:
            self.construct(line, construct, "unsupported", "")
        return _Refuse(code, message, alternative, line, col)

    # --- values ---
    def need_num(self, v: V, node: Any, what: str) -> V:
        if v.kind == "num":
            return v
        if v.kind == "dir":
            raise self.refuse("supertrend_direction", "the Supertrend direction can only be compared with a number (dir < 0, dir > 0) or "
                              "with itself bars back", "write the condition as dir < 0 (up) or dir > 0 (down)", node)
        if v.kind == "str":
            raise self.refuse("text_value", f"{what} must be a number, not text", "use a number, or input.int / input.float", node)
        raise self.refuse("type", f"{what} must be a number, here it is {'a condition (true/false)' if v.kind == 'bool' else 'not a number'}",
                          "compare the values with > or < to make a condition, or use the number itself", node)

    def need_bool(self, v: V, node: Any, what: str) -> V:
        if v.kind == "bool":
            return v
        if v.kind == "num":
            raise self.refuse("type", f"{what} must be a condition (true/false), here it is a number",
                              "compare it: x > 0, close > ta.ema(close, 20)", node)
        if v.kind == "dir":
            raise self.refuse("supertrend_direction", f"{what} must be a condition; the Supertrend direction is a number",
                              "write dir < 0 for up, dir > 0 for down", node)
        raise self.refuse("type", f"{what} must be a condition (true/false)", "compare values with > or < to make a condition", node)

    def window(self, v: V, node: Any, fn: str, arg: str) -> Any:
        v = self.need_num(v, node, f"{fn}'s {arg}")
        if v.literal is not None:
            if not float(v.literal).is_integer() or not 1 <= v.literal <= 500:
                raise self.refuse("window", f"{fn}'s {arg} must be a whole number of bars from 1 to 500, got {v.literal:g}",
                                  "use a whole number from 1 to 500", node)
            return n.Num(float(int(v.literal)))
        if v.param is not None:
            spec = self.params.get(v.param)
            if spec is not None and spec.type != "int":
                raise self.refuse("window", f"{fn}'s {arg} must be a whole number; input {v.param!r} is not input.int",
                                  "declare the length with input.int", node)
            return n.Param(v.param)
        raise self.refuse("window", f"{fn}'s {arg} must be a number or an input, not a calculation",
                          "write the length as a number, or declare it with input.int on its own line", node)

    def setting(self, v: V, node: Any, fn: str, arg: str) -> Any:
        """A setting read once for the whole series (Supertrend factor, Bollinger multiplier): a number or an input."""
        v = self.need_num(v, node, f"{fn}'s {arg}")
        if v.literal is not None:
            if not math.isfinite(v.literal) or v.literal <= 0:
                raise self.refuse("setting", f"{fn}'s {arg} must be a positive number", "use a positive number", node)
            return n.Num(float(v.literal))
        if v.param is not None:
            return n.Param(v.param)
        raise self.refuse("setting", f"{fn}'s {arg} must be a number or an input, not a calculation",
                          "write it as a number, or declare it with input.float on its own line", node)

    def close_source(self, v: V, node: Any, fn: str) -> int:
        """RSI and the Bollinger bands in ATP read the close: the source must be `close` (or close[k]); returns k."""
        x = v.node if v.kind == "num" else None
        if isinstance(x, n.Field) and x.name == "close" and x.tf is None:
            return x.offset
        raise self.refuse("source", f"{fn} in ATP reads the close; another source is not translated",
                          f"use close as the source of {fn}", node)

    # --- expressions ---
    def tx(self, e: p.PExpr) -> V:  # noqa: C901 - one switch over the Pine node kinds
        if isinstance(e, p.PNum):
            if not math.isfinite(e.value):
                raise self.refuse("number", "the number is out of range", "use a smaller number", e)
            return _num(n.Num(e.value), literal=e.value, is_int=e.is_int)
        if isinstance(e, p.PBool):
            return _bool(n.Bool(e.value))
        if isinstance(e, p.PStr):
            return V("str", text=e.value)
        if isinstance(e, p.PColor):
            return V("cosmetic")
        if isinstance(e, p.PList):
            raise self.refuse("array", "lists ([a, b]) are only allowed on the left of a tuple declaration",
                              "declare each value under its own name", e, "[...]")
        if isinstance(e, p.PName):
            return self.name(e)
        if isinstance(e, p.PIndex):
            return self.index(e)
        if isinstance(e, p.PUnary):
            return self.unary(e)
        if isinstance(e, p.PBinary):
            return self.binary(e)
        if isinstance(e, p.PTernary):
            return self.ternary(e)
        if isinstance(e, p.PCall):
            return self.call(e)
        raise self.refuse("syntax", "not a Pine value", "", e)

    def name(self, e: p.PName) -> V:  # noqa: C901
        name = e.name
        if name in self.env:
            return self.env[name]
        if name in self.bad:
            raise _Depends(name, self.bad[name])
        if name in FIELDS:
            self.construct(e.line, name, "supported", name)
            return _num(n.Field(name))
        if name in ("hl2", "hlc3", "ohlc4", "hlcc4"):
            hi, lo, cl, op = n.Field("high"), n.Field("low"), n.Field("close"), n.Field("open")
            parts = {"hl2": [hi, lo], "hlc3": [hi, lo, cl], "ohlc4": [op, hi, lo, cl], "hlcc4": [hi, lo, cl, n.Field("close")]}[name]
            total: Any = parts[0]
            for x in parts[1:]:
                total = n.Binary("+", total, x)
            node = n.Binary("/", total, n.Num(float(len(parts))))
            self.construct(e.line, name, "supported", n.to_text(node))
            return _num(node, size=2 * len(parts) + 1, depth=len(parts) + 1)
        if name in ("strategy.long", "strategy.short"):
            return V("side", text=name.split(".")[1])
        if name == "barstate.isconfirmed":
            self.construct(e.line, name, "supported", "TRUE (ATP reads closed bars only)")
            return _bool(n.Bool(True))
        if name == "barmerge.lookahead_on":
            raise _Refuse("look_ahead", "barmerge.lookahead_on reads a higher-timeframe bar before it has closed (look-ahead)",
                          "remove lookahead (the default, barmerge.lookahead_off)", e.line, e.col, look_ahead=True)
        if name == "barmerge.lookahead_off":
            return V("const")
        if name.startswith(COSMETIC_PREFIXES):
            return V("cosmetic")
        if name in UNSUPPORTED_NAMES:
            raise self.refuse("unsupported_name", f"{name} is not in the Pine subset", UNSUPPORTED_NAMES[name] or "", e, name)
        if name.startswith(("ta.", "math.", "request.", "str.", "array.", "matrix.", "map.", "input")):
            raise self.refuse("unsupported_name", f"{name} is not in the Pine subset", f"supported: {SUPPORTED_LIST}", e, name)
        raise self.refuse("unknown_name", f"{name!r} is not declared before this line", f"declare {name} = ... on a line above", e)

    def index(self, e: p.PIndex) -> V:
        target = self.tx(e.target)
        idx = self.tx(e.index)
        if idx.kind == "num" and idx.literal is not None:
            k = idx.literal
            if k < 0:
                raise _Refuse("look_ahead", f"[{k:g}] reads a future bar (look-ahead)", "history references count bars back: x[1], x[2]",
                              e.line, e.col, look_ahead=True)
            if not float(k).is_integer():
                raise self.refuse("history", "a history reference is a whole number of bars back", "write x[1], x[2]...", e)
            if k > MAX_OFFSET:
                raise self.refuse("history", f"a history reference is at most {MAX_OFFSET} bars back", f"use at most [{MAX_OFFSET}]", e)
            k_int = int(k)
            self.construct(e.line, "x[n]", "supported", f"[{k_int}]")
            if target.kind in ("num", "bool"):
                out = shift(target.node, k_int)
                if _max_offset(out) > MAX_OFFSET:
                    raise self.refuse("history", f"the history reference reaches more than {MAX_OFFSET} bars back",
                                      "use a shorter reference", e)
                return V(target.kind, out, target.size, target.depth)
            if target.kind in ("dir", "dirchange"):
                return V(target.kind, target.node, target.size, target.depth, offset=target.offset + k_int)
            raise self.refuse("history", "a history reference needs a number or a condition", "", e)
        if idx.kind == "num" and idx.param is not None and target.kind == "num":
            spec = self.params.get(idx.param)
            if spec is not None and spec.type == "int":
                if isinstance(spec.default, (int, float)) and spec.default < 0 or (spec.min is not None and spec.min < 0):
                    raise _Refuse("look_ahead", f"input {idx.param!r} may be negative: a negative history reference reads a future bar",
                                  "give the input minval=0", e.line, e.col, look_ahead=True)
                size, depth = _grow(e.line, e.col, target)
                self.construct(e.line, "x[input]", "supported", "Lag(x, $n)")
                return _num(n.Call("Lag", [target.node, n.Param(idx.param)]), size + 1, depth)
        raise self.refuse("history", "a history reference must be a whole number of bars back (or an input.int for a number)",
                          "write x[1], x[2]...", e)

    def unary(self, e: p.PUnary) -> V:
        v = self.tx(e.operand)
        if e.op == "not":
            v = self.need_bool(v, e.operand, "`not`")
            if isinstance(v.node, n.Bool):
                return _bool(n.Bool(not v.node.value))
            if isinstance(v.node, n.Not):
                return _bool(v.node.item, max(1, v.size - 1), max(1, v.depth - 1))
            size, depth = _grow(e.line, e.col, v)
            return _bool(n.Not(v.node), size, depth)
        v = self.need_num(v, e.operand, f"`{e.op}`")
        if e.op == "+":
            return v
        if v.literal is not None:
            return _num(n.Num(-v.literal), literal=-v.literal, is_int=v.is_int)
        size, depth = _grow(e.line, e.col, v)
        return _num(n.Unary("-", v.node), size, depth)

    def binary(self, e: p.PBinary) -> V:  # noqa: C901
        op = e.op
        left, right = self.tx(e.left), self.tx(e.right)
        if op in ("and", "or"):
            a = self.need_bool(left, e.left, f"`{op}`")
            b = self.need_bool(right, e.right, f"`{op}`")
            group = "ALL" if op == "and" else "ANY"
            items: List[Any] = []
            for v in (a, b):
                if isinstance(v.node, n.Logic) and v.node.op == group:
                    items.extend(v.node.items)
                else:
                    items.append(v.node)
            size, depth = _grow(e.line, e.col, a, b)
            self.construct(e.line, op, "supported", "AND" if op == "and" else "OR")
            return _bool(n.Logic(group, items), size, depth)
        if op in ("+", "-", "*", "/", "%"):
            a = self.need_num(left, e.left, f"`{op}`")
            b = self.need_num(right, e.right, f"`{op}`")
            if op == "%":
                raise self.refuse("modulo", "`%` (remainder) is not in the Pine subset", "compare the values directly", e, "%")
            if a.literal is not None and b.literal is not None:
                if op == "/" and b.literal == 0:
                    raise self.refuse("number", "division by zero", "divide by a non-zero number", e)
                value = {"+": a.literal + b.literal, "-": a.literal - b.literal, "*": a.literal * b.literal}.get(op)
                if value is None:
                    value = a.literal / b.literal
                if not math.isfinite(value):
                    raise self.refuse("number", "the number is out of range", "use a smaller number", e)
                return _num(n.Num(value), literal=value, is_int=a.is_int and b.is_int and op != "/" and float(value).is_integer())
            size, depth = _grow(e.line, e.col, a, b)
            return _num(n.Binary(op, a.node, b.node), size, depth)
        # comparisons
        if left.kind in ("dir", "dirchange") or right.kind in ("dir", "dirchange"):
            return self.direction_compare(e, left, right)
        if left.kind == "bool" and right.kind == "bool" and op in ("==", "!="):
            lit, other = (left, right) if isinstance(left.node, n.Bool) else (right, left)
            if isinstance(lit.node, n.Bool):
                want = lit.node.value == (op == "==")
                if want:
                    return other
                size, depth = _grow(e.line, e.col, other)
                return _bool(n.Not(other.node), size, depth)
            raise self.refuse("bool_compare", "comparing two conditions with == or != is not in the Pine subset",
                              "combine them with and / or / not", e)
        a = self.need_num(left, e.left, f"`{op}`")
        b = self.need_num(right, e.right, f"`{op}`")
        size, depth = _grow(e.line, e.col, a, b)
        return _bool(n.Compare(op, a.node, b.node), size, depth)

    # Supertrend direction: Pine's `dir` is -1 while the trend is up and +1 while it is down. ATP's Supertrend line is
    # below the close in an up trend and above it in a down trend, so `dir < 0` on bar k is `close[k] > Supertrend[k]`.
    def _dir_state(self, v: V, value: int) -> Any:
        st = shift(v.node, v.offset)
        close = n.Field("close", v.offset)
        return n.Compare(">" if value < 0 else "<", close, st)

    def _any(self, parts: List[Any]) -> Any:
        if not parts:
            return n.Bool(False)
        return parts[0] if len(parts) == 1 else n.Logic("ANY", parts)

    def direction_compare(self, e: p.PBinary, left: V, right: V) -> V:  # noqa: C901
        op = e.op
        ops: Dict[str, Callable[[float, float], bool]] = {"<": lambda x, y: x < y, ">": lambda x, y: x > y, "<=": lambda x, y: x <= y,
                                                           ">=": lambda x, y: x >= y, "==": lambda x, y: x == y, "!=": lambda x, y: x != y}
        if op not in ops:
            raise self.refuse("supertrend_direction", "the Supertrend direction is compared with < > <= >= == !=", "", e)
        flip = {"<": ">", ">": "<", "<=": ">=", ">=": "<=", "==": "==", "!=": "!="}
        dirv, other, cmp = (left, right, op) if left.kind in ("dir", "dirchange") else (right, left, flip[op])
        test = ops[cmp]
        if other.kind == "num" and other.literal is not None:
            k = other.literal
            if dirv.kind == "dir":
                parts = [self._dir_state(dirv, s) for s in (-1, 1) if test(s, k)]
                node = self._any(parts) if len(parts) < 2 else n.Logic("ANY", parts)
            else:
                # ta.change(dir): -2 when it turns up, +2 when it turns down, 0 when it holds
                prev = V("dir", dirv.node, offset=dirv.offset + 1)
                cur = V("dir", dirv.node, offset=dirv.offset)
                parts = []
                if test(-2, k):
                    parts.append(n.Logic("ALL", [self._dir_state(prev, 1), self._dir_state(cur, -1)]))
                if test(2, k):
                    parts.append(n.Logic("ALL", [self._dir_state(prev, -1), self._dir_state(cur, 1)]))
                if test(0, k):
                    parts.append(n.Logic("ANY", [n.Logic("ALL", [self._dir_state(prev, -1), self._dir_state(cur, -1)]),
                                                 n.Logic("ALL", [self._dir_state(prev, 1), self._dir_state(cur, 1)])]))
                node = self._any(parts)
            self.construct(e.line, "Supertrend direction", "supported", "close compared with Supertrend()")
            return _bool(node, 12, 5)
        if dirv.kind == "dir" and other.kind == "dir" and cmp in ("==", "!=") and n.to_text(dirv.node) == n.to_text(other.node):
            same: List[Any] = [n.Logic("ALL", [self._dir_state(dirv, s), self._dir_state(other, s)]) for s in (-1, 1)]
            diff: List[Any] = [n.Logic("ALL", [self._dir_state(dirv, s), self._dir_state(other, -s)]) for s in (-1, 1)]
            self.construct(e.line, "Supertrend direction", "supported", "close compared with Supertrend()")
            return _bool(n.Logic("ANY", same if cmp == "==" else diff), 16, 4)
        raise self.refuse("supertrend_direction", "the Supertrend direction can only be compared with a number (dir < 0, dir > 0) "
                          "or with itself bars back (dir != dir[1])", "write dir < 0 for up, dir > 0 for down", e)

    def ternary(self, e: p.PTernary) -> V:
        c = self.need_bool(self.tx(e.cond), e.cond, "the condition before `?`")
        a, b = self.tx(e.a), self.tx(e.b)
        if a.kind == "bool" and b.kind == "bool":
            size, depth = _grow(e.line, e.col, c, c, a, b)
            self.construct(e.line, "?:", "supported", "(c AND a) OR (NOT c AND b)")
            return _bool(n.Logic("ANY", [n.Logic("ALL", [c.node, a.node]), n.Logic("ALL", [n.Not(c.node), b.node])]), size + 3, depth + 1)
        raise self.refuse("ternary", "`?:` is translated only when both results are conditions (true/false)",
                          "write two conditions instead: (cond and a) or (not cond and b), with a and b compared to a level", e, "?:")

    # --- calls ---
    def args(self, e: p.PCall, names: Sequence[str], required: int) -> List[Optional[p.PExpr]]:
        """Positional and named arguments by Pine's parameter names."""
        out: List[Optional[p.PExpr]] = [None] * len(names)
        if len(e.args) > len(names):
            raise self.refuse("arguments", f"{e.name} takes at most {len(names)} arguments", "", e)
        for i, a in enumerate(e.args):
            out[i] = a
        for key, value in e.kwargs.items():
            if key not in names:
                raise self.refuse("arguments", f"{e.name} has no argument {key!r}", f"its arguments are {', '.join(names)}", e)
            out[names.index(key)] = value
        for i in range(required):
            if out[i] is None:
                raise self.refuse("arguments", f"{e.name} needs {names[i]}", "", e)
        return out

    def call(self, e: p.PCall) -> V:  # noqa: C901 - one switch over the supported functions
        name = e.name
        if name == "request.security" or name.startswith("request.security"):
            look = e.kwargs.get("lookahead")
            if look is None and len(e.args) >= 6:
                look = e.args[5]
            if look is not None and not (isinstance(look, p.PName) and look.name == "barmerge.lookahead_off"):
                raise _Refuse("look_ahead", "request.security with lookahead reads a higher-timeframe bar before it has closed (look-ahead)",
                              "remove lookahead (the default, barmerge.lookahead_off)", e.line, e.col, look_ahead=True)
        if name in COSMETIC_CALLS or name.startswith(("color.", "label.", "line.", "box.", "table.", "str.", "log.")):
            return V("cosmetic")
        if name in UNSUPPORTED_FUNCTIONS:
            raise self.refuse("unsupported_function", f"{name}() is not in the Pine subset", UNSUPPORTED_FUNCTIONS[name], e, f"{name}()")
        if name == "input" or name.startswith("input."):
            raise self.refuse("input_inline", "an input must be declared on its own line", "write len = input.int(14) and use len", e)
        handler = _CALLS.get(name)
        if handler is None:
            alt = f"supported functions: {SUPPORTED_LIST}" if name.startswith(("ta.", "math.")) else ""
            raise self.refuse("unsupported_function", f"{name}() is not in the Pine subset", alt, e, f"{name}()")
        out = handler(self, e)
        self.construct(e.line, f"{name}()", "supported", out[1])
        return out[0]

    # handlers return (value, what it maps to)
    def _ma(self, e: p.PCall, fn: str) -> Tuple[V, str]:
        src_e, len_e = self.args(e, ("source", "length"), 2)
        src = self.need_num(self.tx(src_e), src_e, f"{e.name}'s source")       # type: ignore[arg-type]
        length = self.window(self.tx(len_e), len_e, e.name, "length")         # type: ignore[arg-type]
        size, depth = _grow(e.line, e.col, src)
        return _num(n.Call(fn, [src.node, length]), size + 1, depth), f"{fn}(source, length)"

    def ta_ema(self, e: p.PCall) -> Tuple[V, str]:
        return self._ma(e, "EMA")

    def ta_sma(self, e: p.PCall) -> Tuple[V, str]:
        return self._ma(e, "SMA")

    def ta_rsi(self, e: p.PCall) -> Tuple[V, str]:
        src_e, len_e = self.args(e, ("source", "length"), 2)
        k = self.close_source(self.tx(src_e), src_e, "ta.rsi")             # type: ignore[arg-type]
        length = self.window(self.tx(len_e), len_e, e.name, "length")      # type: ignore[arg-type]
        return _num(n.Call("RSI", [length], {}, k)), "RSI(length)"

    def ta_atr(self, e: p.PCall) -> Tuple[V, str]:
        (len_e,) = self.args(e, ("length",), 1)
        length = self.window(self.tx(len_e), len_e, e.name, "length")      # type: ignore[arg-type]
        return _num(n.Call("ATR", [length])), "ATR(length)"

    def ta_supertrend(self, e: p.PCall) -> Tuple[V, str]:
        factor_e, len_e = self.args(e, ("factor", "atrPeriod"), 2)
        factor = self.setting(self.tx(factor_e), factor_e, e.name, "factor")   # type: ignore[arg-type]
        length = self.window(self.tx(len_e), len_e, e.name, "atrPeriod")        # type: ignore[arg-type]
        line = n.Call("Supertrend", [length, factor])
        return V("tuple", items=(_num(line, 3), V("dir", line, 3))), "[Supertrend(atrPeriod, factor), direction]"

    def _cross(self, e: p.PCall, fn: str) -> Tuple[V, str]:
        a_e, b_e = self.args(e, ("source1", "source2"), 2)
        a = self.need_num(self.tx(a_e), a_e, f"{e.name}'s first value")      # type: ignore[arg-type]
        b = self.need_num(self.tx(b_e), b_e, f"{e.name}'s second value")     # type: ignore[arg-type]
        size, depth = _grow(e.line, e.col, a, b)
        if fn == "both":
            node: Any = n.Logic("ANY", [n.Call("CrossAbove", [a.node, b.node]), n.Call("CrossBelow", [a.node, b.node])])
            size, depth = _grow(e.line, e.col, a, b, a, b)
            return _bool(node, size + 2, depth + 1), "CrossAbove(a, b) OR CrossBelow(a, b)"
        return _bool(n.Call(fn, [a.node, b.node]), size, depth), f"{fn}(a, b)"

    def ta_crossover(self, e: p.PCall) -> Tuple[V, str]:
        return self._cross(e, "CrossAbove")

    def ta_crossunder(self, e: p.PCall) -> Tuple[V, str]:
        return self._cross(e, "CrossBelow")

    def ta_cross(self, e: p.PCall) -> Tuple[V, str]:
        return self._cross(e, "both")

    def _extreme(self, e: p.PCall, fn: str, default_field: str) -> Tuple[V, str]:
        if len(e.args) + len(e.kwargs) == 1 and "source" not in e.kwargs:
            (len_e,) = self.args(e, ("length",), 1)
            src = _num(n.Field(default_field))
        else:
            src_e, len_e = self.args(e, ("source", "length"), 2)
            src = self.need_num(self.tx(src_e), src_e, f"{e.name}'s source")   # type: ignore[arg-type]
        length = self.window(self.tx(len_e), len_e, e.name, "length")         # type: ignore[arg-type]
        size, depth = _grow(e.line, e.col, src)
        return _num(n.Call(fn, [src.node, length]), size + 1, depth), f"{fn}(source, length)"

    def ta_highest(self, e: p.PCall) -> Tuple[V, str]:
        return self._extreme(e, "Max", "high")

    def ta_lowest(self, e: p.PCall) -> Tuple[V, str]:
        return self._extreme(e, "Min", "low")

    def ta_change(self, e: p.PCall) -> Tuple[V, str]:
        src_e, len_e = self.args(e, ("source", "length"), 1)
        src = self.tx(src_e)                                                    # type: ignore[arg-type]
        k = 1
        length_v = self.tx(len_e) if len_e is not None else _num(n.Num(1.0), literal=1.0, is_int=True)
        if src.kind == "dir":
            if length_v.literal != 1:
                raise self.refuse("supertrend_direction", "ta.change of the Supertrend direction is read over one bar", "use ta.change(dir)", e)
            return V("dirchange", src.node, src.size, src.depth, offset=src.offset), "the Supertrend direction turning"
        src = self.need_num(src, src_e, f"{e.name}'s source")
        if length_v.literal is not None:
            if not float(length_v.literal).is_integer() or not 1 <= length_v.literal <= MAX_OFFSET:
                raise self.refuse("window", f"{e.name}'s length must be a whole number from 1 to {MAX_OFFSET}", "", e)
            k = int(length_v.literal)
            size, depth = _grow(e.line, e.col, src, src)
            return _num(n.Binary("-", src.node, shift(src.node, k)), size, depth), "x - x[length]"
        length = self.window(length_v, len_e, e.name, "length")
        size, depth = _grow(e.line, e.col, src, src)
        return _num(n.Binary("-", src.node, n.Call("Lag", [src.node, length])), size + 1, depth + 1), "x - Lag(x, length)"

    def ta_mom(self, e: p.PCall) -> Tuple[V, str]:
        if len(e.args) + len(e.kwargs) < 2:
            raise self.refuse("arguments", "ta.mom needs source and length", "", e)
        return self.ta_change(e)

    def ta_macd(self, e: p.PCall) -> Tuple[V, str]:
        src_e, f_e, s_e, g_e = self.args(e, ("source", "fastlen", "slowlen", "siglen"), 4)
        src = self.need_num(self.tx(src_e), src_e, "ta.macd's source")         # type: ignore[arg-type]
        fast = self.window(self.tx(f_e), f_e, e.name, "fastlen")                # type: ignore[arg-type]
        slow = self.window(self.tx(s_e), s_e, e.name, "slowlen")                # type: ignore[arg-type]
        sig = self.window(self.tx(g_e), g_e, e.name, "siglen")                  # type: ignore[arg-type]
        size, depth = _grow(e.line, e.col, src, src)
        macd = n.Binary("-", n.Call("EMA", [src.node, fast]), n.Call("EMA", [src.node, slow]))
        signal = n.Call("EMA", [macd, sig])
        hist = n.Binary("-", macd, signal)
        mv = _num(macd, size + 2, depth + 1)
        sv = _num(signal, size + 3, depth + 2)
        hv = _num(hist, 2 * size + 6, depth + 3)
        _grow(e.line, e.col, hv)
        return V("tuple", items=(mv, sv, hv)), "[EMA(fast) - EMA(slow), EMA of that over siglen, their difference]"

    def ta_stoch(self, e: p.PCall) -> Tuple[V, str]:
        src_e, hi_e, lo_e, len_e = self.args(e, ("source", "high", "low", "length"), 4)
        src = self.need_num(self.tx(src_e), src_e, "ta.stoch's source")        # type: ignore[arg-type]
        hi = self.need_num(self.tx(hi_e), hi_e, "ta.stoch's high")             # type: ignore[arg-type]
        lo = self.need_num(self.tx(lo_e), lo_e, "ta.stoch's low")              # type: ignore[arg-type]
        length = self.window(self.tx(len_e), len_e, e.name, "length")          # type: ignore[arg-type]
        size, depth = _grow(e.line, e.col, src, hi, lo, lo)
        lowest = n.Call("Min", [lo.node, length])
        highest = n.Call("Max", [hi.node, length])
        node = n.Binary("/", n.Binary("*", n.Num(100.0), n.Binary("-", src.node, lowest)), n.Binary("-", highest, lowest))
        return _num(node, size + 8, depth + 3), "100 * (source - Min(low, length)) / (Max(high, length) - Min(low, length))"

    def ta_bb(self, e: p.PCall) -> Tuple[V, str]:
        src_e, len_e, mult_e = self.args(e, ("series", "length", "mult"), 3)
        k = self.close_source(self.tx(src_e), src_e, "ta.bb")                  # type: ignore[arg-type]
        length = self.window(self.tx(len_e), len_e, e.name, "length")          # type: ignore[arg-type]
        mult = self.setting(self.tx(mult_e), mult_e, e.name, "mult")           # type: ignore[arg-type]
        mid, up, lo = (n.Call(fn, [length, mult], {}, k) for fn in ("BBMid", "BBUpper", "BBLower"))
        return V("tuple", items=(_num(mid, 3), _num(up, 3), _num(lo, 3))), "[BBMid(length, mult), BBUpper(...), BBLower(...)]"

    def ta_stdev(self, e: p.PCall) -> Tuple[V, str]:
        src_e, len_e, biased_e = self.args(e, ("source", "length", "biased"), 2)
        if biased_e is not None and not (isinstance(biased_e, p.PBool) and biased_e.value):
            raise self.refuse("stdev_biased", "ATP's standard deviation is the population one (biased=true)",
                              "leave biased at its default (true)", e)
        src = self.need_num(self.tx(src_e), src_e, "ta.stdev's source")        # type: ignore[arg-type]
        length = self.window(self.tx(len_e), len_e, e.name, "length")          # type: ignore[arg-type]
        size, depth = _grow(e.line, e.col, src)
        return _num(n.Call("StDev", [src.node, length]), size + 1, depth), "StDev(source, length) (needs the AMW functions flag)"

    def ta_roc(self, e: p.PCall) -> Tuple[V, str]:
        src_e, len_e = self.args(e, ("source", "length"), 2)
        src = self.need_num(self.tx(src_e), src_e, "ta.roc's source")          # type: ignore[arg-type]
        length = self.window(self.tx(len_e), len_e, e.name, "length")          # type: ignore[arg-type]
        size, depth = _grow(e.line, e.col, src)
        return _num(n.Call("PctChange", [src.node, length]), size + 1, depth), "PctChange(source, length)"

    def _trend(self, e: p.PCall, rising: bool) -> Tuple[V, str]:
        src_e, len_e = self.args(e, ("source", "length"), 2)
        src = self.need_num(self.tx(src_e), src_e, f"{e.name}'s source")       # type: ignore[arg-type]
        length = self.window(self.tx(len_e), len_e, e.name, "length")          # type: ignore[arg-type]
        size, depth = _grow(e.line, e.col, src, src)
        ref = n.Call("Max" if rising else "Min", [src.node, length], {}, 1)
        return _bool(n.Compare(">" if rising else "<", src.node, ref), size + 1, depth + 1), \
            ("source > Max(source, length)[1]" if rising else "source < Min(source, length)[1]")

    def ta_rising(self, e: p.PCall) -> Tuple[V, str]:
        return self._trend(e, True)

    def ta_falling(self, e: p.PCall) -> Tuple[V, str]:
        return self._trend(e, False)

    def ta_dmi(self, e: p.PCall) -> Tuple[V, str]:
        di_e, adx_e = self.args(e, ("diLength", "adxSmoothing"), 2)
        di = self.window(self.tx(di_e), di_e, e.name, "diLength")              # type: ignore[arg-type]
        sm = self.window(self.tx(adx_e), adx_e, e.name, "adxSmoothing")        # type: ignore[arg-type]
        if n.to_text(di) != n.to_text(sm):
            raise self.refuse("dmi", "ATP's DMI uses one length for the DI and the ADX smoothing",
                              "give ta.dmi the same length twice: ta.dmi(14, 14)", e)
        return V("tuple", items=(_num(n.Call("PlusDI", [di]), 2), _num(n.Call("MinusDI", [di]), 2), _num(n.Call("ADX", [di]), 2))), \
            "[PlusDI(length), MinusDI(length), ADX(length)]"

    def _greatest(self, e: p.PCall, fn: str) -> Tuple[V, str]:
        if e.kwargs or len(e.args) < 2:
            raise self.refuse("arguments", f"{e.name} needs two or more values", "", e)
        vals = [self.need_num(self.tx(a), a, f"{e.name}'s values") for a in e.args]
        size, depth = _grow(e.line, e.col, *vals)
        return _num(n.Call(fn, [v.node for v in vals]), size, depth), f"{fn}(a, b, ...)"

    def math_max(self, e: p.PCall) -> Tuple[V, str]:
        return self._greatest(e, "Greatest")

    def math_min(self, e: p.PCall) -> Tuple[V, str]:
        return self._greatest(e, "Least")

    # --- inputs ---
    def input_decl(self, name: str, e: p.PCall) -> Optional[V]:  # noqa: C901
        kind = e.name.split(".", 1)[1] if "." in e.name else ""
        if kind in ("color",):
            self.construct(e.line, e.name + "()", "ignored", "drawing only")
            return V("cosmetic")
        if kind in ("string", "timeframe", "symbol", "session", "text_area", "time", "enum"):
            self.construct(e.line, e.name + "()", "ignored", "")
            self.diag(e.line, e.col, "warning", "input_text", f"{e.name}() is not translated; {name!r} cannot be used in a condition",
                      "use input.int, input.float, input.bool or input.source")
            return V("str")
        names: Tuple[str, ...] = ("defval", "title", "minval", "maxval", "step", "tooltip", "inline", "group", "confirm", "display")
        if kind in ("bool", "source"):
            names = ("defval", "title", "tooltip", "inline", "group", "confirm", "display")
        if kind not in ("", "int", "float", "bool", "source", "price"):
            raise self.refuse("unsupported_function", f"{e.name}() is not in the Pine subset",
                              "use input.int, input.float, input.bool or input.source", e, e.name + "()")
        positional = list(e.args)
        values: Dict[str, Optional[p.PExpr]] = {k: None for k in names}
        for i, a in enumerate(positional[: len(names)]):
            values[names[i]] = a
        options: Tuple[Any, ...] = ()
        for key, arg in e.kwargs.items():
            if key == "options" and isinstance(arg, p.PList):
                options = tuple(_literal(x) for x in arg.items)
                continue
            values[key] = arg
        default_e = values.get("defval")
        if default_e is None:
            raise self.refuse("input", f"{e.name}() needs a default value", "write input.int(14)", e)
        title_e = values.get("title")
        title = title_e.value[:80] if isinstance(title_e, p.PStr) else ""
        if kind == "source" or (kind == "" and isinstance(default_e, p.PName)):
            src = self.tx(default_e)
            src = self.need_num(src, default_e, "the source input's default")
            self.construct(e.line, e.name + "()", "supported", f"fixed at its default ({n.to_text(src.node)})")
            self.diag(e.line, e.col, "info", "input_source", f"source input {name!r} is fixed at its default ({n.to_text(src.node)})",
                      "change the default in the script to use another source")
            return src
        default = _literal(default_e)
        if isinstance(default, bool) or kind == "bool":
            if not isinstance(default, bool):
                raise self.refuse("input", "input.bool needs true or false", "", e)
            self.params[name] = ParamSpec(name, "bool", default, title=title, line=e.line)
            self.construct(e.line, e.name + "()", "supported", f"${name}")
            return V("bool", n.Param(name), param=name)
        if not isinstance(default, (int, float)):
            raise self.refuse("input", f"{e.name}()'s default must be a number", "write a number, e.g. input.int(14)", e)
        is_int = kind == "int" or (kind == "" and isinstance(default, int))
        if is_int and not float(default).is_integer():
            raise self.refuse("input", "input.int's default must be a whole number", "", e)
        lo, hi = _literal(values.get("minval")), _literal(values.get("maxval"))
        lo_f = float(lo) if isinstance(lo, (int, float)) and not isinstance(lo, bool) else None
        hi_f = float(hi) if isinstance(hi, (int, float)) and not isinstance(hi, bool) else None
        if lo_f is not None and hi_f is not None and lo_f > hi_f:
            raise self.refuse("input", "minval is above maxval", "", e)
        if (lo_f is not None and default < lo_f) or (hi_f is not None and default > hi_f):
            raise self.refuse("input", "the default is outside minval..maxval", "", e)
        value: Any = int(default) if is_int else float(default)
        self.params[name] = ParamSpec(name, "int" if is_int else "float", value, lo_f, hi_f, title, e.line, options)
        self.construct(e.line, e.name + "()", "supported", f"${name}")
        return V("num", n.Param(name), param=name, is_int=is_int)

    # --- statements ---
    def run(self, program: p.Program, lookahead: List[Tuple[str, int, int]]) -> None:
        for code_msg, line, col in lookahead:
            self.diag(line, col, "error", "look_ahead", code_msg, "history references count bars back (x[1]); leave lookahead at its default")
        for stmt in program.statements:
            self.statement(stmt, [], None)

    def _fail(self, exc: Exception, names: Sequence[str] = ()) -> None:
        if isinstance(exc, _Refuse):
            self.diag(exc.line, exc.col, "error", exc.code, exc.message, exc.alternative)
            line = exc.line
        elif isinstance(exc, _Depends):
            return self._mark(names, exc.line)
        else:
            raise exc
        self._mark(names, line)

    def _mark(self, names: Sequence[str], line: int) -> None:
        for name in names:
            if name != "_":
                self.bad[name] = line
                self.env.pop(name, None)

    def statement(self, stmt: p.Stmt, conds: List[Tuple[V, int]], failed: Optional[Tuple[str, int]]) -> None:  # noqa: C901
        if isinstance(stmt, p.SUnsupported):
            self.diag(stmt.line, stmt.col, "error", stmt.code, stmt.message, stmt.alternative)
            self.construct(stmt.line, stmt.code, "unsupported")
            return
        if isinstance(stmt, p.SAssign):
            if conds:
                self.diag(stmt.line, stmt.col, "error", "local_declaration", "a declaration inside an `if` block is not in the Pine subset",
                          "declare the value above the `if`, at the top level")
                return
            for name in stmt.names:
                if name != "_" and name in self.declared:
                    self.diag(stmt.line, stmt.col, "error", "redeclared", f"{name!r} is already declared on line {self.declared[name]}",
                              "give the new value its own name")
                    self._mark(stmt.names, stmt.line)
                    return
            for name in stmt.names:
                self.declared.setdefault(name, stmt.line)
            try:
                if not stmt.is_tuple and isinstance(stmt.expr, p.PCall) and (stmt.expr.name == "input" or stmt.expr.name.startswith("input.")):
                    value = self.input_decl(stmt.names[0], stmt.expr)
                    if value is not None:
                        self.env[stmt.names[0]] = value
                    return
                value = self.tx(stmt.expr)
            except (_Refuse, _Depends) as exc:
                self._fail(exc, stmt.names)
                return
            if stmt.is_tuple:
                if value.kind != "tuple":
                    self.diag(stmt.line, stmt.col, "error", "tuple", "only a function that returns several values can fill [a, b, ...]",
                              "ta.macd, ta.bb, ta.supertrend and ta.dmi return several values")
                    self._mark(stmt.names, stmt.line)
                    return
                if len(value.items) != len(stmt.names):
                    self.diag(stmt.line, stmt.col, "error", "tuple", f"the function returns {len(value.items)} values, the left side names {len(stmt.names)}",
                              f"name {len(value.items)} values (use _ for one you do not need)")
                    self._mark(stmt.names, stmt.line)
                    return
                for name, item in zip(stmt.names, value.items):
                    if name != "_":
                        self.env[name] = item
                return
            if value.kind == "tuple":
                self.diag(stmt.line, stmt.col, "error", "tuple", "this function returns several values", "declare them as [a, b, ...] = ...")
                self._mark(stmt.names, stmt.line)
                return
            self.env[stmt.names[0]] = value
            return
        if isinstance(stmt, p.SIf):
            cond = V("bool", n.Bool(True))
            if failed is None:
                try:
                    cond = self.need_bool(self.tx(stmt.cond), stmt.cond, "the `if` condition")
                    self.construct(stmt.line, "if", "supported", "the signal condition")
                except _Refuse as exc:
                    self.diag(exc.line, exc.col, "error", exc.code, exc.message, exc.alternative)
                    failed = ("the `if` condition", exc.line)
                except _Depends as exc:
                    failed = (repr(exc.name), exc.line)
            inner = conds + [(cond, stmt.line)]
            for s in stmt.body:
                self.statement(s, inner, failed)
            return
        if isinstance(stmt, p.SExpr):
            self.signal_call(stmt, conds, failed)

    def signal_call(self, stmt: p.SExpr, conds: List[Tuple[V, int]], failed: Optional[Tuple[str, int]]) -> None:  # noqa: C901
        e = stmt.expr
        if not isinstance(e, p.PCall):
            self.diag(stmt.line, stmt.col, "error", "statement", "this line does nothing in the Pine subset",
                      "declare it (name = ...) or use it in alertcondition / plotshape / an `if` block")
            return
        name = e.name
        if name in ("indicator", "strategy", "study"):
            self.header(e, conds)
            return
        if name in ("library",):
            self.diag(e.line, e.col, "error", "library", "a library is not a signal script", "use indicator() or strategy()")
            return
        try:
            if name == "alertcondition":
                cond_e = e.args[0] if e.args else e.kwargs.get("condition")
                title_e = e.args[1] if len(e.args) > 1 else e.kwargs.get("title")
                self.construct(e.line, "alertcondition()", "supported", "an alert signal")
                self.add_signal("alert", _text(title_e) or f"alertcondition on line {e.line}", e, cond_e, conds, failed)
                return
            if name in ("plotshape", "plotchar"):
                cond_e = e.args[0] if e.args else e.kwargs.get("series")
                title_e = e.args[1] if len(e.args) > 1 else e.kwargs.get("title")
                if cond_e is None:
                    raise self.refuse("arguments", f"{name} needs a series", "", e)
                try:
                    probe = self.tx(cond_e)
                except (_Refuse, _Depends):
                    probe = V("bool")
                if probe.kind != "bool":
                    self.construct(e.line, f"{name}()", "ignored", "drawing only (its series is a number)")
                    return
                self.construct(e.line, f"{name}()", "supported", "a marker signal")
                self.add_signal("marker", _text(title_e) or f"{name} on line {e.line}", e, cond_e, conds, failed)
                return
            if name == "strategy.entry":
                id_e = e.args[0] if e.args else e.kwargs.get("id")
                dir_e = e.args[1] if len(e.args) > 1 else e.kwargs.get("direction")
                side = self.tx(dir_e).text if dir_e is not None else ""
                if side not in ("long", "short"):
                    raise self.refuse("direction", "strategy.entry needs strategy.long or strategy.short", "", e)
                self.order_kwargs(e, skip={"id", "direction", "when"}, positional_from=2)
                self.construct(e.line, "strategy.entry()", "supported", f"a {side}-side signal")
                label = f"{side.capitalize()}-side condition ({_text(id_e) or 'entry'})"
                self.add_signal(f"{side}_entry", label, e, e.kwargs.get("when"), conds, failed)
                return
            if name in ("strategy.close", "strategy.close_all"):
                id_e = (e.args[0] if e.args else e.kwargs.get("id")) if name == "strategy.close" else None
                self.order_kwargs(e, skip={"id", "when"}, positional_from=1 if name == "strategy.close" else 0)
                self.construct(e.line, f"{name}()", "supported", "an exit signal")
                label = f"Exit condition ({_text(id_e) or 'all'})"
                self.add_signal("exit", label, e, e.kwargs.get("when"), conds, failed)
                return
            if name == "alert":
                if not conds:
                    raise self.refuse("alert", "alert() runs on every bar unless it sits inside `if cond`",
                                      "put it inside an `if` block, or use alertcondition(cond, title)", e)
                self.construct(e.line, "alert()", "supported", "an alert signal")
                self.add_signal("alert", f"alert on line {e.line}", e, None, conds, failed)
                return
            if name in IGNORED_ORDER_CALLS:
                self.construct(e.line, f"{name}()", "ignored", "your own exit and risk settings")
                self.diag(e.line, e.col, "warning", "order_ignored", f"{name}() is ignored: stops, targets and risk limits come from your own "
                          "deployment settings", "")
                return
            if name in COSMETIC_CALLS:
                self.construct(e.line, f"{name}()", "ignored", "drawing only")
                return
            self.tx(e)          # an unsupported or unknown call reports itself
            self.diag(e.line, e.col, "error", "statement", f"{name}() on its own line does nothing in the Pine subset",
                      "declare it (name = ...) or use it in alertcondition / plotshape / an `if` block")
        except (_Refuse, _Depends) as exc:
            self._fail(exc)

    def order_kwargs(self, e: p.PCall, skip: set, positional_from: int) -> None:
        ignored = [k for k in e.kwargs if k not in skip]
        if len(e.args) > positional_from:
            ignored.append("positional settings")
        sizing = [k for k in ignored if k in SIZING_KWARGS or k == "positional settings"]
        prices = [k for k in ignored if k in ORDER_PRICE_KWARGS]
        if sizing:
            self.diag(e.line, e.col, "warning", "sizing_ignored", f"order size ({', '.join(sizing)}) is ignored: lots and quantity come from "
                      "your own deployment settings", "")
        if prices:
            self.diag(e.line, e.col, "warning", "order_ignored", f"order settings ({', '.join(prices)}) are ignored: ATP generates the "
                      "signal; entries and exits follow your own settings", "")

    def header(self, e: p.PCall, conds: List[Tuple[V, int]]) -> None:
        if conds or self.script_kind is not None:
            self.diag(e.line, e.col, "warning", "header", "a second header is ignored", "keep one indicator() or strategy() line")
            return
        self.script_kind = "strategy" if e.name == "strategy" else "indicator"
        self.header_line = e.line
        title_e = e.args[0] if e.args else e.kwargs.get("title")
        self.title = (_text(title_e) or "")[:120]
        self.construct(e.line, f"{e.name}()", "supported", "the script's name (metadata only)")
        sizing = sorted(k for k in e.kwargs if k in SIZING_KWARGS)
        if sizing:
            self.diag(e.line, e.col, "warning", "sizing_ignored", f"strategy settings ({', '.join(sizing)}) are ignored: lots, capital "
                      "and costs come from your own settings and ATP's cost model", "")
        tick = e.kwargs.get("calc_on_every_tick")
        if isinstance(tick, p.PBool) and tick.value:
            self.diag(e.line, e.col, "warning", "closed_bars", "calc_on_every_tick is ignored: ATP evaluates closed bars only", "")

    def add_signal(self, kind: str, label: str, e: p.PCall, cond_e: Optional[p.PExpr], conds: List[Tuple[V, int]],
                   failed: Optional[Tuple[str, int]]) -> None:
        if len(self.raw_signals) >= MAX_SIGNALS:
            self.diag(e.line, e.col, "warning", "limit", f"only the first {MAX_SIGNALS} signals are translated", "")
            return
        if failed is not None:
            self.raw_signals.append((kind, label[:120], e.line, e.col, None, failed))
            return
        parts = [c for c, _ in conds]
        try:
            if cond_e is not None:
                parts.append(self.need_bool(self.tx(cond_e), cond_e, "the signal condition"))
        except _Refuse as exc:
            self.diag(exc.line, exc.col, "error", exc.code, exc.message, exc.alternative)
            self.raw_signals.append((kind, label[:120], e.line, e.col, None, ("the condition", exc.line)))
            return
        except _Depends as exc:
            self.raw_signals.append((kind, label[:120], e.line, e.col, None, (repr(exc.name), exc.line)))
            return
        if not parts:
            self.diag(e.line, e.col, "error", "no_condition", "this signal has no condition, so it would fire on every bar",
                      "put it inside `if cond`, or give it when=cond")
            self.raw_signals.append((kind, label[:120], e.line, e.col, None, ("the condition", e.line)))
            return
        items: List[Any] = []
        for v in parts:
            if isinstance(v.node, n.Logic) and v.node.op == "ALL":
                items.extend(v.node.items)
            elif not (isinstance(v.node, n.Bool) and v.node.value and len(parts) > 1):
                items.append(v.node)
        node = items[0] if len(items) == 1 else n.Logic("ALL", items)
        size, depth = _grow(e.line, e.col, *parts)
        self.raw_signals.append((kind, label[:120], e.line, e.col, V("bool", node, size, depth), None))


def _literal(e: Optional[p.PExpr]) -> Any:
    if e is None:
        return None
    if isinstance(e, p.PNum):
        return int(e.value) if e.is_int and abs(e.value) < 1e15 else e.value
    if isinstance(e, p.PUnary) and e.op == "-" and isinstance(e.operand, p.PNum):
        v = _literal(e.operand)
        return -v
    if isinstance(e, p.PBool):
        return e.value
    if isinstance(e, p.PStr):
        return e.value[:80]
    return None


def _text(e: Optional[p.PExpr]) -> str:
    return e.value.strip()[:120] if isinstance(e, p.PStr) else ""


_CALLS: Dict[str, Callable[[_Translator, p.PCall], Tuple[V, str]]] = {
    "ta.ema": _Translator.ta_ema, "ta.sma": _Translator.ta_sma, "ta.rsi": _Translator.ta_rsi, "ta.atr": _Translator.ta_atr,
    "ta.supertrend": _Translator.ta_supertrend, "ta.crossover": _Translator.ta_crossover,
    "ta.crossunder": _Translator.ta_crossunder, "ta.cross": _Translator.ta_cross, "ta.highest": _Translator.ta_highest,
    "ta.lowest": _Translator.ta_lowest, "ta.change": _Translator.ta_change, "ta.mom": _Translator.ta_mom,
    "ta.macd": _Translator.ta_macd, "ta.stoch": _Translator.ta_stoch, "ta.bb": _Translator.ta_bb,
    "ta.stdev": _Translator.ta_stdev, "ta.roc": _Translator.ta_roc, "ta.rising": _Translator.ta_rising,
    "ta.falling": _Translator.ta_falling, "ta.dmi": _Translator.ta_dmi, "math.max": _Translator.math_max,
    "math.min": _Translator.math_min,
}


def _lookahead_tokens(tokens: List[Tok]) -> List[Tuple[str, int, int]]:
    """Look-ahead found anywhere in the text, including lines the subset does not translate: `barmerge.lookahead_on`,
    a negative history offset `x[-1]` and a negative `offset=`."""
    out: List[Tuple[str, int, int]] = []
    for i, t in enumerate(tokens):
        if t.kind == "name" and t.text == "barmerge.lookahead_on":
            out.append(("barmerge.lookahead_on reads a higher-timeframe bar before it has closed (look-ahead)", t.line, t.col))
        if t.kind == "op" and t.text == "[" and i > 0 and i + 2 < len(tokens):
            before = tokens[i - 1]
            indexes = before.kind == "name" or (before.kind == "op" and before.text in (")", "]"))
            if indexes and tokens[i + 1].kind == "op" and tokens[i + 1].text == "-" and tokens[i + 2].kind in ("num", "name"):
                out.append(("a negative history offset reads a future bar (look-ahead)", t.line, t.col))
        if t.kind == "name" and t.text == "offset" and i + 2 < len(tokens) and tokens[i + 1].text == "=" and tokens[i + 2].text == "-":
            out.append(("a negative offset shows a value on a bar before it was known (look-ahead)", t.line, t.col))
    return out


def translate(text: str, *, base_tf: str = "1d") -> Translation:
    """Pine subset text -> Translation. Never raises for any input text (fuzz-tested): a problem is a diagnostic."""
    try:
        program = p.parse(text)
        from app.lab.pine.tokens import tokenize
        tokens = tokenize(text).tokens
    except PineSyntaxError as exc:
        diag = Diagnostic(exc.line, exc.col, "error", exc.code, exc.message,
                          "shorten the script" if exc.code == "limit" else "check the line against the supported subset")
        return Translation(False, False, False, None, None, "", [], {}, [diag], [])
    except RecursionError:
        diag = Diagnostic(1, 1, "error", "limit", "the script is nested too deeply", "simplify the expressions")
        return Translation(False, False, False, None, None, "", [], {}, [diag], [])
    t = _Translator(base_tf)
    lookahead = _lookahead_tokens(tokens)
    if program.version is None:
        t.diag(1, 1, "error", "version", "the script has no //@version line", "add //@version=5 as the first line")
    elif program.version not in SUPPORTED_VERSIONS:
        t.diag(program.version_line, 1, "error", "version", f"Pine v{program.version[:8]} is not supported; the subset is Pine v5",
               "convert the script to v5 in TradingView's Pine Editor, then paste it again")
    elif program.version == "6":
        t.diag(program.version_line, 1, "info", "version", "Pine v6 is read as the v5 subset", "")
    try:
        t.run(program, lookahead)
    except RecursionError:
        t.diag(1, 1, "error", "limit", "the script is nested too deeply", "simplify the expressions")
    if t.script_kind is None:
        t.diag(1, 1, "warning", "header", "no indicator() or strategy() line was found", 'add indicator("My signal") after //@version=5')
    refused = any(d.code == "look_ahead" for d in t.diags)
    signals = [] if refused else _finish(t, base_tf)
    if refused:
        t.diag(1, 1, "error", "refused", "the script reads future bars, so no signal is translated",
               "remove every look-ahead the lines above point to")
    if not refused and not t.raw_signals:
        t.diag(1, 1, "error", "no_signal", "the script states no signal condition",
               "add alertcondition(cond, \"title\"), plotshape(cond) or `if cond` with strategy.entry(...)")
    ok = (not refused) and any(s.ok for s in signals) and (program.version in SUPPORTED_VERSIONS)
    complete = ok and not any(d.severity == "error" for d in t.diags) and all(s.ok for s in signals)
    return Translation(ok, complete, refused, program.version, t.script_kind, t.title, signals, t.params, t.diags, t.constructs)


def _finish(t: _Translator, base_tf: str) -> List[Signal]:
    from app.lab.pine.words import plain_words
    out: List[Signal] = []
    for kind, label, line, col, cond, failed in t.raw_signals:
        if cond is None:
            what, where = failed or ("the condition", line)
            out.append(Signal(kind, label, line, False, problems=[f"not translated: {what} on line {where} is outside the subset"]))
            continue
        text = n.to_text(cond.node)
        used = sorted({x.name for x in n.walk(cond.node) if isinstance(x, n.Param)})
        params = {k: t.params[k].default for k in used if k in t.params}
        ast, validated = compile_screen(text, base_tf=base_tf, params=params)
        if ast is None or not validated.ok:
            problems = [p_.message for p_ in validated.problems][:10]
            for msg in problems:
                t.diag(line, col, "error", "screenql", f"the translated condition is refused: {msg}", "")
            out.append(Signal(kind, label, line, False, text, params, "", problems))
            continue
        canonical = n.to_text(ast)
        out.append(Signal(kind, label, line, True, canonical, params, plain_words(ast, t.params)))
    return out


__all__ = ["translate", "Translation", "Signal", "Diagnostic", "Construct", "ParamSpec", "FLAG", "shift", "MAX_NODES"]
