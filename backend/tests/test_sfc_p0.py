"""SFC P0 (owner APPROVED 19 Nov, docs/plans/strategy-first-chart-PLAN.md): the `atp.strategy/2` schema, its compiler and
THE evaluator the chart, the backtest, PAPER and LIVE all read.

Checks: DEF-0 (nothing pre-filled, an incomplete rule refused), the comparators' exact ScreenQL, OR >= AND (SPEC test 2's
engine side), no look-ahead (a placed signal never moves when bars are added - SPEC test 5's engine side), a higher
timeframe's value only after its bar closed, chart = backtest = worker on the same bars, a v1 strategy translated gives
the v1 engine's own answers, the resolver, ADR-0006 (no signal module imports the order path) and the speed target.
Made-up bars only (app/quality/lookahead.synthetic_bars)."""
from __future__ import annotations

import ast
import copy
import pathlib
import time

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from app.core.enums import SignalDirection
from app.core.models import RiskConfig
from app.quality.lookahead import synthetic_bars
from app.strategy_engine.declarative import CustomStrategyConfig, DeclarativeStrategy
from app.strategy_v2 import adapter as A
from app.strategy_v2.compile import StrategyError, compile_strategy
from app.strategy_v2.evaluate import evaluate
from app.strategy_v2.schema import StrategyV2
from app.strategy_v2.translate import translate

RISK = RiskConfig(capital=10_000_000, risk_per_trade_pct=0.5)


def ema(n):
    return {"kind": "indicator", "block": "EMA", "params": {"x": "close", "n": n}}


def cond(cid, left, cmp, right=None, **kw):
    out = {"id": cid, "left": left, "cmp": cmp, **kw}
    if right is not None:
        out["right"] = right
    return out


def strategy(long_items, op="AND", short_items=None, tf="5m", exit=None, window=None):
    entry = {"sides": "both" if short_items else "long", "long": {"op": op, "items": long_items}}
    if short_items:
        entry["short"] = {"op": op, "items": short_items}
    if window:
        entry["window"] = window
    raw = {"schema": "atp.strategy/2", "version": 1, "name": "Test", "timeframe": tf, "entry": entry}
    if exit:
        raw["exit"] = exit
    return StrategyV2.model_validate(raw)


CROSS = cond("c1", ema(9), "crosses_above", ema(21))
RSI_UP = cond("c2", {"kind": "indicator", "block": "RSI", "params": {"n": 14}}, "gt", {"kind": "value", "value": 55})
SHORT = [cond("s1", ema(9), "crosses_below", ema(21))]


def bars(n=900, tf="5m", seed=3):
    return synthetic_bars(n, tf=tf, seed=seed)


# --- DEF-0 and the schema --------------------------------------------------------------------------------------------
def test_nothing_is_pre_filled_and_an_incomplete_rule_is_refused():
    s = strategy([CROSS])
    assert s.exit is None and s.options is None and s.entry.window is None and s.entry.max_entries_per_day is None
    with pytest.raises(ValidationError, match="needs: value, atr_period"):
        strategy([CROSS], exit={"stop": {"kind": "atr"}})
    with pytest.raises(ValidationError, match="needs something to compare"):
        strategy([cond("x", ema(9), "gt")])
    with pytest.raises(ValidationError, match="needs the number of bars"):
        strategy([cond("x", ema(9), "rising")])
    with pytest.raises(ValidationError, match="used twice"):
        strategy([CROSS, CROSS])
    with pytest.raises(ValidationError, match="long entries need"):
        StrategyV2.model_validate({"schema": "atp.strategy/2", "version": 1, "name": "x", "timeframe": "5m", "entry": {"sides": "long"}})


