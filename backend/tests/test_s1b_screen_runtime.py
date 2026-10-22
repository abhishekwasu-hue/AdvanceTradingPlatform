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
    # strategy templates E2 (TPL-7): period= left out is RSI 14; the divergence's prices held from its confirmation bar
    assert _ev('RsiDivergence("bullish", 60)', walk).equals(_ev('RsiDivergence("bullish", 60, 0, period=14)', walk))
    for side in ("bullish", "bearish"):
        ext = _ev(f'RsiDivergenceRef("extreme", "{side}", 0, period=5)', walk)
        far = _ev(f'RsiDivergenceRef("confirm_bar", "{side}", 0, period=5)', walk)
        assert ext.notna().any() and (ext.notna() == far.notna()).all(), side
        rising = _ev(f'RsiDivergence("{side}", 300, 0, period=5)', walk)
        first = int(np.flatnonzero(rising.to_numpy())[0])
        assert ext.iloc[:first].isna().all() and ext.iloc[first:].notna().all(), side



def test_wave_count_functions_w1():
    # deeper checks (per count type, causality, mirror, the flag) are in tests/test_wave2_counter.py / test_wave2_recipe.py
    from app.price_action.waves import counter as wc
    walk = _sym(list(100 + np.cumsum(np.random.default_rng(4).normal(0, 1, 600))))
    state, typ = _ev("WaveState(1)", walk), _ev("WaveType(1)", walk)
    alt, clarity = _ev("WaveAlt(1)", walk), _ev("WaveClarity(1)", walk)
    assert state.notna().any() and set(state.dropna()) <= set(wc.STATES) and set(typ.dropna()) <= set(wc.TYPES)
    assert set(alt.dropna()) <= set(wc.STATES) and set(clarity.dropna()) <= set(wc.CLARITY)


def test_wave_levels_lines_and_retest_w2_w4():
    # deeper checks (per count type, causality, mirror, the flag, RetestEntry identity) are in tests/test_wave3_levels_lines.py
    walk = _sym(list(100 + np.cumsum(np.random.default_rng(4).normal(0, 1, 600))))
    for text in ("WaveOrigin(1)", 'WaveEnd(1, "current")', "WaveInvalidation(1)", "WaveRetrace(1, 0.5)", 'WaveProjection(1, "previous", 1)',
                 "WaveRatio(1)", 'WaveLineLevel(1, "channel_current")'):
        v = _ev(text, walk)
        assert v.dtype == float and v.notna().any(), text
    assert _ev("WaveCap(1)", walk).dtype == float
    for text in ("WaveBeyondOrigin(1)", 'WaveLineBreak(1, "channel_current", "up", 0)',
                 'RetestEntry("up", "wave_line", 20, FALSE, 1, "channel_current", "A", 0)'):
        assert _ev(text, walk).dtype == bool, text
    # the wave companions (deeper checks in tests/test_wave6_companions.py): the volume, the zone edge, the hidden divergence
    assert _ev('WaveVolumeLower(1, "current")', walk).dtype == bool
    edge = _ev('SwingZoneEdge("low", 0, 1)', walk)
    assert edge.dtype == float and edge.notna().any()
    assert _ev('RsiDivergence("bullish", 60, 0, kind="regular")', walk).equals(_ev('RsiDivergence("bullish", 60)', walk))
    assert _ev('RsiDivergence("bullish", 60, 0, kind="hidden")', walk).dtype == bool


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


def test_tpl1_indicator_functions():
    # deeper checks (reference parity, truncation, bounds, the argument order) are in tests/test_tpl1_indicators.py
    d = _sym([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])                      # high = close + 1, low = close - 1
    assert np.isnan(_ev("WMA(close, 3)", d).iloc[1]) and _ev("WMA(close, 3)", d).iloc[2] == pytest.approx(14 / 6)
    hull = _ev("HMA(close, 4)", d)                                 # a straight line: the Hull average has no lag
    assert hull.iloc[:4].isna().all() and hull.iloc[4:].tolist() == pytest.approx([5.0, 6.0])
    flat = _sym([10.0] * 8)
    assert _ev("MACD(2, 3, 2)", flat).iloc[2:].tolist() == [0.0] * 6 and np.isnan(_ev("MACDSignal(2, 3, 2)", flat).iloc[2])
    assert _ev("MACDHist(2, 3, 2)", flat).iloc[3:].tolist() == [0.0] * 5
    s = _sym([10.0, 11.0, 12.0, 13.0])
    assert _ev("StochK(3, 1)", s).iloc[2:].tolist() == [75.0, 75.0] and _ev("StochD(3, 1, 2)", s).iloc[-1] == 75.0
    assert _ev("PSAR(0.02, 0.02, 0.2)", s).iloc[1:].tolist() == pytest.approx([9.0, 9.0, 9.16])
    assert _ev("PSARDir()", s).iloc[1:].tolist() == [1.0, 1.0, 1.0] and np.isnan(_ev("PSARDir()", s).iloc[0])
    up = _ev("SupertrendDir(3, 1)", d)
    assert up.iloc[:3].isna().all() and up.iloc[3:].tolist() == [1.0, 1.0, 1.0]


