"""M72 PR-1b (CP-1, findings F1 / F2, owner decision M72Q-1): the PLATFORM AI key.

The operator's own provider key, kept on the host (environment `AI_PLATFORM_PROVIDER` + `AI_PLATFORM_API_KEY`, or
`AI_PLATFORM_API_KEY_FILE` naming a file that holds it) and never in the repository, the database or a log. It answers a
user-started AI call (`app.ai.resolve.provider_for_user`) when the flag `ai_platform_key` (needs `ui_v2`) is on for the
organisation and neither the user's own key (`ai_one_key`) nor the organisation's own key is there. `ai_one_key` keeps its
meaning (the user's own key in one paste, OKT-9).

Limits - ATP chooses no number (M72Q-1, M68). Each is Not set until the owner sets it, and Not set = the rules answer:
- the plan's monthly platform budget in rupees: Admin (platform control `ai_platform_limits`), else the host's
  `AI_PLATFORM_BUDGET_INR_<PLAN>` (`app.plans.registry.platform_ai_budget_inr`). The Free plan has none until he sets one.
- the per-user daily cap in rupees: Admin, else the host's `AI_PLATFORM_USER_DAILY_CAP_INR`; the user's own lower cap
  (`user_prefs` namespace `ai_usage`) can only lower it.
Both are checked before EVERY model call. A cap reached is a hard stop for the AI, then ATP's rules answer (never another
key). Spend is metered in `usage_records` under the `ai_platform_*` metrics (the organisation's own-key budget never counts
them) with the user on `source`, and every call is logged in `llm_calls` like the organisation's key. Nothing leaves for
the provider before the organisation's owner accepted the current data-sharing consent (P0.8-D), and a read-only support
view never uses it (MO3-A1). The AI never places, blocks or changes an order (ADR-0006).
"""
from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import byok, pricing
from app.ai import providers as ai_providers
from app.ai.metering import MeteredProvider, log_call, month_start
from app.ai.providers import Completion, ProviderError, RuleBasedProvider
from app.audit.log import write_audit_log
from app.billing.service import meter
from app.db.models import Tenant, UsageRecord, User
from app.plans.registry import PLANS, platform_ai_budget_inr

logger = logging.getLogger(__name__)

FLAG = "ai_platform_key"
PAGE_FLAG = "ui_v2"
CONTROL_KEY = "ai_platform_limits"
PREFS_NAMESPACE = "ai_usage"
PLATFORM_PROVIDERS = ("anthropic", "openai")       # the providers `build_provider` builds from a key and the operator's models
ENV_PROVIDER = "AI_PLATFORM_PROVIDER"
ENV_KEY = "AI_PLATFORM_API_KEY"
ENV_KEY_FILE = "AI_PLATFORM_API_KEY_FILE"
ENV_USER_DAILY_CAP = "AI_PLATFORM_USER_DAILY_CAP_INR"

METRIC_CALLS = "ai_platform_calls"
METRIC_TOKENS_IN = "ai_platform_tokens_input"
METRIC_TOKENS_OUT = "ai_platform_tokens_output"
METRIC_COST_USD = "ai_platform_cost_usd"
PLATFORM_METRICS = (METRIC_CALLS, METRIC_TOKENS_IN, METRIC_TOKENS_OUT, METRIC_COST_USD)

# Why the platform key is not answering, in plain words (shown on the Copilot and in Settings › AI).
REASON_BUDGET_NOT_SET = "budget_not_set"
REASON_CAP_NOT_SET = "cap_not_set"
REASON_BUDGET_SPENT = "budget_spent"
REASON_DAILY_CAP = "daily_cap"
REASON_CONSENT = "consent"
MESSAGES = {
    REASON_BUDGET_NOT_SET: "AI answers are not set up for your plan yet, so answers come from ATP's rules.",
    REASON_CAP_NOT_SET: "AI answers are not set up for your plan yet, so answers come from ATP's rules.",
    REASON_BUDGET_SPENT: "Your plan's AI budget for this month is used up. Answers come from ATP's rules until the 1st.",
    REASON_DAILY_CAP: "You reached your AI limit for today. Answers come from ATP's rules until it starts again tomorrow.",
    REASON_CONSENT: "The account owner has not accepted the current data-sharing consent, so answers come from ATP's rules.",
}
LIMIT_REASONS = (REASON_BUDGET_SPENT, REASON_DAILY_CAP)


# --- the operator's key (host only) ----------------------------------------------------------------------------------------

def configured_provider() -> str | None:
    """The platform key's provider when the host has both a known provider and a key, else None. Never returns the key."""
    name = os.environ.get(ENV_PROVIDER, "").strip().lower()
    return name if name in PLATFORM_PROVIDERS and _secret() else None


