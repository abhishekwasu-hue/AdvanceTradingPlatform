"""Phase B2: plan limits (deployments, LIVE entitlement, custom strategies, members, alert
channels) and suspended-tenant enforcement across the API gate, the execution pipeline and the
worker."""
import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.models import BrokerCredentialRecord, StrategyDeploymentRecord, Tenant, User
from app.plans.registry import PLANS, get_plan
from tests.test_auth_api import _register, _session_factory, client

STRATEGY = "ema_rsi_scalper_1m"


def _run(coro):
    return asyncio.run(coro)


def _owner(email: str, plan="free"):
    headers = {"Authorization": f"Bearer {_register(email)}"}
    me = client.get("/api/auth/me", headers=headers).json()
    if plan != "free":
        _set_tenant(me["tenant_id"], plan=plan)
    return headers, me


def _set_tenant(tenant_id: int, **fields):
    async def go():
        async with _session_factory() as session:
            tenant = await session.get(Tenant, tenant_id)
            for k, v in fields.items():
                setattr(tenant, k, v)
            await session.commit()
    _run(go())


def _store_broker(headers, valid=True):
    client.post("/api/broker/upstox/credentials", headers=headers, json={"api_key": "k", "api_secret": "s", "access_token": "t"})
    if valid:
        me = client.get("/api/auth/me", headers=headers).json()

        async def mark():
            async with _session_factory() as session:
                rec = await session.scalar(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == me["tenant_id"]))
                rec.token_status = "VALID"
                rec.token_expires_at = datetime.now(timezone.utc) + timedelta(hours=8)
                await session.commit()
        _run(mark())


def _deploy(headers, symbol, mode="PAPER"):
    body = {"strategy_id": STRATEGY, "symbol": symbol, "mode": mode}
    if mode == "LIVE":
        body["broker_name"] = "upstox"
    return client.post("/api/deployments", headers=headers, json=body)


def test_plan_registry_falls_back_to_free_for_unknown_ids():
    assert get_plan("free").live_trading is False and get_plan("pro").live_trading is True
    assert get_plan("nonsense") is PLANS["free"] and get_plan("") is PLANS["free"]
    assert get_plan("BUSINESS").max_active_deployments == 50


def test_tenant_endpoint_reports_plan_limits_and_usage():
    headers, _ = _owner("plan-info@example.com")
    body = client.get("/api/team/tenant", headers=headers).json()
    assert body["plan"] == "free" and body["plan_name"] == "Free"
    assert body["limits"]["active_deployments"] == 2 and body["limits"]["live_trading"] is False
    assert body["usage"] == {"active_deployments": 0, "custom_strategies": 0, "members": 1, "alert_channels": 0}


def test_free_plan_caps_active_deployments_and_stopped_ones_free_a_slot():
    headers, _ = _owner("plan-deps@example.com")
    _store_broker(headers)
    assert _deploy(headers, "A").status_code == 201
    second = _deploy(headers, "B")
    assert second.status_code == 201
    third = _deploy(headers, "C")
    assert third.status_code == 402 and "allows 2 active deployment" in third.json()["detail"]
    # Paused still counts; stopped frees the slot.
    client.post(f"/api/deployments/{second.json()['id']}/pause", headers=headers, json={})
    assert _deploy(headers, "C").status_code == 402
    client.post(f"/api/deployments/{second.json()['id']}/stop", headers=headers, json={})
    assert _deploy(headers, "C").status_code == 201
    assert client.get("/api/team/tenant", headers=headers).json()["usage"]["active_deployments"] == 2


def test_free_plan_refuses_live_and_pro_allows_it():
    headers, me = _owner("plan-live@example.com")
    _store_broker(headers)
    refused = _deploy(headers, "NIFTY 50", mode="LIVE")
    assert refused.status_code == 402 and "paper trading only" in refused.json()["detail"]
    _set_tenant(me["tenant_id"], plan="pro")
    assert _deploy(headers, "NIFTY 50", mode="LIVE").status_code == 201


def test_downgrade_blocks_resuming_a_paused_live_deployment():
    headers, me = _owner("plan-downgrade@example.com", plan="pro")
    _store_broker(headers)
    dep_id = _deploy(headers, "X", mode="LIVE").json()["id"]
    client.post(f"/api/deployments/{dep_id}/pause", headers=headers, json={})
    _set_tenant(me["tenant_id"], plan="free")
    assert client.post(f"/api/deployments/{dep_id}/resume", headers=headers).status_code == 402
    # A paper one on the same (now over-quota) tenant can still be resumed: resume never adds a slot.
    paper_id = _deploy(headers, "Y").json()["id"]  # free allows 2 active: the paused LIVE + this
    client.post(f"/api/deployments/{paper_id}/pause", headers=headers, json={})
    assert client.post(f"/api/deployments/{paper_id}/resume", headers=headers).status_code == 200


