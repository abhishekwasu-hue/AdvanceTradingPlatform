"""TT-2 (owner, 24 Oct; answers FNO-1-4): with approval-only mode on, an order the user places and confirms himself is
his approval. The origin "manual" is decided by the server from the route - the F&O ticket and basket routes only - and
never read from a request. Bot and signal entries (the worker, deployments, webhooks, the console) keep needing an
approved proposal. Every other check stays: risk, the kill switch, the Phase 0 PAPER lock, idempotency."""
import ast
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.core.enums import SignalDirection, SignalGrade
from app.core.models import Signal
from app.db.models import User
from app.execution import approval
from app.execution.signal_execution import execute_signal_for_user
from app.paper import fno_basket, fno_ticket
from app.platform import phase0_lock
from tests import test_fno1_chain_ticket as fno1
from tests import test_fno2_basket as fno2
from tests.test_auth_api import _session_factory, client
from tests.test_trading_worker import OPEN_NOW, _deploy, _FakeBroker, _force_signal, _signal, _tenant, _trades, _worker
from tests.test_z3_approval_entries import _actions, _email, _flag_for, _orders, _run, _sig, _user

BACKEND = Path(__file__).resolve().parents[1]
setup = fno1.setup
condor = fno2.condor


def test_a_manual_ticket_order_passes_approval_only_mode(setup):
    t, expiry, strike, symbol = setup
    _flag_for(t["tenant_id"])
    view = fno1._preview(t, fno1._body(expiry, strike, stop_loss=70.0))
    assert view["can_place"] is True and not any("approval" in r.lower() for r in view["refusals"]), view["refusals"]
    ref = uuid.uuid4().hex
    placed = fno1._place(t, fno1._body(expiry, strike, stop_loss=70.0, client_ref=ref))
    assert placed["status"] == "PLACED" and placed["mode"] == "PAPER", placed
    again = fno1._place(t, fno1._body(expiry, strike, stop_loss=70.0, client_ref=ref))     # idempotency still holds
    assert again["order_id"] == placed["order_id"] and len(fno1._orders(t["tenant_id"])) == 1
    assert _actions(t["tenant_id"], "ENTRY") == []                                         # no proposal to himself


def test_the_risk_check_and_the_kill_switch_still_refuse_a_manual_order(setup):
    t, expiry, strike, _ = setup
    _flag_for(t["tenant_id"])
    too_big = fno1._preview(t, fno1._body(expiry, strike, lots=3))
    assert not too_big["can_place"] and any("allows 1 lot(s)" in r for r in too_big["refusals"])
    assert client.post("/api/kill-switch/tenant/engage", headers=t["headers"], json={"reason": "tt2 test"}).status_code == 200
    try:
        refused = fno1._place(t, fno1._body(expiry, strike, stop_loss=70.0))
        assert refused["status"] == "REFUSED" and fno1._orders(t["tenant_id"]) == []
    finally:
        assert client.post("/api/kill-switch/tenant/disengage", headers=t["headers"]).status_code == 200


def test_a_basket_order_passes_approval_only_mode(condor):
    expiry, _, legs = condor
    t = fno2._org_tenant("tt2")
    _flag_for(t["tenant_id"])
    body = {"legs": fno2._condor_legs(expiry, legs), "product": "NRML"}
    preview = fno2._post(t, "preview", body)
    assert preview["can_execute"] is True, preview
    out = fno2._post(t, "execute", {**body, "basket_ref": uuid.uuid4().hex})
    assert out["status"] == "PLACED" and out["placed"] == 4, out
    assert all(o.mode == "PAPER" for o in fno2._orders(t["tenant_id"])) and _actions(t["tenant_id"], "ENTRY") == []


