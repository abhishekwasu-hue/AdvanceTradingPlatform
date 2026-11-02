"""DEF-0 (owner, 28 Oct): RiskConfig has no built-in limits any more. Tests written against the old platform defaults
set them explicitly here - the values a trader could set himself - so they keep testing the same rule."""
from app.core.models import RiskConfig

LEGACY_LIMITS = dict(capital=100_000.0, risk_per_trade_pct=0.5, max_daily_loss_pct=3.0, max_trades_per_day=20, max_open_positions=3,
                     max_consecutive_losses=4, max_portfolio_risk_pct=6.0, stop_cooldown_minutes=30, dd_level_1_pct=5.0,
                     dd_level_2_pct=10.0, event_size_cut_pct=50.0,
                     # M64: the old built-ins that became switches - the market-event rules on, 80% of the available margin
                     event_rules=True, margin_safety_pct=80.0,
                     # M68 PR-5: the old built-ins that became the trader's own choices - the pause ends at the next
                     # session, the first drawdown step halves the size, two bots may not hold opposite sides
                     loss_streak_pause="next_session", dd_cut_pct=50.0, opposite_positions="deny")


def legacy(**overrides) -> RiskConfig:
    """The old platform defaults, with `overrides` on top."""
    return RiskConfig(**{**LEGACY_LIMITS, **overrides})


# M68 PR-5 (TS1-056): the India VIX check is each user's own switch and numbers (Settings > Trade settings > Risk limits);
# tests of the gate save the old operator numbers as the user's own: a 5 % rise pauses, a level of 20 cuts the size 50 %.
LEGACY_VIX = {"risk_limits.vix_gate": True, "risk_limits.vix_change_pct": 5.0, "risk_limits.vix_level": 20.0,
              "risk_limits.vix_size_cut_pct": 50.0}


def own_vix(client, headers, **values) -> None:
    """Saves the user's own India VIX check (LEGACY_VIX, with `values` on top, e.g. {"risk_limits.vix_level": None})."""
    body = {**LEGACY_VIX, **{f"risk_limits.{k}": v for k, v in values.items()}}
    out = client.put("/api/me/trade-settings", headers=headers, json={"values": body})
    assert out.status_code == 200, out.text