def test_custom_strategy_limit():
    headers, _ = _owner("plan-strats@example.com")
    cond = {"left": {"type": "indicator", "value": 50, "indicator": "RSI", "period": 14, "multiplier": 3.0}, "operator": "GT",
            "right": {"type": "value", "value": 50, "indicator": "RSI", "period": 14, "multiplier": 3.0}}
    body = {"name": "s", "timeframe": "1min", "long_conditions": [cond], "short_conditions": [], "stop_loss_atr_mult": 1, "atr_period": 14, "target_rr": [1.5, 2], "min_rr": 1.2}
    codes = [client.post("/api/custom-strategies", headers=headers, json={**body, "name": f"s{i}"}).status_code for i in range(4)]
    assert codes == [201, 201, 201, 402]


def test_member_limit_counts_open_invites():
    headers, _ = _owner("plan-members@example.com")  # free: 1 member = the owner
    blocked = client.post("/api/team/invites", headers=headers, json={"email": "more@example.com", "role": "USER"})
    assert blocked.status_code == 402 and "team member" in blocked.json()["detail"]
    headers_pro, _ = _owner("plan-members-pro@example.com", plan="pro")
    assert client.post("/api/team/invites", headers=headers_pro, json={"email": "more2@example.com", "role": "USER"}).status_code == 201


def test_alert_channel_limit_applies_to_new_channels_only():
    headers, _ = _owner("plan-alerts@example.com")
    tg = {"config": {"bot_token": "123456:ABCDEFghij", "chat_id": "1"}}
    assert client.put("/api/alert-channels/telegram", headers=headers, json=tg).status_code == 200
    assert client.put("/api/alert-channels/telegram", headers=headers, json={**tg, "min_severity": "CRITICAL"}).status_code == 200  # update is fine
    email = {"config": {"smtp_host": "h", "from_address": "a@b.com", "to_addresses": ["c@d.com"]}}
    assert client.put("/api/alert-channels/email", headers=headers, json=email).status_code == 402


def test_suspended_tenant_keeps_read_access_but_no_writes():
    headers, me = _owner("plan-susp@example.com")
    _store_broker(headers)
    dep_id = _deploy(headers, "A").json()["id"]
    _set_tenant(me["tenant_id"], status="suspended")

    assert client.get("/api/deployments", headers=headers).status_code == 200
    assert client.get("/api/positions", headers=headers).status_code == 200
    assert client.get("/api/team/members", headers=headers).status_code == 200
    blocked = _deploy(headers, "B")
    assert blocked.status_code == 403 and "suspended" in blocked.json()["detail"]
    assert client.post(f"/api/deployments/{dep_id}/pause", headers=headers, json={}).status_code == 403
    assert client.post("/api/team/invites", headers=headers, json={"email": "x@example.com", "role": "USER"}).status_code == 403
    assert client.put("/api/risk-settings", headers=headers, json=client.get("/api/risk-settings", headers=headers).json()).status_code == 403


def test_suspended_tenant_orders_are_rejected_in_the_pipeline():
    from app.core.enums import SignalDirection, SignalGrade
    from app.core.models import Signal
    from app.execution.signal_execution import execute_signal_for_user

    headers, me = _owner("plan-susp-exec@example.com")
    _set_tenant(me["tenant_id"], status="suspended")

    async def go():
        async with _session_factory() as session:
            user = await session.get(User, me["id"])
            signal = Signal(symbol="RELIANCE", strategy_id=STRATEGY, strategy_name="x", direction=SignalDirection.LONG,
                            timestamp=datetime.now(timezone.utc), entry=100, stop_loss=98, target1=104, risk_reward=2,
                            score=80, grade=SignalGrade.HIGH_QUALITY, reasons=[], timeframe_combo="1min")
            return await execute_signal_for_user(session, user, mode="PAPER", strategy_id=STRATEGY, signal=signal)
    result, order = _run(go())
    assert not result.executed and order.status == "REJECTED"
    assert any("suspended" in r for r in result.reasons)


def test_worker_skips_entries_for_suspended_tenant_and_live_without_entitlement(monkeypatch):
    from tests import test_trading_worker as w

    t = w._tenant("plan-worker@example.com")
    _set_tenant(t["tenant_id"], plan="pro")
    live_id = w._deploy(t, mode="LIVE")
    broker = w._FakeBroker(ltp=101.0)
    worker = w._worker(monkeypatch, broker)
    w._force_signal(monkeypatch, w._signal)
    monkeypatch.setattr("app.execution.router.OrderRouter.fill_poll_delay_seconds", 0)

    # Plan downgraded after deployment: the worker must not fire the LIVE entry.
    _set_tenant(t["tenant_id"], plan="free")
    report = _run(worker.run_cycle(now=w.OPEN_NOW))
    assert report.signals_executed == 0 and broker.placed == []
    assert "live trading" in w._get(StrategyDeploymentRecord, live_id).last_error

    # Suspended: no entries at all, but the row explains why.
    _set_tenant(t["tenant_id"], plan="pro", status="suspended")
    report = _run(worker.run_cycle(now=w.OPEN_NOW + timedelta(minutes=1)))
    assert report.signals_executed == 0 and broker.placed == []
    assert "suspended" in w._get(StrategyDeploymentRecord, live_id).last_error
