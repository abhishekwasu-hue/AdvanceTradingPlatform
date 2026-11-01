"""M72 PR-4 (CP-6, findings F12 / M68): the Copilot's options-seller toolkit and the end of the pre-chosen values in the
agent's backtest tool. Every number a model passes to these tools must come from the trader's message or a tool result;
a rule he never saved and never stated is asked, never assumed. No test calls a real provider (scripted models only)."""
import json
from datetime import UTC, date, datetime, timedelta

import pytest

from app.ai import agent, tools
from app.ai.tools import ToolContext, market, options_kit, run_tool
from app.brokers.models import OptionChain, OptionChainRow
from app.db.models import User
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixture
from tests.test_m72_pr1a_safety import _org
from tests.test_m72_pr1c_planner import Scripted, _flag, _run

NOW = datetime.now(UTC)
STRIKES = [24400.0 + 50 * i for i in range(17)]          # 24400 .. 25200, ATM 24800


def _chain():
    rows = [OptionChainRow(strike=s, call_ltp=max(1.0, 24800 + 120 - s) if s <= 24800 else max(1.0, 120 - (s - 24800) / 5), put_ltp=max(1.0, 120 - (24800 - s) / 5) if s <= 24800 else s - 24800 + 120,
                           call_oi=1000.0 + (s == 25100) * 9000, put_oi=1000.0 + (s == 24600) * 8000, call_iv=13.5, put_iv=14.0) for s in STRIKES]
    return OptionChain(underlying="NIFTY", expiry="2026-11-25", underlying_ltp=24812.0, rows=rows)


@pytest.fixture
def chain(monkeypatch):
    async def fake(ctx, underlying, expiry):
        return _chain(), "broker:fake"
    monkeypatch.setattr(market, "read_chain", fake)


def _saved(monkeypatch, values):
    async def fake(ctx):
        return dict(values)
    monkeypatch.setattr(options_kit, "_options_settings", fake)


def _call(user_id, name, args):
    async def go():
        async with _session_factory() as session:
            user = await session.get(User, user_id)
            return await run_tool(name, args, ToolContext(session, user.tenant_id, user, NOW))
    return _run(go())


@pytest.fixture
def member():
    tenant_id, a, _b = _org(f"pr4-{datetime.now().timestamp()}")
    _run(_flag("copilot_agent", False, [tenant_id]))
    yield tenant_id, a
    _run(_flag("copilot_agent", False))


# --- the registry ---------------------------------------------------------------------------------------------------------------

def test_the_toolkit_is_flagged_read_only_and_strict():
    reg = tools.registry()
    for name in options_kit.OPTIONS_TOOLS:
        assert reg[name].kind == "read" and reg[name].flag == "copilot_agent" and reg[name].agent, name
        assert reg[name].schema()["additionalProperties"] is False
    assert {"find_strikes", "calc_option_payoff", "calc_option_what_if", "run_backtest"} <= agent.grounded_arg_tools()


# --- find_strikes -----------------------------------------------------------------------------------------------------------------

def test_strikes_meet_only_the_stated_rule_nearest_first():
    rows = options_kit._rows(_chain(), "CE")
    two, words = options_kit.strikes_matching(rows, 24812.0, "CE", distance_points=None, strikes_away=2, premium_min=None, premium_max=None)
    assert [r["strike"] for r in two] == [24900.0] and words == "2 strikes from ATM"
    far, _ = options_kit.strikes_matching(rows, 24812.0, "CE", distance_points=300, strikes_away=None, premium_min=None, premium_max=None)
    assert far and all(r["strike"] - 24812.0 >= 300 for r in far) and far[0]["strike"] == 25150.0
    band, words = options_kit.strikes_matching(rows, 24812.0, "CE", distance_points=None, strikes_away=None, premium_min=40, premium_max=60)
    assert band and all(40 <= r["ltp"] <= 60 for r in band) and words == "premium 40 to 60"
    puts, _ = options_kit.strikes_matching(options_kit._rows(_chain(), "PE"), 24812.0, "PE", distance_points=200, strikes_away=None,
                                           premium_min=None, premium_max=None)
    assert puts and all(24812.0 - r["strike"] >= 200 for r in puts)


def test_find_strikes_asks_when_no_rule_is_saved_or_stated(member, chain, monkeypatch):
    _tenant, a = member
    _saved(monkeypatch, {})
    res = _call(a, "find_strikes", {"underlying": "NIFTY", "right": "CE"})
    assert not res.ok and res.error == options_kit.NOT_SET_RULE
    _saved(monkeypatch, {"strike_rule": "OTM"})                     # OTM without its distance: still asked
    assert not _call(a, "find_strikes", {"underlying": "NIFTY", "right": "CE"}).ok
    _saved(monkeypatch, {"strike_rule": "OTM", "strike_offset": 3})
    saved = _call(a, "find_strikes", {"underlying": "NIFTY", "right": "PE"})
    assert saved.ok and saved.data["rule_from"] == "his Trade settings" and [m["strike"] for m in saved.data["matches"]] == [24650.0]
    stated = _call(a, "find_strikes", {"underlying": "NIFTY", "right": "PE", "distance_points": 200})
    assert stated.ok and stated.data["rule_from"] == "his message" and stated.data["highest_oi_strikes"][0]["strike"] == 24600.0
    assert "recommend" not in json.dumps(stated.data).lower().replace("not a recommendation", "")


