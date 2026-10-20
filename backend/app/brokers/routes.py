import json
import logging
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.service import ensure_account_for_credential
from app.audit.log import write_audit_log
from app.auth.dependencies import current_session_id, ensure_live_step_up, get_current_user, require_trader
from app.auth.scopes import require_scope
from app.brokers.exceptions import BrokerError
from app.brokers.models import BrokerCredentials, BrokerProfile
from app.brokers.registry import available_brokers, get_broker_adapter
from app.brokers.smoke import run_smoke
from app.brokers.token_lifecycle import (
    _is_auth_failure,
    build_adapter,
    LOGIN_URL_BROKERS,
    OAUTH_BROKERS,
    build_login_url,
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
from app.core.enums import BrokerTokenStatus, NotificationSeverity, NotificationType, UserRole
from app.db.models import BrokerCredentialRecord, User
from app.db.session import get_session
from app.notifications.service import notify
from app.secrets_store.encryption import decrypt_text, encrypt_text
from app.secrets_store.envelope import PURPOSE_BROKER_CREDENTIAL

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
    # True when the platform can open the broker's hosted login page and take the pasted one-time
    # code (Fyers, Kite): GET /{name}/login-url, then POST /{name}/login-code.
    login_url_supported: bool = False
    # The query parameter of the broker's redirect that carries that code (`auth_code`, `request_token`).
    code_param: Optional[str] = None


def _clean_label(label: Optional[str]) -> str:
    """The account label every per-account broker route acts on (default "primary"). A malformed one is a
    request-validation error (422, like any other bad parameter FastAPI rejects), never a lookup."""
    label = (label or "primary").strip().lower()
    if not label or len(label) > 50 or not all(c.isalnum() or c in "-_" for c in label):
        raise HTTPException(status_code=422, detail="account_label must be 1-50 letters, digits, '-' or '_'")
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
        login_url_supported=record.broker_name in LOGIN_URL_BROKERS,
        code_param=(LOGIN_URL_BROKERS.get(record.broker_name) or {}).get("code_param"),
    )


def _merge_with_stored(incoming: BrokerCredentials, existing: Optional[BrokerCredentialRecord]) -> BrokerCredentials:
    """A field left blank keeps its stored value, so pasting today's access token does not wipe the
    API key/secret saved yesterday. Values are trimmed (a pasted trailing space fails the broker
    login). Clearing a field means deleting the credential and storing it again."""
    fresh = {k: v.strip() if isinstance(v, str) else v for k, v in incoming.model_dump(exclude_none=True).items()}
    fresh = {k: v for k, v in fresh.items() if v != ""}
    stored: dict = {}
    if existing is not None:
        try:
            stored = load_credentials(existing).model_dump(exclude_none=True)
        except Exception:  # unreadable old payload (e.g. key rotated): start from what was sent
            stored = {}
    if fresh.get("request_token") and "access_token" not in fresh:
        # A new one-time code means a new session is wanted: yesterday's access token must not win
        # over it (the auth-code adapters - Fyers, Kite - skip the exchange while a token is on file,
        # so keeping the stale one would make every pasted code "fail" with yesterday's token).
        stored.pop("access_token", None)
    return BrokerCredentials(**{**stored, **fresh})


def _holder_error(record: BrokerCredentialRecord) -> str:
    return (f"This {record.broker_name} connection ({record.account_label or 'primary'}) was linked by another member of your "
            "organisation. With the bring-your-own-broker data tier on, a broker session belongs to the person who logs in to "
            "it, so only they can use it here. Ask them to log in, or ask an owner to remove it before you link your own account.")


async def _byob(session: AsyncSession, tenant_id: int) -> bool:
    from app.platform.controls import flag_enabled
    return await flag_enabled(session, "byob_data_tier", tenant_id)


