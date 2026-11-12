"""M94 PR-4 (docs/design/M94_INTRADAY_PACK.md section 4, F2 / F8 / F17; OPEN_QUESTIONS M94-Q1 / Q2 / Q3 / Q7): the strategy
template family H "Intraday" and its first five templates - H6 Opening range breakout (spec T6), H7 VWAP false breakout
(T7), H8 VWAP reversal (T8), H9 VWAP and moving-average trend (T9), H10 Previous-day level rejection (T10).

* Each template's trigger and invalidation on hand-built bar series: it fires where it should, not where it should not;
  the same on random walks against the parts evaluated on their own by the screener runtime; no look-ahead (every series
  cut at many bars equals the full run); the short side is the long side on mirrored prices.
* The recipe compiles on every allowed timeframe and every choice; each setup part runs as a screen on its own.
* Family H is listed (and adoptable, and runnable) only with `intraday_pack` on; families A-G are unchanged.
* The "Reference values - edit before use" label; no time-of-day rule in any template (M94-Q7: the user's own window).
* Shorts: an intraday template is adopted with the INTRADAY holding, so a short on cash shares uses MIS.
* Scan = backtest = worker (parity.py), and one PAPER trade of H6 end to end through the deployment route and the worker.
Signals only: no size, no order, no price in any template; PAPER only.
"""
import asyncio
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from app.core.models import RiskConfig
from app.execution.products import product_for
from app.quality.lookahead import synthetic_bars
from app.screener.parser import parse
from app.screener.runtime import SymbolData, evaluate
from app.screener.validator import validate
from app.strategy_templates import compile as C
from app.strategy_templates import engine, ids, language, library, parity
from app.strategy_templates.adapter import ENGINE_LABEL, TemplateStrategy
from app.strategy_templates.routes import FAMILY_FLAGS, FAMILY_NAMES, REFERENCE_LABEL, WINDOW_NOTE
from app.strategy_templates.schema import Template
from tests.breakout_fixtures import history, orb_day, quiet_day, sessions_frame

H_IDS = ("H6", "H7", "H8", "H9", "H10")
FAMILY_H = ("H1", "H2", "H3", "H4", "H5") + H_IDS          # M94 PR-5 (tests/test_m94_pr5_templates.py) added H1-H5
T = {tid: library.get(tid) for tid in H_IDS}
RISK = RiskConfig(capital=10_000_000, risk_per_trade_pct=0.5)
K = 400.0                                                     # mirror axis: price -> K - price
IST = ZoneInfo("Asia/Kolkata")


def run(tid, df, tf="5m", sides=None, **params):
    t = T[tid]
    return engine.compute(C.compile_recipe(t, C.resolve_settings(t, tf, sides, params)), {tf: df})


def ev(text, df, tf="5m"):
    return evaluate(parse(text), SymbolData("S", {tf: df}), base_tf=tf)


def signals(s):
    return {t: int(s.direction[t]) for t in s.signal_bars()}


def at(df, s):
    """The signals as (bar start, side, invalidation) - for the hand-built fixtures."""
    return [(df.index[t], int(s.direction[t]), round(float(s.invalidation[t]), 4)) for t in s.signal_bars()]


def mirror(df, axis=K):
    out = df.copy()
    out["open"], out["close"] = axis - df["open"], axis - df["close"]
    out["high"], out["low"] = axis - df["low"], axis - df["high"]
    return out


def whole(df):
    """Whole ticks (x 20: the synthetic bars move in 0.05 steps), so mirrored prices are exact."""
    out = df.copy()
    out[["open", "high", "low", "close"]] = (df[["open", "high", "low", "close"]] * 20).round()
    return out


def bar(c, h=0.25, lo=0.25, o=None, v=1000.0):
    o = c if o is None else o
    return (o, max(o, c) + h, min(o, c) - lo, c, v)


def flat_day(mid=100.0, n=75):
    """A session whose every close equals its typical price: nothing is above or below VWAP or the averages."""
    return [(mid, mid + 0.25, mid - 0.25, mid, 1000.0)] * n


def slot(df, day, k, minutes=5):
    """The bar start of slot k (0 = 09:15 IST) of the session `day` (0-based) of a sessions_frame."""
    first = df.index.normalize().unique()[day]
    return first + pd.Timedelta(hours=3, minutes=45 + minutes * k)


