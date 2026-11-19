"""SFC P2 (owner APPROVED 19 Nov, docs/plans/strategy-first-chart-PLAN.md): the plot's PA parts, OR groups end to end,
the open trade's position, and the forming bar's pending hint.

Checks: a fixed number his condition compares a series with is a flat line in that series' pane (once); an OR group
gives at least the signals of the same conditions under AND (SPEC T2, server side); a breakout condition's box comes from
the screener's own evidence builder; the open trade carries his own stop / target and none is made up (OQ-10); the
pending hint reads the forming bar apart from the closed bars (it never changes a closed-bar marker) and only a bar newer
than the last closed one counts. Made-up bars only."""
from __future__ import annotations

import copy

import pandas as pd

from app.core.models import RiskConfig
from app.quality.lookahead import synthetic_bars
from app.strategy_v2.adapter import StrategyV2Strategy
from app.strategy_v2.plot import levels_of, pending_of, plot, position_of
from app.strategy_v2.schema import StrategyV2
from tests.test_sfc_p1 import RAW

RISK = RiskConfig(capital=1_000_000, risk_per_trade_pct=0.5)


def _strategy(raw=RAW) -> StrategyV2Strategy:
    return StrategyV2Strategy("custom:1", StrategyV2.model_validate(raw))


def _bars(n=900, seed=5) -> pd.DataFrame:
    return synthetic_bars(n, tf="5m", seed=seed)


def test_a_fixed_number_is_a_flat_line_in_its_series_pane():
    assert levels_of(_strategy()) == [{"id": "l1", "value": 45.0, "pane": "own:RSI", "label": "RSI(14) 45"}]
    out = plot(_strategy(), _bars(), "S", RISK)
    assert {s["pane"] for s in out["series"]} >= {lv["pane"] for lv in out["levels"]}     # the same pane as its series


def test_t2_an_or_group_signals_at_least_as_often_as_and():
    ored = copy.deepcopy(RAW)
    ored["entry"]["long"]["op"] = "OR"
    df = _bars(1500, seed=9)
    count = lambda raw: sum(m["kind"] in ("entry", "skipped") and m["side"] == "long" for m in plot(_strategy(raw), df, "S", RISK)["markers"])  # noqa: E731
    anded, ord_ = count(RAW), count(ored)
    assert ord_ >= anded and ord_ > 0


def test_the_open_trade_has_his_own_stop_and_target_and_none_is_made_up():
    class T:
        def __init__(self, **kw): self.__dict__.update(kw)
    from app.core.enums import SignalDirection
    t0 = pd.Timestamp("2031-06-10T04:00:00Z")
    open_ = T(entry_time=t0, entry_price=100.0, direction=SignalDirection.LONG, stop_loss=98.0, target1=None, exit_time=None, exit_reason=None)
    assert position_of([open_]) == {"side": "long", "entry_time": t0.isoformat(), "entry": 100.0, "stop": 98.0, "target": None}
    shut = T(entry_time=t0, entry_price=100.0, direction=SignalDirection.SHORT, stop_loss=102.0, target1=96.0, exit_time=t0, exit_reason="Stop Loss")
    assert position_of([shut]) is None


def test_the_pending_hint_reads_only_a_newer_forming_bar_and_never_moves_a_closed_marker():
    strat = _strategy()
    df = _bars()
    before = plot(strat, df, "S", RISK)["markers"]
    last = df.index[-1]
    assert pending_of(strat, df, "S", {"t": last.isoformat(), "o": 1, "h": 1, "l": 1, "c": 1}) is None   # not forming: nothing
    found = None
    for k in range(80, 400):                         # a made-up next bar for each history cut: some turn a rule true
        cut = df.iloc[:-k]
        nxt = df.iloc[-k]
        bar = {"t": df.index[-k].isoformat(), "o": nxt["open"], "h": nxt["high"], "l": nxt["low"], "c": nxt["close"], "v": nxt["volume"]}
        got = pending_of(strat, cut, "S", bar)
        ev, _ = strat.evaluation(df.iloc[: len(df) - k + 1], "S")
        closed_side = "long" if ev.signal_long[-1] else "short" if ev.signal_short[-1] else None
        assert (got or {}).get("side") == closed_side        # the same answer the bar gives once it closes
        if got:
            found = got
    assert found is not None
    after = plot(strat, df, "S", RISK, forming={"t": (last + pd.Timedelta(minutes=5)).isoformat(), "o": 1, "h": 1, "l": 1, "c": 1})
    assert after["markers"] == before                        # the forming bar never moves a closed-bar marker


