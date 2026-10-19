"""Paper trading (SIMULATED fills - real execution may differ). No broker connection.

Ledger model: cash starts at the configured account_equity; buying debits cash, selling credits it; costs are
charged per side (brokerage+exchange/taxes+slippage). `reconcile()` recomputes cash from the ledger and must match.
Daily risk control: new trades are refused once realised loss today >= max_daily_loss_pct.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..risk import cost_per_side, position_size


@dataclass
class PaperPosition:
    id: int
    symbol: str
    direction: str
    qty: int
    entry: float
    stop: float
    target: float | None
    entry_cost: float
    opened: str
    signal_key: str | None = None
    exit: float | None = None
    exit_cost: float = 0.0
    closed: str | None = None
    reason: str = ""

    def realised(self) -> float | None:
        if self.exit is None:
            return None
        gross = (self.exit - self.entry) * self.qty * (1 if self.direction == "bullish" else -1)
        return gross - self.entry_cost - self.exit_cost

    def unrealised(self, price: float) -> float:
        return (price - self.entry) * self.qty * (1 if self.direction == "bullish" else -1) - self.entry_cost


@dataclass
class PaperAccount:
    equity_start: float
    cfg: dict
    cash: float = 0.0
    positions: list[PaperPosition] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)

    def __post_init__(self):
        if self.equity_start is None or self.equity_start <= 0:
            raise ValueError("account_equity must be configured (no balance is assumed)")
        self.cash = float(self.equity_start)

    def _cost(self, notional: float) -> float:
        return notional * cost_per_side(self.cfg["costs"])

    def open_risk(self) -> float:
        return sum(abs(p.entry - p.stop) * p.qty for p in self.positions if p.exit is None)

    def realised_on(self, day: str) -> float:
        return sum(p.realised() or 0 for p in self.positions if p.closed and p.closed[:10] == day)

    def open(self, symbol: str, direction: str, price: float, stop: float, target: float | None, when: str,
             signal_key: str | None = None) -> PaperPosition | None:
        risk_cfg = {**self.cfg["risk"], "account_equity": self.equity()}
        day_loss = max(0.0, -self.realised_on(when[:10]))
        plan = position_size(price, stop, direction, risk_cfg, self.open_risk(), day_loss)
        if not plan.ok:
            self.events.append({"t": when, "event": "refused", "symbol": symbol, "reason": "; ".join(plan.reasons)})
            return None
        notional = plan.quantity * price
        cost = self._cost(notional)
        sign = 1 if direction == "bullish" else -1
        self.cash -= sign * notional + cost
        pos = PaperPosition(len(self.positions) + 1, symbol, direction, plan.quantity, price, stop, target, cost, when, signal_key)
        self.positions.append(pos)
        self.events.append({"t": when, "event": "open", "id": pos.id, "symbol": symbol, "qty": pos.qty, "price": price, "cost": round(cost, 2)})
        return pos

    def close(self, pos_id: int, price: float, when: str, reason: str) -> PaperPosition:
        p = next(x for x in self.positions if x.id == pos_id and x.exit is None)
        notional = p.qty * price
        p.exit_cost = self._cost(notional)
        sign = 1 if p.direction == "bullish" else -1
        self.cash += sign * notional - p.exit_cost
        p.exit, p.closed, p.reason = price, when, reason
        self.events.append({"t": when, "event": "close", "id": p.id, "price": price, "reason": reason, "pnl": round(p.realised(), 2)})
        return p

    def mark(self, prices: dict[str, dict], when: str) -> list[PaperPosition]:
        """prices: {symbol: {"open","high","low","close"}} of a COMPLETED bar. Stop checked first (conservative)."""
        closed = []
        for p in [x for x in self.positions if x.exit is None]:
            b = prices.get(p.symbol)
            if not b:
                continue
            long = p.direction == "bullish"
            if (long and b["open"] <= p.stop) or (not long and b["open"] >= p.stop):
                closed.append(self.close(p.id, b["open"], when, "gap stop"))
            elif (long and b["low"] <= p.stop) or (not long and b["high"] >= p.stop):
                closed.append(self.close(p.id, p.stop, when, "stop"))
            elif p.target is not None and ((long and b["high"] >= p.target) or (not long and b["low"] <= p.target)):
                closed.append(self.close(p.id, p.target, when, "target"))
        return closed

    def equity(self, prices: dict[str, float] | None = None) -> float:
        prices = prices or {}
        val = self.cash
        for p in self.positions:
            if p.exit is None:
                sign = 1 if p.direction == "bullish" else -1
                val += sign * p.qty * prices.get(p.symbol, p.entry)
        return val

    def reconcile(self) -> float:
        """cash recomputed from the ledger minus actual cash (should be ~0)."""
        cash = self.equity_start
        for p in self.positions:
            sign = 1 if p.direction == "bullish" else -1
            cash -= sign * p.qty * p.entry + p.entry_cost
            if p.exit is not None:
                cash += sign * p.qty * p.exit - p.exit_cost
        return round(cash - self.cash, 6)

    def realised_total(self) -> float:
        return sum(p.realised() or 0 for p in self.positions)
