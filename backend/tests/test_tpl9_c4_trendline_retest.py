"""Strategy templates TPL-9 (owner decision TPL-Q18, 23 Oct): the trendline retest read at the retest bar is a NEW choice,
`RetestEntry(.., "trendline_projected", ..)` (and RetestEntryRef / the B9 screen), while the older `trendline` choice keeps
its level frozen at the breakout bar, byte for byte; the template C4 (trendline breakout and retest) uses the new choice.

The pins: the older trendline retest's outputs (RetestEntry, every RetestEntryRef price, the B9 screen, the retest events)
and the library's params hashes / ids, computed with the code before this change (main aa175fea). Then C4 against the
owner order's section 8 gate - must fire long and short on a hand-made falling-line retest that only the projected line
holds, must not (a close back through the line first, a weak retest unless a touch is enough, too late, the breakout's own
distance / touches), the invalidation choices by value, S6, the exit along the projected line, the warm-up, "exactly the
retests" on random data, the truncation test over many t, the mirror test, scan = backtest = options backtest = worker on
one trailing window (the TPL-1 harness), property tests on random valid settings, the language guard, "no number written
into a rule", the ids, the adapter's provenance and flags, PAPER only and the API listing only with `strategy_templates`."""
import hashlib
import json
import re

import numpy as np
import pandas as pd
import pytest
from hypothesis import HealthCheck, given
from hypothesis import settings as hsettings
from hypothesis import strategies as st

from app.backtest.options_engine import OptionBacktestConfig
from app.core.enums import OptionStrategy
from app.core.models import RiskConfig
from app.quality.lookahead import synthetic_bars
from app.screener.breakouts import params as bp
from app.screener.breakouts.lines import _lines
from app.screener.breakouts.retest import BASES, PROJECTED, base_params, retest_events
from app.screener.breakouts.screens import Bars, evaluate_screen
from app.screener.parser import parse
from app.screener.registry import RETEST_OF_CHOICES, RETEST_REF_OF
from app.screener.runtime import SymbolData, evaluate
from app.strategy_templates import compile as C
from app.strategy_templates import engine, ids, language, library, parity
from app.strategy_templates.adapter import ENGINE_LABEL, TemplateStrategy
from tests.breakout_bk3_fixtures import frame, path

T, B4 = library.get("C4"), library.get("B4")
RISK = RiskConfig(capital=10_000_000, risk_per_trade_pct=0.5)
K = 400.0                                                     # mirror axis: price -> K - price
TOUCH = {"retest_reversal": False}
NEW = "trendline_projected"


def run(df, tf="15m", template=T, **params):
    return engine.compute(C.compile_recipe(template, C.resolve_settings(template, tf, params=params)), {tf: df})


def ev(text, df, tf="15m"):
    return evaluate(parse(text), SymbolData("S", {tf: df}), base_tf=tf)


def mirror(df):
    out = df.copy()
    out["open"], out["close"] = K - df["open"], K - df["close"]
    out["high"], out["low"] = K - df["low"], K - df["high"]
    return out


def signals(s, after=0):
    return {t: int(s.direction[t]) for t in s.signal_bars() if t >= after}


# ================================================================================= the older trendline retest, pinned
PIN_CASES = ['RetestEntry("up", "trendline", 10, TRUE)', 'RetestEntry("down", "trendline", 10, TRUE)',
             'RetestEntry("up", "trendline", 20, FALSE)', 'RetestEntry("down", "trendline", 20, FALSE)',
             'RetestEntry("up", "trendline", 15, FALSE, k=0.1, touches=3)', 'RetestEntry("down", "trendline", 15, FALSE, k=0, touches=2)']
PIN_WHATS = ("level", "retest_low", "base_high", "base_low", "held_level", "stop_level_atr", "stop_base", "stop_midpoint")
PIN_DATA = ((1, "15m"), (5, "15m"), (9, "15m"), (7, "5m"), (3, "1d"))
# SHA-256 of the outputs below with the code BEFORE this change (main aa175fea), floats as repr (byte for byte). The
# touch retests read no reversal rule; the reversal retests read app/price_action/reversal.py, so a deliberate change of
# that rule (e.g. TPL-F6) re-pins REVERSAL_SHA256 in its own PR, with the count of signals that change.
TOUCH_SHA256, TOUCH_COUNT = "f1466b20f301cdbca2bfdc3ecbd2494346d7e950a00a9ebb079dc3146fd56489", 14923
REVERSAL_SHA256, REVERSAL_COUNT = "ee9e54e6f0e3bd5faa8de5314c7654368f42dac7b40d509c79e77f0ca4d9b688", 2853
# the params hash (with the price-action settings' fingerprint held fixed) and the default adopted id of every library
# template outside family G (G has its own pins), and of every archived version, before this change. Re-pinned by
# TPL-F6 (STRUCTURE_VERSION "2" enters the params hash of A2, B2, E2, E3 and F2 only; ids unchanged); was 11728d86...
TEMPLATES_SHA256, TEMPLATES_COUNT = "c9ee3604e1414caed2e64390dc3640d61c529bc7b5e21f4347f255a7ecddf959", 64


