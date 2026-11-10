"""M94 R1 (owner order M94, spec docs/design/M94_INTRADAY_PACK.md R1; OPEN_QUESTIONS M94-Q9): the position-size calculator.

Information only: it places nothing, saves nothing and reads none of the trader's settings - every number is the one he
typed (account, risk per trade as a % of it and / or in rupees, entry, stop and the lot). Nothing is pre-filled; the page
shows the reference as a hint only. The size is worked out by the engine's own risk-based sizing
(`RiskManager.validate_and_size`, Settings > Risk limits "Risk-based sizing"), so the calculator and an entry sized by
risk always agree: risk per trade / stop distance, rounded DOWN to whole lots (a cash share's lot is one share). Less
than one lot is said so and never rounded up. With both a % and a rupee amount, the smaller applies (as in the engine).
"""
from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from app.core.enums import SignalDirection, SignalGrade
from app.core.models import RiskConfig, Signal
from app.risk_engine.risk_manager import CASH_UNIT, RiskManager, TradingDayState

SHARES, LOTS = "SHARES", "LOTS"
UNITS = (SHARES, LOTS)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = float(value)
    return v if math.isfinite(v) else None


def problems_of(body: dict[str, Any]) -> list[str]:
    """What the typed values still need, in plain words (empty: they can be sized). Zero and negative numbers are refused."""
    out: list[str] = []
    entry, stop = _number(body.get("entry")), _number(body.get("stop"))
    account, pct, rupees = (_number(body.get(k)) for k in ("account", "risk_pct", "risk_rupees"))
    for key, name in (("entry", "Entry price"), ("stop", "Stop price")):
        if _number(body.get(key)) is None or (_number(body.get(key)) or 0) <= 0:
            out.append(f"{name}: enter a price above 0.")
    if entry is not None and stop is not None and entry > 0 and stop > 0 and entry == stop:
        out.append("The stop price is the same as the entry price: there is no stop distance to size on.")
    if body.get("risk_pct") is None and body.get("risk_rupees") is None:
        out.append("Risk per trade: enter a % of the account or a rupee amount.")
    if body.get("risk_pct") is not None:
        if pct is None or not 0 < pct <= 100:
            out.append("Risk per trade: the % must be more than 0 and at most 100.")
        if account is None or account <= 0:
            out.append("Account: enter an amount above 0 to use a % risk.")
    elif body.get("account") is not None and (account is None or account <= 0):
        out.append("Account: enter an amount above 0, or leave it empty.")
    if body.get("risk_rupees") is not None and (rupees is None or rupees <= 0):
        out.append("Risk per trade: the rupee amount must be above 0.")
    unit = body.get("unit")
    if unit not in UNITS:
        out.append("Size in: pick shares or F&O lots.")
    elif unit == LOTS:
        lot = body.get("lot_size")
        if isinstance(lot, bool) or not isinstance(lot, (int, float)) or not math.isfinite(lot) or lot < 1 or not float(lot).is_integer():
            out.append("Lot size: enter the contract's lot size, a whole number from 1.")
    return out


def calculate(body: dict[str, Any]) -> dict[str, Any]:
    """`{"ok": False, "problems": [...]}` or the size: quantity, whole lots, the lot, the money at risk at that size and the
    risk per trade it came from. `{"ok": True, "quantity": 0, "note": ...}` when the risk is less than one lot."""
    problems = problems_of(body)
    if problems:
        return {"ok": False, "problems": problems}
    entry, stop = float(body["entry"]), float(body["stop"])
    lot = int(body["lot_size"]) if body["unit"] == LOTS else CASH_UNIT
    cfg = RiskConfig(capital=_number(body.get("account")), risk_per_trade_pct=_number(body.get("risk_pct")),
                     risk_per_trade_rupees=_number(body.get("risk_rupees")), lot_size=lot)
    signal = Signal(symbol="CALCULATOR", strategy_id="size_calculator", strategy_name="Position-size calculator",
                    direction=SignalDirection.LONG if entry > stop else SignalDirection.SHORT, timestamp=datetime.now(UTC),
                    entry=entry, stop_loss=stop, grade=SignalGrade.VALID)
    decision = RiskManager(cfg).validate_and_size(signal, TradingDayState())
    risk_amount = cfg.risk_amount()
    per_unit = abs(entry - stop)
    base = {"ok": True, "unit": body["unit"], "lot_size": lot, "risk_amount": round(risk_amount or 0.0, 2), "per_share": round(per_unit, 4),
            "per_lot": round(per_unit * lot, 2)}
    if not decision.approved:
        what = "one share" if body["unit"] == SHARES else "one lot"
        note = (f"Your risk per trade of ₹{risk_amount:,.0f} is less than the risk of {what} (₹{per_unit * lot:,.2f}) at this stop: "
                "no size. It is never rounded up." if risk_amount is not None else (decision.reasons[0] if decision.reasons else "No size."))
        return {**base, "quantity": 0, "lots": 0, "at_risk": 0.0, "note": note}
    quantity = int(decision.quantity)
    return {**base, "quantity": quantity, "lots": quantity // lot, "at_risk": round(quantity * per_unit, 2), "note": None}


__all__ = ["calculate", "problems_of", "SHARES", "LOTS", "UNITS"]
