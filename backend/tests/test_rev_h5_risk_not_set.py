"""REV-H5 (docs/design/REV1_PLAN.md M1; DEF-0): a limit the trader leaves "Not set" is saved as null and stays null; the
Risk limits page sends null for a blank field. The API accepts it (no limit, no gate) next to limits that are set."""
from tests.test_auth_api import client
from tests.test_phase_m_closure import _owner

NULLABLE = ("capital", "risk_per_trade_pct", "max_daily_loss_pct", "max_trades_per_day", "max_open_positions",
            "max_consecutive_losses", "max_portfolio_risk_pct", "stop_cooldown_minutes", "dd_level_1_pct", "dd_level_2_pct",
            "event_size_cut_pct")


def test_not_set_limits_save_as_null_next_to_one_that_is_set():
    headers, _ = _owner("rev-h5@example.com")
    body = {**{k: None for k in NULLABLE}, "max_daily_loss_pct": 2.0, "min_risk_reward": 0.0, "lot_size": 1, "loss_streak_pause": "next_session"}
    res = client.put("/api/risk-settings", headers=headers, json=body)
    assert res.status_code == 200, res.text
    back = client.get("/api/risk-settings", headers=headers).json()
    assert back["max_daily_loss_pct"] == 2.0
    assert all(back[k] is None for k in NULLABLE if k != "max_daily_loss_pct")
