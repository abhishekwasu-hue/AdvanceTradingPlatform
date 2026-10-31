"""Broker session-token lifecycle.

Indian retail broker APIs (Upstox, Zerodha Kite, Shoonya) issue an access token that dies every
trading day in the small hours - Upstox at 03:30 IST, Kite around 06:00 IST - and give retail
apps no refresh token. So an autonomous engine cannot "log in once": someone (or a browser
redirect flow) has to produce a fresh token every morning, and the engine has to *know* whether
the token it holds is still good before it fires a live order. This module is that knowledge:

* `token_is_usable(record)` - the gate every LIVE entry passes (VALID and not past expiry).
* `store_access_token(...)` - persists a freshly exchanged token (re-encrypted) with its expiry.
* `verify_token(...)` - a real round-trip to the broker (profile call); marks the record VALID or
  EXPIRED and raises a TOKEN_EXPIRED notification on the latter.
* OAuth "state" helpers for the redirect flows - a short-lived signed token binding the
  callback (which arrives unauthenticated from the browser) to the tenant that started it; for
  Zerodha and Fyers (UI2-5) also a single-use nonce whose hash waits on the credential row.
"""
import contextlib
import hashlib
import hmac
import json
import logging
import re
import secrets
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode

import httpx
import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.base import BrokerInterface
from app.brokers.contract_symbols import wrap_contract_symbols
from app.brokers.exceptions import BrokerAPIError, BrokerAuthenticationError
from app.brokers.models import BrokerCredentials
from app.brokers.registry import get_broker_adapter
from app.core.config import JWT_ALGORITHM, JWT_SECRET_KEY
from app.core.enums import BrokerTokenStatus, NotificationSeverity, NotificationType
from app.db.models import BrokerCredentialRecord
from app.market_data.calendar import IST
from app.notifications.service import notify
from app.secrets_store.encryption import decrypt_text, encrypt_text
from app.secrets_store.envelope import PURPOSE_BROKER_CREDENTIAL

logger = logging.getLogger(__name__)

# When each broker's daily access token is invalidated (IST). Anything not listed gets the
# conservative 06:00 default. These are the documented daily-expiry windows, not guesses about
# session length - a token issued at 09:00 and one issued at 22:00 both die at the same moment.
TOKEN_DAILY_EXPIRY_IST: Dict[str, time] = {
    "upstox": time(3, 30),
    "zerodha": time(6, 0),
    "shoonya": time(6, 0),
    "angel_one": time(5, 0),     # SmartAPI JWTs are invalidated in the early morning
    "fyers": time(6, 0),
    "dhan": time(6, 0),
    # Part 1B: Kotak Neo publishes no session end time (KN-4); the conservative default until it is confirmed. A TOTP
    # login each day, as SEBI's retail-algo framework asks.
    "kotak_neo": time(6, 0),
    # Part 1B: Groww's access token ends daily at 06:00 (Groww's API docs, search extract; the time zone is not stated,
    # IST assumed - GW-2). A TOTP login each morning (one click) or the approval flow.
    "groww": time(6, 0),
}
_DEFAULT_EXPIRY_IST = time(6, 0)
# Brokers whose credential is a long-lived API key/secret rather than a daily session token: once a
# login proves the key, the stored session has no expiry (token_is_usable reads None as "no expiry").
PERMANENT_KEY_BROKERS = {"coindcx"}

OAUTH_STATE_TTL_MINUTES = 10
UPSTOX_AUTHORIZE_URL = "https://api.upstox.com/v2/login/authorization/dialog"

# Brokers whose login is a browser redirect the platform can drive end-to-end.
OAUTH_BROKERS = {"upstox"}

# Brokers whose daily login is a hosted page the platform can only *open*: the broker sends the
# browser to the redirect URL registered on its developer console (not to the platform), and the
# operator copies the one-time code out of that address bar into the Broker page. `code_param` names the
# query parameter that carries it. Fyers: `generate-authcode` -> `?auth_code=...`; Kite: `?request_token=...`.
FYERS_AUTHORIZE_URL = "https://api-t1.fyers.in/api/v3/generate-authcode"
KITE_AUTHORIZE_URL = "https://kite.zerodha.com/connect/login"
LOGIN_URL_BROKERS: Dict[str, Dict[str, Any]] = {
    "fyers": {"code_param": "auth_code", "needs_redirect_uri": True},
    "zerodha": {"code_param": "request_token", "needs_redirect_uri": False},
}


