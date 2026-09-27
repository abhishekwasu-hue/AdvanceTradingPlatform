"""Phase I2: broker accounts - labelled credentials, account rows, sync, enable/disable,
default routing, deployment validation and the worker's per-account adapters."""
import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.brokers.models import BrokerPosition, BrokerProfile, MarginInfo
from app.db.models import BrokerAccountRecord, BrokerCredentialRecord, StrategyDeploymentRecord
from tests.test_auth_api import _session_factory, client
from tests.test_deployments_api import _auth, _create, _me, _store_broker
from tests.test_trading_worker import OPEN_NOW, _FakeBroker, _deploy, _force_signal, _get, _signal, _tenant, _trades, _worker


def _run(coro):
    return asyncio.run(coro)


def _mark_valid(tenant_id, label="primary", name="upstox"):
    async def go():
        async with _session_factory() as session:
            record = await session.scalar(select(BrokerCredentialRecord).where(
                BrokerCredentialRecord.tenant_id == tenant_id, BrokerCredentialRecord.broker_name == name,
                BrokerCredentialRecord.account_label == label))
            record.token_status = "VALID"
            record.token_expires_at = datetime.now(timezone.utc) + timedelta(hours=8)
            record.last_verified_at = datetime.now(timezone.utc)
            await session.commit()
    _run(go())


class _AccountBroker(_FakeBroker):
    def __init__(self, ident="U123"):
        super().__init__()
        self.ident = ident

    async def get_profile(self):
        return BrokerProfile(broker="upstox", user_id=self.ident)

    async def get_margins(self):
        return MarginInfo(available_cash=250_000.0, used_margin=40_000.0, available_margin=210_000.0)

    async def get_positions(self):
        return [BrokerPosition(symbol="RELIANCE", quantity=10, average_price=100.0, pnl=350.0),
                BrokerPosition(symbol="TCS", quantity=0, average_price=0.0, pnl=-120.0)]


def test_credentials_with_labels_create_accounts_and_sync(monkeypatch):
    headers = _auth("acct-owner@example.com")
    me = _me(headers)
    assert client.post("/api/broker/upstox/credentials", headers=headers, json={"api_key": "k", "api_secret": "s", "access_token": "t"}).status_code == 204
    assert client.post("/api/broker/upstox/credentials?account_label=hedge", headers=headers, json={"api_key": "k2", "api_secret": "s2", "access_token": "t2"}).status_code == 204
    assert client.post("/api/broker/upstox/credentials?account_label=bad label!", headers=headers, json={"api_key": "k"}).status_code == 400

    stored = client.get("/api/broker/credentials", headers=headers).json()
    assert sorted(s["account_label"] for s in stored) == ["hedge", "primary"]
    statuses = client.get("/api/broker/token-status", headers=headers).json()
    assert {s["account_label"] for s in statuses} == {"hedge", "primary"}

    accounts = client.get("/api/accounts", headers=headers).json()
    assert [(a["account_label"], a["is_default"], a["status"]) for a in accounts] == [("primary", True, "ACTIVE"), ("hedge", False, "ACTIVE")]
    hedge = next(a for a in accounts if a["account_label"] == "hedge")

    # Sync needs a usable token; then it pulls balance/margin/pnl through the account's own credential.
    assert client.post(f"/api/accounts/{hedge['id']}/sync", headers=headers).status_code == 409
    _mark_valid(me["tenant_id"], "hedge")
    monkeypatch.setattr("app.accounts.routes.build_adapter", lambda record, client=None: _AccountBroker(ident="HEDGE-1"))
    synced = client.post(f"/api/accounts/{hedge['id']}/sync", headers=headers).json()
    assert synced["available_balance"] == 210_000.0 and synced["used_margin"] == 40_000.0
    assert synced["unrealized_pnl"] == 350.0 and synced["realized_pnl"] == -120.0 and synced["broker_account_identifier"] == "HEDGE-1"
    assert synced["token_status"] == "VALID" and synced["last_sync_at"]

    # Default and status changes.
    made = client.post(f"/api/accounts/{hedge['id']}/default", headers=headers).json()
    assert made["is_default"] is True
    assert [a["is_default"] for a in client.get("/api/accounts", headers=headers).json()] == [True, False]  # hedge now first
    disabled = client.post(f"/api/accounts/{hedge['id']}/disable", headers=headers).json()
    assert disabled["status"] == "DISABLED"
    assert client.patch(f"/api/accounts/{hedge['id']}", headers=headers, json={"display_name": "Hedge book"}).json()["display_name"] == "Hedge book"
    # Another tenant cannot see or touch it.
    other = _auth("acct-other@example.com")
    assert client.get("/api/accounts", headers=other).json() == []
    assert client.post(f"/api/accounts/{hedge['id']}/enable", headers=other).status_code == 404

    # Deleting the labelled credential leaves the primary in place.
    assert client.delete("/api/broker/upstox/credentials?account_label=hedge", headers=headers).status_code == 204
    assert [s["account_label"] for s in client.get("/api/broker/credentials", headers=headers).json()] == ["primary"]


