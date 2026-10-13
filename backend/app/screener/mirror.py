"""U9 TX2 (docs/specs/ATP_SCREENER_TAXONOMY_ADDENDUM.md §3 "mirror screens"): a long screen's short / breakdown mirror,
generated from its AST so nobody maintains two definitions.

The mirror is defined, not guessed: `mirror(S)` holds on a symbol's bars exactly when `S` holds on the same bars
reflected through a price level C (every price p becomes C - p: highs and lows swap, a rise becomes a fall, volume
and time are unchanged). The breakout screens already define their down side this way (SCREENER.md U6 BK1).
`tests/test_tx2_library.py` checks every rule below on synthetic bars, bar by bar, against the original screen on
the reflected bars (prices on whole ticks, several seeds).

"Exactly" has one warm-up: the swing functions (SwingHigh / SwingLow / SwingDirection / SwingZone*, and the screens
built on swings) agree from the third confirmed swing of the bars on (`SWING_WARMUP`). Before it, the zigzag's opening
swing can differ on reflected bars: it starts out testing a high before a low, so when both could confirm on the same
early bar the two sides open with different swings (seen on bars 13-32 of the test bars in 6 of 40 seeds). Equal highs
or lows also only mirror when the reflected prices are exact (whole ticks); a float reflection can break such a tie
the other way.

Each sub-expression is rewritten into an expression on the real bars plus how its value relates to the original's
value on the reflected bars:

* INV  - the same value (volume, ATR, ADX, a count, a true/false condition, a literal);
* REFL - C minus the value (a price: close, a moving average of a price, a swing high becomes the swing low);
* NEG  - minus the value (a difference of two prices, a z-score of a price);
* COMP - 100 minus the value (RSI: gains and losses swap).

Where the logic is not symmetric the screen has no mirror and the reason names the part: a price against a fixed
number (C is not known), a ratio of prices (`0.9 * Max(high, 250)`, PctChange of a price), a function whose answer
is not defined on reflected prices (Supertrend, the option chain, zones), or a cross-sectional rank of one. Nothing
here runs a screen; the mirror is validated like any other screen before it runs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from app.screener import nodes as n

INV, REFL, NEG, COMP = "inv", "refl", "neg", "comp"
SWING_WARMUP = 3                       # swings: exact from the third confirmed swing on (see the module note)
FLIP = {">": "<", ">=": "<=", "<": ">", "<=": ">=", "==": "==", "!=": "!="}

# Fields: what each one is on the reflected bars.
FIELD_RULES: Dict[str, Tuple[str, str]] = {"open": (REFL, "open"), "close": (REFL, "close"), "high": (REFL, "low"),
                                           "low": (REFL, "high"), "volume": (INV, "volume"), "oi": (INV, "oi")}
# Functions that read no argument expression, by their class on the reflected bars and their mirror's name.
LEVELS: Dict[str, Tuple[str, str]] = {
    "VWAP": (REFL, "VWAP"), "SessionVWAP": (REFL, "SessionVWAP"), "DayOpen": (REFL, "DayOpen"), "PDC": (REFL, "PDC"),
    "PDH": (REFL, "PDL"), "PDL": (REFL, "PDH"), "ORHigh": (REFL, "ORLow"), "ORLow": (REFL, "ORHigh"),
    "BBUpper": (REFL, "BBLower"), "BBLower": (REFL, "BBUpper"), "BBMid": (REFL, "BBMid"),
    "SwingHigh": (REFL, "SwingLow"), "SwingLow": (REFL, "SwingHigh"),
    "RSI": (COMP, "RSI"), "ATR": (INV, "ATR"), "ADX": (INV, "ADX"), "MedianRange": (INV, "MedianRange"),
    "PlusDI": (INV, "MinusDI"), "MinusDI": (INV, "PlusDI"), "RelVolume": (INV, "RelVolume"),
    "RelVolumeTOD": (INV, "RelVolumeTOD"),
    "IsFnO": (INV, "IsFnO"), "IndexMember": (INV, "IndexMember"),
}
# Linear in their first argument (an average, a lag): the class passes through. Max / Min swap when it is reversed.
LINEAR = {"SMA": "SMA", "EMA": "EMA", "Lag": "Lag"}
EXTREMES = {"Max": "Min", "Min": "Max", "Greatest": "Least", "Least": "Greatest"}
# Breakout screens and other filters with a side argument (the first one): the side swaps, the result is a condition.
SIDE_SWAP = {"up": "down", "down": "up", "above": "below", "below": "above", "bullish": "bearish", "bearish": "bullish",
             "low": "high", "high": "low"}
SIDED_FILTERS = {"RangeBreakout", "SwingBreakout", "NRBreakout", "NewHighBreakout", "RangeExpansion", "VolumeBreakout",
                 "ORBreakout", "DayLevelBreak", "DayExtremeBreak"}
# Classifiers: the value map of each (a value not listed stays as it is).
CATEGORIES: Dict[str, Dict[str, str]] = {
    "SwingDirection": {"UP": "DOWN", "DOWN": "UP"},
    "Sector": {}, "Industry": {}, "McapBucket": {},
}
# Checked on synthetic bars and found NOT exactly symmetric - no mirror rule, with the reason (tests keep them out).
_TIES = "an equal swing high or low is read as lower on both sides, so ties do not mirror"
NOT_SYMMETRIC: Dict[str, str] = {
    "DowTrend": _TIES, "SwingBreakType": _TIES,
    "ATRPercentile": "ATR as a percentage of the close depends on the price level",
    "Supertrend": "not checked on reflected prices",
}
PATTERN_SWAP = {"bullish_engulfing": "bearish_engulfing", "bearish_engulfing": "bullish_engulfing", "morning_star": "evening_star",
                "evening_star": "morning_star", "bullish_pin_bar": "bearish_pin_bar", "bearish_pin_bar": "bullish_pin_bar",
                "bullish_rejection": "bearish_rejection", "bearish_rejection": "bullish_rejection", "doji": "doji", "inside_bar": "inside_bar"}


class NoMirror(ValueError):
    """The screen's logic is not symmetric at `part`; `reason` says why."""

    def __init__(self, part: str, reason: str) -> None:
        super().__init__(f"{part}: {reason}")
        self.part, self.reason = part, reason


