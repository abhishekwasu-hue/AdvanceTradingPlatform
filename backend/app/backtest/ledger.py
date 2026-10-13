"""N7 C5: the trial ledger and the deflated Sharpe for `/api/backtest` runs, and lake-sourced runs behind the holdout.

Flag `backtest_ledger` (operator, off by default). Off: `/api/backtest` behaves exactly as before - no trial is written,
`deflation` is absent and a `lake` window is refused with the reason.

On:
* every logged-in `/api/backtest` run is appended to `research_trials` (H-C3a's append-only ledger) as a trial of kind
  `backtest` - what ran (strategy, params, symbol, timeframe, exit rules, option settings, execution models, data
  source) and its daily returns (net P&L per IST session over the run's sessions, as a fraction of the risk capital);
* the response carries `deflation`: the run's Sharpe deflated (Bailey & Lopez de Prado) against every `backtest` trial
  this ORGANISATION ran in the last LEDGER_DAYS days (BT1b: never another organisation's) - the search is part of the
  result, so the 20th variant tried is judged as the best of 20;
* `lake` (instead of `candles`): the server reads the bars itself from the shared market-data lake - NSE cash-market
  bhavcopy day bars (public exchange files, the same for every organisation), adjusted for the splits and bonuses on
  record whose ex-date is inside the window (a run is its own as-of view: later bars or later actions never change it). The sealed holdout (`data_policy`, BACKTEST_HOLDOUT_START) applies: a window that reaches it is refused,
  never truncated silently. Candles posted by the client are the client's data and cannot be held to the holdout; they
  are recorded as `uploaded` whatever label the request carries.

Research only: a trial is a record of a simulation, never an order, and nothing here changes a strategy.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.backtest.data_policy import HoldoutError, check_range, holdout_start
from app.core.models import OHLCVBar
from app.market_data.calendar import IST

FLAG = "backtest_ledger"
KIND = "backtest"
LEDGER_DAYS = 90                        # provisional (OPEN_QUESTIONS C5-1): the search window a run is deflated against
LAKE_EXCHANGES = ("NSE",)
LAKE_TIMEFRAMES = ("day", "1d", "d", "daily")
MAX_LAKE_DAYS = 3700                    # about ten years of sessions in one run
SESSION_OPEN = time(9, 15)              # a lake day bar is stamped at its session's open, IST (OPEN_QUESTIONS C5-4)
NOTE = ("Deflated for {n} backtests this organisation ran in the last {days} days (the search is part of the result). "
        "A simulation on past data - not a recommendation, and not a prediction.")


class LedgerError(ValueError):
    """A request the ledger cannot serve (the reason is shown as is)."""


class LakeWindow(BaseModel):
    """`/api/backtest` on the server's own bars: the trade dates [start, end] of the NSE bhavcopy day bars."""
    exchange: str = Field(default="NSE", description="NSE only (the cash-market bhavcopy)")
    start: date
    end: date
    adjusted: bool = Field(default=True, description="splits and bonuses on record adjusted out (dividends as the lake setting)")

    @model_validator(mode="after")
    def _check(self) -> "LakeWindow":
        self.exchange = self.exchange.strip().upper()
        if self.exchange not in LAKE_EXCHANGES:
            raise ValueError(f"lake: exchange must be one of {', '.join(LAKE_EXCHANGES)}")
        if self.end < self.start:
            raise ValueError("lake: end is before start")
        if (self.end - self.start).days > MAX_LAKE_DAYS:
            raise ValueError(f"lake: at most {MAX_LAKE_DAYS} days in one run")
        return self


async def enabled(session: AsyncSession, tenant_id: Optional[int]) -> bool:
    from app.platform.controls import flag_enabled
    return tenant_id is not None and await flag_enabled(session, FLAG, tenant_id)


def check_holdout(window: LakeWindow) -> None:
    """The window's bars - stamped at their session's open, IST - must end before the sealed holdout (HoldoutError).
    Review of C3 (LOW): the last bar's own time, not its date at midnight, so a boundary set during that day refuses it."""
    first = pd.Timestamp(datetime.combine(window.start, SESSION_OPEN, tzinfo=IST))
    last = pd.Timestamp(datetime.combine(window.end, SESSION_OPEN, tzinfo=IST))
    check_range(first, last, holdout_start())