def test_live_deployment_routes_to_an_account_and_refuses_a_disabled_one():
    headers = _auth("acct-deploy@example.com")
    me = _me(headers)
    _store_broker(headers, token_status="VALID")
    assert client.post("/api/broker/upstox/credentials?account_label=second", headers=headers, json={"api_key": "k", "api_secret": "s", "access_token": "t"}).status_code == 204
    accounts = client.get("/api/accounts", headers=headers).json()
    second = next(a for a in accounts if a["account_label"] == "second")

    # The second account's token is UNKNOWN -> LIVE refused with the usual message.
    resp = _create(headers, mode="LIVE", broker_name="upstox", broker_account_id=second["id"])
    assert resp.status_code == 409 and "log in" in resp.json()["detail"].lower()
    _mark_valid(me["tenant_id"], "second")
    resp = _create(headers, mode="LIVE", broker_name="upstox", broker_account_id=second["id"])
    assert resp.status_code == 201, resp.text
    assert resp.json()["broker_account_id"] == second["id"] and resp.json()["broker_name"] == "upstox"

    client.post(f"/api/accounts/{second['id']}/disable", headers=headers)
    resp = _create(headers, mode="LIVE", broker_name="upstox", broker_account_id=second["id"], symbol="TCS")
    assert resp.status_code == 409 and "DISABLED" in resp.json()["detail"]
    assert _create(headers, mode="LIVE", broker_name="upstox", broker_account_id=999999, symbol="TCS").status_code == 404
    assert _create(headers, mode="LIVE", broker_name="zerodha", broker_account_id=second["id"], symbol="TCS").status_code == 400


def test_worker_uses_the_routed_accounts_adapter_and_skips_a_disabled_account(monkeypatch):
    from app.brokers import token_lifecycle
    from app.workers import trading_worker as tw

    t = _tenant("acct-worker@example.com")
    assert client.post("/api/broker/upstox/credentials?account_label=second", headers=t["headers"],
                       json={"api_key": "k", "api_secret": "s", "access_token": "t"}).status_code == 204
    _mark_valid(t["tenant_id"], "second")
    accounts = client.get("/api/accounts", headers=t["headers"]).json()
    second = next(a for a in accounts if a["account_label"] == "second")
    dep_id = _deploy(t, mode="LIVE")

    async def route():
        async with _session_factory() as session:
            dep = await session.get(StrategyDeploymentRecord, dep_id)
            dep.broker_account_id = second["id"]
            await session.commit()
    _run(route())

    primary, secondary = _FakeBroker(ltp=101.0), _FakeBroker(ltp=101.0)
    by_label = {"primary": primary, "second": secondary}
    monkeypatch.setattr(tw, "build_adapter", lambda record, client=None: by_label[record.account_label or "primary"])
    monkeypatch.setattr(token_lifecycle, "build_adapter", lambda record, client=None: by_label[record.account_label or "primary"])
    monkeypatch.setattr("app.execution.router.OrderRouter.fill_poll_delay_seconds", 0)
    worker = _worker(monkeypatch, primary)
    monkeypatch.setattr(tw, "build_adapter", lambda record, client=None: by_label[record.account_label or "primary"])
    monkeypatch.setattr(token_lifecycle, "build_adapter", lambda record, client=None: by_label[record.account_label or "primary"])
    _force_signal(monkeypatch, _signal)

    report = _run(worker.run_cycle(now=OPEN_NOW))
    assert report.signals_executed == 1, report.errors
    assert [o.order_type for o in secondary.placed] == ["MARKET", "SL-M"] and primary.placed == []
    assert _trades(t["tenant_id"])[0].mode == "LIVE"

    client.post(f"/api/accounts/{second['id']}/disable", headers=t["headers"])
    from datetime import timedelta
    from tests.test_trading_worker import BAR_TS
    _force_signal(monkeypatch, lambda: _signal(ts=BAR_TS + timedelta(minutes=1)))
    # Close the open position first so the second entry is not blocked by "one position per deployment".
    async def close_all():
        async with _session_factory() as session:
            for tr in await session.scalars(select(__import__("app.db.models", fromlist=["TradeRecord"]).TradeRecord).where(
                    __import__("app.db.models", fromlist=["TradeRecord"]).TradeRecord.tenant_id == t["tenant_id"])):
                tr.exit_time = datetime.now(timezone.utc); tr.exit_price = 101.0; tr.pnl = 0.0; tr.exit_reason = "test"
            await session.commit()
    _run(close_all())
    report = _run(worker.run_cycle(now=OPEN_NOW + timedelta(minutes=1)))
    assert report.signals_executed == 0
    assert "DISABLED" in _get(StrategyDeploymentRecord, dep_id).last_error and len(secondary.placed) == 2
