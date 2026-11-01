"""Z3 / BT2 (beta spec, Phase 0): approval-only entries. With the `approval_only_entries` flag on (off by default) every
automatic entry becomes an ENTRY proposal; only an APPROVED one places an order, always PAPER, at a re-quoted price
(refused and re-proposed past the drift limit, expired after its window). Exits are automatic (M55 S2: target exits no longer ask "Exit now?"); protective
exits stay automatic and are never held by a pending or snoozed proposal (ADR-0004); "Exit now" is always available."""
import ast
import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select, update

from app.ai import monitor, worker_bridge
from app.core import config as app_config
from app.core.enums import SignalDirection, SignalGrade
from app.core.models import Signal
from app.db.models import AiActionRecord, OrderRecord, StrategyDeploymentRecord, TradeRecord, User
from app.execution import approval
from app.execution.signal_execution import execute_signal_for_user
from app.platform import controls
from tests.test_auth_api import _session_factory, client
from tests.test_trading_worker import OPEN_NOW, _deploy, _FakeBroker, _force_signal, _get, _signal, _tenant, _trades, _worker

pytestmark = pytest.mark.usefixtures("no_open_positions_from_earlier_tests", "owner_switched_live_on")   # counts are per cycle (ADR-0004 sweep)

UTC = timezone.utc
BACKEND = Path(__file__).resolve().parents[1]


def _run(coro):
    return asyncio.run(coro)


def _email(tag: str) -> str:
    return f"z3-{tag}-{uuid.uuid4().hex[:6]}@example.com"


def _flag_for(tenant_id: int, on: bool = True) -> None:
    """The flag for this organisation only (the suite shares one database), the other flags untouched."""
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            entry = flags.get(approval.FLAG) or {"on": False, "tenants": []}
            tenants = set(entry.get("tenants") or [])
            (tenants.add if on else tenants.discard)(tenant_id)
            flags[approval.FLAG] = {"on": False, "tenants": sorted(tenants)}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())


def _price(monkeypatch, value, seen=None):
    """The live price an approval re-quotes at (the deciding user's own broker session in production). `seen` collects
    the (symbol, exchange) pairs asked for."""
    async def factory(session, who):
        if value is None:
            return None

        async def lookup(symbol, exchange="NSE"):
            if seen is not None:
                seen.append((symbol, exchange))
            return float(value() if callable(value) else value)
        return lookup
    monkeypatch.setattr(worker_bridge, "price_lookup_for_tenant", factory)
    monkeypatch.setattr(worker_bridge, "price_lookup_for_user", factory)


def _actions(tenant_id, action=None):
    async def go():
        async with _session_factory() as session:
            query = select(AiActionRecord).where(AiActionRecord.tenant_id == tenant_id)
            if action:
                query = query.where(AiActionRecord.action == action)
            return list(await session.scalars(query.order_by(AiActionRecord.id)))
    return _run(go())


def _orders(tenant_id):
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(OrderRecord).where(OrderRecord.tenant_id == tenant_id).order_by(OrderRecord.id)))
    return _run(go())


def _user(user_id) -> User:
    async def go():
        async with _session_factory() as session:
            return await session.get(User, user_id)
    return _run(go())


def _shift_expiry(action_id, minutes=10):
    async def go():
        async with _session_factory() as session:
            row = await session.get(AiActionRecord, action_id)
            row.expires_at = datetime.now(UTC) + timedelta(minutes=minutes)
            await session.commit()
    _run(go())


def quiet_alerts(tenant_id: int) -> None:
    """Remove a test organisation's alert channels and undelivered alerts, so a later worker cycle in the same test
    process never tries to reach Telegram for it (no network in tests)."""
    from sqlalchemy import delete

    from app.db.models import AlertChannelRecord, AlertDeliveryRecord

    async def go():
        async with _session_factory() as session:
            await session.execute(delete(AlertDeliveryRecord).where(AlertDeliveryRecord.tenant_id == tenant_id))
            await session.execute(delete(AlertChannelRecord).where(AlertChannelRecord.tenant_id == tenant_id))
            await session.commit()
    _run(go())


def _sig(entry=100.0, stop=98.0, target=104.0, direction=SignalDirection.LONG, symbol="RELIANCE") -> Signal:
    return Signal(symbol=symbol, strategy_id="ema_rsi_scalper_1m", strategy_name="EMA + RSI", direction=direction, timestamp=datetime.now(UTC),
                  entry=entry, stop_loss=stop, target1=target, target2=None, risk_reward=2.0, score=80, grade=SignalGrade.HIGH_QUALITY,
                  reasons=["test"], timeframe_combo="1min")


def _propose(t, *, dep_id=None, entry=100.0, now=None):
    async def go():
        async with _session_factory() as session:
            dep = await session.get(StrategyDeploymentRecord, dep_id) if dep_id else None
            row = await approval.propose_entry(session, t["tenant_id"], signal=_sig(entry=entry), strategy_id="ema_rsi_scalper_1m", source="tradingview",
                                               idempotency_key=f"test:{uuid.uuid4().hex}", deployment=dep, user_id=t["user_id"], now=now)
            return row.id
    return _run(go())


# --- the re-quote rule (pure) -----------------------------------------------------------------------------------------

