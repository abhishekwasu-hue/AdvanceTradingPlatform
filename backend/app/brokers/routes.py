import json

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.brokers.models import BrokerCredentials, BrokerProfile
from app.brokers.registry import available_brokers, get_broker_adapter
from app.core.enums import NotificationSeverity, NotificationType
from app.db.models import AuditLogRecord, BrokerCredentialRecord, User
from app.db.session import get_session
from app.notifications.service import notify
from app.secrets_store.encryption import decrypt_text, encrypt_text

router = APIRouter(prefix="/api/broker", tags=["broker"])


class StoredBrokerInfo(BaseModel):
    broker_name: str
    updated_at: str


def _ensure_known_broker(name: str) -> None:
    if name not in available_brokers():
        raise HTTPException(status_code=404, detail=f"Unknown broker '{name}'. Available: {available_brokers()}")


@router.post("/{name}/credentials", status_code=status.HTTP_204_NO_CONTENT)
async def store_broker_credentials(
    name: str, credentials: BrokerCredentials,
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> None:
    """Encrypts and stores this tenant's credentials for one broker. Nothing is ever stored in
    plaintext; the ciphertext is only decrypted in memory, on demand, when /authenticate runs.
    """
    _ensure_known_broker(name)
    encrypted = encrypt_text(credentials.model_dump_json())

    existing = await session.scalar(
        select(BrokerCredentialRecord).where(
            BrokerCredentialRecord.tenant_id == user.tenant_id, BrokerCredentialRecord.broker_name == name
        )
    )
    if existing:
        existing.encrypted_payload = encrypted
        existing.user_id = user.id
    else:
        session.add(
            BrokerCredentialRecord(
                tenant_id=user.tenant_id, user_id=user.id, broker_name=name, encrypted_payload=encrypted
            )
        )

    session.add(AuditLogRecord(tenant_id=user.tenant_id, user_id=user.id, event="broker_credentials_stored", detail=name))
    await session.commit()


@router.get("/credentials", response_model=list[StoredBrokerInfo])
async def list_stored_broker_credentials(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> list[StoredBrokerInfo]:
    records = await session.scalars(
        select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == user.tenant_id)
    )
    return [
        StoredBrokerInfo(broker_name=r.broker_name, updated_at=r.updated_at.isoformat()) for r in records
    ]


@router.delete("/{name}/credentials", status_code=status.HTTP_204_NO_CONTENT)
async def delete_broker_credentials(
    name: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> None:
    existing = await session.scalar(
        select(BrokerCredentialRecord).where(
            BrokerCredentialRecord.tenant_id == user.tenant_id, BrokerCredentialRecord.broker_name == name
        )
    )
    if existing:
        await session.delete(existing)
        session.add(AuditLogRecord(tenant_id=user.tenant_id, user_id=user.id, event="broker_credentials_deleted", detail=name))
        await session.commit()


@router.post("/{name}/authenticate", response_model=BrokerProfile)
async def authenticate_broker(
    name: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> BrokerProfile:
    """Loads this user's stored (encrypted) credentials for `name`, decrypts them in memory,
    and performs a real login against that broker. This is the endpoint the previously-deferred
    "no credential-accepting endpoint" note pointed to - it only exists now that credentials are
    encrypted at rest rather than ever being logged or stored as plaintext.
    """
    _ensure_known_broker(name)
    record = await session.scalar(
        select(BrokerCredentialRecord).where(
            BrokerCredentialRecord.tenant_id == user.tenant_id, BrokerCredentialRecord.broker_name == name
        )
    )
    if record is None:
        raise HTTPException(status_code=404, detail=f"No stored credentials for broker '{name}'")

    credentials = BrokerCredentials(**json.loads(decrypt_text(record.encrypted_payload)))
    adapter = get_broker_adapter(name, credentials)

    try:
        profile = await adapter.authenticate()
    except Exception as exc:
        # Broker-side errors (BrokerError) and raw network failures (DNS, timeout, TLS, ...)
        # both mean the same thing to the caller: authentication did not succeed. Either way
        # this must come back as a clean error, never an unhandled 500, and always be audited.
        session.add(
            AuditLogRecord(
                tenant_id=user.tenant_id, user_id=user.id,
                event="broker_authentication_failed", detail=f"{name}: {exc}",
            )
        )
        await session.commit()
        # A heuristic, not a certainty: real broker APIs (Kite, Upstox) return distinguishable
        # error text for an expired/invalid token vs. other failures, but there's no structured
        # error code to key off across three different broker error formats.
        is_token_issue = "token" in str(exc).lower()
        await notify(
            session, user.tenant_id,
            NotificationType.TOKEN_EXPIRED if is_token_issue else NotificationType.BROKER_DISCONNECT,
            title=f"{name} authentication failed", message=str(exc),
            severity=NotificationSeverity.CRITICAL, user_id=user.id,
        )
        raise HTTPException(status_code=502, detail=f"Broker authentication failed: {exc}") from exc

    session.add(AuditLogRecord(tenant_id=user.tenant_id, user_id=user.id, event="broker_authenticated", detail=name))
    await session.commit()
    return profile
