"""Item 5 (owner's handover), job 6 follow-up CP6-2: the honest test of the Copilot's draft paper strategy on past data.

The draft (built only from the trader's own words) runs through ATP's existing backtest engine, through one Copilot tool
(`backtest_draft`) that only the chat's rules call - never the agent's model. The test runs only on his ask, only with
every value his and a symbol named; the answer is factual (sample size, costs, drawdown, "too few trades", the earlier
and later parts, the walk-forward windows, the luck-adjusted chance counted as one try) and nothing is saved, started
or placed."""
import asyncio

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import func, select

from app.ai import copilot_chat as chat
from app.ai import draft_backtest as dbt
from app.ai import strategy_builder as sb
from app.ai import tools
from app.ai.output_filter import findings
from app.ai.tools import ToolContext, market, run_tool
from app.core.models import RiskConfig
from app.db.models import AiActionRecord, AiStrategyDraftRecord, CustomStrategyRecord, OrderRecord, StrategyDeploymentRecord, User
from evals import runner
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_copilot_v2_chat import _flag, recorded_tools, v2_on  # noqa: F401 - fixtures
from tests.test_copilot_v2_job6 import FULL, _config
from tests.test_hc2a_agent import NOW, _agent, _Scripted
from tests.test_phase_l_ai import _owner

FIX = runner.copilot_fixtures()
TEST = FULL + " Test it on past data."
BOTH = ("on the 15-minute chart, go long when the 20 EMA crosses above the 50 EMA on NIFTY, go short when the 20 EMA crosses below the 50 "
        "EMA. Stop 1.5 x ATR 14, objective 2R.")
RISK = RiskConfig(capital=200000.0, risk_per_trade_pct=1.0, max_consecutive_losses=3)


def _run(coro):
    return asyncio.run(coro)


def _candles(period=9.0, days=60, amplitude=150.0):
    starts = [t for d in pd.bdate_range("2026-07-01", periods=days) for t in pd.date_range(d + pd.Timedelta("3h45min"), periods=25, freq="15min")]
    i = np.arange(len(starts)).astype(float)
    base = 24000 + amplitude * np.sin(i / period) + 10 * np.sin(i / 2.3) + i * 0.3
    return pd.DataFrame({"open": base - 3, "high": base + 12, "low": base - 12, "close": base, "volume": 1000.0},
                        index=pd.DatetimeIndex(starts, tz="UTC"))


def _answer(question, fixture="default"):
    calls = []
    out = _run(chat.answer(question, runner.recorded_runner(FIX[fixture], calls)))
    return out, calls


# --- the ask -----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("question", [
    TEST, FULL + " and backtest it", "Backtest my idea: " + BOTH, "Test this idea: " + BOTH, BOTH + " How did it do in the past?",
    "How did my idea do in the past? " + BOTH, BOTH + ", then test it on past data for me",
])
def test_the_ask_for_a_test_is_heard_and_never_read_as_part_of_the_idea(question):
    assert sb.test_asked(question)
    d = sb.build(question, "NIFTY", 1.5)
    assert d.unread == [] and d.unsupported == [] and not d.asks()
    base = sb.build(BOTH if "20 EMA crosses below" in question else FULL, "NIFTY", 1.5).as_dict()
    assert {k: v for k, v in d.as_dict().items() if k != "idea"} == {k: v for k, v in base.items() if k != "idea"}


@pytest.mark.parametrize("question", [FULL, BOTH, "go long when price retests VWAP", "Turn my idea into a strategy"])
def test_an_idea_without_the_ask_is_not_tested(question):
    assert not sb.test_asked(question)


# --- the draft as the engine's strategy --------------------------------------------------------------------------------

def test_the_engine_strategy_is_exactly_what_the_review_card_saves_and_never_uses_engine_defaults():
    d = sb.build(FULL, "NIFTY", 1.5)
    cfg = sb.config_of(d)
    assert cfg is not None and cfg.model_dump() == _config(d.as_dict()).model_dump()
    assert (cfg.stop_loss_atr_mult, cfg.atr_period, tuple(cfg.target_rr), cfg.min_rr) == (1.5, 14, (2.0, 2.0), 1.5)
    # any open value: no strategy (the engine's own stop 1.0 / objective 1.5, 2.0 / reward to risk 1.2 are never used instead)
    for idea in ("Make a strategy: go long when the fast EMA crosses the slow EMA", "go long when the 20 EMA crosses above the 50 EMA on NIFTY",
                 "on the 15-minute chart, go long when the 20 EMA crosses above the 50 EMA. Stop 1.5 x ATR 14."):
        assert sb.config_of(sb.build(idea, "NIFTY", 1.5)) is None, idea
    assert sb.config_of(sb.build(FULL, "NIFTY", None)) is None          # no minimum reward to risk of his own: asked, not tested