def test_requote_rule_percent_ticks_stricter_wins_and_levels_expire():
    check = approval.requote_check
    # Within 0.5% of 100 -> placed at the new price; beyond -> re-quote.
    assert check("LONG", 100.0, 100.4, stop_loss=98, target1=104, max_drift_pct=0.5, max_drift_ticks=0, tick_size=0.05)[0] == "ok"
    verdict, why = check("LONG", 100.0, 100.6, stop_loss=98, target1=104, max_drift_pct=0.5, max_drift_ticks=0, tick_size=0.05)
    assert verdict == "requote" and "0.5% limit" in why
    # A favourable move is still a move: the person decides again at the new price.
    assert check("LONG", 100.0, 99.4, stop_loss=98, target1=104, max_drift_pct=0.5, max_drift_ticks=0, tick_size=0.05)[0] == "requote"
    # Ticks: 4 ticks of 0.05 = 0.20 is stricter than 0.5% of 100 = 0.50, so it decides.
    verdict, why = check("LONG", 100.0, 100.3, stop_loss=98, target1=104, max_drift_pct=0.5, max_drift_ticks=4, tick_size=0.05)
    assert verdict == "requote" and "4 ticks" in why
    assert check("LONG", 100.0, 100.2, stop_loss=98, target1=104, max_drift_pct=0.5, max_drift_ticks=4, tick_size=0.05)[0] == "ok"
    # Through the stop or target 1 there is no trade left: expire, no re-quote (both directions).
    assert check("LONG", 100.0, 97.9, stop_loss=98, target1=104, max_drift_pct=50, max_drift_ticks=0, tick_size=0.05)[0] == "expire"
    assert check("LONG", 100.0, 104.0, stop_loss=98, target1=104, max_drift_pct=50, max_drift_ticks=0, tick_size=0.05)[0] == "expire"
    assert check("SHORT", 100.0, 102.0, stop_loss=102, target1=96, max_drift_pct=50, max_drift_ticks=0, tick_size=0.05)[0] == "expire"
    assert check("SHORT", 100.0, 99.8, stop_loss=102, target1=96, max_drift_pct=0.5, max_drift_ticks=0, tick_size=0.05)[0] == "ok"
    # No limit configured: any price between the levels is placed.
    assert check("LONG", 100.0, 103.0, stop_loss=98, target1=104, max_drift_pct=0, max_drift_ticks=0, tick_size=0.05)[0] == "ok"
    assert check("LONG", 100.0, 0.0, stop_loss=98, target1=104, max_drift_pct=0.5, max_drift_ticks=0, tick_size=0.05)[0] == "expire"
    # UI2-3 follow-up: a NaN compares false with everything, so it used to pass as "ok"; not finite is no price.
    for bad in (float("nan"), float("inf"), float("-inf")):
        assert check("LONG", 100.0, bad, stop_loss=98, target1=104, max_drift_pct=0.5, max_drift_ticks=0, tick_size=0.05) == (
            "expire", "No valid price to re-quote the entry")
        assert check("SHORT", 100.0, bad, stop_loss=None, target1=None, max_drift_pct=0, max_drift_ticks=0, tick_size=0.05)[0] == "expire"
        assert check("LONG", bad, 100.0, stop_loss=98, target1=104, max_drift_pct=0.5, max_drift_ticks=0, tick_size=0.05)[0] == "expire"


def test_flag_is_registered_off_by_default():
    assert approval.FLAG in controls.FEATURE_FLAGS and approval.FLAG in controls.DEFAULT_OFF_FLAGS
    assert app_config.APPROVAL_ENTRY_TTL_MINUTES >= 1 and app_config.APPROVAL_MAX_REQUOTES >= 0


# --- worker: a signal becomes a proposal, never an order ---------------------------------------------------------------

def test_worker_raises_one_entry_proposal_and_places_nothing_until_approved(monkeypatch):
    t = _tenant(_email("worker"))
    _flag_for(t["tenant_id"])
    dep_id = _deploy(t)
    _force_signal(monkeypatch, _signal)
    worker = _worker(monkeypatch, _FakeBroker(ltp=100.1))

    first = _run(worker.run_cycle(now=OPEN_NOW))
    second = _run(worker.run_cycle(now=OPEN_NOW + timedelta(minutes=1)))
    assert first.signals_executed == 0 and second.signals_executed == 0
    assert _trades(t["tenant_id"]) == [] and _orders(t["tenant_id"]) == []                    # nothing placed
    entries = _actions(t["tenant_id"], "ENTRY")
    assert len(entries) == 1                                                                  # the same bar is proposed once
    row = entries[0]
    assert row.status == "PROPOSED" and row.deployment_id == dep_id and row.idempotency_key.startswith(f"deployment:{dep_id}:")
    evidence = json.loads(row.evidence_json)
    assert evidence["mode"] == "PAPER" and evidence["proposal_price"] == 100.0 and evidence["direction"] == "LONG"
    dep = _get(StrategyDeploymentRecord, dep_id)
    assert dep.last_error == f"Waiting for approval: entry proposal #{row.id}" and dep.last_signal_at is not None

    assert row.expires_at.replace(tzinfo=UTC) - OPEN_NOW.astimezone(UTC) == timedelta(minutes=app_config.APPROVAL_ENTRY_TTL_MINUTES)
    _shift_expiry(row.id)                     # the worker ran at the test's fixed clock; the person approves within the window

    # Approve: re-quoted at the live price (within 0.5%) and placed as one PAPER order through the ordinary pipeline.
    _price(monkeypatch, 100.2)
    out = client.post(f"/api/ai/actions/{row.id}/approve", headers=t["headers"], json={})
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["status"] == "EXECUTED" and body["trade_id"] is not None and "re-quoted price 100.2" in body["result"]
    trades = _trades(t["tenant_id"])
    assert len(trades) == 1 and trades[0].mode == "PAPER" and trades[0].deployment_id == dep_id
    assert abs(trades[0].entry_price - 100.2) < 0.1                                           # the re-quoted price (+ paper slippage)
    orders = _orders(t["tenant_id"])
    assert [o.idempotency_key for o in orders] == [f"approval:{row.id}"] and orders[0].mode == "PAPER"


def test_live_and_derived_deployments_are_not_proposed_in_phase_0(monkeypatch):
    t = _tenant(_email("live"))
    _flag_for(t["tenant_id"])
    dep_id = _deploy(t, mode="LIVE")
    _force_signal(monkeypatch, _signal)
    worker = _worker(monkeypatch, _FakeBroker(ltp=100.0))
    _run(worker.run_cycle(now=OPEN_NOW))
    assert _trades(t["tenant_id"]) == [] and _actions(t["tenant_id"], "ENTRY") == []
    assert "ATP places no LIVE entry" in (_get(StrategyDeploymentRecord, dep_id).last_error or "")


