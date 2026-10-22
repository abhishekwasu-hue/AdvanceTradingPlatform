"""Strategy templates, family F: the option seller packs F1 (directional: the Supertrend flip read as a bullish / bearish
continuation) and F2 (range: the range-edge rejections read as the range's lower / upper edge) against the owner order's
section 8 gate - the pack equals its member template part by part and series by series (a label mapping, no new rule),
must fire long and short / must not, the truncation test over many t, the mirror test, scan = backtest = options backtest =
worker on one trailing window, property tests on random valid settings, the language guard, "no number written into a
rule", the ids, the adapter's provenance and flags - and the options side: the option structure, its legs, width and lots
come only from the deployment's own settings (bull put on the bullish side, bear call on the bearish one, an iron condor on
either), never from the pack."""
import re

import numpy as np
import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings as hsettings, strategies as st

from app.backtest.options_engine import OptionBacktestConfig, run_option_backtest
from app.core.enums import OptionStrategy
from app.core.models import RiskConfig
from app.indicators.volatility import supertrend, supertrend_dir
from app.quality.lookahead import synthetic_bars
from app.screener.parser import parse
from app.screener.runtime import SymbolData, evaluate, resample
from app.strategy_templates import compile as C
from app.strategy_templates import engine, ids, language, library, packs, parity
from app.strategy_templates.adapter import ENGINE_LABEL, TemplateStrategy
from app.strategy_templates.schema import Template, leaves

F1, F2, D1, A2 = (library.get(t) for t in ("F1", "F2", "D1", "A2"))
# TPL-7: version 2 of each pack added members (F1: D3, F2: E3); these tests read the newest version, whose first `source`
# (the default) is version 1's only member, so every version 1 case below runs unchanged. The version 1 files themselves
# and their ids are tested in tests/test_tpl7_e2_packs_v2.py.
PACKS = ("F1", "F2")
RISK = RiskConfig(capital=10_000_000, risk_per_trade_pct=0.5)
K = 400.0                                                     # mirror axis: price -> K - price


def run(template, df, tf="15m", **params):
    return engine.compute(C.compile_recipe(template, C.resolve_settings(template, tf, params=params)), {tf: df})


def ev(text, df, tf="15m"):
    return evaluate(parse(text), SymbolData("S", {tf: df}), base_tf=tf)


def mirror(df, axis=K):
    out = df.copy()
    out["open"], out["close"] = axis - df["open"], axis - df["close"]
    out["high"], out["low"] = axis - df["low"], axis - df["high"]
    return out


def signals(s):
    return {t: int(s.direction[t]) for t in s.signal_bars()}


def bars(seed, n=600, tf="15m"):
    return synthetic_bars(n, tf=tf, seed=seed)


def same_series(a, b):
    np.testing.assert_array_equal(a.direction, b.direction)
    np.testing.assert_allclose(a.invalidation, b.invalidation, equal_nan=True)
    np.testing.assert_array_equal(a.exit_long, b.exit_long)
    np.testing.assert_array_equal(a.exit_short, b.exit_short)
    assert list(a.reason) == list(b.reason) and a.warmup_bars == b.warmup_bars
    assert [a.instance_id(t) for t in a.signal_bars()] == [b.instance_id(t) for t in b.signal_bars()]


# ================================================================== the pack is its member: a label mapping, no new rule
def _member_cases():
    out = []
    for pack_id, sources in packs.MEMBERS.items():
        for source, member_id in sources.items():
            for tf in library.get(member_id).allowed_timeframes:
                out.append((pack_id, source, tf, {}))
            if member_id == "D1":
                out += [("F1", source, "15m", {"use_context": True, "context_timeframe": "1h"}),
                        ("F1", source, "1h", {"period": 7, "multiplier": 2.5, "use_context": True, "context_timeframe": "1w"})]
            elif member_id == "D3":                                     # TPL-7: D3's own switches, nested under `source`
                out += [("F1", source, "15m", {"trigger_bar": "same", "exit_rule": "none"}),
                        ("F1", source, "1h", {"ma_type": "SMA", "fast": 10, "slow": 30, "slope_bars": 3, "pullback_max_bars": 8}),
                        ("F1", source, "15m", {"use_context": True, "context_timeframe": "1h"})]   # D1's filter: no part for D3
            elif member_id == "E3":
                out += [("F2", source, "15m", {"regime": "structure", "swing_degree": 0, "exit_at": "opposite"}),
                        ("F2", source, "1h", {"bb_n": 30, "bb_k": 2.5, "adx_period": 10, "adx_threshold": 25.0})]
            else:
                out += [("F2", source, "15m", {"swing_degree": 0}), ("F2", source, "1d", {"swing_degree": 3})]
    return out


