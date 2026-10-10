"""H-C3b (spec C3): the strategy research loop - idea -> draft -> validate -> backtest in-sample -> diagnose -> revise,
at most `max_drafts` times, then ONE out-of-sample run of the chosen draft, then the honest report (H-C3a).

* Data: server bars only (the agent's market tools), split in-sample / out-of-sample; the sealed holdout is refused.
* Every draft - valid or not - is a trial in the ledger, so the report deflates by the real size of the search.
* The proposer (an LLM in production, a fake in tests) sees the idea and the earlier trials' metrics; that is the
  "diagnose and revise" step. Its output is untrusted: it must parse as a `CustomStrategyConfig` and construct a
  `DeclarativeStrategy`, or the trial is recorded as invalid.
* Nothing is deployed or saved as a strategy here. The report is shown to the trader; adopting a draft still goes
  through the existing human approval gate (ADR-0006).
"""
from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

import pandas as pd
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import research
from app.backtest.data_policy import HoldoutError

MAX_DRAFTS = 8                       # H-9 provisional
OOS_FRACTION = 0.3                   # H-9 provisional

# propose(idea, history) -> a draft (dict) or None to stop early; history = earlier trials as plain dicts.
Propose = Callable[[str, List[Dict[str, Any]]], Awaitable[Optional[Dict[str, Any]]]]


@dataclass
class StudyInput:
    idea: str
    symbol: str
    exchange: str
    timeframe: str
    max_drafts: int = MAX_DRAFTS


def _validate(draft: Dict[str, Any], timeframe: str) -> Any:
    from app.strategy_engine.declarative import CustomStrategyConfig, DeclarativeStrategy
    config = CustomStrategyConfig.model_validate({**draft, "timeframe": timeframe})
    return DeclarativeStrategy("research:draft", config)


def _metrics(result: Any) -> Dict[str, Any]:
    return {"trades": result.total_trades, "win_rate": round(float(result.win_rate), 2), "net_pnl": round(float(result.net_pnl), 2),
            "profit_factor": None if result.profit_factor is None else round(float(result.profit_factor), 3),
            "max_drawdown": round(float(result.max_drawdown), 2), "expectancy": round(float(result.expectancy), 2)}


async def _backtest(strategy: Any, df: pd.DataFrame, symbol: str, timeframe: str, risk: Any) -> Any:
    from app.backtest.engine import run_backtest
    return await asyncio.to_thread(run_backtest, strategy, df, symbol, timeframe, risk)      # CPU-bound: off the event loop


