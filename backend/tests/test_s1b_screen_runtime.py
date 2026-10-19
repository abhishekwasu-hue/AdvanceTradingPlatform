"""S1b (ADR-0021 §2, §4): the ScreenQL runtime - one fixture test per registry entry (hand-computed values on small
deterministic bars), the higher-timeframe look-ahead guard, cross-sectional ranks, and run_screen over a universe."""
import numpy as np
import pandas as pd
import pytest

from app.screener import compile_screen, parse
from app.screener.registry import FIELDS, FUNCTIONS
from app.screener.runtime import ScreenRuntimeError, SymbolData, evaluate, resample, run_screen

COVERED = set()


def _bars(closes, start="2026-03-02 03:45", freq="5min", vol=None):
    idx = pd.date_range(start, periods=len(closes), freq=freq, tz="UTC")
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame({"open": c - 0.5, "high": c + 1.0, "low": c - 1.0, "close": c,
                         "volume": np.asarray(vol if vol is not None else [100.0] * len(c), dtype=float)}, index=idx)


def _ev(text, data, base_tf="5m", **params):
    for name in set(FUNCTIONS) | set(FIELDS):
        if name in text:
            COVERED.add(name)
    return evaluate(parse(text), data, base_tf=base_tf, params=params)


def _sym(closes, **kw):
    return SymbolData("S", {"5m": _bars(closes, **{k: v for k, v in kw.items() if k in ("vol", "freq", "start")})},
                      **{k: v for k, v in kw.items() if k not in ("vol", "freq", "start")})


def test_fields_and_offsets():
    d = _sym([10, 11, 12, 13], vol=[1, 2, 3, 4])
    for name, expected in (("open", 12.5), ("high", 14.0), ("low", 12.0), ("close", 13.0), ("volume", 4.0)):
        assert _ev(name, d).iloc[-1] == expected
    assert _ev("close[1]", d).iloc[-1] == 12.0 and np.isnan(_ev("close[4]", d).iloc[-1])
    assert _ev("oi", d).isna().all()                                                        # no OI column: NaN, never a match


def test_moving_windows():
    d = _sym([1, 2, 3, 4, 5, 6])
    assert _ev("SMA(close, 3)", d).iloc[-1] == 5.0 and np.isnan(_ev("SMA(close, 3)", d).iloc[1])
    ema = _ev("EMA(close, 3)", d)
    assert np.isnan(ema.iloc[1]) and round(ema.iloc[-1], 4) == round(pd.Series([1, 2, 3, 4, 5, 6.0]).ewm(span=3, adjust=False).mean().iloc[-1], 4)
    assert _ev("Max(high, 3)", d).iloc[-1] == 7.0 and _ev("Min(low, 3)", d).iloc[-1] == 3.0
    assert _ev("Lag(close, 2)", d).iloc[-1] == 4.0
    assert round(_ev("PctChange(close, 2)", d).iloc[-1], 6) == round((6 - 4) / 4 * 100, 6)
    z = _ev("ZScore(close, 3)", d).iloc[-1]
    assert round(z, 6) == round((6 - 5) / pd.Series([4, 5, 6.0]).std(), 6)
    assert np.isnan(_ev("ZScore(close, 3)", _sym([5, 5, 5])).iloc[-1])                      # zero spread: NaN, not inf


def test_oscillators_and_vwap_match_the_indicator_library():
    from app.indicators.directional import adx
    from app.indicators.momentum import rsi
    from app.indicators.volatility import atr
    from app.indicators.vwap import session_vwap
    closes = [100 + np.sin(i / 2) * 3 + i * 0.1 for i in range(60)]
    d = _sym(closes, vol=[100 + i for i in range(60)])
    f = d.frames["5m"]
    assert _ev("RSI(14)", d).iloc[-1] == rsi(f["close"], 14).iloc[-1] and _ev("RSI()", d).iloc[-1] == rsi(f["close"], 14).iloc[-1]
    assert _ev("ADX(10)", d).iloc[-1] == adx(f, 10)["adx"].iloc[-1]
    assert _ev("ATR(5)", d).iloc[-1] == atr(f, 5).iloc[-1]
    assert _ev("VWAP()", d).iloc[-1] == session_vwap(f).iloc[-1]