def _values(a):
    a = np.asarray(a, dtype=float)
    return [[int(i), repr(float(a[i]))] for i in np.flatnonzero(np.isfinite(a))]


def trendline_outputs(reversal):
    """Every output of the `trendline` retest with the reversal rule on (True) or off (False)."""
    out = {}
    flag = "TRUE" if reversal else "FALSE"
    for seed, tf in PIN_DATA:
        df = synthetic_bars(900, tf=tf, seed=seed)
        data = SymbolData("S", {tf: df})
        for case in [c for c in PIN_CASES if flag in c]:
            out[f"{seed}|{tf}|{case}"] = [int(x) for x in np.flatnonzero(evaluate(parse(case), data, base_tf=tf).to_numpy(bool))]
        for side in ("up", "down"):
            for what in PIN_WHATS:
                for args in (("10, TRUE, 1.5",) if reversal else ("10, FALSE", "12, FALSE, 0.5, k=0.1, touches=3")):
                    text = f'RetestEntryRef("{what}", "{side}", "trendline", {args})'
                    out[f"{seed}|{tf}|{text}"] = _values(evaluate(parse(text), data, base_tf=tf).to_numpy(float))
        p = bp.settings(retest_of="trendline", retest_reversal=reversal)
        screen = evaluate_screen(df, "B9", p)
        for side in ("up", "down"):
            o = screen[side]
            row = {c: _values(o[c].to_numpy(float)) for c in ("level", "fail_level", "base_high", "base_low")}
            row["signal"] = [int(x) for x in np.flatnonzero(o["signal"].to_numpy(bool))]
            row["event"] = sorted(set(map(str, o["event"][o["signal"]])))
            out[f"{seed}|{tf}|B9|{side}"] = row
        b = Bars(df, p)
        for sign in (1, -1):
            out[f"{seed}|{tf}|events|{sign}"] = [[e.breakout, e.bar, repr(e.level), repr(e.base_high), repr(e.base_low), e.slope, e.c0]
                                                 for e in retest_events(b, b.side(sign), p)]
    return out


def _count(out):
    return sum(len(v["signal"]) if isinstance(v, dict) else len(v) for v in out.values())


def _sha(out):
    return hashlib.sha256(json.dumps(out, sort_keys=True).encode()).hexdigest()


def template_pins():
    out = {}
    for t in library.templates() + library.archived():
        if t.family == "G":
            continue
        tf = t.allowed_timeframes[0]
        s = C.resolve_settings(t, tf)
        out[f"{t.id}.v{t.version}"] = C.params_hash(t, s)
        if t.version == library.get(t.id).version and t.id != "C4":
            out[f"{t.id}.id"] = ids.adopt(t, tf)[0]
    out.pop("C4.v1", None)                                                   # the new template is not part of the pin
    out.pop("A1.v1", None)                                                   # M55 T5: neither is A1 (added after the pin)
    out.pop("A1.id", None)
    return out


def test_the_older_trendline_touch_retest_is_byte_identical():
    out = trendline_outputs(False)
    assert _count(out) == TOUCH_COUNT and _sha(out) == TOUCH_SHA256


def test_the_older_trendline_reversal_retest_is_byte_identical():
    out = trendline_outputs(True)
    assert _count(out) == REVERSAL_COUNT and _sha(out) == REVERSAL_SHA256


def test_existing_params_hashes_and_ids_are_unchanged(monkeypatch):
    monkeypatch.setattr(C, "_pa_settings_fingerprint", lambda: "pinned")
    out = template_pins()
    assert len(out) == TEMPLATES_COUNT and _sha(out) == TEMPLATES_SHA256


