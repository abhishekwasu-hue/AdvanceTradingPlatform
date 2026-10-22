"""Strategy templates TPL-8: R2 for the gap family (the level companion GapBreakoutRef of the B8 gap screen) and the template
B10 gap and go ("Intraday (session-based)"), against the owner order's section 8 gate - must fire long and short / must not
(a gap that faded, a gap that filled, an open inside the level, a session whose data starts after the open, a level from a
month of sessions the open did not clear), the invalidation choices by value, the session clock (the opening window in
whole bars from each session's open on the exchange calendar), S5 / S6, the warm-up, the truncation test over many t, the
mirror test, scan = backtest = options backtest = worker on one trailing window (the TPL-1 harness), property tests on
random valid settings, the language guard, "no number written into a rule", the ids, the adapter, the flags and the API."""
import re

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings as hsettings, strategies as st

from app.backtest.options_engine import OptionBacktestConfig
from app.core.enums import OptionStrategy
from app.core.models import RiskConfig
from app.quality.lookahead import synthetic_bars
from app.screener.breakouts import params as bp
from app.screener.breakouts.quality import stop_reference
from app.screener.breakouts.screens import Bars, evaluate_screen
from app.screener.parser import parse
from app.screener.registry import FUNCTIONS
from app.screener.runtime import SymbolData, evaluate
from app.strategy_templates import compile as C
from app.strategy_templates import engine, ids, language, library, parity
from app.strategy_templates.adapter import ENGINE_LABEL, TemplateStrategy
from app.strategy_templates.schema import Template
from tests.breakout_bk4_fixtures import daily_gap
from tests.breakout_fixtures import quiet_day, sessions_frame

T = library.get("B10")
RISK = RiskConfig(capital=10_000_000, risk_per_trade_pct=0.5)
K = 400.0                                                     # mirror axis: price -> K - price


def run(df, tf="15m", **params):
    return engine.compute(C.compile_recipe(T, C.resolve_settings(T, tf, params=params)), {tf: df})


def ev(text, df, tf="15m"):
    return evaluate(parse(text), SymbolData("S", {tf: df}), base_tf=tf)


def mirror(df, axis=K):
    out = df.copy()
    out["open"], out["close"] = axis - df["open"], axis - df["close"]
    out["high"], out["low"] = axis - df["low"], axis - df["high"]
    return out


def whole(df):
    """The bars in whole ticks (x 20: the synthetic bars move in 0.05 steps), so the mirrored prices are exact."""
    out = df.copy()
    out[["open", "high", "low", "close"]] = (df[["open", "high", "low", "close"]] * 20).round()
    return out


def signals(s):
    return {t: int(s.direction[t]) for t in s.signal_bars()}


def walk(seed=5, n=1500, tf="15m"):
    return synthetic_bars(n, tf=tf, seed=seed)


# ================================================================================= R2: the gap companion
@pytest.mark.parametrize("tf, n, seed, level, hold", [("5m", 3000, 1, "prev_day", 30), ("15m", 1500, 5, "prev_day", 45),
                                                      ("15m", 1500, 0, "base", 15), ("3m", 2500, 2, "prev_day", 30)])
def test_the_companion_gives_the_gaps_own_prices_on_its_signal_bars(tf, n, seed, level, hold):
    df = walk(seed, n, tf)
    p = bp.settings(gap_level=level, gap_hold_min=hold)
    b = Bars(df, p, tf=tf)
    seen = 0
    for side in ("up", "down"):
        fired = ev(f'GapBreakout("{side}", "{level}", {hold})', df, tf).to_numpy(dtype=bool)
        out = evaluate_screen(df, "B8", p, bars=b, sides=(side,))[side]
        assert (out["signal"].to_numpy(dtype=bool) == fired).all()
        for what in ("level", "fail_level", "base_high", "base_low"):
            v = ev(f'GapBreakoutRef("{what}", "{side}", "{level}", {hold})', df, tf).to_numpy(dtype=float)
            assert np.isnan(v[~fired]).all(), what                                  # prices on the signal bars only
            np.testing.assert_allclose(v[fired], out[what].to_numpy(dtype=float)[fired], err_msg=what)
        lv = out["level"].to_numpy(dtype=float)
        np.testing.assert_allclose(lv[fired], out["fail_level"].to_numpy(dtype=float)[fired])    # the gap's fail level is its level
        held = ev(f'GapBreakoutRef("held_level", "{side}", "{level}", {hold})', df, tf).to_numpy(dtype=float)
        last = np.nan
        for t in range(len(df)):                                                    # held forward until the next gap
            last = lv[t] if fired[t] else last
            assert (np.isnan(held[t]) and np.isnan(last)) or held[t] == pytest.approx(last), t
        for mode in ("level_atr", "base", "midpoint"):
            v = ev(f'GapBreakoutRef("stop_{mode}", "{side}", "{level}", {hold}, 1.5)', df, tf).to_numpy(dtype=float)
            for t in np.flatnonzero(fired):
                want = stop_reference(lv[t], out["base_low"].iloc[t], out["base_high"].iloc[t], float(b.atr_prev[t]), side, mode, 1.5)
                assert v[t] == pytest.approx(want), (mode, t)
                seen += 1
    assert seen, "the fixture must carry gaps"