def test_greatest_least_count_streak_and_crosses():
    d = _sym([5, 4, 6, 7, 8, 3])
    assert _ev("Greatest(open, close)", d).iloc[-1] == 3.0 and _ev("Least(open, close)", d).iloc[-1] == 2.5
    assert _ev("Count(4, close > close[1])", d).iloc[-1] == 3.0
    assert list(_ev("CountStreak(close > close[1])", d)) == [0, 0, 1, 2, 3, 0]
    a = _sym([1, 2, 3, 4, 5, 6])
    up = _ev("CrossAbove(close, 3.5)", a)
    assert list(up) == [False, False, False, True, False, False]
    assert list(_ev("CrossBelow(close, 3.5)", _sym([6, 5, 4, 3, 2]))) == [False, False, False, True, False]
    assert not _ev("CrossAbove(close, SMA(close, 50))", a).any()                           # not enough history: never a cross


def test_classifiers_and_filters():
    d = _sym([1, 2], sector="IT", industry="Software", mcap_bucket="LARGE", is_fno=True, indices={"NIFTY 50", "NIFTY 200"})
    assert _ev("Sector()", d) == "IT" and _ev("Industry()", d) == "Software" and _ev("McapBucket()", d) == "LARGE"
    assert _ev("IsFnO()", d) is True and _ev('IndexMember("NIFTY 200")', d) is True and _ev('IndexMember("NIFTY BANK")', d) is False
    unknown = _sym([1, 2])
    assert _ev('Sector() == "IT"', unknown) is False and _ev("IsFnO()", unknown) is False   # missing reference data never matches


def test_operators_nan_and_division():
    d = _sym([10, 20, 30])
    assert list(_ev("close BETWEEN 15 AND 30", d)) == [False, True, True]
    assert list(_ev("close / (close - close) > 1", d)) == [False, False, False]              # x/0 -> NaN -> no match
    assert list(_ev("NOT close > 15", d)) == [True, False, False]
    assert list(_ev("close > 15 OR volume > 1000", d)) == [False, True, True]
    assert list(_ev("close IN (10, 30)", d)) == [True, False, True]
    assert list(_ev("-close < -15", d)) == [False, True, True]
    assert _ev("close > $x", d, x=25).iloc[-1]


def test_higher_timeframe_values_appear_only_after_their_bar_closes():
    # two IST sessions of 5-minute bars; day 1 closes rise, day 2 starts lower
    day1 = _bars([100 + i for i in range(75)], start="2026-03-02 03:45")
    day2 = _bars([50 + i for i in range(75)], start="2026-03-03 03:45")
    base = pd.concat([day1, day2])
    d = SymbolData("S", {"5m": base})
    daily_close = _ev("close@1d", d)
    first_day = daily_close[daily_close.index < "2026-03-03"]
    assert first_day.isna().all()                                                           # day 1 has not closed while it is trading
    second_day = daily_close[daily_close.index >= "2026-03-03"]
    assert (second_day == 174.0).all()                                                     # day 1's close, never day 2's own (still open)
    assert len(resample(base, "5m", "1d")) == 1                                             # the trading day is dropped as incomplete
    assert _ev("close@1d[1]", d).dropna().empty                                             # yesterday's yesterday: no data yet


def test_screen_over_a_universe_with_cross_sectional_ranks():
    def sym(name, closes, sector):
        return SymbolData(name, {"1d": _bars(closes, freq="1D", start="2026-01-01")}, sector=sector)
    universe = [sym("A", [100, 110, 121], "IT"), sym("B", [100, 101, 102], "IT"), sym("C", [100, 90, 80], "BANK"), sym("D", [100, 120, 150], "BANK")]
    ast, v = compile_screen("Rank(PctChange(close, 2)) <= 2", base_tf="1d")
    assert v.ok and v.cross_sectional
    assert [m.symbol for m in run_screen(ast, v, universe, base_tf="1d") if m.matched] == ["A", "D"]
    COVERED.update({"Rank", "PercentileRank"})
    ast2, v2 = compile_screen("PercentileRank(PctChange(close, 2), by=Sector()) == 100", base_tf="1d")
    assert [m.symbol for m in run_screen(ast2, v2, universe, base_tf="1d") if m.matched] == ["A", "D"]   # top of each sector
    short = [sym("E", [100, 101], "IT")]
    ast3, v3 = compile_screen("close > SMA(close, 3)", base_tf="1d")
    res = run_screen(ast3, v3, short, base_tf="1d")
    assert not res[0].matched and "not enough history" in res[0].reason
    with pytest.raises(ScreenRuntimeError):
        run_screen(ast3, compile_screen("close >", base_tf="1d")[1], universe, base_tf="1d")


