"""Z3 / BT2 (beta spec, Phase 0): approval-only entries. With the `approval_only_entries` flag on (off by default) every
automatic entry becomes an ENTRY proposal; only an APPROVED one places an order, always PAPER, at a re-quoted price
(refused and re-proposed past the drift limit, expired after its window). Target exits ask "Exit now?"; protective
exits stay automatic and are never held by a pending or snoozed proposal (ADR-0004); "Exit now" is always available."""
import ast
import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from app.ai import monitor, worker_bridge
from app.core import config as app_config
from app.core.enums import SignalDirection, SignalGrade
from app.core.models import Signal
from app.db.models import AiActionRecord, OrderRecord, StrategyDeploymentRecord, TradeRecord, User
from app.execution import approval
from app.execution.signal_execution import execute_signal_for_user
from app.platform import controls
from tests.test_auth_api import _session_factory, client
from tests.test_trading_worker import OPEN_NOW, _FakeBroker, _deploy, _force_signal, _get, _signal, _tenant, _trades, _worker

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


def _price(monkeypatch, value):
    """The live price an approval re-quotes at (the worker bridge's broker session in production)."""
    async def factory(session, tenant_id):
        if value is None:
            return None

        async def lookup(symbol):
            return float(value() if callable(value) else value)
        return lookup
    monkeypatch.setattr(worker_bridge, "price_lookup_for_tenant", factory)


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


def test_deployment_entry_mode_approval_binds_without_the_flag_and_needs_the_flag_to_be_set(monkeypatch):
    t = _tenant(_email("mode"))
    # Creating an APPROVAL deployment needs the flag.
    body = {"strategy_id": "ema_rsi_scalper_1m", "symbol": "TCS", "mode": "PAPER", "broker_name": "upstox", "entry_mode": "APPROVAL"}
    refused = client.post("/api/deployments", headers=t["headers"], json=body)
    assert refused.status_code == 400 and "approval_only_entries" in refused.text
    _flag_for(t["tenant_id"])
    created = client.post("/api/deployments", headers=t["headers"], json=body)
    assert created.status_code == 201, created.text and created.json()["entry_mode"] == "APPROVAL"
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
    # A person's own console entry is their approval.
    result, status, _ = _run(go(origin="manual"))
    assert result.executed and status == "POSITION_OPEN"


def test_structures_are_refused_under_approval_only():
    t = _tenant(_email("structure"))
    _flag_for(t["tenant_id"])

    async def go():
        async with _session_factory() as session:
            return await approval.gate_refusal(session, t["tenant_id"], deployment_id=None, origin=approval.ORIGIN_AUTOMATIC, approval_action_id=None)
    assert "Approval-only mode" in _run(go())
    source = (BACKEND / "app" / "execution" / "multileg.py").read_text()
    assert "approval_gate.gate_refusal(" in source and "approval_action_id=None" in source


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
    assert _pipeline_callers() == {"app/main.py": {"execute_signal_for_user"}, "app/webhooks/routes.py": {"execute_signal_for_user"},
                                   "app/public_api/routes.py": {"execute_signal_for_user"}, "app/execution/approval.py": {"execute_signal_for_user"},
                                   "app/workers/trading_worker.py": {"execute_signal_for_user", "execute_structure"},
                                   "app/paper/ticket.py": {"execute_signal_for_user"}}
    main = (BACKEND / "app" / "main.py").read_text()
    assert 'origin="manual"' in main                                           # the console: a person's own entry
    # Z5 / P1-d: the plan-before-trade PAPER ticket is the person's own entry (they confirm the plan), always PAPER.
    ticket_src = (BACKEND / "app" / "paper" / "ticket.py").read_text()
    assert "origin=approval.ORIGIN_MANUAL" in ticket_src and "mode=by_default.MODE" in ticket_src
    for rel in ("app/webhooks/routes.py", "app/public_api/routes.py"):
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

    # The worker's per-cycle sweep expires due rows of its organisation only.
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

def _open_trade(t, *, dep_id=None, entry=100.0, stop=98.0, target=104.0, mode="PAPER"):
    async def go():
        async with _session_factory() as session:
            trade = TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], mode=mode, symbol="RELIANCE", strategy_id="ema_rsi_scalper_1m",
                                direction="LONG", entry_time=datetime.now(UTC) - timedelta(minutes=20), entry_price=entry, quantity=10.0,
                                stop_loss=stop, target1=target, exchange="NSE", deployment_id=dep_id)
            session.add(trade)
            await session.commit()
            return trade.id
    return _run(go())


