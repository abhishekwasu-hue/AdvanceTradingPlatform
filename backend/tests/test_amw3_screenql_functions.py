"""AMW-3 (docs/design/AMW_PORT.md section 4): ScreenQL functions for the AMW screener's predicates, flag
`screenql_amw_functions` (off).

The AMW tests are behaviour specifications, re-written here on ATP bars (AMW's code is never run): its candle v2 cases
(test_analysis_v2 / test_scanner_predicates: a gap up closing below the prior close is a bearish Gap Reversal, not a
bullish one; next-bar confirmation), its RSI divergence case (a fast fall, a bounce, a slow grind to a slightly lower
low: bullish, not bearish) and its "the predicate equals the analysis module" check (here: the series equals the rule
recomputed from the swings known at each prefix). Causality is covered by the Z10-a harness (app/quality/causal_cases.py).
"""
from __future__ import annotations

import asyncio
import warnings
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from app.price_action import candlestick_patterns as cp
from app.quality.lookahead import synthetic_bars
from app.screener import amw_functions as amw
from app.screener import compile_screen, panel, parse
from app.screener.registry import AMW_FLAG, AMW_FUNCTIONS, CONFIRM_STATES, FUNCTIONS, PATTERN_NAMES_V2, READS_HISTORY, describe
from app.screener.runtime import SymbolData, _swing_pivots, evaluate, run_screen
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner


def _ev(text, frame, tf="1d"):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        return evaluate(parse(text), SymbolData("S", {tf: frame}), base_tf=tf)


def _ohlc(path, spread=0.3, start="2025-01-01"):
    c = np.asarray(path, float)
    o = np.r_[c[0], c[:-1]]
    idx = pd.bdate_range(start, periods=len(c), tz="UTC")
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + spread, "low": np.minimum(o, c) - spread, "close": c, "volume": 1e6},
                        index=idx)


def _trend(n=60, slope=0.5):
    c = 100 + slope * np.arange(n) + np.sin(np.arange(n) / 3)
    return pd.DataFrame({"open": c - 0.2, "high": c + 1, "low": c - 1, "close": c, "volume": 1e6},
                        index=pd.bdate_range("2025-01-01", periods=n, tz="UTC"))


# ------------------------------------------------------------------------------------------------------ registry / flag
def test_the_flag_is_registered_off_and_names_every_new_function():
    from app.alerts import barclose
    from app.platform.controls import DEFAULT_OFF_FLAGS, FEATURE_FLAGS
    assert AMW_FLAG == amw.FLAG == barclose.AMW_FLAG == "screenql_amw_functions"
    assert AMW_FLAG in DEFAULT_OFF_FLAGS and "places no orders" in FEATURE_FLAGS[AMW_FLAG]
    assert set(AMW_FUNCTIONS) == {"StDev", "RsiDivergence", "SwingLabel", "TimeOfDay"}
    assert PATTERN_NAMES_V2 == cp.V2_PATTERN_NAMES and CONFIRM_STATES == cp.CONFIRM_STATES
    assert "StDev" in READS_HISTORY
    for name in ("Pattern", "PatternBullish", "PatternBearish"):
        assert "confirm" in describe()[name]["args"] and FUNCTIONS[name].flag is None     # the argument is gated, not the function
    assert set(PATTERN_NAMES_V2) <= set(describe()["Pattern"]["choices"]["name"])


def test_validation_forms_timeframes_and_history():
    def check(text, tf="5m"):
        _, v = compile_screen(text, base_tf=tf)
        return v
    assert check('TimeOfDay("09:30", "14:45")').ok and check('TimeOfDay("22:00", "02:00")').ok
    for bad in ('TimeOfDay("9:30", "14:45")', 'TimeOfDay("09:30", "24:00")', 'TimeOfDay("09:60", "10:00")', 'TimeOfDay("09:30", close)'):
        assert not check(bad).ok, bad
    assert "HH:MM" in check('TimeOfDay("9:30", "14:45")').problems[0].message
    assert not check('TimeOfDay("09:30", "14:45")', "1d").ok                                   # intraday only
    assert check('TimeOfDay($a, "14:45")', "5m").problems[0].message.startswith("parameter $a")
    assert compile_screen('TimeOfDay($a, "14:45")', base_tf="5m", params={"a": "10:00"})[1].ok
    # history: v2 candles read ATR(14) (15 bars), a status the bar after the pattern (+1)
    assert check('Pattern("hammer")').lookback["5m"] == 3 and check('Pattern("hammer", "confirmed")').lookback["5m"] == 4
    assert check('Pattern("bullish_strong_body")').lookback["5m"] == 15
    assert check('Pattern("bullish_gap_reversal", "failed")').lookback["5m"] == 16
    assert check('RsiDivergence("bullish")').lookback["5m"] == 100 and check('RsiDivergence("bearish", 15, 2)').lookback["5m"] == 600
    assert check('SwingLabel("low", 1) == "HL"').lookback["5m"] == 250
    # a neutral candle has no side to confirm; classifier values and choices are checked
    v = check('Pattern("doji", "confirmed")')
    assert not v.ok and "no direction" in v.problems[0].message
    assert check('Pattern("doji", "any")').ok and check('Pattern("inside_bar")').ok
    assert not check('Pattern("hammer", "maybe")').ok and not check('PatternBullish("later")').ok
    assert not check('SwingLabel("high") == "UP"').ok
    assert not check('RsiDivergence("up")').ok
    # Z10-a: a last-bar-only answer cannot be read back by StDev
    assert not check("StDev(PCR(), 5) > 0").ok