def test_the_companion_on_daily_gaps_and_its_mirror():
    go = daily_gap(101.5, 102.0)
    j = len(go) - 1
    assert ev('GapBreakoutRef("level", "up")', go, "1d").iloc[j] == pytest.approx(100.5)
    assert ev('GapBreakoutRef("base_low", "up")', go, "1d").iloc[j] == pytest.approx(99.5)         # the previous day's low
    assert ev('GapBreakoutRef("level", "up")', go, "1d").iloc[:j].isna().all()
    assert ev('GapBreakoutRef("level", "up")', daily_gap(101.5, 100.2), "1d").isna().all()        # a fill is no signal
    m = mirror(go, 300.0)
    assert ev('GapBreakoutRef("level", "down")', m, "1d").iloc[j] == pytest.approx(300.0 - 100.5)
    assert ev('GapBreakoutRef("base_high", "down")', m, "1d").iloc[j] == pytest.approx(300.0 - 99.5)


def test_the_companion_keeps_the_gaps_arguments_bounds_defaults_and_session_clock():
    r, b = FUNCTIONS["GapBreakoutRef"], FUNCTIONS["GapBreakout"]
    assert [a.name for a in r.args] == ["what", "side", *[a.name for a in b.args[1:]], "stop_mult"]
    for a, c in zip(r.args[2:-1], b.args[1:]):
        assert (a.type, a.default, a.choices, a.bounds) == (c.type, c.default, c.choices, c.bounds), a.name
    assert (r.flag, r.also_flags, r.timeframes, r.tf_multiple_args, r.min_sessions, r.min_bars) == \
        (b.flag, b.also_flags, b.timeframes, b.tf_multiple_args, b.min_sessions, b.min_bars)
    assert r.unit == "price" and "GapBreakoutRef" in engine.SPARSE_SERIES
    from app.screener import compile_screen
    assert not compile_screen('close > GapBreakoutRef("level", "up", "prev_day", 20)', base_tf="15m")[1].ok   # whole bars only
    assert not compile_screen('close > GapBreakoutRef("level", "up")', base_tf="1h")[1].ok                     # intraday sessions or Daily
    assert compile_screen('close > GapBreakoutRef("level", "up")', base_tf="5m")[1].lookback["5m"] >= 150     # two whole sessions


# ================================================================================= B10 on hand-made sessions
def _gap_days(last, before=None, starts=None):
    return sessions_frame((before or [quiet_day() for _ in range(3)]) + [last], starts=starts)


GO = quiet_day(mid=101.0)                                    # opens at 100.9 above the 100.25 highs, closes back at its open
END = 3 * 75 + 5                                             # the bar that ends 09:15-09:45 on the gap day


def test_b10_must_fire_on_a_gap_that_kept_going_both_sides():
    f = _gap_days(GO)
    s = run(f, "5m")
    assert signals(s) == {END: 1} and s.invalidation[END] == pytest.approx(100.25)                 # the level the gap cleared
    m = run(mirror(f, 300.0), "5m")
    assert signals(m) == {END: -1} and m.invalidation[END] == pytest.approx(300.0 - 100.25)
    assert s.instance_id(END) != "" and s.exit_long.sum() == 0 and s.exit_short.sum() == 0        # no exit: the order names none
    buf = run(f, "5m", invalidation="stop_level_atr", stop_mult=2.0)
    atr_prev = Bars(f, bp.settings(), tf="5m").atr_prev[END]
    assert signals(buf) == {END: 1} and buf.invalidation[END] == pytest.approx(100.25 - 2.0 * atr_prev)