async def _ensure_holder(session: AsyncSession, record: BrokerCredentialRecord, user: User) -> None:
    """BT1b: with `byob_data_tier` on, a broker credential row is its account holder's (`user_id` = who linked it and
    logs in to it): another member may not log in to it or read through it - 403. Flag off: no change (any trader of the
    organisation may do the daily login on keys the owner stored, as before)."""
    if record.user_id != user.id and await _byob(session, record.tenant_id):
        raise HTTPException(status_code=403, detail=_holder_error(record))


async def _ensure_holder_or_owner(session: AsyncSession, record: BrokerCredentialRecord, user: User) -> None:
    """BT1b: removing / disconnecting another member's connection (flag on) is for its holder or an organisation owner."""
    if record.user_id != user.id and user.role not in (UserRole.OWNER.value, UserRole.SUPER_ADMIN.value) and await _byob(session, record.tenant_id):
        raise HTTPException(status_code=403, detail=_holder_error(record))


# Smoke-test steps whose detail carries market data (prices, chain, position marks).
SMOKE_PRICE_STEPS = frozenset({"quote", "contract_quote", "option_chain", "positions"})

CHANGED_HANDS = "The broker connection changed while the login was in progress; nothing was stored. Log in again."


async def _write_if_unchanged(session: AsyncSession, record: BrokerCredentialRecord, expected_user_id: int, expected_payload: str,
                              *, new_user_id: Optional[int] = None) -> bool:
    """BT1b (both flag states): persist the credential row's new state only if nobody changed it during the broker
    round-trip - a conditional UPDATE on (id, user_id, payload) with a rowcount check, so a member who stored over the row
    meanwhile can never end up holding this login's fresh token (or the holder's secrets). The ORM's pending changes are
    discarded either way; False = changed meanwhile, nothing written."""
    values = {"encrypted_payload": record.encrypted_payload, "token_status": record.token_status,
              "token_expires_at": record.token_expires_at, "last_verified_at": record.last_verified_at,
              "user_id": expected_user_id if new_user_id is None else new_user_id}
    record_id = record.id
    session.expire(record)
    result = await session.execute(update(BrokerCredentialRecord).where(
        BrokerCredentialRecord.id == record_id, BrokerCredentialRecord.user_id == expected_user_id,
        BrokerCredentialRecord.encrypted_payload == expected_payload).values(**values).execution_options(synchronize_session=False))
    return int(getattr(result, "rowcount", 0) or 0) == 1


@router.post("/{name}/credentials", status_code=status.HTTP_204_NO_CONTENT)
async def store_broker_credentials(
    name: str, credentials: BrokerCredentials,
    user: User = Depends(require_trader), session: AsyncSession = Depends(get_session),
    session_id: Optional[int] = Depends(current_session_id), account_label: str = "primary",
) -> None:
    from app.auth.scopes import has_scope, scope_denied
    from app.auth.verification import ensure_verified
    if not has_scope(user, "brokers:write"):
        raise scope_denied(user, "brokers:write")
    ensure_verified(user, "Storing broker credentials")
    await ensure_live_step_up(session, user, session_id, "Storing broker credentials")
    """Encrypts and stores this tenant's credentials for one broker. Nothing is ever stored in
    plaintext; the ciphertext is only decrypted in memory, on demand, when /authenticate runs.
    Storing resets the token status to UNKNOWN - a pasted access token is unproven until
    /authenticate (or the OAuth callback) succeeds against the broker.
    """
    _ensure_known_broker(name)
    account_label = _clean_label(account_label)
    existing = await get_credential_record(session, user.tenant_id, name, account_label)
    taking_over = existing is not None and existing.user_id != user.id and await _byob(session, user.tenant_id)
    # BT1b (flag on): storing over another member's row makes it yours, so nothing of theirs is kept - no stored secret
    # and above all no stored access token (their broker session): fresh secrets are required and the row starts unproven.
    credentials = _merge_with_stored(credentials, None if taking_over else existing)
    if taking_over and not credentials.api_key:
        raise HTTPException(status_code=400, detail=f"This {name} connection was linked by another member: enter all of your own "
                                                    "credentials (at least the API key) to replace it - nothing of theirs is kept.")
    encrypted = encrypt_text(credentials.model_dump_json(), user.tenant_id, PURPOSE_BROKER_CREDENTIAL)

    if existing:
        # BT1b: conditional on the row being exactly what was merged from (no concurrent store / login in between).
        expected_user, expected_payload = existing.user_id, existing.encrypted_payload
        existing.encrypted_payload = encrypted
        existing.token_status = BrokerTokenStatus.UNKNOWN.value
        existing.token_expires_at = None
        existing.last_verified_at = None
        if not await _write_if_unchanged(session, existing, expected_user, expected_payload, new_user_id=user.id):
            await session.rollback()
            raise HTTPException(status_code=409, detail="The broker connection changed while saving; nothing was stored. Try again.")
        await session.refresh(existing)
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