def _secret() -> str:
    """The key itself, read at call time from the host (never cached on a module, never logged, never returned by a route)."""
    direct = os.environ.get(ENV_KEY, "").strip()
    if direct:
        return direct
    path = os.environ.get(ENV_KEY_FILE, "").strip()
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError as exc:
        logger.warning("The platform AI key file could not be read (%s)", exc.__class__.__name__)
        return ""


def _clean(text: Any) -> str:
    """Error text with the platform key (and anything key- or token-shaped) removed."""
    return byok.sanitize_error(text, (_secret(),))


# --- limits (Admin first, then the host; ATP chooses none) ---------------------------------------------------------------

def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _env_cap() -> float | None:
    return _number(os.environ.get(ENV_USER_DAILY_CAP, "").strip() or None)


async def limits(session: AsyncSession) -> dict[str, Any]:
    """{"plan_budget_inr": {plan id: rupees or None}, "user_daily_cap_inr": rupees or None} - the owner's Admin values,
    else the host's; None = Not set."""
    from app.platform import controls
    stored = await controls._get(session, CONTROL_KEY)
    raw = stored.get("plan_budget_inr")
    budgets: dict[str, Any] = raw if isinstance(raw, dict) else {}
    plan_budget: dict[str, float | None] = {}
    for plan_id in PLANS:
        own = _number(budgets.get(plan_id))
        plan_budget[plan_id] = own if own is not None else platform_ai_budget_inr(plan_id)
    cap = _number(stored.get("user_daily_cap_inr"))
    return {"plan_budget_inr": plan_budget, "user_daily_cap_inr": cap if cap is not None else _env_cap()}


async def set_limits(session: AsyncSession, user: User, *, plan_budget_inr: dict[str, float | None] | None = None,
                     user_daily_cap_inr: float | None = None, clear_cap: bool = False) -> dict[str, Any]:
    """The owner's Admin values. A plan budget of None clears that plan's Admin value (the host's, if any, applies again);
    `clear_cap` clears the daily cap the same way. Negative or non-numbers are refused (ValueError -> 400). Audited."""
    from app.platform import controls
    stored = await controls._get(session, CONTROL_KEY)
    raw = stored.get("plan_budget_inr")
    budgets: dict[str, float] = dict(raw) if isinstance(raw, dict) else {}
    for plan_id, wanted in (plan_budget_inr or {}).items():
        if plan_id not in PLANS:
            raise ValueError(f"Unknown plan '{plan_id}' - known: {sorted(PLANS)}")
        if wanted is None:
            budgets.pop(plan_id, None)
            continue
        number = _number(wanted)
        if number is None:
            raise ValueError("A plan's AI budget must be a number of rupees, 0 or more")
        budgets[plan_id] = number
    cap = _number(stored.get("user_daily_cap_inr"))
    if clear_cap:
        cap = None
    elif user_daily_cap_inr is not None:
        cap = _number(user_daily_cap_inr)
        if cap is None:
            raise ValueError("The daily cap per user must be a number of rupees, 0 or more")
    value: dict[str, Any] = {"plan_budget_inr": budgets}
    if cap is not None:
        value["user_daily_cap_inr"] = cap
    await controls._set(session, CONTROL_KEY, value, user)
    shown = ", ".join(f"{k}={v:g}" for k, v in sorted(budgets.items())) or "-"
    await write_audit_log(session, None, user.id, "ai_platform_limits_set", f"budgets {shown}; daily cap {'-' if cap is None else f'{cap:g}'}")
    await session.commit()
    return await limits(session)


async def own_cap_inr(session: AsyncSession, user_id: int, tenant_id: int) -> float | None:
    """The user's own daily limit (`ai_usage.daily_cap_inr`), None = Not set. Only ever his own row."""
    from app.db.models import UserPrefsRecord
    from app.me.prefs import stored_value
    raw = await session.scalar(select(UserPrefsRecord.value_json).where(UserPrefsRecord.tenant_id == tenant_id, UserPrefsRecord.user_id == user_id,
                                                                         UserPrefsRecord.namespace == PREFS_NAMESPACE))
    return _number(stored_value(PREFS_NAMESPACE, raw).get("daily_cap_inr"))


def effective_cap(operator_cap: float | None, own_cap: float | None) -> float | None:
    """The cap that applies: the operator's, lowered by the user's own; None while the operator's is Not set."""
    if operator_cap is None:
        return None
    return min(operator_cap, own_cap) if own_cap is not None else operator_cap


# --- spend (the platform metrics only) -------------------------------------------------------------------------------------

def _user_source(user_id: int) -> str:
    return f"platform:u{int(user_id)}"


