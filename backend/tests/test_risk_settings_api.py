from app.core.models import RiskConfig
from tests.test_auth_api import _register, client


def test_risk_settings_requires_authentication():
    assert client.get("/api/risk-settings").status_code in (401, 403)
    assert client.put("/api/risk-settings", json=RiskConfig().model_dump()).status_code in (401, 403)


def test_get_risk_settings_returns_default_when_never_customized():
    token = _register("alice2@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    response = client.get("/api/risk-settings", headers=headers)
    assert response.status_code == 200
    assert response.json() == RiskConfig().model_dump()


def test_put_then_get_risk_settings_round_trips():
    token = _register("bob2@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    custom = RiskConfig(
        capital=250_000.0, risk_per_trade_pct=1.0, max_daily_loss_pct=2.0,
        max_trades_per_day=10, max_open_positions=2, max_consecutive_losses=3,
        min_risk_reward=1.5, lot_size=25,
    )
    put_response = client.put("/api/risk-settings", headers=headers, json=custom.model_dump())
    assert put_response.status_code == 200
    assert put_response.json() == custom.model_dump()

    get_response = client.get("/api/risk-settings", headers=headers)
    assert get_response.json() == custom.model_dump()


def test_risk_settings_are_private_per_user():
    token_a = _register("carol2@example.com")
    token_b = _register("dave2@example.com")

    client.put(
        "/api/risk-settings", headers={"Authorization": f"Bearer {token_a}"},
        json=RiskConfig(capital=999_999.0).model_dump(),
    )
    b_settings = client.get("/api/risk-settings", headers={"Authorization": f"Bearer {token_b}"}).json()
    assert b_settings["capital"] == RiskConfig().capital