@pytest.mark.parametrize("pack_id, source, tf, params", _member_cases())
def test_every_pack_variant_compiles_to_its_members_own_parts(pack_id, source, tf, params):
    pack = library.get(pack_id)
    settings = C.resolve_settings(pack, tf, params={"source": source, **params})
    member, member_settings = packs.member(pack, settings)
    assert member.id == packs.MEMBERS[pack_id][source]
    mine, theirs = C.compile_recipe(pack, settings), C.compile_recipe(member, member_settings)
    assert {k: p.text for k, p in mine.parts.items()} == {k: p.text for k, p in theirs.parts.items()}
    assert mine.lookback == theirs.lookback and mine.required_flags == theirs.required_flags
    assert (mine.context_timeframe, mine.context_on, mine.expiry_bars) == (theirs.context_timeframe, theirs.context_on, theirs.expiry_bars)
    assert mine.chosen_setup == theirs.chosen_setup and mine.has_trigger == theirs.has_trigger


def test_the_packs_settings_are_the_members_own():
    """TPL-7 (version 2): the pack's settings are its members' own, in member order (a key two members share - F2's swing
    size - is one setting with the same type, default and bounds; only its help is the first member's); the constraints
    are the members'; each member keeps its own timeframes and its own words, followed by the pack's reading."""
    for pack_id, sources in packs.MEMBERS.items():
        pack = library.get(pack_id)
        source = pack.param(packs.SOURCE)
        assert source.type == "enum" and list(source.choices) == list(sources) and len(source.choice_labels) == len(source.choices)
        assert pack.params[0].key == packs.SOURCE and source.default == source.choices[0]
        union, constraints = [], []
        for choice, member_id in sources.items():
            member = library.get(member_id)
            for p in member.params:                                             # keys, labels, defaults, bounds
                mine = pack.param(p.key)
                assert mine.model_dump(exclude={"help"}) == p.model_dump(exclude={"help"}), (pack_id, p.key)
                if p.key not in {q.key for q in union}:
                    assert mine == p
                    union.append(p)
            constraints += [c for c in member.constraints if c not in constraints]
            assert pack.sides_default == member.sides_default
            allowed = pack.timeframes_by.allowed.get(choice, pack.allowed_timeframes) if pack.timeframes_by else pack.allowed_timeframes
            assert set(allowed) == set(member.allowed_timeframes) and set(allowed) <= set(pack.allowed_timeframes)
            text = pack.chosen_text({packs.SOURCE: choice})
            for side in ("long", "short"):
                mine_tokens = [(p.token, p.when) for p in getattr(text, side)]
                theirs = [(p.token, p.when) for p in getattr(member.chosen_text(C.resolve_settings(member).params), side)]
                assert mine_tokens[:-1] == theirs and mine_tokens[-1][0].startswith("pack_")    # the member's words first
        assert list(pack.params[1:]) == union and list(pack.constraints) == constraints
        assert set(pack.allowed_timeframes) == {tf for m in sources.values() for tf in library.get(m).allowed_timeframes}


@pytest.mark.parametrize("pack_id, params, seed", [("F1", {}, 1), ("F1", {"use_context": True, "context_timeframe": "1h"}, 3),
                                                   ("F1", {"period": 5, "multiplier": 1.5}, 7), ("F2", {"swing_degree": 0}, 1),
                                                   ("F2", {}, 2), ("F2", {"swing_degree": 0}, 12),
                                                   # TPL-7: the version 2 members, with their own switches
                                                   ("F1", {"source": "ma_pullback"}, 1),
                                                   ("F1", {"source": "ma_pullback", "trigger_bar": "same", "ma_type": "SMA"}, 4),
                                                   ("F2", {"source": "band_reversion"}, 1),
                                                   ("F2", {"source": "band_reversion", "regime": "structure", "swing_degree": 0,
                                                           "exit_at": "opposite"}, 2)])