def test_offset_and_timeframe_in_either_order():
    assert parse("close@1d[1] > 1") == parse("close[1]@1d > 1")
    for bad in ("close[1][2] > 1", "close@1d@1w > 1"):
        with pytest.raises(Exception, match="twice"):
            parse(bad)


def test_strategy_builder_and_scanner_entries_match_their_source():
    from app.brokers.models import OptionChain, OptionChainRow
    from app.option_chain.analysis import analyze_option_chain
    from app.price_action.market_structure import analyze_market_structure
    from app.strategy_engine.declarative import Operand
    rng = np.random.default_rng(5)
    day1 = _bars(list(100 + rng.normal(0, 0.5, 75).cumsum()), start="2026-03-02 03:45")
    day2 = _bars(list(101 + rng.normal(0, 0.5, 75).cumsum()), start="2026-03-03 03:45")
    f = pd.concat([day1, day2])
    rows = [OptionChainRow(strike=s, call_oi=c, put_oi=p) for s, c, p in ((90.0, 50.0, 80.0), (100.0, 60.0, 70.0), (110.0, 90.0, 20.0))]
    chain = OptionChain(underlying="X", expiry="2026-03-26", underlying_ltp=101.0, rows=rows)
    d = SymbolData("S", {"5m": f}, option_chain=chain)

    def op(ind, period=14, mult=3.0):
        return Operand(type="indicator", indicator=ind, period=period, multiplier=mult).series(f)
    pairs = {"PlusDI(10)": op("PLUS_DI", 10), "MinusDI(10)": op("MINUS_DI", 10), "Supertrend(7, 2.5)": op("SUPERTREND", 7, 2.5),
             "BBUpper(20, 2)": op("BB_UPPER", 20, 2.0), "BBMid(20, 2)": op("BB_MID", 20, 2.0), "BBLower(20, 2)": op("BB_LOWER", 20, 2.0),
             "DayOpen()": op("DAY_OPEN"), "PDH()": op("PDH"), "PDL()": op("PDL"), "PDC()": op("PDC"), "ORHigh(15)": op("OR_HIGH", 15),
             "ORLow(15)": op("OR_LOW", 15), "VWAP()": op("VWAP")}
    for text, expected in pairs.items():
        got = _ev(text, d)
        pd.testing.assert_series_equal(got.astype(float), expected.astype(float), check_names=False, obj=text)
    structure = analyze_market_structure(f, window=3)
    assert _ev("Trend(3)", d) == structure.trend.value
    latest = structure.events[-1] if structure.events else None
    assert _ev("StructureEvent(3)", d) == (f"{latest.event}_{latest.direction}".upper() if latest else "")
    assert isinstance(_ev("PatternBullish()", d), bool) and isinstance(_ev("PatternBearish()", d), bool)
    assert isinstance(_ev("NearSupport(0.5)", d), bool) and isinstance(_ev("NearResistance(0.5, 3)", d), bool)
    a = analyze_option_chain(chain)
    assert _ev("PCR()", d) == a.pcr and _ev("ChainBias()", d) == a.bias.value
    assert round(_ev("MaxPainDistancePct()", d), 6) == round(abs(101.0 - a.max_pain) / 101.0 * 100, 6)
    bare = SymbolData("S", {"5m": f})
    assert np.isnan(_ev("PCR()", bare)) and _ev('ChainBias() == "BULLISH"', bare) is False         # no chain: never a match


