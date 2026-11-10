"""M94 PR-5 (docs/design/M94_INTRADAY_PACK.md section 4, T1-T5; OPEN_QUESTIONS M94-Q1 / Q2 / Q3 / Q7 / Q18-Q20): the rest of the
strategy template family H "Intraday" - H1 Fallen angel (spec T1, long), H2 ABCD (T2, long), H3 Reverse ABCD (T3, short), H4
Bull flag (T4, long) and H5 Bear flag (T5, short). Same structure and checks as PR-4 (tests/test_m94_pr4_templates.py):

* Each template's trigger and invalidation on hand-built bar series: it fires where it should, not where it should not;
  the same on random walks against the parts evaluated on their own by the screener runtime; no look-ahead (every series
  cut at many bars equals the full run); the short side is the long side on mirrored prices, and H3 / H5 are H2 / H4
  with the short side named (every rule equal).
* The recipe compiles on every allowed timeframe and every choice; each setup part runs as a screen on its own.
* Family H is listed (and adoptable) only with `intraday_pack` on; H4 / H5 (the flag breakout) also need the pattern
  breakouts' own flag. The "Reference values - edit before use" label; no time-of-day rule in any template (M94-Q7 / Q18).
* Shorts: an intraday template is adopted with the INTRADAY holding, so a short on cash shares uses MIS.
* Scan = backtest = worker (parity.py), and one PAPER trade of H5 (a short) end to end through the deployment route and
  the worker.
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
from app.strategy_templates.routes import FAMILY_FLAGS, REFERENCE_LABEL, WINDOW_NOTE
from tests.breakout_fixtures import history, profile, quiet_day, sessions_frame

P_IDS = ("H1", "H2", "H3", "H4", "H5")
T = {tid: library.get(tid) for tid in P_IDS}
NAMED_SIDE = {"H1": "long", "H2": "long", "H3": "short", "H4": "long", "H5": "short"}
MIRROR_OF = {"H3": "H2", "H5": "H4"}                          # the short template: the long one with the short side named
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


def chain(closes, start, v=1000.0):
    """Bars walking through `closes`, each opening at the close before it (0.1 beyond each end)."""
    out, o = [], start
    for c in closes:
        out.append(bar(c, 0.1, 0.1, o, v))
        o = c
    return out


def shift(bars, k):
    return [(o + k, h + k, lo + k, c + k, v) for o, h, lo, c, v in bars]


def slot(df, day, k, minutes=5):
    """The bar start of slot k (0 = 09:15 IST) of the session `day` (0-based) of a sessions_frame."""
    first = df.index.normalize().unique()[day]
    return first + pd.Timedelta(hours=3, minutes=45 + minutes * k)


def swing_day():
    """A session of swings, so every swing series has a value before the test session (time-of-day volume)."""
    out, o = [], 100.0
    for i in range(75):
        c = 100.0 + (1.5 if (i // 6) % 2 == 0 else -1.5) * ((i % 6) / 5)
        out.append((o, max(o, c) + 0.1, min(o, c) - 0.1, c, profile(i)))
        o = c
    return out


def _parts(t):
    data = t.model_dump(by_alias=True)
    return {k: v for k, v in data.items() if k not in ("id", "name", "idea", "sides_default")}


# ================================================================================= the schema and the library
@pytest.mark.parametrize("tid", P_IDS)
def test_each_template_is_family_h_with_reference_values_and_its_named_side(tid):
    t = T[tid]
    assert t.family == "H" and t.reference_values and t.style == "Intraday (session-based)"
    assert t.sides_default == NAMED_SIDE[tid]
    assert FAMILY_FLAGS["H"] == "intraday_pack"


@pytest.mark.parametrize("short, long", sorted(MIRROR_OF.items()))
def test_the_short_templates_are_the_long_ones_with_the_short_side_named(short, long):
    assert _parts(T[short]) == _parts(T[long])
    assert T[short].sides_default == "short" and T[long].sides_default == "long" and T[short].name != T[long].name


@pytest.mark.parametrize("tid", P_IDS)
def test_the_recipe_compiles_on_every_timeframe_and_choice_and_its_text_passes_the_guard(tid):
    t = T[tid]
    assert C.validate_template(t) == []
    assert t.allowed_timeframes == ("5m", "1m", "3m", "15m") and set(t.allowed_timeframes) <= set(ENGINE_LABEL)
    for tf in t.allowed_timeframes:
        settings = C.resolve_settings(t, tf)
        recipe = C.compile_recipe(t, settings)
        assert recipe.chosen_setup.session_bound                     # an instance never outlives its session
        flags = {"screener_breakouts", "intraday_pack"} | ({"screener_breakouts_bk4"} if tid in ("H4", "H5") else set())
        assert recipe.required_flags == flags
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


# the bar before; a percent (H1's open against the previous close, in the setting's unit)
_STRUCTURE = re.compile(r"(\[1\])|(\* 100 >=)")


@pytest.mark.parametrize("tid", P_IDS)
def test_every_number_is_a_setting_and_no_clock_rule_and_signals_only(tid):
    t = T[tid]
    texts = _texts(t)
    assert texts
    for text in texts:
        assert not re.search(r"(?<![\w$])\d", _STRUCTURE.sub("", text)), text            # numbers only in settings (I2)
        assert "TimeOfDay" not in text                                                  # M94-Q7 / Q18: no clock rule
        assert not re.search(r"\b(target|lots|strike|premium|order|buy|sell|stop)\b", text, re.I)
    data = t.model_dump(by_alias=True)
    assert not {"target", "targets", "lots", "quantity", "strike", "premium", "order"} & set(data)


@pytest.mark.parametrize("tid", P_IDS)
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


def test_h4_reads_the_library_flag_breakout_with_its_settings_as_named_arguments():
    """H4 / H5 read the B6 function (as template B6) with the PR-1 named arguments; the template defaults are B6's own."""
    from app.screener.breakouts.params import DEFAULTS
    defaults = {p.key: p.default for p in T["H4"].params}
    assert (defaults["pole"], defaults["pole_bars"], defaults["flag_min"], defaults["flag_max"], defaults["retrace"]) == (
        DEFAULTS["pole_min_atr"], DEFAULTS["pole_max_bars"], DEFAULTS["flag_min_bars"], DEFAULTS["flag_max_bars"],
        DEFAULTS["flag_max_retrace"])
    assert defaults["max_flags"] == 2                                          # the spec's reference value
    for text in (T["H4"].setup.long, T["H4"].setup.short):
        assert text.count("PatternBreakout(") == 2 and text.count("pole=$pole, pole_bars=$pole_bars") == 2
    with pytest.raises(C.TemplateError):
        C.resolve_settings(T["H4"], "5m", params={"flag_min": 20, "flag_max": 10})