# ----------------------------------------------------------------------------------------------------------- StDev
def test_stdev_is_the_population_standard_deviation():
    f = _ohlc([1.0, 2.0, 4.0, 8.0, 16.0])
    s = _ev("StDev(close, 3)", f)
    assert np.isnan(s.iloc[:2]).all()
    np.testing.assert_allclose(s.iloc[2:].to_numpy(), [np.std([1, 2, 4]), np.std([2, 4, 8]), np.std([4, 8, 16])])
    assert (_ev("StDev(close, 1)", f) == 0).all()
    assert _ev("StDev(close, 5)@1w", _ohlc(np.arange(80.0) + 100)).notna().any()


# -------------------------------------------------------------------------------------------------------- candle v2
def test_a_gap_up_closing_below_the_prior_close_is_a_bearish_gap_reversal():
    """AMW test_candle_pattern_and_gap_predicates, on ATP bars."""
    df = _trend()
    prev = df["close"].iloc[-2]
    df.iloc[-1] = (prev + 3.0, prev + 3.3, prev - 1.5, prev - 1.2, 1e6)
    assert _ev('Pattern("bearish_gap_reversal")', df).iloc[-1]
    assert not _ev('Pattern("bullish_gap_reversal")', df).iloc[-1]
    m = cp.detect_gap_reversal(df, len(df) - 1)
    assert m is not None and m.pattern == "Gap Reversal" and m.direction == "BEARISH"
    up = df.copy()                                                                  # the mirror case: a gap down closing above
    up.iloc[-1] = (prev - 3.0, prev + 1.5, prev - 3.3, prev + 1.2, 1e6)
    assert _ev('Pattern("bullish_gap_reversal")', up).iloc[-1] and not _ev('Pattern("bearish_gap_reversal")', up).iloc[-1]
    small = df.copy()                                                               # a gap under 0.5 ATR is not one
    small.iloc[-1] = (prev + 0.1, prev + 0.4, prev - 1.5, prev - 1.2, 1e6)
    assert not _ev('Pattern("bearish_gap_reversal")', small).iloc[-1]


def test_a_strong_body_is_a_wide_bar_that_is_nearly_all_body():
    df = _trend()
    p = df["close"].iloc[-2]
    df.iloc[-1] = (p, p + 4.1, p - 0.1, p + 4.0, 1e6)                                 # body 4.0 of range 4.2, range > ATR
    assert _ev('Pattern("bullish_strong_body")', df).iloc[-1] and not _ev('Pattern("bearish_strong_body")', df).iloc[-1]
    df.iloc[-1] = (p, p + 0.1, p - 4.1, p - 4.0, 1e6)
    assert _ev('Pattern("bearish_strong_body")', df).iloc[-1]
    df.iloc[-1] = (p, p + 4.1, p - 2.0, p + 2.0, 1e6)                                 # half body: no
    assert not _ev('Pattern("bullish_strong_body")', df).iloc[-1]
    df.iloc[-1] = (p, p + 0.3, p - 0.01, p + 0.29, 1e6)                               # all body but narrower than ATR: no
    assert not _ev('Pattern("bullish_strong_body")', df).iloc[-1]


def test_v2_masks_equal_the_detectors_bar_by_bar_and_wait_for_the_atr():
    for seed, tf in ((7, "5m"), (3, "5m"), (11, "1d")):
        f = synthetic_bars(500, tf=tf, seed=seed)
        masks = cp.pattern_masks_v2(f)
        atr_values = cp._v2_atr(f)
        for i in range(len(f)):
            found = {f"{m.direction.lower()}_{m.pattern.lower().replace(' ', '_')}" for d in cp.V2_DETECTORS
                     if (m := d(f, i, atr_values)) is not None}
            assert found == {k for k, v in masks.items() if v[i]}, (seed, i)
        assert not any(v[:13].any() for v in masks.values())
    i = 300                                                                          # the detector alone reads only bars up to i
    assert cp.detect_strong_body(f, i) == cp.detect_strong_body(f, i, cp._v2_atr(f))
    assert set(cp.PATTERN_DIRECTION) == set(cp.pattern_masks(f)) | set(PATTERN_NAMES_V2)


