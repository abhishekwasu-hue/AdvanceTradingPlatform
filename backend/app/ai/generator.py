"""Phase L2: the AI strategy generator behind a review gate (section 56, V4.3, safety rule 16).

The model is asked for one JSON object in the `CustomStrategyConfig` contract (the same schema
the no-code builder saves), plus an explanation and its own caveats. The answer is validated
by the same pydantic model that guards `POST /api/custom-strategies`; an invalid answer is
retried once with the validation error quoted back, then recorded as FAILED. A validated draft
is only a *draft*: it becomes a custom strategy solely through `approve()`, which requires a
saved backtest run on the draft and a human user's explicit call, and stamps the strategy with
`origin="ai:<draft>"` and `ai_approved_by`. Nothing generated here can be deployed otherwise.
"""
import json
import re
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import httpx
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import settings as ai_settings
from app.ai.providers import LLMProvider, ProviderError
from app.audit.log import write_audit_log
from app.custom_strategies import versioning
from app.db.models import AiStrategyDraftRecord, BacktestRunRecord, CustomStrategyRecord, Tenant, User
from app.plans.limits import check_can_add_custom_strategy
from app.strategy_engine.declarative import CustomStrategyConfig

SYSTEM_PROMPT = """You design rule-based intraday trading strategies for Indian equities and index derivatives.
Answer with ONE JSON object and nothing else, shaped exactly like:
{"config": {"name": str, "timeframe": one of ["1min","3min","5min","15min","30min","60min"],
  "long_conditions": [Condition], "short_conditions": [Condition],
  "stop_loss_atr_mult": number > 0, "atr_period": int 2..500, "target_rr": [number, number], "min_rr": number > 0},
 "explanation": str, "warnings": [str]}
Condition = {"left": Operand, "operator": one of ["GT","LT","GTE","LTE","CROSSES_ABOVE","CROSSES_BELOW"], "right": Operand}
Operand = {"type": "value", "value": number} or {"type": "indicator", "indicator": one of
  ["EMA","SMA","RSI","ADX","PLUS_DI","MINUS_DI","ATR","SUPERTREND","CLOSE","OPEN","HIGH","LOW"], "period": int 1..500}
Rules: every condition set is AND-combined; use at most 4 conditions per side; at least one side non-empty;
prefer conditions a human can verify on a chart; never promise returns; list what could make the strategy fail
in "warnings" (regimes, costs, slippage, over-fitting). Output must be valid JSON - no markdown fences."""

MAX_ATTEMPTS = 2


class GenerationError(RuntimeError):
    pass


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("no JSON object in the answer")
    return json.loads(text[start:end + 1])


def parse_answer(text: str) -> Tuple[CustomStrategyConfig, str, List[str]]:
    data = _extract_json(text)
    config = CustomStrategyConfig.model_validate(data.get("config", data))
    explanation = str(data.get("explanation") or "").strip()
    warnings = [str(w) for w in (data.get("warnings") or []) if str(w).strip()]
    return config, explanation, warnings


async def generate(session: AsyncSession, tenant: Tenant, user: User, prompt: str, *, client: Optional[httpx.AsyncClient] = None,
                   provider: Optional[LLMProvider] = None) -> AiStrategyDraftRecord:
    provider = provider or await ai_settings.provider_for(session, tenant, client=client)
    draft = AiStrategyDraftRecord(tenant_id=tenant.id, user_id=user.id, prompt=prompt.strip(), provider=provider.name, model=provider.model)
    session.add(draft)
    user_message = f"USER REQUEST:\n{prompt.strip()}"
    last_error: Optional[str] = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            raw = await provider.complete(SYSTEM_PROMPT, user_message if attempt == 0 else f"{user_message}\n\nYour previous answer was invalid: {last_error}. Answer again with valid JSON only.")
        except ProviderError as exc:
            draft.status, draft.explanation = "FAILED", str(exc)
            await ai_settings.mark_used(session, tenant.id, error=str(exc))
            await session.commit()
            await session.refresh(draft)
            return draft
        draft.raw_response = raw
        try:
            config, explanation, warnings = parse_answer(raw)
        except (ValueError, ValidationError) as exc:
            last_error = str(exc)[:400]
            continue
        draft.config_json = config.model_dump_json()
        draft.explanation = explanation or None
        draft.warnings_json = json.dumps(warnings)
        draft.status = "DRAFT"
        break
    else:
        draft.status, draft.explanation = "FAILED", f"The model did not return a valid strategy: {last_error}"
    await ai_settings.mark_used(session, tenant.id, error=None if draft.status == "DRAFT" else draft.explanation)
    await write_audit_log(session, tenant.id, user.id, "ai_strategy_generated", f"draft {draft.status} via {provider.name}/{provider.model}")
    await session.commit()
    await session.refresh(draft)
    return draft