def test_deployment_entry_mode_approval_binds_without_the_flag(monkeypatch):
    t = _tenant(_email("mode"))
    # M68 PR-6 (TS1-061): "Ask me first" is the user's own choice - no organisation flag is needed to choose it any more
    # (before M68 this create was refused with 400 until the approval_only_entries flag was on).
    body = {"strategy_id": "ema_rsi_scalper_1m", "symbol": "TCS", "mode": "PAPER", "broker_name": "upstox", "entry_mode": "APPROVAL"}
    _flag_for(t["tenant_id"])
    created = client.post("/api/deployments", headers=t["headers"], json=body)
    assert created.status_code == 201, created.text
    assert created.json()["entry_mode"] == "APPROVAL"
    # The flag going off later never turns an APPROVAL deployment back into an automatic one.
    _flag_for(t["tenant_id"], on=False)

    async def required():
        async with _session_factory() as session:
            dep = await session.get(StrategyDeploymentRecord, created.json()["id"])
            return await approval.required(session, t["tenant_id"], dep), await approval.required(session, t["tenant_id"])
    assert _run(required()) == (True, False)


# --- the gate: no entry path bypasses approval ------------------------------------------------------------------------

def test_the_order_pipeline_refuses_an_automatic_entry_without_an_approved_proposal():
    t = _tenant(_email("gate"))
    other = _tenant(_email("gate-other"))
    _flag_for(t["tenant_id"])
    _flag_for(other["tenant_id"])
    user = _user(t["user_id"])

    async def go(**kw):
        async with _session_factory() as session:
            result, order = await execute_signal_for_user(session, user, mode="PAPER", strategy_id="ema_rsi_scalper_1m", signal=_sig(),
                                                          idempotency_key=f"gate:{uuid.uuid4().hex}", **kw)
            return result, order.status, json.loads(order.reasons_json)

    # The default origin is "automatic": refused, visibly, on the order trail.
    result, status, reasons = _run(go())
    assert not result.executed and status == "REJECTED" and "Approval-only mode" in reasons[0]
    # A proposal that is still PROPOSED, or another organisation's APPROVED one, is not an approval.
    pending = _propose(t)
    assert "not an approved entry" in _run(go(origin="approval", approval_action_id=pending))[2][0]
    foreign = _propose(other)
    other_user = _user(other["user_id"])

    async def approve_foreign():
        async with _session_factory() as session:
            row = await session.get(AiActionRecord, foreign)
            await monitor.decide(session, row, other_user, approve=True, note=None)
    _run(approve_foreign())
    assert "not an approved entry" in _run(go(origin="approval", approval_action_id=foreign))[2][0]
    # Review M1 + TT-2: "manual" counts only for a manual order (journal source MANUAL, no deployment) - the F&O ticket /
    # basket routes are its only callers (tests/test_tt2_manual_approval.py). Anything else calling itself "manual" is
    # refused like any other automatic entry.
    result, status, reasons = _run(go(origin="manual"))
    assert not result.executed and status == "REJECTED" and "Approval-only mode" in reasons[0]


def test_structures_are_refused_under_approval_only():
    t = _tenant(_email("structure"))
    _flag_for(t["tenant_id"])

    async def go():
        async with _session_factory() as session:
            return await approval.gate_refusal(session, t["tenant_id"], deployment_id=None, origin=approval.ORIGIN_AUTOMATIC, approval_action_id=None)
    assert "Approval-only mode" in _run(go())
    source = (BACKEND / "app" / "execution" / "multileg.py").read_text()
    assert "approval_gate.gate_refusal(" in source
    # M55 T2: the structure path's default origin is the refusing one; only "approval" (with the APPROVED proposal's id,
    # app/execution/approval.py::_execute_legs) passes - tests/test_m55_t2_approval_structures.py runs it.
    assert "origin: str = approval_gate.ORIGIN_AUTOMATIC" in source
    assert "approval_action_id=approval_action_id if origin == approval_gate.ORIGIN_APPROVAL else None" in source


def _pipeline_callers() -> dict:
    """Every call of the two order pipelines in app/ (AST, so comments and docstrings do not count)."""
    found: dict = {}
    for path in sorted((BACKEND / "app").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call):
                name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
                if name in ("execute_signal_for_user", "execute_structure"):
                    found.setdefault(str(path.relative_to(BACKEND)), set()).add(name)
    return found