def test_v2_candles_stay_out_of_the_last_bar_filters_and_the_scanner():
    assert cp.detect_strong_body not in cp.DETECTORS and cp.detect_gap_reversal not in cp.DETECTORS


# ---------------------------------------------------------------------------------------------- next-bar confirmation
def _engulfing_then(next_close):
    """A bullish engulfing on the second-last bar (high 104, low 97.5), then a bar closing at next_close."""
    df = _trend(40, 0.0)
    df.iloc[-3] = (101.0, 101.5, 98.5, 99.0, 1e6)                                   # bearish
    df.iloc[-2] = (98.0, 104.0, 97.5, 103.0, 1e6)                                   # engulfs it
    df.iloc[-1] = (103.0, max(103.0, next_close) + 0.2, min(103.0, next_close) - 0.2, next_close, 1e6)
    return df


@pytest.mark.parametrize("next_close, status", [(105.0, "confirmed"), (96.0, "failed"), (100.0, "unconfirmed"), (104.0, "unconfirmed"),
                                                 (97.5, "unconfirmed")])
def test_the_status_is_known_on_the_bar_after_the_pattern(next_close, status):
    df = _engulfing_then(next_close)
    assert _ev('Pattern("bullish_engulfing")', df).iloc[-2]
    for s in CONFIRM_STATES[1:]:
        series = _ev(f'Pattern("bullish_engulfing", "{s}")', df)
        assert bool(series.iloc[-1]) is (s == status), (s, status)
        assert not series.iloc[-2]                                                  # never on the pattern bar itself
    assert evaluate(parse(f'PatternBullish("{status}")'), SymbolData("S", {"1d": df}), base_tf="1d") is True
    assert evaluate(parse(f'PatternBearish("{status}")'), SymbolData("S", {"1d": df}), base_tf="1d") is False


def test_a_bearish_status_mirrors_the_bullish_rule():
    df = _trend(40, 0.0)
    df.iloc[-3] = (99.0, 101.5, 98.5, 101.0, 1e6)
    df.iloc[-2] = (102.0, 102.5, 96.0, 97.0, 1e6)                                   # bearish engulfing (low 96, high 102.5)
    for close, status in ((95.0, "confirmed"), (103.0, "failed"), (99.0, "unconfirmed")):
        df.iloc[-1] = (97.0, max(97.0, close) + 0.2, min(97.0, close) - 0.2, close, 1e6)
        assert _ev(f'Pattern("bearish_engulfing", "{status}")', df).iloc[-1], status


def test_the_last_bar_filters_agree_with_the_detectors_on_every_prefix():
    f = synthetic_bars(400, tf="5m", seed=7)
    for t in range(30, len(f), 7):
        pre = f.iloc[:t + 1]
        for direction, fn in (("BULLISH", "PatternBullish"), ("BEARISH", "PatternBearish")):
            had = any(m.direction == direction for m in cp.detect_patterns_at(pre, t - 1))
            close, high, low = pre["close"].iloc[-1], pre["high"].iloc[-2], pre["low"].iloc[-2]
            good, bad = (close > high, close < low) if direction == "BULLISH" else (close < low, close > high)
            want = {"confirmed": had and good, "failed": had and bad, "unconfirmed": had and not good and not bad}
            for status, expected in want.items():
                assert evaluate(parse(f'{fn}("{status}")'), SymbolData("S", {"5m": pre}), base_tf="5m") is bool(expected), (t, fn, status)


# --------------------------------------------------------------------------------------------- RSI divergence / labels
def test_rsi_divergence_after_a_fast_fall_and_a_slow_grind_to_a_lower_low():
    """AMW test_rsi_divergence_detected: low 2 a little under low 1, reached slowly, so RSI is higher there."""
    path = list(np.linspace(130, 150, 20)) + list(np.linspace(150, 100, 15)) + list(np.linspace(100, 115, 8)) \
        + list(np.linspace(115, 98, 25)) + list(np.linspace(98, 108, 8))
    f = _ohlc(path)
    bull = _ev('RsiDivergence("bullish", 30)', f)
    lows = [p for p in _swing_pivots(f, 0) if p.kind == "L"]
    assert lows[-1].price < lows[-2].price
    assert bull.iloc[-1] and not bull.iloc[:int(lows[-1].confirmed_idx)].any()       # known from the second low's confirmation
    assert not _ev('RsiDivergence("bearish", 30)', f).any()
    assert not _ev('RsiDivergence("bullish", 3)', f).iloc[-1]                        # the low is older than 3 bars