@dataclass
class Mirror:
    ast: Optional[n.Node]
    text: Optional[str]
    reason: Optional[str]
    symmetric: bool = False           # the screen is its own mirror (it already reads both sides alike)

    def as_dict(self) -> Dict[str, object]:
        return {"text": self.text, "reason": self.reason, "symmetric": self.symmetric, "ast": n.to_json(self.ast) if self.ast is not None else None}


Rewritten = Tuple[str, n.Node]       # (class, expression on the real bars)


def _part(node: n.Node) -> str:
    try:
        return n.to_text(node)
    except TypeError:
        return type(node).__name__


def _neg(node: n.Node) -> n.Node:
    return n.Num(-node.value) if isinstance(node, n.Num) else n.Unary("-", node)


def _hundred_minus(node: n.Node) -> n.Node:
    return n.Num(100.0 - node.value) if isinstance(node, n.Num) else n.Binary("-", n.Num(100.0), node)


def _same_call(node: n.Call, name: str, args: List[n.Node], kwargs: Optional[Dict[str, n.Node]] = None) -> n.Call:
    return n.Call(name, args, dict(node.kwargs if kwargs is None else kwargs), node.offset, node.tf)


def _swap_str(node: n.Node, table: Dict[str, str], where: n.Node) -> n.Node:
    if isinstance(node, n.Str) and node.value in table:
        return n.Str(table[node.value])
    if isinstance(node, n.Str):
        return node
    raise NoMirror(_part(where), "its side is not a fixed word, so it cannot be swapped")


def _kept(args: List[n.Node]) -> List[n.Node]:
    """Settings (windows, multipliers, degrees): read as they are - they must not depend on prices."""
    out: List[n.Node] = []
    for a in args:
        cls, expr = rewrite(a)
        if cls not in (INV, "str"):
            raise NoMirror(_part(a), "a setting that depends on prices")
        out.append(expr)
    return out