def build_login_url(broker_name: str, api_key: str, redirect_uri: Optional[str] = None, state: str = "atp") -> str:
    """The broker's hosted login page for the paste-the-code flow (LOGIN_URL_BROKERS only)."""
    if broker_name == "fyers":
        if not redirect_uri:
            raise ValueError("Fyers needs the app's redirect URI (the Redirect URL on myapi.fyers.in) stored as redirect_uri")
        return f"{FYERS_AUTHORIZE_URL}?{urlencode({'client_id': api_key, 'redirect_uri': redirect_uri, 'response_type': 'code', 'state': state})}"
    if broker_name == "zerodha":
        return f"{KITE_AUTHORIZE_URL}?{urlencode({'v': '3', 'api_key': api_key})}"
    raise ValueError(f"{broker_name} has no hosted login page the platform can open")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    """SQLite (the test DB) hands `DateTime(timezone=True)` back naive; treat naive as UTC so
    comparisons never blow up on aware-vs-naive."""
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def default_token_expiry(broker_name: str, now: Optional[datetime] = None) -> Optional[datetime]:
    """The next daily invalidation instant for this broker strictly after `now`, as aware UTC; None for a
    broker whose API key never expires on a clock (PERMANENT_KEY_BROKERS)."""
    if broker_name in PERMANENT_KEY_BROKERS:
        return None
    now_ist = (now or _utcnow()).astimezone(IST)
    expiry_time = TOKEN_DAILY_EXPIRY_IST.get(broker_name, _DEFAULT_EXPIRY_IST)
    candidate = datetime.combine(now_ist.date(), expiry_time, tzinfo=IST)
    if candidate <= now_ist:
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def token_is_usable(record: Optional[BrokerCredentialRecord], now: Optional[datetime] = None) -> bool:
    if record is None or record.token_status != BrokerTokenStatus.VALID.value:
        return False
    expires_at = _as_utc(record.token_expires_at)
    return expires_at is None or expires_at > (now or _utcnow())


def load_credentials(record: BrokerCredentialRecord) -> BrokerCredentials:
    return BrokerCredentials(**json.loads(decrypt_text(record.encrypted_payload, PURPOSE_BROKER_CREDENTIAL, record.tenant_id)))


def data_is_usable(record: Optional[BrokerCredentialRecord], now: Optional[datetime] = None) -> bool:
    """Market data can be read through this row: the daily session is usable, or (Upstox) its analytics token is
    unexpired and the last probe found at least one market-data class it serves. Never a gate for orders."""
    if token_is_usable(record, now):
        return True
    from app.brokers.upstox_analytics import analytics_usable
    return record is not None and analytics_usable(record, now)


def build_adapter(record: BrokerCredentialRecord, client: Optional[httpx.AsyncClient] = None) -> BrokerInterface:
    """The tenant's adapter for this credential row. Phase AI: non-Upstox adapters are wrapped so
    derived-contract symbols (spelt the Upstox master's way platform-wide) go out in the broker's
    own spelling and come back in the platform's."""
    adapter = get_broker_adapter(record.broker_name, load_credentials(record), client)
    from app.brokers.upstox_analytics import attach_refusal_hook
    attach_refusal_hook(adapter, record)   # Upstox analytics token: a refusal re-probes this row (this tenant's) only
    from app.brokers.shared_http import count_requests
    for attr in ("_client", "_order_client"):         # M71 PL-2: every HTTP request counted per day (broker_limits)
        count_requests(getattr(adapter, attr, None), record.broker_name)  # type: ignore[arg-type]
    wrapped = wrap_contract_symbols(adapter)
    if isinstance(getattr(record, "id", None), int):
        # M71 PL-2: the credential whose published broker windows a RateLimitedBroker around it shares (broker_limits).
        with contextlib.suppress(AttributeError):
            wrapped.atp_credential_id = record.id   # type: ignore[attr-defined]
    return wrapped


async def get_credential_record(
    session: AsyncSession, tenant_id: int, broker_name: str, account_label: str = "primary",
) -> Optional[BrokerCredentialRecord]:
    """One broker credential row. Phase I2: several accounts at the same broker are several rows
    told apart by `account_label`; callers that predate accounts mean the primary one."""
    return await session.scalar(
        select(BrokerCredentialRecord).where(
            BrokerCredentialRecord.tenant_id == tenant_id, BrokerCredentialRecord.broker_name == broker_name,
            BrokerCredentialRecord.account_label == (account_label or "primary"),
        )
    )


def store_access_token(record: BrokerCredentialRecord, access_token: str, now: Optional[datetime] = None) -> None:
    """Re-encrypts the stored credentials with a freshly obtained access token and marks the
    record VALID until the broker's next daily expiry. The one-time `request_token`/auth code
    is dropped - it is single-use and keeping it around only invites a confusing retry."""
    now = now or _utcnow()
    credentials = load_credentials(record)
    credentials.access_token = access_token
    credentials.request_token = None
    record.encrypted_payload = encrypt_text(credentials.model_dump_json(), record.tenant_id, PURPOSE_BROKER_CREDENTIAL)
    record.token_status = BrokerTokenStatus.VALID.value
    record.token_expires_at = default_token_expiry(record.broker_name, now)
    record.last_verified_at = now