# ================================================================================= the schema and the library
def test_family_h_is_a_schema_change_and_families_a_to_g_are_unchanged():
    for t in library.templates():
        if t.family in ("J", "K"):                # M99 P3 / P4 (tests/test_m99_p3_vsa.py, test_m99_p4_wyckoff.py)
            continue
        if t.family == "L":                       # M99 P5 (tests/test_m99_p5_fib_ew.py)
            continue
        if t.family == "H":
            assert t.id in FAMILY_H and t.reference_values and t.style == "Intraday (session-based)"
        else:
            assert t.family in "ABCDEFG" and t.id[0] in "ABCDEFG" and t.reference_values is False, t.id
    assert FAMILY_NAMES["H"] == "Intraday" and FAMILY_FLAGS == {"G": "screener_waves", "H": "intraday_pack", "J": "vsa_pack",
                                                                  "K": "wyckoff_pack", "L": "fib_ew_pack"}   # M99 P3: J; P4: K; P5: L
    assert {k: v for k, v in FAMILY_NAMES.items() if k not in ("H", "J", "K", "L")} == {
        "A": "Price action", "B": "Breakout", "C": "Breakout and retest", "D": "Trend following",
        "E": "Momentum and mean reversion", "F": "Option seller packs", "G": "Waves"}
    data = T["H6"].model_dump(by_alias=True)
    for bad in ({"id": "B99"}, {"family": "B"}, {"id": "I1"}, {"family": "I"}):
        with pytest.raises(ValueError):
            Template.model_validate({**data, **bad})
    # an A-G template without the new field reads exactly as before (the field defaults to off)
    b10 = library.get("B10").model_dump(by_alias=True)
    b10.pop("reference_values")
    assert Template.model_validate(b10) == library.get("B10")
    # the ids of families A-F are written as before (G and H: choices by position)
    assert ids.COMPACT_FAMILIES == {"G", "H"}


@pytest.mark.parametrize("tid", H_IDS)
def test_the_recipe_compiles_on_every_timeframe_and_choice_and_its_text_passes_the_guard(tid):
    t = T[tid]
    assert C.validate_template(t) == []
    assert t.allowed_timeframes == ("5m", "1m", "3m", "15m") and set(t.allowed_timeframes) <= set(ENGINE_LABEL)
    for tf in t.allowed_timeframes:
        settings = C.resolve_settings(t, tf)
        recipe = C.compile_recipe(t, settings)
        assert recipe.chosen_setup.session_bound                     # an instance never outlives its session
        assert recipe.required_flags <= {"screener_breakouts", "intraday_pack"}
        for side in ("long", "short"):
            on = {p.key: True for p in t.params if p.type == "bool"}
            text = library.signal_text(t, C.Settings(tf, "both", {**settings.params, **on}), side)
            assert text and language.guard(text) == [], text
    for p in t.params:
        assert language.guard(p.label) == [] or p.type in ("bool", "int", "float"), p.key
        if p.type in ("int", "float"):
            assert p.min is not None and p.max is not None and p.min <= p.default <= p.max
        if p.choices:
            assert len(p.choice_labels) == len(p.choices)


def _texts(t):
    out = []
    for part in (t.setup, t.trigger, t.invalidation, t.exit_signal, t.context):
        if part is None:
            continue
        leaves = [v for v in part.variants.values() if v] if hasattr(part, "variants") else [part]
        out += [x for v in leaves if hasattr(v, "long") for x in (v.long, v.short)]
    return out


_STRUCTURE = re.compile(r"(\[1\])|(/ 2\b)|(@1d\b)")         # the bar before; the middle of a range; the Daily bars


@pytest.mark.parametrize("tid", H_IDS)
def test_every_number_is_a_setting_and_no_clock_rule_and_signals_only(tid):
    t = T[tid]
    texts = _texts(t)
    assert texts
    for text in texts:
        assert not re.search(r"(?<![\w$])\d", _STRUCTURE.sub("", text)), text            # numbers only in settings (I2)
        assert "TimeOfDay" not in text                                                  # M94-Q7: no clock rule
        assert not re.search(r"\b(target|lots|strike|premium|order|buy|sell|stop)\b", text, re.I)
    data = t.model_dump(by_alias=True)
    assert not {"target", "targets", "lots", "quantity", "strike", "premium", "order"} & set(data)