def test_the_pack_signals_exactly_where_its_member_does(pack_id, params, seed):
    df = bars(seed, 800)
    pack = library.get(pack_id)
    settings = C.resolve_settings(pack, "15m", params=params)
    member, member_settings = packs.member(pack, settings)
    mine = engine.compute(C.compile_recipe(pack, settings), {"15m": df})
    same_series(mine, engine.compute(C.compile_recipe(member, member_settings), {"15m": df}))
    assert mine.signal_bars(), "the fixture must carry signals"


# ================================================================================================= F1 must fire / must not
def _flips(df, period=10, mult=3.0):
    d = supertrend_dir(df, period, mult).to_numpy()
    prev = np.r_[np.nan, d[:-1]]
    return {int(t): int(d[t]) for t in np.flatnonzero((d != prev) & ~np.isnan(d) & ~np.isnan(prev))}


def leg_bars(path, start="2026-03-02 03:45", freq="15min"):
    c = np.asarray(path, dtype=float)
    idx = pd.date_range(start, periods=len(c), freq=freq, tz="UTC")
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + 0.5, "low": np.minimum(o, c) - 0.5, "close": c, "volume": 100.0}, index=idx)


def test_f1_must_fire_on_the_turn_both_sides_with_the_line_and_the_opposite_flip_as_exit():
    df = leg_bars(list(np.linspace(130, 100, 40)) + list(np.linspace(100, 135, 40)) + list(np.linspace(135, 105, 40)))
    s = run(F1, df)
    sig = signals(s)
    assert 1 in sig.values() and -1 in sig.values() and sig == _flips(df)
    line = supertrend(df, 10, 3.0)["supertrend"]
    for t, d in sig.items():
        assert s.invalidation[t] == pytest.approx(line.iloc[t])
        assert bool(s.exit_short[t]) if d > 0 else bool(s.exit_long[t])


def test_f1_must_not_fire_in_a_steady_trend_or_on_noise():
    assert run(F1, leg_bars(list(np.linspace(100, 160, 120)))).signal_bars() == []
    calm = leg_bars([100 + (0.3 if i % 2 else -0.3) for i in range(120)])
    assert run(F1, calm).signal_bars() == []


@pytest.mark.parametrize("seed", [3, 6])
def test_f1_higher_timeframe_filter_keeps_only_agreeing_turns(seed):
    df = bars(seed, 800)
    plain, kept = run(F1, df), run(F1, df, use_context=True, context_timeframe="1h")
    ctx = ev("SupertrendDir(10, 3)@1h", df)
    assert set(kept.signal_bars()) < set(plain.signal_bars()) and kept.signal_bars()
    for t in plain.signal_bars():
        assert (t in kept.signal_bars()) == bool(ctx.iloc[t] == plain.direction[t]), t


def test_f1_warm_up_and_settings_refused_in_plain_words():
    df = bars(2, 300)
    s = run(F1, df, period=20)
    assert set(s.reason[:21]) == {engine.REASON_WARMUP} and s.reason[21] != engine.REASON_WARMUP
    with pytest.raises(C.TemplateError, match="higher than the signal"):
        C.resolve_settings(F1, "1h", params={"use_context": True, "context_timeframe": "1h"})
    with pytest.raises(C.TemplateError, match="Signals from must be one of"):
        C.resolve_settings(F1, "15m", params={"source": "area_pullback"})                # A1 is not a member yet (TPL-Q50)
    with pytest.raises(C.TemplateError, match="Moving-average pullback in trend runs on the .* timeframes only"):
        C.resolve_settings(F1, "1w", params={"source": "ma_pullback"})                   # TPL-7: D3 has no Weekly chart
    assert C.resolve_settings(F1, "1w").timeframe == "1w"                               # the Supertrend flip still has
    with pytest.raises(C.TemplateError, match="between 1 and 100"):
        C.resolve_settings(F1, "15m", params={"period": 0})


