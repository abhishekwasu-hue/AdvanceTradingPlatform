"""DEF-0 PR 2 (owner order, 28 Oct): no hidden exit and no gate the trader did not set.

* D3: a structure's target / stop and a single option's premium stop exist only when the trader set them (no built-in
  50 / 100 / 30 / 50 %); the basket starts a sold leg without a stop; the interview adds no break-even or time exit;
* D4: the experience-level gates apply only to a user who turned on "Limit me by my experience level";
* D7: a sold option or a future needs a stop-loss only under the trader's own setting; a short with no stop (0) is never
  stopped out by a made-up level and gets no broker stop;
* exits are never blocked (ADR-0004): every exit path keeps working.
"""
import ast
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.ai import interview as iv
from app.execution.contract_execution import build_order_plan
from app.instruments.contracts import ContractRules
from app.paper import fno_ticket
from app.trading.exit_logic import check_contract_exit, determine_exit_price
from tests.test_contract_execution import BUY, WRITE, _load_master, _resolve, _signal
from tests.test_fno1_chain_ticket import _body, _contract, _expiry, _preview
from tests.test_trading_worker import _tenant
from tests.test_z3_approval_entries import _price
from tests.test_z3b_paper_account import _paper_flag
from tests.test_z5_p1d_risk_by_default import _flag

BACKEND = Path(__file__).resolve().parents[1]


# --- D3: structures ---------------------------------------------------------------------------------------------------

def test_no_numeric_default_exit_in_spreads_or_contracts():
    for rel in ("app/instruments/spreads.py", "app/instruments/contracts.py"):
        tree = ast.parse((BACKEND / rel).read_text())
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id.startswith("DEFAULT_") for t in node.targets):
                assert not isinstance(node.value, (ast.Constant, ast.Dict)), f"{rel}: {ast.unparse(node)}"


def test_a_structure_without_the_traders_exits_has_none():
    from app.instruments.spreads import StructureMetrics
    m = StructureMetrics(net_credit=40.0, max_profit=40.0, max_loss=60.0, breakevens=[24460.0], target_value=None, stop_value=None,
                         short_strikes={"PE": 24500.0}, legs=[], risk_per_unit=60.0)
    assert m.exit_summary() == "no target or stop set"
    from app.trading.position_monitor import structure_exit_reason
    meta = m.as_dict()
    assert structure_exit_reason({**meta, "short_strikes": {}}, 500.0, None) is None             # no level, no exit
    set_ = StructureMetrics(net_credit=40.0, max_profit=40.0, max_loss=60.0, breakevens=[], target_value=20.0, stop_value=80.0,
                            short_strikes={}, legs=[], risk_per_unit=60.0)
    assert set_.exit_summary() == "target at value 20; stop at value 80"
    assert structure_exit_reason(set_.as_dict(), 80.0, None).startswith("Spread stop")


def test_a_payoff_structure_with_only_a_target_never_stops_on_a_made_up_level():
    from app.trading.position_monitor import structure_exit_reason
    meta = {"payoff": True, "net_credit": 10.0, "pnl_target": 5.0, "pnl_stop": None, "underlying_exits": {}}
    assert structure_exit_reason(meta, 500.0, None) is None                                       # deep loss: no stop set
    assert structure_exit_reason(meta, 4.0, None).startswith("Structure target")


# --- D3: a single option's premium stop -------------------------------------------------------------------------------

def test_an_option_without_a_premium_stop_gets_no_premium_exit():
    _load_master()
    written = _resolve(WRITE)
    plan = build_order_plan(_signal(), written, ContractRules(kind=written.kind, position=written.position), 100.0)
    assert plan.order_signal.stop_loss == 0.0 and "no premium stop set" in plan.order_signal.reasons[-1]
    bought = _resolve(BUY)
    plan = build_order_plan(_signal(), bought, ContractRules(kind=bought.kind, position=bought.position), 100.0)
    assert plan.order_signal.stop_loss == 0.0
    with_stop = build_order_plan(_signal(), written, ContractRules(kind=written.kind, position=written.position, premium_stop_pct=40.0), 100.0)
    assert with_stop.order_signal.stop_loss == 140.0


