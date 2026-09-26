from typing import List

import numpy as np
import pandas as pd

from app.brokers.models import OptionChain, OptionChainRow
from app.core.models import OHLCVBar
from app.scanner.engine import run_scanner
from app.scanner.models import (
    OptionFilter,
    OptionFilterType,
    ScannerRequest,
    ScannerSymbolInput,
    StructureFilter,
    StructureFilterType,
)
from app.strategy_engine.declarative import Condition, Operand
from tests.utils import make_series, rally_then_decline


def _zigzag(turning_points: List[float], bars_per_leg: int = 8) -> List[float]:
    prices: List[float] = []
    for a, b in zip(turning_points[:-1], turning_points[1:]):
        leg = list(np.linspace(a, b, bars_per_leg, endpoint=False))
        prices.extend(leg)
    prices.append(turning_points[-1])
    return prices


def _bars_from_df(df: pd.DataFrame) -> List[OHLCVBar]:
    return [
        OHLCVBar(timestamp=ts, open=row.open, high=row.high, low=row.low, close=row.close, volume=row.volume)
        for ts, row in df.iterrows()
    ]


def _rsi_condition(operator: str, value: float, period: int = 14) -> Condition:
    return Condition(
        left=Operand(type="indicator", indicator="RSI", period=period),
        operator=operator,
        right=Operand(type="value", value=value),
    )


def test_indicator_filter_matches_symbol_meeting_condition():
    up_df = make_series(_zigzag([100, 130, 118, 150]))
    down_df = make_series(list(reversed(_zigzag([100, 130, 118, 150]))))

    request = ScannerRequest(
        symbols=[
            ScannerSymbolInput(symbol="RISING", candles=_bars_from_df(up_df)),
            ScannerSymbolInput(symbol="FALLING", candles=_bars_from_df(down_df)),
        ],
        indicator_conditions=[_rsi_condition("GT", 60.0)],
    )
    result = run_scanner(request)

    matched_symbols = {m.symbol for m in result.matches}
    assert "RISING" in matched_symbols
    assert "FALLING" not in matched_symbols
    assert result.scanned_count == 2


def test_indicator_conditions_are_and_combined():
    df = make_series(_zigzag([100, 130, 118, 150]))
    request = ScannerRequest(
        symbols=[ScannerSymbolInput(symbol="X", candles=_bars_from_df(df))],
        indicator_conditions=[_rsi_condition("GT", 60.0), _rsi_condition("GT", 99.9)],
    )
    result = run_scanner(request)
    assert result.matches == []


def test_structure_trend_filters():
    up_df = make_series(_zigzag([100, 110, 105, 118, 112, 126, 120, 135]))
    down_df = make_series(_zigzag([135, 120, 126, 112, 118, 105, 110, 100]))

    uptrend_request = ScannerRequest(
        symbols=[ScannerSymbolInput(symbol="UP", candles=_bars_from_df(up_df))],
        structure_filters=[StructureFilter(filter_type=StructureFilterType.TREND_UPTREND)],
    )
    assert [m.symbol for m in run_scanner(uptrend_request).matches] == ["UP"]

    wrong_trend_request = ScannerRequest(
        symbols=[ScannerSymbolInput(symbol="UP", candles=_bars_from_df(up_df))],
        structure_filters=[StructureFilter(filter_type=StructureFilterType.TREND_DOWNTREND)],
    )
    assert run_scanner(wrong_trend_request).matches == []

    downtrend_request = ScannerRequest(
        symbols=[ScannerSymbolInput(symbol="DOWN", candles=_bars_from_df(down_df))],
        structure_filters=[StructureFilter(filter_type=StructureFilterType.TREND_DOWNTREND)],
    )
    assert [m.symbol for m in run_scanner(downtrend_request).matches] == ["DOWN"]


def test_structure_bos_bullish_filter():
    up_df = make_series(_zigzag([100, 110, 105, 118, 112, 126, 120, 135]))
    request = ScannerRequest(
        symbols=[ScannerSymbolInput(symbol="UP", candles=_bars_from_df(up_df))],
        structure_filters=[StructureFilter(filter_type=StructureFilterType.BOS_BULLISH)],
    )
    matches = run_scanner(request).matches
    assert len(matches) == 1
    assert any("BOS BULLISH" in label for label in matches[0].matched_structure_labels)