def draft_config(draft: AiStrategyDraftRecord) -> CustomStrategyConfig:
    if not draft.config_json:
        raise GenerationError("This draft has no valid strategy configuration")
    return CustomStrategyConfig.model_validate_json(draft.config_json)


async def attach_backtest(session: AsyncSession, draft: AiStrategyDraftRecord, run: BacktestRunRecord) -> AiStrategyDraftRecord:
    if run.tenant_id != draft.tenant_id or run.strategy_id != f"ai_draft_{draft.id}":
        raise GenerationError("The backtest run does not belong to this draft")
    if draft.status not in ("DRAFT", "BACKTESTED"):
        raise GenerationError(f"A {draft.status} draft cannot take a backtest")
    draft.backtest_run_id = run.id
    draft.status = "BACKTESTED"
    await session.commit()
    await session.refresh(draft)
    return draft


async def approve(session: AsyncSession, draft: AiStrategyDraftRecord, user: User, tenant: Tenant, *, name: Optional[str] = None) -> CustomStrategyRecord:
    """The human gate: a backtested draft becomes a real custom strategy with lineage stamped on
    it. It then runs through the ordinary backtest -> paper -> live path like any strategy."""
    if draft.status != "BACKTESTED" or draft.backtest_run_id is None:
        raise GenerationError("Backtest the draft first - an AI strategy is approved only after a human has seen its backtest (safety rule 16)")
    config = draft_config(draft)
    if name:
        config = config.model_copy(update={"name": name.strip()[:200]})
    await check_can_add_custom_strategy(session, tenant)
    record = CustomStrategyRecord(tenant_id=tenant.id, user_id=user.id, name=config.name, config_json=config.model_dump_json(),
                                  origin=f"ai:{draft.id}", ai_approved_by=user.id)
    session.add(record)
    await session.flush()
    await versioning.create_version(session, record, config, user, source=f"ai:{draft.id}")
    draft.status, draft.custom_strategy_id = "APPROVED", record.id
    draft.approved_by, draft.approved_at = user.id, datetime.now(timezone.utc)
    await write_audit_log(session, tenant.id, user.id, "ai_strategy_approved", f"draft {draft.id} -> strategy {record.id} ({config.name})")
    await session.commit()
    await session.refresh(record)
    return record


async def reject(session: AsyncSession, draft: AiStrategyDraftRecord, user: User, note: Optional[str]) -> AiStrategyDraftRecord:
    if draft.status == "APPROVED":
        raise GenerationError("An approved draft is already a strategy - delete the strategy instead")
    draft.status = "REJECTED"
    if note:
        draft.explanation = f"{draft.explanation or ''}\n\nRejected: {note.strip()}".strip()
    await write_audit_log(session, draft.tenant_id, user.id, "ai_strategy_rejected", f"draft {draft.id} {note or ''}".strip())
    await session.commit()
    await session.refresh(draft)
    return draft


def as_dict(draft: AiStrategyDraftRecord, *, include_raw: bool = False) -> dict:
    out = {
        "id": draft.id, "prompt": draft.prompt, "provider": draft.provider, "model": draft.model, "status": draft.status,
        "config": json.loads(draft.config_json) if draft.config_json else None, "explanation": draft.explanation,
        "warnings": json.loads(draft.warnings_json or "[]"), "backtest_run_id": draft.backtest_run_id,
        "custom_strategy_id": draft.custom_strategy_id, "strategy_id": f"custom_{draft.custom_strategy_id}" if draft.custom_strategy_id else None,
        "approved_by": draft.approved_by, "approved_at": draft.approved_at.isoformat() if draft.approved_at else None,
        "created_at": draft.created_at.isoformat() if draft.created_at else None,
        "lineage": {"provider": draft.provider, "model": draft.model, "prompt_chars": len(draft.prompt), "generated_at": draft.created_at.isoformat() if draft.created_at else None},
        "disclaimer": "Generated rules are a starting point, not advice. Backtest, paper-trade and review every condition before approving.",
    }
    if include_raw:
        out["raw_response"] = draft.raw_response
    return out