def test_price_action_series_s5a():
    # deeper checks (detector parity, confirmation bar, truncation invariance) are in tests/test_s5a_price_action.py
    closes = [100 + 3 * np.sin(i / 6) for i in range(160)]
    data = _sym(closes)
    assert _ev('Pattern("doji")', data).dtype == bool
    hi, lo = _ev("SwingHigh()", data), _ev("SwingLow(0)", data)
    assert hi.notna().any() and lo.notna().any() and (hi.dropna() > lo.dropna().min()).all()
    assert set(_ev("SwingDirection()", data).dropna().unique()) <= {"UP", "DOWN"}
    assert _ev("MedianRange(5)", data).iloc[-1] == pytest.approx(2.0)          # high - low is 2 on every bar
    assert _ev('ReversalAt(SwingLow(), "bullish")', data).dtype == bool        # S5-A2 (deeper checks: test_s5a2_reversal.py)
    assert _ev('RealBreak(SwingLow(), "below")', data).dtype == bool         # S5-A3 (deeper checks: test_s5a3_breaks.py)
    zs = _ev('SwingZoneStrength("low")', data)                                  # S5-A4 (test_s5a4_zone_strength.py)
    assert zs.notna().any() and zs.dropna().between(0, 100).all()
    assert _ev('SwingZoneDistance("high")', data).notna().any()


def test_breakout_series_u6_bk1():
    # deeper checks (fixtures per screen, overlay, parity with the engine) are in tests/test_z7_bk1_breakouts.py
    closes = [100 + 3 * np.sin(i / 6) + i * 0.05 for i in range(200)]
    data = _sym(closes)
    for text in ('RangeBreakout("up")', 'RangeBreakout("down", 10, 0, 0, "auto")', 'SwingBreakout("up", 0)', 'NRBreakout("up", "inside", 0)',
                 'NewHighBreakout("up", 1, 0)', 'RangeExpansion("down", 1, 0)', 'VolumeBreakout("up", 0.5)'):
        assert _ev(text, data).dtype == bool, text
    jump = _sym([100.0] * 60 + [104.0])                                         # closes 3 above the 10-bar high (101)
    assert bool(_ev('RangeBreakout("up", 10, 0, 0)', jump).iloc[-1]) and not _ev('RangeBreakout("up", 10, 0, 0)', jump).iloc[:-1].any()
    assert set(_ev('SwingBreakType("up", 0)', data).unique()) <= {"BOS", "CHOCH", "BREAK", ""}
    assert _ev("RelVolume(5)", data).dropna().eq(1.0).all()                    # constant volume
    assert _ev("ATRPercentile(14, 50)", data).dropna().between(0, 100).all()
    assert set(_ev("DowTrend(0)", data).dropna().unique()) <= {"UP", "DOWN", "RANGE"}


def test_intraday_breakout_series_u6_bk2():
    # deeper checks (fixtures per screen, half days, no look-ahead, parity with the engine) are in tests/test_z7_bk2_intraday.py
    days = pd.bdate_range("2026-03-02", periods=8, tz="UTC")
    frame = pd.concat([_bars([100 + 2 * np.sin(i / 9) for i in range(75)], start=str(d + pd.Timedelta(hours=3, minutes=45)), vol=[1000.0] * 75)
                       for d in days])                                          # eight 09:15-15:30 sessions
    data = SymbolData("S", {"5m": frame})
    for text in ('ORBreakout("up", 15, 0, FALSE, 15, 0)', 'DayLevelBreak("down", "prev_day", 0, FALSE)', 'DayExtremeBreak("up", 0, FALSE, 0, 0)'):
        assert _ev(text, data).dtype == bool, text
    assert _ev("RelVolumeTOD(5)", data).dropna().eq(1.0).all()                # constant volume
    assert _ev("SessionVWAP()", data).notna().any()


def test_vcp_and_indicator_breakout_series_u6_bk3():
    # deeper checks (VCP fixtures, squeeze / MA / regime fixtures, no look-ahead, parity with the engine) are in
    # tests/test_z7_bk3_breakouts.py
    closes = [100 + 3 * np.sin(i / 6) + i * 0.05 for i in range(300)]
    data = _sym(closes)
    for text in ('VCPBreakout("up")', 'VCPReady("up", 2, 1, 5, FALSE, FALSE)', 'DonchianBreakout("down", 55)',
                 'SqueezeBreakout("up", "ttm")', 'MABreakout("up", 20, "ema", 0)', 'RegimeBreakout("down")'):
        assert _ev(text, data).dtype == bool, text
    jump = _sym([100.0] * 60 + [104.0])                                         # closes 3 above the 20-bar high (101)
    assert bool(_ev('DonchianBreakout("up", 20, 0)', jump).iloc[-1]) and not _ev('DonchianBreakout("up", 20, 0)', jump).iloc[:-1].any()