def _divergence_from_pivots(f, side, lookback, degree=0):
    """The rule recomputed from the swings known at the frame's last bar (the reference for the series' last value)."""
    from app.indicators.momentum import rsi
    t = len(f) - 1
    kind = "L" if side == "bullish" else "H"
    known = [p for p in _swing_pivots(f, degree) if p.kind == kind and int(p.confirmed_idx) <= t]
    if len(known) < 2 or t - int(known[-1].bar_idx) > lookback:
        return False
    r = rsi(f["close"].astype(float), 14).to_numpy()
    r0, r1 = r[int(known[-2].bar_idx)], r[int(known[-1].bar_idx)]
    if not (np.isfinite(r0) and np.isfinite(r1)):
        return False
    a, b = known[-2].price, known[-1].price
    return bool(b < a and r1 > r0) if kind == "L" else bool(b > a and r1 < r0)


def test_rsi_divergence_series_equals_the_rule_on_every_prefix():
    """AMW test_rsi_divergence_predicate_matches_analysis_module, on ATP's causal swings: the series' value on bar t is
    the rule on the swings known at t (and, Z10-a, the same with or without later bars)."""
    rng = np.random.default_rng(3)
    f = _ohlc(100 + np.cumsum(rng.normal(0, 1, 300)), spread=1.0)
    hits = {"bullish": 0, "bearish": 0}
    for side in ("bullish", "bearish"):
        full = _ev(f'RsiDivergence("{side}", 25)', f)
        for t in range(120, 300, 3):
            pre = f.iloc[:t + 1]
            want = _divergence_from_pivots(pre, side, 25)
            assert bool(_ev(f'RsiDivergence("{side}", 25)', pre).iloc[-1]) is want is bool(full.iloc[t]), (side, t)
            hits[side] += want
    assert hits["bullish"] and hits["bearish"]


def test_swing_labels_follow_the_confirmed_swings_and_agree_with_the_dow_trend():
    from app.screener.breakouts.params import settings
    from app.screener.breakouts.screens import Bars
    f = synthetic_bars(600, tf="5m", seed=3)
    high, low = _ev('SwingLabel("high")', f, "5m"), _ev('SwingLabel("low")', f, "5m")
    pivots = _swing_pivots(f, 0)
    for kind, series, names in (("H", high, ("HH", "LH")), ("L", low, ("HL", "LL"))):
        mine = [p for p in pivots if p.kind == kind]
        assert series.iloc[:int(mine[1].confirmed_idx)].isna().all()             # missing before the second swing of the kind
        for a, b in zip(mine, mine[1:]):
            assert series.iloc[int(b.confirmed_idx)] == (names[0] if b.price > a.price else names[1])
    dow = Bars(f, settings()).dow(0)
    for t in range(len(f)):
        if dow[t] is None or pd.isna(high.iloc[t]) or pd.isna(low.iloc[t]):
            continue
        up, down = (high.iloc[t], low.iloc[t]) == ("HH", "HL"), (high.iloc[t], low.iloc[t]) == ("LH", "LL")
        assert dow[t] == ("UP" if up else "DOWN" if down else "RANGE"), t


# --------------------------------------------------------------------------------------------------------- TimeOfDay
def test_time_of_day_reads_each_bar_start_in_exchange_time():
    idx = pd.date_range("2026-03-02 03:45", periods=8, freq="15min", tz="UTC")       # 09:15 .. 11:00 IST
    f = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}, index=idx)
    assert _ev('TimeOfDay("09:30", "10:15")', f, "15m").tolist() == [False, True, True, True, True, False, False, False]
    assert _ev('TimeOfDay("10:30", "09:15")', f, "15m").tolist() == [True, False, False, False, False, True, True, True]   # wraps
    ist = f.tz_convert("Asia/Kolkata")
    assert _ev('TimeOfDay("09:30", "10:15")', ist, "15m").tolist() == _ev('TimeOfDay("09:30", "10:15")', f, "15m").tolist()
    naive = f.tz_convert(None)                                                       # a naive index is UTC
    assert _ev('TimeOfDay("09:30", "10:15")', naive, "15m").tolist() == _ev('TimeOfDay("09:30", "10:15")', f, "15m").tolist()
    # @1h: hours from 09:15 IST; the 10:15 hour is seen once it has closed (11:15, on the 11:00 bar), never earlier
    hourly = _ev('TimeOfDay("10:15", "10:15")@1h', f, "15m")
    assert hourly.iloc[:3].isna().all() and hourly.iloc[3:].tolist() == [False, False, False, False, True]