def _call(node: n.Call) -> Rewritten:  # noqa: C901 - one switch over the rule tables
    name = node.name
    if name in NOT_SYMMETRIC:
        raise NoMirror(_part(node), NOT_SYMMETRIC[name])
    if node.kwargs:
        raise NoMirror(_part(node), "named arguments are not mirrored")
    if name in LEVELS:
        cls, other = LEVELS[name]
        return cls, _same_call(node, other, _kept(list(node.args)))
    if name in LINEAR or name in EXTREMES:
        if not node.args:
            raise NoMirror(_part(node), "no argument")
        classes = [rewrite(a) for a in (node.args if name in ("Greatest", "Least") else node.args[:1])]
        if len({c for c, _ in classes}) != 1:
            raise NoMirror(_part(node), "its arguments are not mirrored alike")
        cls = classes[0][0]
        rest = [] if name in ("Greatest", "Least") else _kept(list(node.args[1:]))
        new_name = name
        if name in EXTREMES and cls != INV:
            new_name = EXTREMES[name]                         # the highest of a reversed series is its lowest
        return cls, _same_call(node, new_name, [e for _, e in classes] + rest)
    if name == "PctChange":
        cls, x = rewrite(node.args[0])
        if cls != INV:
            raise NoMirror(_part(node), "a percentage change of a price is not symmetric (a rise and a fall of the same size differ in %)")
        return INV, _same_call(node, name, [x, *_kept(list(node.args[1:]))])
    if name == "ZScore":
        cls, x = rewrite(node.args[0])
        if cls not in (INV, REFL, NEG, COMP):
            raise NoMirror(_part(node), "not mirrored")
        return (INV if cls == INV else NEG), _same_call(node, name, [x, *_kept(list(node.args[1:]))])
    if name in ("Count", "CountStreak"):
        cond = node.args[-1]
        ccls, cexpr = rewrite(cond)
        if ccls != INV:
            raise NoMirror(_part(node), "its condition is not a condition")
        return INV, _same_call(node, name, [*_kept(list(node.args[:-1])), cexpr])
    if name in ("CrossAbove", "CrossBelow"):
        a, b, flipped = _pair(node.args[0], node.args[1], node)
        above = (name == "CrossAbove") != flipped
        return INV, _same_call(node, "CrossAbove" if above else "CrossBelow", [a, b])
    if name in SIDED_FILTERS:
        if not node.args:
            raise NoMirror(_part(node), "no side")
        side = _swap_str(node.args[0], SIDE_SWAP, node)
        return INV, _same_call(node, name, [side, *_kept(list(node.args[1:]))])
    if name in ("RealBreak", "ReversalAt"):
        lcls, level = rewrite(node.args[0])
        if lcls != REFL:
            raise NoMirror(_part(node), "its level is not a price")
        side = _swap_str(node.args[1], SIDE_SWAP, node)
        return INV, _same_call(node, name, [level, side, *_kept(list(node.args[2:]))])
    if name in ("SwingZoneStrength", "SwingZoneDistance"):
        side = _swap_str(node.args[0], SIDE_SWAP, node)
        return (INV if name == "SwingZoneStrength" else NEG), _same_call(node, name, [side, *_kept(list(node.args[1:]))])
    if name == "Pattern":
        arg = node.args[0] if node.args else None
        if not isinstance(arg, n.Str) or arg.value not in PATTERN_SWAP:
            raise NoMirror(_part(node), "this candlestick pattern has no mirrored twin (hammer and shooting star differ by context)")
        return INV, _same_call(node, name, [n.Str(PATTERN_SWAP[arg.value])])
    if name in CATEGORIES:
        return "cat:" + name, _same_call(node, name, _kept(list(node.args)))
    raise NoMirror(_part(node), f"{name}() has no mirror rule (its answer on reflected prices is not defined here)")


