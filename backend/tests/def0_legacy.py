"""DEF-0 (owner, 28 Oct): RiskConfig has no built-in limits any more. Tests written against the old platform defaults
set them explicitly here - the values a trader could set himself - so they keep testing the same rule."""
from app.core.models import RiskConfig

LEGACY_LIMITS = dict(capital=100_000.0, risk_per_trade_pct=0.5, max_daily_loss_pct=3.0, max_trades_per_day=20, max_open_positions=3,
                     max_consecutive_losses=4, max_portfolio_risk_pct=6.0, stop_cooldown_minutes=30, dd_level_1_pct=5.0,
                     dd_level_2_pct=10.0, event_size_cut_pct=50.0)


def legacy(**overrides) -> RiskConfig:
    """The old platform defaults, with `overrides` on top."""
    return RiskConfig(**{**LEGACY_LIMITS, **overrides})