async def run_study(session: AsyncSession, *, tenant_id: int, user_id: Optional[int], spec: StudyInput, frame: pd.DataFrame,
                    risk: Any, propose: Propose, holdout_boundary: Any = None, study_id: Optional[str] = None) -> Dict[str, Any]:
    """Runs one study on `frame` (server bars, oldest first). Returns {study_id, report}. Commits the ledger as it goes,
    so a crash mid-study still leaves every trial tried."""
    if frame.empty:
        raise ValueError("no bars to research on")
    max_drafts = max(1, min(spec.max_drafts, MAX_DRAFTS))
    start, cut, end = research.split_window(frame.index, OOS_FRACTION, holdout_boundary)        # HoldoutError before any work
    is_df, oos_df = frame[frame.index < cut], frame[frame.index >= cut]
    is_days, oos_days = research.trading_days(is_df.index), research.trading_days(oos_df.index)
    study_id = study_id or str(uuid.uuid4())
    history: List[Dict[str, Any]] = []
    drafts: Dict[int, Dict[str, Any]] = {}

    async def trial(**kw: Any) -> Any:
        return await research.record_trial(session, tenant_id=tenant_id, user_id=user_id, study_id=study_id, symbol=spec.symbol,
                                           exchange=spec.exchange, timeframe=spec.timeframe, **kw)

    for _ in range(max_drafts):
        try:
            draft = await propose(spec.idea, history)
        except Exception as exc:  # noqa: BLE001 - a failed proposer ends the search; what was tried stays recorded
            history.append({"status": "error", "reason": f"proposer failed: {type(exc).__name__}"})
            break
        if draft is None:
            break
        if not isinstance(draft, dict):
            draft = {"raw": str(draft)[:2000]}
        try:
            strategy = _validate(draft, spec.timeframe)
        except (ValidationError, ValueError, TypeError) as exc:
            row = await trial(dsl=draft, status="invalid", reason=str(exc)[:300])
            history.append({"seq": row.seq, "status": "invalid", "reason": row.reason})
            await session.commit()
            continue
        try:
            result = await _backtest(strategy, is_df, spec.symbol, spec.timeframe, risk)
        except Exception as exc:  # noqa: BLE001 - an engine failure is a trial too
            row = await trial(dsl=draft, status="error", reason=f"{type(exc).__name__}: {exc}"[:300])
            history.append({"seq": row.seq, "status": "error", "reason": row.reason})
            await session.commit()
            continue
        metrics = _metrics(result)
        returns = research.daily_returns(result.trades, float(risk.capital), is_days)
        row = await trial(dsl=draft, status="ok", metrics=metrics, returns=returns, data_from=start.to_pydatetime(),
                          data_to=cut.to_pydatetime())
        drafts[row.seq] = draft
        history.append({"seq": row.seq, "status": "ok", "metrics": metrics})
        await session.commit()
    trials = await research.study_trials(session, tenant_id, study_id)
    report = research.study_report(trials)
    chosen = report.get("chosen")
    if chosen and chosen["seq"] in drafts:
        strategy = _validate(drafts[chosen["seq"]], spec.timeframe)
        oos = await _backtest(strategy, oos_df, spec.symbol, spec.timeframe, risk)
        oos_returns = research.daily_returns(oos.trades, float(risk.capital), oos_days)
        check = {"chosen_seq": chosen["seq"], "from": cut.isoformat(), "to": end.isoformat(), "metrics": _metrics(oos),
                 "sharpe_daily": round(research.sharpe(oos_returns), 4) if oos_returns else None}
        await trial(dsl=drafts[chosen["seq"]], status="oos", metrics=check, data_from=cut.to_pydatetime(), data_to=end.to_pydatetime())
        await session.commit()                                       # stored as the study's check, not as another trial
        report = research.study_report(await research.study_trials(session, tenant_id, study_id))
    return {"study_id": study_id, "report": report}


SYSTEM = ("You draft rule-based intraday strategies for backtesting only. Reply with ONE JSON object in the rule schema, "
          "or null. No prose. You never place orders and your drafts are not recommendations.")


def _first_json(text: str) -> Any:
    """The first JSON value in a model reply (a bare object, or one inside prose / a code fence); None for `null`."""
    text = (text or "").strip()
    if text.lower() in ("null", "none", ""):
        return None
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch in "{[":
            try:
                return decoder.raw_decode(text[i:])[0]
            except ValueError:
                continue
    return {"raw": text[:2000]}


def llm_proposer(provider: Any) -> Propose:
    """The production proposer: the tenant's metered provider drafts the next rule set from the idea and earlier trials.
    The reply is untrusted - a non-object becomes an invalid draft (`{"raw": ...}`), `null` ends the study."""
    from app.strategy_engine.declarative import CustomStrategyConfig
    schema = CustomStrategyConfig.model_json_schema()

    async def propose(idea: str, history: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        reply = await provider.complete(SYSTEM, draft_prompt(idea, history, schema), max_tokens=2000)
        value = _first_json(reply)
        if value is None:
            return None
        return value if isinstance(value, dict) else {"raw": json.dumps(value)[:2000]}
    return propose


def draft_prompt(idea: str, history: List[Dict[str, Any]], schema: Dict[str, Any]) -> str:
    """The proposer's user prompt: the idea, the rule schema and the earlier trials (metrics only - never raw data)."""
    return json.dumps({"idea": idea[:1000], "rule_schema": schema, "earlier_trials": history[-MAX_DRAFTS:],
                       "instructions": "Return ONE JSON object for the next draft (name, long_conditions and/or short_conditions, "
                                       "stop_loss_atr_mult, target_rr). Change one thing at a time based on the earlier trials. "
                                       "Return null when nothing worth trying is left."}, default=str)


__all__ = ["run_study", "StudyInput", "Propose", "MAX_DRAFTS", "OOS_FRACTION", "draft_prompt", "llm_proposer", "HoldoutError"]