def test_a_breakout_condition_draws_its_box_from_the_evidence_builder():
    raw = copy.deepcopy(RAW)
    raw["entry"] = {"sides": "long", "long": {"op": "AND", "items": [
        {"id": "b1", "left": {"kind": "pa", "block": "RangeBreakout", "params": {"side": "up"}}, "cmp": "is_true"}]}}
    out = plot(_strategy(raw), _bars(1200, seed=3), "S", RISK)
    assert [p["kind"] for p in out["pa"]] == ["box"] and out["pa"][0]["level"] is not None
    assert plot(_strategy(), _bars(), "S", RISK)["pa"] == []          # indicator-only: no PA shape is made up
    for p in out["pa"]:
        assert p["kind"] in ("band", "box") and p["low"] <= p["high"] and p["from"] <= p["to"]


# --- P3: the marker's "why" -------------------------------------------------------------------------------------------
def test_t7_a_signal_marker_lists_its_own_rules_conditions_in_words_with_values():
    out = plot(_strategy(), _bars(), "S", RISK)
    longs = [m for m in out["markers"] if m["kind"] in ("entry", "skipped") and m["side"] == "long"]
    rows = out["why"][longs[0]["id"]]
    assert [r["id"] for r in rows] == ["c1", "c2"] and all(r["held"] for r in rows)       # AND: every one held
    assert rows[0]["text"] == "EMA(close, 9) crossed above EMA(close, 21)" and rows[1]["text"] == "RSI(14) > 45"
    assert all(v is not None for r in rows for v in r["values"].values())


def test_t7_a_condition_that_did_not_hold_in_an_or_group_is_marked_optional():
    ored = copy.deepcopy(RAW)
    ored["entry"]["long"]["op"] = "OR"
    out = plot(_strategy(ored), _bars(1500, seed=9), "S", RISK)
    rows = [out["why"][m["id"]] for m in out["markers"] if m["kind"] in ("entry", "skipped") and m["side"] == "long"]
    assert all(any(r["held"] for r in rs) for rs in rows)                                     # the bar met the group
    assert all(r["optional"] == (not r["held"]) for rs in rows for r in rs)
    assert any(r["optional"] for rs in rows for r in rs)


# --- P4: the quick stats ----------------------------------------------------------------------------------------------
def test_t8_the_trade_count_is_the_number_of_entry_markers_and_the_numbers_add_up():
    out = plot(_strategy(), _bars(), "S", RISK)
    st = out["stats"]
    assert st["trades"] == sum(m["kind"] == "entry" for m in out["markers"]) == len(out["trades"])        # T8
    closed = [t for t in out["trades"] if t["exit"] is not None]
    assert st["closed"] == len(closed) and st["open"] == st["trades"] - st["closed"]
    pts = [(t["exit"] - t["entry"]) * (1 if t["side"] == "long" else -1) for t in closed]
    assert abs(st["points"] - sum(pts)) < 0.05 and st["wins"] == sum(p > 0 for p in pts)
    assert st["max_drawdown"] <= 0


def test_stats_by_hand():
    from app.core.enums import SignalDirection
    from app.strategy_v2.plot import stats_of

    class T:
        def __init__(self, **kw): self.__dict__.update(kw)
    t0 = pd.Timestamp("2031-06-10T04:00:00Z")
    L, S = SignalDirection.LONG, SignalDirection.SHORT
    trades = [T(direction=L, entry_price=100, exit_price=110, stop_loss=95, exit_time=t0, exit_reason="Target 1", pnl=1000, entry_time=t0),
              T(direction=S, entry_price=200, exit_price=206, stop_loss=203, exit_time=t0, exit_reason="Stop Loss", pnl=-600, entry_time=t0),
              T(direction=L, entry_price=50, exit_price=46, stop_loss=None, exit_time=t0, exit_reason="Exit rule", pnl=-400, entry_time=t0),
              T(direction=L, entry_price=60, exit_price=None, stop_loss=58, exit_time=None, exit_reason=None, pnl=None, entry_time=t0)]
    assert stats_of(trades) == {"trades": 4, "closed": 3, "open": 1, "wins": 1, "win_rate": 33.3333, "points": 0.0,
                                "max_drawdown": -10.0, "avg_r": 0.0, "pnl": 0.0}
    assert stats_of([])["win_rate"] is None and stats_of([])["avg_r"] is None