def test_the_new_choice_is_a_new_name_and_the_older_ones_keep_theirs():
    assert NEW in RETEST_OF_CHOICES and NEW in RETEST_REF_OF and NEW in bp.BY_NAME["retest_of"].choices
    assert BASES[NEW] == BASES["trendline"] == "B5"
    assert "trendline" not in PROJECTED and NEW in PROJECTED and "channel" in PROJECTED
    assert bp.BY_NAME["retest_of"].default == "range"


# ================================================================================= a hand-made falling-line retest
LINE_START = 260                                              # the falling line's pattern starts after the quiet lead-in
HAMMER = (96.9, 97.7, 95.2, 97.6)                             # a reversal at the projected line (it reaches the line)
WEAK = (96.9, 97.1, 95.2, 97.05)                              # a touch of the line, closing weak: no reversal


def line_retest(last=HAMMER, fail=False, after=()):
    """Daily bars: a quiet lead-in (the retest's history), lower highs along a falling resistance (tests/breakout_bk4_fixtures
    falling_line), the breakout close, two bars away from the line, a pull back that closes below the line's value AS OF THE
    BREAKOUT BAR (so the frozen `trendline` retest has failed) but above the line projected to each bar, then the retest
    bar at the projected line (the breakout is bar 295, the retest bar 303); fail=True closes one pull-back bar below the
    projected line too; `after` rows follow the retest bar."""
    lead = [100.0 + 0.3 * np.sin(i) for i in range(LINE_START)]
    legs = path(100.0, [(5, 6.0), (5, -8.0), (5, 5.0), (5, -7.0), (5, 4.0), (5, -6.0), (5, 3.0)])[1:]
    df = frame(lead + legs + [legs[-1] + 4.0], half=0.4)
    rows = [(101.0, 102.6, 100.9, 102.4), (102.4, 103.0, 101.9, 102.6), (102.6, 102.7, 101.0, 101.2), (101.2, 101.3, 99.4, 99.6),
            (99.6, 99.7, 98.4, 98.6), (98.6, 98.7, 97.0, 97.4), (97.4, 97.6, 96.8, 97.0), last, *after]
    if fail:
        rows[5] = (98.6, 98.7, 95.5, 95.7)
    idx = df.index[-1] + pd.to_timedelta(np.arange(1, len(rows) + 1), unit="D")
    tail = pd.DataFrame([(*r, 1000.0) for r in rows], columns=["open", "high", "low", "close", "volume"], index=idx)
    return pd.concat([df, tail])


BREAK, RETEST = 295, 303


def the_line(f, side="up"):
    p = bp.settings(retest_of=NEW, retest_reversal=False)
    b = Bars(f, p)
    return _lines(b, b.side(1 if side == "up" else -1), base_params(p), "resistance").select(BREAK)


def test_the_fixture_is_a_retest_of_the_projected_line_only():
    f = line_retest()
    line = the_line(f)
    assert line is not None and line.m < 0                                       # a falling resistance
    assert ev('LineBreakoutRef("level", "up", "line", 2, 0.2)', f, "1d").iloc[BREAK] == pytest.approx(line.at(BREAK))
    assert float(f["close"].iloc[BREAK + 6]) < line.at(BREAK) < float(f["close"].iloc[BREAK])   # below the frozen level
    for rev in ("TRUE", "FALSE"):
        old = ev(f'RetestEntry("up", "trendline", 10, {rev})', f, "1d").to_numpy()
        new = ev(f'RetestEntry("up", "{NEW}", 10, {rev})', f, "1d").to_numpy()
        assert not old[BREAK:].any() and np.flatnonzero(new[BREAK:]).tolist() == [RETEST - BREAK]
        level = ev(f'RetestEntryRef("level", "up", "{NEW}", 10, {rev})', f, "1d")
        assert level.iloc[RETEST] == pytest.approx(line.at(RETEST)) and level.iloc[RETEST] < line.at(BREAK)


# ================================================================================= C4 on the fixture
def test_c4_must_fire_on_the_retest_both_sides_with_the_default_invalidation():
    f = line_retest()
    s = run(f, "1d")
    assert signals(s, BREAK) == {RETEST: 1}
    line = the_line(f)
    pullback_low = float(f["low"].iloc[BREAK + 1:RETEST + 1].min())
    atr_prev = float(ev("ATR(14)", f, "1d").iloc[RETEST - 1])
    assert s.invalidation[RETEST] == pytest.approx(min(pullback_low, line.at(RETEST) - atr_prev))   # whichever is farther
    m = run(mirror(f), "1d")
    assert signals(m, BREAK) == {RETEST: -1} and K - m.invalidation[RETEST] == pytest.approx(s.invalidation[RETEST])
    assert signals(run(f, "1d", **TOUCH), BREAK) == {RETEST: 1}
    assert library.signal_text(T, C.resolve_settings(T, "1d"), "long").startswith("The close broke out above a falling trendline")