def test_tpl2_moving_average_and_breakout_companions():
    """Strategy templates step 2: MA(x, n, type) is exactly SMA / EMA / WMA / HMA; RangeBreakoutRef / LineBreakoutRef give
    the breakout's prices on its signal bars only (R2), held_level holds the level forward, the stop references are the
    quality overlay's (level - stop_mult x ATR of the bar before, the base's far side, its middle), mirrored down."""
    d = _sym([10, 11, 13, 12, 15, 14, 16, 18, 17, 19, 21, 20])
    for kind, plain in (("SMA", "SMA(close, 4)"), ("EMA", "EMA(close, 4)"), ("WMA", "WMA(close, 4)"), ("HMA", "HMA(close, 4)")):
        pd.testing.assert_series_equal(_ev(f'MA(close, 4, "{kind}")', d), _ev(plain, d), check_names=False)
    pd.testing.assert_series_equal(_ev("MA(close, 4)", d), _ev("EMA(close, 4)", d), check_names=False)    # EMA when left out
    up = _sym([100.0] * 60 + [110.0, 111.0], vol=[100.0] * 62)          # a flat range (high 101, low 99, ATR 2), then a break
    assert _ev('RangeBreakout("up", 20, 0, 0)', up).iloc[-3:].tolist() == [False, True, False]
    want = {"level": 101.0, "fail_level": 101.0, "base_high": 101.0, "base_low": 99.0, "stop_level_atr": 99.0, "stop_base": 99.0,
            "stop_midpoint": 100.0}
    for what, value in want.items():
        v = _ev(f'RangeBreakoutRef("{what}", "up", 20, 0, 0)', up)
        assert v.iloc[-2] == pytest.approx(value) and v.drop(v.index[-2]).isna().all(), what
    assert _ev('RangeBreakoutRef("stop_level_atr", "up", 20, 0, 0, "fixed", 2)', up).iloc[-2] == pytest.approx(97.0)
    held = _ev('RangeBreakoutRef("held_level", "up", 20, 0, 0)', up)
    assert held.iloc[:-2].isna().all() and held.iloc[-2:].tolist() == [101.0, 101.0]
    down = _sym([100.0] * 60 + [90.0, 89.0], vol=[100.0] * 62)
    for what, value in {"level": 99.0, "stop_level_atr": 101.0, "stop_base": 101.0, "stop_midpoint": 100.0}.items():
        assert _ev(f'RangeBreakoutRef("{what}", "down", 20, 0, 0)', down).iloc[-2] == pytest.approx(value), what
    assert _ev('RangeBreakoutRef("level", "down", 20, 0, 0)', up).isna().all()
    rng = np.random.default_rng(3)
    walk = _sym(list(100 + np.cumsum(rng.normal(0, 1, 400))))
    for side in ("up", "down"):
        fired = _ev(f'TrendlineBreakout("{side}", "channel", 2, 0)', walk)
        level = _ev(f'LineBreakoutRef("level", "{side}", "channel", 2, 0)', walk)
        assert fired.any() and (level.notna() == fired).all(), side
        stop = _ev(f'LineBreakoutRef("stop_level_atr", "{side}", "channel", 2, 0, 0)', walk)
        assert np.allclose(stop[fired], level[fired])                  # no buffer: the broken edge itself


def test_tpl3_retest_companion_and_the_forwarded_breakout_settings():
    """Strategy templates step 3 (R7): RetestEntryRef gives the B9 retest's prices on its retest bars only; RetestEntry and
    RetestEntryRef take the base breakout's own settings as named arguments (left out: the defaults, as before)."""
    from tests.breakout_bk4_fixtures import retest_rows, rows_frame
    f = rows_frame(retest_rows())
    data = SymbolData("S", {"1d": f})
    ev = lambda text: evaluate(parse(text), data, base_tf="1d")  # noqa: E731
    COVERED.update({"RetestEntry", "RetestEntryRef"})
    fired = ev('RetestEntry("up", "range", 10, TRUE)')
    assert fired.tolist()[-1] and fired.sum() == 1
    assert ev('RetestEntry("up", "range", 10, TRUE, k=0.2, n=20, v=1.5, mode="fixed")').equals(fired)   # the defaults, named
    assert not ev('RetestEntry("up", "range", 10, TRUE, v=5)').any()                                     # the breakout's own volume rule
    level = ev('RetestEntryRef("level", "up", "range", 10, TRUE)')
    assert level.notna().sum() == 1 and level.iloc[-1] == pytest.approx(f["high"].iloc[:40].iloc[-20:].max())
    assert ev('RetestEntryRef("retest_low", "up", "range", 10, TRUE)').iloc[-1] == pytest.approx(f["low"].iloc[41:].min())
    assert ev('RetestEntryRef("held_level", "up", "range", 10, TRUE)').iloc[-1] == pytest.approx(level.iloc[-1])
    assert ev('RetestEntryRef("level", "down", "range", 10, TRUE)').isna().all()
    # step 5 (C2): swing= is the swing breakout's degree (the default 1 named = left out; another base ignores it)
    assert ev('RetestEntry("up", "swing", 10, TRUE, swing=1)').equals(ev('RetestEntry("up", "swing", 10, TRUE)'))
    assert ev('RetestEntry("up", "range", 10, TRUE, swing=0)').equals(fired)