@pytest.mark.parametrize("tid", H_IDS)
def test_the_setup_part_runs_as_a_screen_on_its_own(tid):
    """The template's setup scanner: its chosen setup text, with the settings filled in, is a valid screen for the screener's
    own validator and runtime, and its matches are the engine's setup bars."""
    t = T[tid]
    settings = C.resolve_settings(t, "5m")
    recipe = C.compile_recipe(t, settings)
    df = synthetic_bars(2400, tf="5m", seed=3)
    for side in ("long", "short"):
        part = recipe.part("setup", side)
        text = part.text
        for key, value in part.params.items():
            text = re.sub(rf"\${key}\b", str(value).lower() if isinstance(value, bool) else str(value), text)
        assert "$" not in text
        check = validate(parse(text), base_tf="5m")
        assert check.ok, (text, check.problems)
        matches = ev(text, df).reindex(df.index).fillna(False).astype(bool).to_numpy()
        on_engine = engine._bool(engine._Eval(SymbolData("S", {"5m": df}), "5m", part.params, {}, memo={}).ev(part.ast, "5m"), df.index)
        np.testing.assert_array_equal(matches, on_engine)


def test_h6_setup_is_the_library_orb_screens_own_function():
    """H6 reads the same B16 function as the library's ORB screens (breakouts.intraday_5m_orb15 / intraday_15m_orb30)."""
    from app.screener.breakouts.templates import TEMPLATES
    by_id = {x.id: x for x in TEMPLATES}
    assert by_id["intraday_5m_orb15"].screen == "B16" and by_id["intraday_15m_orb30"].screen == "B16"
    assert all("ORBreakout(" in x for x in (T["H6"].setup.long, T["H6"].setup.short))


# ================================================================================= H6 Opening range breakout
def test_h6_fires_on_the_first_close_beyond_the_opening_range_and_not_without_it():
    df = sessions_frame(history(21) + [orb_day()])
    brk = slot(df, 21, 6)                                                    # 09:45, the breaking bar
    assert at(df, run("H6", df)) == [(brk, 1, 99.9)]                         # wrong below the other side of the range
    assert at(df, run("H6", df, invalidation="mid_range")) == [(brk, 1, 100.45)]
    vwap = float(ev("SessionVWAP()", df).loc[brk])
    assert at(df, run("H6", df, invalidation="vwap")) == [(brk, 1, round(vwap, 4))] and vwap < df.loc[brk, "close"]
    assert at(df, run("H6", df, minutes=5)) == [(brk, 1, 99.9)]              # the first bar's range: the same first close beyond
    assert signals(run("H6", sessions_frame(history(22)))) == {}             # a quiet session: nothing
    wick = sessions_frame(history(21) + [orb_day(wick=True)])                # the close is below the session VWAP
    assert signals(run("H6", wick)) == {} and at(wick, run("H6", wick, vwap=False)) == [(slot(wick, 21, 6), 1, 99.9)]
    assert signals(run("H6", df, vol_mult=3.0)) == {}                        # 2.5x the time of day's volume is not 3x
    assert signals(run("H6", df, sides="short")) == {}


def test_h6_range_filter_and_its_constraint():
    df = sessions_frame(history(21) + [orb_day()])
    daily_atr = float(ev("ATR(14)@1d", df).loc[slot(df, 21, 6)])                # the closed Daily bars at the breaking bar
    width = 101.0 - 99.9
    small, big = width / daily_atr + 0.05, width / daily_atr - 0.05
    assert signals(run("H6", df, use_range_filter=True, max_or_atr=round(small, 2)))
    assert signals(run("H6", df, use_range_filter=True, max_or_atr=round(big, 2))) == {}
    with pytest.raises(C.TemplateError):
        C.resolve_settings(T["H6"], "15m", params={"minutes": 20})            # not a whole number of 15-minute bars
    assert C.resolve_settings(T["H6"], "15m", params={"minutes": 30}).params["minutes"] == 30


