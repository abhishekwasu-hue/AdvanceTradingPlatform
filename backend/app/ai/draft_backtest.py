"""Item 5 (owner's handover), job 6 follow-up (CP6-2): the honest test of the Copilot's DRAFT paper strategy on past data.

The draft is the trader's own idea, built only from ATP's building blocks (`app/ai/strategy_builder.py`), with every
value his. This module runs it through ATP's existing backtest engine (`app/backtest/engine.run_backtest`, the same
engine the Backtesting page uses) and reports only facts:

* the whole window: trades, wins, net result AFTER costs (the engine's dated brokerage and statutory charges, and its
  slippage on every fill), the costs themselves, the deepest drop from a peak (in money and as a share of capital);
* the sample size: below `WEAK_EVIDENCE_MIN_TRADES` closed trades the result is "too few trades" and no ratio is
  judged;
* the window split in two (`LATER_SHARE` of the bars at the end kept apart): the draft's values are the trader's own,
  nothing was tuned on this data, and the later part shows whether the earlier part's result held on bars it did not see;
* the walk-forward windows (`app/backtest/robustness.walk_forward`, the same parameters on each window);
* the deflated Sharpe ratio (`app/backtest/validation.deflated_sharpe`), counted as ONE try - every change the trader
  tests is another try, and many tries make a good-looking result more likely by luck (said in the answer);
* the losses-in-a-row pause (CP6-15): when the trader's limit was reached, whether each pause ended at the next session
  or - under "Pause until I resume", which nobody can do inside a test - held to the end of the run;
* the operator's sealed holdout (`BACKTEST_HOLDOUT_START`, `app/backtest/data_policy`) is never touched: its bars are
  dropped before anything runs, and the count is reported.

Nothing is saved, started or placed: the strategy object lives for this call only (ADR-0006). Pure functions over a
frame the caller fetched on the server, so every rule here is unit-tested.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from app.core.models import BacktestResult, RiskConfig

DRAFT_ID = "copilot:draft"          # the in-memory strategy's id (never saved, never deployable)
LATER_SHARE = 0.3                    # the share of the window, at its end, kept apart as the later part
WALK_FORWARD_FOLDS = 4
TEST_DAYS = 60                       # calendar days of server candles (the Copilot backtest tool's own maximum)
# The engine reads a 3-minute strategy from 1-minute bars (brokers serve 1, 5, 15, 30 and 60-minute candles).
FETCH_TIMEFRAME = {"3min": "1min"}
MAX_CHANCE_PCT = 99.9


def _r(x: Any, nd: int = 2) -> Optional[float]:
    try:
        return None if x is None or x != x else round(float(x), nd)
    except (TypeError, ValueError):
        return None


def _iso(ts: Any) -> Optional[str]:
    if ts is None:
        return None
    if isinstance(ts, pd.Timestamp):
        return ts.isoformat()
    return str(ts)


def day_label(ts: Any) -> Optional[str]:
    """A bar's day in India time, as the answer prints it ("1 Jul 2026")."""
    if ts is None:
        return None
    t = pd.Timestamp(ts)
    t = t.tz_localize("UTC") if t.tzinfo is None else t
    d = t.tz_convert("Asia/Kolkata")
    return f"{d.day} {d.strftime('%b %Y')}"


def _part(result: BacktestResult, df: pd.DataFrame, capital: float) -> Dict[str, Any]:
    """One window's facts, in the order the answer reads them."""
    charges = sum(float(t.charges or 0.0) for t in result.trades)
    return {
        "from": _iso(df.index[0]) if len(df) else None, "to": _iso(df.index[-1]) if len(df) else None, "bars": int(len(df)),
        "from_day": day_label(df.index[0]) if len(df) else None, "to_day": day_label(df.index[-1]) if len(df) else None,
        "trades": int(result.total_trades), "wins": int(result.winning_trades), "losses": int(result.losing_trades),
        "win_rate": _r(result.win_rate), "net_pnl": _r(result.net_pnl), "costs": _r(charges),
        "profit_factor": _r(result.profit_factor), "max_drawdown": _r(result.max_drawdown),
        "max_drawdown_pct": _r(result.max_drawdown / capital * 100 if capital else None),
        "expectancy": _r(result.expectancy),
    }


