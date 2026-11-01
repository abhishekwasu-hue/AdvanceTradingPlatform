"""Strategy templates section 5: EXITS AND TARGETS ARE THE USER'S SETTINGS (owner order; R4, R5; WAVE section 4), behind
the existing flag `strategy_templates` (OFF), PAPER only. Owner decision 2026-10-23 (DECISIONS.md): approved, one additive
migration (d7f9b1c3e5a8).

The gates (section 8 style):
* the default (nothing chosen, every existing deployment) is byte for byte the behaviour before: backtest results of
  seven templates on the underlying, a single option and a spread pinned against the base branch (b5d7f9a1c3e6, #423);
  an explicit "the template's level, no target" is the same run;
* the precedence table per instrument (R4) and every refusal in plain words; DSL v1 / built-in strategies refuse the
  choice (they keep their own stop / target);
* R multiple: must fire on exactly the default's signal bars (a choice never changes the signals), the level by value
  long and short, the account's minimum R:R applies (must not below it), scan = backtest = worker;
* the template's exit signal (R5): both backtest engines close at the next bar's open after the exit bar, reason
  "Template exit", never on the entry bar, must not without the choice; the worker closes the deployment's own open PAPER
  trades on the same bar (tenancy: never another organisation's, never LIVE, never a trade entered after the bar);
* the wave projection (family G): the level is WaveProjection's own value on the signal bar, scan = backtest = worker; no
  projection there = no target, still a signal (never a refusal);
* a single option's premium stop only: no underlying-level stop on the trade (backtest and the live order plan), its
  underlying target still exits (`underlying_exit` tolerates a missing stop);
* the signal text never mentions any of it (policy 11l);
* the routes (deployment, backtest, adopt) and the migration (additive, rollback-safe, reversible, the one head).
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import uuid
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.backtest.engine import run_backtest
from app.backtest.options_engine import OptionBacktestConfig, run_option_backtest
from app.core.enums import OptionStrategy, SignalDirection, StrikeRule
from app.core.models import RiskConfig
from app.quality.lookahead import synthetic_bars
from app.strategy_templates import compile as C
from app.strategy_templates import exits as X
from app.strategy_templates import library
from app.strategy_templates.adapter import TemplateStrategy
from tests.def0_legacy import legacy

# REV-S1: these fixtures run around the clock or hold a trade overnight to reach the exit under test; on the 24x7 venue the
# backtest has no INTRADAY cut-off / square-off (the NSE session rules are tested in tests/test_rev1_s1_s2_backtest_intraday.py).
VENUE_24X7 = "BTCINR"

RISK = RiskConfig(capital=10_000_000, risk_per_trade_pct=0.5, min_risk_reward=0.5)
D1 = library.get("D1")


def _strategy(tid="D1", tf="15m", choice=None, **params):
    t = library.get(tid)
    s = TemplateStrategy(t, C.resolve_settings(t, tf, params=params))
    return s.use_exits(choice) if choice is not None else s


def _bars(seed, total=900, scale=1.0):
    df = synthetic_bars(total, tf="15m", seed=seed)
    df[["open", "high", "low", "close"]] *= scale
    return df


def _hash(result) -> str:
    data = result.model_dump(mode="json", exclude={"reproducibility", "run_id"})
    if data.get("history") is None:
        data.pop("history", None)                             # REV-S3 added the field (None on these engine-level runs)
    if data.get("data_source") is None:
        data.pop("data_source", None)                         # M55 T3 added the field (set by the routes only)
    if isinstance(data.get("options"), dict) and data["options"].get("engine_version") in ("9-options", "10-options"):
        data["options"]["engine_version"] = "8-options"      # P1-2b / M68 PR-3 bumped the label; the pins were recorded under 8
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:16]


# DEF-0: the old built-in exit levels (bought premium floor 30%, credit target 50% / stop 100%, the short-strike breach exit)
# set explicitly, so the runs (and the pins below) are the same as before.
SINGLE = OptionBacktestConfig(implied_volatility=0.14, premium_stop_pct=30.0)
SPREAD = OptionBacktestConfig(option_strategy=OptionStrategy.BULL_PUT_SPREAD, implied_volatility=0.14, spread_width=2,
                              target_credit_pct=50, stop_credit_pct=100, underlying_breach_exit=True)


def _option_config(base: OptionBacktestConfig, **changes) -> OptionBacktestConfig:
    from dataclasses import replace
    return replace(base, **changes)


# ============================================================================================ default = before, byte for byte
# Computed on the base branch (origin/claude/cp6-15-loss-streak, before this change) with the same code as `_run_pins`:
# the BacktestResult of each default template run, hashed (the run's fingerprint excluded).
BASE_PINS = {
    "B1:5:single": "357b6d34134653af", "B1:5:spread": "8821754b9ca89451", "B1:5:underlying": "f37e71fa6091642c",
    "B3:8:single": "16f14eb3787306b9", "B3:8:spread": "50f4c523717c48ec", "B3:8:underlying": "2898863d5f5c86f6",
    "C1:6:single": "73a266bd4c7e54fe", "C1:6:spread": "9c5e80cb0d004c27", "C1:6:underlying": "9f39bd369f8038b4",
    "D1:3:single": "879841e3f9236913", "D1:3:spread": "966852aa6cf20fe5", "D1:3:underlying": "c3bfb9a9497f1e87",
    "D2:4:single": "0386963396500afe", "D2:4:spread": "ee043b4ffdcbf1b6", "D2:4:underlying": "6b0aed77ef2ff901",
    "D5:9:single": "ff17ea9d05ee5c5b", "D5:9:spread": "c354f54ab0c73e14", "D5:9:underlying": "2a25ffe3c1d4d26d",
    "E3:7:single": "4de168a943f8d179", "E3:7:spread": "afa8859718b00294", "E3:7:underlying": "9633ed416e99a8e9",
}
# P1-2b: the pins were recorded with the then built-in minimum reward:risk 1.2; it is stated here so they stay comparable.
# DEF-0: the pins were recorded under the old platform risk limits too - set explicitly (tests/def0_legacy.py).
PIN_RISK = legacy(capital=10_000_000, risk_per_trade_pct=0.5, min_risk_reward=1.2)


def _run_pins(choice=None):
    out = {}
    for key in sorted({k.rsplit(":", 1)[0] for k in BASE_PINS}):
        tid, seed = key.split(":")
        t = library.get(tid)
        df = synthetic_bars(900, tf="15m", seed=int(seed))

        def strat():
            s = TemplateStrategy(t, C.resolve_settings(t, "15m"))
            return s.use_exits(choice) if choice is not None else s
        # REV-S1: the pins were recorded before the backtest took the live INTRADAY cut-off / square-off - re-run that way
        out[f"{key}:underlying"] = _hash(run_backtest(strat(), df, "S", "15min", PIN_RISK, session_rules=False))
        big = df.copy()
        big[["open", "high", "low", "close"]] *= 240.0
        out[f"{key}:single"] = _hash(run_option_backtest(strat(), big, "NIFTY 50", "15min", PIN_RISK, SINGLE))
        out[f"{key}:spread"] = _hash(run_option_backtest(strat(), big, "NIFTY 50", "15min", PIN_RISK, SPREAD))
    return out


def test_the_default_is_byte_identical_to_the_base_branch():
    assert _run_pins() == BASE_PINS


def test_an_explicit_template_level_and_no_target_is_the_same_run():
    """TEMPLATE_INVALIDATION + NONE on the underlying and a single option = what NULL means there (today's behaviour)."""
    pins = _run_pins(X.TemplateExits(stop_source="TEMPLATE_INVALIDATION", target_rule="NONE"))
    assert {k: v for k, v in pins.items() if not k.endswith("spread")} == {k: v for k, v in BASE_PINS.items() if not k.endswith("spread")}


def test_every_library_template_answers_exactly_as_before_with_no_choice():
    """No choice: the same series and the same Signal (the invalidation as its stop, no target, risk_reward None) for every
    template the library has, and a stored row with every exit column NULL reads as the default."""
    df = _bars(11, total=700)
    for t in library.templates():
        tf = next((x for x in t.allowed_timeframes if x == "15m"), None)
        if tf is None:
            continue
        a = TemplateStrategy(t, C.resolve_settings(t, tf))
        b = TemplateStrategy(t, C.resolve_settings(t, tf)).use_exits(X.TemplateExits())
        sa, sb = a._compute(df), b._compute(df)
        assert np.array_equal(sa.direction, sb.direction) and np.array_equal(sa.invalidation, sb.invalidation, equal_nan=True), t.id
        assert sa.extras == {} and not a.acts_on_exit_signal, t.id
        for i in sa.signal_bars()[:3]:
            sig = a.analyze({a.label: df.iloc[:i + 1]}, "S")
            assert sig.target1 is None and sig.target2 is None and sig.risk_reward is None, t.id
            assert sig.stop_loss == pytest.approx(float(sa.invalidation[i])), t.id

    class Row:
        stop_source = target_rule = target_r = target_base_wave = target_ratio = None
    assert X.TemplateExits.from_record(Row()).is_default and X.TemplateExits.from_record(object()).is_default


# ===================================================================================================== the precedence (R4)
def test_the_precedence_table_per_instrument():
    assert X.instrument_class("UNDERLYING", "SINGLE") == X.UNDERLYING and X.instrument_class("FUTURE", None) == X.FUTURE
    assert X.instrument_class("OPTION", "SINGLE") == X.OPTION_SINGLE and X.instrument_class("OPTION", "BULL_PUT_SPREAD") == X.OPTION_STRUCTURE
    none = X.TemplateExits()
    assert none.effective(X.UNDERLYING) == none.effective(X.FUTURE) == none.effective(X.OPTION_SINGLE) == ("TEMPLATE_INVALIDATION", "NONE")
    assert none.effective(X.OPTION_STRUCTURE) == ("CREDIT_PCT", "CREDIT_PCT")
    assert X.PRECEDENCE[X.UNDERLYING][0] == X.PRECEDENCE[X.FUTURE][0] == ("TEMPLATE_INVALIDATION",)
    assert X.PRECEDENCE[X.OPTION_SINGLE][0] == ("TEMPLATE_INVALIDATION", "PREMIUM_PCT")
    assert X.PRECEDENCE[X.OPTION_STRUCTURE] == (("CREDIT_PCT",), ("CREDIT_PCT", "TEMPLATE_EXIT"))
    # every offered code is one the spec lists
    for stops, targets in X.PRECEDENCE.values():
        assert set(stops) <= set(X.STOP_SOURCES) and set(targets) <= set(X.TARGET_RULES)


CAPS = X.Capabilities(exit_signal=True, wave_projection=True)


@pytest.mark.parametrize("cls_, choice, message", [
    (X.UNDERLYING, X.TemplateExits(stop_source="PREMIUM_PCT"), "On shares the stop is always the template's own level"),
    (X.FUTURE, X.TemplateExits(stop_source="CREDIT_PCT"), "On a future the stop is always the template's own level"),
    (X.OPTION_SINGLE, X.TemplateExits(stop_source="CREDIT_PCT"), "A single option's stop is the template's own level or your premium stop"),
    (X.OPTION_STRUCTURE, X.TemplateExits(stop_source="TEMPLATE_INVALIDATION"), "A spread's stop is its own loss limit (stop % of the credit)"),
    (X.OPTION_SINGLE, X.TemplateExits(stop_source="NONE"), X.NOT_BUILT["stop:NONE"]),
    (X.UNDERLYING, X.TemplateExits(target_rule="NEXT_AREA"), X.NOT_BUILT["target:NEXT_AREA"]),
    (X.UNDERLYING, X.TemplateExits(stop_source="SOMETHING"), "Pick where the trade's stop comes from from the list"),
    (X.UNDERLYING, X.TemplateExits(target_rule="SOMETHING"), "Pick when to take profit from the list"),
    (X.UNDERLYING, X.TemplateExits(target_rule="CREDIT_PCT"), "A spread's target % applies to spreads only"),
    (X.OPTION_STRUCTURE, X.TemplateExits(target_rule="R_MULTIPLE", target_r=2.0),
     "A spread takes profit at its own target % of the credit, or on the template's exit signal"),
    (X.UNDERLYING, X.TemplateExits(target_rule="R_MULTIPLE"), "Type how many times the risk to take profit at (0.1 to 20)"),
    (X.UNDERLYING, X.TemplateExits(target_rule="R_MULTIPLE", target_r=0.05), "Type how many times the risk to take profit at (0.1 to 20)"),
    (X.UNDERLYING, X.TemplateExits(target_rule="R_MULTIPLE", target_r=float("nan")), "Type how many times the risk to take profit at (0.1 to 20)"),
    (X.UNDERLYING, X.TemplateExits(target_rule="NONE", target_r=2.0), "The times-the-risk number is for the risk-multiple target only"),
    (X.UNDERLYING, X.TemplateExits(target_rule="WAVE_PROJECTION", target_ratio=1.0), "Pick the wave the projection is measured on"),
    (X.UNDERLYING, X.TemplateExits(target_rule="WAVE_PROJECTION", target_base_wave="1", target_ratio=0.0),
     "Type the projection's size as a share of that wave (above 0, up to 10)"),
    (X.UNDERLYING, X.TemplateExits(target_rule="WAVE_PROJECTION", target_base_wave="1", target_ratio=10.5),
     "Type the projection's size as a share of that wave (above 0, up to 10)"),
    (X.UNDERLYING, X.TemplateExits(target_rule="TEMPLATE_EXIT", target_ratio=1.0), "The wave and its share are for the wave projection target only"),
])
def test_every_refusal_in_plain_words(cls_, choice, message):
    assert message in choice.problems(cls_, CAPS)


def test_the_choices_that_fit():
    for cls_ in (X.UNDERLYING, X.FUTURE, X.OPTION_SINGLE):
        for choice in (X.TemplateExits(stop_source="TEMPLATE_INVALIDATION", target_rule="NONE"),
                       X.TemplateExits(target_rule="R_MULTIPLE", target_r=2.0), X.TemplateExits(target_rule="TEMPLATE_EXIT"),
                       X.TemplateExits(target_rule="WAVE_PROJECTION", target_base_wave="1", target_ratio=1.618)):
            assert choice.problems(cls_, CAPS) == [], (cls_, choice)
    assert X.TemplateExits(stop_source="PREMIUM_PCT", target_rule="R_MULTIPLE", target_r=1.5).problems(X.OPTION_SINGLE, CAPS) == []
    assert X.TemplateExits(stop_source="CREDIT_PCT", target_rule="TEMPLATE_EXIT").problems(X.OPTION_STRUCTURE, CAPS) == []
    # a template without an exit signal / not a wave template
    plain = X.Capabilities()
    assert X.TemplateExits(target_rule="TEMPLATE_EXIT").problems(X.UNDERLYING, plain) == ["This template has no exit signal with these settings"]
    assert X.TemplateExits(target_rule="WAVE_PROJECTION", target_base_wave="1", target_ratio=1.0).problems(X.UNDERLYING, plain) == [
        "The wave projection is for the wave templates only"]


def test_the_capabilities_follow_the_template_and_its_settings():
    assert _strategy("D1").capabilities() == X.Capabilities(exit_signal=True, wave_projection=False)
    assert _strategy("B8").capabilities().exit_signal is False                   # B8 has no exit signal (TPL-Q32)
    g1 = library.get("G1")
    caps = TemplateStrategy(g1, C.resolve_settings(g1, "15m", params={"wave_tf": "15m", "higher_tf": "1h"})).capabilities()
    assert caps == X.Capabilities(exit_signal=False, wave_projection=True)      # G1's exit signal is "none" by default
    on = TemplateStrategy(g1, C.resolve_settings(g1, "15m", params={"wave_tf": "15m", "higher_tf": "1h", "exit_signal": "choch"}))
    assert on.capabilities().exit_signal is True


def test_a_strategy_that_is_not_a_template_refuses_the_choice():
    """Section 5: the DSL v1 / built-in strategies keep their own stop / target (regression: unchanged)."""
    from app.strategy_engine.registry import registry
    builtin = registry.get("ema_rsi_scalper_1m")
    with pytest.raises(ValueError) as exc:
        X.checked(builtin, X.TemplateExitsBody(target_rule="R_MULTIPLE", target_r=2.0), "UNDERLYING", "SINGLE")
    assert exc.value.args[0] == ["These exit choices are for strategy templates only"]
    assert X.checked(builtin, None, "UNDERLYING", "SINGLE") is X.DEFAULT
    assert X.checked(builtin, X.TemplateExitsBody(), "UNDERLYING", "SINGLE") is X.DEFAULT
    assert not hasattr(builtin, "acts_on_exit_signal")


# ================================================================================================== the target level
def test_the_target_level_by_value():
    assert X.target_level("R_MULTIPLE", 1, 100.0, 98.0, target_r=2.0) == (104.0, 2.0)
    assert X.target_level("R_MULTIPLE", -1, 100.0, 103.0, target_r=1.5) == (95.5, 1.5)
    assert X.target_level("WAVE_PROJECTION", 1, 100.0, 98.0, projection=106.0) == (106.0, 3.0)
    assert X.target_level("WAVE_PROJECTION", -1, 100.0, 104.0, projection=90.0) == (90.0, 2.5)
    # no projection, or one not beyond the entry on the winning side: no target, never a refusal
    assert X.target_level("WAVE_PROJECTION", 1, 100.0, 98.0, projection=float("nan")) == (None, None)
    assert X.target_level("WAVE_PROJECTION", 1, 100.0, 98.0, projection=99.0) == (None, None)
    assert X.target_level("WAVE_PROJECTION", -1, 100.0, 104.0, projection=100.0) == (None, None)
    assert X.target_level("WAVE_PROJECTION", 1, 100.0, 98.0, projection=None) == (None, None)
    assert X.target_level("NONE", 1, 100.0, 98.0, target_r=2.0) == (None, None)
    assert X.target_level("TEMPLATE_EXIT", 1, 100.0, 98.0) == (None, None)
    assert X.target_level("R_MULTIPLE", 1, 100.0, 100.0, target_r=2.0) == (None, None)      # no risk: no level


# ====================================================================================================== R multiple
R2 = X.TemplateExits(target_rule="R_MULTIPLE", target_r=2.0)


def test_r_multiple_fires_on_exactly_the_default_signal_bars_with_the_level_by_value():
    df = _bars(3)
    plain, chosen = _strategy(), _strategy(choice=R2)
    s0, s1 = plain._compute(df), chosen._compute(df)
    assert np.array_equal(s0.direction, s1.direction) and np.array_equal(s0.invalidation, s1.invalidation, equal_nan=True)
    bars = s1.signal_bars()
    assert {1, -1} <= {int(s1.direction[i]) for i in bars}, "the fixture must carry long and short signals"
    for i in bars:
        sig = chosen.analyze({chosen.label: df.iloc[:i + 1]}, "S")
        d = 1 if sig.direction == SignalDirection.LONG else -1
        entry, stop = float(df["close"].iloc[i]), float(s1.invalidation[i])
        assert sig.entry == entry and sig.stop_loss == stop and sig.risk_reward == 2.0
        assert sig.target1 == pytest.approx(round(entry + d * 2.0 * abs(entry - stop), 2)) and sig.target2 is None
        assert (sig.target1 > entry) if d > 0 else (sig.target1 < entry)


def test_r_multiple_scan_equals_backtest_equals_worker():
    df = _bars(3)
    chosen = _strategy(choice=R2)
    series = chosen._compute(df)
    scan = {df.index[i]: chosen.target_at(series, i, int(series.direction[i]), float(df["close"].iloc[i]), float(series.invalidation[i]))[0]
            for i in series.signal_bars()}
    result = run_backtest(_strategy(choice=R2), df, VENUE_24X7, "15min", RISK)
    assert result.trades
    for trade in result.trades:
        assert trade.target1 == pytest.approx(scan[pd.Timestamp(trade.entry_time)])
        worker = _strategy(choice=R2).analyze({"15min": df.loc[:trade.entry_time]}, "S")
        assert worker.target1 == pytest.approx(trade.target1)
    assert any(t.exit_reason == "Target 1" for t in result.trades), "the R multiple must be reached on this fixture"
    # the options engine reads the same signal levels (the single option's underlying target)
    big = _bars(6, scale=240.0)
    opts = run_option_backtest(_strategy(choice=R2), big, "NIFTY 50", "15min", RISK, SINGLE)
    assert any((s["exit_reason"] or "").startswith("Target 1") for s in opts.options["structures"])


def test_the_accounts_minimum_risk_reward_still_applies():
    df = _bars(3)
    strict = RiskConfig(capital=10_000_000, risk_per_trade_pct=0.5, min_risk_reward=2.5)
    assert run_backtest(_strategy(choice=R2), df, VENUE_24X7, "15min", strict).total_trades == 0          # 2R below the minimum
    assert run_backtest(_strategy(), df, VENUE_24X7, "15min", strict).total_trades > 0                    # no target: no R:R check
    assert run_backtest(_strategy(choice=X.TemplateExits(target_rule="R_MULTIPLE", target_r=3.0)), df, VENUE_24X7, "15min", strict).total_trades > 0


# ========================================================================================= the template's exit signal (R5)
EXIT = X.TemplateExits(target_rule="TEMPLATE_EXIT")


def _first_exit_bar(series, side: int, after: int, last: int):
    flags = series.exit_long if side > 0 else series.exit_short
    for j in range(after + 1, last):
        if flags[j]:
            return j
    return None


def _template_exit_bars(df, series, entries_exits):
    """Each (entry bar, exit bar, reason): a "Template exit" is exactly the bar after the first exit bar of the signal's
    side since its entry bar (never the entry bar itself); any other exit came on or before that bar."""
    seen = 0
    for e, x, reason in entries_exits:
        side = int(series.direction[e])
        assert side != 0, "every trade opens on a scan signal bar"
        j = _first_exit_bar(series, side, e, len(df) - 1)
        if reason == "Template exit":
            seen += 1
            assert j is not None and x == j + 1 and x > e, (e, j, x)
        elif j is not None and reason != "End of backtest":
            assert x <= j + 1, (e, j, x, reason)
    return seen


def test_the_backtest_closes_on_the_exit_signal_at_the_next_open():
    df = _bars(5)
    series = _strategy()._compute(df)
    pos = {ts: i for i, ts in enumerate(df.index)}
    result = run_backtest(_strategy(choice=EXIT), df, VENUE_24X7, "15min", RISK)
    rows = [(pos[pd.Timestamp(t.entry_time)], pos[pd.Timestamp(t.exit_time)], t.exit_reason) for t in result.trades]
    assert _template_exit_bars(df, series, rows) >= 3
    for t in result.trades:
        if t.exit_reason == "Template exit":                     # filled at that bar's open (the paper broker's slippage aside)
            assert t.exit_price == pytest.approx(float(df["open"].loc[t.exit_time]), rel=2e-3)
    # must not: without the choice the same exit bars close nothing
    plain = run_backtest(_strategy(), df, VENUE_24X7, "15min", RISK)
    assert not any(t.exit_reason == "Template exit" for t in plain.trades)


HELD_SINGLE = _option_config(SINGLE, premium_stop_pct=95.0, intraday=False)
HELD_SPREAD = OptionBacktestConfig(option_strategy=OptionStrategy.BULL_PUT_SPREAD, implied_volatility=0.14, spread_width=2, intraday=False,
                                   strike_rule=StrikeRule.OTM, strike_offset=4)


@pytest.mark.parametrize("config", [HELD_SINGLE, HELD_SPREAD], ids=["single", "spread"])
def test_the_options_backtest_closes_on_the_exit_signal_at_the_next_open(config):
    """B1 (exit: a close back inside the range) on positions held past the session (no square-off), so the exit signal
    is reached: a single option with a far premium stop, an out-of-the-money bull put spread."""
    df = _bars(2, scale=240.0)
    series = _strategy("B1")._compute(df)
    pos = {ts: i for i, ts in enumerate(df.index)}
    result = run_option_backtest(_strategy("B1", choice=EXIT), df, "NIFTY 50", "15min", RISK, config)
    rows = [(pos[pd.Timestamp(st["entry_time"])], pos[pd.Timestamp(st["exit_time"])], st["exit_reason"]) for st in result.options["structures"]]
    assert _template_exit_bars(df, series, rows) >= 2, "the fixture must reach the exit signal"
    plain = run_option_backtest(_strategy("B1"), df, "NIFTY 50", "15min", RISK, config)
    assert not any(s["exit_reason"] == "Template exit" for s in plain.options["structures"])


def test_scan_equals_both_engines_on_the_exit_bars():
    """The bar each engine read the exit on (the bar before its close) is a bar of the scan's exit series for that side,
    and the worker's exit_signal on that bar's closed prefix says the same (S11)."""
    df = _bars(5)
    series = _strategy()._compute(df)
    pos = {ts: i for i, ts in enumerate(df.index)}
    result = run_backtest(_strategy(choice=EXIT), df, VENUE_24X7, "15min", RISK)
    for t in (t for t in result.trades if t.exit_reason == "Template exit"):
        j = pos[pd.Timestamp(t.exit_time)] - 1
        side = "LONG" if t.direction == SignalDirection.LONG else "SHORT"
        assert (series.exit_long if side == "LONG" else series.exit_short)[j]
        assert _strategy(choice=EXIT).exit_signal({"15min": df.iloc[:j + 1]}, side) is True


# ================================================================================================== the wave projection
def _g1_case():
    from tests.test_wave4_templates import fixture
    template, bars, params, bar, side = fixture("G1", "choch")
    return template, bars, params, bar, side


def test_the_wave_projection_is_the_registrys_own_value_on_the_signal_bar():
    from tests.test_wave4_templates import ev
    template, bars, params, bar, side = _g1_case()
    choice = X.TemplateExits(target_rule="WAVE_PROJECTION", target_base_wave="1", target_ratio=1.0)
    strat = TemplateStrategy(template, C.resolve_settings(template, "15m", params=params)).use_exits(choice)
    series = strat._compute(bars)
    assert series.signal_bars() == [bar] and side == 1
    want = float(ev(f'WaveProjection({params.get("wave_degree", 1)}, "1", 1.0)', bars).iloc[bar])
    assert np.isfinite(want) and want > float(bars["close"].iloc[bar])
    sig = strat.analyze({strat.label: bars.iloc[:bar + 1]}, "S")
    assert sig.target1 == pytest.approx(round(want, 2))
    risk = abs(sig.entry - sig.stop_loss)
    assert sig.risk_reward == pytest.approx(round(abs(want - sig.entry) / risk, 2))
    # scan = backtest = worker on the same bars
    result = run_backtest(TemplateStrategy(template, C.resolve_settings(template, "15m", params=params)).use_exits(choice), bars, "S",
                          "15min", RiskConfig(capital=10_000_000, risk_per_trade_pct=0.5, min_risk_reward=0.1))
    assert len(result.trades) == 1 and result.trades[0].target1 == pytest.approx(sig.target1)
    # the signals themselves are the default's
    plain = TemplateStrategy(template, C.resolve_settings(template, "15m", params=params))._compute(bars)
    assert np.array_equal(plain.direction, series.direction) and plain.extras == {}


def test_no_projection_on_the_signal_bar_is_no_target_never_a_refusal():
    template, bars, params, bar, _ = _g1_case()
    # wave C of a correction does not exist inside this impulse: the projection is missing on the signal bar
    choice = X.TemplateExits(target_rule="WAVE_PROJECTION", target_base_wave="C", target_ratio=1.0)
    strat = TemplateStrategy(template, C.resolve_settings(template, "15m", params=params)).use_exits(choice)
    series = strat._compute(bars)
    assert not np.isfinite(series.extras["projection"][bar])
    sig = strat.analyze({strat.label: bars.iloc[:bar + 1]}, "S")
    assert sig.is_tradeable and sig.target1 is None and sig.risk_reward is None


def test_the_wave_projection_refuses_a_template_that_counts_no_waves():
    from app.strategy_templates.compile import TemplateError
    with pytest.raises(TemplateError):
        _strategy("D1", choice=X.TemplateExits(target_rule="WAVE_PROJECTION", target_base_wave="1", target_ratio=1.0))


# ============================================================================================ a single option's premium stop
def test_premium_stop_only_drops_the_underlying_stop_in_the_options_backtest():
    df = _bars(2, scale=240.0)
    wide = _option_config(SINGLE, premium_stop_pct=95.0, intraday=False)       # held, with a far premium stop
    default = run_option_backtest(_strategy("D2", invalidation="slow_average"), df, "NIFTY 50", "15min", RISK, wide)
    reasons = [s["exit_reason"] for s in default.options["structures"]]
    assert any(r.startswith("Stop Loss (underlying)") for r in reasons), "the fixture must reach the underlying stop by default"
    premium = run_option_backtest(_strategy("D2", invalidation="slow_average"), df, "NIFTY 50", "15min", RISK,
                                  _option_config(wide, stop_source="PREMIUM_PCT"))
    assert not any(s["exit_reason"].startswith("Stop Loss (underlying)") for s in premium.options["structures"])
    assert premium.options["structures"], "the premium stop only still trades the signals"


def test_the_live_order_plan_and_the_contract_rules_follow_the_stop_source():
    from datetime import date

    from app.core.enums import InstrumentKind, OptionPosition, OrderSide
    from app.core.models import Signal
    from app.execution.contract_execution import build_order_plan
    from app.instruments.contracts import ContractRules, ResolvedContract

    class Dep:
        instrument_kind, option_position, expiry_rule, strike_rule, strike_offset = "OPTION", "BUY", None, None, 0
        premium_stop_pct, max_lots, strike_filters, stop_source = 30.0, 1, None, "PREMIUM_PCT"   # DEF-0: his own 30% floor
    rules = ContractRules.from_deployment(Dep())
    assert rules.underlying_stop is False
    Dep.stop_source = None
    assert ContractRules.from_deployment(Dep()).underlying_stop is True
    contract = ResolvedContract(kind=InstrumentKind.OPTION, underlying="NIFTY", underlying_symbol="NIFTY 50", tradingsymbol="NIFTY26OCT24000CE",
                                exchange="NFO", instrument_key="NSE_FO|1", lot_size=75, tick_size=0.05, expiry=date(2026, 10, 27),
                                strike=24000.0, right="CE", entry_side=OrderSide.BUY, trade_direction=SignalDirection.LONG,
                                position=OptionPosition.BUY)
    sig = Signal(symbol="NIFTY 50", strategy_id="template:D1", strategy_name="x", direction=SignalDirection.LONG,
                 timestamp=pd.Timestamp("2026-10-23T05:00:00Z"), entry=24000.0, stop_loss=23900.0, target1=24200.0)
    assert build_order_plan(sig, contract, rules, 120.0).meta["underlying_stop_loss"] is None
    assert build_order_plan(sig, contract, ContractRules.from_deployment(Dep()), 120.0).meta["underlying_stop_loss"] == 23900.0
    for plan in (build_order_plan(sig, contract, rules, 120.0),):
        assert plan.meta["underlying_target1"] == 24200.0 and plan.order_signal.stop_loss == pytest.approx(84.0)   # the premium floor


def test_an_underlying_target_exits_without_an_underlying_stop():
    from app.trading.exit_logic import check_contract_exit, underlying_exit

    assert underlying_exit("LONG", None, 24610.0, None, 24620.0) == "Target 1"
    assert underlying_exit("SHORT", None, 24414.0, None, 24400.0) == "Target 1"
    assert underlying_exit("LONG", None, 24610.0, None, 24000.0) is None          # no stop: a fall never exits here
    assert underlying_exit("LONG", 24460.0, 24610.0, 24700.0, 24450.0) == "Stop Loss"   # unchanged with a stop

    class Trade:
        instrument_kind, direction, stop_loss = "OPTION", "LONG", 84.0
        underlying_direction, underlying_stop_loss, underlying_target1, underlying_target2 = "LONG", None, 24200.0, None
    assert check_contract_exit(Trade(), 130.0, 24210.0) == ("Target 1 (underlying)", 130.0)
    assert check_contract_exit(Trade(), 130.0, 23000.0) is None                     # the premium stop alone guards it
    assert check_contract_exit(Trade(), 80.0, 23000.0) == ("Premium floor", 80.0)


# ================================================================================================== the signal text (11l)
def test_the_signal_text_never_carries_the_exit_choice():
    df = _bars(3)
    for choice in (R2, EXIT, X.TemplateExits(stop_source="TEMPLATE_INVALIDATION", target_rule="NONE")):
        s = _strategy(choice=choice)
        series = s._compute(df)
        for i in series.signal_bars()[:4]:
            sig = s.analyze({s.label: df.iloc[:i + 1]}, "S")
            text = " ".join(sig.reasons).lower()
            assert not any(w in text for w in ("stop", "target", "profit", "buy", "sell", "risk")), text
            assert not any(ch.isdigit() for ch in text.replace("15-minute", "")), text
            assert sig.reasons == _strategy().analyze({s.label: df.iloc[:i + 1]}, "S").reasons


# ============================================================================================== the worker (R5, tenancy)
def _run(coro):
    return asyncio.run(coro)


class _Market:
    def __init__(self, price):
        self.price, self.asked = price, []

    async def get_ltp(self, symbol, exchange="NSE"):
        self.asked.append(symbol)
        return self.price


def _exit_frame():
    """D1 bars up to a bar where the long exit fires (the opposite flip), and that bar's index."""
    df = _bars(5)
    series = _strategy()._compute(df)
    j = next(i for i in range(200, len(df)) if series.exit_long[i])
    return df.iloc[:j + 1], j


def test_the_worker_closes_the_deployments_own_paper_trade_on_the_exit_bar():
    from sqlalchemy import select

    from app.db.models import StrategyDeploymentRecord, TradeRecord
    from app.workers.trading_worker import TradingWorker
    from tests.test_auth_api import _session_factory
    from tests.test_trading_worker import _tenant

    frame, j = _exit_frame()
    bar_start = frame.index[-1].to_pydatetime()
    a = _tenant(f"tpl10-a-{uuid.uuid4().hex[:6]}@example.com")
    b = _tenant(f"tpl10-b-{uuid.uuid4().hex[:6]}@example.com")

    async def go():
        async with _session_factory() as session:
            dep = StrategyDeploymentRecord(holding="INTRADAY", order_style="MARKET", tenant_id=a["tenant_id"], strategy_id=_strategy().id, symbol="RELIANCE", exchange="NSE",
                                           timeframe="15min", mode="PAPER", broker_name="upstox", status="ACTIVE", created_by=a["user_id"],
                                           target_rule="TEMPLATE_EXIT")
            session.add(dep)
            await session.flush()

            def trade(tenant, **kw):
                row = TradeRecord(tenant_id=tenant["tenant_id"], user_id=tenant["user_id"], deployment_id=dep.id, symbol="RELIANCE",
                                  strategy_id=dep.strategy_id, direction=kw.pop("direction", "LONG"), mode=kw.pop("mode", "PAPER"),
                                  entry_time=kw.pop("entry_time", bar_start - timedelta(minutes=45)), entry_price=100.0, quantity=1,
                                  stop_loss=90.0, target1=None)
                session.add(row)
                return row
            mine = trade(a)
            short = trade(a, direction="SHORT")
            late = trade(a, entry_time=bar_start + timedelta(minutes=16))           # entered after the exit bar closed
            other_org = trade(b)                                                   # another organisation's row: never touched
            await session.commit()
            strat = _strategy(choice=X.TemplateExits(**{k: getattr(dep, k) for k in X.TemplateExits.__dataclass_fields__}))
            market = _Market(101.0)
            closed = await TradingWorker(_session_factory)._template_exit(session, dep, strat, {"15min": frame}, market, a["user_id"])
            await session.commit()
            rows = {r.id: r for r in await session.scalars(select(TradeRecord).where(TradeRecord.deployment_id == dep.id))}
            return closed, rows, (mine.id, short.id, late.id, other_org.id), market
    closed, rows, (mine, short, late, other_org), market = _run(go())
    assert closed == 1
    assert rows[mine].exit_time is not None and rows[mine].exit_reason == "Template exit" and rows[mine].exit_price == pytest.approx(101.0, abs=0.2)
    assert rows[short].exit_time is None                     # the long exit is not the short side's (S10: its own side)
    assert rows[late].exit_time is None                      # only a bar that closed after the entry counts
    assert rows[other_org].exit_time is None                 # tenancy: another organisation's row is never read
    assert market.asked == ["RELIANCE"]


def test_the_worker_never_acts_on_a_live_trade_or_without_the_choice():
    from app.db.models import StrategyDeploymentRecord, TradeRecord
    from app.workers.trading_worker import TradingWorker
    from tests.test_auth_api import _session_factory
    from tests.test_trading_worker import _tenant

    frame, _ = _exit_frame()
    a = _tenant(f"tpl10-live-{uuid.uuid4().hex[:6]}@example.com")

    async def go():
        async with _session_factory() as session:
            dep = StrategyDeploymentRecord(holding="INTRADAY", order_style="MARKET", tenant_id=a["tenant_id"], strategy_id=_strategy().id + "x", symbol="RELIANCE", exchange="NSE",
                                           timeframe="15min", mode="PAPER", broker_name="upstox", status="ACTIVE", created_by=a["user_id"],
                                           target_rule="TEMPLATE_EXIT")
            session.add(dep)
            await session.flush()
            row = TradeRecord(tenant_id=a["tenant_id"], user_id=a["user_id"], deployment_id=dep.id, symbol="RELIANCE",
                              strategy_id=dep.strategy_id, direction="LONG", mode="LIVE",
                              entry_time=frame.index[-1].to_pydatetime() - timedelta(minutes=45), entry_price=100.0, quantity=1, stop_loss=90.0)
            session.add(row)
            await session.commit()
            closed = await TradingWorker(_session_factory)._template_exit(session, dep, _strategy(choice=EXIT), {"15min": frame},
                                                                           _Market(101.0), a["user_id"])
            return closed
    assert _run(go()) == 0
    assert _strategy().acts_on_exit_signal is False and _strategy(choice=R2).acts_on_exit_signal is False


# ======================================================================================================== the routes
def _candles(df):
    return [{"timestamp": ts.isoformat(), "open": r.open, "high": r.high, "low": r.low, "close": r.close, "volume": r.volume}
            for ts, r in df.iterrows()]


@pytest.fixture()
def org():
    from app.core import rate_limit
    from tests.test_tpl2_routes import _org
    rate_limit.reset()
    yield _org(f"x{uuid.uuid4().hex[:4]}", "strategy_templates")
    rate_limit.reset()


def test_the_deployment_route_stores_checks_and_answers_the_choice(org):
    from tests.test_deployments_api import _store_broker
    from tests.test_tpl2_routes import client
    _store_broker(org["headers"])
    pre = client.post("/api/strategy-templates/D1/adopt", headers=org["headers"], json={"timeframe": "15m"}).json()
    dep = {**pre["deployment"], "instrument_kind": "UNDERLYING"}
    plain = client.post("/api/deployments", headers=org["headers"], json={**dep, "symbol": "reliance"})
    assert plain.status_code == 201 and plain.json()["template_exits"] is None
    made = client.post("/api/deployments", headers=org["headers"],
                       json={**dep, "symbol": "tcs", "template_exits": {"target_rule": "R_MULTIPLE", "target_r": 2}})
    assert made.status_code == 201, made.text
    assert made.json()["template_exits"] == {"target_rule": "R_MULTIPLE", "target_r": 2.0}
    assert "target at 2 times the risk" in made.json()["contract_rules"]
    ex = client.post("/api/deployments", headers=org["headers"], json={**dep, "symbol": "infy", "template_exits": {"target_rule": "TEMPLATE_EXIT"}})
    assert ex.status_code == 201 and ex.json()["template_exits"] == {"target_rule": "TEMPLATE_EXIT"}
    bad = client.post("/api/deployments", headers=org["headers"], json={**dep, "symbol": "sbin", "template_exits": {"stop_source": "PREMIUM_PCT"}})
    assert bad.status_code == 422 and bad.json()["detail"]["problems"] == ["On shares the stop is always the template's own level"]
    extra = client.post("/api/deployments", headers=org["headers"], json={**dep, "symbol": "sbin", "template_exits": {"stop": "x"}})
    assert extra.status_code == 422
    builtin = client.post("/api/deployments", headers=org["headers"],
                          json={"strategy_id": "ema_rsi_scalper_1m", "symbol": "sbin", "template_exits": {"target_rule": "TEMPLATE_EXIT"}})
    assert builtin.status_code == 422 and builtin.json()["detail"]["problems"] == ["These exit choices are for strategy templates only"]
    # tenancy: another organisation never sees the rows or their exit choice
    from tests.test_tpl2_routes import _org
    other = _org("other", "strategy_templates")
    assert all(d["symbol"] not in ("TCS", "INFY") for d in client.get("/api/deployments", headers=other["headers"]).json())
    mine = {d["symbol"]: d for d in client.get("/api/deployments", headers=org["headers"]).json()}
    assert mine["TCS"]["template_exits"] == {"target_rule": "R_MULTIPLE", "target_r": 2.0}


def test_the_backtest_route_takes_the_choice(org):
    from tests.test_tpl2_routes import client
    sid = client.post("/api/strategy-templates/D1/adopt", headers=org["headers"], json={"timeframe": "15m"}).json()["strategy_id"]
    body = {"strategy_id": sid, "symbol": VENUE_24X7, "base_timeframe": "15min", "candles": _candles(_bars(5, total=600)), "data_source": "sample",
            "risk_config": {"capital": 10_000_000, "risk_per_trade_pct": 0.5, "min_risk_reward": 0.5}}
    plain = client.post("/api/backtest", headers=org["headers"], json=body)
    chosen = client.post("/api/backtest", headers=org["headers"], json={**body, "template_exits": {"target_rule": "TEMPLATE_EXIT"}})
    assert plain.status_code == chosen.status_code == 200, chosen.text
    assert not any(t["exit_reason"] == "Template exit" for t in plain.json()["trades"])
    assert any(t["exit_reason"] == "Template exit" for t in chosen.json()["trades"])
    assert plain.json()["reproducibility"]["config_hash"] != chosen.json()["reproducibility"]["config_hash"]
    r = client.post("/api/backtest", headers=org["headers"], json={**body, "template_exits": {"target_rule": "R_MULTIPLE", "target_r": 2}})
    assert r.status_code == 200 and all(t["target1"] is not None for t in r.json()["trades"])
    bad = client.post("/api/backtest", headers=org["headers"], json={**body, "template_exits": {"stop_source": "PREMIUM_PCT"}})
    assert bad.status_code == 422 and bad.json()["detail"]["problems"] == ["On shares the stop is always the template's own level"]
    spread = client.post("/api/backtest", headers=org["headers"],
                         json={**body, "options": {"option_strategy": "BULL_PUT_SPREAD", "implied_volatility": 0.14},
                               "template_exits": {"target_rule": "R_MULTIPLE", "target_r": 2}})
    assert spread.status_code == 422


def test_adopt_offers_the_exit_choices_per_instrument(org):
    from tests.test_tpl2_routes import client
    exits = client.post("/api/strategy-templates/D1/adopt", headers=org["headers"], json={"timeframe": "15m"}).json()["exits"]
    assert exits["exit_signal"] is True and exits["wave_projection"] is False and exits["base_waves"] == []
    assert exits["precedence"]["UNDERLYING"] == {"stops": ["TEMPLATE_INVALIDATION"], "targets": ["NONE", "R_MULTIPLE", "TEMPLATE_EXIT"]}
    assert exits["precedence"]["OPTION_SINGLE"]["stops"] == ["TEMPLATE_INVALIDATION", "PREMIUM_PCT"]
    assert exits["precedence"]["OPTION_STRUCTURE"] == {"stops": ["CREDIT_PCT"], "targets": ["CREDIT_PCT", "TEMPLATE_EXIT"]}
    assert exits["target_r"] == {"min": 0.1, "max": 20.0}
    b8 = client.post("/api/strategy-templates/B8/adopt", headers=org["headers"], json={"timeframe": "15m"})
    if b8.status_code == 200:                                   # B8 needs the breakout flags; without them it is a 409
        assert "TEMPLATE_EXIT" not in b8.json()["exits"]["precedence"]["UNDERLYING"]["targets"]


def test_adopt_offers_the_wave_projection_on_a_wave_template():
    from app.core import rate_limit
    from tests.test_tpl2_routes import _org, client
    rate_limit.reset()
    org = _org("waves10", "strategy_templates", "screener_waves", "screener_breakouts", "screener_breakouts_bk4")
    r = client.post("/api/strategy-templates/G1/adopt", headers=org["headers"], json={"timeframe": "15m"})
    assert r.status_code == 200, r.text
    exits = r.json()["exits"]
    assert exits["wave_projection"] is True and "WAVE_PROJECTION" in exits["precedence"]["UNDERLYING"]["targets"]
    assert {"value": "1", "label": "wave one"} in exits["base_waves"] and exits["ratio"] == {"min": 0.0, "max": 10.0}
    assert all(not any(ch.isdigit() for ch in w["label"]) for w in exits["base_waves"])


# ===================================================================================================== the migration
ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "alembic" / "versions" / "d7f9b1c3e5a8_tpl_exits_as_settings.py"


def test_the_migration_is_additive_rollback_safe_reversible_and_on_the_one_head_chain():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from scripts.migration_rollback_safety import lint, risky_operations

    spec = importlib.util.spec_from_file_location("tpl10_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "d7f9b1c3e5a8" and module.down_revision == "c6e8a0b2d4f7" and module.ROLLBACK_SAFE is True
    source = MIGRATION.read_text(encoding="utf-8")
    assert risky_operations(source) == [] and not hasattr(module, "ROLLBACK_SAFE_REASON")
    assert not [p for p in lint() if p.startswith(MIGRATION.name)]
    for column in module.COLUMNS:
        assert f"sa.Column('{column}'" in source and f"drop_column('strategy_deployments', '{column}')" in source
    assert source.count("nullable=True") == 5 and "server_default" not in source and "nullable=False" not in source
    script = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    heads = script.get_heads()                     # one head, with this revision on its chain (View as user chains after it)
    assert len(heads) == 1 and "d7f9b1c3e5a8" in {r.revision for r in script.walk_revisions("base", heads[0])}


def test_the_model_columns_match_the_migration_and_are_nullable():
    from app.db.models import StrategyDeploymentRecord
    table = StrategyDeploymentRecord.__table__
    for name, length in (("stop_source", 24), ("target_rule", 24), ("target_base_wave", 16)):
        assert table.c[name].nullable and table.c[name].type.length == length and table.c[name].server_default is None
    for name in ("target_r", "target_ratio"):
        assert table.c[name].nullable and table.c[name].server_default is None
    assert set(X.TemplateExits.__dataclass_fields__) == {"stop_source", "target_rule", "target_r", "target_base_wave", "target_ratio"}


def test_the_migration_runs_up_and_down_on_sqlite(tmp_path):
    """Upgrade adds the five columns with NULL on an existing row (as before); downgrade removes them again."""
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    spec = importlib.util.spec_from_file_location("tpl10_migration_run", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    eng = sa.create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    with eng.begin() as conn:
        conn.execute(sa.text("CREATE TABLE strategy_deployments (id INTEGER PRIMARY KEY, strategy_id VARCHAR(100))"))
        conn.execute(sa.text("INSERT INTO strategy_deployments (id, strategy_id) VALUES (1, 'template:D1:1')"))
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            module.upgrade()
        cols = {c["name"] for c in sa.inspect(conn).get_columns("strategy_deployments")}
        assert set(module.COLUMNS) <= cols
        row = conn.execute(sa.text("SELECT stop_source, target_rule, target_r, target_base_wave, target_ratio FROM strategy_deployments")).one()
        assert tuple(row) == (None,) * 5
        with Operations.context(ctx):
            module.downgrade()
        assert {c["name"] for c in sa.inspect(conn).get_columns("strategy_deployments")} == {"id", "strategy_id"}