# --- the compiler ------------------------------------------------------------------------------------------------------
def test_each_comparator_compiles_to_its_exact_closed_bar_meaning():
    c = compile_strategy(strategy([
        CROSS, cond("c3", {"kind": "price", "field": "close"}, "between", low=ema(50), high={"kind": "value", "value": 1e9}),
        cond("c4", ema(20), "rising", bars=2, timeframe="15m"),
        cond("c5", {"kind": "pa", "block": "Pattern", "params": {"name": "hammer"}}, "is_true", within_bars=3),
    ]))
    t = {k: v.text for k, v in c.leaves.items()}
    assert t["c1"] == "CrossAbove(EMA(close, 9), EMA(close, 21))"
    assert t["c3"] == "(close) BETWEEN (EMA(close, 50)) AND (1000000000.0)"
    assert t["c4"] == "(EMA(close, 20)) > (EMA(close, 20)[1]) AND (EMA(close, 20)[1]) > (EMA(close, 20)[2])"
    assert t["c5"] == 'Count(3, Pattern("hammer")) >= 1'
    assert c.leaves["c4"].timeframe == "15m" and c.lookback["15m"] >= 20


def test_what_the_registry_refuses_is_said_plainly():
    with pytest.raises(StrategyError, match="is not something ATP computes"):
        compile_strategy(strategy([cond("x", {"kind": "indicator", "block": "Nope", "params": {}}, "gt", {"kind": "value", "value": 1})]))
    with pytest.raises(StrategyError, match="has no setting 'bogus'"):
        compile_strategy(strategy([cond("x", {"kind": "indicator", "block": "RSI", "params": {"bogus": 1}}, "gt", {"kind": "value", "value": 1})]))
    with pytest.raises(StrategyError, match="must be 15m or a higher one"):
        compile_strategy(strategy([cond("x", ema(9), "gt", ema(21), timeframe="5m")], tf="15m"))


# --- the evaluator -----------------------------------------------------------------------------------------------------
def test_or_never_signals_less_than_and():
    df = bars()
    and_ = evaluate(compile_strategy(strategy([CROSS, RSI_UP], "AND")), {"5m": df})
    or_ = evaluate(compile_strategy(strategy([CROSS, RSI_UP], "OR")), {"5m": df})
    assert and_.raw["entry_long"].sum() >= 1
    assert or_.raw["entry_long"].sum() >= and_.raw["entry_long"].sum()
    assert (or_.raw["entry_long"] | ~and_.raw["entry_long"]).all()        # every AND bar is an OR bar
    assert len(or_.signal_bars("long")) >= 1


def test_a_placed_signal_never_moves_when_bars_are_added():
    s = strategy([CROSS, cond("h", ema(20), "rising", bars=1, timeframe="15m")], "AND", SHORT)
    c = compile_strategy(s)
    df = bars(800)
    whole = evaluate(c, {"5m": df})
    assert len(whole.signal_bars("long")) + len(whole.signal_bars("short")) >= 3
    for k in (300, 451, 617, 799):
        part = evaluate(c, {"5m": df.iloc[:k]})
        assert (part.signal_long == whole.signal_long[:k]).all() and (part.signal_short == whole.signal_short[:k]).all(), k
        for leaf in c.leaves:
            assert (part.truth[leaf] == whole.truth[leaf][:k]).all(), (k, leaf)


def test_a_higher_timeframe_value_arrives_only_when_its_bar_has_closed():
    c = compile_strategy(strategy([cond("h", ema(5), "gt", ema(10), timeframe="15m")]))
    df = bars(600)
    ev = evaluate(c, {"5m": df})
    values = ev.values["h"]["left"]
    d = np.diff(values)
    changed = np.flatnonzero(np.isfinite(d) & (d != 0)) + 1           # warm-up NaNs are not changes
    ends = (df.index + pd.Timedelta(minutes=5))[changed]
    assert len(changed) > 10
    assert all(ts.minute % 15 == 0 for ts in ends)          # only on a 5m bar that completes a 15m bar


def test_a_future_spike_never_changes_the_past():
    c = compile_strategy(strategy([CROSS, RSI_UP], "OR", SHORT))
    df = bars(700)
    base = evaluate(c, {"5m": df})
    spiked = df.copy()
    spiked.iloc[500:, spiked.columns.get_indexer(["open", "high", "low", "close"])] *= 1.2
    after = evaluate(c, {"5m": spiked})
    assert (after.signal_long[:500] == base.signal_long[:500]).all() and (after.signal_short[:500] == base.signal_short[:500]).all()