def _pair(left: n.Node, right: n.Node, where: n.Node) -> Tuple[n.Node, n.Node, bool]:
    """Two sides of a comparison -> (left', right', flipped): `left op right` on the reflected bars is
    `left' op' right'` on the real ones, op' = FLIP[op] when flipped."""
    lc, le = rewrite(left)
    rc, re_ = rewrite(right)
    if lc == rc and lc in (INV,):
        return le, re_, False
    if lc == rc and lc in (REFL, NEG, COMP):
        return le, re_, True                                      # C - a > C - b  <=>  a < b
    if {lc, rc} == {NEG, INV}:
        return (le, _neg(re_), True) if lc == NEG else (_neg(le), re_, True)       # -a > d  <=>  a < -d
    if {lc, rc} == {COMP, INV}:
        return (le, _hundred_minus(re_), True) if lc == COMP else (_hundred_minus(le), re_, True)   # 100 - a > d <=> a < 100 - d
    if REFL in (lc, rc):
        raise NoMirror(_part(where), "a price is compared with something that is not a price (the reflection level is not known)")
    raise NoMirror(_part(where), "its two sides are not mirrored alike")


def _binary(node: n.Binary) -> Rewritten:  # noqa: C901 - the arithmetic table
    lc, le = rewrite(node.left)
    rc, re_ = rewrite(node.right)
    op = node.op
    bad = NoMirror(_part(node), "this arithmetic on prices is not symmetric")
    if COMP in (lc, rc) or lc.startswith("cat:") or rc.startswith("cat:"):
        raise bad
    if op in ("+", "-"):
        if lc == INV and rc == INV:
            return INV, n.Binary(op, le, re_)
        if lc == REFL and rc in (INV, NEG):
            # (C - a) + d = C - (a - d); (C - a) - d = C - (a + d); a NEG d is -d
            if rc == INV:
                return REFL, n.Binary("-" if op == "+" else "+", le, re_)
            return REFL, n.Binary("+" if op == "+" else "-", le, re_)
        if lc == INV and rc == REFL and op == "+":
            return REFL, n.Binary("-", re_, le)                    # d + (C - a) = C - (a - d)
        if lc == NEG and rc == REFL and op == "+":
            return REFL, n.Binary("+", re_, le)                    # -x + (C - a) = C - (a + x)
        if lc == REFL and rc == REFL and op == "-":
            return INV, n.Binary("-", re_, le)                     # (C - a) - (C - b) = b - a
        if lc == NEG and rc == NEG:
            return (NEG, n.Binary("+", le, re_)) if op == "+" else (INV, n.Binary("-", re_, le))
        if lc == NEG and rc == INV:
            return NEG, n.Binary("-" if op == "+" else "+", le, re_)   # -x + d = -(x - d); -x - d = -(x + d)
        if lc == INV and rc == NEG:
            return INV, n.Binary("-" if op == "+" else "+", le, re_)   # d + (-x) = d - x; d - (-x) = d + x
        raise bad
    if lc == REFL or rc == REFL:
        raise NoMirror(_part(node), "a ratio or a multiple of a price is not symmetric (C is not known)")
    if lc == INV and rc == INV:
        return INV, n.Binary(op, le, re_)
    if {lc, rc} == {NEG, INV}:
        return NEG, n.Binary(op, le, re_)
    if lc == NEG and rc == NEG:
        return INV, n.Binary(op, le, re_)
    raise bad


