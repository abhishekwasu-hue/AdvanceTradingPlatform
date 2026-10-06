"""Phase N1: per-tenant envelope encryption (spec section 48).

`tenant_keys` holds one random Fernet data key per tenant, *wrapped* (encrypted) by the master
key from `SECRETS_ENCRYPTION_KEY`. The unwrapped keys live only in this process's `key_ring`,
loaded on demand:

* `ensure_tenant_key(session, tenant_id)` - loads (creating on first use) and caches a tenant's
  key. Called from `get_current_user` (every authenticated request), the public-API key auth, the
  TradingView webhook, the worker per tenant, the alert dispatcher per channel, and at startup for
  every tenant (`warm_all`). It is a dict lookup once cached.
* `rotate_master(session, old_key)` - re-wraps every tenant key under the *current* master. This
  is what makes master rotation cheap: no credential row is touched.
* `reencrypt_tenant(session, tenant_id)` - upgrades legacy (master-encrypted) rows of one tenant
  to the tenant-key format. Both are driven by `scripts/reencrypt_secrets.py`.

The plaintext data key never leaves the process and is never logged or returned by any API.
"""
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.db.models import (AiProviderConfigRecord, AlertChannelRecord, BrokerCredentialRecord, MarketplacePayoutRecord, Tenant,
                           TenantKeyRecord, User)
from app.secrets_store import encryption

logger = logging.getLogger(__name__)

key_ring: Dict[int, Fernet] = {}
# P0.4 / S11: the raw data keys (the Fernet objects hide theirs) and the AES-GCM keys derived from them.
raw_ring: Dict[int, bytes] = {}
_aead_ring: Dict[int, AESGCM] = {}

# Column labels bound into AES-GCM associated data. One per encrypted column; never reuse across columns.
PURPOSE_BROKER_CREDENTIAL = "broker_credential"
PURPOSE_ALERT_CHANNEL = "alert_channel"
PURPOSE_AI_PROVIDER_KEY = "ai_provider_key"
PURPOSE_MFA_SECRET = "mfa_secret"
PURPOSE_PAYOUT_DESTINATION = "payout_destination"


def _remember(tenant_id: int, raw: bytes) -> Fernet:
    fernet = Fernet(raw)
    key_ring[tenant_id] = fernet
    raw_ring[tenant_id] = raw
    _aead_ring.pop(tenant_id, None)
    return fernet


def aead_for(tenant_id: int) -> AESGCM:
    """The tenant's AES-256-GCM key (derived once per process from its loaded data key)."""
    cached = _aead_ring.get(tenant_id)
    if cached is None:
        raw = raw_ring.get(tenant_id)
        if raw is None:
            raise ValueError(f"Data key for tenant {tenant_id} is not loaded - call envelope.ensure_tenant_key first")
        cached = _aead_ring[tenant_id] = encryption.aead_key_from_data_key(raw, tenant_id)
    return cached


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _unwrap(record: TenantKeyRecord, master=None) -> bytes:
    master = master or encryption.master_fernet()
    try:
        return master.decrypt(record.wrapped_key.encode())
    except InvalidToken as exc:
        raise ValueError(
            f"Cannot unwrap the data key of tenant {record.tenant_id} - SECRETS_ENCRYPTION_KEY changed without "
            "running scripts/reencrypt_secrets.py rotate-master"
        ) from exc


def _wrap(raw_key: bytes) -> str:
    return encryption.master_fernet().encrypt(raw_key).decode()


async def ensure_tenant_key(session: AsyncSession, tenant_id: Optional[int]) -> Optional[Fernet]:
    """Loads (or creates and persists) the tenant's data key into the ring. Idempotent and cheap
    once cached. Creating flushes but does not commit - the caller's transaction carries it; a
    rolled-back creation is simply recreated next time (nothing was encrypted under it yet)."""
    if tenant_id is None:
        return None
    cached = key_ring.get(tenant_id)
    if cached is not None:
        return cached
    record = await session.get(TenantKeyRecord, tenant_id)
    if record is None:
        raw = Fernet.generate_key()
        record = TenantKeyRecord(tenant_id=tenant_id, wrapped_key=_wrap(raw), key_version=1)
        session.add(record)
        await session.flush()
        logger.info("Created data key for tenant %s", tenant_id)
    else:
        raw = _unwrap(record)
    return _remember(tenant_id, raw)


async def warm_all(session: AsyncSession) -> int:
    """Loads every existing tenant key (startup). Tenants without a key get one lazily on their
    first request, so this never creates rows."""
    rows = list(await session.scalars(select(TenantKeyRecord)))
    for record in rows:
        if record.tenant_id in key_ring:
            continue
        try:
            _remember(record.tenant_id, _unwrap(record))
        except ValueError as exc:
            # One organisation's key that does not open under this master must not keep the whole API from
            # starting: that organisation's requests fail with the same clear error until the key is re-wrapped.
            logger.error("Tenant %s data key not loaded at startup: %s", record.tenant_id, exc)
    return len(rows)


def forget(tenant_id: int) -> None:
    key_ring.pop(tenant_id, None)
    raw_ring.pop(tenant_id, None)
    _aead_ring.pop(tenant_id, None)


