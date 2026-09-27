"""Phase L1: per-tenant provider configuration - the only place an AI API key enters the
system (Settings page -> this API -> Fernet-encrypted column). Never returned once stored."""
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers import DEFAULT_MODELS, DEFAULT_PROVIDER, LLMProvider, PROVIDERS, ProviderError, RuleBasedProvider, build_provider
from app.audit.log import write_audit_log
from app.db.models import AiProviderConfigRecord, Tenant, User
from app.plans.limits import feature_allowed
from app.secrets_store.encryption import decrypt_text, encrypt_text


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
            record.encrypted_api_key = encrypt_text(api_key.strip(), record.tenant_id)
        elif record.provider != provider or not record.encrypted_api_key:
            raise ProviderError(f"{provider} needs an API key")
    else:
        record.encrypted_api_key = None
    record.provider = provider
    record.model = (model or DEFAULT_MODELS[provider]).strip()[:80]
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


async def provider_for(session: AsyncSession, tenant: Tenant, *, client: Optional[httpx.AsyncClient] = None) -> LLMProvider:
    """The tenant's configured provider, or the rule-based one when none is set/enabled. AI
    features on the Free plan always get the rule-based provider (no external calls)."""
    record = await get_config(session, tenant.id)
    if record is None or not record.enabled or not feature_allowed(tenant, "ai_features"):
        return RuleBasedProvider()
    key = decrypt_text(record.encrypted_api_key) if record.encrypted_api_key else None
    return build_provider(record.provider, key, record.model, client=client)


async def mark_used(session: AsyncSession, tenant_id: int, error: Optional[str] = None) -> None:
    record = await get_config(session, tenant_id)
    if record is not None:
        record.last_used_at = datetime.now(timezone.utc)
        record.last_error = (error or "")[:300] or None


def as_dict(record: Optional[AiProviderConfigRecord], tenant: Tenant) -> dict:
    allowed = feature_allowed(tenant, "ai_features")
    if record is None:
        return {"provider": "rule_based", "model": DEFAULT_MODELS["rule_based"], "api_key_set": False, "enabled": True, "configured": False,
                "ai_features_allowed": allowed, "providers": list(PROVIDERS), "default_models": DEFAULT_MODELS, "default_provider": DEFAULT_PROVIDER}
    return {"provider": record.provider, "model": record.model, "api_key_set": bool(record.encrypted_api_key), "enabled": record.enabled,
            "configured": True, "last_used_at": record.last_used_at.isoformat() if record.last_used_at else None, "last_error": record.last_error,
            "ai_features_allowed": allowed, "providers": list(PROVIDERS), "default_models": DEFAULT_MODELS, "default_provider": DEFAULT_PROVIDER}