# ================================================================================= H1 Fallen angel
_BASE = [(101.6, 101.7, 101.4, 101.5, 1000.0), (101.5, 101.75, 101.45, 101.6, 1000.0), (101.6, 101.8, 101.5, 101.55, 1000.0),
         (101.55, 101.7, 101.4, 101.65, 1000.0), (101.65, 101.8, 101.5, 101.6, 1000.0)]   # slots 12-16: 101.4-101.8


def _h1(brk=(101.6, 102.2, 101.55, 102.1, 3000.0), open_=101.0, rest=None):
    """The previous close 100.1; the session opens at `open_`, rises to 104.1 (slots 1-6), falls (7-11), holds a tight base
    (12-16), and slot 17 closes above the base on three times the base's volume."""
    d1 = [(open_, max(open_, 101.2) + 0.1, min(open_, 101.2) - 0.1, 101.2, 3000.0)]
    d1 += chain([101.6, 102.1, 102.6, 103.1, 103.6, 104.0], 101.2) + chain([103.5, 103.0, 102.5, 102.0, 101.6], 104.0)
    return sessions_frame([quiet_day(), d1 + _BASE + [brk] + (rest or [bar(102.2)] * 6)])


def test_h1_fires_on_the_first_close_above_the_base_after_the_fall_and_not_without_it():
    df = _h1()
    brk = slot(df, 1, 17)
    assert at(df, run("H1", df)) == [(brk, 1, 101.4)]                         # wrong below the base's low
    atr = float(ev("ATR(14)[1]", df).loc[brk])
    assert at(df, run("H1", df, buf=0.5)) == [(brk, 1, round(101.4 - 0.5 * atr, 4))]
    assert at(df, run("H1", df, gap_pct=0.8)) == [(brk, 1, 101.4)]            # opened 0.9 % above the previous close
    assert signals(run("H1", df, gap_pct=2.0)) == {}
    fall = (104.1 - 101.4) / atr
    assert signals(run("H1", df, drop_atr=round(fall - 0.1, 1))) and signals(run("H1", df, drop_atr=round(fall + 0.1, 1))) == {}
    assert signals(run("H1", df, base_atr=0.2)) == {}                         # the base is not that tight
    assert signals(run("H1", df, vol_x=2.0)) and signals(run("H1", df, vol_x=4.0)) == {}   # 3x the base's largest volume
    assert signals(run("H1", df, sides="short")) == {}
    below = _h1(open_=99.8)                                                   # opened below the previous close
    assert signals(run("H1", below)) == {}
    inside = _h1(brk=(101.6, 101.75, 101.5, 101.7, 3000.0), rest=[bar(101.6, 0.1, 0.1)] * 6)   # no close above the base
    assert signals(run("H1", inside)) == {}


