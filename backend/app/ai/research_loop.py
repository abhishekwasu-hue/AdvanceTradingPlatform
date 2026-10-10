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
from datetime import timedelta
from typing import Any, Awaitable, Callable, Dict, List, Optional

import pandas as pd
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import prompt_versions, research
from app.backtest.data_policy import HoldoutError

MAX_DRAFTS = 8                       # H-9 provisional
OOS_FRACTION = 0.3                   # H-9 provisional
EARLIER_STUDIES_DAYS = 30            # H-9 provisional: studies on the same symbol and timeframe counted in the report

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


def _trade_metrics(trades: List[Any]) -> Dict[str, Any]:
    """The `_metrics` keys from a trade list (the out-of-sample trades only, without the warm-up's)."""
    pnls = [float(t.pnl) for t in trades if t.pnl is not None]
    wins, losses = [p for p in pnls if p > 0], [p for p in pnls if p < 0]
    equity, peak, drawdown = 0.0, 0.0, 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {"trades": len(pnls), "win_rate": round(100.0 * len(wins) / len(pnls), 2) if pnls else 0.0, "net_pnl": round(sum(pnls), 2),
            "profit_factor": round(sum(wins) / -sum(losses), 3) if losses else None, "max_drawdown": round(drawdown, 2),
            "expectancy": round(sum(pnls) / len(pnls), 2) if pnls else 0.0}


def _aware(ts: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(ts)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp


def oos_trades_only(trades: List[Any], cut: pd.Timestamp) -> List[Any]:
    """The out-of-sample trades: entered at or after `cut` (the warm-up before it computes indicators, never trades)."""
    return [t for t in trades if _aware(t.entry_time) >= cut]


def _warmup_bars(strategy: Any) -> int:
    try:
        return max([int(v) for v in strategy.min_history().values()] or [0])
    except Exception:  # noqa: BLE001 - a strategy without min_history gets no warm-up
        return 0


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
    earlier = await research.earlier_studies(session, tenant_id, spec.symbol, spec.timeframe, study_id,
                                             since=pd.Timestamp.now(tz="UTC").to_pydatetime() - timedelta(days=EARLIER_STUDIES_DAYS))
    if chosen and chosen["seq"] in drafts:
        strategy = _validate(drafts[chosen["seq"]], spec.timeframe)
        # Indicators need history: the run starts `warm-up` bars before the cut, and only trades ENTERED at or after the
        # cut count. Reading earlier bars to compute indicators is not leakage; trading on them would be.
        warm = is_df.iloc[-_warmup_bars(strategy):] if _warmup_bars(strategy) else is_df.iloc[:0]
        try:
            oos = await _backtest(strategy, pd.concat([warm, oos_df]), spec.symbol, spec.timeframe, risk)
            oos_trades = oos_trades_only(oos.trades, cut)
            oos_returns = research.daily_returns(oos_trades, float(risk.capital), oos_days)
            check: Dict[str, Any] = {"chosen_seq": chosen["seq"], "from": cut.isoformat(), "to": end.isoformat(), "warmup_bars": len(warm),
                                     "metrics": _trade_metrics(oos_trades),
                                     "sharpe_daily": round(research.sharpe(oos_returns), 4) if oos_returns else None}
        except Exception as exc:  # noqa: BLE001 - the trials stay recorded; the report says the check failed
            check = {"chosen_seq": chosen["seq"], "error": f"{type(exc).__name__}: {exc}"[:300]}
        await trial(dsl=drafts[chosen["seq"]], status="oos", metrics=check, data_from=cut.to_pydatetime(), data_to=end.to_pydatetime())
        await session.commit()                                       # stored as the study's check, not as another trial
    report = research.study_report(await research.study_trials(session, tenant_id, study_id), earlier=earlier)
    return {"study_id": study_id, "report": report}


SYSTEM = ("You draft rule-based intraday strategies for backtesting only. Reply with ONE JSON object in the rule schema, "
          "or null. No prose. You never place orders and your drafts are not recommendations.")
PROMPT_VERSION = prompt_versions.version_of("research", SYSTEM)        # H-C1 f: the llm_calls row names this prompt


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
    prompt_versions.stamp(provider, PROMPT_VERSION)

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


__all__ = ["run_study", "StudyInput", "Propose", "MAX_DRAFTS", "OOS_FRACTION", "PROMPT_VERSION", "draft_prompt", "llm_proposer", "HoldoutError"]