def mark_verified(record: BrokerCredentialRecord, now: Optional[datetime] = None) -> None:
    now = now or _utcnow()
    record.token_status = BrokerTokenStatus.VALID.value
    record.last_verified_at = now
    expires_at = _as_utc(record.token_expires_at)
    if expires_at is None or expires_at <= now:
        record.token_expires_at = default_token_expiry(record.broker_name, now)


def cap_expiry_at_token_exp(record: BrokerCredentialRecord, token: Optional[str]) -> None:
    """P2a: when the session token is a JWT (Dhan, Angel One, Fyers v3, Upstox) with an `exp` claim earlier than the
    stored expiry, the stored expiry becomes that `exp`. The claim is read without verifying the signature - for the expiry only, never for authentication - and
    it can only shorten the session, never lengthen it."""
    if not token or record.token_status != BrokerTokenStatus.VALID.value:
        return
    try:
        claims = jwt.decode(token, options={"verify_signature": False, "verify_exp": False})
        exp = claims.get("exp") if isinstance(claims, dict) else None
        if not isinstance(exp, (int, float)) or isinstance(exp, bool) or exp <= 0:
            return
        token_exp = datetime.fromtimestamp(float(exp), tz=timezone.utc)
    except Exception:  # noqa: BLE001 - not a JWT (Kite, Shoonya) or no readable `exp`: the daily schedule stands
        return
    if token_exp <= _utcnow():
        return                       # the broker has just accepted the token: a past `exp` (clock skew) is not believed
    current = _as_utc(record.token_expires_at)
    if current is None or token_exp < current:
        record.token_expires_at = token_exp


def mark_expired(record: BrokerCredentialRecord) -> None:
    record.token_status = BrokerTokenStatus.EXPIRED.value


# Brokers that answer a dead session with HTTP 200 and an error body: NorenApi (Shoonya) says "Session Expired : Invalid
# Session Key" under `stat: Not_Ok`.
_SESSION_DEAD = re.compile(r"session\s+expired|invalid\s+session", re.IGNORECASE)


def _is_auth_failure(exc: Exception) -> bool:
    if isinstance(exc, BrokerAuthenticationError):
        return True
    if isinstance(exc, BrokerAPIError) and exc.status_code in (401, 403):
        return True
    text = str(exc).lower()
    return "token" in text or "unauthorized" in text or bool(_SESSION_DEAD.search(text))


async def verify_token(
    session: AsyncSession, record: BrokerCredentialRecord, client: Optional[httpx.AsyncClient] = None,
    user_id: Optional[int] = None,
) -> Tuple[bool, str]:
    """Proves the stored token against the broker with a cheap profile call. Marks VALID on
    success; on an authentication failure marks EXPIRED and raises a CRITICAL TOKEN_EXPIRED
    notification (the operator's cue to log in again). A non-auth failure (DNS, timeout, 5xx)
    leaves the status alone - a flaky network is not an expired token. Does not commit."""
    try:
        adapter = build_adapter(record, client)
        await adapter.get_profile()
    except Exception as exc:  # noqa: BLE001 - every failure mode must be classified, never raised
        if _is_auth_failure(exc):
            already_flagged = record.token_status == BrokerTokenStatus.EXPIRED.value
            mark_expired(record)
            if not already_flagged:
                await notify(
                    session, record.tenant_id, NotificationType.TOKEN_EXPIRED,
                    title=f"{record.broker_name} session expired",
                    message=f"Broker rejected the stored token: {exc}. Log in to {record.broker_name} again "
                            "on the Broker page - LIVE deployments are paused until then.",
                    severity=NotificationSeverity.CRITICAL, user_id=user_id,
                )
            logger.warning("Broker token rejected for tenant %s / %s: %s", record.tenant_id, record.broker_name, exc)
            return False, f"Token rejected by {record.broker_name}: {exc}"
        logger.warning("Broker token check inconclusive for tenant %s / %s: %s", record.tenant_id, record.broker_name, exc)
        return False, f"Could not reach {record.broker_name}: {exc}"

    mark_verified(record)
    return True, "Token verified"


# --- OAuth redirect flow (Upstox) -------------------------------------------------------------

