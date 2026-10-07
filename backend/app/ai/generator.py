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
from app.ai.compliance import CheckResult, ComplianceReport, evaluate_config
from app.ai.prompt import PROMPT_VERSION, DeploymentSuggestion, build_runtime_context, build_system_prompt, parse_suggestion, user_message as prompt_user_message
from app.core.models import RiskConfig
from app.observability.metrics import AI_PROVIDER_CALLS

LEGACY_SYSTEM_PROMPT = """You design rule-based intraday trading strategies for Indian equities and index derivatives.
Answer with ONE JSON object and nothing else, shaped exactly like:
{"config": {"name": str, "timeframe": one of ["1min","3min","5min","15min","30min","60min"],
  "long_conditions": [Condition], "short_conditions": [Condition],
  "stop_loss_atr_mult": number > 0, "atr_period": int 2..500, "target_rr": [number, number], "min_rr": number > 0},
 "explanation": str, "warnings": [str]}
Condition = {"left": Operand, "operator": one of ["GT","LT","GTE","LTE","CROSSES_ABOVE","CROSSES_BELOW"], "right": Operand}
Operand = {"type": "value", "value": number} or {"type": "indicator", "indicator": one of
  ["EMA","SMA","RSI","ADX","PLUS_DI","MINUS_DI","ATR","SUPERTREND","CLOSE","OPEN","HIGH","LOW","VWAP","DAY_OPEN","OR_HIGH","OR_LOW","PDH","PDL","PDC","BB_UPPER","BB_MID","BB_LOWER","VOLUME","VOLUME_SMA"], "period": int 1..500, "multiplier": number (SUPERTREND; BB_* standard deviations), "timeframe": optional higher timeframe such as "15min" (completed bars only)}
  (OR_HIGH/OR_LOW period = opening-range minutes; PDH/PDL/PDC = previous session high/low/close; VWAP is the session VWAP)
Rules: every condition set is AND-combined; use at most 4 conditions per side; at least one side non-empty;
prefer conditions a human can verify on a chart; never promise returns; list what could make the strategy fail
in "warnings" (regimes, costs, slippage, over-fitting). Output must be valid JSON - no markdown fences."""

SYSTEM_PROMPT = LEGACY_SYSTEM_PROMPT   # Phase V3: superseded by app.ai.prompt.build_system_prompt; kept for reference/tests
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
    config, explanation, warnings, _ = parse_answer_full(text)
    return config, explanation, warnings


def parse_answer_full(text: str) -> Tuple[CustomStrategyConfig, str, List[str], Optional[DeploymentSuggestion]]:
    """Phase V3: the config plus the deployment suggestion the model attached (None when absent)."""
    data = _extract_json(text)
    config = CustomStrategyConfig.model_validate(data.get("config", data))
    explanation = str(data.get("explanation") or "").strip()
    warnings = [str(w) for w in (data.get("warnings") or []) if str(w).strip()]
    suggestion, extra = parse_suggestion(data.get("deployment"))
    return config, explanation, warnings + extra, suggestion


async def generate(session: AsyncSession, tenant: Tenant, user: User, prompt: str, *, client: Optional[httpx.AsyncClient] = None,
                   provider: Optional[LLMProvider] = None, regime: Optional[str] = None, language: str = "en",
                   symbol: Optional[str] = None) -> AiStrategyDraftRecord:
    provider = provider or await ai_settings.provider_for(session, tenant, client=client, task="strategy_generation")
    cfg, ceilings = await _effective_risk(session, tenant.id)
    # Phase V3: the versioned guardian prompt, filled from the tenant's live state.
    context = await build_runtime_context(session, tenant, user, cfg, ceilings, regime=regime, language=language, symbol=symbol)
    system_prompt = build_system_prompt(context)
    draft = AiStrategyDraftRecord(tenant_id=tenant.id, user_id=user.id, prompt=prompt.strip(), provider=provider.name, model=provider.model,
                                  prompt_version=PROMPT_VERSION, context_json=json.dumps(context.as_dict()))
    session.add(draft)
    user_message = prompt_user_message(prompt)
    last_error: Optional[str] = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            raw = await provider.complete(system_prompt, user_message if attempt == 0 else f"{user_message}\n\nYour previous answer was invalid: {last_error}. Answer again with valid JSON only.")
            AI_PROVIDER_CALLS.labels(provider=provider.name, outcome="ok").inc()
        except ProviderError as exc:
            AI_PROVIDER_CALLS.labels(provider=provider.name, outcome="error").inc()
            draft.status, draft.explanation = "FAILED", str(exc)
            await ai_settings.mark_used(session, tenant.id, error=str(exc))
            await session.commit()
            await session.refresh(draft)
            return draft
        draft.raw_response = raw
        try:
            config, explanation, warnings, suggestion = parse_answer_full(raw)
        except (ValueError, ValidationError) as exc:
            last_error = str(exc)[:400]
            continue
        # Phase V2: the compliance checklist. A draft-level failure gets one AI auto-fix round
        # (the errors go back with the request); on the last attempt the deterministic fixes
        # apply, so what is saved is compliant either way and the fixes are on the record.
        final_attempt = attempt == MAX_ATTEMPTS - 1
        config, report = evaluate_config(config, cfg, ceilings, autofix=final_attempt, suggestion=suggestion, currency=context.currency)
        if report.failed and not final_attempt:
            last_error = f"it violated the risk rules - {report.failure_text()}"
            continue
        draft.config_json = config.model_dump_json()
        draft.explanation = explanation or None
        draft.warnings_json = json.dumps(warnings)
        draft.compliance_json = json.dumps(report.as_dict())
        draft.deployment_json = suggestion.model_dump_json() if suggestion is not None else None
        draft.status = "DRAFT"
        break
    else:
        draft.status, draft.explanation = "FAILED", f"The model did not return a valid strategy: {last_error}"
    await ai_settings.mark_used(session, tenant.id, error=None if draft.status == "DRAFT" else draft.explanation)
    await write_audit_log(session, tenant.id, user.id, "ai_strategy_generated", f"draft {draft.status} via {provider.name}/{provider.model}")
    await session.commit()
    await session.refresh(draft)
    return draft