# ================================================================================================= F2 must fire / must not
@pytest.mark.parametrize("degree, seed", [(0, 1), (0, 4), (1, 1), (1, 12)])
def test_f2_fires_on_a_rejection_at_a_range_edge_only_in_a_range(degree, seed):
    df = bars(seed)
    s = run(F2, df, swing_degree=degree)
    rng = (ev(f"DowTrend({degree})", df) == "RANGE").to_numpy()
    want = {}
    for direction, edge, sign in (("bullish", "SwingLow", 1), ("bearish", "SwingHigh", -1)):
        fired = ev(f'ReversalAt({edge}({degree}), "{direction}")', df).to_numpy()
        level = ev(f"{edge}({degree})", df)
        for t in np.flatnonzero(fired):
            if t < s.warmup_bars or not rng[t]:
                assert int(t) not in signals(s)
                continue
            if s.reason[t] not in (engine.REASON_GEOMETRY, engine.REASON_CONFLICT):
                want[int(t)] = sign
                assert s.invalidation[t] == pytest.approx(level.iloc[t])           # the edge itself
    assert want and signals(s) == want


@pytest.mark.parametrize("seed", [1, 3])
def test_f2_must_not_fire_on_a_rejection_outside_a_range_and_has_no_exit(seed):
    df = bars(seed)
    s = run(F2, df, swing_degree=0)
    rng = (ev("DowTrend(0)", df) == "RANGE").to_numpy()
    rejections = ev('ReversalAt(SwingLow(0), "bullish")', df).to_numpy() | ev('ReversalAt(SwingHigh(0), "bearish")', df).to_numpy()
    outside = [int(t) for t in np.flatnonzero(rejections & ~rng) if t >= s.warmup_bars]
    assert outside and not set(outside) & set(s.signal_bars())
    assert not s.exit_long.any() and not s.exit_short.any()
    assert run(F2, df).warmup_bars >= 249 and s.warmup_bars >= 99                    # the swings' history


def test_f2_s6_no_signal_with_the_edge_on_the_wrong_side():
    for seed in range(1, 16):
        df = bars(seed)
        s = run(F2, df, swing_degree=0)
        for t in np.flatnonzero(s.reason == engine.REASON_GEOMETRY):
            assert s.direction[t] == 0
        for t, d in signals(s).items():
            assert (s.invalidation[t] < df["close"].iloc[t]) if d > 0 else (s.invalidation[t] > df["close"].iloc[t])


# ====================================================================================== shared gate: both packs
CASES = [("F1", {}, 1), ("F1", {"use_context": True, "context_timeframe": "1h"}, 3), ("F1", {"period": 5, "multiplier": 1.5}, 2),
         ("F2", {"swing_degree": 0}, 1), ("F2", {}, 2), ("F2", {"swing_degree": 0}, 10),
         # TPL-7: the version 2 members (the Bollinger one with its ADX regime: the swing-structure regime breaks ties one
         # way, so E3's own mirror test leaves it out too)
         ("F1", {"source": "ma_pullback"}, 1), ("F2", {"source": "band_reversion"}, 1)]
# F2's seeds: fixtures where the member A2 is mirror-exact. On some random series the member's DowTrend / ReversalAt are
# not (a tie broken one way on both sides; pre-existing, OPEN_QUESTIONS TPL-F6) - the pack is the member there too.
IDS = [f"{tid}-{i}" for i, (tid, _, _) in enumerate(CASES)]