# --------------------------------------------------------------------------------------------------- vectorised path
def _intraday_walk(seed, n=300):
    return synthetic_bars(n, tf="5m", seed=seed)


@pytest.mark.parametrize("text", ['TimeOfDay("09:30", "14:45")', 'TimeOfDay("15:00", "09:30")', "StDev(close, 20)", "StDev(high - low, 7)"])
def test_vectorised_series_equal_the_per_symbol_series(text):
    data = [SymbolData(f"S{i}", {"5m": _intraday_walk(i)}) for i in range(5)]
    ev = panel._PanelEval(panel.Panel([d.frames["5m"] for d in data], [d.symbol for d in data]), "5m", {})
    got = ev.ev(parse(text), "5m")
    for d in data:
        want = evaluate(parse(text), d, base_tf="5m")
        col = got[d.symbol]
        if want.dtype == bool:
            assert col.dtype == bool and (col.to_numpy() == want.to_numpy()).all()
        else:
            np.testing.assert_array_equal(col.to_numpy(dtype=float), want.to_numpy(dtype=float))


def test_vectorised_and_per_symbol_runs_give_the_same_matches():
    data = [SymbolData(f"S{i}", {"5m": _intraday_walk(i)}) for i in range(6)]
    for text in ('TimeOfDay("09:15", "12:00") AND StDev(close, 10) > 0.05', 'StDev(close, 20) > StDev(close, 50)', 'NOT TimeOfDay("13:00", "15:30")'):
        ast, v = compile_screen(text, base_tf="5m")
        assert v.ok and panel.supported(ast, v, "5m"), text
        slow = [(m.symbol, m.matched, m.reason) for m in run_screen(ast, v, data, base_tf="5m")]
        fast = [(m.symbol, m.matched, m.reason) for m in run_screen(ast, v, data, base_tf="5m", vectorized=True)]
        assert slow == fast, text
    for text in ('RsiDivergence("bullish") AND close > 0', 'SwingLabel("low") == "HL"', 'Pattern("hammer", "confirmed")'):
        ast, v = compile_screen(text, base_tf="5m")
        assert not panel.supported(ast, v, "5m"), text                              # swings and candles: per symbol


# --------------------------------------------------------------------------------------------------------- the gate
def test_flagged_calls_name_what_the_flag_covers():
    ast = parse('StDev(close, 5) > 1 AND Pattern("hammer") AND Pattern("hammer", "any") AND Pattern("bullish_strong_body") '
                'AND Pattern("bullish_engulfing", "failed") AND PatternBullish() AND PatternBearish("confirmed") AND TimeOfDay("09:30", "10:00")')
    assert amw.flagged_calls(ast) == ["StDev()", 'Pattern("bullish_strong_body")', 'Pattern("bullish_engulfing", "failed")',
                                      'PatternBearish("confirmed")', "TimeOfDay()"]
    assert amw.flagged_calls(parse('Pattern("doji") AND PatternBullish("any") AND SMA(close, 5) > 1')) == []
    # a $parameter is resolved when the parameters are known, and counts as gated when they are not
    p = parse("Pattern($name)")
    assert amw.flagged_calls(p, {"name": "hammer"}) == [] and amw.flagged_calls(p, {"name": "bearish_gap_reversal"}) == ["Pattern($name)"]
    assert amw.flagged_calls(p, {}) == ["Pattern($name)"]
    assert "screenql_amw_functions" in amw.refusal(["StDev()"])


def test_hide_gives_the_registry_as_it_was_before_amw3():
    hidden = amw.hide(describe())
    assert hidden["Pattern"]["args"] == ["name"] and not set(PATTERN_NAMES_V2) & set(hidden["Pattern"]["choices"]["name"])
    assert hidden["Pattern"]["doc"].endswith("up to two before it)")
    for name in ("PatternBullish", "PatternBearish"):
        assert hidden[name]["args"] == [] and hidden[name]["choices"] == {} and "confirm" not in hidden[name]["doc"]
    assert describe()["Pattern"]["args"] == ["name", "confirm"]                    # hide works on a copy


def _run(coro):
    return asyncio.run(coro)


