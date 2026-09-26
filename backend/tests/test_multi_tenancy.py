import asyncio

from app.auth.security import decode_access_token, hash_password
from app.db.models import User
from tests.test_auth_api import _register, _session_factory, client


def _rsi_config(name="Shared Strategy"):
    return {
        "name": name,
        "timeframe": "1min",
        "long_conditions": [
            {
                "left": {"type": "indicator", "indicator": "RSI", "period": 14},
                "operator": "CROSSES_ABOVE",
                "right": {"type": "value", "value": 50},
            }
        ],
        "short_conditions": [],
    }


def _user_id_and_tenant(token: str) -> tuple:
    user_id = int(decode_access_token(token)["sub"])

    async def _lookup():
        async with _session_factory() as session:
            user = await session.get(User, user_id)
            return user.tenant_id

    return user_id, asyncio.run(_lookup())


def _add_teammate(email: str, tenant_id: int) -> str:
    """Inserts a second user directly into an existing tenant (the invite flow does this for
    real - see tests/test_team_api.py) to exercise same-tenant sharing."""
    async def _create():
        async with _session_factory() as session:
            user = User(tenant_id=tenant_id, email=email, hashed_password=hash_password("S3cur3Pass!"), role="USER")
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user.id

    asyncio.run(_create())
    # Tokens are session-bound (Phase C1): log in for real rather than minting a bare JWT.
    response = client.post("/api/auth/login", json={"email": email, "password": "S3cur3Pass!"})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def test_register_creates_a_new_tenant_with_owner_role():
    token = _register("tenant_a_owner@example.com")
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert me["role"] == "OWNER"  # a registration creates the tenant and owns it (Phase B1)
    assert isinstance(me["tenant_id"], int)


def test_two_registrations_get_different_tenants():
    token1 = _register("tenant_b_owner@example.com")
    token2 = _register("tenant_c_owner@example.com")
    me1 = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token1}"}).json()
    me2 = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token2}"}).json()
    assert me1["tenant_id"] != me2["tenant_id"]


def test_custom_strategy_invisible_across_tenants():
    token1 = _register("tenant_d_owner@example.com")
    token2 = _register("tenant_e_owner@example.com")
    headers1 = {"Authorization": f"Bearer {token1}"}
    headers2 = {"Authorization": f"Bearer {token2}"}

    created = client.post("/api/custom-strategies", headers=headers1, json=_rsi_config())
    assert created.status_code == 201, created.text
    strategy_db_id = created.json()["id"]

    assert client.get("/api/custom-strategies", headers=headers2).json() == []
    assert client.get(f"/api/custom-strategies/{strategy_db_id}", headers=headers2).status_code == 404


def test_custom_strategy_visible_to_same_tenant_teammate():
    token1 = _register("tenant_f_owner@example.com")
    _user1, tenant1 = _user_id_and_tenant(token1)
    token2 = _add_teammate("tenant_f_teammate@example.com", tenant1)
    headers1 = {"Authorization": f"Bearer {token1}"}
    headers2 = {"Authorization": f"Bearer {token2}"}

    created = client.post("/api/custom-strategies", headers=headers1, json=_rsi_config())
    assert created.status_code == 201, created.text
    strategy_db_id = created.json()["id"]

    listing = client.get("/api/custom-strategies", headers=headers2).json()
    assert len(listing) == 1
    assert listing[0]["id"] == strategy_db_id
    assert client.get(f"/api/custom-strategies/{strategy_db_id}", headers=headers2).status_code == 200


def test_risk_settings_shared_within_tenant_not_across():
    token1 = _register("tenant_g_owner@example.com")
    _user1, tenant1 = _user_id_and_tenant(token1)
    token2 = _add_teammate("tenant_g_teammate@example.com", tenant1)
    other_tenant_token = _register("tenant_h_owner@example.com")
    headers1 = {"Authorization": f"Bearer {token1}"}
    headers2 = {"Authorization": f"Bearer {token2}"}
    other_headers = {"Authorization": f"Bearer {other_tenant_token}"}

    updated = client.put("/api/risk-settings", headers=headers1, json={
        "capital": 250_000.0, "risk_per_trade_pct": 1.0, "max_daily_loss_pct": 5.0,
        "max_trades_per_day": 10, "max_open_positions": 5, "max_consecutive_losses": 3,
        "min_risk_reward": 1.5, "lot_size": 1,
    })
    assert updated.status_code == 200, updated.text

    teammate_view = client.get("/api/risk-settings", headers=headers2).json()
    assert teammate_view["capital"] == 250_000.0

    other_tenant_view = client.get("/api/risk-settings", headers=other_headers).json()
    assert other_tenant_view["capital"] != 250_000.0


def test_broker_credentials_shared_within_tenant_not_across():
    token1 = _register("tenant_i_owner@example.com")
    _user1, tenant1 = _user_id_and_tenant(token1)
    token2 = _add_teammate("tenant_i_teammate@example.com", tenant1)
    other_tenant_token = _register("tenant_j_owner@example.com")
    headers1 = {"Authorization": f"Bearer {token1}"}
    headers2 = {"Authorization": f"Bearer {token2}"}
    other_headers = {"Authorization": f"Bearer {other_tenant_token}"}

    stored = client.post(
        "/api/broker/zerodha/credentials", headers=headers1,
        json={"api_key": "k", "api_secret": "s", "access_token": "t"},
    )
    assert stored.status_code == 204, stored.text

    assert len(client.get("/api/broker/credentials", headers=headers2).json()) == 1
    assert client.get("/api/broker/credentials", headers=other_headers).json() == []