@pytest.mark.parametrize("tid, params, seed", CASES, ids=IDS)
def test_mirror_short_equals_long_on_mirrored_prices(tid, params, seed):
    """F2 on mirrored prices bar for bar. F1 from the first bar the two Supertrend directions mirror each other (its first
    direction is seeded up on both series, app/indicators/volatility.supertrend - as the D1 mirror test)."""
    df = bars(seed, 800)
    df[["open", "high", "low", "close"]] = (df[["open", "high", "low", "close"]] * 20).round()   # whole ticks: exact mirror
    template, axis = library.get(tid), K * 20
    s, m = run(template, df, **params), run(template, mirror(df, axis), **params)
    start = 0
    if tid == "F1" and params.get("source", "supertrend_flip") == "supertrend_flip":
        p = {"period": 10, "multiplier": 3.0, **params}
        a, b = supertrend_dir(df, p["period"], p["multiplier"]), supertrend_dir(mirror(df, axis), p["period"], p["multiplier"])
        start = int(np.flatnonzero((a == -b).to_numpy())[0]) + 1
        assert start < 150 and ((a == -b).iloc[start - 1:]).all()
        if params.get("use_context"):                                                # the hourly direction in sync too
            hourly, hourly_m = resample(df, "15m", "1h"), resample(mirror(df, axis), "15m", "1h")
            ah, bh = supertrend_dir(hourly, p["period"], p["multiplier"]), supertrend_dir(hourly_m, p["period"], p["multiplier"])
            h0 = int(np.flatnonzero((ah == -bh).to_numpy())[0])
            assert ((ah == -bh).iloc[h0:]).all()
            start = max(start, int(np.searchsorted(df.index, hourly.index[h0] + pd.Timedelta(hours=1))))
    later = {t: d for t, d in signals(s).items() if t >= start}
    assert later, "the fixture must carry signals"
    assert {t: d for t, d in signals(m).items() if t >= start} == {t: -d for t, d in later.items()}
    for t in later:
        assert axis - m.invalidation[t] == pytest.approx(s.invalidation[t])
    assert (m.exit_short[start:] == s.exit_long[start:]).all() and (m.exit_long[start:] == s.exit_short[start:]).all()


@pytest.mark.parametrize("tid, params, seed", CASES, ids=IDS)
def test_truncation_no_look_ahead_over_many_t(tid, params, seed):
    df = bars(seed, 700)
    template = library.get(tid)
    full = run(template, df, **params)
    marks = sorted(set(full.signal_bars()) | {t + 1 for t in full.signal_bars()} | set(range(full.warmup_bars, len(df), 47)))
    for t in [x for x in marks if x < len(df)]:
        part = run(template, df.iloc[:t + 1], **params)
        np.testing.assert_array_equal(part.direction, full.direction[:t + 1], err_msg=f"t={t}")
        np.testing.assert_allclose(part.invalidation, full.invalidation[:t + 1], equal_nan=True, err_msg=f"t={t}")
        np.testing.assert_array_equal(part.exit_long, full.exit_long[:t + 1], err_msg=f"t={t}")
        np.testing.assert_array_equal(part.exit_short, full.exit_short[:t + 1], err_msg=f"t={t}")
        assert [part.instance_id(i) for i in part.signal_bars()] == [full.instance_id(i) for i in full.signal_bars() if i <= t]


def _window(seed):
    window = synthetic_bars(900, tf="15m", seed=seed).iloc[-500:].copy()          # a fixed TRAILING window (R6)
    window[["open", "high", "low", "close"]] *= 240.0                                # index-like prices for the option ladder
    return window


# The option structures the user's deployment may choose: the pack's signals are the same for every one of them
STRUCTURES = [OptionBacktestConfig(option_strategy=OptionStrategy.BULL_PUT_SPREAD, implied_volatility=0.14, spread_width=2),
              OptionBacktestConfig(option_strategy=OptionStrategy.BEAR_CALL_SPREAD, implied_volatility=0.14, spread_width=3),
              OptionBacktestConfig(option_strategy=OptionStrategy.IRON_CONDOR, implied_volatility=0.14, spread_width=2, strike_offset=2)]
PARITY = [("F1", {}, 1), ("F1", {"use_context": True, "context_timeframe": "1h"}, 3), ("F2", {"swing_degree": 0}, 1), ("F2", {}, 3),
          ("F1", {"source": "ma_pullback"}, 1), ("F2", {"source": "band_reversion"}, 1)]                 # TPL-7