async def _effective_risk(session: AsyncSession, tenant_id: int):
    from app.platform.controls import clamp_config, risk_ceilings
    from app.risk_engine.routes import get_tenant_risk_config
    ceilings = await risk_ceilings(session)
    cfg, _ = clamp_config(await get_tenant_risk_config(tenant_id, session) or RiskConfig(), ceilings)
    return cfg, ceilings


def draft_compliance(draft: AiStrategyDraftRecord) -> Optional[ComplianceReport]:
    if not draft.compliance_json:
        return None
    data = json.loads(draft.compliance_json)
    report = ComplianceReport(checks=[CheckResult(c["rule"], c["status"], c["detail"], bool(c.get("fixed"))) for c in data.get("checks", [])],
                              fixes=list(data.get("fixes") or []), user_must_accept=dict(data.get("user_must_accept") or {}), evidence=data.get("evidence"))
    return report


def draft_suggestion(draft: AiStrategyDraftRecord) -> Optional[DeploymentSuggestion]:
    if not draft.deployment_json:
        return None
    try:
        return DeploymentSuggestion.model_validate_json(draft.deployment_json)
    except ValidationError:
        return None


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
    # Phase V2: judge the evidence and keep it on the record.
    try:
        metrics = json.loads(run.metrics_json or "{}")
    except ValueError:
        metrics = {}
    cfg, ceilings = await _effective_risk(session, draft.tenant_id)
    _, report = evaluate_config(draft_config(draft), cfg, ceilings, backtest=metrics, suggestion=draft_suggestion(draft))
    draft.compliance_json = json.dumps(report.as_dict())
    await session.commit()
    await session.refresh(draft)
    return draft


async def approve(session: AsyncSession, draft: AiStrategyDraftRecord, user: User, tenant: Tenant, *, name: Optional[str] = None,
                  accept_risk: bool = False) -> CustomStrategyRecord:
    """The human gate: a backtested draft becomes a real custom strategy with lineage stamped on
    it. It then runs through the ordinary backtest -> paper -> live path like any strategy.
    Phase V2: the compliance checklist must have no failures and the human must confirm the
    "user must accept" statement (maximum loss per trade, worst case)."""
    if draft.status != "BACKTESTED" or draft.backtest_run_id is None:
        raise GenerationError("Backtest the draft first - an AI strategy is approved only after a human has seen its backtest (safety rule 16)")
    report = draft_compliance(draft)
    if report is not None and not report.ok:
        raise GenerationError(f"Resolve the compliance failures first: {report.failure_text()}")
    if not accept_risk:
        statement = (report.user_must_accept.get("max_loss_per_trade_text") if report is not None else None) or "the maximum loss per trade"
        raise GenerationError(f"Confirm that you accept the risk before approving (accept_risk): {statement}")
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
        "compliance": json.loads(draft.compliance_json) if draft.compliance_json else None,
        "deployment": json.loads(draft.deployment_json) if getattr(draft, "deployment_json", None) else None,
        "deployment_text": (draft_suggestion(draft).describe() if getattr(draft, "deployment_json", None) and draft_suggestion(draft) else None),
        "prompt_version": getattr(draft, "prompt_version", None),
        "runtime_context": json.loads(draft.context_json) if getattr(draft, "context_json", None) else None,
        "custom_strategy_id": draft.custom_strategy_id, "strategy_id": f"custom:{draft.custom_strategy_id}" if draft.custom_strategy_id else None,
        "approved_by": draft.approved_by, "approved_at": draft.approved_at.isoformat() if draft.approved_at else None,
        "created_at": draft.created_at.isoformat() if draft.created_at else None,
        "lineage": {"provider": draft.provider, "model": draft.model, "prompt_chars": len(draft.prompt), "generated_at": draft.created_at.isoformat() if draft.created_at else None},
        "disclaimer": "Generated rules are a starting point, not advice. Backtest, paper-trade and review every condition before approving.",
    }
    if include_raw:
        out["raw_response"] = draft.raw_response
    return out