def test_h1_base_is_in_the_session_and_the_short_side_is_its_mirror():
    df = _h1()
    s = run("H1", df, base_n=20)                                              # 20 bars before the break reach the last session
    assert signals(s) == {}
    m = run("H1", mirror(df, 200.0), sides="short")
    assert at(mirror(df, 200.0), m) == [(slot(df, 1, 17), -1, 98.6)]          # 200 - 101.4


# ================================================================================= H2 ABCD / H3 Reverse ABCD
def _abcd(c_low=101.1, pull=True):
    """Slot 4 makes the session low A (99.5); slots 5-10 a first leg to a new session high B (102.8, on three times the
    time of day's volume); slots 11-14 a pullback to C (`c_low`); slots 15-18 the turn back up."""
    d = [bar(100.0)] * 4 + [(100.0, 100.1, 99.5, 99.7, 3000.0)]
    d += chain([100.2, 100.7, 101.2, 101.7, 102.2, 102.7], 99.7, v=3000.0)
    if pull:
        d += chain([102.3, 101.8, 101.4, c_low + 0.1], 102.7) + chain([101.6, 102.1, 102.4, 102.5], c_low + 0.1)
    else:
        d += chain([103.2, 103.7, 104.2, 104.7, 105.2], 102.7)
    return sessions_frame([swing_day()] + history(21) + [d + [bar(d[-1][3])] * 5])


def _early_low():
    """A higher low (99.3) confirmed BEFORE the new session high, then a straight run up: no pullback after B."""
    d = [bar(100.0)] * 4 + [(100.0, 100.1, 98.8, 98.9, 3000.0)]
    d += chain([99.3, 99.8, 100.1], 98.9) + chain([99.6, 99.4], 100.1) + chain([99.9, 100.4, 100.9, 101.4, 101.9], 99.4, v=3000.0)
    return sessions_frame([swing_day()] + history(21) + [d + [bar(101.9)] * 5])


def test_h2_fires_on_the_turn_up_from_a_confirmed_higher_low_after_the_new_session_high():
    df = _abcd()
    turn = slot(df, 22, 16)                                                   # C (101.1) is confirmed on this bar
    s = run("H2", df)
    assert at(df, s) == [(turn, 1, 101.1)]                                    # wrong below C
    assert df.index[int(s.setup_bar[df.index.get_loc(turn)])] == slot(df, 22, 6)   # the new session high (B's leg)
    assert ev("SwingLow(0)", df).loc[slot(df, 22, 15)] != 101.1               # C is not known on the bar before
    atr = float(ev("ATR(14)[1]", df).loc[turn])
    assert at(df, run("H2", df, buf=0.5)) == [(turn, 1, round(101.1 - 0.5 * atr, 4))]
    assert at(df, run("H2", df, turn_n=3)) == [(turn, 1, 101.1)]
    assert signals(run("H2", df, expiry=8)) == {}                             # the turn came ten bars after the setup
    assert signals(run("H2", df, vol_mult=10.0)) == {}                        # no new session high on that volume
    assert signals(run("H2", df, sides="short")) == {}


