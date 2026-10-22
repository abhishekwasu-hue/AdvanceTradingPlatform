"""The translated condition in plain words, for the Pine panel ("what this condition checks").

Indicator and structure words only (CLAUDE.md §3): no buy / sell / target wording, no advice. Built from the ScreenQL
nodes, so it reads exactly what will run.
"""
from __future__ import annotations

from typing import Any, Dict, List

from app.screener import nodes as n

_CMP = {">": "is above", ">=": "is at or above", "<": "is below", "<=": "is at or below", "==": "equals", "!=": "differs from"}
_NAMES = {"EMA": "EMA", "SMA": "SMA", "StDev": "standard deviation", "PctChange": "% change"}
MAX_WORDS = 1_200


def _ago(offset: int) -> str:
    if not offset:
        return ""
    return " 1 bar ago" if offset == 1 else f" {offset} bars ago"


def _number(v: float) -> str:
    return str(int(v)) if float(v).is_integer() and abs(v) < 1e15 else f"{v:g}"


class _Words:
    def __init__(self, params: Dict[str, Any]) -> None:
        self.params = params

    def param(self, name: str) -> str:
        spec = self.params.get(name)
        if spec is None:
            return name
        title = getattr(spec, "title", "") or name
        default = getattr(spec, "default", None)
        if isinstance(default, bool):
            return f"the '{title}' setting (now {'on' if default else 'off'})"
        # A number reads as its current value; the panel lists each input by its title on its own line.
        return _number(float(default)) if isinstance(default, (int, float)) else title

    def arg(self, node: Any) -> str:
        if isinstance(node, n.Num):
            return _number(node.value)
        if isinstance(node, n.Param):
            return self.param(node.name)
        return self.value(node)

    def value(self, node: Any) -> str:  # noqa: C901 - one switch over the node kinds
        if isinstance(node, n.Num):
            return _number(node.value)
        if isinstance(node, n.Param):
            return self.param(node.name)
        if isinstance(node, n.Bool):
            return "always" if node.value else "never"
        if isinstance(node, n.Field):
            return f"the {node.name}{_ago(node.offset)}"
        if isinstance(node, n.Unary):
            return f"minus {self.value(node.operand)}"
        if isinstance(node, n.Binary):
            words = {"+": "plus", "-": "minus", "*": "times", "/": "divided by"}[node.op]
            return f"({self.value(node.left)} {words} {self.value(node.right)})"
        if isinstance(node, n.Call):
            return self.call(node) + _ago(node.offset)
        return n.to_text(node)

    def call(self, node: n.Call) -> str:  # noqa: C901
        a = node.args
        name = node.name
        if name in ("EMA", "SMA", "StDev", "PctChange") and len(a) == 2:
            return f"the {_NAMES[name]} of {self.value(a[0])} over {self.arg(a[1])} bars"
        if name in ("RSI", "ATR", "ADX", "PlusDI", "MinusDI") and len(a) == 1:
            label = {"PlusDI": "+DI", "MinusDI": "-DI"}.get(name, name)
            return f"{label} over {self.arg(a[0])} bars"
        if name == "Supertrend" and len(a) == 2:
            return f"the Supertrend line (ATR over {self.arg(a[0])} bars, factor {self.arg(a[1])})"
        if name in ("BBUpper", "BBMid", "BBLower") and len(a) == 2:
            band = {"BBUpper": "upper", "BBMid": "middle", "BBLower": "lower"}[name]
            return f"the {band} Bollinger band (over {self.arg(a[0])} bars, width {self.arg(a[1])} deviations)"
        if name in ("Max", "Min") and len(a) == 2:
            return f"the {'highest' if name == 'Max' else 'lowest'} {self.value(a[0])} of the last {self.arg(a[1])} bars"
        if name == "Lag" and len(a) == 2:
            return f"{self.value(a[0])}, {self.arg(a[1])} bars ago"
        if name in ("Greatest", "Least"):
            return f"the {'larger' if name == 'Greatest' else 'smaller'} of {', '.join(self.value(x) for x in a)}"
        if name in ("CrossAbove", "CrossBelow") and len(a) == 2:
            return f"{self.value(a[0])} crosses {'above' if name == 'CrossAbove' else 'below'} {self.value(a[1])}"
        return n.to_text(node)

    def cond(self, node: Any, top: bool = False) -> str:
        if isinstance(node, n.Logic):
            parts = [self.cond(i) for i in node.items]
            if top and node.op == "ALL":
                return "all of these hold: " + "; ".join(parts)
            joined = (" and " if node.op == "ALL" else " or ").join(parts)
            return joined if top else f"({joined})"
        if isinstance(node, n.Not):
            return f"not ({self.cond(node.item, True)})"
        if isinstance(node, n.Compare):
            return f"{self.value(node.left)} {_CMP[node.op]} {self.value(node.right)}"
        if isinstance(node, n.Call) and node.name in ("CrossAbove", "CrossBelow"):
            return self.call(node) + (f" ({_ago(node.offset).strip()})" if node.offset else "")
        if isinstance(node, n.Param):
            return f"{self.param(node.name)} is on"
        return self.value(node)


def plain_words(ast: Any, params: Dict[str, Any]) -> str:
    """One sentence (cut at MAX_WORDS characters) saying what the condition checks on each closed bar."""
    text = _Words(params).cond(ast, True)
    if len(text) > MAX_WORDS:
        text = text[: MAX_WORDS - 3].rstrip() + "..."
    return text[0].upper() + text[1:] if text else text


__all__: List[str] = ["plain_words"]