def test_trendline_pattern_gap_and_retest_series_u6_bk4():
    # deeper checks (fixtures per screen, the trendline port parity, no look-ahead, parity with the engine) are in
    # tests/test_z7_bk4_breakouts.py
    closes = [100 + 3 * np.sin(i / 6) + i * 0.05 for i in range(300)]
    data = _sym(closes)
    for text in ('TrendlineBreakout("up")', 'TrendlineBreakout("down", "channel", 2, 0)', 'PatternBreakout("up", "triangle", 0)',
                 'PatternBreakout("down", "flag", 0, 0)', 'GapBreakout("up", "prev_day", 15)', 'RetestEntry("up", "range", 10, FALSE)'):
        assert _ev(text, data).dtype == bool, text
    assert set(_ev('GapType("up", "prev_day", 15)', data).unique()) <= {"GAP_AND_GO", "GAP_HOLD", "GAP_FILL", ""}


def test_market_zone_functions_z7a3():
    # deeper checks (as-of per bar, no look-ahead, text vs engine, tenant scope) are in tests/test_zones_chart_screenql.py
    from datetime import datetime, timezone
    from app.price_action.zones.read import Snapshot, ZoneTimeline
    d = _sym([100.0, 100.2, 100.4, 100.6])                                   # 5m bars from 03:45 UTC; the third closes at 04:00
    zone = {"zone_key": "z", "zone_type": "DEMAND_ZONE", "timeframe": "1h", "zone_low": 99.6, "zone_high": 99.9, "state": "tested",
            "touches": 1, "strength": None, "remembered": False}
    seen_from = pd.Timestamp("2026-03-02 04:00", tz="UTC")
    d.zones = ZoneTimeline("NSE:S", np.array([seen_from.value], dtype="int64"),
                           [Snapshot(1, datetime(2026, 3, 2, 4, 0, tzinfo=timezone.utc), "platform", 100.0, [zone])])
    assert _ev('NearZone("demand", 0.5)', d).tolist() == [False, False, True, False]   # not before its as_of; 0.69 % away at 100.6
    assert _ev("ZoneRole(1.0)", d).tolist() == [None, None, "DEMAND", "DEMAND"]
    assert _ev('ZoneState("supply", 1.0)', d).isna().all() and _ev('ZoneState("any", 1.0, "zones")', d).iloc[-1] == "TESTED"
    assert _ev("ZoneStrength()", d).iloc[2] == 60.0 and np.isnan(_ev('ZoneStrength("any", 0.5, "levels")', d).iloc[2])


def test_fundamentals_functions_u7_lg3():
    # deeper checks (the store's known_from rules, restatements, no look-ahead, the cards) are in tests/test_n3_lg3_fundamentals.py
    from datetime import date, datetime, timezone
    from app.fundamentals.pit import Version
    d = _sym([100.0, 100.2, 100.4, 100.6])                                   # the third bar closes at 04:00
    seen = datetime(2026, 3, 2, 4, 0, tzinfo=timezone.utc)
    ends = [date(2024, 12, 31), date(2025, 3, 31), date(2025, 6, 30), date(2025, 9, 30)]
    d.fundamentals = [Version("QUARTER", f"Q{i}", e, 1, {"eps": 10.0, "revenue": 1000.0, "ebit": 200.0}, seen) for i, e in enumerate(ends)]
    d.fundamentals.append(Version("QUARTER", "Q4", date(2025, 12, 31), 1,
                                  {"eps": 12.0, "revenue": 1100.0, "ebit": 220.0, "shares_outstanding": 10.0, "shareholders_equity": 400.0,
                                   "total_assets": 900.0, "current_assets": 600.0, "current_liabilities": 100.0, "cash_and_equivalents": 50.0,
                                   "total_debt": 100.0}, seen))
    assert _ev("EPSGrowthYoY()", d).iloc[:2].isna().all()                    # not before the bar that closes at 04:00
    assert _ev("EPSGrowthYoY()", d).iloc[2:].tolist() == [20.0, 20.0] and np.isnan(_ev("SalesGrowthYoY()", d).iloc[0])
    assert _ev("SalesGrowthYoY()", d).iloc[-1] == pytest.approx(10.0)
    assert _ev("ReturnOnCapital()", d).iloc[-1] == pytest.approx(820 / (900 - 100 - 50) * 100)
    assert _ev("EarningsYield()", d).iloc[-1] == pytest.approx(820 / (100.6 * 10 + 100 - 50) * 100)
    assert _ev("GrahamNumber()", d).iloc[-1] == pytest.approx((22.5 * 42 * 40) ** 0.5)
    assert _ev("NCAVPerShare()", d).iloc[-1] == pytest.approx((600 - (900 - 400)) / 10)


