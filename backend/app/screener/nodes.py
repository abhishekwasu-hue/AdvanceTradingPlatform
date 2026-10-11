"""S1a (ADR-0021): the ScreenQL AST - one JSON-serialisable tree that the query text, the visual builder and the
Copilot all produce. `to_text` prints the canonical text; `parse(to_text(ast)) == ast` is tested (round trip).

Nodes carry `pos` (the character offset in the source text) for error messages; it is not part of equality or of the
JSON form, so a builder-made tree and a parsed one compare equal.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

VERSION = "screenql/1"
TIMEFRAMES = ("1m", "3m", "5m", "15m", "30m", "1h", "1d", "1w", "1M")
TF_MINUTES = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "1d": 375, "1w": 375 * 5, "1M": 375 * 21}
COMPARE_OPS = (">", ">=", "<", "<=", "==", "!=")
ARITH_OPS = ("+", "-", "*", "/")


def _pos() -> Any:
    return field(default=None, compare=False, repr=False)


@dataclass
class Num:
    value: float
    pos: Optional[int] = _pos()


@dataclass
class Str:
    value: str
    pos: Optional[int] = _pos()


@dataclass
class Bool:
    value: bool
    pos: Optional[int] = _pos()


@dataclass
class Param:
    name: str
    pos: Optional[int] = _pos()


@dataclass
class Field:
    name: str
    offset: int = 0                 # bars back: [0] the current (last closed) bar, [1] the one before...
    tf: Optional[str] = None        # None = the screen's base timeframe
    pos: Optional[int] = _pos()


@dataclass
class Call:
    name: str
    args: List["Node"] = field(default_factory=list)
    kwargs: Dict[str, "Node"] = field(default_factory=dict)
    offset: int = 0
    tf: Optional[str] = None
    pos: Optional[int] = _pos()


@dataclass
class Unary:
    op: str                          # "-"
    operand: "Node"
    pos: Optional[int] = _pos()


@dataclass
class Binary:
    op: str                          # + - * /
    left: "Node"
    right: "Node"
    pos: Optional[int] = _pos()


@dataclass
class Compare:
    op: str                          # > >= < <= == !=
    left: "Node"
    right: "Node"
    pos: Optional[int] = _pos()


@dataclass
class Between:
    value: "Node"
    low: "Node"
    high: "Node"
    pos: Optional[int] = _pos()


@dataclass
class In:
    value: "Node"
    items: List["Node"]
    pos: Optional[int] = _pos()


@dataclass
class Logic:
    op: str                          # ALL / ANY (AND / OR in the text)
    items: List["Node"]
    pos: Optional[int] = _pos()


@dataclass
class Not:
    item: "Node"
    pos: Optional[int] = _pos()


Node = Union[Num, Str, Bool, Param, Field, Call, Unary, Binary, Compare, Between, In, Logic, Not]
_TYPES = {cls.__name__: cls for cls in (Num, Str, Bool, Param, Field, Call, Unary, Binary, Compare, Between, In, Logic, Not)}


# --- JSON form (the builder's wire format) -----------------------------------------------------------------------

def to_json(node: Node) -> Dict[str, Any]:
    kind = type(node).__name__
    out: Dict[str, Any] = {"k": kind}
    if isinstance(node, (Num, Str, Bool)):
        out["v"] = node.value
    elif isinstance(node, Param):
        out["name"] = node.name
    elif isinstance(node, Field):
        out.update(name=node.name, offset=node.offset, tf=node.tf)
    elif isinstance(node, Call):
        out.update(name=node.name, args=[to_json(a) for a in node.args], kwargs={k: to_json(v) for k, v in node.kwargs.items()},
                   offset=node.offset, tf=node.tf)
    elif isinstance(node, Unary):
        out.update(op=node.op, operand=to_json(node.operand))
    elif isinstance(node, (Binary, Compare)):
        out.update(op=node.op, left=to_json(node.left), right=to_json(node.right))
    elif isinstance(node, Between):
        out.update(value=to_json(node.value), low=to_json(node.low), high=to_json(node.high))
    elif isinstance(node, In):
        out.update(value=to_json(node.value), items=[to_json(i) for i in node.items])
    elif isinstance(node, Logic):
        out.update(op=node.op, items=[to_json(i) for i in node.items])
    elif isinstance(node, Not):
        out["item"] = to_json(node.item)
    return out


def from_json(data: Dict[str, Any]) -> Node:
    """The builder's tree -> nodes. Unknown kinds or shapes raise ValueError (never executed, only validated after)."""
    if not isinstance(data, dict) or data.get("k") not in _TYPES:
        raise ValueError(f"not a ScreenQL node: {str(data)[:80]}")
    k = data["k"]
    try:
        if k == "Num":
            if isinstance(data["v"], bool) or not isinstance(data["v"], (int, float)):
                raise ValueError("Num needs a number")
            return Num(float(data["v"]))
        if k == "Str":
            return Str(str(data["v"]))
        if k == "Bool":
            return Bool(bool(data["v"]))
        if k == "Param":
            return Param(str(data["name"]))
        if k == "Field":
            return Field(str(data["name"]), int(data.get("offset", 0)), data.get("tf"))
        if k == "Call":
            return Call(str(data["name"]), [from_json(a) for a in data.get("args", [])],
                        {str(kk): from_json(v) for kk, v in (data.get("kwargs") or {}).items()}, int(data.get("offset", 0)), data.get("tf"))
        if k == "Unary":
            return Unary(str(data["op"]), from_json(data["operand"]))
        if k == "Binary":
            return Binary(str(data["op"]), from_json(data["left"]), from_json(data["right"]))
        if k == "Compare":
            return Compare(str(data["op"]), from_json(data["left"]), from_json(data["right"]))
        if k == "Between":
            return Between(from_json(data["value"]), from_json(data["low"]), from_json(data["high"]))
        if k == "In":
            return In(from_json(data["value"]), [from_json(i) for i in data["items"]])
        if k == "Logic":
            return Logic(str(data["op"]), [from_json(i) for i in data["items"]])
        return Not(from_json(data["item"]))
    except (KeyError, TypeError) as exc:
        raise ValueError(f"malformed {k} node: {exc}") from exc


