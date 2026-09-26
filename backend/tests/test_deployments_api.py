"""Phase A7: the deployments control surface and the market-holidays admin API."""
import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.models import BrokerCredentialRecord, StrategyDeploymentRecord, TradeRecord, User
from tests.test_auth_api import _register, _session_factory, client

STRATEGY = "ema_rsi_scalper_1m"


def _run(coro):
    return asyncio.run(coro)


def _upgrade_plan(tenant_id: int, plan: str = "business") -> None:
    """Free tenants are paper-only with one member (Phase B2); these tests exercise LIVE, teams
    and multiple channels, which are Pro/Business features."""
    from app.db.models import Tenant

    async def go():
        async with _session_factory() as session:
            tenant = await session.get(Tenant, tenant_id)
            tenant.plan = plan
            await session.commit()
    asyncio.run(go())


def _auth(email: str):
    headers = {"Authorization": f"Bearer {_register(email)}"}
    _upgrade_plan(client.get("/api/auth/me", headers=headers).json()["tenant_id"])
    return headers


def _me(headers):
    return client.get("/api/auth/me", headers=headers).json()


def _store_broker(headers, name="upstox", token_status=None):
    assert client.post(f"/api/broker/{name}/credentials", headers=headers, json={"api_key": "k", "api_secret": "s", "access_token": "t"}).status_code == 204
    if token_status is not None:
        tenant_id = _me(headers)["tenant_id"]

        async def mark():
            async with _session_factory() as session:
                record = await session.scalar(select(BrokerCredentialRecord).where(
                    BrokerCredentialRecord.tenant_id == tenant_id, BrokerCredentialRecord.broker_name == name))
                record.token_status = token_status
                record.token_expires_at = datetime.now(timezone.utc) + timedelta(hours=8) if token_status == "VALID" else None
                await session.commit()
        _run(mark())


def _create(headers, **overrides):
    body = {"strategy_id": STRATEGY, "symbol": "reliance", **overrides}
    return client.post("/api/deployments", headers=headers, json=body)


def test_deployments_require_auth():
    assert client.get("/api/deployments").status_code in (401, 403)
    assert client.post("/api/deployments", json={"strategy_id": STRATEGY, "symbol": "X"}).status_code in (401, 403)


def test_paper_deployment_needs_a_stored_broker_for_market_data():
    headers = _auth("dep-nobroker@example.com")
    response = _create(headers)
    assert response.status_code == 409
    assert "broker credentials" in response.json()["detail"].lower()


def test_create_paper_deployment_autofills_single_stored_broker_and_audits():
    headers = _auth("dep-paper@example.com")
    _store_broker(headers)

    response = _create(headers)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "ACTIVE" and body["mode"] == "PAPER"
    assert body["symbol"] == "RELIANCE" and body["exchange"] == "NSE" and body["timeframe"] == "1min"
    assert body["broker_name"] == "upstox"
    assert body["open_positions"] == 0 and body["consecutive_failures"] == 0

    logs = client.get("/api/audit-logs", headers=headers).json()
    assert any(l["event"] == "deployment_created" and "RELIANCE" in l["detail"] for l in logs)


def test_duplicate_deployment_is_rejected():
    headers = _auth("dep-dup@example.com")
    _store_broker(headers)
    assert _create(headers).status_code == 201
    assert _create(headers).status_code == 409
    # Same strategy/symbol in the *other* mode is a different deployment.
    _store_broker(headers, token_status="VALID")
    assert _create(headers, mode="LIVE", broker_name="upstox").status_code == 201


def test_unknown_strategy_is_404():
    headers = _auth("dep-badstrat@example.com")
    _store_broker(headers)
    assert _create(headers, strategy_id="does_not_exist").status_code == 404


def test_base_timeframe_must_build_every_strategy_timeframe():
    headers = _auth("dep-tf@example.com")
    _store_broker(headers)
    # ema_rsi_scalper_1m consumes 1-minute candles: a 5-minute base cannot produce them.
    response = _create(headers, timeframe="5min")
    assert response.status_code == 400 and "cannot build" in response.json()["detail"]
    assert _create(headers, timeframe="2min").status_code == 400  # not a supported base at all
    # A 5-minute strategy from a 1-minute base is fine (resampled up).
    assert _create(headers, strategy_id="rsi_adx_momentum_5m", timeframe="1min").status_code == 201


def test_live_deployment_requires_named_broker_with_valid_token():
    headers = _auth("dep-live@example.com")
    _store_broker(headers)  # token UNKNOWN
    assert _create(headers, mode="LIVE").status_code == 400  # broker_name missing
    blocked = _create(headers, mode="LIVE", broker_name="upstox")
    assert blocked.status_code == 409 and "log in" in blocked.json()["detail"].lower()

    _store_broker(headers, token_status="VALID")
    ok = _create(headers, mode="LIVE", broker_name="upstox")
    assert ok.status_code == 201 and ok.json()["mode"] == "LIVE"


def test_live_deployment_with_unstored_broker_is_409():
    headers = _auth("dep-live-nostore@example.com")
    _store_broker(headers)
    assert _create(headers, mode="LIVE", broker_name="zerodha").status_code == 409
    assert _create(headers, mode="LIVE", broker_name="not_a_broker").status_code == 404