# --- payoff and what-if -----------------------------------------------------------------------------------------------------------

STRANGLE = [{"right": "PE", "side": "SELL", "strike": 24600, "premium": 50, "quantity": 75},
            {"right": "CE", "side": "SELL", "strike": 25100, "premium": 40, "quantity": 75}]


def test_payoff_is_code_arithmetic_on_the_stated_legs(member):
    _tenant, a = member
    res = _call(a, "calc_option_payoff", {"legs": STRANGLE, "at_prices": [24812, 25300]})
    assert res.ok and res.data["net_premium"] == 6750 and res.data["net_premium_means"] == "received"
    assert res.data["max_profit"] == 6750 and res.data["max_loss"] == "unlimited"
    assert res.data["breakevens"] == [24510.0, 25190.0]
    assert res.data["at_prices"] == [{"price": 24812, "pnl": 6750.0}, {"price": 25300, "pnl": -8250.0}]
    spread = _call(a, "calc_option_payoff", {"legs": [{"right": "CE", "side": "SELL", "strike": 25100, "premium": 40, "quantity": 75},
                                                     {"right": "CE", "side": "BUY", "strike": 25200, "premium": 15, "quantity": 75}]})
    assert spread.data["max_loss"] == -5625.0 and spread.data["max_profit"] == 1875.0


def test_what_if_needs_his_own_rate(member, monkeypatch):
    _tenant, a = member
    legs = [{**leg, "iv_pct": 14, "expiry": (date.today() + timedelta(days=10)).isoformat()} for leg in STRANGLE]
    _saved(monkeypatch, {})
    none = _call(a, "calc_option_what_if", {"legs": legs, "price": 24812})
    assert not none.ok and "risk-free rate is Not set" in none.error
    _saved(monkeypatch, {"risk_free_rate_pct": 6.5})
    res = _call(a, "calc_option_what_if", {"legs": legs, "price": 24812, "price_change": 200, "days_forward": 3})
    assert res.ok and res.data["risk_free_rate_pct"] == 6.5 and res.data["scenario"]["price"] == 25012
    assert res.data["pnl_change"] == round(res.data["pnl_in_scenario"] - res.data["pnl_now"], 2)
    assert set(res.data["net_greeks_now"]) == {"delta", "gamma", "theta", "vega", "rho"}


# --- the planner: no pre-chosen value enters through a model ----------------------------------------------------------------------

def _agent_run(a, model, question):
    async def go():
        async with _session_factory() as session:
            user = await session.get(User, a)
            return await agent.run_agent(model, ToolContext(session, user.tenant_id, user, NOW), question)
    return _run(go())


def test_a_distance_or_window_the_trader_did_not_state_is_refused(member, chain, monkeypatch):
    _tenant, a = member
    _saved(monkeypatch, {})
    made_up = Scripted([{"calls": [{"tool": "find_strikes", "args": {"underlying": "NIFTY", "right": "CE", "distance_points": 300}}]},
                        {"text": json.dumps({"text": "Not available.", "claims": []})}])
    ans = _agent_run(a, made_up, "Which NIFTY calls can I write?")
    assert ans.tool_calls[0]["ok"] is False and "calc refused" in ans.tool_calls[0]["error"]
    stated = Scripted([{"calls": [{"tool": "find_strikes", "args": {"underlying": "NIFTY", "right": "CE", "distance_points": 300}}]},
                       {"text": json.dumps({"text": "Calls 300 points away are listed.", "claims": []})}])
    ans = _agent_run(a, stated, "Which NIFTY calls are at least 300 points away?")
    assert ans.tool_calls[0]["ok"] is True
    window = Scripted([{"calls": [{"tool": "run_backtest", "args": {"strategy_id": "ema_rsi_scalper_1m", "symbol": "NIFTY", "timeframe": "1min", "days": 20}}]},
                       {"text": json.dumps({"text": "Which window?", "claims": []})}])
    ans = _agent_run(a, window, "Backtest the scalper on NIFTY")
    assert "calc refused" in ans.tool_calls[0]["error"]


def test_the_backtest_tool_has_no_pre_chosen_window_or_capital():
    from app.ai.tools.market import _BacktestArgs
    fields = _BacktestArgs.model_fields
    assert fields["timeframe"].is_required() and fields["days"].is_required()
    import inspect
    src = inspect.getsource(market.run_backtest)
    assert "PAPER_STARTING_CAPITAL" not in src and "user_risk_config" in src