# ================================================================================= H7 VWAP false breakout
def _h7(below):
    d2 = [bar(100.0)] * 4 + [bar(99.5)] * below + [bar(100.5)] * 6
    return sessions_frame([quiet_day(), d2])


def test_h7_fires_when_the_close_comes_back_after_holding_the_other_side_and_not_after_a_short_visit():
    df = _h7(8)
    back = slot(df, 1, 12)                                                    # the close back above VWAP
    assert at(df, run("H7", df)) == [(back, 1, 99.25)]                        # the lowest low since the cross below
    vwap = float(ev("SessionVWAP()", df).loc[back])
    atr = float(ev("ATR(14)[1]", df).loc[back])
    assert at(df, run("H7", df, invalidation="vwap_buffer")) == [(back, 1, round(vwap - atr, 4))]
    assert signals(run("H7", _h7(3))) == {}                                   # 3 bars below is not 6 of the last 9
    assert signals(run("H7", _h7(8), bars_min=9)) == {}
    with pytest.raises(C.TemplateError):
        C.resolve_settings(T["H7"], "5m", params={"bars_min": 10, "bars_n": 9})


# ================================================================================= H8 VWAP reversal
_FALL = [bar(100.0 - 0.2 * (i + 1), h=0.05, lo=0.05) for i in range(10)]   # every low below the one before
_HIGHER_LOW = bar(98.05, h=0.1, lo=0.05)                                    # low 98.0: the lows stopped falling


def _h8(turn):
    return sessions_frame([quiet_day(), [bar(100.0)] * 4 + _FALL + [_HIGHER_LOW, turn] + [bar(98.8)] * 4])


def test_h8_fires_on_the_first_turn_after_the_lows_stop_falling_and_not_on_a_weak_bounce():
    df = _h8((98.0, 98.9, 97.9, 98.85, 1000.0))                               # closes above the fast average and the bar before
    turn = slot(df, 1, 15)
    assert at(df, run("H8", df)) == [(turn, 1, 97.9)]                         # the lowest low since the stretch
    assert at(df, run("H8", df, invalidation="prev_bar")) == [(turn, 1, 98.0)]
    session_low = float(ev("SessionLow()", df).loc[turn])
    assert at(df, run("H8", df, invalidation="session_extreme")) == [(turn, 1, round(session_low, 4))]
    assert signals(run("H8", _h8((98.0, 98.2, 97.9, 98.15, 1000.0)))) == {}  # below the fast average: no turn
    assert signals(run("H8", df, ext=20.0)) == {}                             # the stretch is not twenty ATRs
    wait = [(98.05, 98.1, 98.0, 98.05, 1000.0)] * 3                           # no new low, no higher low: no new setup
    late = sessions_frame([quiet_day(), [bar(100.0)] * 4 + _FALL + [_HIGHER_LOW] + wait + [(98.0, 98.9, 97.9, 98.85, 1000.0)]])
    assert at(late, run("H8", late)) == [(slot(late, 1, 18), 1, 97.9)]
    assert signals(run("H8", late, expiry=1)) == {}                           # the turn came after the setting's bars


def test_h8_session_extreme_needs_the_intraday_pack_flag():
    r = C.compile_recipe(T["H8"], C.resolve_settings(T["H8"], "5m", params={"invalidation": "session_extreme"}))
    assert r.required_flags == {"screener_breakouts", "intraday_pack"}
    assert C.compile_recipe(T["H8"], C.resolve_settings(T["H8"], "5m")).required_flags == {"screener_breakouts"}


# ================================================================================= H9 VWAP and moving-average trend
_RISE = [bar(100.0 + 0.1 * i, h=0.05, lo=0.05) for i in range(30)]


