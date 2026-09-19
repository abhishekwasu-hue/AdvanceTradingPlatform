from typing import List, Optional, Tuple

import pandas as pd

from app.brokers.models import OptionChain
from app.core.models import bars_to_dataframe
from app.option_chain.analysis import analyze_option_chain
from app.option_chain.models import OptionChainBias
from app.price_action.candlestick_patterns import detect_patterns_at
from app.price_action.market_structure import TrendState, analyze_market_structure
from app.scanner.models import (
    OptionFilter,
    OptionFilterType,
    ScannerMatch,
    ScannerRequest,
    ScannerResult,
    ScannerSymbolInput,
    StructureFilter,
    StructureFilterType,
)
from app.strategy_engine.declarative import Condition
from app.support_resistance.engine import SupportResistanceEngine

_TREND_FILTERS = {
    StructureFilterType.TREND_UPTREND: TrendState.UPTREND,
    StructureFilterType.TREND_DOWNTREND: TrendState.DOWNTREND,
    StructureFilterType.TREND_RANGE: TrendState.RANGE,
}


def _check_indicator_conditions(conditions: List[Condition], df: pd.DataFrame) -> Optional[List[str]]:
    """Returns matched labels if every condition holds, None if any fails or lacks enough data."""
    labels: List[str] = []
    for condition in conditions:
        holds, enough = condition.evaluate(df)
        if not enough or not holds:
            return None
        labels.append(condition.label())
    return labels


def _check_structure_filter(
    filter_: StructureFilter, df: pd.DataFrame, swing_window: int,
) -> Tuple[bool, Optional[str]]:
    filter_type = filter_.filter_type

    if filter_type in _TREND_FILTERS:
        structure = analyze_market_structure(df, window=swing_window)
        if structure.trend == _TREND_FILTERS[filter_type]:
            return True, f"Trend is {structure.trend.value}"
        return False, None

    if filter_type in (
        StructureFilterType.BOS_BULLISH, StructureFilterType.BOS_BEARISH,
        StructureFilterType.CHOCH_BULLISH, StructureFilterType.CHOCH_BEARISH,
    ):
        structure = analyze_market_structure(df, window=swing_window)
        if not structure.events:
            return False, None
        latest = structure.events[-1]
        wants_bos = filter_type in (StructureFilterType.BOS_BULLISH, StructureFilterType.BOS_BEARISH)
        wants_bullish = filter_type in (StructureFilterType.BOS_BULLISH, StructureFilterType.CHOCH_BULLISH)
        event_matches = (latest.event == "BOS") == wants_bos
        direction_matches = (latest.direction == "BULLISH") == wants_bullish
        if event_matches and direction_matches:
            return True, f"Latest structure event: {latest.event} {latest.direction} at {latest.level:g}"
        return False, None

    if filter_type in (StructureFilterType.PATTERN_BULLISH, StructureFilterType.PATTERN_BEARISH):
        wants_bullish = filter_type == StructureFilterType.PATTERN_BULLISH
        matches = detect_patterns_at(df, len(df) - 1)
        wanted_direction = "BULLISH" if wants_bullish else "BEARISH"
        hits = [m for m in matches if m.direction == wanted_direction]
        if hits:
            return True, f"{hits[0].pattern} ({hits[0].direction}, confidence {hits[0].confidence})"
        return False, None

    if filter_type in (StructureFilterType.NEAR_SUPPORT, StructureFilterType.NEAR_RESISTANCE):
        wants_support = filter_type == StructureFilterType.NEAR_SUPPORT
        zones = SupportResistanceEngine(swing_window=swing_window).build_zones(df, timeframe="scanner")
        close = df["close"].iloc[-1]
        kind = "SUPPORT" if wants_support else "RESISTANCE"
        for zone in zones:
            if zone.kind != kind:
                continue
            distance_pct = min(abs(close - zone.lower), abs(close - zone.upper)) / close * 100
            if distance_pct <= filter_.tolerance_pct:
                return True, f"Within {filter_.tolerance_pct:g}% of {kind.lower()} zone {zone.lower:g}-{zone.upper:g}"
        return False, None

    return False, None


def _check_option_filter(filter_: OptionFilter, chain: Optional[OptionChain]) -> Tuple[bool, Optional[str]]:
    if chain is None:
        # Never fabricate a match from missing data - a symbol with no supplied option chain
        # simply fails every option filter rather than being silently excluded from the check.
        return False, None

    analysis = analyze_option_chain(chain)

    if filter_.filter_type == OptionFilterType.PCR:
        if analysis.pcr is None or filter_.operator is None or filter_.value is None:
            return False, None
        ops = {"GT": lambda a, b: a > b, "LT": lambda a, b: a < b, "GTE": lambda a, b: a >= b, "LTE": lambda a, b: a <= b}
        if ops[filter_.operator](analysis.pcr, filter_.value):
            return True, f"PCR {analysis.pcr:.2f} {filter_.operator} {filter_.value:g}"
        return False, None

    if filter_.filter_type in (OptionFilterType.BIAS_BULLISH, OptionFilterType.BIAS_BEARISH):
        wanted = OptionChainBias.BULLISH if filter_.filter_type == OptionFilterType.BIAS_BULLISH else OptionChainBias.BEARISH
        if analysis.bias == wanted:
            return True, f"Option chain bias: {analysis.bias.value}"
        return False, None

    if filter_.filter_type == OptionFilterType.NEAR_MAX_PAIN:
        if analysis.max_pain is None or analysis.underlying_ltp is None:
            return False, None
        distance_pct = abs(analysis.underlying_ltp - analysis.max_pain) / analysis.underlying_ltp * 100
        if distance_pct <= filter_.tolerance_pct:
            return True, f"Within {filter_.tolerance_pct:g}% of Max Pain {analysis.max_pain:g}"
        return False, None

    return False, None


def _scan_symbol(symbol_input: ScannerSymbolInput, request: ScannerRequest) -> Optional[ScannerMatch]:
    if not symbol_input.candles:
        return None
    df = bars_to_dataframe(symbol_input.candles)
    if df.empty:
        return None

    indicator_labels = _check_indicator_conditions(request.indicator_conditions, df)
    if indicator_labels is None:
        return None

    structure_labels: List[str] = []
    for structure_filter in request.structure_filters:
        holds, label = _check_structure_filter(structure_filter, df, request.swing_window)
        if not holds:
            return None
        structure_labels.append(label or structure_filter.label())

    option_labels: List[str] = []
    for option_filter in request.option_filters:
        holds, label = _check_option_filter(option_filter, symbol_input.option_chain)
        if not holds:
            return None
        option_labels.append(label or option_filter.label())

    return ScannerMatch(
        symbol=symbol_input.symbol, close=float(df["close"].iloc[-1]),
        matched_indicator_labels=indicator_labels, matched_structure_labels=structure_labels,
        matched_option_labels=option_labels,
    )


def run_scanner(request: ScannerRequest) -> ScannerResult:
    """Runs every configured filter (AND-combined within and across categories - indicator
    conditions reuse the exact same `Condition` building block the no-code Strategy Builder uses)
    against each supplied symbol's own candle data, and returns only the symbols that clear all
    of them. A symbol lacking the data a filter needs (too few candles, no option chain supplied
    for an option filter) simply doesn't match that filter - never a fabricated pass.
    """
    matches = [m for m in (_scan_symbol(s, request) for s in request.symbols) if m is not None]
    return ScannerResult(scanned_count=len(request.symbols), matched_count=len(matches), matches=matches)