def test_h2_does_not_fire_without_a_higher_low_or_without_a_pullback_after_the_new_high():
    assert signals(run("H2", _abcd(c_low=99.3))) == {}                        # the pullback broke the session low A
    assert signals(run("H2", _abcd(pull=False))) == {}                        # no pullback at all
    df = _early_low()
    assert signals(run("H2", df)) == {}
    # the spec's draft trigger alone would fire there (its higher low came before the new high): C must follow B, so the
    # trigger also reads the latest confirmed swing high at the session high and a swing low confirmed after it
    draft = "SwingLow(0) > SessionLow() AND close > SwingLow(0) AND close > Max(high, 1)[1]"
    b = df.index.get_loc(slot(df, 22, 12))
    assert bool(ev(draft, df).iloc[b]) and bool(ev('DayExtremeBreak("up", 1.5, true, 0, 0)', df).iloc[b - 1])


def test_h3_is_the_mirror_of_h2():
    df = _abcd()
    m = mirror(df, 200.0)
    assert at(m, run("H3", m)) == [(slot(df, 22, 16), -1, 98.9)]              # wrong above C (200 - 101.1)
    assert signals(run("H3", df)) == {} and at(df, run("H3", df, sides="long")) == at(df, run("H2", df))


# ================================================================================= H4 Bull flag / H5 Bear flag
_FLAG = [(103.0, 103.0, 102.6, 102.7, 800.0), (102.7, 102.9, 102.5, 102.8, 800.0), (102.8, 102.95, 102.4, 102.6, 800.0),
         (102.6, 102.9, 102.45, 102.75, 800.0), (102.75, 103.0, 102.5, 102.7, 800.0), (102.7, 102.9, 102.5, 102.8, 800.0)]
_BREAK = (102.8, 103.6, 102.7, 103.5, 5000.0)


def _flag_day(flag=_FLAG, brk=_BREAK):
    """Slots 4-9: a strong move from 100 to 103.1; slots 10-15 a tight, shallow consolidation; slot 16 the break."""
    return [bar(100.0)] * 4 + chain([100.5, 101.0, 101.5, 102.0, 102.5, 103.0], 100.0) + list(flag) + [brk]


def test_h4_fires_on_the_first_close_above_the_flag_at_a_new_session_high_and_not_without_it():
    df = sessions_frame(history(3) + [_flag_day() + [bar(103.5)] * 6])
    brk = slot(df, 3, 16)
    assert at(df, run("H4", df)) == [(brk, 1, 102.4)]                         # wrong below the consolidation's low
    for kw in ({"vol_mult": 8.0}, {"pole": 10.0}, {"retrace": 0.1}, {"flag_min": 8}, {"pole": 4.0, "pole_bars": 2}):
        assert signals(run("H4", df, **kw)) == {}, kw                          # each setting reaches the flag rule
    assert at(df, run("H4", df, pole=4.0)) == [(brk, 1, 102.4)]               # ... the move's bars too (2 bars: too small)
    assert signals(run("H4", df, sides="short")) == {}
    # the flag breaks but the bar stays below the session high (the strong move's top 103.1): no signal
    low = _flag_day(flag=shift(_FLAG, -0.3), brk=(102.5, 103.05, 102.4, 102.95, 5000.0))
    low_df = sessions_frame(history(3) + [low + [bar(102.9)] * 6])
    assert bool(ev('PatternBreakout("up", "flag", 1.5, 0.2)', low_df).loc[slot(low_df, 3, 16)])
    assert signals(run("H4", low_df)) == {}


def test_h4_counts_only_the_first_flags_of_a_session():
    two = (_flag_day() + chain([103.0, 102.5, 102.0, 101.8], 103.5) + chain([102.3, 102.8, 103.3, 103.8, 104.3, 104.8], 101.8)
           + shift(_FLAG, 1.8) + [(104.6, 105.4, 104.5, 105.3, 5000.0)] + [bar(105.3)] * 4)
    df = sessions_frame(history(3) + [two])
    first, second = (slot(df, 3, 16), 1, 102.4), (slot(df, 3, 33), 1, 104.2)
    assert at(df, run("H4", df)) == [first, second]                           # two flags: the reference value 2
    assert at(df, run("H4", df, max_flags=1)) == [first]


def test_h5_is_the_mirror_of_h4():
    df = sessions_frame(history(3) + [_flag_day() + [bar(103.5)] * 6])
    m = mirror(df, 200.0)
    assert at(m, run("H5", m)) == [(slot(df, 3, 16), -1, 97.6)]               # wrong above the consolidation's high
    assert signals(run("H5", df)) == {} and at(df, run("H5", df, sides="long")) == at(df, run("H4", df))