def clear_ring() -> None:
    key_ring.clear()
    raw_ring.clear()
    _aead_ring.clear()


async def rotate_master(session: AsyncSession, old_master_key: Optional[str]) -> int:
    """Re-wraps every tenant data key from the *old* master to the current one. Run once after
    changing SECRETS_ENCRYPTION_KEY, with the previous value in OLD_SECRETS_ENCRYPTION_KEY.
    Legacy master-encrypted rows are NOT touched here - run `reencrypt` (under the old key still
    loadable) before rotating, or accept that they become unreadable."""
    old = encryption.fernet_for_key(old_master_key)
    rows = list(await session.scalars(select(TenantKeyRecord)))
    rewrapped = 0
    for record in rows:
        try:
            raw = old.decrypt(record.wrapped_key.encode())
        except InvalidToken:
            # Already under the current master (a re-run after a partial failure) - leave it.
            _unwrap(record)
            continue
        record.wrapped_key = _wrap(raw)
        record.key_version += 1
        record.rotated_at = _utcnow()
        _remember(record.tenant_id, raw)
        rewrapped += 1
    await session.commit()
    return rewrapped


async def reencrypt_tenant(session: AsyncSession, tenant_id: int) -> Dict[str, int]:
    """Moves every secret of one tenant that is not in the configured write format (legacy master-key rows;
    with SECRETS_WRITE_FORMAT=aesgcm also the Phase N tenant-Fernet rows) to that format."""
    await ensure_tenant_key(session, tenant_id)
    counts = {"broker_credentials": 0, "alert_channels": 0, "ai_providers": 0, "mfa_secrets": 0, "payout_destinations": 0}

    def _upgrade(ciphertext: Optional[str], purpose: str) -> Optional[str]:
        if not ciphertext or not encryption.needs_rewrite(ciphertext):
            return None
        return encryption.encrypt_text(encryption.decrypt_text(ciphertext, purpose), tenant_id, purpose)

    for row in await session.scalars(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == tenant_id)):
        new = _upgrade(row.encrypted_payload, PURPOSE_BROKER_CREDENTIAL)
        if new:
            row.encrypted_payload = new
            counts["broker_credentials"] += 1
    for row in await session.scalars(select(AlertChannelRecord).where(AlertChannelRecord.tenant_id == tenant_id)):
        new = _upgrade(row.encrypted_config, PURPOSE_ALERT_CHANNEL)
        if new:
            row.encrypted_config = new
            counts["alert_channels"] += 1
    for row in await session.scalars(select(AiProviderConfigRecord).where(AiProviderConfigRecord.tenant_id == tenant_id)):
        new = _upgrade(row.encrypted_api_key, PURPOSE_AI_PROVIDER_KEY)
        if new:
            row.encrypted_api_key = new
            counts["ai_providers"] += 1
    for row in await session.scalars(select(User).where(User.tenant_id == tenant_id)):
        new = _upgrade(row.mfa_secret_encrypted, PURPOSE_MFA_SECRET)
        if new:
            row.mfa_secret_encrypted = new
            counts["mfa_secrets"] += 1
    for row in await session.scalars(select(MarketplacePayoutRecord).where(MarketplacePayoutRecord.tenant_id == tenant_id)):
        new = _upgrade(row.destination_encrypted, PURPOSE_PAYOUT_DESTINATION)
        if new:
            row.destination_encrypted = new
            counts["payout_destinations"] += 1
    await session.commit()
    return counts


async def reencrypt_all(session: AsyncSession) -> Dict[str, int]:
    totals: Dict[str, int] = {}
    tenant_ids: List[int] = list(await session.scalars(select(Tenant.id).order_by(Tenant.id)))
    for tenant_id in tenant_ids:
        for key, value in (await reencrypt_tenant(session, tenant_id)).items():
            totals[key] = totals.get(key, 0) + value
    totals["tenants"] = len(tenant_ids)
    return totals


async def status(session: AsyncSession) -> Dict[str, int]:
    """How far the estate has moved to tenant keys (for the admin console / runbook)."""
    legacy = 0
    total = 0
    formats = {"legacy": 0, encryption.FORMAT_FERNET: 0, encryption.FORMAT_AESGCM: 0}
    pending = 0
    for model, column in ((BrokerCredentialRecord, "encrypted_payload"), (AlertChannelRecord, "encrypted_config"),
                          (AiProviderConfigRecord, "encrypted_api_key"), (User, "mfa_secret_encrypted"),
                          (MarketplacePayoutRecord, "destination_encrypted")):
        for value in await session.scalars(select(getattr(model, column))):
            if not value:
                continue
            total += 1
            fmt = encryption.format_of(value)
            formats[fmt] = formats.get(fmt, 0) + 1
            if fmt == "legacy":
                legacy += 1
            if encryption.needs_rewrite(value):
                pending += 1
    keys = await session.scalar(select(TenantKeyRecord.tenant_id).limit(1))
    key_count = len(list(await session.scalars(select(TenantKeyRecord.tenant_id)))) if keys is not None else 0
    return {"tenant_keys": key_count, "secrets_total": total, "secrets_legacy": legacy, "keys_loaded": len(key_ring),
            "formats": formats, "write_format": encryption.write_format(), "pending_rewrite": pending}