def honest_test(config: Any, df: pd.DataFrame, symbol: str, base_tf: str, risk: RiskConfig, *,
                holdout: Any = None, min_trades: Optional[int] = None, use_windows: Any = ()) -> Dict[str, Any]:
    """The draft `config` (a `CustomStrategyConfig`) on `df` (server candles at `base_tf`) through ATP's backtest engine.
    `holdout`: the sealed holdout boundary (None reads the operator's `BACKTEST_HOLDOUT_START`)."""
    from app.ai.compliance import WEAK_EVIDENCE_MIN_TRADES
    from app.backtest import data_policy, validation
    from app.backtest.engine import run_backtest
    from app.backtest.robustness import walk_forward
    from app.strategy_engine.declarative import DeclarativeStrategy

    need = WEAK_EVIDENCE_MIN_TRADES if min_trades is None else min_trades
    boundary = data_policy.holdout_start(holdout)
    sealed = 0
    if boundary is not None:
        kept = data_policy.filter_allowed(df, boundary)
        sealed, df = int(len(df) - len(kept)), kept
    # M71 PL-7b: the draft test feeds the Copilot's search for a strategy - the tenant's sealed / contaminated rows never
    kept = data_policy.filter_allowed(df, None, use_windows)
    policy_left_out, df = int(len(df) - len(kept)), kept
    if df.empty and policy_left_out:
        raise data_policy.HoldoutError(data_policy.windows_reason(use_windows, policy_left_out) or "no bars left")
    strategy = DeclarativeStrategy(DRAFT_ID, config)
    from app.core import config as app_config
    risk = risk.with_capital(app_config.PAPER_STARTING_CAPITAL)     # DEF-0: own capital, else the PAPER start
    capital = float(risk.capital or 0.0)
    out: Dict[str, Any] = {
        "symbol": symbol.upper(), "timeframe": config.timeframe, "bars": int(len(df)), "capital": _r(capital),
        "from": _iso(df.index[0]) if len(df) else None, "to": _iso(df.index[-1]) if len(df) else None,
        "from_day": day_label(df.index[0]) if len(df) else None, "to_day": day_label(df.index[-1]) if len(df) else None,
        "days_requested": TEST_DAYS, "later_share_pct": int(round(LATER_SHARE * 100)), "min_trades_for_statistics": need, "sealed_holdout_bars": sealed, "slippage_pct_per_fill": None,
        "costs_included": "brokerage and statutory charges at their dated rates, and slippage on every fill",
        "tuned_on_this_data": False, "tries": 1, "note": "Past simulated results on this window only; not a forecast.",
    }
    if use_windows:                       # M71 PL-7b: only a tenant with a data policy sees these (the shape is unchanged)
        out.update({"data_policy_bars_left_out": policy_left_out, "data_policy": data_policy.windows_reason(use_windows, policy_left_out)})

    min_hist = strategy.min_history()[strategy.timeframes[0]]
    if len(df) <= min_hist:
        out.update(sample="no_data", whole=None, earlier=None, later=None, walk_forward=None, deflated=None)
        return out
    from app.execution.paper_broker import PaperBroker
    out["slippage_pct_per_fill"] = _r(PaperBroker().slippage_pct, 4)

    whole = run_backtest(strategy, df, symbol.upper(), base_tf, risk)
    out["whole"] = _part(whole, df, capital)
    out["sample"] = "ok" if whole.total_trades >= need else "too_few"
    # CP6-15: what the losses-in-a-row pause did on the whole window (None when it never engaged). Under "Pause until I
    # resume" nobody can resume a test, so the pause held to the end and the answer says so plainly.
    out["loss_streak"] = whole.loss_streak

    cut = int(len(df) * (1 - LATER_SHARE))
    earlier_df, later_df = df.iloc[:cut], df.iloc[cut:]
    parts: Dict[str, Optional[Dict[str, Any]]] = {"earlier": None, "later": None}
    for key, part_df in (("earlier", earlier_df), ("later", later_df)):
        if len(part_df) > min_hist:
            parts[key] = _part(run_backtest(strategy, part_df, symbol.upper(), base_tf, risk), part_df, capital)
    out.update(parts)

    wf = walk_forward(strategy, df, symbol.upper(), base_tf, risk, folds=WALK_FORWARD_FOLDS)
    out["walk_forward"] = ({"windows": int(wf["folds"]), "profitable_windows": int(wf["profitable_windows"]),
                            "window_trades": [int(w["trades"]) for w in wf["windows"]]} if wf.get("folds") else None)

    returns: List[float] = [float(t.pnl or 0.0) / capital for t in whole.trades if capital]
    if whole.total_trades >= need and returns:
        ds = validation.deflated_sharpe(returns, [validation.sharpe(returns)])
        # never printed as a certainty: at most 99.9 (a finite sample can never prove the edge)
        chance = _r(min(ds["dsr"] * 100, MAX_CHANCE_PCT) if ds["dsr"] is not None else None, 1)
        out["deflated"] = {"sharpe_per_trade": _r(ds["sr"], 3), "chance_above_zero_pct": chance,
                           "tries": int(ds["n_trials"])}
    else:
        out["deflated"] = None
    return out


__all__ = ["honest_test", "DRAFT_ID", "LATER_SHARE", "WALK_FORWARD_FOLDS", "TEST_DAYS", "FETCH_TIMEFRAME"]