@router.delete("/{name}/credentials", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_scope("brokers:write"))])
async def delete_broker_credentials(
    name: str, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session), account_label: str = "primary",
) -> None:
    existing = await get_credential_record(session, user.tenant_id, name, _clean_label(account_label))
    if existing:
        await _ensure_holder_or_owner(session, existing, user)
        await session.delete(existing)
        await write_audit_log(session, user.tenant_id, user.id, "broker_credentials_deleted", name)
        await session.commit()


@router.post("/{name}/disconnect", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_scope("brokers:write"))])
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
    await _ensure_holder_or_owner(session, record, user)
    detail = "token revoked at broker"
    try:
        adapter = build_adapter(record)
        if adapter.access_token:
            await adapter.disconnect()
    except Exception as exc:  # noqa: BLE001 - local revocation is what matters
        logger.warning("Broker %s logout call failed for tenant %s: %s", name, user.tenant_id, exc)
        detail = f"broker logout call failed ({type(exc).__name__}); token marked expired locally"
    payload = json.loads(decrypt_text(record.encrypted_payload, PURPOSE_BROKER_CREDENTIAL, record.tenant_id))
    payload.pop("access_token", None)
    record.encrypted_payload = encrypt_text(json.dumps(payload), record.tenant_id, PURPOSE_BROKER_CREDENTIAL)
    record.token_status = BrokerTokenStatus.EXPIRED.value
    record.token_expires_at = None
    await write_audit_log(session, user.tenant_id, user.id, "broker_disconnected", f"{name}: {detail}")
    await session.commit()


@router.post("/{name}/smoke-test", dependencies=[Depends(require_scope("brokers:write"))])
async def smoke_test_broker(
    name: str, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session), account_label: str = "primary",
) -> dict:
    """Phase AJ: read-only probes of this stored session - profile, funds, instruments, an index quote, one
    option contract resolved and quoted through the worker's own symbol translation, positions and today's
    order book. Never places, modifies or cancels an order. The report is a checklist, not an exception:
    a broker that rejects the token shows up as a failed `profile` line."""
    _ensure_known_broker(name)
    label = _clean_label(account_label)
    record = await get_credential_record(session, user.tenant_id, name, label)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No stored credentials for broker '{name}' ({label})")
    await _ensure_holder(session, record, user)          # BT1b (flag on): quotes, funds and positions are the holder's
    adapter = build_adapter(record)
    report = await run_smoke(adapter, account_label=label)
    await write_audit_log(session, user.tenant_id, user.id, "broker_smoke_test", f"{name}/{label}: {'ok' if report.ok else 'FAILED'} - {report.summary}")
    await session.commit()
    out = report.as_dict()
    if await _byob(session, user.tenant_id):
        from app.market_data.data_terms import terms_for
        terms = terms_for(name)
        if not terms.tier_a_allowed:
            # BT1b: this broker's market data may not be shown in ATP - the probe's outcome stays, its prices do not.
            hidden = f"details hidden: {terms.display_name} market data may not be shown in ATP"
            for step in out.get("steps", []):
                if step.get("name") in SMOKE_PRICE_STEPS:
                    step["detail"] = hidden
    return out