# --- one evaluator: chart = backtest = worker ---------------------------------------------------------------------------
class _Recorder:
    def __init__(self, strat):
        self.strategy, self.answers = strat, {}
        inner = strat.analyze

        def analyze(data, symbol):
            sig = inner(data, symbol)
            self.answers[pd.Timestamp(sig.timestamp)] = (sig.direction, sig.stop_loss)
            return sig
        strat.analyze = analyze


def _chart(strat, df):
    ev, _ = strat.evaluation(df.iloc[:], "S")
    out = {}
    for t in range(len(df)):
        sig = strat.signal_at(ev, t, "S")
        out[pd.Timestamp(ev.index[t])] = (sig.direction, sig.stop_loss)
    return out


def test_chart_equals_backtest_equals_worker():
    from app.backtest.engine import run_backtest
    s = strategy([CROSS], "AND", SHORT, exit={"stop": {"kind": "atr", "value": 1.5, "atr_period": 14}})
    df = bars(900).iloc[-600:]
    chart = _chart(A.StrategyV2Strategy("custom:1", s), df)
    signals = {ts for ts, (d, _) in chart.items() if d != SignalDirection.NO_TRADE}
    assert len(signals) >= 4
    rec = _Recorder(A.StrategyV2Strategy("custom:1", s))
    result = run_backtest(rec.strategy, df, "S", "5min", RISK)
    assert rec.answers and all(chart[ts] == got for ts, got in rec.answers.items())
    assert result.trades and all(pd.Timestamp(t.entry_time) in signals for t in result.trades)
    worker = A.StrategyV2Strategy("custom:1", s)
    picked = sorted({j for ts in signals for j in [df.index.get_loc(ts) - 1, df.index.get_loc(ts)]} | {len(df) - 1})
    for t in picked:
        sig = worker.analyze({"5min": df.iloc[:t + 1]}, "S")
        assert (sig.direction, sig.stop_loss) == chart[pd.Timestamp(df.index[t])], t


def test_the_stop_is_his_own_rule_and_nothing_else():
    df = bars(700)
    bare = A.StrategyV2Strategy("custom:1", strategy([CROSS]))
    ev, _ = bare.evaluation(df)
    t = ev.signal_bars("long")[0]
    sig = bare.signal_at(ev, t, "S")
    assert sig.stop_loss is None and sig.target1 is None and sig.risk_reward is None      # DEF-0: no default exits
    pts = A.StrategyV2Strategy("custom:1", strategy([CROSS], exit={"stop": {"kind": "points", "value": 10}, "target": {"kind": "pct", "value": 2}}))
    sig = pts.signal_at(pts.evaluation(df)[0], t, "S")
    assert sig.stop_loss == pytest.approx(sig.entry - 10) and sig.target1 == pytest.approx(sig.entry * 1.02)


def test_the_entry_window_is_his_operational_setting():
    df = bars(700)
    win = A.StrategyV2Strategy("custom:1", strategy([CROSS], window={"from": "09:15", "to": "09:20"}))
    ev, _ = win.evaluation(df)
    out = [win.signal_at(ev, t, "S") for t in ev.signal_bars("long")]
    assert out and all(s.direction == SignalDirection.NO_TRADE and s.reasons == [A.OUTSIDE_WINDOW] for s in out
                       if not A.in_window(win.strategy, s.timestamp))