def test_structure_pattern_bullish_filter_matches_engulfing():
    idx = pd.date_range("2024-01-02 09:15", periods=2, freq="1min")
    df = pd.DataFrame([
        {"open": 102, "close": 100, "high": 103, "low": 99, "volume": 1000},
        {"open": 99, "close": 103, "high": 104, "low": 98, "volume": 1200},
    ], index=idx)
    bars = [
        OHLCVBar(timestamp=ts, open=row.open, high=row.high, low=row.low, close=row.close, volume=row.volume)
        for ts, row in df.iterrows()
    ]

    bullish_request = ScannerRequest(
        symbols=[ScannerSymbolInput(symbol="ENGULF", candles=bars)],
        structure_filters=[StructureFilter(filter_type=StructureFilterType.PATTERN_BULLISH)],
    )
    assert [m.symbol for m in run_scanner(bullish_request).matches] == ["ENGULF"]

    bearish_request = ScannerRequest(
        symbols=[ScannerSymbolInput(symbol="ENGULF", candles=bars)],
        structure_filters=[StructureFilter(filter_type=StructureFilterType.PATTERN_BEARISH)],
    )
    assert run_scanner(bearish_request).matches == []


def test_structure_near_support_filter():
    # A clean V-shape puts a swing low (support) at the trough, then closes right back near it.
    prices = _zigzag([100, 90, 100], bars_per_leg=10) + [91.0]
    df = make_series(prices)
    request = ScannerRequest(
        symbols=[ScannerSymbolInput(symbol="NEARLOW", candles=_bars_from_df(df))],
        structure_filters=[StructureFilter(filter_type=StructureFilterType.NEAR_SUPPORT, tolerance_pct=5.0)],
    )
    matches = run_scanner(request).matches
    assert len(matches) == 1


def _bullish_option_chain() -> OptionChain:
    rows = [
        OptionChainRow(strike=100, call_oi=20, call_change_oi=-5, put_oi=40, put_change_oi=15),
        OptionChainRow(strike=110, call_oi=15, call_change_oi=-2, put_oi=35, put_change_oi=10),
        OptionChainRow(strike=120, call_oi=10, call_change_oi=-1, put_oi=30, put_change_oi=8),
    ]
    return OptionChain(underlying="NIFTY", expiry="2024-01-25", underlying_ltp=110.0, rows=rows)


def test_option_pcr_and_bias_filters():
    chain = _bullish_option_chain()
    df = make_series([100.0] * 30)
    request = ScannerRequest(
        symbols=[ScannerSymbolInput(symbol="NIFTY", candles=_bars_from_df(df), option_chain=chain)],
        option_filters=[
            OptionFilter(filter_type=OptionFilterType.PCR, operator="GT", value=1.2),
            OptionFilter(filter_type=OptionFilterType.BIAS_BULLISH),
        ],
    )
    matches = run_scanner(request).matches
    assert len(matches) == 1
    assert len(matches[0].matched_option_labels) == 2


def test_option_filter_fails_without_a_supplied_chain():
    df = make_series([100.0] * 30)
    request = ScannerRequest(
        symbols=[ScannerSymbolInput(symbol="NOCHAIN", candles=_bars_from_df(df))],
        option_filters=[OptionFilter(filter_type=OptionFilterType.BIAS_BULLISH)],
    )
    assert run_scanner(request).matches == []


def test_near_max_pain_filter():
    rows = [
        OptionChainRow(strike=100, call_oi=50, put_oi=10),
        OptionChainRow(strike=110, call_oi=30, put_oi=30),
        OptionChainRow(strike=120, call_oi=10, put_oi=50),
    ]
    chain = OptionChain(underlying="NIFTY", expiry="2024-01-25", underlying_ltp=110.5, rows=rows)
    df = make_series([100.0] * 30)
    request = ScannerRequest(
        symbols=[ScannerSymbolInput(symbol="NIFTY", candles=_bars_from_df(df), option_chain=chain)],
        option_filters=[OptionFilter(filter_type=OptionFilterType.NEAR_MAX_PAIN, tolerance_pct=1.0)],
    )
    assert len(run_scanner(request).matches) == 1


def test_scanner_reports_scanned_and_matched_counts():
    df = make_series(rally_then_decline())
    request = ScannerRequest(
        symbols=[
            ScannerSymbolInput(symbol="A", candles=_bars_from_df(df)),
            ScannerSymbolInput(symbol="B", candles=[]),
        ],
        indicator_conditions=[],
    )
    result = run_scanner(request)
    assert result.scanned_count == 2
    assert result.matched_count == 1  # symbol B has no candles, never matches