# ================================================================================= every template, random walks
CASES = [("H1", "5m", {"base_atr": 1.5, "drop_atr": 2.0}), ("H1", "15m", {"base_atr": 2.0, "base_n": 3, "vol_x": 1.0, "buf": 0.5}),
         ("H2", "5m", {}), ("H2", "15m", {"vol_mult": 0.0, "expiry": 40, "turn_n": 3, "buf": 0.5}),
         ("H3", "5m", {"vwap": False, "degree": 1, "expiry": 60}),
         ("H4", "5m", {"vol_mult": 0.0, "pole": 2.0}), ("H4", "15m", {"vol_mult": 1.0, "pole": 2.0, "max_flags": 1}),
         ("H5", "5m", {"vol_mult": 0.0, "pole": 2.0, "flag_min": 3, "flag_max": 8})]


def _walk(tf, seed):
    return synthetic_bars({"5m": 3000, "15m": 1500}[tf], tf=tf, seed=seed)


def _seed_with_signals(tid, tf, params):
    for seed in range(12):
        df = _walk(tf, seed)
        if len(signals(run(tid, df, tf, "both", **params))) >= 2:
            return seed, df
    raise AssertionError(f"no random walk with signals for {tid} {params}")


@pytest.mark.parametrize("tid, tf, params", CASES)
def test_every_signal_is_its_trigger_and_its_invalidation_read_on_its_own(tid, tf, params):
    t = T[tid]
    seed, df = _seed_with_signals(tid, tf, params)
    recipe = C.compile_recipe(t, C.resolve_settings(t, tf, "both", params))
    s = engine.compute(recipe, {tf: df})
    close = df["close"].to_numpy()
    for side, sign in (("long", 1), ("short", -1)):
        fire = recipe.part("trigger" if recipe.has_trigger else "setup", side)
        fired = engine._bool(engine._Eval(SymbolData("S", {tf: df}), tf, fire.params, {}, memo={}).ev(fire.ast, tf), df.index)
        setup = recipe.part("setup", side)
        armed = engine._bool(engine._Eval(SymbolData("S", {tf: df}), tf, setup.params, {}, memo={}).ev(setup.ast, tf), df.index)
        inv = recipe.part("invalidation", side)
        level = engine._num(engine._Eval(SymbolData("S", {tf: df}), tf, inv.params, {}, memo={}).ev(inv.ast, tf), df.index)
        for b, d in signals(s).items():
            if d != sign:
                continue
            assert fired[b], (tid, side, b)                                   # no signal without its trigger on that bar
            assert armed[int(s.setup_bar[b])], (tid, side, b)                 # ... nor without its setup on the setup bar
            assert s.invalidation[b] == pytest.approx(level[b]), (tid, side, b)
            assert (s.invalidation[b] < close[b]) if d > 0 else (s.invalidation[b] > close[b])
            sessions = df.index.tz_convert(IST).date
            assert sessions[b] == sessions[int(s.setup_bar[b])]               # the instance lived in its own session


@pytest.mark.parametrize("tid, tf, params", CASES)
def test_mirror_short_equals_long_on_mirrored_prices(tid, tf, params):
    _, df = _seed_with_signals(tid, tf, params)
    df = whole(df)
    axis = K * 20
    s, m = run(tid, df, tf, "both", **params), run(tid, mirror(df, axis), tf, "both", **params)
    assert signals(s)
    assert signals(m) == {b: -d for b, d in signals(s).items()}
    for b in s.signal_bars():
        assert axis - m.invalidation[b] == pytest.approx(s.invalidation[b])
    # the named pair: the short template on mirrored prices is the long template (H3 of H2, H5 of H4, H1 its own side)
    long_tid = MIRROR_OF.get(tid, tid)
    short_tid = {"H1": "H1", "H2": "H3", "H4": "H5"}[long_tid]
    longs = run(long_tid, df, tf, "long", **params)
    shorts = run(short_tid, mirror(df, axis), tf, "short", **params)
    assert signals(shorts) == {b: -d for b, d in signals(longs).items()}


