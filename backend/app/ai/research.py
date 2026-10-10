"""H-C3a (ADR-0019/0020): the strategy research loop's ledger and honest report - no LLM in this part.

A research *study* tries N strategy drafts on the same symbol, timeframe and in-sample window. Every trial is written
to `research_trials` (append-only, never deleted), including drafts that failed validation. The report then:
* picks the best trial by in-sample Sharpe of its daily returns, and says it was chosen from N trials;
* deflates it: the Deflated Sharpe Ratio against the expected best of N zero-skill trials (`deflated_sharpe`);
* estimates the Probability of Backtest Overfitting across the trials' aligned daily returns (CSCV, `pbo_cscv`);
* carries the chosen draft's out-of-sample check (a window after the in-sample one, never the sealed holdout) when it
  was run, and says so when it was not;
* lists what the simulation does not model.
It reports; it never deploys or changes anything. Turning a draft into a strategy still goes through the existing
human approval gate (ADR-0006).
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.backtest.data_policy import HoldoutError, check_range, holdout_start
from app.backtest.validation import deflated_sharpe, pbo_cscv, sharpe  # noqa: F401 - sharpe is re-exported for the loop
from app.db.models import ResearchTrialRecord
from app.market_data.calendar import IST

MIN_DAYS_FOR_PBO = 32                       # CSCV needs at least S blocks of a few days each
NOT_SIMULATED = (
    "Order queue position and partial fills beyond the engine's simple model.",
    "Slippage beyond the configured cost model; gaps through stop levels are filled at the next bar.",
    "Liquidity limits and market impact of the order size.",
    "Corporate actions and contract rolls not present in the bar data.",
    "Broker rejections, freezes, circuit limits and outages.",
)
DISCLAIMER = ("A simulation on past data, chosen from several trials and deflated for that search. It is not a "
              "recommendation and does not predict future results.")


def dsl_hash(dsl: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(dsl, sort_keys=True, default=str).encode()).hexdigest()


def daily_returns(trades: Iterable[Any], capital: float, days: Sequence[date]) -> List[float]:
    """Net P&L per IST trading day over `days` (a day without exits is 0), as a fraction of `capital`."""
    if capital <= 0:
        raise ValueError("capital must be positive")
    by_day: Dict[date, float] = {d: 0.0 for d in days}
    for t in trades:
        exit_time = getattr(t, "exit_time", None)
        pnl = getattr(t, "pnl", None)
        if exit_time is None or pnl is None:
            continue
        ts = exit_time if exit_time.tzinfo else exit_time.replace(tzinfo=timezone.utc)
        d = ts.astimezone(IST).date()
        if d in by_day:
            by_day[d] += float(pnl)                                    # the engine's pnl is already net of charges
    return [by_day[d] / capital for d in days]


def trading_days(index: pd.DatetimeIndex) -> List[date]:
    idx = index if index.tz is not None else index.tz_localize("UTC")
    return sorted({ts.astimezone(IST).date() for ts in idx})


def split_window(index: pd.DatetimeIndex, oos_fraction: float = 0.3, boundary: Any = None) -> Tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]:
    """(in-sample start, out-of-sample start, end) over bars that end before the sealed holdout. Refuses (HoldoutError)
    a window that reaches into the holdout - the research loop never sees it."""
    if not 0 < oos_fraction < 0.9:
        raise ValueError("oos_fraction must be between 0 and 0.9")
    if len(index) < 10:
        raise ValueError("too few bars to split")
    start, end = index[0], index[-1]
    check_range(start, end, holdout_start(boundary))
    cut = index[int(len(index) * (1 - oos_fraction))]
    return start, cut, end


async def record_trial(session: AsyncSession, *, tenant_id: int, user_id: Optional[int], study_id: str, dsl: Dict[str, Any],
                       symbol: str, exchange: str, timeframe: str, status: str, reason: Optional[str] = None,
                       data_from: Optional[datetime] = None, data_to: Optional[datetime] = None,
                       metrics: Optional[Dict[str, Any]] = None, returns: Optional[Sequence[float]] = None) -> ResearchTrialRecord:
    """Appends one trial (sequence number = next in the study). Never updates or deletes an earlier trial."""
    if status not in ("ok", "invalid", "error", "oos"):
        raise ValueError("status must be ok, invalid, error or oos")
    if status == "ok" and not returns:
        raise ValueError("an ok trial needs its daily returns")
    seq = int(await session.scalar(select(func.count()).select_from(ResearchTrialRecord).where(
        ResearchTrialRecord.tenant_id == tenant_id, ResearchTrialRecord.study_id == study_id)) or 0) + 1
    row = ResearchTrialRecord(tenant_id=tenant_id, user_id=user_id, study_id=study_id, seq=seq, dsl_json=json.dumps(dsl, sort_keys=True, default=str),
                              dsl_hash=dsl_hash(dsl), symbol=symbol.upper(), exchange=exchange, timeframe=timeframe, data_from=data_from,
                              data_to=data_to, status=status, reason=(reason or None) and reason[:300],
                              metrics_json=json.dumps(metrics or {}, default=str), returns_json=json.dumps([float(r) for r in (returns or [])]),
                              created_at=datetime.now(timezone.utc))
    session.add(row)
    await session.flush()
    return row


async def study_trials(session: AsyncSession, tenant_id: int, study_id: str) -> List[ResearchTrialRecord]:
    return list(await session.scalars(select(ResearchTrialRecord).where(ResearchTrialRecord.tenant_id == tenant_id,
                                                                        ResearchTrialRecord.study_id == study_id)
                                      .order_by(ResearchTrialRecord.seq)))


def _pbo_blocks(t: int) -> int:
    s = min(16, t // 4)
    return s - (s % 2)


def study_report(trials: Sequence[ResearchTrialRecord], oos: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The honest summary of a study. `oos` = the chosen draft's out-of-sample metrics when that check was run; when not
    given, a stored out-of-sample row (status "oos" - the check, not a trial) is used."""
    stored_oos = [t for t in trials if t.status == "oos"]
    if oos is None and stored_oos:
        oos = {"run": True, **json.loads(stored_oos[-1].metrics_json or "{}")}
    trials = [t for t in trials if t.status != "oos"]
    ok = [t for t in trials if t.status == "ok"]
    out: Dict[str, Any] = {"trials": len(trials), "backtested": len(ok), "invalid": sum(1 for t in trials if t.status == "invalid"),
                           "errors": sum(1 for t in trials if t.status == "error"), "not_simulated": list(NOT_SIMULATED),
                           "disclaimer": DISCLAIMER, "chosen": None, "deflated": None, "pbo": None,
                           "out_of_sample": oos if oos is not None else {"run": False, "note": "Out-of-sample check not run yet."}}
    if not ok:
        out["summary"] = "No trial was backtested, so there is nothing to choose."
        return out
    rets = {t.seq: np.asarray(json.loads(t.returns_json), dtype=float) for t in ok}
    sharpes = {seq: sharpe(r) for seq, r in rets.items()}
    best = max(ok, key=lambda t: (sharpes[t.seq], -t.seq))
    dsr = deflated_sharpe(rets[best.seq], list(sharpes.values()))
    out["chosen"] = {"seq": best.seq, "dsl_hash": best.dsl_hash, "in_sample_sharpe_daily": round(sharpes[best.seq], 4),
                     "metrics": json.loads(best.metrics_json or "{}")}
    out["deflated"] = dsr
    lengths = {len(r) for r in rets.values()}
    if len(ok) >= 2 and len(lengths) == 1 and next(iter(lengths)) >= MIN_DAYS_FOR_PBO:
        matrix = np.column_stack([rets[t.seq] for t in ok])
        out["pbo"] = {k: v for k, v in pbo_cscv(matrix, S=_pbo_blocks(matrix.shape[0]), max_combos=500).items() if k != "logits"}
    else:
        out["pbo"] = {"pbo": None, "note": "Needs at least 2 backtested trials on the same window of at least "
                                           f"{MIN_DAYS_FOR_PBO} trading days."}
    dsr_value = dsr.get("dsr")
    verdict = ("weak" if dsr_value is None or dsr_value < 0.5 else "moderate" if dsr_value < 0.95 else "strong")
    out["summary"] = (f"Chosen from {len(ok)} backtested trials ({len(trials)} drafts). Deflated Sharpe probability "
                      f"{'n/a' if dsr_value is None else f'{dsr_value:.2f}'} - evidence {verdict} after the search.")
    return out


__all__ = ["record_trial", "study_trials", "study_report", "daily_returns", "trading_days", "split_window", "dsl_hash",
           "NOT_SIMULATED", "DISCLAIMER", "HoldoutError"]
