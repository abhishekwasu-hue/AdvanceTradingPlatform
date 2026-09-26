"""Phase J2: backtest analytics views (master prompt sections 31-32, V2.10).

Everything here is derived from the closed trade list and the equity curve the engine already
produces - monthly P&L, day-of-week and hour-of-day performance, exit-reason breakdown, holding
times, streaks, slippage summary, drawdown curve and the ratio metrics (CAGR, Sharpe) where the
sample allows them. Pure functions, no I/O, reused by the Monte Carlo / walk-forward module.
"""
import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional

from app.core.models import Trade

IST_OFFSET_HOURS = 5.5
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _ist(ts: datetime) -> datetime:
    from zoneinfo import ZoneInfo
    aware = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    return aware.astimezone(ZoneInfo("Asia/Kolkata"))


def _bucket(trades: List[Trade], key_fn) -> List[Dict]:
    groups: Dict[str, List[Trade]] = defaultdict(list)
    for t in trades:
        groups[key_fn(t)].append(t)
    out = []
    for key in sorted(groups):
        rows = groups[key]
        pnl = sum(t.pnl or 0.0 for t in rows)
        wins = sum(1 for t in rows if (t.pnl or 0.0) > 0)
        out.append({"key": key, "trades": len(rows), "pnl": round(pnl, 2), "win_rate": round(wins / len(rows) * 100, 1),
                    "avg_pnl": round(pnl / len(rows), 2)})
    return out


def drawdown_curve(equity_curve: List[float]) -> List[float]:
    peak = equity_curve[0] if equity_curve else 0.0
    out = []
    for e in equity_curve:
        peak = max(peak, e)
        out.append(round(peak - e, 2))
    return out


def streaks(trades: List[Trade]) -> Dict[str, int]:
    best_win = best_loss = cur_win = cur_loss = 0
    for t in trades:
        if (t.pnl or 0.0) > 0:
            cur_win, cur_loss = cur_win + 1, 0
        else:
            cur_loss, cur_win = cur_loss + 1, 0
        best_win, best_loss = max(best_win, cur_win), max(best_loss, cur_loss)
    return {"max_consecutive_wins": best_win, "max_consecutive_losses": best_loss}


def ratio_metrics(trades: List[Trade], equity_curve: List[float], capital: float) -> Dict[str, Optional[float]]:
    if not trades or len(equity_curve) < 2:
        return {"cagr_pct": None, "sharpe": None, "sortino": None, "calmar": None}
    first, last = trades[0].entry_time, (trades[-1].exit_time or trades[-1].entry_time)
    days = max(1.0, (last - first).total_seconds() / 86400.0)
    end_equity = equity_curve[-1]
    cagr = ((end_equity / capital) ** (365.0 / days) - 1) * 100 if capital > 0 and end_equity > 0 else None
    returns = [(equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1] for i in range(1, len(equity_curve)) if equity_curve[i - 1] > 0]
    sharpe = sortino = None
    if len(returns) >= 2:
        mean = sum(returns) / len(returns)
        var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        sd = math.sqrt(var)
        trades_per_year = len(returns) * 365.0 / days
        sharpe = round(mean / sd * math.sqrt(trades_per_year), 2) if sd > 0 else None
        downside = [min(0.0, r) for r in returns]
        dsd = math.sqrt(sum(d ** 2 for d in downside) / (len(returns) - 1))
        sortino = round(mean / dsd * math.sqrt(trades_per_year), 2) if dsd > 0 else None
    max_dd = max(drawdown_curve(equity_curve)) if equity_curve else 0.0
    calmar = round((cagr or 0.0) / (max_dd / capital * 100), 2) if cagr is not None and max_dd > 0 and capital > 0 else None
    return {"cagr_pct": round(cagr, 2) if cagr is not None else None, "sharpe": sharpe, "sortino": sortino, "calmar": calmar,
            "period_days": round(days, 1)}


def build_analytics(trades: List[Trade], equity_curve: List[float], capital: float) -> Dict:
    closed = [t for t in trades if t.exit_time is not None]
    holding = [(t.exit_time - t.entry_time).total_seconds() / 60.0 for t in closed]
    slippages = [abs((t.entry_price - t.expected_price)) for t in closed if t.expected_price]
    charges = sum(t.charges or 0.0 for t in closed)
    gross = sum((t.pnl or 0.0) + (t.charges or 0.0) for t in closed)
    return {
        "monthly": _bucket(closed, lambda t: _ist(t.entry_time).strftime("%Y-%m")),
        "day_of_week": _bucket(closed, lambda t: f"{_ist(t.entry_time).weekday()}-{DAY_NAMES[_ist(t.entry_time).weekday()]}"),
        "hour_of_day": _bucket(closed, lambda t: f"{_ist(t.entry_time).hour:02d}:00"),
        "exit_reasons": _bucket(closed, lambda t: t.exit_reason or "unknown"),
        "direction": _bucket(closed, lambda t: t.direction.value if hasattr(t.direction, "value") else str(t.direction)),
        "holding_minutes": {"avg": round(sum(holding) / len(holding), 1) if holding else None,
                            "max": round(max(holding), 1) if holding else None, "min": round(min(holding), 1) if holding else None},
        "slippage": {"avg_per_unit": round(sum(slippages) / len(slippages), 4) if slippages else None, "trades_with_data": len(slippages)},
        "costs": {"total_charges": round(charges, 2), "gross_pnl": round(gross, 2), "charges_pct_of_gross": round(charges / abs(gross) * 100, 1) if gross else None},
        "streaks": streaks(closed),
        "ratios": ratio_metrics(closed, equity_curve, capital),
        "drawdown_curve": drawdown_curve(equity_curve),
    }