async def month_spend_inr(session: AsyncSession, tenant_id: int, now: datetime | None = None) -> float:
    spent = await session.scalar(select(func.coalesce(func.sum(UsageRecord.quantity), 0.0)).where(
        UsageRecord.tenant_id == tenant_id, UsageRecord.metric == METRIC_COST_USD, UsageRecord.period_start >= month_start(now)))
    return pricing.usd_to_inr(float(spent or 0.0))


async def day_spend_inr(session: AsyncSession, tenant_id: int, user_id: int, now: datetime | None = None) -> float:
    spent = await session.scalar(select(func.coalesce(func.sum(UsageRecord.quantity), 0.0)).where(
        UsageRecord.tenant_id == tenant_id, UsageRecord.metric == METRIC_COST_USD, UsageRecord.source == _user_source(user_id),
        UsageRecord.period_start >= byok.day_start(now)))
    return pricing.usd_to_inr(float(spent or 0.0))


async def state(session: AsyncSession, tenant: Tenant, user_id: int, now: datetime | None = None) -> dict[str, Any]:
    """This user's platform-key state: the plan budget, the caps, today's and this month's spend and the block reason."""
    values = await limits(session)
    budget = values["plan_budget_inr"].get(tenant.plan if tenant.plan in PLANS else "free")
    operator_cap = values["user_daily_cap_inr"]
    own = await own_cap_inr(session, user_id, tenant.id)
    cap = effective_cap(operator_cap, own)
    today = await day_spend_inr(session, tenant.id, user_id, now)
    month = await month_spend_inr(session, tenant.id, now)
    if budget is None or budget <= 0:
        reason: str | None = REASON_BUDGET_NOT_SET
    elif cap is None or cap <= 0:
        reason = REASON_CAP_NOT_SET if operator_cap is None or operator_cap <= 0 else REASON_DAILY_CAP
    elif month >= budget:
        reason = REASON_BUDGET_SPENT
    elif today >= cap:
        reason = REASON_DAILY_CAP
    else:
        reason = None
    return {"plan_budget_inr": budget, "operator_cap_inr": operator_cap, "own_cap_inr": own, "cap_inr": cap,
            "today_inr": round(today, 2), "month_inr": round(month, 2), "reason": reason}


async def block_reason(session: AsyncSession, tenant: Tenant, user_id: int, now: datetime | None = None) -> str | None:
    return (await state(session, tenant, user_id, now))["reason"]


# --- the provider ----------------------------------------------------------------------------------------------------------

class PlatformKeyLimit(ProviderError):
    """A platform cap was reached between two calls of one request: the call is not made (the caller answers from rules)."""

    def __init__(self, reason: str) -> None:
        super().__init__(MESSAGES.get(reason, MESSAGES[REASON_DAILY_CAP]), kind="rate")
        self.reason = reason