def test_every_order_entry_point_is_reviewed_for_approval():
    """A static census: every caller of the two order pipelines in app/ is listed here with how it meets approval-only
    mode. A new caller fails this test until it is reviewed (and the pipelines' own default refuses it meanwhile)."""
    assert _pipeline_callers() == {"app/strategy_engine/console_routes.py": {"execute_signal_for_user"}, "app/webhooks/routes.py": {"execute_signal_for_user"},
                                   "app/public_api/routes.py": {"execute_signal_for_user"}, "app/execution/approval.py": {"execute_signal_for_user", "execute_structure"},
                                   "app/workers/trading_worker.py": {"execute_signal_for_user", "execute_structure"},
                                   "app/paper/ticket.py": {"execute_signal_for_user"},
                                   "app/paper/fno_ticket.py": {"execute_signal_for_user"},
                                   "app/execution/core/dispatcher.py": {"execute_signal_for_user"}}
    # FNO-1 + TT-2 (answers FNO-1-4): the option chain's manual PAPER ticket asks the gate with the origin its route
    # gives ("manual" only on the ticket / basket routes: the user's own confirmed order is his approval); `place` places
    # only what `assess` allowed, PAPER only.
    fno_src = (BACKEND / "app" / "paper" / "fno_ticket.py").read_text()
    assert fno_src.index("approval.gate_refusal(") < fno_src.index("execute_signal_for_user(")
    assert 'if not view["can_place"]:' in fno_src and 'MODE = "PAPER"' in fno_src
    # N1 X2: the core's Auto PAPER route asks approval-only mode again right before the pipeline (falls back to a
    # proposal), PAPER only and without a broker (tests/test_n1_e1_execution_core.py checks the call).
    core = (BACKEND / "app" / "execution" / "core" / "dispatcher.py").read_text()
    paper = core[core.index("async def _paper("):core.index("async def _placed(")]
    assert paper.index("approval.required(") < paper.index("execute_signal_for_user(")
    # Z5 / P1-d: the plan-before-trade PAPER ticket proposes first under approval-only mode (like the console), PAPER only.
    ticket_src = (BACKEND / "app" / "paper" / "ticket.py").read_text()
    assert "mode=by_default.MODE" in ticket_src
    for rel in ("app/strategy_engine/console_routes.py", "app/webhooks/routes.py", "app/public_api/routes.py", "app/paper/ticket.py"):
        text = (BACKEND / rel).read_text()
        assert text.index("approval.required(") < text.index("execute_signal_for_user("), rel   # proposes first
    worker = (BACKEND / "app" / "workers" / "trading_worker.py").read_text()
    evaluate = worker[worker.index("async def _evaluate_deployment"):worker.index("async def _propose_entry")]
    assert evaluate.index("approval.required(") < evaluate.index("execute_signal_for_user(")
    assert evaluate.index("approval.required(") < evaluate.index("self._evaluate_structure(")
    assert "origin=ORIGIN_APPROVAL, approval_action_id=action.id" in (BACKEND / "app" / "execution" / "approval.py").read_text()
    # The default origin of the pipeline is the refusing one.
    pipeline = (BACKEND / "app" / "execution" / "signal_execution.py").read_text()
    assert "origin: str = approval_gate.ORIGIN_AUTOMATIC" in pipeline


def test_tradingview_alert_becomes_a_proposal_once(monkeypatch):
    from tests.test_tradingview_webhook import _alert, _get_token
    t = _tenant(_email("tv"))
    _flag_for(t["tenant_id"])
    token = _get_token(t["headers"])
    first = client.post(f"/api/webhooks/tradingview/{token}", json=_alert(symbol="RELIANCE", alert_id="z3-1"))
    again = client.post(f"/api/webhooks/tradingview/{token}", json=_alert(symbol="RELIANCE", alert_id="z3-1"))
    assert first.status_code == 200 and first.json()["executed"] is False and "entry proposal #" in first.json()["reasons"][0]
    assert again.json()["executed"] is False and "already proposed" in again.json()["reasons"][0]
    assert _orders(t["tenant_id"]) == [] and len(_actions(t["tenant_id"], "ENTRY")) == 1


# --- expiry and re-quote on approval -----------------------------------------------------------------------------------

def test_approval_after_drift_places_nothing_and_requotes_until_the_allowance_runs_out(monkeypatch):
    t = _tenant(_email("requote"))
    _flag_for(t["tenant_id"])
    monkeypatch.setattr(app_config, "APPROVAL_MAX_REQUOTES", 1)
    first = _propose(t, entry=100.0)
    _price(monkeypatch, 101.0)                                                          # +1% > 0.5%
    body = client.post(f"/api/ai/actions/{first}/approve", headers=t["headers"], json={}).json()
    assert body["status"] == "EXPIRED" and "Re-quoted as proposal #" in body["result"] and _orders(t["tenant_id"]) == []
    requoted = [a for a in _actions(t["tenant_id"], "ENTRY") if a.id != first]
    assert len(requoted) == 1 and requoted[0].status == "PROPOSED"
    evidence = json.loads(requoted[0].evidence_json)
    assert evidence["proposal_price"] == 101.0 and evidence["requotes"] == 1 and evidence["requoted_from"] == first

    _price(monkeypatch, 102.0)                                                          # moved again; the allowance is used up
    body = client.post(f"/api/ai/actions/{requoted[0].id}/approve", headers=t["headers"], json={}).json()
    assert body["status"] == "EXPIRED" and "No re-quote left" in body["result"]
    assert len(_actions(t["tenant_id"], "ENTRY")) == 2 and _orders(t["tenant_id"]) == []

    # Through the stop: expired without a re-quote.
    third = _propose(t, entry=100.0)
    _price(monkeypatch, 97.5)
    body = client.post(f"/api/ai/actions/{third}/approve", headers=t["headers"], json={}).json()
    assert body["status"] == "EXPIRED" and "through the stop" in body["result"] and len(_actions(t["tenant_id"], "ENTRY")) == 3


def test_a_requote_refused_for_its_price_says_so_not_that_entries_are_paused(monkeypatch):
    """UI2-3 review nit: InvalidEntryPrice is a subclass of EntriesPaused; the re-quote path names the price, not a pause."""
    t = _tenant(_email("requote-price"))
    _flag_for(t["tenant_id"])
    first, second = _propose(t, entry=100.0), _propose(t, entry=100.0)
    _price(monkeypatch, 101.0)                                                          # +1% > 0.5%: a re-quote

    async def refuse(*args, **kwargs):
        raise approval.InvalidEntryPrice("The signal has no usable entry price")
    monkeypatch.setattr(approval, "propose_entry", refuse)
    body = client.post(f"/api/ai/actions/{first}/approve", headers=t["headers"], json={}).json()
    assert body["status"] == "EXPIRED" and body["result"].endswith("No re-quote: the current price is not usable")
    assert "paused" not in body["result"] and _orders(t["tenant_id"]) == []

    async def paused(*args, **kwargs):
        raise approval.EntriesPaused("Your PAPER entries are paused")
    monkeypatch.setattr(approval, "propose_entry", paused)
    body = client.post(f"/api/ai/actions/{second}/approve", headers=t["headers"], json={}).json()
    assert body["status"] == "EXPIRED" and body["result"].endswith("No re-quote: PAPER entries are paused")


