"""S1c (ADR-0021 §2): the Market Scanner as a ScreenQL screen - translation, the legacy scanner tests re-run on the new
engine, a seeded parity fuzz against the legacy engine on the same bars, and the SCANNER_ENGINE switch on the API."""
import inspect
import random

import numpy as np
import pandas as pd
import pytest

from app.brokers.models import OptionChain, OptionChainRow
from app.core.models import OHLCVBar
from app.scanner import screenql
from app.scanner.engine import run_scanner
from app.scanner.models import (
    OptionFilter,
    OptionFilterType,
    ScannerRequest,
    ScannerSymbolInput,
    StructureFilter,
    StructureFilterType,
)
from app.screener import nodes as n, parse
from app.strategy_engine.declarative import Condition, Operand
from tests import test_scanner as legacy_tests


def test_a_scan_translates_to_one_screen():
    req = ScannerRequest(symbols=[], swing_window=4,
                         indicator_conditions=[Condition(left=Operand(type="indicator", indicator="EMA", period=9), operator="CROSSES_ABOVE",
                                                         right=Operand(type="indicator", indicator="EMA", period=21, timeframe="15min")),
                                               Condition(left=Operand(type="indicator", indicator="RSI", period=14), operator="GT",
                                                         right=Operand(type="value", value=55))],
                         structure_filters=[StructureFilter(filter_type=StructureFilterType.TREND_UPTREND),
                                            StructureFilter(filter_type=StructureFilterType.NEAR_SUPPORT, tolerance_pct=0.8)],
                         option_filters=[OptionFilter(filter_type=OptionFilterType.PCR, operator="GTE", value=1.1),
                                         OptionFilter(filter_type=OptionFilterType.BIAS_BEARISH)])
    text = n.to_text(screenql.to_screen(req, "5m"))
    assert text == ('CrossAbove(EMA(close, 9), EMA(close, 21)@15m) AND RSI(14) > 55 AND Trend(4) == "UPTREND" AND NearSupport(0.8, 4) '
                    'AND PCR() >= 1.1 AND ChainBias() == "BEARISH"')
    assert parse(text) == screenql.to_screen(req, "5m")
    assert screenql.to_screen(ScannerRequest(symbols=[]), "5m") == n.Bool(True)


_LEGACY = [f for name, f in inspect.getmembers(legacy_tests, inspect.isfunction) if name.startswith("test_")]


@pytest.mark.parametrize("legacy_test", _LEGACY, ids=[f.__name__ for f in _LEGACY])
def test_every_legacy_scanner_test_passes_on_the_screenql_engine(monkeypatch, legacy_test):
    monkeypatch.setattr(legacy_tests, "run_scanner", screenql.run_scanner_screenql)
    legacy_test()


# --- parity fuzz -------------------------------------------------------------------------------------------------------

INDICATORS = ["EMA", "SMA", "RSI", "ADX", "PLUS_DI", "MINUS_DI", "ATR", "SUPERTREND", "CLOSE", "OPEN", "HIGH", "LOW", "VWAP", "DAY_OPEN",
              "OR_HIGH", "OR_LOW", "PDH", "PDL", "PDC", "BB_UPPER", "BB_MID", "BB_LOWER", "VOLUME", "VOLUME_SMA"]
PRICE_LIKE = [i for i in INDICATORS if i not in ("RSI", "ADX", "PLUS_DI", "MINUS_DI", "ATR", "VOLUME", "VOLUME_SMA")]


def _walk(rng, sessions=3, per_session=75):
    frames = []
    price = 100.0
    for d in range(sessions):
        idx = pd.date_range(pd.Timestamp("2026-03-02 03:45", tz="UTC") + pd.Timedelta(days=d), periods=per_session, freq="5min")
        steps = rng.normal(0, 0.4, per_session).cumsum() + price
        price = float(steps[-1])
        opens = np.r_[steps[0], steps[:-1]]
        frames.append(pd.DataFrame({"open": opens, "high": np.maximum(opens, steps) + rng.uniform(0, 0.3, per_session),
                                    "low": np.minimum(opens, steps) - rng.uniform(0, 0.3, per_session), "close": steps,
                                    "volume": rng.uniform(500, 1500, per_session)}, index=idx))
    df = pd.concat(frames)
    return [OHLCVBar(timestamp=ts.to_pydatetime(), open=r.open, high=r.high, low=r.low, close=r.close, volume=r.volume) for ts, r in df.iterrows()]