def test_h9_fires_on_a_pullback_that_closes_back_above_the_average_and_not_below_it():
    df = sessions_frame([flat_day(), _RISE + [(102.9, 102.95, 101.9, 102.5, 1000.0)] + [bar(102.6, h=0.05, lo=0.05)] * 4])
    pull = slot(df, 1, 30)
    ema = float(ev("EMA(close, 20)", df).loc[pull])
    assert df.loc[pull, "low"] <= ema < df.loc[pull, "close"]
    assert at(df, run("H9", df)) == [(pull, 1, round(ema, 4))]               # wrong below the average itself (buffer Not set)
    atr = float(ev("ATR(14)[1]", df).loc[pull])
    assert at(df, run("H9", df, buf=0.5)) == [(pull, 1, round(ema - 0.5 * atr, 4))]
    assert at(df, run("H9", df, held_n=20)) == [(pull, 1, round(ema, 4))]    # the average held for the twenty bars
    bad = sessions_frame([flat_day(), _RISE + [(102.9, 102.95, 101.9, 101.95, 1000.0)] + [bar(101.5, h=0.05, lo=0.05)] * 4])
    assert signals(run("H9", bad)) == {}                                      # closed below the average: no signal
    assert signals(run("H9", df, ma_n=5)) == {}                               # the fast average was not touched


# ================================================================================= H10 Previous-day level rejection
_TOUCH = (100.0, 100.3, 99.9, 100.1, 1000.0)                                  # above the previous day's high, closed below
_CONFIRM = (100.0, 100.1, 99.5, 99.6, 1000.0)                                 # closes below the bar before


def _h10(second_touch=True):
    d2 = [bar(99.0)] * 10 + [_TOUCH, bar(99.8), _TOUCH if second_touch else bar(99.9), _CONFIRM] + [bar(99.5)] * 5
    return sessions_frame([quiet_day(mid=101.0), quiet_day(mid=100.0), d2])  # previous day's high: 100.25


@pytest.mark.parametrize("count_in", ["last_bars", "session"])
def test_h10_fires_after_the_touches_on_the_close_beyond_the_bar_before_and_not_after_one_touch(count_in):
    df = _h10()
    confirm = slot(df, 2, 13)
    assert at(df, run("H10", df, count_in=count_in)) == [(confirm, -1, 100.25)]      # wrong above the previous day's high
    assert signals(run("H10", _h10(second_touch=False), count_in=count_in)) == {}
    one = _h10(second_touch=False)                     # one touch is enough: the next bar confirms, and again the confirming bar
    assert at(one, run("H10", one, count_in=count_in, touches=1)) == [(slot(one, 2, 11), -1, 100.25), (confirm, -1, 100.25)]
    s = run("H10", df, count_in=count_in, invalidation="ema")                  # the fast average is below the close
    assert signals(s) == {} and s.reason[df.index.get_loc(confirm)] == engine.REASON_GEOMETRY
    assert signals(run("H10", df, count_in=count_in, tol=0.0, sides="long")) == {}


# ================================================================================= every template, random walks
CASES = [("H6", "15m", {}), ("H6", "5m", {"minutes": 30, "vol_mult": 0.0, "invalidation": "mid_range"}),
         ("H7", "5m", {}), ("H7", "15m", {"bars_n": 4, "bars_min": 2, "invalidation": "vwap_buffer"}),
         ("H8", "5m", {"ext": 1.0}), ("H8", "5m", {"invalidation": "prev_bar", "no_new": 3}),
         ("H9", "5m", {"ma_n": 9, "held_n": 3}), ("H9", "15m", {"buf": 0.5}),
         ("H10", "5m", {"tol": 0.2}), ("H10", "5m", {"count_in": "session", "tol": 0.2, "invalidation": "ema"})]


def _walk(tf, seed):
    return synthetic_bars({"1m": 9000, "3m": 4000, "5m": 3000, "15m": 1500}[tf], tf=tf, seed=seed)


def _seed_with_signals(tid, tf, params):
    for seed in range(12):
        df = _walk(tf, seed)
        if len(signals(run(tid, df, tf, **params))) >= 2:
            return seed, df
    raise AssertionError(f"no random walk with signals for {tid} {params}")