@pytest.mark.parametrize("tid, params, seed", PARITY, ids=[f"{p[0]}-{i}" for i, p in enumerate(PARITY)])
def test_scan_equals_backtest_equals_options_equals_worker(tid, params, seed):
    template = library.get(tid)
    strat = TemplateStrategy(template, C.resolve_settings(template, "15m", params=params))
    window = _window(seed)
    scan = parity.scan_answers(strat.recipe, window)
    assert sum(1 for a in scan.values() if a.direction) >= 2, "the fixture must carry signals to compare"
    answers, trades = parity.backtest_answers(strat, window, "S", RISK)
    assert parity.compare(scan, answers, "backtest") == []
    assert trades and parity.compare_trades(scan, trades, lambda ts: ts) == []
    for config in STRUCTURES:
        assert parity.compare(scan, parity.options_answers(strat, window, "NIFTY 50", config, RISK), f"options {config.option_strategy}") == []
    signal_at = [i for i, a in enumerate(scan.values()) if a.direction]
    at = sorted({j for i in signal_at for j in (i - 1, i, i + 1) if 0 <= j < len(window)} | set(range(0, len(window), 25)) | {len(window) - 1})
    worker = parity.worker_answers(strat, window, "S", at)
    assert parity.compare(scan, worker, "worker") == []
    # the pack's own run equals its member's on the same window, on every path (the scan series is the member's)
    member, member_settings = packs.member(template, strat.settings)
    theirs = parity.scan_answers(C.compile_recipe(member, member_settings), window)
    assert {ts: (a.direction, a.invalidation, a.instance) for ts, a in scan.items()} == \
        {ts: (a.direction, a.invalidation, a.instance) for ts, a in theirs.items()}


# ========================================================== options: the structure, legs, width and lots are the user's
def _structures(tid, config, seed=1, params=None):
    template = library.get(tid)
    strat = TemplateStrategy(template, C.resolve_settings(template, "15m", params=params or {}))
    window = _window(seed)
    scan = parity.scan_answers(strat.recipe, window)
    result = run_option_backtest(strat, window, "NIFTY 50", strat.label, RISK, config)
    return scan, result.options["structures"]


def _side_at(scan, entry_time):
    return scan[pd.Timestamp(entry_time)].direction


@pytest.mark.parametrize("tid, params, seed", [("F1", {}, 1), ("F2", {"swing_degree": 0}, 1)])
def test_the_users_structure_decides_the_legs_bull_put_on_bullish_bear_call_on_bearish(tid, params, seed):
    bull_scan, bull = _structures(tid, STRUCTURES[0], seed, params)
    assert bull, "the fixture must open structures"
    for s in bull:                                  # a bull put spread: one short and one long put, opened on a bullish signal only
        assert _side_at(bull_scan, s["entry_time"]) == 1
        assert sorted((leg["right"], leg["role"]) for leg in s["legs"]) == [("PE", "LONG"), ("PE", "SHORT")]
        short, long_ = (next(leg["strike"] for leg in s["legs"] if leg["role"] == r) for r in ("SHORT", "LONG"))
        assert long_ < short
    bear_scan, bear = _structures(tid, STRUCTURES[1], seed, params)
    assert bear
    for s in bear:                                  # a bear call spread, opened on a bearish signal only
        assert _side_at(bear_scan, s["entry_time"]) == -1
        assert sorted((leg["right"], leg["role"]) for leg in s["legs"]) == [("CE", "LONG"), ("CE", "SHORT")]
    condor_scan, condor = _structures(tid, STRUCTURES[2], seed, params)
    assert {_side_at(condor_scan, s["entry_time"]) for s in condor} == {1, -1}       # either side
    assert all(len(s["legs"]) == 4 for s in condor)


def test_the_width_and_the_lots_come_from_the_deployment_not_the_pack():
    def gaps(config):
        _, out = _structures("F1", config)
        return {abs(s["legs"][0]["strike"] - s["legs"][1]["strike"]) for s in out}, {s["lots"] for s in out}
    narrow, _ = gaps(OptionBacktestConfig(option_strategy=OptionStrategy.BULL_PUT_SPREAD, implied_volatility=0.14, spread_width=2))
    wide, _ = gaps(OptionBacktestConfig(option_strategy=OptionStrategy.BULL_PUT_SPREAD, implied_volatility=0.14, spread_width=4))
    assert len(narrow) == 1 and len(wide) == 1 and next(iter(wide)) == 2 * next(iter(narrow))
    _, lots = gaps(OptionBacktestConfig(option_strategy=OptionStrategy.BULL_PUT_SPREAD, implied_volatility=0.14, spread_width=2, max_lots=1))
    assert lots == {1}


