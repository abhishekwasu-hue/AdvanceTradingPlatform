"""Phase M / V4.4: the portfolio engine - one view of a tenant's open book.

Aggregates open trades into gross and net notional, per-symbol and per-strategy exposure,
concentration, unrealised P&L at the prices given (LTP when a broker session is usable, else
entry), realised P&L today, and open risk at the stops. Pure over records + a price map, so the
risk hierarchy (PORTFOLIO scope) and the API share one implementation.
"""
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TradeRecord


@dataclass
class SymbolExposure:
    symbol: str
    positions: int
    quantity: float
    notional: float
    pct_of_capital: float
    unrealised_pnl: float
    risk_at_stop: float
    strategies: List[str] = field(default_factory=list)


@dataclass
class PortfolioSnapshot:
    as_of: str
    capital: float
    open_positions: int
    gross_notional: float
    net_notional: float
    long_notional: float
    short_notional: float
    gross_pct_of_capital: float
    unrealised_pnl: float
    realised_today: float
    risk_at_stops: float
    risk_pct_of_capital: float
    largest_symbol_pct: float
    by_symbol: List[SymbolExposure]
    by_strategy: Dict[str, float]
    by_mode: Dict[str, float]
    warnings: List[str]

    def to_dict(self) -> dict:
        return asdict(self)


def _notional(trade: TradeRecord, price: float) -> float:
    return abs(float(trade.quantity) * price)


def _unrealised(trade: TradeRecord, price: float) -> float:
    direction = 1.0 if trade.direction == "LONG" else -1.0
    return direction * (price - float(trade.entry_price)) * float(trade.quantity)


def _risk_at_stop(trade: TradeRecord, price: float) -> float:
    direction = 1.0 if trade.direction == "LONG" else -1.0
    loss_if_stopped = direction * (float(trade.stop_loss) - price) * float(trade.quantity)
    return max(0.0, -loss_if_stopped)


def snapshot(trades: List[TradeRecord], prices: Dict[str, float], *, capital: float, realised_today: float = 0.0) -> PortfolioSnapshot:
    by_symbol: Dict[str, SymbolExposure] = {}
    by_strategy: Dict[str, float] = {}
    by_mode: Dict[str, float] = {}
    gross = net = long_n = short_n = unreal = risk = 0.0
    for t in trades:
        price = prices.get(t.symbol) or float(t.entry_price)
        notional, pnl, r = _notional(t, price), _unrealised(t, price), _risk_at_stop(t, price)
        gross += notional
        unreal += pnl
        risk += r
        if t.direction == "LONG":
            long_n += notional
            net += notional
        else:
            short_n += notional
            net -= notional
        by_strategy[t.strategy_id] = by_strategy.get(t.strategy_id, 0.0) + notional
        by_mode[t.mode] = by_mode.get(t.mode, 0.0) + notional
        row = by_symbol.get(t.symbol)
        if row is None:
            row = by_symbol[t.symbol] = SymbolExposure(t.symbol, 0, 0.0, 0.0, 0.0, 0.0, 0.0, [])
        row.positions += 1
        row.quantity += float(t.quantity) if t.direction == "LONG" else -float(t.quantity)
        row.notional += notional
        row.unrealised_pnl += pnl
        row.risk_at_stop += r
        if t.strategy_id not in row.strategies:
            row.strategies.append(t.strategy_id)
    cap = capital if capital > 0 else 1.0
    for row in by_symbol.values():
        row.pct_of_capital = round(row.notional / cap * 100.0, 2)
        row.notional, row.unrealised_pnl, row.risk_at_stop = round(row.notional, 2), round(row.unrealised_pnl, 2), round(row.risk_at_stop, 2)
    rows = sorted(by_symbol.values(), key=lambda r: r.notional, reverse=True)
    largest = rows[0].pct_of_capital if rows else 0.0
    warnings: List[str] = []
    if gross > cap:
        warnings.append(f"Gross exposure {gross:,.0f} exceeds capital {cap:,.0f} (leveraged {gross / cap:.1f}x)")
    if largest > 40:
        warnings.append(f"{rows[0].symbol} is {largest:.0f}% of capital - concentration risk")
    if risk / cap * 100 > 5:
        warnings.append(f"Loss if every stop hits: {risk:,.0f} ({risk / cap * 100:.1f}% of capital)")
    return PortfolioSnapshot(
        as_of=datetime.now(timezone.utc).isoformat(), capital=capital, open_positions=len(trades),
        gross_notional=round(gross, 2), net_notional=round(net, 2), long_notional=round(long_n, 2), short_notional=round(short_n, 2),
        gross_pct_of_capital=round(gross / cap * 100.0, 2), unrealised_pnl=round(unreal, 2), realised_today=round(realised_today, 2),
        risk_at_stops=round(risk, 2), risk_pct_of_capital=round(risk / cap * 100.0, 2), largest_symbol_pct=largest,
        by_symbol=rows, by_strategy={k: round(v, 2) for k, v in by_strategy.items()}, by_mode={k: round(v, 2) for k, v in by_mode.items()},
        warnings=warnings,
    )


async def open_trades(session: AsyncSession, tenant_id: int, *, mode: Optional[str] = None) -> List[TradeRecord]:
    query = select(TradeRecord).where(TradeRecord.tenant_id == tenant_id, TradeRecord.exit_time.is_(None))
    if mode:
        query = query.where(TradeRecord.mode == mode)
    return list(await session.scalars(query.order_by(TradeRecord.id)))


async def realised_today(session: AsyncSession, tenant_id: int) -> float:
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    value = await session.scalar(select(func.coalesce(func.sum(TradeRecord.pnl), 0.0)).where(TradeRecord.tenant_id == tenant_id, TradeRecord.exit_time >= start))
    return float(value or 0.0)


async def gross_exposure(session: AsyncSession, tenant_id: int, *, symbol: Optional[str] = None) -> float:
    """Open notional at entry prices (the hierarchy's pre-trade measure; no quotes needed)."""
    query = select(func.coalesce(func.sum(TradeRecord.entry_price * TradeRecord.quantity), 0.0)).where(
        TradeRecord.tenant_id == tenant_id, TradeRecord.exit_time.is_(None))
    if symbol:
        query = query.where(TradeRecord.symbol == symbol)
    return float((await session.scalar(query)) or 0.0)