def test_c4_invalidation_choices():
    f = line_retest()
    line = the_line(f)
    level = line.at(RETEST)
    pullback_low = float(f["low"].iloc[BREAK + 1:RETEST + 1].min())
    atr_prev = float(ev("ATR(14)", f, "1d").iloc[RETEST - 1])
    base_low = float(f["low"].iloc[line.a:BREAK].min())                            # the far end of the move along the line
    want = {("retest_low", "stop_level_atr"): pullback_low, ("level_minus_stop_ref", "stop_level_atr"): level - atr_prev,
            ("level_minus_stop_ref", "stop_base"): base_low, ("lower_of_both", "stop_base"): min(pullback_low, base_low)}
    for (choice, ref), value in want.items():
        s = run(f, "1d", invalidation=choice, breakout_ref=ref)
        assert s.invalidation[RETEST] == pytest.approx(value), (choice, ref)
    s = run(f, "1d", invalidation="level_minus_stop_ref", stop_mult=2.5)
    assert s.invalidation[RETEST] == pytest.approx(level - 2.5 * atr_prev)


def test_c4_must_not_fire_on_a_failure_before_the_retest_a_weak_retest_too_late_or_a_shorter_break():
    assert signals(run(line_retest(fail=True), "1d"), BREAK) == {}                           # back through the line first
    weak = line_retest(WEAK)
    assert signals(run(weak, "1d"), BREAK) == {}                                             # no reversal at the line
    assert signals(run(weak, "1d", **TOUCH), BREAK) == {RETEST: 1}                           # a touch is enough
    assert signals(run(line_retest(), "1d", retest_max_bars=RETEST - BREAK - 1), BREAK) == {}   # expiry
    assert signals(run(line_retest(), "1d", retest_max_bars=RETEST - BREAK), BREAK) == {RETEST: 1}
    assert signals(run(line_retest(), "1d", k=3.0), BREAK) == {}                              # the breakout's own distance



def test_c4_touches_is_the_trendline_breakouts_own_setting():
    """The line touches reach the trendline breakout (another line can be chosen, so not a subset): the signals are the
    retests of the trendline breakout with those touches."""
    df = synthetic_bars(600, tf="15m", seed=14)
    plain, strict = run(df, **TOUCH), run(df, touches=4, **TOUCH)
    assert signals(plain) != signals(strict)
    want = {}
    for side, sign in (("up", 1), ("down", -1)):
        fired = ev(f'RetestEntry("{side}", "{NEW}", 10, FALSE, touches=4)', df).to_numpy()
        want.update({int(t): sign for t in np.flatnonzero(fired) if t >= strict.warmup_bars and strict.reason[t] != engine.REASON_GEOMETRY})
    assert signals(strict) == want


def test_c4_s6_a_retest_closing_on_the_line_has_no_room():
    """S6: the invalidation on the projected line itself (no buffer) and a retest bar closing exactly there: no signal."""
    level = the_line(line_retest()).at(RETEST)
    f = line_retest((96.9, 97.1, 95.2, level))
    assert f["close"].iloc[RETEST] == pytest.approx(level)
    s = run(f, "1d", invalidation="level_minus_stop_ref", stop_mult=0.0, **TOUCH)
    assert signals(s, BREAK) == {} and s.reason[RETEST] == engine.REASON_GEOMETRY


def test_c4_exit_is_a_close_back_through_the_projected_line():
    """A close below the line's value at the retest bar but above the line projected further is no exit; a close below
    the projected line is."""
    f = line_retest(after=[(97.6, 98.0, 97.0, 97.8), (97.8, 97.9, 94.9, 95.0), (95.0, 95.1, 94.0, 94.1)])
    line = the_line(f)
    s = run(f, "1d")
    assert signals(s, BREAK) == {RETEST: 1}
    assert line.at(RETEST + 2) < f["close"].iloc[RETEST + 2] < line.at(RETEST)
    assert f["close"].iloc[RETEST + 3] < line.at(RETEST + 3)
    assert [bool(x) for x in s.exit_long[RETEST:]] == [False, False, False, True]