# --- the honest test on ATP's engine -----------------------------------------------------------------------------------

def test_the_test_reports_costs_drawdown_parts_windows_and_one_try():
    cfg = sb.config_of(sb.build(BOTH, "NIFTY", 1.5))
    out = dbt.honest_test(cfg, _candles(), "NIFTY", "15min", RISK, holdout=None)
    whole = out["whole"]
    assert out["sample"] == "ok" and whole["trades"] >= out["min_trades_for_statistics"] == 30
    assert whole["costs"] > 0 and whole["trades"] == whole["wins"] + whole["losses"]
    assert whole["max_drawdown_pct"] == round(whole["max_drawdown"] / 200000.0 * 100, 2)
    assert out["earlier"]["bars"] + out["later"]["bars"] == out["bars"] and out["later_share_pct"] == 30
    assert out["earlier"]["to"] < out["later"]["from"]                                     # the later part is apart, not overlapping
    assert out["walk_forward"]["windows"] == dbt.WALK_FORWARD_FOLDS and len(out["walk_forward"]["window_trades"]) == dbt.WALK_FORWARD_FOLDS
    assert out["deflated"]["tries"] == 1 and 0 <= out["deflated"]["chance_above_zero_pct"] <= dbt.MAX_CHANCE_PCT
    assert out["tuned_on_this_data"] is False and "trades_list" not in out and "trades" not in out     # statistics only
    assert out["slippage_pct_per_fill"] == 0.02 and "slippage" in out["costs_included"]


def test_the_net_result_is_after_the_engines_own_costs():
    from app.backtest.engine import run_backtest
    from app.strategy_engine.declarative import DeclarativeStrategy
    cfg = sb.config_of(sb.build(BOTH, "NIFTY", 1.5))
    df = _candles()
    direct = run_backtest(DeclarativeStrategy("x", cfg), df, "NIFTY", "15min", RISK)
    out = dbt.honest_test(cfg, df, "NIFTY", "15min", RISK, holdout=None)
    assert out["whole"]["net_pnl"] == direct.net_pnl and out["whole"]["trades"] == direct.total_trades
    assert out["whole"]["costs"] == round(sum(t.charges for t in direct.trades), 2) and all(t.charges > 0 for t in direct.trades)


def test_too_few_trades_judges_no_ratio():
    cfg = sb.config_of(sb.build(BOTH, "NIFTY", 1.5))
    out = dbt.honest_test(cfg, _candles(period=5.0), "NIFTY", "15min", RISK, holdout=None)
    assert out["sample"] == "too_few" and out["whole"]["trades"] < 30 and out["deflated"] is None


def test_the_sealed_holdout_is_never_used():
    cfg = sb.config_of(sb.build(BOTH, "NIFTY", 1.5))
    df = _candles()
    out = dbt.honest_test(cfg, df, "NIFTY", "15min", RISK, holdout="2026-09-01")
    assert out["sealed_holdout_bars"] == int((df.index >= pd.Timestamp("2026-09-01", tz="Asia/Kolkata")).sum()) > 0
    assert out["bars"] == len(df) - out["sealed_holdout_bars"] and out["to"] < "2026-09-01"


def test_a_window_too_short_says_so():
    cfg = sb.config_of(sb.build(BOTH, "NIFTY", 1.5))
    out = dbt.honest_test(cfg, _candles(days=1), "NIFTY", "15min", RISK, holdout=None)
    assert out["sample"] == "no_data" and out["whole"] is None
    ans = chat.Answer("strategy", "Strategy builder")
    chat._test_lines(ans, out)
    assert "There were not enough NIFTY candles on the 15-minute chart" in " ".join(ans.lines())


# --- the tool ----------------------------------------------------------------------------------------------------------

def _call(user_id, name, args):
    async def go():
        async with _session_factory() as session:
            user = await session.get(User, user_id)
            return await run_tool(name, args, ToolContext(session, user.tenant_id, user, NOW))
    return _run(go())


def test_the_tool_is_a_read_tool_for_the_rules_only_and_takes_no_client_candles(monkeypatch):
    reg = tools.registry()
    tool = reg["backtest_draft"]
    assert tool.kind == "read" and tool.agent is False and tool.flag == "ai_copilot"
    assert "backtest_draft" in tools.names("read") and "backtest_draft" not in tools.names("read", agent=True)
    assert tools.specs(["backtest_draft"])[0]["input_schema"]["additionalProperties"] is False
    _, me = _owner("cp6bt-schema@example.com")
    cfg = sb.config_of(sb.build(FULL, "NIFTY", 1.5)).model_dump(mode="json")
    sneaky = _call(me["id"], "backtest_draft", {"config": cfg, "symbol": "NIFTY", "candles": [{"close": 1}]})
    assert not sneaky.ok and "invalid arguments" in sneaky.error
    broken = _call(me["id"], "backtest_draft", {"config": {"name": "x", "stop_loss_atr_mult": -1}, "symbol": "NIFTY"})
    assert not broken.ok and "not a complete strategy" in broken.error
    nobroker = _call(me["id"], "backtest_draft", {"config": cfg, "symbol": "NIFTY"})
    assert not nobroker.ok and "broker session" in nobroker.error.lower() and nobroker.data is None   # never sample data


