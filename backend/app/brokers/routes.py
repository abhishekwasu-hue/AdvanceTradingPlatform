import json
import logging
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.service import ensure_account_for_credential
from app.audit.log import write_audit_log
from app.auth.dependencies import current_session_id, ensure_live_step_up, get_current_user, require_trader
from app.brokers.models import BrokerCredentials, BrokerProfile
from app.brokers.registry import available_brokers, get_broker_adapter
from app.brokers.token_lifecycle import (
    build_adapter,
    OAUTH_BROKERS,
    build_upstox_authorization_url,
    create_oauth_state,
    get_credential_record,
    load_credentials,
    mark_expired,
    mark_verified,
    parse_oauth_state,
    store_access_token,
    token_is_usable,
)
from app.core.config import FRONTEND_URL
from app.core.enums import BrokerTokenStatus, NotificationSeverity, NotificationType
from app.db.models import BrokerCredentialRecord, User
from app.db.session import get_session
from app.notifications.service import notify
from app.secrets_store.encryption import decrypt_text, encrypt_text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/broker", tags=["broker"])

UPSTOX_CALLBACK_PATH = "/api/broker/upstox/oauth/callback"


class StoredBrokerInfo(BaseModel):
    broker_name: str
    updated_at: str
    account_label: str = "primary"


class BrokerTokenStatusResponse(BaseModel):
    broker_name: str
    account_label: str = "primary"
    token_status: str
    token_expires_at: Optional[str]
    last_verified_at: Optional[str]
    # True when a LIVE deployment on this broker would be refused right now.
    needs_login: bool
    # True when "Login to <broker>" can be driven as a browser redirect from the UI.
    oauth_supported: bool
    # The exact redirect URI to register on the broker's developer console for that flow.
    oauth_callback_url: Optional[str]


def _clean_label(label: Optional[str]) -> str:
    label = (label or "primary").strip().lower()
    if not label or len(label) > 50 or not all(c.isalnum() or c in "-_" for c in label):
        raise HTTPException(status_code=400, detail="account_label must be 1-50 letters, digits, '-' or '_'")
    return label


def _ensure_known_broker(name: str) -> None:
    if name not in available_brokers():
        raise HTTPException(status_code=404, detail=f"Unknown broker '{name}'. Available: {available_brokers()}")


def _callback_url(request: Request, broker_name: str) -> Optional[str]:
    if broker_name not in OAUTH_BROKERS:
        return None
    return str(request.base_url).rstrip("/") + UPSTOX_CALLBACK_PATH


def _token_status_response(record: BrokerCredentialRecord, request: Request) -> BrokerTokenStatusResponse:
    return BrokerTokenStatusResponse(
        broker_name=record.broker_name,
        account_label=record.account_label or "primary",
        token_status=record.token_status,
        token_expires_at=record.token_expires_at.isoformat() if record.token_expires_at else None,
        last_verified_at=record.last_verified_at.isoformat() if record.last_verified_at else None,
        needs_login=not token_is_usable(record),
        oauth_supported=record.broker_name in OAUTH_BROKERS,
        oauth_callback_url=_callback_url(request, record.broker_name),
    )


@router.post("/{name}/credentials", status_code=status.HTTP_204_NO_CONTENT)
async def store_broker_credentials(
    name: str, credentials: BrokerCredentials,
    user: User = Depends(require_trader), session: AsyncSession = Depends(get_session),
    session_id: Optional[int] = Depends(current_session_id), account_label: str = "primary",
) -> None:
    await ensure_live_step_up(session, user, session_id, "Storing broker credentials")
    """Encrypts and stores this tenant's credentials for one broker. Nothing is ever stored in
    plaintext; the ciphertext is only decrypted in memory, on demand, when /authenticate runs.
    Storing resets the token status to UNKNOWN - a pasted access token is unproven until
    /authenticate (or the OAuth callback) succeeds against the broker.
    """
    _ensure_known_broker(name)
    account_label = _clean_label(account_label)
    encrypted = encrypt_text(credentials.model_dump_json())

    existing = await get_credential_record(session, user.tenant_id, name, account_label)
    if existing:
        existing.encrypted_payload = encrypted
        existing.user_id = user.id
        existing.token_status = BrokerTokenStatus.UNKNOWN.value
        existing.token_expires_at = None
        existing.last_verified_at = None
        record = existing
    else:
        record = BrokerCredentialRecord(
            tenant_id=user.tenant_id, user_id=user.id, broker_name=name, account_label=account_label, encrypted_payload=encrypted
        )
        session.add(record)
        await session.flush()
    await ensure_account_for_credential(session, record)  # Phase I2: the account row behind the credential

    await write_audit_log(session, user.tenant_id, user.id, "broker_credentials_stored", f"{name}/{account_label}")
    await session.commit()


