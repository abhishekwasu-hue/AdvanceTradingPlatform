"""Phase M / V4.15: performance intelligence - live results per strategy against the latest
saved backtest of that strategy, with a plain status. Same numbers the monitoring agent's
WIN_RATE_DRIFT rule reads, laid out for the Analytics page."""
import json
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BacktestRunRecord, TradeRecord

MIN_TRADES = 10
WATCH_DROP = 0.10      # win-rate points below the backtest -> WATCH
DEGRADED_DROP = 0.25   # -> DEGRADED


def live_metrics(trades: List[TradeRecord]) -> Dict:
    closed = [t for t in trades if t.exit_time is not None]
    pnls = [float(t.pnl or 0.0) for t in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_profit, gross_loss = sum(wins), -sum(losses)
    return {
        "trades": len(closed), "win_rate": round(len(wins) / len(closed), 4) if closed else None,
        "net_pnl": round(sum(pnls), 2), "expectancy": round(sum(pnls) / len(closed), 2) if closed else None,
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss > 0 else (None if not wins else float("inf")),
        "avg_win": round(gross_profit / len(wins), 2) if wins else None, "avg_loss": round(-gross_loss / len(losses), 2) if losses else None,
    }


def _norm_rate(value) -> Optional[float]:
    if value is None:
        return None
    value = float(value)
    return value / 100.0 if value > 1 else value


def compare(live: Dict, backtest: Optional[Dict]) -> Dict:
    if live["trades"] < MIN_TRADES:
        return {"status": "INSUFFICIENT_DATA", "reasons": [f"{live['trades']} closed trades; need {MIN_TRADES}"]}
    if not backtest:
        return {"status": "NO_BASELINE", "reasons": ["no saved backtest run for this strategy"]}
    reasons: List[str] = []
    status = "OK"
    bt_wr, live_wr = _norm_rate(backtest.get("win_rate")), live["win_rate"]
    if bt_wr is not None and live_wr is not None:
        drop = bt_wr - live_wr
        if drop >= DEGRADED_DROP:
            status = "DEGRADED"
            reasons.append(f"win rate {live_wr:.0%} vs {bt_wr:.0%} backtest")
        elif drop >= WATCH_DROP:
            status = "WATCH"
            reasons.append(f"win rate {live_wr:.0%} vs {bt_wr:.0%} backtest")
    bt_exp = backtest.get("expectancy")
    if bt_exp is not None and live["expectancy"] is not None and float(bt_exp) > 0 and live["expectancy"] < 0:
        status = "DEGRADED"
        reasons.append(f"expectancy {live['expectancy']:.0f} per trade vs +{float(bt_exp):.0f} backtest")
    bt_pf = backtest.get("profit_factor")
    if bt_pf is not None and live["profit_factor"] is not None and live["profit_factor"] != float("inf") and float(bt_pf) > 1 and live["profit_factor"] < 1:
        status = "DEGRADED" if status == "DEGRADED" else "WATCH"
        reasons.append(f"profit factor {live['profit_factor']:.2f} vs {float(bt_pf):.2f} backtest")
    return {"status": status, "reasons": reasons or ["within the backtest's range"]}


async def build_degradation(session: AsyncSession, tenant_id: int) -> Dict:
    trades = list(await session.scalars(select(TradeRecord).where(TradeRecord.tenant_id == tenant_id, TradeRecord.exit_time.is_not(None)).order_by(TradeRecord.id)))
    by_strategy: Dict[str, List[TradeRecord]] = {}
    for t in trades:
        by_strategy.setdefault(t.strategy_id, []).append(t)
    rows = []
    for strategy_id, rows_for in sorted(by_strategy.items()):
        run = await session.scalar(select(BacktestRunRecord).where(BacktestRunRecord.tenant_id == tenant_id, BacktestRunRecord.strategy_id == strategy_id)
                                   .order_by(BacktestRunRecord.id.desc()))
        backtest = None
        if run is not None:
            m = json.loads(run.metrics_json or "{}")
            backtest = {"run_id": run.id, "win_rate": _norm_rate(m.get("win_rate")), "expectancy": m.get("expectancy"), "profit_factor": m.get("profit_factor"),
                        "net_pnl": m.get("net_pnl"), "total_trades": m.get("total_trades"), "data_to": run.data_to.isoformat() if run.data_to else None}
        live = live_metrics(rows_for)
        if live["profit_factor"] == float("inf"):
            live["profit_factor"] = None
        recent = live_metrics(rows_for[-20:])
        if recent["profit_factor"] == float("inf"):
            recent["profit_factor"] = None
        verdict = compare(live_metrics(rows_for[-20:]) if len(rows_for) >= 20 else live_metrics(rows_for), backtest)
        rows.append({"strategy_id": strategy_id, "live": live, "recent_20": recent, "backtest": backtest, **verdict})
    order = {"DEGRADED": 0, "WATCH": 1, "OK": 2, "NO_BASELINE": 3, "INSUFFICIENT_DATA": 4}
    rows.sort(key=lambda r: order.get(r["status"], 9))
    return {"strategies": rows, "degraded": sum(1 for r in rows if r["status"] == "DEGRADED"), "watch": sum(1 for r in rows if r["status"] == "WATCH"),
            "note": "Recent-20 window is judged against the latest saved backtest of the same strategy; save a backtest to get a baseline."}