def test_the_tool_runs_on_server_candles_and_reads_a_three_minute_draft_from_one_minute_bars(monkeypatch):
    seen = []

    async def frame(ctx, symbol, exchange, timeframe, days):
        seen.append((ctx.tenant_id, symbol, exchange, timeframe, days))
        return _candles(), "broker:fake"
    monkeypatch.setattr(market, "_frame", frame)
    _, me = _owner("cp6bt-tool@example.com")
    cfg = sb.config_of(sb.build(BOTH, "NIFTY", 1.5)).model_dump(mode="json")
    res = _call(me["id"], "backtest_draft", {"config": cfg, "symbol": "NIFTY"})
    assert res.ok and res.source == "broker:fake" and seen[0] == (me["tenant_id"], "NIFTY", "NSE", "15min", dbt.TEST_DAYS)
    assert res.data["whole"]["trades"] > 0 and res.as_of == res.data_timestamps["last_bar"]
    _call(me["id"], "backtest_draft", {"config": {**cfg, "timeframe": "3min"}, "symbol": "NIFTY"})
    assert seen[1][3] == "1min"


def test_the_agents_model_is_never_offered_the_tool_and_cannot_call_it(monkeypatch):
    called = []

    async def frame(*a, **k):
        called.append(a)
        return _candles(), "broker:fake"
    monkeypatch.setattr(market, "_frame", frame)
    _, me = _owner("cp6bt-agent@example.com")
    cfg = sb.config_of(sb.build(FULL, "NIFTY", 1.5)).model_dump(mode="json")
    offered = []

    class Watching(_Scripted):
        async def complete_tools(self, system, messages, tools_, *, max_tokens=4000):
            offered.append([t["name"] for t in tools_])
            return await super().complete_tools(system, messages, tools_, max_tokens=max_tokens)
    fake = Watching([("call", [("backtest_draft", {"config": cfg, "symbol": "NIFTY"})]), ("text", '{"text": "I could not test it.", "claims": []}')])
    _run(_agent(me["id"], fake, "Backtest my EMA idea on NIFTY"))
    assert offered and all("backtest_draft" not in names and "get_candles" in names for names in offered)
    assert called == []                                                 # named by the model anyway: refused, never run


# --- the chat answer ---------------------------------------------------------------------------------------------------

def test_on_his_ask_a_complete_draft_is_tested_exactly_as_built_and_every_line_is_grounded():
    out, calls = _answer(TEST)
    assert [c["tool"] for c in calls] == ["get_risk_limits", "backtest_draft"]
    sent = calls[1]["args"]
    assert sent["symbol"] == "NIFTY" and sent["config"] == sb.config_of(sb.build(FULL, "NIFTY", 1.5)).model_dump(mode="json")
    titles = [s["title"] for s in out.sections]
    assert {"Your draft on past data", "Is it enough to judge?", "Did it hold over time?", "How I tested it"} <= set(titles)
    assert not out.diagnostics.get("dropped_lines") and not findings("\n".join(out.lines()))
    assert out.followup is None and out.draft["complete"] is True and out.action["to"] == "/deployments"
    text = "\n".join(out.lines())
    assert "Past results do not say what happens next" in text and "This counts as one try" in text


def test_without_the_ask_the_test_is_offered_as_a_follow_up_that_rebuilds_the_same_draft():
    out, calls = _answer(FULL)
    assert [c["tool"] for c in calls] == ["get_risk_limits"]
    assert out.followup == {"label": "Test it on past data", "question": TEST}
    assert sb.test_asked(out.followup["question"]) and chat.route(out.followup["question"]) == "strategy"
    again, _ = _answer(out.followup["question"])
    assert {k: v for k, v in again.draft.items() if k != "idea"} == {k: v for k, v in out.draft.items() if k != "idea"}
    long_idea = FULL + " " + "x" * (chat.MAX_MESSAGE - len(FULL) - 3)
    assert _answer(long_idea)[0].followup is None                       # the follow-up must fit the question box