def test_no_pack_field_or_rule_names_an_option_choice():
    """A pack only gives the side: the strict schema has no field for a structure, a strike, a premium, an expiry, a lot or
    an order, and no rule or setting names one."""
    words = r"\b(strike|strikes|premium|expiry|lots?|spread|put|call|condor|order|quantity|target)\b"
    for tid in PACKS:
        data = library.get(tid).model_dump(by_alias=True)
        assert set(data) == set(Template.model_fields) - {"schema_"} | {"schema"}
        for p in data["params"]:
            assert not re.search(words, p["key"] + " " + p["label"], re.I), p["key"]
        for text in _texts(library.get(tid)):
            assert not re.search(words, text, re.I)


# ======================================================================================== properties, words, ids, adapter
@hsettings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(period=st.integers(1, 40), mult=st.floats(0.5, 6.0), use_ctx=st.booleans(), seed=st.integers(0, 30))
def test_f1_property_random_valid_settings(period, mult, use_ctx, seed):
    df = bars(seed, 400)
    p = {"period": period, "multiplier": mult, "use_context": use_ctx, "context_timeframe": "1h"}
    s = run(F1, df, **p)
    same_series(s, run(D1, df, **p))
    flips = _flips(df, period, mult)
    for t, d in signals(s).items():
        assert flips.get(t) == d                                                       # no signal without its trigger
        assert (s.invalidation[t] < df["close"].iloc[t]) if d > 0 else (s.invalidation[t] > df["close"].iloc[t])   # S6


