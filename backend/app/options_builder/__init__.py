"""P1-a: the Options Strategy Builder core (ADR-0025), ported from the Trade repo as pure, typed functions.

Source: Trade@73f652c `strategy_payoff.py` (payoff, combined Greeks, breakevens, ready-made templates with the
hedge-first rule, the per-lot strategy result) and `strategy.py` (PoP-driven and rule-based strike selectors, position
sizing). Behaviour is kept bit-for-bit (golden fixtures in tests/fixtures/options_builder/); what changed is the
language of the docstrings and errors, types, and that instrument-specific numbers (strike step, hedge width, lot
size) are parameters the caller takes from the instrument master instead of NIFTY defaults.

Nothing here places an order: the builder computes; execution goes through the platform's execution and risk layers.
"""
from app.options_builder.greeks import leg_with_model_greeks
from app.options_builder.payoff import (
    build_default_price_range, compute_combined_greeks, compute_leg_payoff, compute_max_profit_loss,
    compute_strategy_payoff_curve, find_breakeven_points,
)
from app.options_builder.selectors import (
    compute_position_size, select_credit_spread, select_credit_spread_fixed_strikes, select_credit_spread_itm,
    select_iron_butterfly, select_iron_condor, select_naked_option_itm,
)
from app.options_builder.templates import (
    READY_MADE_CATEGORIES, build_ready_made_strategy, build_strategy_result_from_legs, hedge_first,
)

__all__ = [
    "compute_leg_payoff", "compute_strategy_payoff_curve", "compute_combined_greeks", "find_breakeven_points",
    "compute_max_profit_loss", "build_default_price_range", "build_ready_made_strategy", "build_strategy_result_from_legs",
    "hedge_first", "READY_MADE_CATEGORIES", "select_iron_condor", "select_iron_butterfly", "select_credit_spread",
    "select_credit_spread_fixed_strikes", "select_credit_spread_itm", "select_naked_option_itm", "compute_position_size",
    "leg_with_model_greeks",
]
