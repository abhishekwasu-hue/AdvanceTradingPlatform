import asyncio
from datetime import datetime, timezone

from app.brokers.models import BrokerPosition
from app.db.models import TradeRecord, User
from tests.test_auth_api import _register, _session_factory, client


def _store_credentials(headers):
    stored = client.post(
        "/api/broker/zerodha/credentials", headers=headers,
        json={"api_key": "k", "access_token": "t"},
    )
    assert stored.status_code == 204, stored.text


def _seed_open_trade(token, symbol="NIFTY", direction="LONG", quantity=50):
    """A LIVE position the platform believes it holds. Reconciliation compares LIVE trades only
    (Phase G1): PAPER positions never exist at the broker, so they would always be "missing"."""
    from app.auth.security import decode_access_token
    user_id = int(decode_access_token(token)["sub"])

    async def _seed():
        async with _session_factory() as session:
            user = await session.get(User, user_id)
            session.add(TradeRecord(
                tenant_id=user.tenant_id, user_id=user.id, mode="LIVE", symbol=symbol, strategy_id="s",
                direction=direction, entry_time=datetime.now(timezone.utc), entry_price=100.0,
                quantity=quantity, stop_loss=98.0, target1=104.0,
            ))
            await session.commit()

    asyncio.run(_seed())


def test_reconciliation_requires_authentication():
    assert client.post("/api/reconciliation/zerodha").status_code in (401, 403)


def test_reconciliation_requires_stored_credentials():
    token = _register("recon_no_creds@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    response = client.post("/api/reconciliation/zerodha", headers=headers)
    assert response.status_code == 404


def test_reconciliation_rejects_unknown_broker():
    token = _register("recon_unknown@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    response = client.post("/api/reconciliation/not_a_real_broker", headers=headers)
    assert response.status_code == 404


def test_reconciliation_reports_matched_and_mismatched_positions(monkeypatch):
    token = _register("recon_owner@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    _store_credentials(headers)
    _seed_open_trade(token, symbol="NIFTY", direction="LONG", quantity=50)
    _seed_open_trade(token, symbol="MISSING", direction="LONG", quantity=10)

    class _FakeAdapter:
        async def get_positions(self):
            return [
                BrokerPosition(symbol="NIFTY", quantity=50, average_price=100.0),
                BrokerPosition(symbol="UNTRACKED", quantity=5, average_price=50.0),
            ]

    monkeypatch.setattr("app.reconciliation.routes.get_broker_adapter", lambda name, creds: _FakeAdapter())

    response = client.post("/api/reconciliation/zerodha", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["broker_name"] == "zerodha"
    by_symbol = {i["symbol"]: i for i in body["items"]}
    assert by_symbol["NIFTY"]["status"] == "MATCHED"
    assert by_symbol["MISSING"]["status"] == "MISSING_AT_BROKER"
    assert by_symbol["UNTRACKED"]["status"] == "UNTRACKED_AT_BROKER"
    assert body["mismatched_count"] == 2

    logs = client.get("/api/audit-logs", headers=headers).json()
    events = [entry["event"] for entry in logs]
    assert events.count("position_reconciliation_mismatch") == 2
    assert "position_reconciliation_run" in events


def test_reconciliation_broker_failure_is_audited_and_returns_502(monkeypatch):
    token = _register("recon_fail@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    _store_credentials(headers)

    class _FailingAdapter:
        async def get_positions(self):
            raise RuntimeError("network error")

    monkeypatch.setattr("app.reconciliation.routes.get_broker_adapter", lambda name, creds: _FailingAdapter())

    response = client.post("/api/reconciliation/zerodha", headers=headers)
    assert response.status_code == 502

    logs = client.get("/api/audit-logs", headers=headers).json()
    assert any(entry["event"] == "position_reconciliation_failed" for entry in logs)


def test_reconciliation_is_tenant_scoped(monkeypatch):
    token1 = _register("recon_tenant1@example.com")
    token2 = _register("recon_tenant2@example.com")
    headers1 = {"Authorization": f"Bearer {token1}"}
    headers2 = {"Authorization": f"Bearer {token2}"}
    _store_credentials(headers2)
    _seed_open_trade(token1, symbol="NIFTY", direction="LONG", quantity=50)

    class _FakeAdapter:
        async def get_positions(self):
            return []

    monkeypatch.setattr("app.reconciliation.routes.get_broker_adapter", lambda name, creds: _FakeAdapter())

    # tenant2 has credentials but no open trades of its own - tenant1's open NIFTY trade must
    # never leak into tenant2's reconciliation report.
    response = client.post("/api/reconciliation/zerodha", headers=headers2)
    assert response.status_code == 200
    assert response.json()["items"] == []