# --- v1 strategies ---------------------------------------------------------------------------------------------------
def test_a_v1_strategy_translated_answers_as_the_v1_engine():
    v1 = CustomStrategyConfig(name="v1", timeframe="5min", stop_loss_atr_mult=1.0, atr_period=14, long_conditions=[
        {"left": {"type": "indicator", "indicator": "EMA", "period": 9}, "operator": "GT", "right": {"type": "indicator", "indicator": "EMA", "period": 21}},
        {"left": {"type": "indicator", "indicator": "RSI", "period": 14}, "operator": "GT", "right": {"type": "value", "value": 50}}])
    v2 = translate(v1)
    assert v2 is not None and v2.exit.stop.kind == "atr" and v2.exit.target is None
    df = bars(600)
    ev = evaluate(compile_strategy(v2), {"5m": df})
    old = DeclarativeStrategy("custom:9", v1)
    held = np.ones(len(df), dtype=bool)
    for c in v1.long_conditions:
        held &= np.asarray(c.holds_series(df), dtype=bool)
    warm = ev.warm
    assert (ev.raw["entry_long"][warm] == held[warm]).all()
    assert old.min_history()                                    # the v1 engine still loads as before


def test_a_v1_condition_across_two_timeframes_stays_v1():
    v1 = CustomStrategyConfig(name="mixed", timeframe="5min", long_conditions=[
        {"left": {"type": "indicator", "indicator": "EMA", "period": 9, "timeframe": "15min"}, "operator": "GT",
         "right": {"type": "indicator", "indicator": "EMA", "period": 21}}])
    assert translate(v1) is None


# --- the resolver ----------------------------------------------------------------------------------------------------
def test_the_resolver_runs_a_v2_record_on_the_evaluator_and_v1_unchanged():
    import asyncio
    import json
    from types import SimpleNamespace

    from app.custom_strategies import resolver

    class _Session:
        def __init__(self, rows):
            self.rows = rows

        async def get(self, model, key):
            return self.rows.get(key)
    v2 = strategy([CROSS]).model_dump(mode="json", by_alias=True)
    v1 = {"name": "old", "timeframe": "5min", "long_conditions": [CustomStrategyConfig.model_fields and
          {"left": {"type": "indicator", "indicator": "CLOSE"}, "operator": "GT", "right": {"type": "value", "value": 1}}]}
    rows = {1: SimpleNamespace(tenant_id=7, config_json=json.dumps(v2)), 2: SimpleNamespace(tenant_id=7, config_json=json.dumps(v1))}
    user = SimpleNamespace(tenant_id=7)
    got1 = asyncio.run(resolver.load_custom_strategy("custom:1", user, _Session(rows)))
    got2 = asyncio.run(resolver.load_custom_strategy("custom:2", user, _Session(rows)))
    assert isinstance(got1, A.StrategyV2Strategy) and isinstance(got2, DeclarativeStrategy)


# --- ADR-0006 and speed --------------------------------------------------------------------------------------------------
def test_no_signal_or_ai_module_imports_the_order_path():
    """ADR-0006 / CLAUDE.md section 3: the evaluator and the AI modules only produce signals or proposals."""
    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    banned = ("app.execution.signal_execution", "app.execution.router", "app.execution.multileg")
    offenders = []
    for folder in ("strategy_v2", "ai", "chart_analyze", "chart_reader"):
        for path in (root / folder).rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                    [node.module or ""] if isinstance(node, ast.ImportFrom) else []
                if any(name.startswith(b) for name in names for b in banned):
                    offenders.append(f"{path.relative_to(root)}: {names}")
    assert offenders == []


def test_three_thousand_bars_ten_conditions_within_the_budget():
    items = [copy.deepcopy(CROSS)] + [cond(f"r{i}", {"kind": "indicator", "block": "RSI", "params": {"n": 7 + i}}, "gt",
                                           {"kind": "value", "value": 40 + i}) for i in range(8)]
    items.append(cond("h", ema(20), "rising", bars=2, timeframe="15m"))
    c = compile_strategy(strategy(items, "OR"))
    df = bars(3000)
    evaluate(c, {"5m": df.iloc[:200]})                       # import / first-call costs out of the timing
    t0 = time.perf_counter()
    evaluate(c, {"5m": df})
    took = time.perf_counter() - t0
    assert took < 3.0, took                                   # CI box budget; the plan's target is 0.3 s on the server
