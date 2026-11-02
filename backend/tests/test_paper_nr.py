"""PAPER-NR (owner, 1 Nov; corrected scope): in PAPER only the MONEY checks go - a short practice balance, paper capital
or margin never refuses or cuts a PAPER entry; each becomes a margin vs free balance information line and the balance may
go below zero. His own limits refuse in PAPER exactly as in LIVE (PAPER = LIVE), platform safety (kill switch) still
refuses, a PAPER risk-limit block never engages a STRATEGY / TENANT kill switch, LIVE is unchanged and no exit path
reaches the chokepoint (app/risk_engine/paper_nr.py)."""
import ast
import asyncio
import pathlib

from app.core.models import RiskConfig
from app.db.models import User
from app.execution import multileg
from app.paper import fno_basket
from app.risk_engine import paper_nr
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_contract_rules import _load_master
from tests.test_deployments_api import _auth
from tests.test_live_execution import _signal
from tests.test_m55_t1_hold_for_days import _execute
from tests.test_trading_worker import _tenant

APP = pathlib.Path(__file__).resolve().parents[1] / "app"


def _run(coro):
    return asyncio.run(coro)


def _me(headers):
    return client.get("/api/auth/me", headers=headers).json()


def _paper_entry(headers, **cfg):
    me = _me(headers)

    async def go():
        from app.execution.signal_execution import execute_signal_for_user
        async with _session_factory() as session:
            user = await session.get(User, me["id"])
            return await execute_signal_for_user(session, user, mode="PAPER", strategy_id="ema_rsi_scalper_1m", signal=_signal(),
                                                 risk_config=RiskConfig(capital=1_000_000, **cfg), holding="INTRADAY", order_style="MARKET")
    return _run(go())


# --- the chokepoint ----------------------------------------------------------------------------------------------------

def test_only_paper_is_advisory_and_only_live_engages_a_kill_switch():
    assert paper_nr.advisory("PAPER") and paper_nr.advisory("paper")
    assert not paper_nr.advisory("LIVE") and not paper_nr.advisory(None)
    assert not paper_nr.engages_kill_switch("PAPER") and paper_nr.engages_kill_switch("LIVE")


def test_margin_line_says_needed_free_after_and_below_zero():
    assert paper_nr.margin_info(500.0, 2_000.0) == "Practice margin: needs ₹500; ₹2,000 free, ₹1,500 after"
    short = paper_nr.margin_info(5_000.0, 1_000.0)
    assert short.endswith("(below zero - practice only)") and "₹-4,000 after" in short
    assert "not estimated" in paper_nr.margin_info(None, 100.0)
    assert paper_nr.margin_info(None, -10.0).endswith("(below zero - practice only)")


# --- money checks: PAPER places with the information line; non-PAPER keeps the refusal ---------------------------------

def test_paper_hold_margin_short_balance_places_with_the_info_line(monkeypatch):
    _load_master()
    t = _tenant("pnr-hold@example.com", with_credentials=False)

    async def small(session, user):
        return 1_000.0
    monkeypatch.setattr(fno_basket, "practice_balance", small)
    placed = _execute(t, holding="SWING", key="pnr-hold-1")
    assert placed.executed, placed.reasons
    line = next(r for r in placed.reasons if r.startswith("Practice margin: needs"))
    assert "₹1,000 free" in line and line.endswith("(below zero - practice only)")


def test_without_the_paper_exception_the_hold_margin_still_refuses(monkeypatch):
    """The refusal itself is kept (LIVE-shaped callers and any non-PAPER mode): only `advisory` turns it into a line."""
    _load_master()
    t = _tenant("pnr-hold-live@example.com", with_credentials=False)

    async def small(session, user):
        return 1_000.0
    monkeypatch.setattr(fno_basket, "practice_balance", small)
    monkeypatch.setattr(multileg.paper_nr, "advisory", lambda mode: False)
    refused = _execute(t, holding="SWING", key="pnr-hold-2")
    assert not refused.executed and any("not enough margin to hold this spread for days" in r for r in refused.reasons)


def test_a_placed_paper_entry_carries_the_margin_vs_balance_line():
    result, order = _paper_entry(_auth("pnr-line@example.com"), risk_per_trade_pct=0.1)
    assert result.executed, result.reasons
    assert any(r.startswith("Practice margin: needs ₹") and "free" in r for r in result.reasons)


# --- his own limits and platform safety still refuse in PAPER ----------------------------------------------------------

def test_his_own_limits_still_refuse_in_paper():
    headers = _auth("pnr-own@example.com")
    result, order = _paper_entry(headers, risk_per_trade_pct=0.1, min_risk_reward=5.0)       # signal pays 2:1
    assert not result.executed and order.status == "REJECTED"
    assert any("reward" in r.lower() for r in result.reasons)
    assert not any(r.startswith("Practice margin") for r in result.reasons)
    result, order = _paper_entry(headers, risk_per_trade_pct=0.1, max_open_positions=0)
    assert not result.executed and order.status == "REJECTED"


def test_the_kill_switch_still_refuses_a_paper_entry():
    headers = _auth("pnr-kill@example.com")
    assert client.post("/api/kill-switch/tenant/engage", headers=headers, json={"reason": "pnr proof"}).status_code == 200
    try:
        result, order = _paper_entry(headers, risk_per_trade_pct=0.1)
        assert not result.executed and order.status == "REJECTED" and any("kill switch" in r.lower() for r in result.reasons)
    finally:
        client.post("/api/kill-switch/tenant/disengage", headers=headers)


# --- exits never reach the chokepoint (ADR-0004) -----------------------------------------------------------------------

def _functions_calling_paper_nr():
    found = set()
    for path in APP.rglob("*.py"):
        if path.name == "paper_nr.py":
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if any(isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "paper_nr"
                       for n in ast.walk(node)):
                    found.add((path.relative_to(APP).as_posix(), node.name))
    return found


def test_call_graph_only_entry_functions_reach_paper_nr_and_no_exit_does():
    callers = _functions_calling_paper_nr()
    assert callers == {
        ("execution/multileg.py", "execute_structure"), ("execution/multileg.py", "_hold_margin_refusal"),
        ("execution/signal_execution.py", "execute_signal_for_user"), ("execution/signal_execution.py", "_practice_margin_line"),
        ("risk_engine/hierarchy.py", "evaluate"), ("accounts/paper_fanout.py", "place"),
    }, callers
    for _, name in callers:
        assert not any(word in name.lower() for word in ("exit", "square", "close", "settle")), name
