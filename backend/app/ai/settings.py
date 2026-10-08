"""Phase L1: per-tenant provider configuration - the only place an AI API key enters the
system (Settings page -> this API -> Fernet-encrypted column). Never returned once stored."""
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers import DEFAULT_MODELS, DEFAULT_PROVIDER, LLMProvider, PROVIDERS, ProviderError, RuleBasedProvider, build_provider, default_models, model_for
from app.audit.log import write_audit_log
from app.db.models import AiProviderConfigRecord, Tenant, User
from app.plans.limits import feature_allowed
from app.secrets_store.encryption import decrypt_text, encrypt_text
from app.secrets_store.envelope import PURPOSE_AI_PROVIDER_KEY


async def get_config(session: AsyncSession, tenant_id: int) -> Optional[AiProviderConfigRecord]:
    return await session.scalar(select(AiProviderConfigRecord).where(AiProviderConfigRecord.tenant_id == tenant_id))


async def save_config(session: AsyncSession, user: User, *, provider: str, model: Optional[str], api_key: Optional[str],
                      enabled: bool = True) -> AiProviderConfigRecord:
    provider = provider.lower()
    if provider not in PROVIDERS:
        raise ProviderError(f"Unknown provider '{provider}'; one of {PROVIDERS}")
    record = await get_config(session, user.tenant_id)
    if record is None:
        record = AiProviderConfigRecord(tenant_id=user.tenant_id, provider=provider)
        session.add(record)
    if provider != "rule_based":
        if api_key:
            record.encrypted_api_key = encrypt_text(api_key.strip(), record.tenant_id, PURPOSE_AI_PROVIDER_KEY)
        elif record.provider != provider or not record.encrypted_api_key:
            raise ProviderError(f"{provider} needs an API key")
    else:
        record.encrypted_api_key = None
    record.provider = provider
    # P0.8-C / C2: a blank model, or the operator's current default typed back in, is no override - the row keeps "" and
    # the environment model applies (and follows when the operator changes it). Only a different name is pinned.
    chosen = (model or "").strip()[:80]
    record.model = "" if provider == "rule_based" or chosen == default_models()[provider]["strong"] else chosen
    record.enabled = enabled
    record.updated_by = user.id
    record.last_error = None
    await write_audit_log(session, user.tenant_id, user.id, "ai_provider_configured", f"{provider} {record.model} key={'set' if record.encrypted_api_key else 'none'}")
    await session.commit()
    await session.refresh(record)
    return record


async def delete_config(session: AsyncSession, user: User) -> None:
    record = await get_config(session, user.tenant_id)
    if record is not None:
        await session.delete(record)
        await write_audit_log(session, user.tenant_id, user.id, "ai_provider_removed", record.provider)
        await session.commit()


async def provider_for(session: AsyncSession, tenant: Tenant, *, client: Optional[httpx.AsyncClient] = None, task: str = "general",
                       user_id: Optional[int] = None) -> LLMProvider:
    """The tenant's configured provider for `task`, metered (P0.8-C), or the rule-based one when none is set/enabled,
    when the plan has no AI features (the Free plan never calls out) or when the plan's monthly AI budget is spent.
    `task` picks the model tier: cheap tasks (narration, classification, Q&A) run the operator's fast model at low
    effort, rule-writing tasks the tenant's generation model."""
    from app.ai import metering
    record = await get_config(session, tenant.id)
    if record is None or not record.enabled or not feature_allowed(tenant, "ai_features"):
        return RuleBasedProvider()
    if record.provider != "rule_based":
        # P0.8-D (DPDP): nothing leaves for an outside model until the owner accepted the current data-sharing consent -
        # also for a provider saved before the consent existed, and again after the consent text changes.
        from app.ai import compliance_terms as terms
        if await terms.latest(session, terms.KIND_DATA, tenant_id=tenant.id) is None:
            return RuleBasedProvider(reason="the account owner has not accepted the current data-sharing consent (Settings > AI provider)")
    if record.provider != "rule_based" and await metering.budget_exhausted(session, tenant):
        return RuleBasedProvider(reason=f"the plan's monthly AI budget ({metering.budget_inr(tenant):,.0f} INR) is spent; the rules answer until the 1st")
    key = decrypt_text(record.encrypted_api_key, PURPOSE_AI_PROVIDER_KEY, record.tenant_id) if record.encrypted_api_key else None
    inner = build_provider(record.provider, key, record.model, client=client, task=task)
    if isinstance(inner, RuleBasedProvider):
        return inner
    return metering.MeteredProvider(inner, session, tenant.id, feature=task, user_id=user_id)