def test_no_price_means_no_order_and_expired_proposals_cannot_be_approved(monkeypatch):
    t = _tenant(_email("expiry"))
    _flag_for(t["tenant_id"])
    _price(monkeypatch, None)
    first = _propose(t)
    body = client.post(f"/api/ai/actions/{first}/approve", headers=t["headers"], json={}).json()
    assert body["status"] == "FAILED" and "No live price" in body["result"] and _orders(t["tenant_id"]) == []

    late = _propose(t, now=datetime.now(UTC) - timedelta(minutes=app_config.APPROVAL_ENTRY_TTL_MINUTES + 1))
    refused = client.post(f"/api/ai/actions/{late}/approve", headers=t["headers"], json={})
    assert refused.status_code == 409 and "expired" in refused.text

    # The sweep expires due rows (here for one organisation; the worker's housekeeping runs it for all, every cycle).
    stale = _propose(t, now=datetime.now(UTC) - timedelta(minutes=app_config.APPROVAL_ENTRY_TTL_MINUTES + 1))
    other = _tenant(_email("expiry-other"))
    other_stale = _propose(other, now=datetime.now(UTC) - timedelta(hours=30))

    async def sweep():
        async with _session_factory() as session:
            return await monitor.expire_stale(session, datetime.now(UTC), tenant_id=t["tenant_id"])
    assert _run(sweep()) >= 1
    statuses = {a.id: a.status for a in _actions(t["tenant_id"]) + _actions(other["tenant_id"])}
    assert statuses[stale] == "EXPIRED" and statuses[other_stale] == "PROPOSED"


# --- exits policy ------------------------------------------------------------------------------------------------------

def _open_trade(t, *, dep_id=None, entry=100.0, stop=98.0, target=104.0, mode="PAPER", approved=False, mark=None, mark_age_minutes=1.0):
    """An open position; `approved` links it to an EXECUTED entry proposal (it was entered by approval)."""
    async def go():
        async with _session_factory() as session:
            trade = TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], mode=mode, symbol="RELIANCE", strategy_id="ema_rsi_scalper_1m",
                                direction="LONG", entry_time=datetime.now(UTC) - timedelta(minutes=20), entry_price=entry, quantity=10.0,
                                stop_loss=stop, target1=target, exchange="NSE", deployment_id=dep_id, mark_price=mark,
                                mark_time=(datetime.now(UTC) - timedelta(minutes=mark_age_minutes)) if mark is not None else None)
            session.add(trade)
            await session.flush()
            if approved:
                session.add(AiActionRecord(tenant_id=t["tenant_id"], deployment_id=dep_id, trade_id=trade.id, action="ENTRY", rule="ENTRY_SIGNAL",
                                           reason="test", status="EXECUTED", expires_at=datetime.now(UTC), owner_user_id=t["user_id"],
                                           idempotency_key=f"test:{uuid.uuid4().hex}"))
            await session.commit()
            return trade.id
    return _run(go())


def test_target_exits_are_automatic_under_approval_only_and_the_stop_closes_at_once(monkeypatch):
    """M55 S2 (A8, owner order): the approval gate is for entries only - a target closes an approved position at once,
    no "Exit now?" question is raised; the stop stays automatic as before."""
    t = _tenant(_email("exits"))
    _flag_for(t["tenant_id"])
    dep_id = _deploy(t)
    trade_id = _open_trade(t, dep_id=dep_id, approved=True)
    other_id = _open_trade(t, dep_id=None)                             # entered another way: automatic as before
    broker = _FakeBroker(ltp=104.5)                                    # through target 1
    worker = _worker(monkeypatch, broker)
    _force_signal(monkeypatch, lambda: _signal(direction=SignalDirection.NO_TRADE))

    report = _run(worker.run_cycle(now=OPEN_NOW))
    assert report.positions_closed == 2
    assert _get(TradeRecord, trade_id).exit_reason == "Target 1" and _get(TradeRecord, other_id).exit_reason == "Target 1"
    assert _actions(t["tenant_id"], "EXIT_POSITION") == []                                      # nothing waits for an answer

    stopped_id = _open_trade(t, dep_id=dep_id, approved=True)
    broker.ltp = 97.0
    report = _run(worker.run_cycle(now=OPEN_NOW + timedelta(minutes=2)))
    closed = _get(TradeRecord, stopped_id)
    assert report.positions_closed == 1 and closed.exit_reason == "Stop Loss" and closed.exit_time is not None


def test_flag_off_keeps_target_exits_automatic_and_hides_exit_now(monkeypatch):
    t = _tenant(_email("exits-off"))
    trade_id = _open_trade(t, dep_id=_deploy(t))
    worker = _worker(monkeypatch, _FakeBroker(ltp=104.5))
    _force_signal(monkeypatch, lambda: _signal(direction=SignalDirection.NO_TRADE))
    approved_id = _open_trade(t, dep_id=None, approved=True)          # review V6: entered by approval, flag now off
    _run(worker.run_cycle(now=OPEN_NOW))
    assert _get(TradeRecord, trade_id).exit_reason == "Target 1" and _actions(t["tenant_id"], "EXIT_POSITION") == []
    assert _get(TradeRecord, approved_id).exit_reason == "Target 1"
    # Review M4: "Exit now" does not exist with the flag off (exits run automatically).
    assert client.post(f"/api/positions/{_open_trade(t)}/exit-now", headers=t["headers"]).status_code == 404


def test_protective_reasons_are_everything_but_targets():
    for reason in ("Stop Loss", "Premium floor", "Premium ceiling", "Time exit at 15:00", "Square-off", "Spread stop (value 3 >= 2)",
                   "Spread target (value 1 <= 2)", "Kill switch", "Something new"):
        assert approval.is_protective(reason), reason
    for reason in ("Target 1", "Target 2", "Target 1 (underlying)"):
        assert not approval.is_protective(reason), reason