def test_a_short_with_no_stop_is_never_stopped_out():
    assert determine_exit_price("SHORT", 0.0, None, None, 1.0, 10_000.0) is None
    assert determine_exit_price("SHORT", 120.0, None, None, 100.0, 125.0)[0] == "Stop Loss"
    assert determine_exit_price("LONG", 0.0, None, None, 0.05, 50.0) is None
    trade = SimpleNamespace(instrument_kind="OPTION", direction="SHORT", stop_loss=0.0, underlying_direction=None)
    assert check_contract_exit(trade, 900.0, None) is None
    trade.stop_loss = 150.0
    assert check_contract_exit(trade, 151.0, None)[0] == "Premium ceiling"


# --- D3: the basket and the interview ---------------------------------------------------------------------------------

def test_the_interview_adds_no_exit_the_trader_did_not_set():
    assert iv.default_exit_rules(iv.InterviewAnswers(style="intraday")) == {"break_even_at_r": None, "time_exit_at": None}


# --- D7: the stop-loss on a sold option or a future -------------------------------------------------------------------

def test_a_stop_is_asked_only_under_the_traders_own_setting():
    ask = fno_ticket.OrderAsk(underlying="NIFTY", expiry=_expiry(), strike=22700.0, right="CE", side="SELL", lots=1, order_type="MARKET",
                              product="NRML", price=None, trigger_price=None, stop_loss=None)
    assert fno_ticket.ask_errors(ask, 0.05) == []
    assert any("Your setting asks for a stop-loss" in e for e in fno_ticket.ask_errors(ask, 0.05, stop_required=True))
    assert fno_ticket.position_stop("SELL", None, 0.05) == 0.0
    assert fno_ticket.max_loss("SELL", 131.5, None, 65)["value"] is None


@pytest.fixture
def fresh(monkeypatch):
    t = _tenant(f"def0b-{uuid.uuid4().hex[:6]}@example.com")
    _flag("ui_v2", t["tenant_id"])
    _paper_flag(t["tenant_id"])
    expiry = _expiry()
    _contract(expiry, 22700.0)
    _price(monkeypatch, 131.5)
    return t, expiry


def test_a_sold_option_without_a_stop_can_be_placed_and_says_its_loss_has_no_limit(fresh):
    t, expiry = fresh
    view = _preview(t, _body(expiry, 22700.0, side="SELL", product="NRML"))
    assert view["errors"] == [] and view["refusals"] == [] and view["can_place"] is True, (view["errors"], view["refusals"])
    assert view["max_loss"]["value"] is None and "no fixed limit" in view["max_loss"]["basis"]


def test_his_own_setting_asks_for_the_stop(fresh):
    from tests.test_auth_api import client
    t, expiry = fresh
    assert client.put("/api/me/prefs/trading_defaults", headers=t["headers"], json={"stop_required_on_sold": True}).status_code == 200
    view = _preview(t, _body(expiry, 22700.0, side="SELL", product="NRML"))
    assert not view["can_place"] and any("Your setting asks for a stop-loss" in e for e in view["errors"])


# --- D4: experience levels -------------------------------------------------------------------------------------------

def test_experience_gates_apply_only_to_a_user_who_turned_them_on(fresh):
    from app.paper import experience
    from tests.test_auth_api import client
    t, expiry = fresh
    _flag(experience.FLAG, t["tenant_id"])                                                       # the organisation's flag on
    view = _preview(t, _body(expiry, 22700.0, side="SELL", product="NRML", stop_loss=150.0))
    assert view["refusals"] == [] and view["can_place"] is True                                  # not opted in: no gate
    assert client.put("/api/me/prefs/trading_defaults", headers=t["headers"], json={"limit_by_experience": True}).status_code == 200
    view = _preview(t, _body(expiry, 22700.0, side="SELL", product="NRML", stop_loss=150.0))
    assert not view["can_place"] and any("experience level" in r for r in view["refusals"])
