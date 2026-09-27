"""Phase G3: the V4.9 health aliases (live/ready/dependencies) and ending a broker session on
purpose (BrokerInterface.disconnect + POST /api/broker/{name}/disconnect)."""
import asyncio

from sqlalchemy import select

from app.brokers import token_lifecycle
from app.brokers.circuit_breaker import breaker_for, reset_all
from app.db.models import AuditLogRecord, BrokerCredentialRecord
from tests.test_auth_api import _register, _session_factory, client
from tests.test_trading_worker import _FakeBroker


def test_health_aliases_answer_like_their_originals():
    assert client.get("/api/system/health/live").json() == {"status": "ok"}
    ready = client.get("/api/system/health/ready")
    assert ready.status_code == 200 and ready.json() == {"status": "ready"}
    assert client.get("/api/v1/system/health/live").status_code == 200  # versioned alias still applies


def test_dependencies_view_includes_circuit_breakers_and_reconciliation_state():
    reset_all()
    breaker_for("upstox")
    body = client.get("/api/system/health/dependencies").json()
    assert body["checks"]["database"]["status"] == "ok"
    assert body["checks"]["broker_circuits"]["upstox"]["state"] == "CLOSED"
    assert isinstance(body["checks"]["broker_uncertain_tenants"], int)

    b = breaker_for("upstox")
    b.min_calls = 1
    b.record_failure("boom")
    body = client.get("/api/system/health/dependencies").json()
    assert body["checks"]["broker_circuits"]["upstox"]["state"] == "OPEN" and body["status"] == "degraded"
    reset_all()


class _DisconnectingBroker(_FakeBroker):
    def __init__(self):
        super().__init__()
        self._access_token = "t"
        self.logged_out = 0

    async def disconnect(self):
        self.logged_out += 1
        self._access_token = None


def test_disconnect_revokes_the_broker_session_and_marks_it_expired(monkeypatch):
    token = _register("g-disconnect@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    assert client.post("/api/broker/upstox/credentials", headers=headers,
                       json={"api_key": "k", "api_secret": "s", "access_token": "t"}).status_code == 204
    broker = _DisconnectingBroker()
    monkeypatch.setattr("app.brokers.routes.build_adapter", lambda record, client=None: broker)

    assert client.post("/api/broker/upstox/disconnect", headers=headers).status_code == 204
    assert broker.logged_out == 1

    async def state():
        async with _session_factory() as session:
            record = await session.scalar(select(BrokerCredentialRecord).where(BrokerCredentialRecord.broker_name == "upstox")
                                          .order_by(BrokerCredentialRecord.id.desc()))
            logs = list(await session.scalars(select(AuditLogRecord).where(AuditLogRecord.tenant_id == record.tenant_id)))
            return record, logs
    record, logs = asyncio.run(state())
    assert record.token_status == "EXPIRED" and record.token_expires_at is None
    assert not token_lifecycle.token_is_usable(record)
    assert any(l.event == "broker_disconnected" and "revoked at broker" in l.detail for l in logs)
    # The key and secret survive; only the session token is gone - a re-login needs no re-entry.
    statuses = client.get("/api/broker/token-status", headers=headers).json()
    assert statuses[0]["token_status"] == "EXPIRED" and statuses[0]["needs_login"] is True
    assert client.post("/api/broker/nothere/disconnect", headers=headers).status_code == 404


def test_default_disconnect_and_balance_on_the_interface():
    broker = _FakeBroker()
    broker._access_token = "abc"
    asyncio.run(broker.disconnect())
    assert broker.access_token is None

    class _WithMargins(_FakeBroker):
        async def get_margins(self):
            from app.brokers.models import MarginInfo
            return MarginInfo(available_cash=1000.0, available_margin=900.0)
    assert asyncio.run(_WithMargins().get_balance()).available_margin == 900.0