@router.post("/{name}/authenticate", response_model=BrokerProfile, dependencies=[Depends(require_scope("brokers:write"))])
async def authenticate_broker(
    name: str, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session), account_label: str = "primary",
) -> BrokerProfile:
    """Loads this tenant's stored (encrypted) credentials for `name` / `account_label` (default "primary"),
    decrypts them in memory, and performs a real login against that broker. Success marks the stored token
    VALID until the broker's next daily expiry (and persists a newly exchanged access token, if the login
    produced one); a token-related failure marks it EXPIRED so LIVE deployments stop entering.
    """
    _ensure_known_broker(name)
    label = _clean_label(account_label)
    record = await get_credential_record(session, user.tenant_id, name, label)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No stored credentials for broker '{name}' ({label})")
    await _ensure_holder(session, record, user)
    expected_user, expected_payload = record.user_id, record.encrypted_payload

    credentials = load_credentials(record)
    adapter = get_broker_adapter(name, credentials)

    try:
        profile = await adapter.authenticate()
    except Exception as exc:
        # Broker-side errors (BrokerError) and raw network failures (DNS, timeout, TLS, ...)
        # both mean the same thing to the caller: authentication did not succeed. Either way
        # this must come back as a clean error, never an unhandled 500, and always be audited.
        await write_audit_log(session, user.tenant_id, user.id, "broker_authentication_failed", f"{name}/{label}: {exc}")
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
            title=f"{name} ({label}) authentication failed", message=str(exc),
            severity=NotificationSeverity.CRITICAL, user_id=user.id,
        )
        raise HTTPException(status_code=502, detail=f"Broker authentication failed: {exc}") from exc

    new_token = getattr(adapter, "access_token", None)
    if new_token and new_token != credentials.access_token:
        store_access_token(record, new_token)
    else:
        mark_verified(record)
    if not await _write_if_unchanged(session, record, expected_user, expected_payload):
        await session.rollback()
        raise HTTPException(status_code=409, detail=CHANGED_HANDS)
    await write_audit_log(session, user.tenant_id, user.id, "broker_authenticated", f"{name}/{label}")
    await session.commit()
    return profile


# --- Paste-the-code login (Fyers, Kite) -----------------------------------------------------------
#
# These brokers send the browser to the redirect URL registered on *their* developer console, not
# to the platform, so the platform cannot receive the code. What it can do: open the right login page
# (built from the stored App ID and redirect URI - no secret leaves the server) and take the pasted
# code in one step: store it as the one-time request_token (dropping yesterday's access token),
# exchange it, persist the new token encrypted, and mark the session VALID for the day.


class LoginUrlResponse(BaseModel):
    broker_name: str
    authorization_url: str
    code_param: str
    instructions: str


class LoginCodeBody(BaseModel):
    code: str


def _code_from_paste(raw: str, code_param: str) -> str:
    """Accept the bare code or the whole redirected URL pasted from the address bar (query or fragment)."""
    text = (raw or "").strip()
    if "?" in text or "&" in text or "=" in text or "#" in text:
        from urllib.parse import parse_qs, urlparse
        try:
            parsed = urlparse(text) if "://" in text else None
        except ValueError:                      # a stray "[" makes urlparse raise; treat the paste as the bare code
            return text
        parts = [parsed.query, parsed.fragment] if parsed is not None else [text.lstrip("?#")]
        for part in parts:
            values = parse_qs(part).get(code_param) or parse_qs(part).get("code") or []
            if values and values[0].strip():
                return values[0].strip()
    return text