def test_tpl4_breakout_family_companions():
    """Strategy templates step 4 (R2 for the other breakout families): each companion gives its breakout's prices on that
    breakout's signal bars only, held_level holds the level forward (tests/test_tpl4_templates.py checks every output
    against the breakout screen's own columns and the stop reference)."""
    from app.quality.lookahead import synthetic_bars
    walk = SymbolData("S", {"5m": synthetic_bars(600, tf="5m", seed=4)})
    for ref, base in (('SwingBreakoutRef("level", "{s}", 0, 0)', 'SwingBreakout("{s}", 0, 0)'),
                      ('NRBreakoutRef("fail_level", "{s}", "nr7", 0, 0)', 'NRBreakout("{s}", "nr7", 0, 0)'),
                      ('VolumeBreakoutRef("level", "{s}", 1.5, 10)', 'VolumeBreakout("{s}", 1.5, 10)'),
                      ('DonchianBreakoutRef("stop_midpoint", "{s}", 20, 0)', 'DonchianBreakout("{s}", 20, 0)'),
                      ('MABreakoutRef("level", "{s}", 10, "sma", 0)', 'MABreakout("{s}", 10, "sma", 0)'),
                      ('PatternBreakoutRef("base_low", "{s}", "triangle", 0, 0)', 'PatternBreakout("{s}", "triangle", 0, 0)')):
        for side in ("up", "down"):
            fired, level = _ev(base.format(s=side), walk), _ev(ref.format(s=side), walk)
            assert (level.notna() == fired).all(), (ref, side)
        assert _ev(base.format(s="up"), walk).any() or _ev(base.format(s="down"), walk).any(), base
    up = _sym([100.0] * 60 + [110.0, 111.0], vol=[100.0] * 62)          # a flat channel (high 101, low 99), then a break
    assert _ev('DonchianBreakoutRef("stop_midpoint", "up", 20, 0)', up).iloc[-2] == pytest.approx(100.0)
    held = _ev('DonchianBreakoutRef("held_level", "up", 20, 0)', up)
    assert held.iloc[:-2].isna().all() and held.iloc[-2:].tolist() == [101.0, 101.0]
    from tests.breakout_bk3_fixtures import vcp
    f = vcp()
    COVERED.add("VCPBreakoutRef")
    v = evaluate(parse('VCPBreakoutRef("stop_base", "up")'), SymbolData("S", {"1d": f}), base_tf="1d")
    assert v.notna().sum() == 1 and v.iloc[-1] < f["close"].iloc[-1]


def test_tpl8_gap_companion():
    """Strategy templates TPL-8 (R2 for the gap family): GapBreakoutRef gives the B8 gap's prices on the gap's signal bars
    only, on the same session clock (tests/test_tpl8_b10_gap.py checks every output against the screen's own columns)."""
    from app.quality.lookahead import synthetic_bars
    walk = SymbolData("S", {"5m": synthetic_bars(3000, tf="5m", seed=1)})
    seen = 0
    for side in ("up", "down"):
        fired = _ev(f'GapBreakout("{side}", "prev_day", 30)', walk)
        level = _ev(f'GapBreakoutRef("level", "{side}", "prev_day", 30)', walk)
        assert (level.notna() == fired).all(), side
        held = _ev(f'GapBreakoutRef("held_level", "{side}", "prev_day", 30)', walk)
        assert held[fired].tolist() == level[fired].tolist()
        seen += int(fired.sum())
    assert seen, "the fixture must carry gaps"


def test_every_registry_entry_has_a_runtime_test():
    # runs last in this module (pytest keeps file order): every field and function was exercised above
    missing = (set(FUNCTIONS) | set(FIELDS)) - COVERED
    assert missing == set(), f"registry entries without a runtime fixture test: {sorted(missing)}"