def test_c4_warm_up_is_the_retests_history():
    df = synthetic_bars(400, tf="15m", seed=3)
    s = run(df)
    assert s.warmup_bars == 249 and set(s.reason[:249]) == {engine.REASON_WARMUP}
    assert all(t >= 249 for t in s.signal_bars())


# ================================================================================= the projected line on random data
@pytest.mark.parametrize("seed", [1, 9, 11, 14])
def test_the_projected_retest_reads_the_broken_line_at_the_retest_bar(seed):
    df = synthetic_bars(600, tf="15m", seed=seed)
    p = bp.settings(retest_of=NEW, retest_max_bars=10, retest_reversal=False)
    b = Bars(df, p)
    seen = 0
    for side, sign in (("up", 1), ("down", -1)):
        broke = ev(f'LineBreakoutRef("level", "{side}", "line", 2, 0.2)', df)
        level = ev(f'RetestEntryRef("level", "{side}", "{NEW}", 10, FALSE)', df)
        held = ev(f'RetestEntryRef("held_level", "{side}", "{NEW}", 10, FALSE)', df)
        fired = ev(f'RetestEntry("{side}", "{NEW}", 10, FALSE)', df)
        events = retest_events(b, b.side(sign), p)
        assert [e.bar for e in events] == [int(t) for t in np.flatnonzero(fired.to_numpy())]
        assert (level.notna() == fired).all()
        for e in events:
            seen += 1
            assert sign * e.level_at(e.breakout) == pytest.approx(broke.iloc[e.breakout])          # the breakout's own line
            assert level.iloc[e.bar] == pytest.approx(sign * e.level_at(e.bar))                  # projected to the retest bar
            assert e.slope is not None and e.slope < 0                                            # falling up / rising down
            assert sign * (level.iloc[e.bar] - broke.iloc[e.breakout]) < 0                        # not the frozen value
            nxt = next((x.bar for x in events if x.bar > e.bar), len(df))
            for x in range(e.bar, nxt):                                                           # held along the line
                assert held.iloc[x] == pytest.approx(sign * e.level_at(x))
    assert seen, "the fixture must carry trendline retests"


def test_the_b9_screen_follows_the_projected_line_too():
    df = synthetic_bars(600, tf="15m", seed=1)
    out = evaluate_screen(df, "B9", bp.settings(retest_of=NEW, retest_reversal=False))
    fired = {s: [int(t) for t in np.flatnonzero(out[s]["signal"].to_numpy())] for s in ("up", "down")}
    assert fired["up"] and fired["down"]
    for side in ("up", "down"):
        assert fired[side] == [int(t) for t in np.flatnonzero(ev(f'RetestEntry("{side}", "{NEW}", 10, FALSE)', df).to_numpy())]
        assert set(out[side]["event"][out[side]["signal"]]) <= {"RETEST_B5"}
    # the B9 screen's own `tl_mode` cannot turn the projected trendline retest into a channel retest
    channel = evaluate_screen(df, "B9", bp.settings(retest_of=NEW, retest_reversal=False, tl_mode="channel"))
    assert all((channel[s]["signal"] == out[s]["signal"]).all() for s in ("up", "down"))


def test_named_arguments_reach_the_projected_retest_and_are_validated():
    from app.screener.validator import validate_study
    df = synthetic_bars(600, tf="15m", seed=11)
    assert ev(f'RetestEntry("down", "{NEW}", 20, FALSE)', df).equals(ev(f'RetestEntry("down", "{NEW}", 20, FALSE, k=0.2, touches=2)', df))
    assert not ev(f'RetestEntry("up", "{NEW}", 20, FALSE)', df).equals(ev(f'RetestEntry("up", "{NEW}", 20, FALSE, touches=4)', df))
    for text in (f'RetestEntry("up", "{NEW}", 10, TRUE, touches=1)', f'RetestEntryRef("level", "up", "{NEW}", 10, TRUE, k=9)'):
        assert validate_study(parse(text), base_tf="15m", params={}).validated.problems, text
    assert not validate_study(parse(f'RetestEntryRef("held_level", "down", "{NEW}", 10, TRUE, k=0.3, touches=3)'), base_tf="15m",
                              params={}).validated.problems


