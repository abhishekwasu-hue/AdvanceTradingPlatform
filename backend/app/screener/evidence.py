"""U8 EV1 (docs/specs/ATP_SCREEN_EVIDENCE_CHARTS_ADDENDUM.md): the evidence pack - what a screen used on one symbol, so a
chart can show ONLY the screen's own conditions and a verify strip can show the engine's own numbers.

`build_pack(ast, validated, data, ...)` evaluates the screen with the runtime's own evaluator (`runtime._Eval`, the
same code `run_screen` runs, on the same bars and parameters - never a separate re-computation) and returns
`evidence/1`, a JSON-ready dict:

* `bars` - the last `window` CLOSED bars of the screen's timeframe up to `as_of` (default: the last bar given). Bars
  that close after `as_of` are dropped before anything is evaluated, so appending or changing later bars (or later zone
  snapshots) never changes a pack (tested). The matched bar is the last one (`matched_index`).
* `blocks` - the top-level stages (a top-level AND is a list of blocks, like the U5 funnel), each with its pass/fail.
* `rows` - one per condition (a comparison, a range, a membership or a true/false function) with its value(s) at the
  matched bar, units, pass/fail and a one-line `verify` text ("close 1,842.00 > SMA(close, 20) 1,830.40").
* `drawables` - at most one per condition, in bar-index + price terms (engine-neutral): `overlay` (the series and
  levels a price condition compares), `pane` (an oscillator / volume condition with its threshold), `box` (a breakout
  base and its level), `band` (a market zone, through the Z7a-3 band selector, at most six), `bars` (pattern candles).
  A condition that did not pass is drawn only with `debug=True` (marked `failed`). Nothing else is ever drawn: no
  user layers, no other indicators, no zones the screen did not read (owner rule 0).
* `caption` - the plain-text lines a Telegram image carries (EV2).

Size-bounded: at most MAX_ROWS conditions and MAX_SERIES series per drawable; the rest are summarised in `summary`.
Informational only: a pack says how a screen's conditions evaluated on the data shown; it never places, proposes or
ranks an order.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from app.screener import nodes as n
from app.screener.runtime import ScreenRuntimeError, SymbolData, _Eval, _last, bar_close, closed_only

SCHEMA = "evidence/1"
DEFAULT_WINDOW = 120
MAX_WINDOW = 300
MAX_ROWS = 24
MAX_SERIES = 2
MAX_LEVELS = 2
MAX_ZONE_BANDS = 6
MAX_PACK_BYTES = 256 * 1024
CAPTION_MAX = 1024                    # Telegram's limit for a photo caption
DISCLAIMER = ("Informational: how the screen's conditions evaluated on the data shown, with the engine's own numbers. "
              "Not a recommendation.")

PRICE_FIELDS = ("open", "high", "low", "close")

# U6 BK1: a breakout function's arguments -> the breakout parameters (the runtime's own defaults; parity is tested)
BREAKOUT_ARGS: Dict[str, Tuple[str, Tuple[Tuple[str, str, Any], ...]]] = {
    "RangeBreakout": ("B1", (("range_n", "int", 20), ("min_break_atr", "num", 0.2), ("vol_mult", "num", 1.5), ("range_mode", "text", "fixed"))),
    "SwingBreakout": ("B2", (("swing_degree", "int", 1), ("min_break_atr", "num", 0.2))),
    "SwingBreakType": ("B2", (("swing_degree", "int", 1), ("min_break_atr", "num", 0.2))),
    "NRBreakout": ("B4", (("nr_setup", "text", "nr7"), ("vol_mult", "num", 1.5), ("min_break_atr", "num", 0.2))),
    "NewHighBreakout": ("B7", (("high_weeks", "int", 52), ("vol_mult", "num", 1.5), ("min_break_atr", "num", 0.2))),
    "RangeExpansion": ("B13", (("expansion_k", "num", 2.0), ("vol_mult", "num", 1.5))),
    "VolumeBreakout": ("B14", (("shock_mult", "num", 2.0), ("range_n", "int", 20))),
}
BREAKOUT_LEVEL_WORDS: Dict[str, Tuple[str, str]] = {
    "B1": ("range high", "range low"), "B2": ("swing high", "swing low"), "B4": ("setup bar high", "setup bar low"),
    "B7": ("N-week high", "N-week low"), "B13": ("bar midpoint", "bar midpoint"), "B14": ("level", "level"),
}
ZONE_CALLS = ("NearZone", "ZoneRole", "ZoneState", "ZoneStrength")
CROSS_CALLS = ("CrossAbove", "CrossBelow")
# candles a named pattern reads (this bar and up to two before it, app/price_action/candlestick_patterns.py)
PATTERN_BARS: Dict[str, int] = {"bullish_engulfing": 2, "bearish_engulfing": 2, "inside_bar": 2, "bullish_outside_bar": 2,
                                "bearish_outside_bar": 2, "morning_star": 3, "evening_star": 3}
# U8 EV3 (flag `evidence_context`): the option-chain functions draw the chain's OI walls / max pain on the price pane
CHAIN_CALLS = ("PCR", "ChainBias", "MaxPainDistancePct")
CONTEXT_TFS = ("1h", "1d", "1w")      # the multi-timeframe strip: these (above the screen's own) plus the screen's timeframes
CONTEXT_DEGREE = 1                    # the strip's trend tag: the engine's DowTrend at this swing degree

# U8 EV4: how every ScreenQL registry entry shows in a pack - the declaration the coverage lint holds every entry to
# (tests/test_u8_ev3_ev4.py: a new registry entry fails the lint until it is declared here, and each declaration is
# checked against what build_pack really produces).
#   series  - a number: drawn through the comparison it is in (an overlay on the price pane, or a lower pane)
#   box     - a breakout base (EV2)          band  - a market zone (Z7a-3 selector)      bars - pattern candles
#   overlay - the function's own level / crossing on the price pane                     levels - option-chain OI walls (EV3)
#   strip   - a category or a filter without geometry: in the verify strip only, with the reason
DRAWN_AS: Dict[str, str] = {
    **{name: "box" for name in BREAKOUT_ARGS if name != "SwingBreakType"},
    "SwingBreakType": "strip",
    **{name: "band" for name in ZONE_CALLS},
    **{name: "overlay" for name in CROSS_CALLS},
    "RealBreak": "overlay", "ReversalAt": "overlay",
    "Pattern": "bars", "PatternBullish": "bars", "PatternBearish": "bars",
    **{name: "levels" for name in CHAIN_CALLS},
}


# ------------------------------------------------------------------------------------------------------- formatting
def _num(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return round(x, 4) if math.isfinite(x) else None


def value_text(value: Any, unit: Optional[str]) -> str:
    """The number as the verify strip shows it, by unit (the value itself stays exact in `value`)."""
    if value is None:
        return "n/a"
    if isinstance(value, (bool, np.bool_)):
        return "yes" if value else "no"
    if isinstance(value, str):
        return value or "none"
    x = float(value)
    if not math.isfinite(x):
        return "n/a"
    if unit == "price":
        return f"{x:,.2f}"
    if unit in ("volume", "contracts"):
        return f"{x:,.0f}"
    if unit == "pct":
        return f"{x:,.2f}%"
    if unit == "index":
        return f"{x:,.1f}"
    if unit == "count":
        return f"{x:,.0f}"
    if unit == "ratio":
        return f"{x:,.2f}"
    return f"{x:,.4g}" if abs(x) < 1e6 else f"{x:,.0f}"


def _iso(ts: Any) -> str:
    t = pd.Timestamp(ts)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    return t.isoformat()


# ---------------------------------------------------------------------------------------------------------- the tree
def blocks_of(ast: Any) -> List[Any]:
    """The top-level stages: a top-level AND is a list of blocks (the U5 funnel's stages); anything else is one block."""
    return list(ast.items) if isinstance(ast, n.Logic) and ast.op == "ALL" else [ast]


def conditions_of(node: Any) -> List[Any]:
    """The conditions inside a block, in reading order: AND / OR groups and a NOT over a group are opened; a NOT over
    one condition stays one condition."""
    if isinstance(node, n.Logic):
        return [c for item in node.items for c in conditions_of(item)]
    if isinstance(node, n.Not) and isinstance(node.item, (n.Logic, n.Not)):
        return conditions_of(node.item)
    return [node]


def _unit(node: Any, base_tf: str, params: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    """(type, unit) of an expression - the validator's own typing (so `same` resolves like it does for the screen)."""
    from app.screener.validator import _Checker
    t, u = _Checker(base_tf, params, strict_units=False).check(node)
    return t, (None if u == "any" else u)


def _truth(v: Any) -> Optional[bool]:
    v = _last(v)
    if v is None:
        return False
    if isinstance(v, float) and np.isnan(v):
        return False
    return bool(v)


# ------------------------------------------------------------------------------------------------------- the builder
@dataclass
class _Ctx:
    ev: _Eval
    base_tf: str
    params: Dict[str, Any]
    frame: pd.DataFrame                 # base-timeframe bars up to the matched bar (all of them, for the engine)
    window: int                         # bars kept for drawing (the last `window` of `frame`)
    debug: bool
    zone_cands: List[Tuple[str, Dict[str, Any]]] = field(default_factory=list)   # (row id, zone candidate) for the selector

    @property
    def j(self) -> int:
        return self.window - 1          # the matched bar, in window coordinates

    def start(self) -> int:
        return len(self.frame) - self.window

    def values(self, series: Any) -> List[Optional[float]]:
        if not isinstance(series, pd.Series):
            return [_num(series)] * self.window
        tail = series.iloc[-self.window:]
        out = [_num(v) if not isinstance(v, (bool, np.bool_)) else (1.0 if v else 0.0) for v in tail.tolist()]
        return [None] * (self.window - len(out)) + out


def _term(ctx: _Ctx, node: Any) -> Dict[str, Any]:
    t, unit = _unit(node, ctx.base_tf, ctx.params)
    try:
        raw = _last(ctx.ev.ev(node, ctx.base_tf))
    except ScreenRuntimeError as exc:
        return {"text": n.to_text(node), "value": None, "value_text": "n/a", "unit": unit, "literal": False, "note": str(exc)[:160]}
    literal = isinstance(node, (n.Num, n.Str, n.Bool, n.Param))
    if t in ("cat", "str"):
        value: Any = None if raw is None or (isinstance(raw, float) and np.isnan(raw)) else str(raw)
    elif t == "bool":
        value = _truth(raw)
    else:
        value = _num(raw)
    return {"text": n.to_text(node), "value": value, "value_text": value_text(value, unit), "unit": unit, "literal": literal}


def _verify_text(node: Any, terms: List[Dict[str, Any]]) -> str:
    def show(term: Dict[str, Any]) -> str:
        if term["literal"]:
            return term["value_text"] if isinstance(term["value"], (int, float)) and not isinstance(term["value"], bool) else term["text"]
        return f"{term['text']} {term['value_text']}"
    if isinstance(node, n.Not) and terms:
        return "NOT (" + _verify_text(node.item, terms) + ")"
    if isinstance(node, n.Compare) and len(terms) == 2:
        return f"{show(terms[0])} {node.op} {show(terms[1])}"
    if isinstance(node, n.Between) and len(terms) == 3:
        return f"{show(terms[0])} between {show(terms[1])} and {show(terms[2])}"
    if isinstance(node, n.In) and terms:
        return f"{show(terms[0])} in ({', '.join(n.to_text(i) for i in node.items)})"
    shown = [t for t in terms if not t["literal"]]
    if shown:
        return f"{n.to_text(node)}: " + "; ".join(f"{t['text']} {t['value_text']}" for t in shown)
    return n.to_text(node)


def _operands(node: Any) -> List[Any]:
    if isinstance(node, n.Not):
        return _operands(node.item)
    if isinstance(node, n.Compare):
        return [node.left, node.right]
    if isinstance(node, n.Between):
        return [node.value, node.low, node.high]
    if isinstance(node, n.In):
        return [node.value]
    return []


def _inner_call(node: Any) -> Optional[n.Call]:
    """The true/false function a condition is (or negates)."""
    if isinstance(node, n.Not):
        return _inner_call(node.item)
    return node if isinstance(node, n.Call) else None


def _zone_call(node: Any) -> Optional[n.Call]:
    for sub in n.walk(node):
        if isinstance(sub, n.Call) and sub.name in ZONE_CALLS:
            return sub
    return None


def _pane_for(unit: Optional[str]) -> str:
    return "price" if unit == "price" else "volume" if unit in ("volume", "contracts") else "oscillator"


def _overlay(ctx: _Ctx, operands: Sequence[Any], unit: Optional[str], label: str) -> Optional[Dict[str, Any]]:
    """The series and levels a comparison reads, on the price pane (price units) or its own pane (anything else)."""
    series: List[Dict[str, Any]] = []
    levels: List[Dict[str, Any]] = []
    for op in operands:
        if isinstance(op, (n.Num, n.Param)):
            value = _num(_last(ctx.ev.ev(op, ctx.base_tf)))
            if value is not None and len(levels) < MAX_LEVELS:
                levels.append({"value": value, "label": n.to_text(op), "value_text": value_text(value, unit)})
            continue
        if isinstance(op, n.Field) and op.name in PRICE_FIELDS and not op.offset and not op.tf:
            continue                                      # the bar itself (the matched candle is marked)
        if isinstance(op, (n.Str, n.Bool)):
            continue
        if len(series) >= MAX_SERIES:
            continue
        try:
            values = ctx.values(ctx.ev.series(op, ctx.base_tf))
        except ScreenRuntimeError:
            continue
        style = "histogram" if isinstance(op, n.Field) and op.name in ("volume", "oi") and not op.offset else "line"
        series.append({"name": n.to_text(op), "values": values, "style": style})
    if not series and not levels:
        return None
    pane = _pane_for(unit)
    return {"kind": "overlay" if pane == "price" else "pane", "pane": pane, "unit": unit, "label": label, "series": series,
            "levels": levels}


def _breakout(ctx: _Ctx, call: n.Call, row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A breakout filter: the box of its base (or its level), from the breakout engine's own frame on the screen's bars;
    the verify terms are the engine's close, level, distance in ATR (ATR of the bar before) and relative volume."""
    from app.screener.breakouts import params as bp
    from app.screener.breakouts.screens import Bars, evaluate_screen
    screen, spec = BREAKOUT_ARGS[call.name]
    side = str(ctx.ev.ev(call.args[0], ctx.base_tf)) if call.args else "up"
    overrides: Dict[str, Any] = {}
    for i, (name, kind, default) in enumerate(spec, start=1):
        if len(call.args) > i:
            raw = _last(ctx.ev.ev(call.args[i], ctx.base_tf))
            overrides[name] = int(float(raw)) if kind == "int" else (str(raw) if kind == "text" else float(raw))
        else:
            overrides[name] = default
    p = bp.settings(**overrides)
    f = ctx.frame
    bars = Bars(f, p)
    out = evaluate_screen(f, screen, p, bars=bars, sides=(side,))[side]
    k = len(f) - 1
    sign = 1.0 if side == "up" else -1.0
    level = _num(out["level"].iloc[k])
    close = float(bars.c[k])
    atr_prev = float(bars.atr_prev[k]) if np.isfinite(bars.atr_prev[k]) else float("nan")
    dist = (close - level) * sign / atr_prev if level is not None and atr_prev and math.isfinite(atr_prev) and atr_prev > 0 else None
    rvol = float(bars.rvol[k]) if np.isfinite(bars.rvol[k]) else None
    words = BREAKOUT_LEVEL_WORDS[screen][0 if side == "up" else 1]
    terms = [{"text": "close", "value": _num(close), "value_text": value_text(close, "price"), "unit": "price", "literal": False},
             {"text": words, "value": level, "value_text": value_text(level, "price"), "unit": "price", "literal": False},
             {"text": "distance (ATR)", "value": _num(dist), "value_text": "n/a" if dist is None else f"{dist:+.2f} ATR", "unit": "ratio",
              "literal": False},
             {"text": "volume vs average", "value": _num(rvol), "value_text": "n/a" if rvol is None else f"{rvol:.2f}x", "unit": "ratio",
              "literal": False}]
    event = str(out["event"].iloc[k] or "")
    if event:
        terms.append({"text": "type", "value": event, "value_text": event, "unit": None, "literal": False})
    row["terms"] = terms
    row["verify"] = (f"{call.name}: close {terms[0]['value_text']} vs {words} {terms[1]['value_text']} ({terms[2]['value_text']}), "
                     f"volume {terms[3]['value_text']} average")
    row["engine_signal"] = bool(out["signal"].iloc[k])
    if level is None:
        return None
    base_len, hi, lo = out["base_len"].iloc[k], out["base_high"].iloc[k], out["base_low"].iloc[k]
    label = f"{words} {value_text(level, 'price')}"
    if screen == "B13":
        return {"kind": "bars", "pane": "price", "label": label, "index": [ctx.j], "level": level}
    if all(isinstance(x, (int, float)) and np.isfinite(x) for x in (base_len, hi, lo)) and base_len >= 1:
        frm = max(0, ctx.j - int(base_len))
        return {"kind": "box", "pane": "price", "label": label, "from": frm, "to": ctx.j - 1 if ctx.j - 1 >= frm else ctx.j,
                "low": _num(min(hi, lo)), "high": _num(max(hi, lo)), "level": level}
    return {"kind": "overlay", "pane": "price", "unit": "price", "label": label, "series": [],
            "levels": [{"value": level, "label": words, "value_text": value_text(level, "price")}]}


def _zone(ctx: _Ctx, call: n.Call, row: Dict[str, Any]) -> None:
    """A zone function: the live zone it read at the matched bar's close (the stored snapshot as of then) becomes a band
    candidate; the Z7a-3 selector keeps at most six across the pack (`_zone_bands`)."""
    from app.price_action.zones.chart import nearest_zone
    from app.screener.zones import _closes_ns
    timeline = getattr(ctx.ev.data, "zones", None)
    if timeline is None or timeline.empty or not len(ctx.frame):
        row["note"] = "no stored market zones for this symbol"
        return
    args = [ctx.ev.ev(a, ctx.base_tf) for a in call.args]
    if call.name == "ZoneRole":
        role, tol, family = "any", float(args[0]) if args else 0.5, str(args[1]) if len(args) > 1 else "all"
    else:
        role = str(args[0]) if args else "any"
        tol = float(args[1]) if len(args) > 1 else 0.5
        family = str(args[2]) if len(args) > 2 else "all"
    i = int(timeline.index_at(_closes_ns(ctx.frame.index[-1:], ctx.base_tf))[0])
    snap = timeline.snapshots[i] if i >= 0 else None
    close = float(ctx.frame["close"].iloc[-1])
    zone = nearest_zone(snap.rows, close, role, tol, family) if snap is not None else None
    if zone is None:
        row["note"] = "no live zone within the tolerance at the matched bar"
        return
    zone_terms: List[Dict[str, Any]] = [
        {"text": "close", "value": _num(close), "value_text": value_text(close, "price"), "unit": "price", "literal": False},
        {"text": f"{zone['role']} zone", "value": f"{zone['low_text']}-{zone['high_text']}", "value_text": f"{zone['low_text']}-{zone['high_text']}",
         "unit": "price", "literal": False},
        {"text": "zone state", "value": zone["state"], "value_text": zone["state"], "unit": None, "literal": False},
        {"text": "distance", "value": _num(zone["distance_pct"]), "value_text": value_text(zone["distance_pct"], "pct"), "unit": "pct",
         "literal": False}]
    words = (f"close {zone_terms[0]['value_text']}, {zone['role']} zone {zone_terms[1]['value_text']} ({zone['state']}), "
             f"{zone_terms[3]['value_text']} away")
    if row.get("is_call"):                                # the zone function is the condition: its own terms are the zone's
        row["terms"] = zone_terms
        row["verify"] = f"{n.to_text(call)}: {words}"
    else:                                                 # a comparison on a zone value: its operands, then the zone read
        row["terms"] = row["terms"] + zone_terms[1:]
        row["verify"] = f"{row['verify']} ({words})"
    row["zone_as_of"] = snap.as_of.astimezone(timezone.utc).isoformat() if snap is not None else None
    stored: Dict[str, Any] = next((r for r in snap.rows if str(r.get("zone_key")) == zone["zone_key"]), {}) if snap is not None else {}
    zone["formed_at"] = stored.get("formed_at")            # the zone's own start: the band is drawn from there
    ctx.zone_cands.append((row["id"], zone))


def _drawable(ctx: _Ctx, node: Any, row: Dict[str, Any]) -> Optional[Dict[str, Any]]:  # noqa: C901 - one switch over kinds
    call = _inner_call(node)
    if call is not None and (call.tf or call.offset):
        row["note"] = "on another timeframe or an earlier bar: shown in the strip, not drawn"
        return None
    if call is not None and call.name in BREAKOUT_ARGS and call.name != "SwingBreakType":
        return _breakout(ctx, call, row)
    zone_call = _zone_call(node)
    if zone_call is not None:
        if not zone_call.tf and not zone_call.offset:
            _zone(ctx, zone_call, row)
        return None                                           # the band is added by the selector (at most six)
    if call is not None and call.name in CROSS_CALLS and len(call.args) == 2:
        _t, unit = _unit(call.args[0], ctx.base_tf, ctx.params)
        d = _overlay(ctx, call.args, unit, n.to_text(call))
        if d is not None:
            d["marker"] = {"index": ctx.j, "text": "cross"}
        return d
    if call is not None and call.name == "Pattern" and call.args:
        name = str(ctx.ev.ev(call.args[0], ctx.base_tf))
        k = PATTERN_BARS.get(name, 1)
        return {"kind": "bars", "pane": "price", "label": name.replace("_", " "), "index": list(range(max(0, ctx.j - k + 1), ctx.j + 1))}
    if call is not None and call.name in ("PatternBullish", "PatternBearish"):
        return {"kind": "bars", "pane": "price", "label": n.to_text(call), "index": [ctx.j]}
    if call is not None and call.name in ("RealBreak", "ReversalAt") and call.args:
        d = _overlay(ctx, [call.args[0]], "price", n.to_text(call))
        if d is not None and call.name == "ReversalAt":
            d["marker"] = {"index": ctx.j, "text": "reversal"}
        return d
    if call is not None:
        row.setdefault("note", "shown in the strip, not drawn")
        return None
    ops = _operands(node)
    if not ops or isinstance(node, n.In) or (isinstance(node, n.Not) and isinstance(node.item, n.In)):
        row.setdefault("note", "shown in the strip, not drawn")
        return None
    types_units = [_unit(op, ctx.base_tf, ctx.params) for op in ops]
    if any(t in ("cat", "str", "bool") for t, _u in types_units):
        row.setdefault("note", "a category: shown in the strip, not drawn")
        return None
    unit = next((u for _t, u in types_units if u is not None), None)
    return _overlay(ctx, ops, unit, n.to_text(node))


def _chain_call(node: Any) -> Optional[n.Call]:
    for sub in n.walk(node):
        if isinstance(sub, n.Call) and sub.name in CHAIN_CALLS:
            return sub
    return None


def _chain(ctx: _Ctx, call: n.Call, row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """EV3: an option-chain condition (PCR, ChainBias, MaxPainDistancePct) - the chain the engine read, analysed by the
    same `analyze_option_chain` the runtime calls: the call / put OI walls (the strikes with the most open interest) or
    max pain as levels on the price pane, and those numbers in the strip."""
    chain = ctx.ev.data.option_chain
    if chain is None:
        row["note"] = "no option chain for this bar (a chain is a snapshot of its own time): shown in the strip, not drawn"
        return None
    from app.option_chain.analysis import analyze_option_chain
    a = analyze_option_chain(chain)
    ce = a.call_resistance_strikes[0] if a.call_resistance_strikes else None
    pe = a.put_support_strikes[0] if a.put_support_strikes else None
    facts = [("call OI wall", ce, "price"), ("put OI wall", pe, "price"), ("max pain", a.max_pain, "price"), ("PCR", a.pcr, "ratio"),
             ("underlying", a.underlying_ltp, "price")]
    row["terms"] = list(row["terms"]) + [{"text": label, "value": _num(v), "value_text": value_text(_num(v), unit), "unit": unit, "literal": False}
                                         for label, v, unit in facts if v is not None]
    row["note"] = f"option chain {chain.underlying} {chain.expiry}: " + " · ".join(
        f"{label} {value_text(_num(v), unit)}" for label, v, unit in facts if v is not None)
    wanted = [("max pain", a.max_pain), ("underlying", a.underlying_ltp)] if call.name == "MaxPainDistancePct" else [("call OI wall", ce), ("put OI wall", pe)]
    levels = [{"value": _num(v), "label": label, "value_text": value_text(_num(v), "price")} for label, v in wanted if v is not None][:MAX_LEVELS]
    if not levels:
        return None
    return {"kind": "overlay", "pane": "price", "unit": "price", "label": f"{call.name}: option chain {chain.expiry}", "series": [],
            "levels": levels, "chain": {"underlying": chain.underlying, "expiry": chain.expiry}}


def _context(ctx: _Ctx, ast: Any, validated: Any, order: List[str], rows: Dict[str, Dict[str, Any]], t_close: Any) -> List[Dict[str, Any]]:
    """EV3: the multi-timeframe strip - the engine's own DowTrend tag on each timeframe at or above the screen's (the
    screen's own timeframes plus 1H / D / W), as of the matched bar's close (the runtime's close-time alignment: a
    higher timeframe's bar counts only once it closed), and which conditions read that timeframe. The degree is the
    screen's own DowTrend degree when it reads one, else CONTEXT_DEGREE."""
    from app.screener.parser import parse
    base_m = n.TF_MINUTES[ctx.base_tf]
    degree = CONTEXT_DEGREE
    for node in n.walk(ast):                              # the screen's own DowTrend degree, when it reads one
        if isinstance(node, n.Call) and node.name == "DowTrend" and node.args and isinstance(node.args[0], n.Num):
            degree = int(node.args[0].value)
            break
    tfs = sorted({tf for tf in set(getattr(validated, "timeframes", set()) or set()) | set(CONTEXT_TFS)
                  if tf in n.TF_MINUTES and n.TF_MINUTES[tf] >= base_m}, key=lambda tf: n.TF_MINUTES[tf])
    out: List[Dict[str, Any]] = []
    for tf in tfs:
        text = f"DowTrend({degree})" + ("" if tf == ctx.base_tf else f"@{tf}")
        tag: Optional[str] = None
        note: Optional[str] = None
        try:
            raw = _last(ctx.ev.ev(parse(text), ctx.base_tf))
            tag = None if raw is None or (isinstance(raw, float) and np.isnan(raw)) else str(raw)
        except ScreenRuntimeError as exc:
            note = str(exc)[:120]
        as_of = None
        try:
            frame = ctx.ev.frame(tf)
            closes = bar_close(pd.DatetimeIndex(frame.index), tf)
            done = closes[closes <= t_close]
            as_of = _iso(done[-1]) if len(done) else None
        except (ScreenRuntimeError, ValueError, KeyError):
            pass
        if tag is None and note is None:
            note = "not enough bars on this timeframe"
        used_by = [rid for rid in order if (f"@{tf}" in rows[rid]["text"]) or (tf == ctx.base_tf and "@" not in rows[rid]["text"])]
        out.append({"tf": tf, "tag": tag, "expr": text, "bar_close": as_of, "used_by": used_by, "note": note,
                    "text": f"{tf} {tag.lower() if tag else 'n/a'}"})
    return out


def _zone_bands(ctx: _Ctx, rows: Dict[str, Dict[str, Any]], drawables: List[Dict[str, Any]]) -> None:
    """Z7a-3 band selector over the zones the screen's zone conditions read (never any other zone), at most six."""
    from app.price_action.zones.chart import select_bands
    if not ctx.zone_cands:
        return
    close = float(ctx.frame["close"].iloc[-1])
    by_key: Dict[str, List[str]] = {}
    cands: List[Dict[str, Any]] = []
    for row_id, zone in ctx.zone_cands:
        if zone["zone_key"] not in by_key:
            cands.append({k: zone[k] for k in ("zone_key", "zone_type", "family", "timeframe", "kind", "low", "high", "low_text", "high_text",
                                               "state", "touches", "strength")})
        by_key.setdefault(zone["zone_key"], []).append(row_id)
    bands = select_bands(cands, close, max_bands=MAX_ZONE_BANDS, max_distance_pct=None)
    kept = {b["zone_key"]: b for b in bands}
    formed = {zone["zone_key"]: zone.get("formed_at") for _r, zone in ctx.zone_cands}
    for key, row_ids in by_key.items():
        band = kept.get(key)
        if band is None:
            for row_id in row_ids:
                rows[row_id]["note"] = f"zone not drawn: more than {MAX_ZONE_BANDS} zone bands"
            continue
        # one band per zone, however many conditions read it, owned by the first condition that passed
        eligible = [r for r in row_ids if (rows[r]["passed"] or ctx.debug) and not rows[r].get("drawable")]
        if not eligible:
            continue
        owner = next((r for r in eligible if rows[r]["passed"]), eligible[0])
        start, formed_iso = _band_start(ctx, formed.get(key))
        d = {"id": f"d{len(drawables) + 1}", "row": owner, "kind": "band", "pane": "price", "label": band["label"],
             "low": _num(band["low"]), "high": _num(band["high"]), "from": start, "to": ctx.j, "role": band["role"], "state": band["state"],
             "touch_text": band["touch_text"], "formed_at": formed_iso, "failed": not rows[owner]["passed"]}
        drawables.append(d)
        for row_id in eligible:
            rows[row_id]["drawable"] = d["id"]


def _band_start(ctx: _Ctx, formed_at: Any) -> Tuple[int, Optional[str]]:
    """The first window bar at or after the zone's own start (`formed_at` of the stored row); 0 when it formed before the
    window or the row has no start time (then the band runs from the window's first bar)."""
    if formed_at is None:
        return 0, None
    try:
        t = pd.Timestamp(formed_at)
    except (TypeError, ValueError):
        return 0, None
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    idx = pd.DatetimeIndex(ctx.frame.index[-ctx.window:])
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    pos = int(idx.searchsorted(t, side="left"))
    return min(max(pos, 0), ctx.j), t.isoformat()


def _trim(data: SymbolData, base_tf: str, as_of: Optional[datetime]) -> SymbolData:
    if as_of is None:
        return data
    at = as_of if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc)
    frames: Dict[str, pd.DataFrame] = {}
    for tf, df in data.frames.items():
        if df is None or df.empty:
            frames[tf] = df
            continue
        if df.index.tz is None:
            closes = bar_close(df.index.tz_localize("UTC"), tf)
            frames[tf] = df[closes <= pd.Timestamp(at)]
        else:
            frames[tf] = closed_only(df, tf, at)
    # every other field (zones, the BK2 session exchange, reference data) carried over as is - only the frames are cut;
    # EV3: an option chain is a snapshot of the newest bar's time, so a pack as of an earlier bar never reads it
    cut = any(df is not None and frames.get(tf) is not None and len(frames[tf]) < len(df) for tf, df in data.frames.items())
    return dataclasses.replace(data, frames=frames, indices=set(data.indices), option_chain=None if cut else data.option_chain)


def ast_sha256(ast: Any) -> str:
    return hashlib.sha256(json.dumps(n.to_json(ast), sort_keys=True).encode()).hexdigest()


def build_pack(ast: Any, validated: Any, data: SymbolData, *, base_tf: str, params: Optional[Dict[str, Any]] = None,
               as_of: Optional[datetime] = None, window: int = DEFAULT_WINDOW, debug: bool = False,
               constants: Optional[Dict[int, float]] = None, exchange: str = "NSE", data_source: Optional[str] = None,
               run_id: Optional[int] = None, screen_name: Optional[str] = None, context: bool = False) -> Dict[str, Any]:
    """The evidence pack of one symbol (module docstring). Raises ScreenRuntimeError when the screen cannot run or the
    symbol has no closed bar up to `as_of`. `context` (EV3, flag `evidence_context`): the option-chain drawables and the
    multi-timeframe strip."""
    if not getattr(validated, "ok", False):
        raise ScreenRuntimeError("the screen did not pass validation")
    params = dict(params or {})
    trimmed = _trim(data, base_tf, as_of)
    base = trimmed.frames.get(base_tf)
    if base is None or base.empty:
        raise ScreenRuntimeError(f"{data.symbol}: no closed {base_tf} bar up to the evidence time")
    ev = _Eval(trimmed, base_tf, params, dict(constants or {}))
    w = max(1, min(int(window), MAX_WINDOW, len(base)))
    ctx = _Ctx(ev=ev, base_tf=base_tf, params=params, frame=base, window=w, debug=debug)
    tail = base.iloc[-w:]
    try:
        matched: Optional[bool] = _truth(ev.ev(ast, base_tf))
    except ScreenRuntimeError:
        matched = None

    blocks: List[Dict[str, Any]] = []
    rows: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    drawables: List[Dict[str, Any]] = []
    not_shown = 0
    for bi, block in enumerate(blocks_of(ast), start=1):
        bid = f"b{bi}"
        try:
            bpass: Optional[bool] = _truth(ev.ev(block, base_tf))
        except ScreenRuntimeError:
            bpass = None
        row_ids: List[str] = []
        entry: Dict[str, Any] = {"id": bid, "text": n.to_text(block), "passed": bpass, "rows": row_ids}
        for ci, cond in enumerate(conditions_of(block), start=1):
            if len(order) >= MAX_ROWS:
                not_shown += 1
                continue
            rid = f"{bid}.c{ci}"
            try:
                passed: Optional[bool] = _truth(ev.ev(cond, base_tf))
            except ScreenRuntimeError as exc:
                passed = None
                row: Dict[str, Any] = {"id": rid, "block": bid, "text": n.to_text(cond), "passed": None, "terms": [],
                                       "verify": n.to_text(cond), "note": str(exc)[:160], "drawable": None}
                rows[rid] = row
                row_ids.append(rid)
                order.append(rid)
                continue
            terms = [_term(ctx, op) for op in _operands(cond)]
            call = _inner_call(cond)
            if call is not None and not terms:
                terms = [_term(ctx, a) for a in call.args if not isinstance(a, (n.Str, n.Bool))][:3]
            row = {"id": rid, "block": bid, "text": n.to_text(cond), "passed": passed, "terms": terms,
                   "verify": _verify_text(cond, terms), "drawable": None, "is_call": call is not None and not _operands(cond)}
            if isinstance(cond, n.Not):
                row["negated"] = True
            chain_call = _chain_call(cond) if context else None
            d = _chain(ctx, chain_call, row) if chain_call is not None else _drawable(ctx, cond, row)
            if d is not None and (passed or debug):
                d = {"id": f"d{len(drawables) + 1}", "row": rid, **d, "failed": not passed}
                drawables.append(d)
                row["drawable"] = d["id"]
            row.pop("is_call", None)
            rows[rid] = row
            row_ids.append(rid)
            order.append(rid)
        blocks.append(entry)
    _zone_bands(ctx, rows, drawables)

    t_close = bar_close(pd.DatetimeIndex([tail.index[-1]]), base_tf)[0]
    pack: Dict[str, Any] = {
        "schema": SCHEMA,
        "symbol": data.symbol, "exchange": exchange, "base_tf": base_tf,
        "screen": {"text": n.to_text(ast), "ast_sha256": ast_sha256(ast), "version": n.VERSION, "name": screen_name, "params": params},
        "run_id": run_id,
        "as_of": {"bar_start": _iso(tail.index[-1]), "bar_close": _iso(t_close)},
        "data": {"source": data_source},
        "matched": matched,
        "bars": {"t": [_iso(t) for t in tail.index], "o": [_num(v) for v in tail["open"]], "h": [_num(v) for v in tail["high"]],
                 "l": [_num(v) for v in tail["low"]], "c": [_num(v) for v in tail["close"]],
                 "v": [_num(v) for v in tail["volume"]] if "volume" in tail.columns else []},
        "matched_index": ctx.j,
        "blocks": blocks,
        "rows": [rows[r] for r in order],
        "drawables": drawables,
        "summary": {"conditions": len(order) + not_shown, "shown": len(order), "not_shown": not_shown, "drawn": len(drawables),
                    "debug": debug},
        "disclaimer": DISCLAIMER,
    }
    if context:
        pack["context"] = _context(ctx, ast, validated, order, rows, t_close)
    pack["caption"] = caption_lines(pack)
    if len(json.dumps(pack, separators=(",", ":"))) > MAX_PACK_BYTES and w > 30:
        return build_pack(ast, validated, data, base_tf=base_tf, params=params, as_of=as_of, window=max(30, w // 2), debug=debug,
                          constants=constants, exchange=exchange, data_source=data_source, run_id=run_id, screen_name=screen_name,
                          context=context)
    return pack


INTRADAY_TFS = ("1m", "3m", "5m", "15m", "30m", "1h")


def bar_time_text(pack: Dict[str, Any]) -> str:
    """"bar of 19 Jul 2026" (a daily / weekly / monthly bar: its session date in IST) or "bar closed 10 Oct 2026 11:15 IST"
    (an intraday bar)."""
    if pack["base_tf"] in INTRADAY_TFS:
        t = pd.Timestamp(pack["as_of"]["bar_close"]).tz_convert("Asia/Kolkata")
        return f"bar closed {t.strftime('%d %b %Y %H:%M')} IST"
    t = pd.Timestamp(pack["as_of"]["bar_start"]).tz_convert("Asia/Kolkata")
    return f"bar of {t.strftime('%d %b %Y')}"


def caption_lines(pack: Dict[str, Any]) -> List[str]:
    """The text an image carries: symbol, timeframe and the bar time (IST), the screen, each shown condition with its
    numbers and pass mark (failed ones only in debug), and the informational note."""
    lines = [f"{pack['symbol']} · {pack['base_tf']} · {bar_time_text(pack)}"]
    name = pack["screen"].get("name")
    lines.append(f"Screen: {name}" if name else f"Screen: {pack['screen']['text'][:200]}")
    for row in pack["rows"]:
        if row["passed"] is False and not pack["summary"]["debug"]:
            continue
        mark = "✅" if row["passed"] else ("❌" if row["passed"] is False else "–")
        lines.append(f"{mark} {row['verify']}")
    if pack["summary"]["not_shown"]:
        lines.append(f"+{pack['summary']['not_shown']} more conditions")
    if pack.get("context"):
        lines.append("Trend: " + " · ".join(c["text"] for c in pack["context"]))
    lines.append(DISCLAIMER)
    return lines


def caption_text(pack: Dict[str, Any], limit: int = CAPTION_MAX) -> str:
    """The caption within Telegram's limit: whole lines, the note always last."""
    lines = list(pack.get("caption") or caption_lines(pack))
    note = lines[-1]
    body: List[str] = []
    for line in lines[:-1]:
        if len("\n".join(body + [line, note])) > limit:
            break
        body.append(line)
    text = "\n".join(body + [note])
    return text[:limit]


__all__ = ["SCHEMA", "DISCLAIMER", "build_pack", "blocks_of", "conditions_of", "caption_lines", "caption_text", "value_text", "ast_sha256", "bar_time_text",
           "BREAKOUT_ARGS", "MAX_ROWS", "MAX_ZONE_BANDS", "DEFAULT_WINDOW", "MAX_WINDOW", "DRAWN_AS", "CHAIN_CALLS", "CONTEXT_TFS", "drawn_as"]


def drawn_as(name: str) -> Optional[str]:
    """EV4: how a registry entry shows in a pack (DRAWN_AS, else by its type: a number is a `series`, a category or a
    true/false filter without geometry is `strip`). None only for a name the registry does not have."""
    from app.screener.registry import BOOL, CAT, FUNCTIONS, NUM, STR
    if name in DRAWN_AS:
        return DRAWN_AS[name]
    spec = FUNCTIONS.get(name)
    if spec is None:
        return None
    if spec.returns == NUM and spec.timeframed:
        return "series"
    if spec.returns in (BOOL, CAT, STR) or spec.returns == NUM:
        return "strip"
    return None