async def mark_used(session: AsyncSession, tenant_id: int, error: Optional[str] = None) -> None:
    record = await get_config(session, tenant_id)
    if record is not None:
        record.last_used_at = datetime.now(timezone.utc)
        record.last_error = (error or "")[:300] or None


# P0.9: the token sizes behind the per-call cost estimate on the provider card (a typical call of each tier, thinking
# included in the output) - an estimate for the owner, not a bill; the real figures are metered per call.
TYPICAL_CALL_TOKENS = {"cheap": (1500, 300), "fast": (3000, 1200), "strong": (6000, 4000)}
TASK_LABELS = {"strategy_generation": "Strategy drafts", "strategist": "Strategist rule proposals", "scanner_plan": "Scanner plans",
               "narration": "Narration", "copilot": "Copilot chat", "knowledge": "Guide answers", "thesis": "Thesis wording",
               "classification": "News classification", "scanner_read": "Scanner reads"}


def task_models(tenant_model: Optional[str] = None, tenant_provider: Optional[str] = None) -> dict:
    """Per external provider: every AI task with its tier, the model it runs on (environment per tier; the tenant's model
    only for the strong tier of the configured provider) and an estimated cost of one typical call in INR."""
    from app.ai import pricing
    from app.ai.providers import TASK_TIERS
    out: dict = {}
    for provider in ("anthropic", "openai"):
        rows = []
        for task, tier in TASK_TIERS.items():
            if task == "general":
                continue
            model = model_for(provider, tenant_model if provider == tenant_provider else None, task)
            tokens_in, tokens_out = TYPICAL_CALL_TOKENS[tier]
            cost = pricing.cost_usd(provider, model, input_tokens=tokens_in, output_tokens=tokens_out)
            rows.append({"task": task, "label": TASK_LABELS.get(task, task), "tier": tier, "model": model,
                         "price_per_mtok_usd": {"input": cost.price[0], "output": cost.price[1]},
                         "est_inr_per_call": round(pricing.usd_to_inr(cost.amount), 2), "estimated_price": cost.estimated})
        out[provider] = rows
    return out


def as_dict(record: Optional[AiProviderConfigRecord], tenant: Tenant, usage: Optional[dict] = None) -> dict:
    """`usage` (P0.8-C) is `metering.budget_state(...)` - this month's calls, tokens and spend against the plan budget."""
    allowed = feature_allowed(tenant, "ai_features")
    base = {"ai_features_allowed": allowed, "providers": list(PROVIDERS), "default_models": DEFAULT_MODELS.as_dict(), "default_provider": DEFAULT_PROVIDER,
            "tier_models": default_models(), "usage": usage,
            "task_models": task_models(record.model if record is not None else None, record.provider if record is not None else None),
            "typical_call_tokens": {tier: {"input": i, "output": o} for tier, (i, o) in TYPICAL_CALL_TOKENS.items()}}
    if record is None:
        return {"provider": "rule_based", "model": DEFAULT_MODELS["rule_based"], "api_key_set": False, "enabled": True, "configured": False,
                "models": {"strong": DEFAULT_MODELS["rule_based"], "fast": DEFAULT_MODELS["rule_based"], "cheap": DEFAULT_MODELS["rule_based"]}, **base}
    return {"provider": record.provider, "model": record.model or "", "api_key_set": bool(record.encrypted_api_key), "enabled": record.enabled,
            "configured": True, "last_used_at": record.last_used_at.isoformat() if record.last_used_at else None, "last_error": record.last_error,
            "models": {"strong": model_for(record.provider, record.model, "strategy_generation"), "fast": model_for(record.provider, record.model, "narration"),
                       "cheap": model_for(record.provider, record.model, "classification")},
            **base}
