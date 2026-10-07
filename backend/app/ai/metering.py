"""P0.8-C / C5: every provider call is metered - calls, tokens in and out, USD - per tenant with the feature, provider
and model on the record (`usage_records`, metrics `ai_calls`, `ai_tokens_input`, `ai_tokens_output`, `ai_cost_usd`),
and a plan's monthly budget in rupees turns the tenant over to the rule-based provider once it is spent (until the
first of the next month). `MeteredProvider` wraps the tenant's real provider so the eleven call sites need no change;
recording failures are logged and never fail the AI call itself.
"""
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import pricing
from app.ai.providers import Completion, LLMProvider, ProviderError
from app.billing.service import meter
from app.db.models import Tenant, UsageRecord
from app.observability.metrics import AI_COST_USD, AI_TOKENS
from app.plans.registry import get_plan

logger = logging.getLogger(__name__)

METRIC_CALLS = "ai_calls"
METRIC_TOKENS_IN = "ai_tokens_input"
METRIC_TOKENS_OUT = "ai_tokens_output"
METRIC_COST_USD = "ai_cost_usd"
AI_METRICS = (METRIC_CALLS, METRIC_TOKENS_IN, METRIC_TOKENS_OUT, METRIC_COST_USD)


def month_start(now: Optional[datetime] = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def budget_inr(tenant: Tenant) -> float:
    """The plan's monthly AI budget in rupees; 0 = no cap (the Free plan never reaches a provider anyway)."""
    return float(getattr(get_plan(tenant.plan), "ai_monthly_budget_inr", 0.0) or 0.0)


async def record(session: AsyncSession, tenant_id: int, feature: str, provider: str, model: str, result: Completion, *, source: str = "api") -> float:
    """Write the four usage rows for one call and return the USD cost. Called for successful answers and for failed
    attempts that still consumed tokens (a refusal, an answer cut off twice)."""
    if result.cost_usd_override is not None:
        cost = pricing.Cost(amount=float(result.cost_usd_override), estimated=False, price=(0.0, 0.0, 0.0))
    else:
        cost = pricing.cost_usd(provider, model, input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                                cache_read_tokens=result.cache_read_tokens, cache_write_tokens=result.cache_write_tokens)
    meta = {"feature": feature, "provider": provider, "model": model, "cache_read": result.cache_read_tokens, "cache_write": result.cache_write_tokens,
            "estimated": cost.estimated, "stop_reason": result.stop_reason}
    await meter(session, tenant_id, METRIC_CALLS, 1, source=source, metadata=meta, commit=False)
    await meter(session, tenant_id, METRIC_TOKENS_IN, float(result.input_tokens + result.cache_read_tokens + result.cache_write_tokens), source=source, metadata=meta, commit=False)
    await meter(session, tenant_id, METRIC_TOKENS_OUT, float(result.output_tokens), source=source, metadata=meta, commit=False)
    await meter(session, tenant_id, METRIC_COST_USD, cost.amount, source=source, metadata=meta, commit=True)
    try:
        AI_TOKENS.labels(provider=provider, model=model, kind="input").inc(result.input_tokens + result.cache_read_tokens + result.cache_write_tokens)
        AI_TOKENS.labels(provider=provider, model=model, kind="output").inc(result.output_tokens)
        AI_COST_USD.labels(provider=provider, model=model).inc(cost.amount)
    except Exception:  # noqa: BLE001 - metrics are best effort
        pass
    return cost.amount


async def month_usage(session: AsyncSession, tenant_id: int, now: Optional[datetime] = None) -> Dict[str, Any]:
    """This month's AI usage of one organisation: totals per metric and the USD spend per feature and per model."""
    since = month_start(now)
    rows = await session.execute(select(UsageRecord.metric, func.sum(UsageRecord.quantity)).where(
        UsageRecord.tenant_id == tenant_id, UsageRecord.metric.in_(AI_METRICS), UsageRecord.period_start >= since).group_by(UsageRecord.metric))
    totals = {metric: float(total or 0.0) for metric, total in rows.all()}
    by_feature: Dict[str, float] = {}
    by_model: Dict[str, float] = {}
    cost_rows = await session.execute(select(UsageRecord.quantity, UsageRecord.metadata_json).where(
        UsageRecord.tenant_id == tenant_id, UsageRecord.metric == METRIC_COST_USD, UsageRecord.period_start >= since))
    for quantity, meta_json in cost_rows.all():
        try:
            meta = json.loads(meta_json or "{}")
        except ValueError:
            meta = {}
        feature = str(meta.get("feature") or "other")
        model = str(meta.get("model") or "?")
        by_feature[feature] = round(by_feature.get(feature, 0.0) + float(quantity or 0.0), 8)
        by_model[model] = round(by_model.get(model, 0.0) + float(quantity or 0.0), 8)
    spent_usd = totals.get(METRIC_COST_USD, 0.0)
    return {"month": since.strftime("%Y-%m"), "calls": int(totals.get(METRIC_CALLS, 0)), "tokens_input": int(totals.get(METRIC_TOKENS_IN, 0)),
            "tokens_output": int(totals.get(METRIC_TOKENS_OUT, 0)), "spent_usd": round(spent_usd, 6), "spent_inr": pricing.usd_to_inr(spent_usd),
            "by_feature": by_feature, "by_model": by_model, "usd_inr_rate": pricing.usd_inr_rate()}


async def budget_state(session: AsyncSession, tenant: Tenant, now: Optional[datetime] = None) -> Dict[str, Any]:
    usage = await month_usage(session, tenant.id, now)
    budget = budget_inr(tenant)
    exhausted = budget > 0 and usage["spent_inr"] >= budget
    note = (f"The plan's AI budget for {usage['month']} ({budget:,.0f} INR) is spent; answers come from the rules until the 1st."
            if exhausted else (f"{usage['spent_inr']:,.2f} of {budget:,.0f} INR used this month." if budget > 0 else "No monthly AI budget on this plan."))
    return {**usage, "budget_inr": budget, "exhausted": exhausted, "note": note}


async def budget_exhausted(session: AsyncSession, tenant: Tenant) -> bool:
    budget = budget_inr(tenant)
    if budget <= 0:
        return False
    spent = await session.scalar(select(func.coalesce(func.sum(UsageRecord.quantity), 0.0)).where(
        UsageRecord.tenant_id == tenant.id, UsageRecord.metric == METRIC_COST_USD, UsageRecord.period_start >= month_start()))
    return pricing.usd_to_inr(float(spent or 0.0)) >= budget


@dataclass
class MeteredProvider:
    """The tenant's provider with the meter attached: same `complete`/`complete_full`, every answer (and every failed
    attempt that reported tokens) recorded against the tenant and the feature."""

    inner: LLMProvider
    session: AsyncSession
    tenant_id: int
    feature: str
    source: str = "api"

    @property
    def name(self) -> str:
        return self.inner.name

    @property
    def model(self) -> str:
        return self.inner.model

    @property
    def reason(self) -> str:
        return str(getattr(self.inner, "reason", "") or "")

    async def _record(self, result: Completion) -> None:
        try:
            await record(self.session, self.tenant_id, self.feature, self.inner.name, result.model or self.inner.model, result, source=self.source)
        except Exception as exc:  # noqa: BLE001 - metering must never fail the answer
            logger.warning("AI usage not recorded for tenant %s (%s): %s", self.tenant_id, self.feature, exc)

    async def complete_full(self, system: str, user: str, *, max_tokens: int = 2000) -> Completion:
        try:
            result = await self.inner.complete_full(system, user, max_tokens=max_tokens)
        except ProviderError as exc:
            if exc.usage is not None and (exc.usage.input_tokens or exc.usage.output_tokens):
                await self._record(exc.usage)
            raise
        await self._record(result)
        return result

    async def complete(self, system: str, user: str, *, max_tokens: int = 2000) -> str:
        return (await self.complete_full(system, user, max_tokens=max_tokens)).text