@pytest.mark.parametrize("tid, tf, params", CASES)
def test_every_signal_is_its_trigger_and_its_invalidation_read_on_its_own(tid, tf, params):
    t = T[tid]
    seed, df = _seed_with_signals(tid, tf, params)
    recipe = C.compile_recipe(t, C.resolve_settings(t, tf, None, params))
    s = engine.compute(recipe, {tf: df})
    close = df["close"].to_numpy()
    for side, sign in (("long", 1), ("short", -1)):
        fire = recipe.part("trigger" if recipe.has_trigger else "setup", side)
        fired = engine._bool(engine._Eval(SymbolData("S", {tf: df}), tf, fire.params, {}, memo={}).ev(fire.ast, tf), df.index)
        inv = recipe.part("invalidation", side)
        level = (engine._num(engine._Eval(SymbolData("S", {tf: df}), tf, inv.params, {}, memo={}).ev(inv.ast, tf), df.index)
                 if inv is not None else None)
        for b, d in signals(s).items():
            if d != sign:
                continue
            assert fired[b], (tid, side, b)                                   # no signal without its trigger on that bar
            if level is not None:
                assert s.invalidation[b] == pytest.approx(level[b]), (tid, side, b)
            assert (s.invalidation[b] < close[b]) if d > 0 else (s.invalidation[b] > close[b])
            sessions = df.index.tz_convert(IST).date
            assert sessions[b] == sessions[int(s.setup_bar[b])]               # the instance lived in its own session


@pytest.mark.parametrize("tid, tf, params", CASES)
def test_mirror_short_equals_long_on_mirrored_prices(tid, tf, params):
    _, df = _seed_with_signals(tid, tf, params)
    df = whole(df)
    axis = K * 20
    s, m = run(tid, df, tf, **params), run(tid, mirror(df, axis), tf, **params)
    assert signals(s)
    assert signals(m) == {b: -d for b, d in signals(s).items()}
    for b in s.signal_bars():
        assert axis - m.invalidation[b] == pytest.approx(s.invalidation[b])


@pytest.mark.parametrize("tid, tf, params", CASES)
def test_no_look_ahead_a_cut_series_equals_the_full_run(tid, tf, params):
    _, df = _seed_with_signals(tid, tf, params)
    full = run(tid, df, tf, **params)
    marks = sorted({b + o for b in full.signal_bars() for o in (-1, 0, 1)} | set(range(full.warmup_bars, len(df), 97)))
    for b in [x for x in marks if 0 <= x < len(df)]:
        part = run(tid, df.iloc[:b + 1], tf, **params)
        np.testing.assert_array_equal(part.direction, full.direction[:b + 1], err_msg=f"{tid} t={b}")
        np.testing.assert_allclose(part.invalidation, full.invalidation[:b + 1], equal_nan=True, err_msg=f"{tid} t={b}")


@pytest.mark.parametrize("tid, tf, params", [("H6", "15m", {}), ("H7", "5m", {}), ("H8", "5m", {"ext": 1.0}),
                                             ("H9", "5m", {"ma_n": 9, "held_n": 3}), ("H10", "5m", {"tol": 0.2})])
def test_scan_equals_backtest_equals_worker(tid, tf, params):
    t = T[tid]
    strat = TemplateStrategy(t, C.resolve_settings(t, tf, params=params))
    df = _walk(tf, _seed_with_signals(tid, tf, params)[0])
    window = df.iloc[-max(strat.recipe.min_bars + 250, 730):].copy()                # a fixed TRAILING window (R6)
    scan = parity.scan_answers(strat.recipe, window)
    if sum(1 for a in scan.values() if a.direction) < 1:
        pytest.skip("no signal inside the trailing window")
    answers, trades = parity.backtest_answers(strat, window, "S", RISK)
    assert parity.compare(scan, answers, "backtest") == []
    signal_at = [i for i, a in enumerate(scan.values()) if a.direction]
    marks = sorted({j for i in signal_at for j in (i - 1, i, i + 1) if 0 <= j < len(window)} | set(range(0, len(window), 50))
                   | {len(window) - 1})
    worker = parity.worker_answers(strat, window, "S", marks)
    assert parity.compare(scan, worker, "worker") == []


def test_ids_round_trip_and_fit_the_shortest_column():
    for tid, t in T.items():
        for tf in t.allowed_timeframes:
            sid, settings = ids.adopt(t, tf)
            assert ids.decode(sid) == (t, settings)
        worst = {}
        for p in t.params:
            if p.type in ("int", "float"):
                worst[p.key] = int(p.max) if p.type == "int" else round(p.max - 0.000123, 6)
            elif p.type == "bool":
                worst[p.key] = not p.default
            else:
                worst[p.key] = p.choices[-1] if p.choices[-1] != p.default else p.choices[0]
        if tid == "H6":
            worst["minutes"] = 240
        if tid == "H7":
            worst["bars_min"] = worst["bars_n"]
        sid, settings = ids.adopt(t, "15m", "short", worst)
        assert len(sid) <= ids.MAX_LENGTH and ids.decode(sid) == (t, settings), sid