@pytest.mark.parametrize("seed", [1, 9, 11, 14])
def test_c4_fires_exactly_on_the_projected_retests_with_the_users_settings(seed):
    df = synthetic_bars(600, tf="15m", seed=seed)
    for params in ({}, TOUCH, {"touches": 3, "k": 0.1, **TOUCH}):
        s = run(df, **params)
        st_ = C.resolve_settings(T, "15m", params=params).params
        tail = f'{str(st_["retest_reversal"]).upper()}, k={st_["k"]!r}, touches={st_["touches"]}'
        want = {}
        for side, sign in (("up", 1), ("down", -1)):
            fired = ev(f'RetestEntry("{side}", "{NEW}", 10, {tail})', df)
            low = ev(f'RetestEntryRef("retest_low", "{side}", "{NEW}", 10, {tail})', df)
            ref = ev(f'RetestEntryRef("stop_level_atr", "{side}", "{NEW}", 10, {tail.replace(", k=", ", 1, k=")})', df)
            for t in np.flatnonzero(fired.to_numpy()):
                if t >= s.warmup_bars and s.reason[t] != engine.REASON_GEOMETRY:
                    want[int(t)] = sign
                    inv = min(low.iloc[t], ref.iloc[t]) if sign > 0 else max(low.iloc[t], ref.iloc[t])
                    assert s.invalidation[t] == pytest.approx(inv)
        assert signals(s) == want, params
        if params == TOUCH:
            assert want, "the fixture must carry projected retests"


def test_c4_a_larger_break_distance_or_a_shorter_retest_window_never_adds_a_signal():
    df = synthetic_bars(900, tf="15m", seed=15)
    plain = signals(run(df, **TOUCH))
    assert plain
    for strict in ({"k": 1.5}, {"retest_max_bars": 3}):
        fewer = signals(run(df, **TOUCH, **strict))
        assert set(fewer.items()) <= set(plain.items()) and len(fewer) < len(plain), strict


# ================================================================================= the shared gate
CASES = [(TOUCH, 1), (TOUCH, 14), ({"invalidation": "level_minus_stop_ref", **TOUCH}, 9),
         ({"invalidation": "retest_low", "breakout_ref": "stop_base", **TOUCH}, 11), ({}, 29)]
# with the reversal on, a seed where the reversal check itself is mirror-exact: on some series it is not, for the older
# `trendline` retest too (the pre-existing TPL-F6, fixed in the price-action functions, not here)
IDS = [f"C4-{i}" for i in range(len(CASES))]


@pytest.mark.parametrize("params, seed", CASES, ids=IDS)
def test_mirror_short_equals_long_on_mirrored_prices(params, seed):
    df = synthetic_bars(600, tf="15m", seed=seed)
    s, m = run(df, **params), run(mirror(df), **params)
    assert signals(s), "the fixture must carry signals"
    assert signals(m) == {t: -d for t, d in signals(s).items()}
    for t in s.signal_bars():
        assert K - m.invalidation[t] == pytest.approx(s.invalidation[t])
    assert (m.exit_short == s.exit_long).all() and (m.exit_long == s.exit_short).all()


@pytest.mark.parametrize("params, seed", CASES, ids=IDS)
def test_truncation_no_look_ahead_over_many_t(params, seed):
    df = synthetic_bars(600, tf="15m", seed=seed)
    full = run(df, **params)
    assert full.signal_bars(), "the fixture must carry signals"
    marks = sorted(set(full.signal_bars()) | {t + 1 for t in full.signal_bars()} | {t - 1 for t in full.signal_bars()}
                   | set(range(full.warmup_bars, len(df), 41)))
    for t in [x for x in marks if x < len(df)]:
        part = run(df.iloc[:t + 1], **params)
        np.testing.assert_array_equal(part.direction, full.direction[:t + 1], err_msg=f"t={t}")
        np.testing.assert_allclose(part.invalidation, full.invalidation[:t + 1], equal_nan=True, err_msg=f"t={t}")
        np.testing.assert_array_equal(part.exit_long, full.exit_long[:t + 1], err_msg=f"t={t}")
        np.testing.assert_array_equal(part.exit_short, full.exit_short[:t + 1], err_msg=f"t={t}")
        assert [part.instance_id(i) for i in part.signal_bars()] == [full.instance_id(i) for i in full.signal_bars() if i <= t]


def test_truncation_on_the_fixture_the_retest_is_known_on_its_own_bar_only():
    f = line_retest()
    for t in range(BREAK, RETEST):
        assert signals(run(f.iloc[:t + 1], "1d"), BREAK) == {}, t
    assert signals(run(f.iloc[:RETEST + 1], "1d"), BREAK) == {RETEST: 1}