def _set(**flags):
    async def go():
        from app.platform import controls
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            stored = dict(current.get("flags") or {})
            for name, on in flags.items():
                stored[name] = {"on": on, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": stored}, None)
            await session.commit()
    _run(go())


@pytest.fixture
def screener_on():
    _set(screener_v2=True, screenql_amw_functions=False)
    yield
    _set(screener_v2=False, screenql_amw_functions=False)


def test_hidden_and_refused_while_off_listed_and_accepted_when_on(screener_on):
    headers, _ = _owner("amw3-gate@example.com")
    reg = client.get("/api/screener/registry", headers=headers).json()["entries"]
    assert not set(AMW_FUNCTIONS) & set(reg) and reg["Pattern"]["args"] == ["name"]
    assert "bullish_strong_body" not in reg["Pattern"]["choices"]["name"]
    for source in ('StDev(close, 5) > 1', 'Pattern("bearish_gap_reversal")', 'Pattern("hammer", "confirmed")', 'PatternBullish("failed")',
                   'SwingLabel("low") == "HL"', 'RsiDivergence("bullish")'):
        v = client.post("/api/screener/validate", json={"source": source, "base_tf": "1d"}, headers=headers).json()
        assert not v["ok"] and "screenql_amw_functions" in v["problems"][-1]["message"], source
    v = client.post("/api/screener/validate", json={"source": "Pattern($p)", "base_tf": "1d", "params": {"p": "bullish_strong_body"}},
                    headers=headers).json()
    assert not v["ok"] and "screenql_amw_functions" in str(v)
    assert client.post("/api/screener/validate", json={"source": "Pattern($p)", "base_tf": "1d", "params": {"p": "hammer"}},
                       headers=headers).json()["ok"]
    assert client.post("/api/screener/validate", json={"source": 'Pattern("doji", "any")', "base_tf": "1d"}, headers=headers).json()["ok"]
    assert client.post("/api/screener/screens", json={"name": "a", "source": "StDev(close, 5) > 1"}, headers=headers).status_code == 422
    run = client.post("/api/screener/run", json={"source": 'Pattern("hammer", "failed")', "symbols": ["X"]}, headers=headers)
    assert run.status_code == 422 and "screenql_amw_functions" in str(run.json())
    r = client.post("/api/alerts/rules", json={"name": "a", "kind": "instrument", "symbol": "ABC", "condition": 'TimeOfDay("09:30", "10:00")',
                                               "base_tf": "15m"}, headers=headers)
    assert r.status_code == 422 and "screenql_amw_functions" in str(r.json()), r.text
    _set(screenql_amw_functions=True)
    reg = client.get("/api/screener/registry", headers=headers).json()["entries"]
    assert set(AMW_FUNCTIONS) <= set(reg) and reg["Pattern"]["args"] == ["name", "confirm"] and reg["TimeOfDay"]["patterns"]
    for source in ('StDev(close, 5) > 1', 'Pattern("bearish_gap_reversal", "confirmed")', 'PatternBullish("failed")', 'SwingLabel("low") == "HL"'):
        assert client.post("/api/screener/validate", json={"source": source, "base_tf": "1d"}, headers=headers).json()["ok"], source
    r = client.post("/api/alerts/rules", json={"name": "a", "kind": "instrument", "symbol": "ABC", "condition": 'TimeOfDay("09:30", "10:00")',
                                               "base_tf": "15m"}, headers=headers)
    assert r.status_code in (200, 201), r.text


def test_a_rule_pauses_while_off_and_resumes_on_the_same_closed_bar(screener_on):
    from app.alerts import barclose
    from app.db.models import AlertEventRecord, AlertRuleRecord
    from tests.test_s4a_barclose import TUE, FakeFetch, _bars as s4_bars, _rule, ist as s4_ist
    _, me = _owner("amw3-pause@example.com")
    rid = _rule(me["tenant_id"], condition='TimeOfDay("09:15", "15:30")', tf="5m", symbol="AMWA")
    fetch = FakeFetch({"AMWA": s4_bars(s4_ist(TUE, 9, 15), [100.0] * 61)})

    def cycle(now):
        async def go():
            async with _session_factory() as session:
                await barclose.run_due(session, now=now, fetch=fetch, holidays=set())
                rule = await session.get(AlertRuleRecord, rid)
                events = list(await session.scalars(select(AlertEventRecord).where(AlertEventRecord.rule_id == rid)))
                return rule.status, rule.last_problem, len(events)
        return _run(go())
    now = s4_ist(TUE, 14, 21)
    status, problem, fired = cycle(now)
    assert status == "active" and "screenql_amw_functions" in problem and fired == 0
    _set(screenql_amw_functions=True)
    status, problem, fired = cycle(now + timedelta(minutes=2))
    assert status == "active" and problem is None and fired == 1


# ------------------------------------------------------------------------------------------------ evidence and mirror
def test_a_confirmation_status_draws_the_pattern_bar_and_the_bar_that_confirmed_it():
    from app.screener import evidence as ev
    df = _engulfing_then(105.0)
    ast, v = compile_screen('Pattern("bullish_engulfing", "confirmed")', base_tf="1d")
    pack = ev.build_pack(ast, v, SymbolData("S", {"1d": df}), base_tf="1d", debug=True)
    d = next(x for x in pack["drawables"] if x["kind"] == "bars")
    j = len(df) - 1
    assert d["index"] == [j - 2, j - 1, j] and "confirmed by the next bar" in d["label"]
    for name in AMW_FUNCTIONS:
        assert ev.drawn_as(name) == ev.DRAWN_AS[name]


def test_the_mirror_swaps_the_v2_candles_and_keeps_the_status():
    from app.screener import mirror as mr
    from app.screener import nodes as n
    cls, expr = mr.rewrite(parse('Pattern("bullish_gap_reversal", "confirmed")'))
    assert cls == mr.INV and n.to_text(expr) == 'Pattern("bearish_gap_reversal", "confirmed")'
    with pytest.raises(mr.NoMirror):
        mr.rewrite(parse('RsiDivergence("bullish")'))                              # no mirror rule (RSI is not checked reflected)


# ------------------------------------------------------------------------------------------ review of AMW-3 (follow-ups)
def test_the_flag_off_view_is_the_registry_as_it_was_before_amw3():
    from app.screener.registry import PATTERN_NAMES
    from app.screener.validator import validate
    off = amw.FLAG_OFF_FUNCTIONS
    assert [a.name for a in off["Pattern"].args] == ["name"] and off["Pattern"].args[0].choices == PATTERN_NAMES
    assert off["PatternBullish"].args == () and off["PatternBearish"].args == () and off["Pattern"].choice_extra_bars == ()
    hidden = amw.hide(describe())
    for name in amw.PATTERN_CALLS:
        assert off[name].doc == hidden[name]["doc"]
    assert set(AMW_FUNCTIONS) <= set(off)                                           # still known: refused with the flag's reason
    v = validate(parse('Pattern("foo")'), base_tf="5m", functions=off)
    assert v.problems[0].message == f"Pattern: name 'foo' is not one of {', '.join(PATTERN_NAMES)}"


@pytest.mark.parametrize("source, main_message", [
    ('Pattern("foo")', "Pattern: name 'foo' is not one of doji, hammer, shooting_star"),
    ('Pattern("hammer", 5)', "Pattern takes 1 argument(s), got 2"),
    ('Pattern("doji", "confirmed")', "Pattern takes 1 argument(s), got 2"),
    ('PatternBullish("confirmed")', "PatternBullish takes 0 argument(s), got 1"),
    ('PatternBearish(1)', "PatternBearish takes 0 argument(s), got 1"),
])
def test_flag_off_mistakes_get_mains_messages_and_name_nothing_hidden(screener_on, source, main_message):
    headers, _ = _owner(f"amw3-messages-{abs(hash(source)) % 10**8}@example.com")
    v = client.post("/api/screener/validate", json={"source": source, "base_tf": "5m"}, headers=headers).json()
    text = " | ".join(p["message"] for p in v["problems"])
    assert not v["ok"] and v["problems"][0]["message"].startswith(main_message), text
    assert not any(name in text for name in PATTERN_NAMES_V2) and "confirm\"" not in text and "1-2 argument" not in text, text
    r = client.post("/api/alerts/rules", json={"name": "m", "kind": "instrument", "symbol": "ABC", "condition": source, "base_tf": "15m"},
                    headers=headers)
    assert r.status_code == 422 and main_message in str(r.json()) and "strong_body" not in str(r.json())
    _set(screenql_amw_functions=True)                                              # on: the full registry's messages
    v = client.post("/api/screener/validate", json={"source": 'Pattern("foo")', "base_tf": "5m"}, headers=headers).json()
    assert "bullish_strong_body" in v["problems"][0]["message"]


def test_flag_off_chart_studies_check_against_the_flag_off_view():
    from app.charts import studies
    row = studies.check(['Pattern("foo")'], "15m", amw.FLAG_OFF_FUNCTIONS)[0]
    assert not row["ok"] and "strong_body" not in row["problems"][0]["message"]
    assert "strong_body" in studies.check(['Pattern("foo")'], "15m")[0]["problems"][0]["message"]


def test_closed_bar_parts_are_named_for_intrabar():
    ast = parse('Pattern("hammer", "confirmed") AND Pattern("hammer", "any") AND PatternBearish("failed") AND RsiDivergence("bullish") '
                'AND SwingLabel("low") == "HL" AND StDev(close, 5) > 0 AND TimeOfDay("09:30", "10:00") AND Pattern("bullish_strong_body")')
    assert amw.intrabar_calls(ast) == ['Pattern("hammer", "confirmed")', 'PatternBearish("failed")', "RsiDivergence()", "SwingLabel()"]
    assert "bar close only" in amw.intrabar_refusal(["SwingLabel()"])


def test_intrabar_rules_with_a_status_or_a_swing_function_are_refused(screener_on):
    headers, _ = _owner("amw3-intrabar@example.com")
    _set(screenql_amw_functions=True, screener_intrabar=True)
    for condition in ('Pattern("hammer", "confirmed")', 'RsiDivergence("bullish")', 'SwingLabel("high") == "HH"', 'PatternBullish("failed")'):
        r = client.post("/api/alerts/rules", json={"name": "i", "kind": "instrument", "symbol": "ABC", "condition": condition, "base_tf": "15m",
                                                   "fire_on": "intrabar"}, headers=headers)
        assert r.status_code == 422 and "bar close only" in str(r.json()), (condition, r.text)
        r = client.post("/api/alerts/rules", json={"name": "b", "kind": "instrument", "symbol": "ABC", "condition": condition, "base_tf": "15m"},
                        headers=headers)
        assert r.status_code in (200, 201), (condition, r.text)                      # at bar close it is fine
    for condition in ("StDev(close, 5) > 0", 'TimeOfDay("09:30", "14:45")', 'Pattern("bullish_strong_body")'):
        r = client.post("/api/alerts/rules", json={"name": "j", "kind": "instrument", "symbol": "ABC", "condition": condition, "base_tf": "15m",
                                                   "fire_on": "intrabar"}, headers=headers)
        assert r.status_code in (200, 201), (condition, r.text)
    _set(screener_intrabar=False)


def test_a_screen_rule_and_an_edit_of_its_screen_pass_the_amw3_check_too(screener_on):
    # merge of N3 S7a: screen_rule_checks (rule save, and an edit of the screen a rule runs) runs the AMW-3 check with
    # the screen's own $parameters, beside the breakout, zone and RankScore checks
    headers, _ = _owner("amw3-screen-rule@example.com")
    _set(screenql_amw_functions=True, screener_intrabar=True)
    sid = client.post("/api/screener/screens", json={"name": "s", "source": "close > 0", "base_tf": "15m"}, headers=headers).json()["id"]
    r = client.post("/api/alerts/rules", json={"name": "r", "kind": "screen", "screen_id": sid, "symbols": ["ABC"], "fire_on": "intrabar"},
                    headers=headers)
    assert r.status_code in (200, 201), r.text
    rid = r.json()["id"]
    edit = client.put(f"/api/screener/screens/{sid}", json={"name": "s", "source": 'Pattern($p, "confirmed")', "base_tf": "15m",
                                                             "params": {"p": "hammer"}}, headers=headers)
    assert edit.status_code == 422 and f"alert rule #{rid}" in edit.text and "bar close only" in edit.text, edit.text
    sid2 = client.post("/api/screener/screens", json={"name": "t", "source": 'SwingLabel("high") == "HH"', "base_tf": "15m"},
                       headers=headers).json()["id"]
    r = client.post("/api/alerts/rules", json={"name": "q", "kind": "screen", "screen_id": sid2, "symbols": ["ABC"], "fire_on": "intrabar"},
                    headers=headers)
    assert r.status_code == 422 and "bar close only" in r.text, r.text
    _set(screener_intrabar=False)


def test_the_engine_skips_an_intrabar_swing_rule_with_the_reason(screener_on):
    from app.alerts import barclose
    from app.db.models import AlertRuleRecord
    from tests.test_s4a_barclose import TUE, FakeFetch, _bars as s4_bars, _rule, ist as s4_ist
    _, me = _owner("amw3-intrabar-engine@example.com")
    _set(screenql_amw_functions=True, screener_intrabar=True)
    rid = _rule(me["tenant_id"], condition='SwingLabel("low") == "HL"', tf="5m", symbol="AMWB", fire_on="intrabar")
    fetch = FakeFetch({"AMWB": s4_bars(s4_ist(TUE, 9, 15), [100.0] * 61)})

    async def go():
        async with _session_factory() as session:
            await barclose.run_due(session, now=s4_ist(TUE, 14, 23), fetch=fetch, holidays=set())
            rule = await session.get(AlertRuleRecord, rid)
            return rule.status, rule.last_problem
    status, problem = _run(go())
    assert status == "active" and "bar close only" in (problem or ""), problem
    _set(screener_intrabar=False)


def test_v2_candles_read_the_atr_of_their_own_bar_like_amw():
    """AMW3-5: the threshold is ATR(14) at the candle's own bar (AMW's atr14 at the same index), gap bar included."""
    df = _trend()
    prev = df["close"].iloc[-2]
    df.iloc[-1] = (prev + 3.0, prev + 3.3, prev - 1.5, prev - 1.2, 1e6)
    atr_values = cp._v2_atr(df)
    from app.indicators.volatility import atr
    assert atr_values[-1] == atr(df, 14).iloc[-1] and atr_values[-1] > atr_values[-2]  # the gap bar's range is in it