# --- canonical text ------------------------------------------------------------------------------------------------

_PREC = {"OR": 1, "AND": 2, "NOT": 3, "CMP": 4, "+": 5, "-": 5, "*": 6, "/": 6, "NEG": 7, "ATOM": 8}


def _num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() and abs(v) < 1e15 else repr(float(v))


def _suffix(offset: int, tf: Optional[str]) -> str:
    return (f"[{offset}]" if offset else "") + (f"@{tf}" if tf else "")


def _prec(node: Node) -> int:
    if isinstance(node, Logic):
        return _PREC["OR"] if node.op == "ANY" else _PREC["AND"]
    if isinstance(node, Not):
        return _PREC["NOT"]
    if isinstance(node, (Compare, Between, In)):
        return _PREC["CMP"]
    if isinstance(node, Binary):
        return _PREC[node.op]
    if isinstance(node, Unary):
        return _PREC["NEG"]
    return _PREC["ATOM"]


def _wrap(node: Node, minimum: int) -> str:
    text = to_text(node)
    return f"({text})" if _prec(node) < minimum else text


def to_text(node: Node) -> str:
    if isinstance(node, Num):
        return _num(node.value)
    if isinstance(node, Str):
        return '"' + node.value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(node, Bool):
        return "TRUE" if node.value else "FALSE"
    if isinstance(node, Param):
        return f"${node.name}"
    if isinstance(node, Field):
        return node.name + _suffix(node.offset, node.tf)
    if isinstance(node, Call):
        parts = [to_text(a) for a in node.args] + [f"{k}={to_text(v)}" for k, v in node.kwargs.items()]
        return f"{node.name}({', '.join(parts)})" + _suffix(node.offset, node.tf)
    if isinstance(node, Unary):
        if isinstance(node.operand, Num):                                     # "-5" parses as the literal -5
            return f"-({to_text(node.operand)})"
        return "-" + _wrap(node.operand, _PREC["NEG"])
    if isinstance(node, Binary):
        p = _PREC[node.op]
        return f"{_wrap(node.left, p)} {node.op} {_wrap(node.right, p + 1)}"           # left-associative
    if isinstance(node, Compare):
        return f"{_wrap(node.left, _PREC['+'])} {node.op} {_wrap(node.right, _PREC['+'])}"
    if isinstance(node, Between):
        return f"{_wrap(node.value, _PREC['+'])} BETWEEN {_wrap(node.low, _PREC['+'])} AND {_wrap(node.high, _PREC['+'])}"
    if isinstance(node, In):
        return f"{_wrap(node.value, _PREC['+'])} IN ({', '.join(to_text(i) for i in node.items)})"
    if isinstance(node, Logic):
        p = _prec(node)
        word = " OR " if node.op == "ANY" else " AND "
        return word.join(_wrap(i, p + 1) for i in node.items)
    if isinstance(node, Not):
        return "NOT " + _wrap(node.item, _PREC["NOT"])
    raise TypeError(f"not a ScreenQL node: {node!r}")


def walk(node: Node):
    """Every node, depth first (the node itself first)."""
    yield node
    children: Tuple[Any, ...] = ()
    if isinstance(node, Call):
        children = (*node.args, *node.kwargs.values())
    elif isinstance(node, Unary):
        children = (node.operand,)
    elif isinstance(node, (Binary, Compare)):
        children = (node.left, node.right)
    elif isinstance(node, Between):
        children = (node.value, node.low, node.high)
    elif isinstance(node, In):
        children = (node.value, *node.items)
    elif isinstance(node, Logic):
        children = tuple(node.items)
    elif isinstance(node, Not):
        children = (node.item,)
    for child in children:
        yield from walk(child)