@pytest.mark.parametrize("params, seed", [(TOUCH, 2), (TOUCH, 9), ({}, 3)], ids=["C4-touch-2", "C4-touch-9", "C4-3"])
def test_scan_equals_backtest_equals_options_equals_worker(params, seed):
    strat = TemplateStrategy(T, C.resolve_settings(T, "15m", params=params))
    window = synthetic_bars(900, tf="15m", seed=seed).iloc[-500:].copy()          # a fixed TRAILING window (R6)
    window[["open", "high", "low", "close"]] *= 240.0                                # index-like prices for the option ladder
    scan = parity.scan_answers(strat.recipe, window)
    assert sum(1 for a in scan.values() if a.direction) >= 2, "the fixture must carry signals to compare"
    answers, trades = parity.backtest_answers(strat, window, "S", RISK)
    assert parity.compare(scan, answers, "backtest") == []
    assert trades and parity.compare_trades(scan, trades, lambda ts: ts) == []
    config = OptionBacktestConfig(option_strategy=OptionStrategy.BULL_PUT_SPREAD, implied_volatility=0.14, spread_width=2)
    assert parity.compare(scan, parity.options_answers(strat, window, "NIFTY 50", config, RISK), "options") == []
    signal_at = [i for i, a in enumerate(scan.values()) if a.direction]
    bars = sorted({j for i in signal_at for j in (i - 1, i, i + 1) if 0 <= j < len(window)} | set(range(0, len(window), 25)) | {len(window) - 1})
    worker = parity.worker_answers(strat, window, "S", bars)
    assert parity.compare(scan, worker, "worker") == []
    assert {ts for ts, a in scan.items() if a.direction} <= {ts for ts, a in worker.items() if a.direction}