def test_a_deployment_entry_is_still_held_for_approval(monkeypatch):
    t = _tenant(_email("tt2-bot"))
    _flag_for(t["tenant_id"])
    dep_id = _deploy(t)
    _force_signal(monkeypatch, _signal)
    worker = _worker(monkeypatch, _FakeBroker(ltp=100.1))
    _run(worker.run_cycle(now=OPEN_NOW))
    assert _trades(t["tenant_id"]) == [] and _orders(t["tenant_id"]) == []
    entries = _actions(t["tenant_id"], "ENTRY")
    assert len(entries) == 1 and entries[0].status == "PROPOSED" and entries[0].deployment_id == dep_id

    # A deployment's entry calling itself "manual" (even with the manual journal source) is still refused.
    user = _user(t["user_id"])

    async def go():
        async with _session_factory() as session:
            result, order = await execute_signal_for_user(session, user, mode="PAPER", strategy_id="ema_rsi_scalper_1m", signal=_sig(),
                                                          idempotency_key=f"tt2:{uuid.uuid4().hex}", deployment_id=dep_id,
                                                          origin=approval.ORIGIN_MANUAL, journal_source="MANUAL")
            return result.executed, order.status, json.loads(order.reasons_json)
    executed, status, reasons = _run(go())
    assert not executed and status == "REJECTED" and "Approval-only mode" in reasons[0]


def test_the_gate_takes_manual_only_without_a_deployment():
    t = _tenant(_email("tt2-gate"))
    _flag_for(t["tenant_id"])

    async def go(**kw):
        async with _session_factory() as session:
            return await approval.gate_refusal(session, t["tenant_id"], approval_action_id=None, **kw)
    assert _run(go(deployment_id=None, origin=approval.ORIGIN_MANUAL)) is None
    assert "Approval-only mode" in _run(go(deployment_id=None, origin=approval.ORIGIN_AUTOMATIC))
    assert "Approval-only mode" in _run(go(deployment_id=None, origin="MANUAL"))         # exactly the server's constant


def test_a_forged_manual_origin_is_ignored(monkeypatch, setup):
    t, expiry, strike, _ = setup
    _flag_for(t["tenant_id"])
    # The ticket's body forbids extra fields: the browser cannot send an origin at all.
    out = client.post("/api/paper/fno/order", headers=t["headers"], json={**fno1._body(expiry, strike, stop_loss=70.0), "origin": "manual"})
    assert out.status_code == 422 and fno1._orders(t["tenant_id"]) == []
    # A non-manual path (the console's paper-execute) given "origin": "manual" still raises a proposal and places nothing.
    from app.strategy_engine.registry import registry
    from tests.test_trading_api import _fake_long_signal, _sample_candles_payload
    monkeypatch.setattr(registry.get("ema_rsi_scalper_1m"), "analyze", lambda data, symbol: _fake_long_signal())
    body = {"symbol": "TESTSYM", "candles": {"1min": _sample_candles_payload()}, "origin": "manual"}
    console = client.post("/api/strategies/ema_rsi_scalper_1m/paper-execute", headers=t["headers"], json=body)
    assert console.status_code == 200, console.text
    assert console.json()["executed"] is False and "entry proposal #" in console.json()["reasons"][0]
    assert fno1._orders(t["tenant_id"]) == []
    # The ticket called by any other code (no origin given) is automatic and refused, in plain words.
    user = _user(t["user_id"])

    async def place():
        async with _session_factory() as session:
            from app.ai.worker_bridge import price_lookup_for_user
            lookup = await price_lookup_for_user(session, user)
            return await fno_ticket.place(session, user, fno_ticket.OrderAsk(**{**fno1._body(expiry, strike, stop_loss=70.0),
                                                                                "expiry": expiry}), price_lookup=lookup)
    refused = _run(place())
    assert refused["status"] == "REFUSED" and any("approval-only mode" in r for r in refused["reasons"]), refused["reasons"]
    assert fno1._orders(t["tenant_id"]) == []