@pytest.mark.parametrize("tid, tf, params", CASES)
def test_no_look_ahead_a_cut_series_equals_the_full_run(tid, tf, params):
    _, df = _seed_with_signals(tid, tf, params)
    full = run(tid, df, tf, "both", **params)
    marks = sorted({b + o for b in full.signal_bars()[:8] for o in (-1, 0, 1)} | set(range(full.warmup_bars, len(df), 97)))
    for b in [x for x in marks if 0 <= x < len(df)]:
        part = run(tid, df.iloc[:b + 1], tf, "both", **params)
        np.testing.assert_array_equal(part.direction, full.direction[:b + 1], err_msg=f"{tid} t={b}")
        np.testing.assert_allclose(part.invalidation, full.invalidation[:b + 1], equal_nan=True, err_msg=f"{tid} t={b}")


@pytest.mark.parametrize("tid, tf, params", [("H1", "5m", {"base_atr": 1.5, "drop_atr": 2.0}), ("H2", "5m", {}),
                                             ("H3", "5m", {"vwap": False, "expiry": 40}),
                                             ("H4", "5m", {"vol_mult": 0.0, "pole": 2.0}),
                                             ("H5", "5m", {"vol_mult": 0.0, "pole": 2.0, "flag_min": 3, "flag_max": 8})])
def test_scan_equals_backtest_equals_worker(tid, tf, params):
    t = T[tid]
    strat = TemplateStrategy(t, C.resolve_settings(t, tf, "both", params=params))
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
    """Every setting at the far end of what the form offers (its own steps) fits the 80 characters; values with many
    decimals on every setting at once are refused in words (as for every template), never cut."""
    for tid, t in T.items():
        for tf in t.allowed_timeframes:
            sid, settings = ids.adopt(t, tf)
            assert ids.decode(sid) == (t, settings)
        worst = {}
        for p in t.params:
            if p.type in ("int", "float"):
                worst[p.key] = int(p.max) if p.type == "int" else round(p.max - p.step, 2)
            elif p.type == "bool":
                worst[p.key] = not p.default
            else:
                worst[p.key] = p.choices[-1] if p.choices[-1] != p.default else p.choices[0]
        sid, settings = ids.adopt(t, "15m", "both", worst)
        assert len(sid) <= ids.MAX_LENGTH and ids.decode(sid) == (t, settings), sid
    many = {p.key: int(p.max) if p.type == "int" else round(p.max - 0.000123, 6) for p in T["H1"].params}
    with pytest.raises(C.TemplateError, match="fewer decimals"):
        ids.adopt(T["H1"], "15m", "both", many)


def test_the_adapter_reads_the_signal_with_its_provenance():
    df = sessions_frame(history(3) + [_flag_day() + [bar(103.5)] * 6])
    strat = TemplateStrategy(T["H4"], C.resolve_settings(T["H4"], "5m"))
    b = strat._compute(df).signal_bars()[0]
    sig = strat.analyze({ENGINE_LABEL["5m"]: df.iloc[:b + 1]}, "S")
    assert sig.is_tradeable and sig.stop_loss == pytest.approx(102.4) and sig.target1 is None and sig.risk_reward is None
    assert sig.provenance["template"] == "H4" and all(language.guard(r) == [] for r in sig.reasons)
    assert strat.analyze({ENGINE_LABEL["5m"]: df.iloc[:b]}, "S").is_tradeable is False       # the bar before: nothing yet


# ================================================================================= shorts: the intraday product
@pytest.mark.parametrize("tid", P_IDS)
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


def test_the_five_templates_are_hidden_with_the_flag_off_and_listed_with_their_label_with_it_on():
    FLAG, _org, client = _api()
    off = _org("h5-off", FLAG, "screener_breakouts", "screener_breakouts_bk4")
    cards = client.get("/api/strategy-templates", headers=off["headers"]).json()["templates"]
    assert cards and not [c for c in cards if c["family"] == "H"]
    for tid in P_IDS:
        assert client.post(f"/api/strategy-templates/{tid}/adopt", headers=off["headers"], json={}).status_code == 404
    on = _org("h5-on", FLAG, "screener_breakouts", "intraday_pack")
    listed = {c["id"]: c for c in client.get("/api/strategy-templates", headers=on["headers"]).json()["templates"]}
    for tid in P_IDS:
        card = listed[tid]
        assert card["family_name"] == "Intraday" and card["reference_values"] is True, tid
        side, other = NAMED_SIDE[tid], {"long": "short", "short": "long"}[NAMED_SIDE[tid]]
        assert card["reads"][side] and card["reads"][other] is None and card["sides_default"] == side   # the named side
        assert card["available"] is (tid not in ("H4", "H5")), tid            # the flag breakout needs its own flag too
    bk4 = _org("h5-bk4", FLAG, "screener_breakouts", "intraday_pack", "screener_breakouts_bk4")
    listed = {c["id"]: c for c in client.get("/api/strategy-templates", headers=bk4["headers"]).json()["templates"]}
    assert all(listed[tid]["available"] for tid in P_IDS)
    for tid in P_IDS:
        body = client.post(f"/api/strategy-templates/{tid}/adopt", headers=bk4["headers"], json={"timeframe": "5m"}).json()
        assert body["deployment"] == {"strategy_id": body["strategy_id"], "timeframe": "5min", "holding": "INTRADAY", "mode": "PAPER"}
        assert f"{REFERENCE_LABEL}." in body["notes"] and WINDOW_NOTE in body["notes"]
        assert ids.decode(body["strategy_id"])[1].sides == NAMED_SIDE[tid]