def test_target_asks_exit_now_while_the_stop_closes_at_once_even_with_a_snoozed_question(monkeypatch):
    t = _tenant(_email("exits"))
    _flag_for(t["tenant_id"])
    dep_id = _deploy(t)
    trade_id = _open_trade(t, dep_id=dep_id)
    broker = _FakeBroker(ltp=104.5)                                    # through target 1
    worker = _worker(monkeypatch, broker)
    _force_signal(monkeypatch, lambda: _signal(direction=SignalDirection.NO_TRADE))

    report = _run(worker.run_cycle(now=OPEN_NOW))
    assert report.positions_closed == 0 and _get(TradeRecord, trade_id).exit_time is None
    asks = _actions(t["tenant_id"], "EXIT_POSITION")
    assert len(asks) == 1 and asks[0].rule == "TARGET_1_REACHED" and asks[0].trade_id == trade_id and asks[0].reason.startswith("Exit now?")
    _run(worker.run_cycle(now=OPEN_NOW + timedelta(minutes=1)))
    assert len(_actions(t["tenant_id"], "EXIT_POSITION")) == 1                                  # asked once per level

    # The person snoozes the question; the price falls through the stop: the stop closes the position at once.
    assert client.post(f"/api/ai/actions/{asks[0].id}/snooze", headers=t["headers"], json={"minutes": 15}).status_code == 200
    broker.ltp = 97.0
    report = _run(worker.run_cycle(now=OPEN_NOW + timedelta(minutes=2)))
    closed = _get(TradeRecord, trade_id)
    assert report.positions_closed == 1 and closed.exit_reason == "Stop Loss" and closed.exit_time is not None
    retired = _actions(t["tenant_id"], "EXIT_POSITION")[0]
    assert retired.status == "EXPIRED" and retired.result == "Position closed: Stop Loss"     # the stale question is void


def test_flag_off_keeps_target_exits_automatic(monkeypatch):
    t = _tenant(_email("exits-off"))
    trade_id = _open_trade(t, dep_id=_deploy(t))
    worker = _worker(monkeypatch, _FakeBroker(ltp=104.5))
    _force_signal(monkeypatch, lambda: _signal(direction=SignalDirection.NO_TRADE))
    _run(worker.run_cycle(now=OPEN_NOW))
    assert _get(TradeRecord, trade_id).exit_reason == "Target 1" and _actions(t["tenant_id"], "EXIT_POSITION") == []


def test_protective_reasons_are_everything_but_targets():
    for reason in ("Stop Loss", "Premium floor", "Premium ceiling", "Time exit at 15:00", "Square-off", "Spread stop (value 3 >= 2)",
                   "Spread target (value 1 <= 2)", "Kill switch", "Something new"):
        assert approval.is_protective(reason), reason
    for reason in ("Target 1", "Target 2", "Target 1 (underlying)"):
        assert not approval.is_protective(reason), reason


def test_exit_now_api_closes_at_once_with_the_given_price_when_no_quote(monkeypatch):
    t = _tenant(_email("exitnow"))
    other = _tenant(_email("exitnow-other"))
    _price(monkeypatch, None)
    trade_id = _open_trade(t)
    assert client.post(f"/api/positions/{trade_id}/exit-now", headers=other["headers"], json={"price": 101}).status_code == 404
    no_price = client.post(f"/api/positions/{trade_id}/exit-now", headers=t["headers"], json={})
    assert no_price.status_code == 409 and "give the exit price" in no_price.text
    out = client.post(f"/api/positions/{trade_id}/exit-now", headers=t["headers"], json={"price": 101.0})
    assert out.status_code == 200, out.text
    closed = out.json()["closed"][0]
    assert closed["exit_reason"] == "Exit now (web)" and closed["exit_price"] == 101.0 and closed["pnl"] < 10.0   # charges come off
    assert client.post(f"/api/positions/{trade_id}/exit-now", headers=t["headers"], json={"price": 101.0}).status_code == 409

    # With a quote the live price wins over the one typed.
    _price(monkeypatch, 102.5)
    second = _open_trade(t)
    out = client.post(f"/api/positions/{second}/exit-now", headers=t["headers"], json={"price": 90.0})
    assert out.json()["closed"][0]["exit_price"] == 102.5


# --- Telegram ----------------------------------------------------------------------------------------------------------

@pytest.fixture
def _tg(monkeypatch):
    from tests.test_phase_be_telegram_inbound import _Telegram, _no_shared_rate_limit  # noqa: F401
    from app.telegram_inbound import service as tg

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

    # /exit closes that PAPER position at once; a LIVE one is refused over Telegram.
    reply = _post(t, _message("987654", f"/exit {row.trade_id}")).json()["reply"]
    assert reply.startswith(f"Closed #{row.trade_id} at 100.10") and _get(TradeRecord, row.trade_id).exit_reason == "Exit now (telegram)"
    live = _open_trade(t, mode="LIVE")
    assert "never over Telegram" in _post(t, _message("987654", f"/exit {live}")).json()["reply"]
    assert _get(TradeRecord, live).exit_time is None
    assert "Usage" in _post(t, _message("987654", "/exit abc")).json()["reply"]