def test_exit_now_never_takes_a_typed_price_refuses_live_and_is_the_holders(monkeypatch):
    from tests.test_z3_members import member
    t = _tenant(_email("exitnow"))
    _flag_for(t["tenant_id"])
    mate = member(t)
    other = _tenant(_email("exitnow-other"))
    _price(monkeypatch, None)
    trade_id = _open_trade(t)
    assert client.post(f"/api/positions/{trade_id}/exit-now", headers=other["headers"]).status_code == 404
    assert client.post(f"/api/positions/{trade_id}/exit-now", headers=mate["headers"]).status_code == 403       # L8
    # M68 PR-3 (TS1-097, ADR-0004): no quote and never priced by the monitor - the exit is NOT refused: it closes at the
    # entry price, labelled so; a sent price is still never used (review M2).
    no_price = client.post(f"/api/positions/{trade_id}/exit-now", headers=t["headers"], json={"price": 100000})
    assert no_price.status_code == 200, no_price.text
    row = no_price.json()["closed"][0]
    assert row["exit_price"] == 100.0 and row["exit_reason"] == "Exit now (web, entry price (no price was seen))"
    # Review V2 kept as a label: a last price older than the limit is no "last seen price" - it closes at that older price,
    # with when it was seen.
    stale = _open_trade(t, mark=130.0, mark_age_minutes=3 * 24 * 60)
    old = client.post(f"/api/positions/{stale}/exit-now", headers=t["headers"])
    assert old.status_code == 200 and old.json()["closed"][0]["exit_price"] == 130.0
    assert old.json()["closed"][0]["exit_reason"].startswith("Exit now (web, older price seen ")
    aged = _open_trade(t, mark=130.0, mark_age_minutes=app_config.EXIT_NOW_MAX_MARK_AGE_MINUTES + 1)
    aged_out = client.post(f"/api/positions/{aged}/exit-now", headers=t["headers"])
    assert aged_out.status_code == 200 and "older price" in aged_out.json()["closed"][0]["exit_reason"]
    # With the monitor's last price it closes there.
    marked = _open_trade(t, mark=101.0)
    out = client.post(f"/api/positions/{marked}/exit-now", headers=t["headers"], json={"price": 100000})
    assert out.status_code == 200, out.text
    closed = out.json()["closed"][0]
    assert closed["exit_reason"] == "Exit now (web, last seen price)" and closed["exit_price"] == 101.0 and closed["pnl"] < 10.0
    assert client.post(f"/api/positions/{marked}/exit-now", headers=t["headers"]).status_code == 409
    # The live price of the holder's own entitlement wins.
    _price(monkeypatch, 102.5)
    second = _open_trade(t, mark=90.0)
    out = client.post(f"/api/positions/{second}/exit-now", headers=t["headers"])
    assert out.json()["closed"][0]["exit_price"] == 102.5 and out.json()["closed"][0]["exit_reason"] == "Exit now (web, live price)"
    # LIVE: Phase 0 has no broker order path (review M4).
    live = _open_trade(t, mode="LIVE", mark=101.0)
    refused = client.post(f"/api/positions/{live}/exit-now", headers=t["headers"])
    assert refused.status_code == 409 and "broker app" in refused.text and _get(TradeRecord, live).exit_time is None


def test_two_closes_of_one_position_book_once():
    """Review M3: "Exit now" and the worker reach the same position at once - each with its own (stale) view. Only the
    conditional claim that lands books the exit: one P&L, one EXIT notification."""
    from app.db.models import NotificationRecord
    from app.trading.position_monitor import close_position
    t = _tenant(_email("race"))
    trade_id = _open_trade(t)

    async def go():
        async with _session_factory() as first, _session_factory() as second:
            a = await first.get(TradeRecord, trade_id)
            b = await second.get(TradeRecord, trade_id)
            assert a.exit_time is None and b.exit_time is None
            won = await close_position(first, a, 103.0, "Target 1")
            lost = await close_position(second, b, 97.0, "Exit now (web, live price)")
            return won, lost
    won, lost = _run(go())
    assert won.closed and not lost.closed and "closed at the same moment" in lost.warnings[0]
    row = _get(TradeRecord, trade_id)
    assert row.exit_reason == "Target 1" and row.exit_price == 103.0

    async def exits():
        async with _session_factory() as session:
            return list(await session.scalars(select(NotificationRecord).where(NotificationRecord.related_trade_id == trade_id,
                                                                                NotificationRecord.event_type == "EXIT")))
    assert len(_run(exits())) == 1


def test_overdue_and_interrupted_proposals_free_the_deployment_slot():
    """Review M6: an overdue PROPOSED row, or an APPROVED row interrupted before execution, never swallows the next signal."""
    t = _tenant(_email("slot"))
    _flag_for(t["tenant_id"])
    dep_id = _deploy(t)
    overdue = _propose(t, dep_id=dep_id, now=datetime.now(UTC) - timedelta(minutes=app_config.APPROVAL_ENTRY_TTL_MINUTES + 5))
    fresh = _propose(t, dep_id=dep_id)
    assert fresh is not None and fresh != overdue
    statuses = {a.id: a.status for a in _actions(t["tenant_id"], "ENTRY")}
    assert statuses[overdue] == "EXPIRED" and statuses[fresh] == "PROPOSED"

    async def interrupt():                    # decided, then the process died before execute()
        async with _session_factory() as session:
            row = await session.get(AiActionRecord, fresh)
            row.status, row.decided_by = "APPROVED", t["user_id"]
            row.decided_at = datetime.now(UTC) - timedelta(minutes=monitor.ORPHAN_MINUTES + 1)
            await session.commit()
    _run(interrupt())
    third = _propose(t, dep_id=dep_id)
    statuses = {a.id: (a.status, a.result) for a in _actions(t["tenant_id"], "ENTRY")}
    assert statuses[fresh][0] == "FAILED" and "interrupted" in statuses[fresh][1] and statuses[third][0] == "PROPOSED"

    # The global sweep (worker housekeeping) does the same for every organisation, conditionally.
    async def orphan_and_sweep():
        async with _session_factory() as session:
            row = await session.get(AiActionRecord, third)
            row.status, row.decided_by = "APPROVED", t["user_id"]
            row.decided_at = datetime.now(UTC) - timedelta(minutes=monitor.ORPHAN_MINUTES + 1)
            await session.commit()
            return await monitor.expire_stale(session, datetime.now(UTC))
    assert _run(orphan_and_sweep()) >= 1
    assert {a.id: a.status for a in _actions(t["tenant_id"], "ENTRY")}[third] == "FAILED"