def test_the_adapter_reads_the_signal_with_its_provenance():
    df = sessions_frame(history(21) + [orb_day()])
    strat = TemplateStrategy(T["H6"], C.resolve_settings(T["H6"], "5m"))
    b = strat._compute(df).signal_bars()[0]
    sig = strat.analyze({ENGINE_LABEL["5m"]: df.iloc[:b + 1]}, "S")
    assert sig.is_tradeable and sig.stop_loss == pytest.approx(99.9) and sig.target1 is None and sig.risk_reward is None
    assert sig.provenance["template"] == "H6" and all(language.guard(r) == [] for r in sig.reasons)
    assert strat.analyze({ENGINE_LABEL["5m"]: df.iloc[:b]}, "S").is_tradeable is False       # the bar before: nothing yet


# ================================================================================= shorts: the intraday product
@pytest.mark.parametrize("tid", H_IDS)
def test_a_short_intraday_template_runs_with_the_intraday_holding_so_mis(tid):
    t = T[tid]
    for tf in t.allowed_timeframes:
        strat = TemplateStrategy(t, ids.adopt(t, tf, "short")[1])
        assert strat.label != "day"                                       # the adopt route: holding INTRADAY, never SWING
    assert product_for("INTRADAY", "UNDERLYING", "NSE") == "MIS"
    assert product_for("SWING", "UNDERLYING", "NSE") == "CNC"             # a swing short on cash is skipped by the worker


# ================================================================================= the API: flag, label, PAPER
def _api():
    from tests.test_contract_rules import _load_master
    from tests.test_tpl2_routes import FLAG, _org, client
    _load_master()
    return FLAG, _org, client


def test_family_h_is_hidden_with_the_flag_off_and_listed_with_its_label_with_it_on():
    FLAG, _org, client = _api()
    off = _org("h-off", FLAG, "screener_breakouts")
    cards = client.get("/api/strategy-templates", headers=off["headers"]).json()["templates"]
    assert cards and not [c for c in cards if c["family"] == "H"]
    assert all(c["reference_values"] is False for c in cards)                         # A-G: no label
    assert client.post("/api/strategy-templates/H6/adopt", headers=off["headers"], json={}).status_code == 404
    on = _org("h-on", FLAG, "screener_breakouts", "intraday_pack")
    listed = {c["id"]: c for c in client.get("/api/strategy-templates", headers=on["headers"]).json()["templates"]}
    assert {tid for tid, c in listed.items() if c["family"] == "H"} == set(FAMILY_H)
    assert set(listed) - set(FAMILY_H) == {c["id"] for c in cards}                    # A-G are the same cards either way
    for tid in H_IDS:
        card = listed[tid]
        assert card["family_name"] == "Intraday" and card["reference_values"] is True and card["available"], tid
        assert card["reads"]["long"] and card["reads"]["short"]
    body = client.post("/api/strategy-templates/H6/adopt", headers=on["headers"], json={"timeframe": "5m"}).json()
    assert body["deployment"] == {"strategy_id": body["strategy_id"], "timeframe": "5min", "holding": "INTRADAY", "mode": "PAPER"}
    assert f"{REFERENCE_LABEL}." in body["notes"] and WINDOW_NOTE in body["notes"]
    e5 = client.post("/api/strategy-templates/E5/adopt", headers=on["headers"], json={"timeframe": "5m"}).json()
    assert f"{REFERENCE_LABEL}." not in e5["notes"] and WINDOW_NOTE not in e5["notes"]


def test_a_family_h_strategy_id_runs_only_with_the_flag_on():
    from app.custom_strategies.resolver import load_template_strategy
    from app.db.models import User
    from tests.test_tpl2_routes import _set_flag
    FLAG, _org, client = _api()
    org = _org("h-resolve", FLAG, "screener_breakouts")
    sid = ids.adopt(T["H6"], "5m")[0]

    async def resolve():
        from tests.test_auth_api import _session_factory
        async with _session_factory() as session:
            user = User(tenant_id=org["tenant_id"], email="x@example.com")
            return await load_template_strategy(sid, user, session)
    with pytest.raises(KeyError):
        asyncio.run(resolve())
    _set_flag("intraday_pack", org["tenant_id"])
    assert asyncio.run(resolve()).template.id == "H6"