def test_b10_the_opening_window_is_a_setting_on_the_session_clock():
    f = _gap_days(GO)
    assert signals(run(f, "5m", hold=60)) == {END + 6: 1}                                         # 09:15-10:15
    assert signals(run(f, "5m", hold=5)) == {3 * 75: 1}                                           # the opening bar alone
    late = _gap_days(GO, starts={3: "03:50"})                                                     # the data starts after the open
    assert signals(run(late, "5m")) == {}
    with pytest.raises(C.TemplateError) as exc:
        C.resolve_settings(T, "15m", params={"hold": 20})
    assert exc.value.problems == [T.constraints[0].message]                                       # in words, before any expression
    assert C.resolve_settings(T, "5m", params={"hold": 20}).params["hold"] == 20
    with pytest.raises(C.TemplateError):
        C.resolve_settings(T, "1d")                                                               # session-based: intraday only


@pytest.mark.parametrize("last", [
    [(101.5, 101.6, 101.0, 101.2, 3000.0)] + [(101.2 - 0.08 * i, 101.3 - 0.08 * i, 100.9 - 0.08 * i, 101.1 - 0.08 * i, 1000.0)
                                              for i in range(74)],                  # held, but faded toward the level (GAP_HOLD)
    [(100.6, 100.7, 100.1, 100.2, 3000.0)] + quiet_day(74, 100.3),                  # a close back inside the level (GAP_FILL)
    quiet_day(mid=100.2),                                                           # opened inside the level: no gap
], ids=["gap_hold", "gap_fill", "no_gap"])
def test_b10_must_not_fire_on_a_gap_that_did_not_keep_going(last):
    f = _gap_days(last)
    assert signals(run(f, "5m")) == {} and signals(run(mirror(f, 300.0), "5m")) == {}


def test_b10_the_level_from_a_month_of_sessions():
    before = [quiet_day(mid=101.5)] + [quiet_day() for _ in range(19)]               # an older, higher session in the month
    f = sessions_frame(before + [GO])
    end = 20 * 75 + 5
    assert signals(run(f, "5m")) == {end: 1}                                          # above the previous session's high
    assert signals(run(f, "5m", gap_level="base")) == {}                              # not above the month's high
    high = sessions_frame(before + [quiet_day(mid=102.5)])
    s = run(high, "5m", gap_level="base")
    assert signals(s) == {end: 1} and s.invalidation[end] == pytest.approx(101.75)
    short = sessions_frame([quiet_day() for _ in range(3)] + [GO])[: 3 * 75]           # warm-up: no gap day yet
    assert signals(run(short, "5m")) == {}


# ================================================================================= B10 on random sessions
@pytest.mark.parametrize("tf, n, seed, params", [("15m", 1500, 5, {}), ("15m", 1500, 0, {"hold": 15}), ("5m", 3000, 1, {}),
                                                 ("15m", 1500, 3, {"invalidation": "stop_level_atr", "stop_mult": 0.5})])
def test_b10_fires_exactly_on_the_gaps_that_kept_going(tf, n, seed, params):
    df = walk(seed, n, tf)
    s = run(df, tf, **params)
    settings = C.resolve_settings(T, tf, params=params).params
    lv, hold, inv, mult = settings["gap_level"], settings["hold"], settings["invalidation"], settings["stop_mult"]
    close = df["close"].to_numpy(dtype=float)
    want = {}
    lo = (ev(f'GapType("up", "{lv}", {hold})', df, tf) == "GAP_AND_GO").to_numpy()
    sh = (ev(f'GapType("down", "{lv}", {hold})', df, tf) == "GAP_AND_GO").to_numpy()
    il = ev(f'GapBreakoutRef("{inv}", "up", "{lv}", {hold}, {mult})', df, tf).to_numpy(dtype=float)
    ish = ev(f'GapBreakoutRef("{inv}", "down", "{lv}", {hold}, {mult})', df, tf).to_numpy(dtype=float)
    for t in range(s.warmup_bars, len(df)):
        assert not (lo[t] and sh[t])                                     # one gap direction per session by construction
        if lo[t] or sh[t]:
            sign, level = (1, il[t]) if lo[t] else (-1, ish[t])
            assert (level < close[t]) if sign > 0 else (level > close[t])                  # S6 holds for a gap that kept going
            want[t] = sign
            assert s.invalidation[t] == pytest.approx(level), t
    assert signals(s) == want and want
    sessions = df.index.normalize()
    assert len({sessions[t] for t in want}) == len(want)                                         # at most one per session


CASES = [({}, 5), ({"hold": 15}, 0), ({"invalidation": "stop_level_atr"}, 6), ({"gap_level": "base", "hold": 45}, 9)]


@pytest.mark.parametrize("params, seed", CASES)
def test_mirror_short_equals_long_on_mirrored_prices(params, seed):
    df = whole(walk(seed))
    axis = K * 20
    s, m = run(df, **params), run(mirror(df, axis), **params)
    assert signals(s), "the fixture must carry signals"
    assert signals(m) == {t: -d for t, d in signals(s).items()}
    for t in s.signal_bars():
        assert axis - m.invalidation[t] == pytest.approx(s.invalidation[t])