def test_console_paper_execute_needs_a_trader_and_becomes_a_proposal_under_approval_only(monkeypatch):
    """Review M1: the console path is not a bypass - a VIEWER is refused, and with approval-only on the signal (priced
    from candles the browser sent) becomes an entry proposal; nothing is placed. Flag off, the fill is marked "client"."""
    from app.strategy_engine.registry import registry
    from tests.test_trading_api import _fake_long_signal, _sample_candles_payload
    from tests.test_z3_members import member
    monkeypatch.setattr(registry.get("ema_rsi_scalper_1m"), "analyze", lambda data, symbol: _fake_long_signal())
    t = _tenant(_email("console"))
    viewer = member(t, role="VIEWER")
    body = {"symbol": "TESTSYM", "candles": {"1min": _sample_candles_payload()}}
    url = "/api/strategies/ema_rsi_scalper_1m/paper-execute"
    assert client.post(url, headers=viewer["headers"], json=body).status_code == 403

    flag_off = client.post(url, headers=t["headers"], json=body).json()
    assert flag_off["executed"] is True
    assert _get(TradeRecord, _orders(t["tenant_id"])[0].trade_id).price_source == "client"

    _flag_for(t["tenant_id"])
    out = client.post(url, headers=t["headers"], json=body)
    assert out.status_code == 200 and out.json()["executed"] is False and "entry proposal #" in out.json()["reasons"][0]
    assert len(_orders(t["tenant_id"])) == 1                                        # nothing new placed
    entries = _actions(t["tenant_id"], "ENTRY")
    assert len(entries) == 1 and json.loads(entries[0].evidence_json)["source"] == "console" and entries[0].owner_user_id == t["user_id"]


def test_proposals_are_decided_by_their_owner_or_the_organisation_owner_and_requote_uses_the_exchange(monkeypatch):
    """Review L8 / L3: a member's entry proposal is not another member's to decide; the owner (or the organisation OWNER)
    decides, the PAPER order lands in the proposal owner's account, and the re-quote asks for the proposal's exchange."""
    from tests.test_z3_members import member
    t = _tenant(_email("owner"))
    _flag_for(t["tenant_id"])
    mate = member(t)
    third = member(t)

    async def propose_for_mate():
        async with _session_factory() as session:
            row = await approval.propose_entry(session, t["tenant_id"], signal=_sig(symbol="SENSEX"), strategy_id="x", source="tradingview",
                                               idempotency_key=f"o:{uuid.uuid4().hex}", user_id=mate["user_id"], exchange="BSE")
            return row.id
    pid = _run(propose_for_mate())
    assert client.post(f"/api/ai/actions/{pid}/approve", headers=third["headers"], json={}).status_code == 403
    assert client.post(f"/api/ai/actions/{pid}/reject", headers=third["headers"], json={}).status_code == 403
    assert client.post(f"/api/ai/actions/{pid}/snooze", headers=third["headers"], json={"minutes": 15}).status_code == 403
    seen: list = []
    _price(monkeypatch, 100.1, seen)
    out = client.post(f"/api/ai/actions/{pid}/approve", headers=t["headers"], json={})          # the organisation OWNER may
    assert out.status_code == 200 and out.json()["status"] == "EXECUTED", out.text
    assert seen == [("SENSEX", "BSE")]
    assert _get(TradeRecord, out.json()["trade_id"]).user_id == mate["user_id"]               # the proposal owner's position