def rewrite(node: n.Node) -> Rewritten:  # noqa: C901 - one switch over the node kinds
    """(class, expression on the real bars) for a sub-expression; raises NoMirror where the logic is not symmetric."""
    if isinstance(node, (n.Num, n.Bool, n.Param)):
        return INV, node
    if isinstance(node, n.Str):
        return "str", node
    if isinstance(node, n.Field):
        if node.name not in FIELD_RULES:
            raise NoMirror(node.name, "no mirror rule")
        cls, name = FIELD_RULES[node.name]
        return cls, n.Field(name, node.offset, node.tf)
    if isinstance(node, n.Call):
        return _call(node)
    if isinstance(node, n.Unary):
        cls, x = rewrite(node.operand)
        if cls == INV:
            return NEG, x                                          # -(d)
        if cls == NEG:
            return INV, x                                          # -(-x) = x
        raise NoMirror(_part(node), "minus a price is not symmetric")
    if isinstance(node, n.Binary):
        return _binary(node)
    if isinstance(node, n.Compare):
        if isinstance(node.right, n.Str) or isinstance(node.left, n.Str):
            return INV, _category_compare(node)
        a, b, flipped = _pair(node.left, node.right, node)
        return INV, n.Compare(FLIP[node.op] if flipped else node.op, a, b)
    if isinstance(node, n.Between):
        vc, v = rewrite(node.value)
        lc, lo = rewrite(node.low)
        hc, hi = rewrite(node.high)
        if vc == lc == hc == INV:
            return INV, n.Between(v, lo, hi)
        if vc == lc == hc and vc in (REFL, NEG, COMP):
            return INV, n.Between(v, hi, lo)                        # the bounds swap
        if vc == NEG and lc == hc == INV:
            return INV, n.Between(v, _neg(hi), _neg(lo))
        if vc == COMP and lc == hc == INV:
            return INV, n.Between(v, _hundred_minus(hi), _hundred_minus(lo))
        raise NoMirror(_part(node), "its bounds are not mirrored like its value")
    if isinstance(node, n.In):
        cls, v = rewrite(node.value)
        if not cls.startswith("cat:"):
            raise NoMirror(_part(node), "IN is mirrored for categories only")
        table = CATEGORIES[cls[4:]]
        return INV, n.In(v, [n.Str(table.get(i.value, i.value)) if isinstance(i, n.Str) else i for i in node.items])
    if isinstance(node, n.Logic):
        return INV, n.Logic(node.op, [_cond(i) for i in node.items])
    if isinstance(node, n.Not):
        return INV, n.Not(_cond(node.item))
    raise NoMirror(type(node).__name__, "no mirror rule")


def _cond(node: n.Node) -> n.Node:
    cls, expr = rewrite(node)
    if cls != INV:
        raise NoMirror(_part(node), "not a condition")
    return expr


def _category_compare(node: n.Compare) -> n.Node:
    value, text = (node.left, node.right) if isinstance(node.right, n.Str) else (node.right, node.left)
    cls, v = rewrite(value)
    if not cls.startswith("cat:") or not isinstance(text, n.Str) or node.op not in ("==", "!="):
        raise NoMirror(_part(node), "a word is compared with something that is not a category")
    swapped = n.Str(CATEGORIES[cls[4:]].get(text.value, text.value))
    return n.Compare(node.op, v, swapped) if isinstance(node.right, n.Str) else n.Compare(node.op, swapped, v)


def mirror(ast: n.Node) -> Mirror:
    """The short / breakdown mirror of a screen, or the reason it has none."""
    try:
        out = _cond(ast)
    except NoMirror as exc:
        return Mirror(None, None, f"No mirror: {exc.part} - {exc.reason}.")
    if out == ast:
        return Mirror(None, None, "No separate mirror: the screen reads both sides alike (it is its own mirror).", symmetric=True)
    return Mirror(out, n.to_text(out), None)


def reflect_bars(frame, level: Optional[float] = None):  # type: ignore[no-untyped-def]
    """The bars reflected through `level` (default: highest high + lowest low, so prices stay in the same band) - for
    tests and checks: `mirror(S)` on bars == `S` on `reflect_bars(bars)`."""
    c = float(frame["high"].max() + frame["low"].min()) if level is None else float(level)
    out = frame.copy()
    out["open"], out["close"] = c - frame["open"], c - frame["close"]
    out["high"], out["low"] = c - frame["low"], c - frame["high"]
    return out


__all__ = ["SWING_WARMUP", "Mirror", "NoMirror", "mirror", "rewrite", "reflect_bars", "INV", "REFL", "NEG", "COMP", "CATEGORIES", "SIDED_FILTERS",
           "NOT_SYMMETRIC", "FIELD_RULES", "LEVELS", "LINEAR", "EXTREMES", "PATTERN_SWAP"]