def test_the_paper_lock_still_applies_to_a_manual_order(setup, monkeypatch):
    t, expiry, strike, symbol = setup
    _flag_for(t["tenant_id"])
    fno1._lock(t["tenant_id"], True)
    try:
        asserted = []
        real = phase0_lock.assert_paper
        monkeypatch.setattr(phase0_lock, "assert_paper", lambda mode, tenant_id: asserted.append((mode, tenant_id)) or real(mode, tenant_id))
        placed = fno1._place(t, fno1._body(expiry, strike, stop_loss=70.0))
        assert placed["status"] == "PLACED" and placed["mode"] == "PAPER" and asserted == [("PAPER", t["tenant_id"])]
        broker = _FakeBroker()
        user = _user(t["user_id"])

        async def live():
            async with _session_factory() as session:
                sig = Signal(symbol=symbol, strategy_id="manual_fno", strategy_name="x", direction=SignalDirection.LONG,
                             timestamp=datetime.now(timezone.utc), entry=100.0, stop_loss=70.0, grade=SignalGrade.VALID)
                result, order = await execute_signal_for_user(session, await session.get(User, user.id), mode="LIVE", strategy_id="manual_fno",
                                                              signal=sig, broker=broker, idempotency_key=f"tt2-live:{uuid.uuid4().hex}",
                                                              origin=approval.ORIGIN_MANUAL, journal_source="MANUAL")
                return result.executed, order.status, json.loads(order.reasons_json or "[]")
        executed, status, reasons = _run(live())
        assert not executed and status == "REJECTED" and any(phase0_lock.ENTRY_REASON in r for r in reasons)
        assert broker.placed == []
    finally:
        fno1._lock(t["tenant_id"], False)


def _manual_origin_sites() -> set:
    """Every order call in app/ that passes `origin=` with the manual origin (AST: comments and docstrings do not count)."""
    found = set()
    order_calls = ("assess", "place", "preview", "execute", "execute_smart", "execute_signal_for_user", "gate_refusal")
    for path in sorted((BACKEND / "app").rglob("*.py")):
        for call in ast.walk(ast.parse(path.read_text())):
            if not isinstance(call, ast.Call):
                continue
            fn = call.func.id if isinstance(call.func, ast.Name) else call.func.attr if isinstance(call.func, ast.Attribute) else ""
            for node in call.keywords if fn in order_calls else []:
                if node.arg != "origin":
                    continue
                v = node.value
                name = v.id if isinstance(v, ast.Name) else v.attr if isinstance(v, ast.Attribute) else v.value if isinstance(v, ast.Constant) else ""
                if name in ("MANUAL", "ORIGIN_MANUAL", approval.ORIGIN_MANUAL):
                    found.add(str(path.relative_to(BACKEND)))
    return found


def test_only_the_ticket_and_basket_routes_set_the_manual_origin():
    # M45 MA-3: the multi-account ticket order is the same person's own ticket, placed per practice account.
    # T2 (TT-9): Reverse's new side is the same person's own tap on his position (preview + place, like the ticket).
    assert _manual_origin_sites() == {"app/paper/fno_routes.py", "app/accounts/routes.py", "app/paper/fno_position_actions.py"}
    assert (BACKEND / "app" / "paper" / "fno_position_actions.py").read_text().count("origin=ORIGIN_MANUAL") == 2
    routes = (BACKEND / "app" / "paper" / "fno_routes.py").read_text()
    assert routes.count("origin=MANUAL") == 4                 # ticket preview + place, basket preview + execute
    assert (BACKEND / "app" / "accounts" / "routes.py").read_text().count("origin=MANUAL") == 1   # the multi-account ticket order
    for fn in (fno_ticket.assess, fno_ticket.place, fno_basket.preview, fno_basket.execute, fno_basket.execute_smart):
        assert fn.__kwdefaults__["origin"] == approval.ORIGIN_AUTOMATIC, fn.__name__     # any other caller is automatic