@router.get("/{name}/login-url", response_model=LoginUrlResponse)
async def broker_login_url(
    name: str, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session), account_label: str = "primary",
) -> LoginUrlResponse:
    _ensure_known_broker(name)
    spec = LOGIN_URL_BROKERS.get(name)
    if spec is None:
        raise HTTPException(status_code=400, detail=f"{name} has no hosted login page the platform can open (Upstox: use the OAuth button)")
    record = await get_credential_record(session, user.tenant_id, name, _clean_label(account_label))
    if record is None:
        raise HTTPException(status_code=404, detail=f"Store your {name} API key (App ID) in Settings first")
    await _ensure_holder(session, record, user)          # BT1b (flag on): the holder's App ID / redirect URI stay theirs
    credentials = load_credentials(record)
    if not credentials.api_key:
        raise HTTPException(status_code=400, detail=f"{name} credentials need api_key (the App ID)")
    if spec["needs_redirect_uri"] and not credentials.redirect_uri:
        raise HTTPException(status_code=400, detail=f"Store the Redirect URI exactly as registered on the {name} app (Settings > Redirect URI), then try again")
    url = build_login_url(name, credentials.api_key, credentials.redirect_uri)
    code_param = spec["code_param"]
    return LoginUrlResponse(broker_name=name, authorization_url=url, code_param=code_param,
                            instructions=f"Log in on the {name} page; when the browser lands on your redirect URL, copy the `{code_param}` value "
                                         "from the address bar (or the whole address) and paste it here. The code is single-use and expires in minutes.")