@pytest.mark.parametrize("params, seed", CASES)
def test_truncation_no_look_ahead_over_many_t(params, seed):
    df = walk(seed)
    full = run(df, **params)
    assert full.signal_bars(), "the fixture must carry signals"
    marks = sorted(set(full.signal_bars()) | {t + 1 for t in full.signal_bars()} | {t - 1 for t in full.signal_bars()}
                   | set(range(full.warmup_bars, len(df), 61)))
    for t in [x for x in marks if x < len(df)]:
        part = run(df.iloc[:t + 1], **params)
        np.testing.assert_array_equal(part.direction, full.direction[:t + 1], err_msg=f"t={t}")
        np.testing.assert_allclose(part.invalidation, full.invalidation[:t + 1], equal_nan=True, err_msg=f"t={t}")
        assert [part.instance_id(i) for i in part.signal_bars()] == [full.instance_id(i) for i in full.signal_bars() if i <= t]


@pytest.mark.parametrize("params, seed", [({}, 5), ({"invalidation": "stop_level_atr", "hold": 15}, 10)])
def test_scan_equals_backtest_equals_options_equals_worker(params, seed):
    strat = TemplateStrategy(T, C.resolve_settings(T, "15m", params=params))
    window = walk(seed, 1500).iloc[-730:].copy()                                      # a fixed TRAILING window (R6), mid-session start
    window[["open", "high", "low", "close"]] *= 240.0                                # index-like prices for the option ladder
    scan = parity.scan_answers(strat.recipe, window)
    assert sum(1 for a in scan.values() if a.direction) >= 2, "the fixture must carry signals to compare"
    answers, trades = parity.backtest_answers(strat, window, "S", RISK)
    assert parity.compare(scan, answers, "backtest") == []
    assert trades and parity.compare_trades(scan, trades, lambda ts: ts) == []
    config = OptionBacktestConfig(option_strategy=OptionStrategy.BULL_PUT_SPREAD, implied_volatility=0.14, spread_width=2)
    assert parity.compare(scan, parity.options_answers(strat, window, "NIFTY 50", config, RISK), "options") == []
    signal_at = [i for i, a in enumerate(scan.values()) if a.direction]
    at = sorted({j for i in signal_at for j in (i - 1, i, i + 1) if 0 <= j < len(window)} | set(range(0, len(window), 25)) | {len(window) - 1})
    worker = parity.worker_answers(strat, window, "S", at)
    assert parity.compare(scan, worker, "worker") == []
    assert {ts for ts, a in scan.items() if a.direction} <= {ts for ts, a in worker.items() if a.direction}


@hsettings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(level=st.sampled_from(["prev_day", "base"]), hold=st.sampled_from([15, 30, 45, 60, 90, 120]),
       inval=st.sampled_from(["fail_level", "stop_level_atr"]), mult=st.floats(0.0, 3.0), sides=st.sampled_from(["long", "short", "both"]),
       seed=st.integers(0, 30))
def test_b10_property_random_valid_settings(level, hold, inval, mult, sides, seed):
    df = walk(seed, 900)
    s = engine.compute(C.compile_recipe(T, C.resolve_settings(T, "15m", sides, {"gap_level": level, "hold": hold, "invalidation": inval,
                                                                                  "stop_mult": mult})), {"15m": df})
    lo = (ev(f'GapType("up", "{level}", {hold})', df) == "GAP_AND_GO").to_numpy()
    sh = (ev(f'GapType("down", "{level}", {hold})', df) == "GAP_AND_GO").to_numpy()
    for t, d in signals(s).items():
        assert (lo if d > 0 else sh)[t], t                                          # no signal without its trigger
        assert sides == "both" or (d > 0) == (sides == "long")
        assert (s.invalidation[t] < df["close"].iloc[t]) if d > 0 else (s.invalidation[t] > df["close"].iloc[t])   # S6


# ================================================================================= the shared gate
def test_the_template_validates_and_its_text_passes_the_guard():
    assert C.validate_template(T) == []
    assert T.style == "Intraday (session-based)" and set(T.allowed_timeframes) <= set(FUNCTIONS["GapType"].timeframes) - {"1d"}
    for tf in T.allowed_timeframes:
        settings = C.resolve_settings(T, tf)
        for side in ("long", "short"):
            text = library.signal_text(T, settings, side, None)
            assert text and language.guard(text) == [], text
    for p in T.params:
        assert language.guard(p.label) == [] or p.type in ("bool", "int", "float"), p.key
        if p.type in ("int", "float"):
            assert p.min is not None and p.max is not None and p.min <= p.default <= p.max
        if p.choices:
            assert len(p.choice_labels) == len(p.choices)
    assert T.family == "B" and T.difficulty in ("Beginner", "Intermediate", "Advanced")