def test_an_open_value_or_no_symbol_is_said_and_nothing_is_tested():
    out, calls = _answer("Test my idea: go long when the fast EMA crosses the slow EMA on NIFTY")
    assert [c["tool"] for c in calls] == ["get_risk_limits"] and out.followup is None
    assert chat.TEST_NEEDS_VALUES in out.lines()
    out, calls = _answer("Backtest my idea: on the 15-minute chart, go long when the 20 EMA crosses above the 50 EMA. Stop 1.5 x ATR 14, objective 2R.")
    assert [c["tool"] for c in calls] == ["get_risk_limits"] and chat.TEST_NEEDS_SYMBOL in out.lines()


def test_a_draft_without_parts_of_the_idea_says_the_test_is_not_of_the_whole_idea():
    out, calls = _answer("Backtest my idea: on the 15-minute chart, go long when the 20 EMA crosses above the 50 EMA and the MACD turns up on "
                         "NIFTY. Stop 1.5 x ATR 14, objective 2R.")
    assert [c["tool"] for c in calls] == ["get_risk_limits", "backtest_draft"]
    lines = next(s["lines"] for s in out.sections if s["title"] == "Your draft on past data")
    assert lines[0] == chat.TEST_PARTIAL
    full, _ = _answer(TEST)
    assert chat.TEST_PARTIAL not in full.lines()


def test_too_few_trades_and_a_failed_read_are_said_plainly():
    out, _ = _answer("Backtest my idea: on the 15-minute chart, go long when the 20 EMA crosses above the 50 EMA on SBIN. Stop 1.5 x ATR 14, objective 2R.")
    text = "\n".join(out.lines())
    assert "Too few trades to judge" in text and "Trades won" not in text and "allowing for luck" not in text
    out, calls = _answer("Backtest my idea: on the 15-minute chart, go long when the 20 EMA crosses above the 50 EMA on NIFTY. Stop 1.5 x ATR 14, "
                         "objective 2R, minimum reward to risk 1.5.", "no_data")
    assert calls[-1]["tool"] == "backtest_draft" and any(u.startswith("I can't test it right now") for u in out.unknowns)
    assert not any(s["title"] == "Your draft on past data" for s in out.sections)


def test_the_route_tests_on_his_ask_and_saves_starts_or_places_nothing(v2_on, recorded_tools, monkeypatch):  # noqa: F811 - fixtures
    async def frame(ctx, symbol, exchange, timeframe, days):
        return _candles(), "broker:fake"
    monkeypatch.setattr(market, "_frame", frame)
    headers, me = _owner("cp6bt-route@example.com")

    async def counts():
        async with _session_factory() as session:
            t = me["tenant_id"]
            return tuple([await session.scalar(select(func.count()).select_from(m).where(m.tenant_id == t))
                          for m in (CustomStrategyRecord, StrategyDeploymentRecord, OrderRecord, AiActionRecord, AiStrategyDraftRecord)])
    before = _run(counts())
    offered = client.post("/api/ai/chat", headers=headers, json={"message": FULL}).json()
    assert offered["followup"]["label"] == "Test it on past data" and offered["tool_calls"] == ["get_risk_limits"]
    body = client.post("/api/ai/chat", headers=headers, json={"message": offered["followup"]["question"]}).json()
    assert body["job"] == "strategy" and body["tool_calls"] == ["get_risk_limits", "backtest_draft"] and body["draft"]["mode"] == "PAPER"
    assert any(s["title"] == "Your draft on past data" for s in body["sections"]) and body["followup"] is None
    assert _run(counts()) == before                                  # nothing saved, deployed, ordered or proposed


def test_the_tool_answers_nothing_while_the_copilot_switch_is_off(monkeypatch):
    async def frame(*a, **k):
        raise AssertionError("never fetched")
    monkeypatch.setattr(market, "_frame", frame)
    _, me = _owner("cp6bt-off@example.com")
    cfg = sb.config_of(sb.build(FULL, "NIFTY", 1.5)).model_dump(mode="json")
    _run(_flag("ai_copilot", False))
    try:
        res = _call(me["id"], "backtest_draft", {"config": cfg, "symbol": "NIFTY"})
    finally:
        _run(_flag("ai_copilot", True))
    assert not res.ok and "not enabled" in res.error


def test_the_recorded_results_are_the_engines_shape():
    """The eval fixture's draft tests come from `honest_test` itself (evals/build_copilot_fixtures.py)."""
    keys = set(dbt.honest_test(sb.config_of(sb.build(BOTH, "NIFTY", 1.5)), _candles(), "NIFTY", "15min", RISK, holdout=None)) | {"exchange"}
    for name in ("backtest_draft:NIFTY", "backtest_draft:SBIN"):
        assert set(FIX["default"][name]["data"]) == keys, name