@pytest.mark.usefixtures("redis_required", "no_open_positions_from_earlier_tests")
def test_one_paper_trade_of_h6_through_the_deployment_route_and_the_worker(monkeypatch):
    """H6 end to end: adopt (a pre-fill) -> the deployment route (PAPER) -> the worker reads the template's signal on the
    breaking bar -> one PAPER trade, its stop the template's invalidation, nothing sent to the broker."""
    from sqlalchemy import select

    from app.db.models import OrderRecord, StrategyDeploymentRecord
    from app.workers import strategy_bars
    from tests.test_auth_api import _session_factory
    from tests.test_deployments_api import _upgrade_plan
    from tests.test_tpl2_routes import _set_flag
    from tests.test_trading_worker import _FakeBroker, _tenant, _trades, _worker
    FLAG, _, client = _api()

    t = _tenant(f"m94-h6-{datetime.now().timestamp():.0f}@example.com")
    _upgrade_plan(t["tenant_id"])
    for flag in (FLAG, "screener_breakouts", "intraday_pack"):
        _set_flag(flag, t["tenant_id"])
    pre = client.post("/api/strategy-templates/H6/adopt", headers=t["headers"], json={"timeframe": "5m"}).json()["deployment"]
    made = client.post("/api/deployments", headers=t["headers"], json={**pre, "symbol": "reliance", "instrument_kind": "UNDERLYING"})
    assert made.status_code == 201, made.text
    dep = made.json()
    assert dep["mode"] == "PAPER" and dep["strategy_id"].startswith("template:H6:1:5m:") and dep["timeframe"] == "5min"

    # 21 quiet sessions and the breakout session up to its breaking bar (09:45) plus the bar still forming (09:50)
    last = pd.Timestamp("2026-09-25")
    first = pd.bdate_range(end=last, periods=22)[0].strftime("%Y-%m-%d")
    day = orb_day()[:8]
    df = sessions_frame(history(21) + [day], first=first)
    assert df.index[-1] == pd.Timestamp("2026-09-25 04:20", tz="UTC")                   # 09:50 IST, still forming at 09:51
    expected = TemplateStrategy(T["H6"], C.resolve_settings(T["H6"], "5m"))._compute(df.iloc[:-1])
    assert expected.direction[-1] == 1 and expected.invalidation[-1] == pytest.approx(99.9)

    async def frames(session, user, market_data, symbol, exchange, base_timeframe, timeframes, *, now=None, store=None):
        return {"5min": df.copy()}
    monkeypatch.setattr(strategy_bars, "frames", frames)
    broker = _FakeBroker(ltp=101.6)
    worker = _worker(monkeypatch, broker)
    now = datetime(2026, 9, 25, 9, 51, tzinfo=IST)
    report = asyncio.run(worker.run_cycle(now=now))
    assert report.market_open and report.signals_executed == 1, report

    trades = [x for x in _trades(t["tenant_id"]) if x.deployment_id == dep["id"]]
    assert len(trades) == 1
    trade = trades[0]
    assert trade.mode == "PAPER" and trade.direction == "LONG" and trade.holding == "INTRADAY"
    assert trade.stop_loss == pytest.approx(99.9) and trade.strategy_id == dep["strategy_id"]
    assert broker.placed == []                                                          # PAPER: nothing reached the broker

    async def rows():
        async with _session_factory() as session:
            orders = list(await session.scalars(select(OrderRecord).where(OrderRecord.tenant_id == t["tenant_id"])))
            record = await session.get(StrategyDeploymentRecord, dep["id"])
            return orders, record
    orders, record = asyncio.run(rows())
    assert orders and all(o.mode == "PAPER" for o in orders) and record.mode == "PAPER"
    assert product_for(record.holding, record.instrument_kind, record.exchange) == "MIS"
    second = asyncio.run(worker.run_cycle(now=now + timedelta(seconds=20)))
    assert second.signals_executed == 0                                                 # one signal, one entry