@router.post("/{name}/login-code", response_model=BrokerTokenStatusResponse)
async def broker_login_code(
    name: str, body: LoginCodeBody, request: Request, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session),
    session_id: Optional[int] = Depends(current_session_id), account_label: str = "primary",
) -> BrokerTokenStatusResponse:
    """One step for the daily login: the pasted code replaces the stored one-time request_token and
    yesterday's access token, the adapter exchanges it, the new token is stored encrypted and the
    session is VALID until the broker's next daily expiry. Audited like /authenticate."""
    from app.auth.scopes import has_scope, scope_denied
    from app.auth.verification import ensure_verified
    if not has_scope(user, "brokers:write"):
        raise scope_denied(user, "brokers:write")
    ensure_verified(user, "Logging in to the broker")
    await ensure_live_step_up(session, user, session_id, "Logging in to the broker")
    _ensure_known_broker(name)
    spec = LOGIN_URL_BROKERS.get(name)
    if spec is None:
        raise HTTPException(status_code=400, detail=f"{name} does not log in with a pasted code")
    label = _clean_label(account_label)
    record = await get_credential_record(session, user.tenant_id, name, label)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No stored credentials for broker '{name}' ({label})")
    await _ensure_holder(session, record, user)
    expected_user, expected_payload = record.user_id, record.encrypted_payload
    code = _code_from_paste(body.code, spec["code_param"])
    if not code or len(code) > 4096:
        raise HTTPException(status_code=400, detail="Paste the code (or the whole redirected address)")
    credentials = load_credentials(record)
    if not credentials.api_key or not credentials.api_secret:
        raise HTTPException(status_code=400, detail=f"{name} needs api_key and api_secret stored before a code can be exchanged")
    credentials.access_token = None
    credentials.request_token = code
    try:
        adapter = get_broker_adapter(name, credentials)
        await adapter.authenticate()
    except Exception as exc:  # noqa: BLE001 - every failure is a clean 502, audited
        await write_audit_log(session, user.tenant_id, user.id, "broker_authentication_failed", f"{name}/{label}: {exc}")
        # The broker answered (a rejected / used code, a dead token): the stored session is not usable.
        # A network failure says nothing about it, so the status is left alone, as /authenticate does.
        if isinstance(exc, BrokerError) or _is_auth_failure(exc):
            mark_expired(record)
        await session.commit()
        await notify(session, user.tenant_id, NotificationType.TOKEN_EXPIRED if _is_auth_failure(exc) else NotificationType.BROKER_DISCONNECT,
                     title=f"{name} login failed", message=str(exc), severity=NotificationSeverity.CRITICAL, user_id=user.id)
        raise HTTPException(status_code=502, detail=f"Broker login failed: {exc}") from exc
    new_token = adapter.access_token
    if not new_token:
        raise HTTPException(status_code=502, detail=f"{name} returned no access token for the code")
    # Persist the exchanged token on the stored row (re-encrypted; the one-time code is dropped).
    record.encrypted_payload = encrypt_text(credentials.model_dump_json(), record.tenant_id, PURPOSE_BROKER_CREDENTIAL)
    store_access_token(record, new_token)
    # The person who logged in holds the session (as before); the write is conditional on nobody changing the row meanwhile.
    if not await _write_if_unchanged(session, record, expected_user, expected_payload, new_user_id=user.id):
        await session.rollback()
        raise HTTPException(status_code=409, detail=CHANGED_HANDS)
    await write_audit_log(session, user.tenant_id, user.id, "broker_authenticated", f"{name}/{label} (pasted code)")
    await session.commit()
    await session.refresh(record)
    return _token_status_response(record, request)


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
    session_id: Optional[int] = Depends(current_session_id), account_label: str = "primary",
) -> OAuthStartResponse:
    """Starts the Upstox login for one stored account (`account_label`, default "primary"). The label rides
    in the signed state, so the callback updates the same account's row and no other."""
    await ensure_live_step_up(session, user, session_id, "Logging in to the broker")
    label = _clean_label(account_label)
    record = await get_credential_record(session, user.tenant_id, "upstox", label)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Store your Upstox API key/secret in Settings first ({label})")
    await _ensure_holder(session, record, user)
    credentials = load_credentials(record)
    if not credentials.api_key or not credentials.api_secret:
        raise HTTPException(status_code=400, detail="Upstox credentials need both api_key and api_secret for OAuth")
    redirect_uri = credentials.redirect_uri or str(request.base_url).rstrip("/") + UPSTOX_CALLBACK_PATH
    if credentials.redirect_uri is None:
        # Remember the redirect URI we are about to use so the token exchange sends the same one.
        credentials.redirect_uri = redirect_uri
        record.encrypted_payload = encrypt_text(credentials.model_dump_json(), record.tenant_id, PURPOSE_BROKER_CREDENTIAL)

    state = create_oauth_state(user.tenant_id, user.id, "upstox", account_label=label)
    await write_audit_log(session, user.tenant_id, user.id, "broker_oauth_started", f"upstox/{label}")
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
    try:
        # A state signed before labels were carried means the primary account, as it did then.
        label = _clean_label(claims.get("account_label"))
    except HTTPException:
        return _frontend_redirect(broker="upstox", error="invalid_state")
    record = await get_credential_record(session, tenant_id, "upstox", label)
    if record is None:
        return _frontend_redirect(broker="upstox", error="credentials_missing")
    if record.user_id != user_id and await _byob(session, tenant_id):
        # BT1b (flag on): the state names someone other than the connection's holder - the token would be theirs while
        # the row is someone else's.
        await write_audit_log(session, tenant_id, user_id, "broker_oauth_failed", f"upstox/{label}: not the connection's holder")
        await session.commit()
        return _frontend_redirect(broker="upstox", error="not_holder")
    expected_user, expected_payload = record.user_id, record.encrypted_payload

    credentials = load_credentials(record)
    credentials.access_token = None
    credentials.request_token = code
    credentials.redirect_uri = credentials.redirect_uri or _callback_url(request, "upstox")
    adapter = get_broker_adapter("upstox", credentials)
    try:
        await adapter.authenticate()
    except Exception as exc:  # noqa: BLE001 - must always end in a redirect
        await write_audit_log(session, tenant_id, user_id, "broker_oauth_failed", f"upstox/{label}: {exc}")
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
    if not await _write_if_unchanged(session, record, expected_user, expected_payload):
        # BT1b (both flag states): the row was stored over during the code exchange - the fresh token is not written.
        await session.rollback()
        await write_audit_log(session, tenant_id, user_id, "broker_oauth_failed", f"upstox/{label}: the connection changed during the login")
        await session.commit()
        return _frontend_redirect(broker="upstox", error="changed_during_login")
    await write_audit_log(session, tenant_id, user_id, "broker_authenticated", f"upstox/{label} (oauth)")
    await session.commit()
    return _frontend_redirect(broker="upstox", connected="1")