def create_oauth_state(tenant_id: int, user_id: int, broker_name: str, now: Optional[datetime] = None,
                       account_label: str = "primary", nonce: Optional[str] = None) -> str:
    """A signed, short-lived state parameter. The broker echoes it back to the (unauthenticated)
    callback, which is the only way the callback can know which tenant's credentials (and which of
    its accounts at that broker) to update - and the signature means a forged callback can't point
    the code at someone else's tenant or account."""
    now = now or _utcnow()
    payload: Dict[str, Any] = {
        "purpose": "broker_oauth", "tenant_id": tenant_id, "user_id": user_id, "broker": broker_name,
        "account_label": account_label or "primary",
        "iat": now, "exp": now + timedelta(minutes=OAUTH_STATE_TTL_MINUTES),
    }
    if nonce is not None:
        payload["nonce"] = nonce
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def parse_oauth_state(state: str) -> Dict[str, Any]:
    try:
        payload = jwt.decode(state, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise ValueError(f"Invalid or expired OAuth state: {exc}") from exc
    if payload.get("purpose") != "broker_oauth":
        raise ValueError("Not a broker OAuth state token")
    return payload


def build_upstox_authorization_url(api_key: str, redirect_uri: str, state: str) -> str:
    query = urlencode({"response_type": "code", "client_id": api_key, "redirect_uri": redirect_uri, "state": state})
    return f"{UPSTOX_AUTHORIZE_URL}?{query}"


# --- OAuth callbacks for Zerodha (Kite) and Fyers (UI2-5) --------------------------------------
#
# Both brokers send the browser to the redirect URL registered on their developer console. With ATP's callback
# registered there, nothing is pasted: the callback receives the one-time code, exchanges it with the adapter's own code
# and stores the session as the paste-the-code login does. The `state` the broker echoes back is the signed token above
# plus a random nonce; only the nonce's SHA-256 waits on the credential row (inside its encrypted payload), with the
# user who started the login and a 10-minute expiry. The callback accepts a state only if it is signed, unexpired, names
# this broker, and its nonce matches the one waiting for the same user - and it clears the nonce before exchanging the
# code, so a state works once (a replay, a second tab or a forged link finds nothing waiting).

KITE_OAUTH_CALLBACK_PATH = "/api/broker/zerodha/oauth/callback"
FYERS_OAUTH_CALLBACK_PATH = "/api/broker/fyers/oauth/callback"
OAUTH_CALLBACK_BROKERS: Dict[str, str] = {"zerodha": KITE_OAUTH_CALLBACK_PATH, "fyers": FYERS_OAUTH_CALLBACK_PATH}


def _nonce_hash(nonce: str) -> str:
    return hashlib.sha256(nonce.encode("utf-8")).hexdigest()


def begin_oauth_login(credentials: BrokerCredentials, tenant_id: int, user_id: int, broker_name: str,
                      account_label: str = "primary", now: Optional[datetime] = None) -> str:
    """Records a fresh single-use nonce on `credentials` (the caller re-encrypts and stores them) and returns the signed
    state to send to the broker. Starting again replaces the waiting nonce, so only the latest login can finish."""
    now = now or _utcnow()
    nonce = secrets.token_urlsafe(24)
    credentials.oauth_pending = {
        "broker": broker_name, "user_id": int(user_id), "nonce_sha256": _nonce_hash(nonce),
        "expires_at": (now + timedelta(minutes=OAUTH_STATE_TTL_MINUTES)).isoformat(),
    }
    return create_oauth_state(tenant_id, user_id, broker_name, now=now, account_label=account_label, nonce=nonce)


def oauth_pending_matches(credentials: BrokerCredentials, claims: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    """True when the state's nonce is the one waiting on this row, for the same broker and user, and not yet lapsed."""
    pending = credentials.oauth_pending or {}
    nonce = claims.get("nonce")
    if not pending or not isinstance(nonce, str) or not nonce:
        return False
    if pending.get("broker") != claims.get("broker") or int(pending.get("user_id") or -1) != int(claims.get("user_id") or -2):
        return False
    try:
        expires_at = _as_utc(datetime.fromisoformat(str(pending.get("expires_at"))))
    except ValueError:
        return False
    if expires_at is None or expires_at <= (now or _utcnow()):
        return False
    return hmac.compare_digest(str(pending.get("nonce_sha256") or ""), _nonce_hash(nonce))


def build_oauth_login_url(broker_name: str, api_key: str, redirect_uri: str, state: str) -> str:
    """The broker's login page for the callback flow. Kite takes no redirect URL in the link (it uses the one registered
    on the app) and hands `redirect_params` back on the redirect; Fyers takes both the redirect URL and `state`."""
    if broker_name == "zerodha":
        return f"{KITE_AUTHORIZE_URL}?{urlencode({'v': '3', 'api_key': api_key, 'redirect_params': urlencode({'state': state})})}"
    if broker_name == "fyers":
        return f"{FYERS_AUTHORIZE_URL}?{urlencode({'client_id': api_key, 'redirect_uri': redirect_uri, 'response_type': 'code', 'state': state})}"
    raise ValueError(f"{broker_name} has no OAuth callback flow")