@router.get("/credentials", response_model=list[StoredBrokerInfo])
async def list_stored_broker_credentials(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> list[StoredBrokerInfo]:
    records = await session.scalars(
        select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == user.tenant_id)
    )
    return [
        StoredBrokerInfo(broker_name=r.broker_name, updated_at=r.updated_at.isoformat(), account_label=r.account_label or "primary")
        for r in records
    ]


@router.get("/token-status", response_model=list[BrokerTokenStatusResponse])
async def list_broker_token_status(
    request: Request, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> list[BrokerTokenStatusResponse]:
    """Token health for every broker this tenant has credentials for - what the Settings banner
    and the Deployments tab read to say "log in to Upstox again before LIVE can run"."""
    records = await session.scalars(
        select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == user.tenant_id)
    )
    return [_token_status_response(r, request) for r in records]


@router.delete("/{name}/credentials", status_code=status.HTTP_204_NO_CONTENT)
async def delete_broker_credentials(
    name: str, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session), account_label: str = "primary",
) -> None:
    existing = await get_credential_record(session, user.tenant_id, name, _clean_label(account_label))
    if existing:
        await session.delete(existing)
        await write_audit_log(session, user.tenant_id, user.id, "broker_credentials_deleted", name)
        await session.commit()


@router.post("/{name}/disconnect", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_broker(
    name: str, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session), account_label: str = "primary",
) -> None:
    """Phase G3 (V3.14 rule 2): end today's broker session on purpose - invalidate the access
    token at the broker (Upstox `DELETE /logout`, Kite `DELETE /session/token`) and mark it
    EXPIRED here, so LIVE deployments stop until someone logs in again. The API key/secret stay
    stored; only the session token is gone. Audited. A broker that refuses the logout call still
    ends up EXPIRED locally - the platform will not use a token it has tried to revoke."""
    record = await get_credential_record(session, user.tenant_id, name, _clean_label(account_label))
    if record is None:
        raise HTTPException(status_code=404, detail=f"No stored credentials for broker '{name}'")
    detail = "token revoked at broker"
    try:
        adapter = build_adapter(record)
        if adapter.access_token:
            await adapter.disconnect()
    except Exception as exc:  # noqa: BLE001 - local revocation is what matters
        logger.warning("Broker %s logout call failed for tenant %s: %s", name, user.tenant_id, exc)
        detail = f"broker logout call failed ({type(exc).__name__}); token marked expired locally"
    payload = json.loads(decrypt_text(record.encrypted_payload))
    payload.pop("access_token", None)
    record.encrypted_payload = encrypt_text(json.dumps(payload))
    record.token_status = BrokerTokenStatus.EXPIRED.value
    record.token_expires_at = None
    await write_audit_log(session, user.tenant_id, user.id, "broker_disconnected", f"{name}: {detail}")
    await session.commit()


@router.post("/{name}/authenticate", response_model=BrokerProfile)
async def authenticate_broker(
    name: str, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session),
) -> BrokerProfile:
    """Loads this tenant's stored (encrypted) credentials for `name`, decrypts them in memory,
    and performs a real login against that broker. Success marks the stored token VALID until the
    broker's next daily expiry (and persists a newly exchanged access token, if the login
    produced one); a token-related failure marks it EXPIRED so LIVE deployments stop entering.
    """
    _ensure_known_broker(name)
    record = await get_credential_record(session, user.tenant_id, name)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No stored credentials for broker '{name}'")

    credentials = load_credentials(record)
    adapter = get_broker_adapter(name, credentials)

    try:
        profile = await adapter.authenticate()
    except Exception as exc:
        # Broker-side errors (BrokerError) and raw network failures (DNS, timeout, TLS, ...)
        # both mean the same thing to the caller: authentication did not succeed. Either way
        # this must come back as a clean error, never an unhandled 500, and always be audited.
        await write_audit_log(session, user.tenant_id, user.id, "broker_authentication_failed", f"{name}: {exc}")
        # A heuristic, not a certainty: real broker APIs (Kite, Upstox) return distinguishable
        # error text for an expired/invalid token vs. other failures, but there's no structured
        # error code to key off across three different broker error formats.
        is_token_issue = "token" in str(exc).lower()
        if is_token_issue:
            mark_expired(record)
        await session.commit()
        await notify(
            session, user.tenant_id,
            NotificationType.TOKEN_EXPIRED if is_token_issue else NotificationType.BROKER_DISCONNECT,
            title=f"{name} authentication failed", message=str(exc),
            severity=NotificationSeverity.CRITICAL, user_id=user.id,
        )
        raise HTTPException(status_code=502, detail=f"Broker authentication failed: {exc}") from exc

    new_token = getattr(adapter, "access_token", None)
    if new_token and new_token != credentials.access_token:
        store_access_token(record, new_token)
    else:
        mark_verified(record)
    await write_audit_log(session, user.tenant_id, user.id, "broker_authenticated", name)
    await session.commit()
    return profile