@hsettings(max_examples=10, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(degree=st.integers(0, 1), seed=st.integers(0, 30))
def test_f2_property_random_valid_settings(degree, seed):
    df = bars(seed, 400)
    s = run(F2, df, swing_degree=degree)
    same_series(s, run(A2, df, swing_degree=degree))
    lo = ev(f'ReversalAt(SwingLow({degree}), "bullish")', df).to_numpy()
    hi = ev(f'ReversalAt(SwingHigh({degree}), "bearish")', df).to_numpy()
    for t, d in signals(s).items():
        assert (lo if d > 0 else hi)[t]
        assert (s.invalidation[t] < df["close"].iloc[t]) if d > 0 else (s.invalidation[t] > df["close"].iloc[t])


@pytest.mark.parametrize("tid", PACKS)
def test_library_template_validates_and_its_text_passes_the_guard(tid):
    template = library.get(tid)
    assert C.validate_template(template) == [] and template.family == "F"
    for tf in template.allowed_timeframes:
        settings = C.resolve_settings(template, tf)
        for side in ("long", "short"):
            for ctx in (None, "1d", "1w"):
                text = library.signal_text(template, settings, side, ctx)
                assert text and language.guard(text) == [], text
    settings = C.resolve_settings(template, "15m")
    words = {"F1": ("bullish continuation", "bearish continuation"), "F2": ("lower edge of the range", "upper edge of the range")}[tid]
    assert words[0] in library.signal_text(template, settings, "long") and words[1] in library.signal_text(template, settings, "short")
    for p in template.params:
        assert language.guard(p.label) == [] or p.type in ("bool", "int", "float"), p.key


def _texts(template):
    out = []
    for part in (template.setup, template.trigger, template.invalidation, template.exit_signal, template.context):
        if part is None:
            continue
        variants = [v for v in leaves(part) if v is not None and hasattr(v, "long")]     # TPL-7: nested switches opened
        out += [x for v in variants for x in (v.long, v.short)]
    return out


@pytest.mark.parametrize("tid", PACKS)
def test_no_number_is_written_into_a_template_rule(tid):
    structure = re.compile(r"(== -?1\b)|(\[1\])")                                     # a direction value and the bar before
    for text in _texts(library.get(tid)):
        assert not re.search(r"(?<![\w$])\d", structure.sub("", text)), (tid, text)


@pytest.mark.parametrize("tid", PACKS)
def test_template_ids_round_trip_and_fit(tid):
    template = library.get(tid)
    for tf in [t for t in template.allowed_timeframes if t in ENGINE_LABEL]:
        sid, settings = ids.adopt(template, tf)
        assert ids.decode(sid) == (template, settings) and sid.startswith(f"template:{tid}:{template.version}:")
    worst = {"F1": {"period": 100, "multiplier": 9.876543, "use_context": True, "context_timeframe": "1w"}, "F2": {"swing_degree": 3}}[tid]
    sid, settings = ids.adopt(template, template.allowed_timeframes[0], "short", worst)
    assert len(sid) <= ids.MAX_LENGTH and ids.decode(sid) == (template, settings)


@pytest.mark.parametrize("tid, params, flags", [("F1", {}, set()), ("F2", {"swing_degree": 0}, {"screener_breakouts"})])
def test_the_adapter_reads_the_signal_with_its_provenance_and_the_flags(tid, params, flags):
    template = library.get(tid)
    df = bars(1)
    strat = TemplateStrategy(template, C.resolve_settings(template, "15m", params=params))
    series = engine.compute(strat.recipe, {"15m": df})
    t = series.signal_bars()[0]
    sig = strat.analyze({"15min": df.iloc[:t + 1]}, "S")
    assert sig.is_tradeable and sig.stop_loss == pytest.approx(series.invalidation[t]) and sig.target1 is None and sig.risk_reward is None
    assert sig.provenance["template"] == tid and sig.provenance["setup"] == series.instance_id(t)
    assert sig.strategy_id.startswith(f"template:{tid}:") and all(language.guard(r) == [] for r in sig.reasons)
    member, member_settings = packs.member(template, strat.settings)
    assert strat.recipe.required_flags == C.compile_recipe(member, member_settings).required_flags == flags


# ============================================================================== the API: cards, adopt, an option bot on paper
def test_the_packs_are_listed_adopted_as_a_pre_fill_and_run_on_the_users_option_settings_on_paper():
    from tests.test_contract_rules import _load_master
    from tests.test_deployments_api import _store_broker
    from tests.test_tpl2_routes import FLAG, _org, client
    _load_master()
    org = _org("packs", FLAG)
    cards = {c["id"]: c for c in client.get("/api/strategy-templates", headers=org["headers"]).json()["templates"]}
    assert cards["F1"]["available"] and not cards["F2"]["available"]                 # F2 reads DowTrend, as A2 (its flag is off here)
    assert cards["A2"]["available"] == cards["F2"]["available"]
    for tid in PACKS:
        assert cards[tid]["family_name"] == "Option seller packs"
        assert cards[tid]["params"][0]["key"] == "source" and len(cards[tid]["params"][0]["choices"]) == 2     # TPL-7: version 2
    body = client.post("/api/strategy-templates/F1/adopt", headers=org["headers"], json={"timeframe": "15m"}).json()
    assert body["strategy_id"] == "template:F1:2:15m:both:,,,,,,,,,,,"
    assert body["deployment"] == {"strategy_id": body["strategy_id"], "timeframe": "15min", "holding": "INTRADAY", "mode": "PAPER"}
    assert packs.STRUCTURE_NOTE in body["notes"]                                      # no option field in the pre-fill
    d1 = client.post("/api/strategy-templates/D1/adopt", headers=org["headers"], json={"timeframe": "15m"}).json()
    assert packs.STRUCTURE_NOTE not in d1["notes"]
    _store_broker(org["headers"])
    made = client.post("/api/deployments", headers=org["headers"],
                       json={**body["deployment"], "symbol": "NIFTY 50", "instrument_kind": "OPTION", "option_strategy": "BULL_PUT_SPREAD",
                             "spread_width": 3, "max_lots": 1})
    assert made.status_code == 201, made.text
    dep = made.json()
    assert dep["strategy_id"] == body["strategy_id"] and dep["mode"] == "PAPER"
    assert dep["option_strategy"] == "BULL_PUT_SPREAD" and dep["spread_width"] == 3                # the user's own construction
    live = client.post("/api/deployments", headers=org["headers"], json={**body["deployment"], "symbol": "BANKNIFTY", "mode": "LIVE"})
    assert live.status_code in (400, 403, 503)
