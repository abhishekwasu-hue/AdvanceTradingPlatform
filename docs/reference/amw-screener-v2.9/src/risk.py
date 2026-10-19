"""Risk management helpers. Uses ONLY the user-configured account equity - never assumes a balance."""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class PositionPlan:
    ok: bool
    quantity: int = 0
    risk_amount: float = 0.0
    risk_per_share: float = 0.0
    position_value: float = 0.0
    reasons: list[str] = field(default_factory=list)


def atr_stop(entry: float, atr: float, mult: float, direction: str) -> float:
    return entry - mult * atr if direction == "bullish" else entry + mult * atr


def position_size(entry: float, stop: float, direction: str, risk_cfg: dict,
                  open_risk_amount: float = 0.0, day_realized_loss: float = 0.0) -> PositionPlan:
    eq = risk_cfg.get("account_equity")
    plan = PositionPlan(ok=False)
    if eq is None or eq <= 0:
        plan.reasons.append("account_equity is not configured - quantity cannot be calculated (set it in Settings)")
        return plan
    rps = (entry - stop) if direction == "bullish" else (stop - entry)
    if rps <= 0:
        plan.reasons.append("stop is on the wrong side of entry")
        return plan
    budget = eq * risk_cfg["risk_per_trade_pct"] / 100
    max_open = eq * risk_cfg["max_open_risk_pct"] / 100
    max_day = eq * risk_cfg["max_daily_loss_pct"] / 100
    if day_realized_loss >= max_day:
        plan.reasons.append(f"daily loss limit reached ({day_realized_loss:.0f} >= {max_day:.0f}) - no new trades")
        return plan
    budget = min(budget, max(0.0, max_open - open_risk_amount))
    if budget <= 0:
        plan.reasons.append("max open risk reached")
        return plan
    lot = max(1, int(risk_cfg.get("lot_size", 1)))
    qty = int(math.floor(budget / rps / lot)) * lot
    qty = min(qty, int(eq // entry // lot) * lot)          # cannot exceed cash (no leverage assumed)
    if qty <= 0:
        plan.reasons.append("risk budget too small for one lot at this stop distance")
        return plan
    return PositionPlan(True, qty, round(qty * rps, 2), round(rps, 4), round(qty * entry, 2),
                        [f"risk {risk_cfg['risk_per_trade_pct']}% of equity, capped by open-risk limit"])


def concentration_warnings(open_positions: list[dict], new_industry: str, risk_cfg: dict) -> list[str]:
    same = [p for p in open_positions if p.get("industry") and p.get("industry") == new_industry]
    if new_industry and len(same) >= risk_cfg["max_sector_positions"]:
        return [f"sector concentration: already {len(same)} open positions in {new_industry}"]
    return []


def liquidity_warning(avg_traded_value: float | None, risk_cfg: dict) -> str | None:
    if avg_traded_value is None:
        return "liquidity unknown (volume data missing)"
    if avg_traded_value < risk_cfg["min_avg_traded_value"]:
        return f"low liquidity: avg traded value {avg_traded_value / 1e7:.2f} Cr < {risk_cfg['min_avg_traded_value'] / 1e7:.2f} Cr"
    return None


def cost_per_side(cost_cfg: dict) -> float:
    return (cost_cfg["brokerage_bps"] + cost_cfg["exchange_tax_bps"] + cost_cfg["slippage_bps"]) / 1e4
