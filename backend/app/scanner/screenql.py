"""S1c (ADR-0021 §2): the Market Scanner on the ScreenQL engine.

`to_screen(request)` translates a `ScannerRequest` (indicator conditions, structure filters, option filters, all ANDed)
into one ScreenQL AST, so the scanner's filters are the first registry entries and a scan is an ordinary screen.
`run_scanner_screenql(request)` validates and runs that screen per symbol and returns the same `ScannerResult` shape
as the legacy engine (`app/scanner/engine.py`); a parity test runs both on the same bars.

`POST /api/scanner/run` uses this path when `SCANNER_ENGINE=screenql` (default `legacy` until the owner switches it);
the response is the same either way. The match decision is the screen's; the human-readable labels on a match are the
legacy helpers' wording, so the page does not change.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from app.core.models import bars_to_dataframe
from app.scanner import engine as legacy
from app.scanner.models import (
    OptionFilter,
    OptionFilterType,
    ScannerMatch,
    ScannerRequest,
    ScannerResult,
    StructureFilter,
    StructureFilterType,
)
from app.screener import nodes as n
from app.screener.runtime import SymbolData, run_screen
from app.screener.validator import validate
from app.strategy_engine.declarative import Condition, Operand, tf_minutes

_FIELD = {"CLOSE": "close", "OPEN": "open", "HIGH": "high", "LOW": "low", "VOLUME": "volume"}
_PERIOD_FN = {"RSI": "RSI", "ADX": "ADX", "ATR": "ATR", "PLUS_DI": "PlusDI", "MINUS_DI": "MinusDI"}
_SESSION_FN = {"VWAP": "VWAP", "DAY_OPEN": "DayOpen", "PDH": "PDH", "PDL": "PDL", "PDC": "PDC"}
_BANDS = {"BB_UPPER": "BBUpper", "BB_MID": "BBMid", "BB_LOWER": "BBLower"}
_CMP = {"GT": ">", "LT": "<", "GTE": ">=", "LTE": "<="}
_TF_BY_MINUTES = {1: "1m", 3: "3m", 5: "5m", 15: "15m", 30: "30m", 60: "1h", 375: "1d"}


class NotTranslatable(ValueError):
    """A request the ScreenQL path cannot express (an unusual timeframe); the legacy engine runs it instead."""


def screen_tf(timeframe: Optional[str]) -> str:
    minutes = tf_minutes(timeframe or "5min")
    tf = _TF_BY_MINUTES.get(minutes or -1)
    if tf is None:
        raise NotTranslatable(f"timeframe {timeframe!r} has no ScreenQL equivalent")
    return tf


def operand(op: Operand, base_tf: str) -> n.Node:
    if op.type == "value":
        return n.Num(float(op.value))
    tf = None
    if op.timeframe:
        tf = screen_tf(op.timeframe)
        if tf == "1d" and base_tf != "1d":
            # The Strategy Builder's "day" filter is a 375-minute bucket grid from the first session's open, not the calendar
            # session; ScreenQL's @1d is the session. Until the owner picks one (SCREENER.md SC-4), such a scan stays legacy.
            raise NotTranslatable("a 'day' operand inside an intraday scan keeps the legacy semantics")
        tf = None if tf == base_tf else tf
    name = op.indicator
    node: n.Node
    if name in _FIELD:
        return n.Field(_FIELD[name], tf=tf)
    if name in ("EMA", "SMA"):
        node = n.Call(name, [n.Field("close"), n.Num(op.period)])
    elif name == "VOLUME_SMA":
        node = n.Call("SMA", [n.Field("volume"), n.Num(op.period)])
    elif name in _PERIOD_FN:
        node = n.Call(_PERIOD_FN[name], [n.Num(op.period)])
    elif name in _SESSION_FN:
        node = n.Call(_SESSION_FN[name], [])
    elif name in ("OR_HIGH", "OR_LOW"):
        node = n.Call("ORHigh" if name == "OR_HIGH" else "ORLow", [n.Num(op.period)])
    elif name == "SUPERTREND" or name in _BANDS:
        node = n.Call("Supertrend" if name == "SUPERTREND" else _BANDS[name], [n.Num(op.period), n.Num(float(op.multiplier))])
    else:
        raise NotTranslatable(f"indicator {name} has no ScreenQL equivalent")
    if isinstance(node, n.Call):
        node.tf = tf
    return node


def condition(c: Condition, base_tf: str) -> n.Node:
    left, right = operand(c.left, base_tf), operand(c.right, base_tf)
    if c.operator == "CROSSES_ABOVE":
        return n.Call("CrossAbove", [left, right])
    if c.operator == "CROSSES_BELOW":
        return n.Call("CrossBelow", [left, right])
    return n.Compare(_CMP[c.operator], left, right)


def structure(f: StructureFilter, swing: int) -> n.Node:
    t = f.filter_type
    w = n.Num(swing)
    if t.value.startswith("TREND_"):
        return n.Compare("==", n.Call("Trend", [w]), n.Str(t.value.removeprefix("TREND_")))
    if t in (StructureFilterType.BOS_BULLISH, StructureFilterType.BOS_BEARISH, StructureFilterType.CHOCH_BULLISH, StructureFilterType.CHOCH_BEARISH):
        return n.Compare("==", n.Call("StructureEvent", [w]), n.Str(t.value))
    if t == StructureFilterType.PATTERN_BULLISH:
        return n.Call("PatternBullish", [])
    if t == StructureFilterType.PATTERN_BEARISH:
        return n.Call("PatternBearish", [])
    return n.Call("NearSupport" if t == StructureFilterType.NEAR_SUPPORT else "NearResistance", [n.Num(f.tolerance_pct), w])


def option(f: OptionFilter) -> n.Node:
    if f.filter_type == OptionFilterType.PCR:
        if f.operator is None or f.value is None:
            return n.Bool(False)                                     # the legacy engine never matches an incomplete PCR filter
        return n.Compare(_CMP[f.operator], n.Call("PCR", []), n.Num(f.value))
    if f.filter_type in (OptionFilterType.BIAS_BULLISH, OptionFilterType.BIAS_BEARISH):
        return n.Compare("==", n.Call("ChainBias", []), n.Str(f.filter_type.value.removeprefix("BIAS_")))
    return n.Compare("<=", n.Call("MaxPainDistancePct", []), n.Num(f.tolerance_pct))


def to_screen(request: ScannerRequest, base_tf: str) -> n.Node:
    items: List[n.Node] = [condition(c, base_tf) for c in request.indicator_conditions]
    items += [structure(f, request.swing_window) for f in request.structure_filters]
    items += [option(f) for f in request.option_filters]
    if not items:
        return n.Bool(True)
    return items[0] if len(items) == 1 else n.Logic("ALL", items)


def run_scanner_screenql(request: ScannerRequest) -> ScannerResult:
    """The scan as a screen. Falls back to the legacy engine for a request ScreenQL cannot express."""
    matches: List[ScannerMatch] = []
    screens: Dict[str, tuple] = {}
    for item in request.symbols:
        if not item.candles:
            continue
        df = bars_to_dataframe(item.candles)
        if df.empty:
            continue
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        try:
            tf = screen_tf(item.timeframe)
            if tf not in screens:
                ast = to_screen(request, tf)
                screens[tf] = (ast, validate(ast, base_tf=tf, cost_cap=float("inf"), strict_units=False))
        except NotTranslatable:
            legacy_match = legacy._scan_symbol(item, request)
            if legacy_match is not None:
                matches.append(legacy_match)
            continue
        ast, validated = screens[tf]
        if not validated.ok:
            raise ValueError(f"translated scan failed validation: {[p.message for p in validated.problems]}")
        data = SymbolData(item.symbol, {tf: df}, option_chain=item.option_chain)
        result = run_screen(ast, validated, [data], base_tf=tf, check_history=False)[0]   # legacy: short history just never matches
        if result.matched:
            matches.append(_labelled(item, request, df))
    return ScannerResult(scanned_count=len(request.symbols), matched_count=len(matches), matches=matches)


def _labelled(item, request: ScannerRequest, df) -> ScannerMatch:
    """The legacy wording for a match (presentation only; the decision was the screen's)."""
    indicator = [c.label() for c in request.indicator_conditions]
    structure_labels = [(legacy._check_structure_filter(f, df, request.swing_window)[1] or f.label()) for f in request.structure_filters]
    option_labels = [(legacy._check_option_filter(f, item.option_chain)[1] or f.label()) for f in request.option_filters]
    return ScannerMatch(symbol=item.symbol, close=float(df["close"].iloc[-1]), matched_indicator_labels=indicator,
                        matched_structure_labels=structure_labels, matched_option_labels=option_labels)


__all__ = ["to_screen", "run_scanner_screenql", "screen_tf", "NotTranslatable"]