@pytest.mark.usefixtures("redis_required", "no_open_positions_from_earlier_tests")
def test_one_paper_trade_of_h5_a_short_through_the_deployment_route_and_the_worker(monkeypatch):
    """H5 end to end: adopt (a pre-fill) -> the deployment route (PAPER) -> the worker reads the template's signal on the
    breaking bar -> one PAPER short, its stop the template's invalidation, the intraday holding (MIS), nothing sent to the
    broker."""
    from sqlalchemy import select

    from app.db.models import OrderRecord, StrategyDeploymentRecord
    from app.workers import strategy_bars
    from tests.test_auth_api import _session_factory
    from tests.test_deployments_api import _upgrade_plan
    from tests.test_tpl2_routes import _set_flag
    from tests.test_trading_worker import _FakeBroker, _tenant, _trades, _worker
    FLAG, _, client = _api()

    t = _tenant(f"m94-h5-{datetime.now().timestamp():.0f}@example.com")
    _upgrade_plan(t["tenant_id"])
    for flag in (FLAG, "screener_breakouts", "screener_breakouts_bk4", "intraday_pack"):
        _set_flag(flag, t["tenant_id"])
    pre = client.post("/api/strategy-templates/H5/adopt", headers=t["headers"], json={"timeframe": "5m"}).json()["deployment"]
    made = client.post("/api/deployments", headers=t["headers"], json={**pre, "symbol": "reliance", "instrument_kind": "UNDERLYING"})
    assert made.status_code == 201, made.text
    dep = made.json()
    assert dep["mode"] == "PAPER" and dep["strategy_id"].startswith("template:H5:1:5m:") and dep["timeframe"] == "5min"

    # three quiet sessions and the bear flag session (the bull flag mirrored) up to its breaking bar (10:35) plus the bar
    # still forming (10:40)
    last = pd.Timestamp("2026-09-25")
    first = pd.bdate_range(end=last, periods=4)[0].strftime("%Y-%m-%d")
    df = mirror(sessions_frame(history(3) + [_flag_day() + [bar(103.5)]], first=first), 200.0)
    assert df.index[-1] == pd.Timestamp("2026-09-25 05:10", tz="UTC")                   # 10:40 IST, still forming at 10:41
    expected = TemplateStrategy(T["H5"], C.resolve_settings(T["H5"], "5m"))._compute(df.iloc[:-1])
    assert expected.direction[-1] == -1 and expected.invalidation[-1] == pytest.approx(97.6)

    async def frames(session, user, market_data, symbol, exchange, base_timeframe, timeframes, *, now=None, store=None):
        return {"5min": df.copy()}
    monkeypatch.setattr(strategy_bars, "frames", frames)
    broker = _FakeBroker(ltp=96.6)
    worker = _worker(monkeypatch, broker)
    now = datetime(2026, 9, 25, 10, 41, tzinfo=IST)
    report = asyncio.run(worker.run_cycle(now=now))
    assert report.market_open and report.signals_executed == 1, report

    trades = [x for x in _trades(t["tenant_id"]) if x.deployment_id == dep["id"]]
    assert len(trades) == 1
    trade = trades[0]
    assert trade.mode == "PAPER" and trade.direction == "SHORT" and trade.holding == "INTRADAY"
    assert trade.stop_loss == pytest.approx(97.6) and trade.strategy_id == dep["strategy_id"]
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