def test_the_whole_bars_constraint_is_checked_by_the_schema_and_leaves_the_others_alone():
    data = T.model_dump(by_alias=True)
    for bad in ({"left": "stop_mult"}, {"left": "gap_level"}, {"right": "hold"}):
        broken = {**data, "constraints": [{**data["constraints"][0], **bad}]}
        with pytest.raises(ValueError):
            Template.model_validate(broken)
    for template in library.templates():
        assert template.id == "B10" or all(c.op != "whole_bars" for c in template.constraints)


_STRUCTURE = re.compile(r"(== -?1\b)|(\[1\])|([<>] 0\b)")


def _texts(template):
    out = []
    for part in (template.setup, template.invalidation, template.exit_signal, template.context):
        if part is None:
            continue
        variants = [v for v in part.variants.values() if v] if hasattr(part, "variants") else [part]
        out += [x for v in variants for x in (v.long, v.short)]
    return out


def test_no_number_is_written_into_a_template_rule():
    for text in _texts(T):
        assert not re.search(r"(?<![\w$])\d", _STRUCTURE.sub("", text)), text


def test_template_ids_round_trip_and_fit():
    for tf in [t for t in T.allowed_timeframes if t in ENGINE_LABEL]:
        sid, settings = ids.adopt(T, tf)
        assert ids.decode(sid) == (T, settings)
    worst = {"gap_level": "base", "hold": 240, "invalidation": "stop_level_atr", "stop_mult": 9.9}
    sid, settings = ids.adopt(T, "15m", "short", worst)
    assert len(sid) <= ids.MAX_LENGTH and ids.decode(sid) == (T, settings)


def test_the_adapter_reads_the_signal_with_its_provenance_and_the_flags():
    df = walk(5)
    strat = TemplateStrategy(T, C.resolve_settings(T, "15m"))
    series = engine.compute(strat.recipe, {"15m": df})
    t = series.signal_bars()[0]
    sig = strat.analyze({ENGINE_LABEL["15m"]: df.iloc[:t + 1]}, "S")
    assert sig.is_tradeable and sig.stop_loss == pytest.approx(series.invalidation[t]) and sig.target1 is None
    assert sig.risk_reward is None
    assert sig.provenance["template"] == "B10" and sig.provenance["setup"] == series.instance_id(t)
    assert all(language.guard(r) == [] for r in sig.reasons)
    assert strat.recipe.required_flags == {"screener_breakouts", "screener_breakouts_bk4"}
    assert strat.analyze({ENGINE_LABEL["15m"]: df.iloc[:t]}, "S").is_tradeable is False      # the bar before: nothing yet


def test_the_template_is_paper_only_signals_only():
    data = T.model_dump(by_alias=True)
    assert not {"target", "targets", "lots", "quantity", "strike", "premium", "order"} & set(data)
    for text in _texts(T):
        assert not re.search(r"\b(target|lots|strike|premium|order)\b", text, re.I)


def test_the_api_lists_b10_only_with_the_flag_and_adopts_it_as_paper():
    from tests.test_contract_rules import _load_master
    from tests.test_tpl2_routes import FLAG, _org, client
    _load_master()
    off = _org("b10-off")
    assert client.get("/api/strategy-templates", headers=off["headers"]).status_code == 404
    assert client.post("/api/strategy-templates/B10/adopt", headers=off["headers"], json={}).status_code == 404
    org = _org("b10", FLAG, "screener_breakouts", "screener_breakouts_bk4")
    cards = {c["id"]: c for c in client.get("/api/strategy-templates", headers=org["headers"]).json()["templates"]}
    card = cards["B10"]
    assert card["available"] and card["style"] == "Intraday (session-based)" and card["family"] == "B"
    assert T.constraints[0].message in card["rules"]
    body = client.post("/api/strategy-templates/B10/adopt", headers=org["headers"], json={"timeframe": "5m"}).json()
    assert body["strategy_id"].startswith("template:B10:1:5m:") and body["deployment"]["mode"] == "PAPER"
    bad = client.post("/api/strategy-templates/B10/adopt", headers=org["headers"], json={"timeframe": "15m", "params": {"hold": 20}})
    assert bad.status_code == 422