# --- Upstox OAuth redirect flow ------------------------------------------------------------------
#
# Upstox retail apps get a daily-expiring access token and no refresh token, so every trading
# morning someone has to log in again. Rather than copy-pasting a code out of a browser address
# bar into Settings, the UI's "Login to Upstox" button hits /oauth/start, the browser is sent to
# Upstox's login dialog, and Upstox sends it back to /oauth/callback with a one-time code the
# platform exchanges and stores (encrypted) itself. The redirect URI registered on the Upstox
# developer console must be exactly the callback URL this API is reachable at.


class OAuthStartResponse(BaseModel):
    authorization_url: str


@router.get("/upstox/oauth/start", response_model=OAuthStartResponse)
async def upstox_oauth_start(
    request: Request, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session),
    session_id: Optional[int] = Depends(current_session_id),
) -> OAuthStartResponse:
    await ensure_live_step_up(session, user, session_id, "Logging in to the broker")
    record = await get_credential_record(session, user.tenant_id, "upstox")
    if record is None:
        raise HTTPException(status_code=404, detail="Store your Upstox API key/secret in Settings first")
    credentials = load_credentials(record)
    if not credentials.api_key or not credentials.api_secret:
        raise HTTPException(status_code=400, detail="Upstox credentials need both api_key and api_secret for OAuth")
    redirect_uri = credentials.redirect_uri or _callback_url(request, "upstox")
    if credentials.redirect_uri is None:
        # Remember the redirect URI we are about to use so the token exchange sends the same one.
        credentials.redirect_uri = redirect_uri
        record.encrypted_payload = encrypt_text(credentials.model_dump_json())

    state = create_oauth_state(user.tenant_id, user.id, "upstox")
    await write_audit_log(session, user.tenant_id, user.id, "broker_oauth_started", "upstox")
    await session.commit()
    return OAuthStartResponse(authorization_url=build_upstox_authorization_url(credentials.api_key, redirect_uri, state))


def _frontend_redirect(**params: str) -> RedirectResponse:
    return RedirectResponse(url=f"{FRONTEND_URL}?{urlencode(params)}", status_code=302)


@router.get("/upstox/oauth/callback", include_in_schema=True)
async def upstox_oauth_callback(
    request: Request, code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    """Unauthenticated by necessity (the browser arrives here from Upstox, not from the app), so
    the signed `state` is the sole proof of which tenant this code belongs to. Every outcome ends
    in a redirect back to the UI with a status in the query string - never a JSON error page a
    non-technical operator has to interpret.
    """
    if error:
        return _frontend_redirect(broker="upstox", error=error)
    if not code or not state:
        return _frontend_redirect(broker="upstox", error="missing_code_or_state")
    try:
        claims = parse_oauth_state(state)
    except ValueError as exc:
        logger.warning("Rejected Upstox OAuth callback: %s", exc)
        return _frontend_redirect(broker="upstox", error="invalid_state")
    if claims.get("broker") != "upstox":
        return _frontend_redirect(broker="upstox", error="invalid_state")

    tenant_id, user_id = int(claims["tenant_id"]), int(claims["user_id"])
    record = await get_credential_record(session, tenant_id, "upstox")
    if record is None:
        return _frontend_redirect(broker="upstox", error="credentials_missing")

    credentials = load_credentials(record)
    credentials.access_token = None
    credentials.request_token = code
    credentials.redirect_uri = credentials.redirect_uri or _callback_url(request, "upstox")
    adapter = get_broker_adapter("upstox", credentials)
    try:
        await adapter.authenticate()
    except Exception as exc:  # noqa: BLE001 - must always end in a redirect
        await write_audit_log(session, tenant_id, user_id, "broker_oauth_failed", f"upstox: {exc}")
        await session.commit()
        await notify(
            session, tenant_id, NotificationType.BROKER_DISCONNECT, title="Upstox login failed",
            message=str(exc), severity=NotificationSeverity.CRITICAL, user_id=user_id,
        )
        return _frontend_redirect(broker="upstox", error="exchange_failed")

    new_token = adapter.access_token
    if not new_token:
        return _frontend_redirect(broker="upstox", error="no_token_returned")
    store_access_token(record, new_token)
    await write_audit_log(session, tenant_id, user_id, "broker_authenticated", "upstox (oauth)")
    await session.commit()
    return _frontend_redirect(broker="upstox", connected="1")