@dataclass
class PlatformKeyProvider(MeteredProvider):
    """The platform key, wrapped like the organisation's (`MeteredProvider`: same calls, `spent` for the cost chip, every
    call in `llm_calls`), but metered on the `ai_platform_*` metrics with the user on `source`, and both caps checked
    before every call."""

    tenant: Tenant | None = None
    platform: bool = True

    async def _check_cap(self) -> None:
        if self.tenant is None or self.user_id is None:
            raise PlatformKeyLimit(REASON_CAP_NOT_SET)
        reason = await block_reason(self.session, self.tenant, int(self.user_id))
        if reason is not None:
            raise PlatformKeyLimit(reason)

    async def _record(self, result: Completion | None, system: str, user: str, response: str | None, status: str) -> None:
        cost = 0.0
        if result is not None and (result.input_tokens or result.output_tokens or result.cache_read_tokens):
            try:
                cost = pricing.cost_usd(self.inner.name, result.model or self.inner.model, input_tokens=result.input_tokens,
                                        output_tokens=result.output_tokens, cache_read_tokens=result.cache_read_tokens,
                                        cache_write_tokens=result.cache_write_tokens).amount
                meta = {"feature": self.feature, "provider": self.inner.name, "model": result.model or self.inner.model, "platform": True}
                source = _user_source(int(self.user_id or 0))
                async with self.session.begin_nested():
                    await meter(self.session, self.tenant_id, METRIC_CALLS, 1, source=source, metadata=meta, commit=False)
                    await meter(self.session, self.tenant_id, METRIC_TOKENS_IN, float(result.input_tokens + result.cache_read_tokens + result.cache_write_tokens),
                                source=source, metadata=meta, commit=False)
                    await meter(self.session, self.tenant_id, METRIC_TOKENS_OUT, float(result.output_tokens), source=source, metadata=meta, commit=False)
                    await meter(self.session, self.tenant_id, METRIC_COST_USD, cost, source=source, metadata=meta, commit=False)
            except Exception as exc:  # noqa: BLE001 - metering must never fail the answer
                logger.warning("Platform AI usage not recorded for tenant %s (%s): %s", self.tenant_id, self.feature, exc.__class__.__name__)
            self.spent["calls"] += 1
            self.spent["tokens_input"] += result.input_tokens + result.cache_read_tokens + result.cache_write_tokens
            self.spent["tokens_output"] += result.output_tokens
            self.spent["cost_usd"] += cost
        try:
            await log_call(self.session, tenant_id=self.tenant_id, user_id=self.user_id, feature=self.feature, provider=self.inner.name,
                           model=(result.model if result else "") or self.inner.model, prompt_version=self.prompt_version or f"unversioned:{self.feature}",
                           system=system, user=user, response=response, status=_clean(status)[:300], result=result, cost_usd=cost)
        except Exception as exc:  # noqa: BLE001 - the audit row is never the cause of a failed answer
            logger.warning("Platform AI call not logged for tenant %s (%s): %s", self.tenant_id, self.feature, exc.__class__.__name__)

    async def complete_full(self, system: str, user: str, *, max_tokens: int = 2000, image_png: bytes | None = None) -> Completion:
        await self._check_cap()
        try:
            return await super().complete_full(system, user, max_tokens=max_tokens, image_png=image_png)
        except PlatformKeyLimit:
            raise
        except ProviderError as exc:
            raise ProviderError(_clean(str(exc)), usage=exc.usage, kind=exc.kind) from None

    async def complete(self, system: str, user: str, *, max_tokens: int = 2000) -> str:
        return (await self.complete_full(system, user, max_tokens=max_tokens)).text

    async def complete_tools(self, system: str, messages, tools, *, max_tokens: int = 4000):
        await self._check_cap()
        try:
            return await super().complete_tools(system, messages, tools, max_tokens=max_tokens)
        except PlatformKeyLimit:
            raise
        except ProviderError as exc:
            raise ProviderError(_clean(str(exc)), usage=exc.usage, kind=exc.kind) from None


@dataclass
class PlatformRules(RuleBasedProvider):
    """ATP's rules in place of the platform key, with the machine reason (`platform_reason`) next to the plain one."""

    platform_reason: str = ""


def rules(reason: str) -> PlatformRules:
    """The rules, with the plain reason the platform key is not answering."""
    return PlatformRules(reason=MESSAGES.get(reason, MESSAGES[REASON_BUDGET_NOT_SET]), platform_reason=reason)


async def platform_on(session: AsyncSession, tenant_id: int) -> bool:
    from app.platform.controls import flag_enabled
    return await flag_enabled(session, PAGE_FLAG, tenant_id) and await flag_enabled(session, FLAG, tenant_id)


async def provider_for(session: AsyncSession, user_id: int, tenant: Tenant, task: str, *, need_vision: bool = False,
                       client: Any | None = None) -> Any | None:
    """The platform key for one user-started call: a `PlatformKeyProvider`, the rules with the reason when the platform key
    applies but may not answer (a limit Not set or reached, no consent), or None when the platform key does not apply here
    at all (flag off, no key on the host, a support view, a chart call the provider cannot see) - the caller then keeps
    today's answer."""
    from app.auth import view_as
    if view_as.active() or not await platform_on(session, tenant.id):
        return None
    name = configured_provider()
    if name is None or (need_vision and name not in ai_providers.VISION_PROVIDERS):
        return None
    from app.ai import compliance_terms as terms
    if await terms.latest(session, terms.KIND_DATA, tenant_id=tenant.id) is None:
        return rules(REASON_CONSENT)
    reason = await block_reason(session, tenant, user_id)
    if reason is not None:
        return rules(reason)
    key = _secret()
    try:
        inner = ai_providers.build_provider(name, key, None, client=client, task=task)
    except ProviderError as exc:
        logger.warning("The platform AI key could not be used (%s)", _clean(str(exc)))
        return None
    finally:
        key = ""
    return PlatformKeyProvider(inner=inner, session=session, tenant_id=int(tenant.id), feature=task, source="platform",
                               user_id=int(user_id), tenant=tenant)


def is_platform(provider: Any) -> bool:
    return isinstance(provider, PlatformKeyProvider)


__all__ = ["FLAG", "CONTROL_KEY", "PREFS_NAMESPACE", "PLATFORM_METRICS", "MESSAGES", "LIMIT_REASONS", "PlatformKeyProvider",
           "PlatformKeyLimit", "configured_provider", "limits", "set_limits", "own_cap_inr", "effective_cap", "month_spend_inr",
           "day_spend_inr", "state", "block_reason", "provider_for", "platform_on", "rules", "is_platform", "PlatformRules"]