def test_pause_resume_stop_delete_lifecycle():
    headers = _auth("dep-lifecycle@example.com")
    _store_broker(headers)
    dep_id = _create(headers).json()["id"]

    paused = client.post(f"/api/deployments/{dep_id}/pause", headers=headers, json={"reason": "lunch"})
    assert paused.status_code == 200 and paused.json()["status"] == "PAUSED"
    assert "lunch" in paused.json()["pause_reason"]

    resumed = client.post(f"/api/deployments/{dep_id}/resume", headers=headers)
    assert resumed.status_code == 200 and resumed.json()["status"] == "ACTIVE" and resumed.json()["pause_reason"] is None

    assert client.delete(f"/api/deployments/{dep_id}", headers=headers).status_code == 409  # must stop first

    stopped = client.post(f"/api/deployments/{dep_id}/stop", headers=headers, json={"reason": "done"})
    assert stopped.status_code == 200 and stopped.json()["status"] == "STOPPED"
    assert client.post(f"/api/deployments/{dep_id}/resume", headers=headers).status_code == 409
    assert client.post(f"/api/deployments/{dep_id}/pause", headers=headers, json={}).status_code == 409

    listed = client.get("/api/deployments", headers=headers).json()
    assert all(d["id"] != dep_id for d in listed)
    listed_all = client.get("/api/deployments?include_stopped=true", headers=headers).json()
    assert any(d["id"] == dep_id for d in listed_all)

    assert client.delete(f"/api/deployments/{dep_id}", headers=headers).status_code == 204
    assert client.get(f"/api/deployments/{dep_id}", headers=headers).status_code == 404

    events = [l["event"] for l in client.get("/api/audit-logs", headers=headers).json()]
    for expected in ("deployment_created", "deployment_paused", "deployment_resumed", "deployment_stopped", "deployment_deleted"):
        assert expected in events


def test_resume_live_deployment_rechecks_token():
    headers = _auth("dep-live-resume@example.com")
    _store_broker(headers, token_status="VALID")
    dep_id = _create(headers, mode="LIVE", broker_name="upstox").json()["id"]
    client.post(f"/api/deployments/{dep_id}/pause", headers=headers, json={})
    _store_broker(headers, token_status="EXPIRED")
    blocked = client.post(f"/api/deployments/{dep_id}/resume", headers=headers)
    assert blocked.status_code == 409 and "EXPIRED" in blocked.json()["detail"]


def test_deployments_are_tenant_isolated():
    a = _auth("dep-tenant-a@example.com")
    b = _auth("dep-tenant-b@example.com")
    _store_broker(a)
    dep_id = _create(a).json()["id"]

    assert client.get("/api/deployments", headers=b).json() == []
    assert client.get(f"/api/deployments/{dep_id}", headers=b).status_code == 404
    assert client.post(f"/api/deployments/{dep_id}/stop", headers=b, json={}).status_code == 404
    assert client.delete(f"/api/deployments/{dep_id}", headers=b).status_code == 404


def test_open_position_count_is_reported_per_deployment():
    headers = _auth("dep-open@example.com")
    _store_broker(headers)
    dep_id = _create(headers).json()["id"]
    me = _me(headers)

    async def seed():
        async with _session_factory() as session:
            session.add(TradeRecord(
                tenant_id=me["tenant_id"], user_id=me["id"], mode="PAPER", symbol="RELIANCE", strategy_id=STRATEGY,
                direction="LONG", entry_time=datetime.now(timezone.utc), entry_price=100, quantity=10,
                stop_loss=98, target1=104, deployment_id=dep_id,
            ))
            await session.commit()
    _run(seed())

    assert client.get(f"/api/deployments/{dep_id}", headers=headers).json()["open_positions"] == 1


def test_support_role_is_read_only():
    headers = _auth("dep-support@example.com")
    _store_broker(headers)
    me = _me(headers)

    async def demote():
        async with _session_factory() as session:
            user = await session.get(User, me["id"])
            user.role = "SUPPORT"
            await session.commit()
    _run(demote())

    assert _create(headers).status_code == 403
    assert client.get("/api/deployments", headers=headers).status_code == 200


# --- market holidays --------------------------------------------------------------------------

def test_market_holidays_admin_api():
    headers = _auth("holiday-user@example.com")
    assert client.get("/api/market-holidays").status_code in (401, 403)
    assert client.get("/api/market-holidays?year=2026", headers=headers).status_code == 200
    body = {"exchange": "nse", "holiday_date": "2027-01-26", "description": "Republic Day"}
    assert client.post("/api/market-holidays", headers=headers, json=body).status_code == 403  # plain user

    me = _me(headers)

    async def promote():
        async with _session_factory() as session:
            user = await session.get(User, me["id"])
            user.role = "SUPER_ADMIN"
            await session.commit()
    _run(promote())

    created = client.post("/api/market-holidays", headers=headers, json=body)
    assert created.status_code == 201 and created.json()["exchange"] == "NSE"
    assert client.post("/api/market-holidays", headers=headers, json=body).status_code == 409
    listed = client.get("/api/market-holidays?year=2027", headers=headers).json()
    assert [h["holiday_date"] for h in listed] == ["2027-01-26"]
    assert client.delete(f"/api/market-holidays/{created.json()['id']}", headers=headers).status_code == 204
    assert client.get("/api/market-holidays?year=2027", headers=headers).json() == []
