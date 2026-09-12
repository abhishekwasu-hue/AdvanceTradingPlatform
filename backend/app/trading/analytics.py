from collections import defaultdict
from typing import Dict, List

from pydantic import BaseModel

from app.db.models import TradeRecord


class GroupStats(BaseModel):
    key: str
    trades: int
    wins: int
    win_rate: float
    net_pnl: float


class AnalyticsSummary(BaseModel):
    total_trades: int
    closed_trades: int
    open_trades: int
    win_rate: float
    net_pnl: float
    gross_profit: float
    gross_loss: float
    profit_factor: float | None
    avg_win: float
    avg_loss: float
    by_strategy: List[GroupStats]
    by_symbol: List[GroupStats]


def _group_stats(rows: List[TradeRecord], key_fn) -> List[GroupStats]:
    groups: Dict[str, List[TradeRecord]] = defaultdict(list)
    for row in rows:
        groups[key_fn(row)].append(row)

    stats = []
    for key, group_rows in groups.items():
        wins = sum(1 for r in group_rows if (r.pnl or 0) > 0)
        net_pnl = sum(r.pnl or 0.0 for r in group_rows)
        stats.append(GroupStats(
            key=key, trades=len(group_rows), wins=wins,
            win_rate=round(100 * wins / len(group_rows), 1) if group_rows else 0.0,
            net_pnl=round(net_pnl, 2),
        ))
    return sorted(stats, key=lambda s: s.net_pnl, reverse=True)


def build_analytics_summary(all_trades: List[TradeRecord]) -> AnalyticsSummary:
    """Aggregates a user's full trade history (open + closed) into win rate / P&L breakdowns by
    strategy and symbol. Only closed trades (those with a recorded pnl) count toward win
    rate/profit-factor - an open position hasn't realized anything yet.
    """
    closed = [t for t in all_trades if t.exit_time is not None and t.pnl is not None]

    wins = [t for t in closed if t.pnl > 0]
    losses = [t for t in closed if t.pnl <= 0]
    gross_profit = sum(t.pnl for t in wins)
    gross_loss = sum(t.pnl for t in losses)
    net_pnl = gross_profit + gross_loss

    return AnalyticsSummary(
        total_trades=len(all_trades),
        closed_trades=len(closed),
        open_trades=len(all_trades) - len(closed),
        win_rate=round(100 * len(wins) / len(closed), 1) if closed else 0.0,
        net_pnl=round(net_pnl, 2),
        gross_profit=round(gross_profit, 2),
        gross_loss=round(gross_loss, 2),
        profit_factor=round(gross_profit / abs(gross_loss), 2) if gross_loss < 0 else None,
        avg_win=round(gross_profit / len(wins), 2) if wins else 0.0,
        avg_loss=round(gross_loss / len(losses), 2) if losses else 0.0,
        by_strategy=_group_stats(closed, lambda r: r.strategy_id),
        by_symbol=_group_stats(closed, lambda r: r.symbol),
    )