def _chain(rng):
    rows = [OptionChainRow(strike=float(s), call_oi=float(rng.integers(10, 100)), call_change_oi=float(rng.integers(-20, 20)),
                           put_oi=float(rng.integers(10, 100)), put_change_oi=float(rng.integers(-20, 20))) for s in (90, 100, 110, 120)]
    return OptionChain(underlying="X", expiry="2026-03-26", underlying_ltp=float(rng.uniform(95, 115)), rows=rows)


def _operand(rng, price_side):
    if rng.random() < 0.3:
        return Operand(type="value", value=float(rng.choice([20, 50, 70, 100, 101.5, 1000])))
    name = rng.choice(PRICE_LIKE if price_side else INDICATORS)
    tf = rng.choice([None, None, None, "15min", "30min", "60min"])
    return Operand(type="indicator", indicator=name, period=int(rng.choice([3, 5, 9, 14, 20, 30])), multiplier=float(rng.choice([1.5, 2.0, 3.0])),
                   timeframe=tf)


def _request(rng, symbols):
    conds = []
    for _ in range(rng.integers(0, 3)):
        price = rng.random() < 0.6
        conds.append(Condition(left=_operand(rng, price), operator=rng.choice(["GT", "LT", "GTE", "LTE", "CROSSES_ABOVE", "CROSSES_BELOW"]),
                               right=_operand(rng, price)))
    structure = [StructureFilter(filter_type=StructureFilterType(t), tolerance_pct=float(rng.choice([0.3, 1.0, 3.0])))
                 for t in rng.choice([t.value for t in StructureFilterType], size=rng.integers(0, 2), replace=False)]
    options = []
    for t in rng.choice([t.value for t in OptionFilterType], size=rng.integers(0, 2), replace=False):
        options.append(OptionFilter(filter_type=OptionFilterType(t), operator=rng.choice(["GT", "LT", "GTE", "LTE"]) if t == "PCR" else None,
                                    value=float(rng.choice([0.7, 1.0, 1.3])) if t == "PCR" else None, tolerance_pct=float(rng.choice([1.0, 5.0]))))
    return ScannerRequest(symbols=symbols, indicator_conditions=conds, structure_filters=structure, option_filters=options,
                          swing_window=int(rng.choice([2, 3, 5])))


def test_parity_fuzz_against_the_legacy_engine():
    rng = np.random.default_rng(20261011)
    symbols = [ScannerSymbolInput(symbol=f"S{i}", timeframe="5min", candles=_walk(rng), option_chain=_chain(rng) if i % 2 else None)
               for i in range(6)]
    compared = matched = 0
    for _ in range(120):
        req = _request(rng, symbols)
        old = [m.symbol for m in run_scanner(req).matches]
        new = screenql.run_scanner_screenql(req)
        assert [m.symbol for m in new.matches] == old, (req.model_dump_json()[:600], old, [m.symbol for m in new.matches])
        assert new.scanned_count == len(symbols)
        compared += 1
        matched += bool(old)
    assert compared == 120 and matched >= 20                                                # the fuzz is not all empty results


def test_a_day_operand_inside_an_intraday_scan_stays_on_the_legacy_engine():
    rng = random.Random(3)
    bars = _walk(np.random.default_rng(3))
    req = ScannerRequest(symbols=[ScannerSymbolInput(symbol="A", candles=bars)],
                         indicator_conditions=[Condition(left=Operand(type="indicator", indicator="CLOSE"), operator="GT",
                                                         right=Operand(type="indicator", indicator="EMA", period=5, timeframe="day"))])
    with pytest.raises(screenql.NotTranslatable):
        screenql.to_screen(req, "5m")
    assert [m.symbol for m in screenql.run_scanner_screenql(req).matches] == [m.symbol for m in run_scanner(req).matches]
    assert rng                                                                              # seeded, deterministic


def test_the_api_switch(monkeypatch):
    from app.core import config
    from tests.test_scanner_api import _h as _auth_headers
    from tests.test_auth_api import client
    bars = [b.model_dump(mode="json") for b in _walk(np.random.default_rng(9))]
    body = {"symbols": [{"symbol": "A", "candles": bars}], "indicator_conditions": [
        {"left": {"type": "indicator", "indicator": "CLOSE"}, "operator": "GT", "right": {"type": "indicator", "indicator": "SMA", "period": 20}}]}
    headers = _auth_headers()
    legacy = client.post("/api/scanner/run", json=body, headers=headers).json()
    monkeypatch.setattr(config, "SCANNER_ENGINE", "screenql")
    called = []
    original = screenql.run_scanner_screenql
    monkeypatch.setattr(screenql, "run_scanner_screenql", lambda r: called.append(1) or original(r))
    new = client.post("/api/scanner/run", json=body, headers=headers).json()
    assert new == legacy and called == [1]