@hsettings(max_examples=15, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(touches=st.integers(2, 4), k=st.floats(0.0, 1.0), bars=st.integers(1, 30), rev=st.booleans(),
       inval=st.sampled_from(T.param("invalidation").choices), ref=st.sampled_from(T.param("breakout_ref").choices),
       mult=st.floats(0.0, 3.0), seed=st.integers(0, 30))
def test_c4_property_random_valid_settings(touches, k, bars, rev, inval, ref, mult, seed):
    df = synthetic_bars(400, tf="15m", seed=seed)
    s = run(df, touches=touches, k=k, retest_max_bars=bars, retest_reversal=rev, invalidation=inval, breakout_ref=ref, stop_mult=mult)
    for side, sign in (("up", 1), ("down", -1)):
        fired = ev(f'RetestEntry("{side}", "{NEW}", {bars}, {str(rev).upper()}, k={k!r}, touches={touches})', df).to_numpy()
        for t, d in signals(s).items():
            if d == sign:
                assert fired[t]                                                   # no signal without its retest
                assert (s.invalidation[t] < df["close"].iloc[t]) if d > 0 else (s.invalidation[t] > df["close"].iloc[t])


# ================================================================================= the template itself
def _texts(template):
    out = []
    for part in (template.setup, template.invalidation, template.exit_signal, template.context):
        if part is None:
            continue
        variants = [v for v in part.variants.values() if v] if hasattr(part, "variants") else [part]
        out += [x for v in variants for x in (v.long, v.short)]
    return out


def test_c4_validates_and_its_text_passes_the_guard():
    assert C.validate_template(T) == []
    assert (T.family, T.version, T.difficulty) == ("C", 1, "Advanced")
    for tf in T.allowed_timeframes:
        settings = C.resolve_settings(T, tf)
        for side in ("long", "short"):
            text = library.signal_text(T, settings, side)
            assert text and language.guard(text) == [], text
            words = re.sub(r"\b\d+-(minute|hour)\b", "", text)                     # the timeframe's name is the only number
            assert not re.search(r"\d|\b(buy|sell|target|stop|entry|exit)\b", words, re.I), text
    for token in ("trendline_retest_held_from_above", "trendline_retest_held_from_below"):
        assert language.guard(language.TOKENS[token]) == []
    for p in T.params:
        assert p.default is not None
        if p.type in ("int", "float"):
            assert p.min is not None and p.max is not None and p.min <= p.default <= p.max
        if p.choices:
            assert len(p.choice_labels) == len(p.choices)


def test_no_number_is_written_into_a_rule_and_every_rule_reads_the_projected_line():
    for text in _texts(T):
        assert not re.search(r"(?<![\w$])\d", text), text
        assert f'"{NEW}"' in text and '"trendline"' not in text, text


def test_c4_uses_the_trendline_breakout_templates_own_settings_and_c3s_retest_settings():
    for key in ("touches", "k", "stop_mult"):
        a, b = T.param(key), B4.param(key)
        assert (a.type, a.default, a.min, a.max, a.choices) == (b.type, b.default, b.min, b.max, b.choices), key
    assert T.param("breakout_ref").choices == B4.param("invalidation").choices
    assert T.param("breakout_ref").choice_labels == B4.param("invalidation").choice_labels
    c3 = library.get("C3")
    for key in ("retest_max_bars", "retest_reversal", "invalidation"):
        a, b = T.param(key), c3.param(key)
        assert (a.type, a.default, a.min, a.max, a.choices) == (b.type, b.default, b.min, b.max, b.choices), key
    assert T.allowed_timeframes == B4.allowed_timeframes


def test_c4_ids_round_trip_and_fit():
    for tf in [t for t in T.allowed_timeframes if t in ENGINE_LABEL]:
        sid, settings = ids.adopt(T, tf)
        assert sid.startswith("template:C4:1:") and ids.decode(sid) == (T, settings)
    worst = {"touches": 10, "k": 4.65, "retest_max_bars": 100, "retest_reversal": False, "invalidation": "level_minus_stop_ref",
             "breakout_ref": "stop_level_atr", "stop_mult": 9.65}
    sid, settings = ids.adopt(T, "15m", "short", worst)
    assert len(sid) <= ids.MAX_LENGTH and ids.decode(sid) == (T, settings)


def test_the_adapter_reads_the_retest_signal_and_its_provenance():
    df = synthetic_bars(600, tf="15m", seed=1)
    strat = TemplateStrategy(T, C.resolve_settings(T, "15m", params=TOUCH))
    series = engine.compute(strat.recipe, {"15m": df})
    t = series.signal_bars()[0]
    sig = strat.analyze({ENGINE_LABEL["15m"]: df.iloc[:t + 1]}, "S")
    assert sig.is_tradeable and sig.stop_loss == pytest.approx(series.invalidation[t]) and sig.target1 is None
    assert sig.risk_reward is None
    assert sig.provenance["template"] == "C4" and sig.provenance["setup"] == series.instance_id(t)
    assert sig.provenance["settings"] == strat.recipe.params_hash == C.params_hash(T, strat.settings)
    assert all(language.guard(r) == [] for r in sig.reasons)
    assert strat.recipe.required_flags == {"screener_breakouts", "screener_breakouts_bk4"}
    assert strat.analyze({ENGINE_LABEL["15m"]: df.iloc[:t]}, "S").is_tradeable is False      # the bar before: nothing yet


def test_the_template_is_paper_only_signals_only():
    data = T.model_dump(by_alias=True)
    assert not {"target", "targets", "lots", "quantity", "strike", "premium", "order"} & set(data)
    for text in _texts(T):
        assert not re.search(r"\b(target|lots|strike|premium|order)\b", text, re.I)


def test_the_api_lists_c4_only_with_the_flag_and_adopts_it_as_paper():
    from tests.test_contract_rules import _load_master
    from tests.test_tpl2_routes import FLAG, _org, client
    _load_master()
    off = _org("c4-off")
    assert client.get("/api/strategy-templates", headers=off["headers"]).status_code == 404
    assert client.post("/api/strategy-templates/C4/adopt", headers=off["headers"], json={}).status_code == 404
    bare = _org("c4-bare", FLAG)
    card = {c["id"]: c for c in client.get("/api/strategy-templates", headers=bare["headers"]).json()["templates"]}["C4"]
    assert card["available"] is False                                             # the breakout flags are off
    org = _org("c4", FLAG, "screener_breakouts", "screener_breakouts_bk4")
    card = {c["id"]: c for c in client.get("/api/strategy-templates", headers=org["headers"]).json()["templates"]}["C4"]
    assert card["available"] and card["family"] == "C" and card["name"] == "Trendline breakout and retest"
    body = client.post("/api/strategy-templates/C4/adopt", headers=org["headers"], json={"timeframe": "15m"}).json()
    assert body["strategy_id"].startswith("template:C4:1:15m:") and body["deployment"]["mode"] == "PAPER"