def test_requote_with_byob_on_uses_only_the_approvers_own_entitlement():
    """Review M5: with byob_data_tier on, a member without their own confirmed broker session has no live price - the
    organisation's (the owner's) credential is never borrowed - so the approval places nothing."""
    from tests.test_z3_members import member
    t = _tenant(_email("byob"))                 # the owner holds the organisation's broker credential
    _flag_for(t["tenant_id"])
    mate = member(t)

    async def byob(on):
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            flags["byob_data_tier"] = {"on": False, "tenants": [t["tenant_id"]] if on else []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(byob(True))
    try:
        async def propose_for_mate():
            async with _session_factory() as session:
                row = await approval.propose_entry(session, t["tenant_id"], signal=_sig(), strategy_id="x", source="tradingview",
                                                   idempotency_key=f"b:{uuid.uuid4().hex}", user_id=mate["user_id"])
                return row.id
        pid = _run(propose_for_mate())
        body = client.post(f"/api/ai/actions/{pid}/approve", headers=mate["headers"], json={}).json()
        assert body["status"] == "FAILED" and "own broker session" in body["result"] and _orders(t["tenant_id"]) == []
    finally:
        _run(byob(False))


def test_typed_price_never_closes_a_counted_paper_position():
    """Review V3: with approval-only on the typed-price route is closed; with it off a PAPER close there is tagged
    price_source="client" (left out of the PAPER account and ladder)."""
    t = _tenant(_email("markprice"))
    first = _open_trade(t)
    out = client.post(f"/api/positions/{first}/mark-price", headers=t["headers"], json={"current_price": 105.0})
    assert out.json()["closed"] is True and _get(TradeRecord, first).price_source == "client"
    untouched = _open_trade(t)
    assert client.post(f"/api/positions/{untouched}/mark-price", headers=t["headers"], json={"current_price": 101.0}).json()["closed"] is False
    assert _get(TradeRecord, untouched).price_source is None
    _flag_for(t["tenant_id"])
    second = _open_trade(t)
    refused = client.post(f"/api/positions/{second}/mark-price", headers=t["headers"], json={"current_price": 105.0})
    assert refused.status_code == 409 and "Exit now" in refused.text and _get(TradeRecord, second).exit_time is None


def test_an_approval_settled_by_the_sweep_meanwhile_places_nothing(monkeypatch):
    """Review V5: the gate re-reads the proposal from the database and the final write is conditional - an approval the
    orphan sweep already settled (FAILED) cannot place an order through a stale in-memory copy."""
    t = _tenant(_email("orphanrace"))
    _flag_for(t["tenant_id"])
    _price(monkeypatch, 100.1)
    pid = _propose(t)
    user = _user(t["user_id"])

    async def go():
        async with _session_factory() as session, _session_factory() as sweeper:
            action = await session.get(AiActionRecord, pid)
            action = await monitor.decide(session, action, user, approve=True, note=None)
            # Meanwhile, elsewhere: the sweep settles it.
            await sweeper.execute(update(AiActionRecord).where(AiActionRecord.id == pid)
                                  .values(status="FAILED", result="Approved but not carried out (interrupted) - no order was placed"))
            await sweeper.commit()
            lookup = await worker_bridge.price_lookup_for_user(session, user)
            return await approval.execute_entry(session, action, user, price_lookup=lookup)
    row = _run(go())
    assert row.status == "FAILED" and "interrupted" in row.result
    assert all(o.status == "REJECTED" for o in _orders(t["tenant_id"]))
    assert not any(tr.mode == "PAPER" and tr.exit_time is None for tr in _trades_of(t))


def test_an_interrupted_exit_approval_says_what_happened_to_the_position():
    """Review V5: the orphan result of an exit tells the truth about the position."""
    t = _tenant(_email("orphanexit"))
    closed_id = _open_trade(t)
    open_id = _open_trade(t)

    async def go():
        async with _session_factory() as session:
            closed = await session.get(TradeRecord, closed_id)
            closed.exit_time, closed.exit_price, closed.exit_reason, closed.pnl = datetime.now(UTC), 101.0, "Stop Loss", -20.0
            ids = []
            for trade_id in (closed_id, open_id):
                row = AiActionRecord(tenant_id=t["tenant_id"], trade_id=trade_id, action="EXIT_POSITION", rule="TARGET_1_REACHED", reason="x",
                                     status="APPROVED", decided_by=t["user_id"], expires_at=datetime.now(UTC) + timedelta(minutes=5),
                                     decided_at=datetime.now(UTC) - timedelta(minutes=monitor.ORPHAN_MINUTES + 1))
                session.add(row)
                await session.flush()
                ids.append(row.id)
            await session.commit()
            await monitor.expire_stale(session, datetime.now(UTC), tenant_id=t["tenant_id"])
            return ids
    closed_action, open_action = _run(go())
    rows = {a.id: a for a in _actions(t["tenant_id"], "EXIT_POSITION")}
    assert rows[closed_action].status == "FAILED" and "is closed (Stop Loss)" in rows[closed_action].result
    assert rows[open_action].status == "FAILED" and "still open" in rows[open_action].result


def _trades_of(t):
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(TradeRecord).where(TradeRecord.tenant_id == t["tenant_id"])))
    return _run(go())


# --- Telegram ----------------------------------------------------------------------------------------------------------

@pytest.fixture
def _tg(monkeypatch):
    from app.telegram_inbound import service as tg
    from tests.test_phase_be_telegram_inbound import _no_shared_rate_limit, _Telegram  # noqa: F401

    async def never(*_a, **_k):
        return False
    monkeypatch.setattr(tg, "rate_limited", never)
    return _Telegram()


def test_telegram_approves_a_paper_entry_and_exit_command_closes_paper_only(monkeypatch, _tg):
    from tests.test_phase_be_telegram_inbound import _setup
    t = _setup(_email("tg"), monkeypatch, _tg)
    try:
        _telegram_entry_and_exit(t, monkeypatch, _tg)
    finally:
        quiet_alerts(t["tenant_id"])


def _telegram_entry_and_exit(t, monkeypatch, _tg):
    from tests.test_phase_be_telegram_inbound import _callback, _dispatch, _message, _post
    _flag_for(t["tenant_id"])
    action_id = _propose(t, entry=100.0)
    sent, failed = _dispatch(t, _tg)
    assert failed == 0 and sent >= 1
    msg = next(c for c in _tg.calls if c["method"] == "sendMessage" and "reply_markup" in c)
    approve = msg["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    _price(monkeypatch, 100.1)
    out = _post(t, _callback("987654", approve)).json()
    assert "EXECUTED" in out["reply"], out
    row = next(a for a in _actions(t["tenant_id"], "ENTRY") if a.id == action_id)
    assert row.status == "EXECUTED" and row.trade_id is not None

    # /exit asks first (L4), then the confirmed command closes that PAPER position at once; a LIVE one is refused.
    ask = _post(t, _message("987654", f"/exit {row.trade_id}")).json()["reply"]
    assert ask.startswith(f"Exit #{row.trade_id} LONG RELIANCE") and "confirm" in ask and _get(TradeRecord, row.trade_id).exit_time is None
    reply = _post(t, _message("987654", f"/exit {row.trade_id} confirm")).json()["reply"]
    assert reply.startswith(f"Closed #{row.trade_id} at 100.10") and _get(TradeRecord, row.trade_id).exit_reason == "Exit now (telegram, live price)"
    live = _open_trade(t, mode="LIVE")
    assert "broker app" in _post(t, _message("987654", f"/exit {live} confirm")).json()["reply"]
    assert _get(TradeRecord, live).exit_time is None
    assert "Usage" in _post(t, _message("987654", "/exit abc")).json()["reply"]