async def lake_candles(session: AsyncSession, symbol: str, base_timeframe: str, window: LakeWindow) -> List[OHLCVBar]:
    """The server's own bars for a run (holdout first, then the lake). LedgerError / HoldoutError with the reason."""
    from app.market_lake import adjust
    from app.market_lake.bhavcopy_backfill import SOURCE, TIMEFRAME, bar_end
    from app.market_lake.ingest import trade_date
    if base_timeframe.strip().lower() not in LAKE_TIMEFRAMES:
        raise LedgerError(f"lake: day bars only (base_timeframe 'day'); intraday lake bars are not offered for backtests yet "
                          f"(asked {base_timeframe!r})")
    check_holdout(window)
    sym = symbol.strip().upper()
    bars = await adjust.candles(session, f"{window.exchange}:{sym}", TIMEFRAME, bar_end(window.start) - timedelta(hours=1),
                                bar_end(window.end), symbol=sym, exchange=window.exchange, adjusted=False, sources=[SOURCE["cm"]])
    if window.adjusted:
        # only the actions whose ex-date falls inside the run: the run as of its own last day - later bars or a later
        # split added to the lake never change it (no look-ahead; tested)
        actions = [a for a in await adjust.actions_as_of(session, sym, window.exchange) if a.ex_date <= window.end]
        bars = adjust.adjust(bars, actions)
    if len(bars) < 2:
        raise LedgerError(f"lake: {len(bars)} NSE day bar(s) of {sym} between {window.start} and {window.end}")
    return [OHLCVBar(timestamp=datetime.combine(trade_date(b.ts), SESSION_OPEN, tzinfo=IST), open=b.open, high=b.high, low=b.low,
                     close=b.close, volume=float(b.volume)) for b in bars]


def lake_label(window: LakeWindow) -> str:
    from app.market_lake.bhavcopy_backfill import SOURCE
    return f"lake:{SOURCE['cm']}" + (":adjusted" if window.adjusted else "")


def trial_dsl(body: Any, data_source: str) -> Dict[str, Any]:
    """What ran - the ledger's key for this trial (the candles themselves are not stored; their span and source are)."""
    return {"kind": KIND, "strategy_id": body.strategy_id, "params": body.strategy_params or {}, "symbol": body.symbol.upper(),
            "base_timeframe": body.base_timeframe, "exit_rules": body.exit_rules.model_dump(exclude_none=True) if body.exit_rules else None,
            "options": body.options.model_dump(mode="json", exclude={"option_chain"}, exclude_none=True) if body.options else None,
            "execution_models": body.execution_models or None, "data_source": data_source}


async def earlier_sharpes(session: AsyncSession, tenant_id: int, since: datetime) -> List[float]:
    """The Sharpe of every `backtest` trial this organisation recorded since `since` (its own rows only - BT1b)."""
    import json

    from app.backtest.validation import sharpe
    from app.db.models import ResearchTrialRecord
    rows = await session.scalars(select(ResearchTrialRecord).where(
        ResearchTrialRecord.tenant_id == tenant_id, ResearchTrialRecord.status == "ok", ResearchTrialRecord.created_at >= since,
        ResearchTrialRecord.dsl_json.like(f'%"kind": "{KIND}"%')))
    # the LIKE is a pre-filter only: a strategy parameter could contain the same text - the kind is checked exactly
    return [sharpe(json.loads(r.returns_json or "[]")) for r in rows if _kind_of(r.dsl_json) == KIND]


def _kind_of(dsl_json: Optional[str]) -> Optional[str]:
    import json
    try:
        value = json.loads(dsl_json or "{}")
    except ValueError:
        return None
    return value.get("kind") if isinstance(value, dict) else None


async def record(session: AsyncSession, *, tenant_id: int, user_id: Optional[int], body: Any, result: Any, df: pd.DataFrame,
                 capital: float, data_source: str, exchange: str = "NSE") -> Dict[str, Any]:
    """Append the run to the ledger and deflate it against the organisation's recent backtests -> the `deflation` block."""
    from app.ai.research import daily_returns, record_trial, trading_days
    from app.backtest.validation import deflated_sharpe, sharpe
    days = trading_days(pd.DatetimeIndex(df.index))
    returns = daily_returns(result.trades, capital, days) if days else []
    since = datetime.now(timezone.utc) - timedelta(days=LEDGER_DAYS)
    earlier = await earlier_sharpes(session, tenant_id, since)
    if len(returns) < 2:
        return {"recorded": False, "n_trials": len(earlier), "dsr": None,
                "note": "Fewer than two sessions: not a trial (a Sharpe needs at least two daily returns)."}
    first, last = pd.Timestamp(df.index[0]), pd.Timestamp(df.index[-1])
    row = await record_trial(session, tenant_id=tenant_id, user_id=user_id, study_id=f"{KIND}:{uuid.uuid4()}",
                             dsl=trial_dsl(body, data_source), symbol=body.symbol, exchange=exchange, timeframe=body.base_timeframe,
                             status="ok", data_from=first.to_pydatetime(), data_to=last.to_pydatetime(),
                             metrics={"net_pnl": result.net_pnl, "total_trades": result.total_trades, "win_rate": result.win_rate,
                                      "max_drawdown": result.max_drawdown, "sharpe": round(sharpe(returns), 6),
                                      "run_id": getattr(result, "run_id", None)},
                             returns=returns)
    await session.commit()
    out = deflated_sharpe(returns, earlier + [sharpe(returns)])
    return {**out, "recorded": True, "trial_id": row.id, "data_source": data_source,
            "note": NOTE.format(n=out.get("n_trials", 1), days=LEDGER_DAYS)}


__all__ = ["FLAG", "KIND", "LEDGER_DAYS", "LakeWindow", "LedgerError", "HoldoutError", "enabled", "lake_candles", "lake_label",
           "check_holdout", "trial_dsl", "earlier_sharpes", "record"]