def test_amw3_functions_amw_port():
    # deeper checks (hand-built bars, parity, the flag gate, the vectorised path) are in tests/test_amw3_screenql_functions.py
    d = _sym([1.0, 2.0, 3.0, 4.0])
    assert _ev("StDev(close, 4)", d).iloc[-1] == pytest.approx(np.std([1.0, 2.0, 3.0, 4.0]))            # population (ddof 0)
    assert np.isnan(_ev("StDev(close, 4)", d).iloc[-2])
    on = _ev('TimeOfDay("09:15", "09:20")', d)                                 # 5m bars from 03:45 UTC = 09:15 IST
    assert on.tolist() == [True, True, False, False]
    walk = _sym(list(100 + np.cumsum(np.random.default_rng(4).normal(0, 1, 300))))
    labels = _ev('SwingLabel("high")', walk)
    assert set(labels.dropna()) <= {"HH", "LH"} and labels.notna().any()
    assert _ev('RsiDivergence("bullish", 60)', walk).dtype == bool


def test_rankscore_n3_s7a():
    # deeper checks (scoring, no look-ahead against cut bars, the routes) are in tests/test_n3_s7a_rankings.py
    from app.screener import ranking as RK
    up = SymbolData("UP", {"1d": _bars([100 + i for i in range(30)], start="2026-01-01", freq="1D")})
    down = SymbolData("DOWN", {"1d": _bars([130 - i for i in range(30)], start="2026-01-01", freq="1D")})
    system = RK.parse("t", {"nodes": [{"name": "r", "factor": "PctChange(close, 10)", "weight": 100}]})
    ast, v = compile_screen('RankScore("t") == 100', base_tf="1d")
    assert v.ok
    assert {m.symbol: m.matched for m in RK.within({"t": system}, run_screen, ast, v, [up, down], base_tf="1d")} == {"UP": True, "DOWN": False}
    COVERED.add("RankScore")


def test_ownership_functions_n3_s8a():
    # deeper checks (point in time, corrections, staleness, cut bars, the store, the routes) are in tests/test_n3_s8a_ownership.py
    from datetime import date, datetime, timezone
    from app.screener.ownership import Event, Ownership, Pattern
    d = _sym([100.0, 100.2, 100.4, 100.6])                                   # 5m bars from 03:45 UTC; the second closes at 03:55
    seen = datetime(2026, 3, 2, 3, 55, tzinfo=timezone.utc)
    stakes = {"promoter": 50.0, "pledge": 2.0, "fii": 20.0, "dii": 10.0, "public": 20.0}
    d.ownership = Ownership([Pattern(date(2025, 12, 31), datetime(2026, 1, 20, tzinfo=timezone.utc), stakes),
                             Pattern(date(2026, 3, 1), seen, {**stakes, "promoter": 52.5})], [Event("results", date(2026, 3, 5), seen)])
    assert _ev('Holding("promoter")', d).tolist() == [50.0, 52.5, 52.5, 52.5]
    assert np.isnan(_ev('HoldingChange("promoter", 1)', d).iloc[0]) and _ev('HoldingChange("promoter", 1)', d).iloc[1] == 2.5
    assert np.isnan(_ev('DaysToEvent("results")', d).iloc[0]) and _ev('DaysToEvent("any")', d).iloc[-1] == 3.0


def test_every_registry_entry_has_a_runtime_test():
    # runs last in this module (pytest keeps file order): every field and function was exercised above
    missing = (set(FUNCTIONS) | set(FIELDS)) - COVERED
    assert missing == set(), f"registry entries without a runtime fixture test: {sorted(missing)}"
