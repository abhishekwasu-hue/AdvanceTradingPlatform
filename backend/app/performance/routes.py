"""REV-1 M2 (review: Performance): the Performance page reads the signed-in user's OWN trades.

`/api/analytics/summary` and `/api/analytics/degradation` (app/trading) read the whole organisation's trades, while the
page says "your" history and its Tax card and the Coach read only the user's own: a team member's numbers disagreed.
These two reads are the same numbers for the user's own trades only:

* `GET /api/performance/summary` - `build_analytics_summary` over `TradeRecord.user_id == user.id`.
* `GET /api/performance/vs-backtest` - each strategy's results against its latest saved backtest
  (`app.trading.degradation`), per strategy AND trade mode: PAPER results are labelled PAPER, never "live". The latest
  backtest runs are read in one query (no query per strategy).

Read-only; nothing here changes an order, a trade or a setting. The organisation-wide endpoints stay as they were for
their other callers.
"""
import json
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.models import BacktestRunRecord, TradeRecord, User
from app.db.session import get_session
from app.trading.analytics import AnalyticsSummary, build_analytics_summary
from app.trading.degradation import _norm_rate, compare, live_metrics

router = APIRouter(prefix="/api/performance", tags=["performance"])

STATUS_ORDER = {"DEGRADED": 0, "WATCH": 1, "OK": 2, "NO_BASELINE": 3, "INSUFFICIENT_DATA": 4}


def _own(user: User):
    return select(TradeRecord).where(TradeRecord.tenant_id == user.tenant_id, TradeRecord.user_id == user.id)


@router.get("/summary", response_model=AnalyticsSummary)
async def own_summary(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
                      days: int | None = Query(None, ge=1, le=3650)) -> AnalyticsSummary:
    """Win rate / P&L by strategy and symbol from the user's own trades (closed ones for the rates)."""
    query = _own(user)
    if days is not None:
        since = datetime.now(UTC) - timedelta(days=days)
        query = query.where(or_(TradeRecord.exit_time.is_(None), TradeRecord.exit_time >= since))
    return build_analytics_summary(list(await session.scalars(query)))


async def _latest_runs(session: AsyncSession, tenant_id: int, strategy_ids: list[str]) -> dict[str, BacktestRunRecord]:
    """The latest saved backtest run of each strategy, in one query."""
    if not strategy_ids:
        return {}
    latest = (select(BacktestRunRecord.strategy_id, func.max(BacktestRunRecord.id).label("id"))
              .where(BacktestRunRecord.tenant_id == tenant_id, BacktestRunRecord.strategy_id.in_(strategy_ids))
              .group_by(BacktestRunRecord.strategy_id).subquery())
    runs = await session.scalars(select(BacktestRunRecord).join(latest, BacktestRunRecord.id == latest.c.id))
    return {run.strategy_id: run for run in runs}


def _finite(metrics: dict) -> dict:
    if metrics["profit_factor"] == float("inf"):
        metrics["profit_factor"] = None
    return metrics


@router.get("/vs-backtest")
async def own_vs_backtest(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """Each strategy's own closed results per mode (PAPER / LIVE) against its latest saved backtest."""
    trades = list(await session.scalars(_own(user).where(TradeRecord.exit_time.is_not(None)).order_by(TradeRecord.id)))
    groups: dict[tuple[str, str], list[TradeRecord]] = {}
    for t in trades:
        groups.setdefault((t.strategy_id, (t.mode or "PAPER").upper()), []).append(t)
    runs = await _latest_runs(session, user.tenant_id, sorted({sid for sid, _ in groups}))
    rows = []
    for (strategy_id, mode), rows_for in sorted(groups.items()):
        run = runs.get(strategy_id)
        backtest = None
        if run is not None:
            m = json.loads(run.metrics_json or "{}")
            backtest = {"run_id": run.id, "win_rate": _norm_rate(m.get("win_rate")), "expectancy": m.get("expectancy"),
                        "profit_factor": m.get("profit_factor"), "net_pnl": m.get("net_pnl"), "total_trades": m.get("total_trades"),
                        "data_to": run.data_to.isoformat() if run.data_to else None}
        verdict = compare(live_metrics(rows_for[-20:]) if len(rows_for) >= 20 else live_metrics(rows_for), backtest)
        rows.append({"strategy_id": strategy_id, "mode": mode, "live": _finite(live_metrics(rows_for)),
                     "recent_20": _finite(live_metrics(rows_for[-20:])), "backtest": backtest, **verdict})
    rows.sort(key=lambda r: STATUS_ORDER.get(r["status"], 9))
    return {"strategies": rows, "degraded": sum(1 for r in rows if r["status"] == "DEGRADED"),
            "watch": sum(1 for r in rows if r["status"] == "WATCH"),
            "note": "Your last 20 closed trades of each strategy are compared with its latest saved backtest. "
                    "Save a backtest of a strategy to compare it."}
