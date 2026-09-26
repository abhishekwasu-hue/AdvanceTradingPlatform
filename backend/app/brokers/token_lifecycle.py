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
* OAuth "state" helpers for the Upstox redirect flow - a short-lived signed token binding the
  callback (which arrives unauthenticated from the browser) to the tenant that started it.
"""
import json
import logging
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode

import httpx
import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.base import BrokerInterface
from app.brokers.exceptions import BrokerAPIError, BrokerAuthenticationError
from app.brokers.models import BrokerCredentials
from app.brokers.registry import get_broker_adapter
from app.core.config import JWT_ALGORITHM, JWT_SECRET_KEY
from app.core.enums import BrokerTokenStatus, NotificationSeverity, NotificationType
from app.db.models import BrokerCredentialRecord
from app.market_data.calendar import IST
from app.notifications.service import notify
from app.secrets_store.encryption import decrypt_text, encrypt_text

logger = logging.getLogger(__name__)

# When each broker's daily access token is invalidated (IST). Anything not listed gets the
# conservative 06:00 default. These are the documented daily-expiry windows, not guesses about
# session length - a token issued at 09:00 and one issued at 22:00 both die at the same moment.
TOKEN_DAILY_EXPIRY_IST: Dict[str, time] = {
    "upstox": time(3, 30),
    "zerodha": time(6, 0),
    "shoonya": time(6, 0),
}
_DEFAULT_EXPIRY_IST = time(6, 0)

OAUTH_STATE_TTL_MINUTES = 10
UPSTOX_AUTHORIZE_URL = "https://api.upstox.com/v2/login/authorization/dialog"

# Brokers whose login is a browser redirect the platform can drive end-to-end.
OAUTH_BROKERS = {"upstox"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    """SQLite (the test DB) hands `DateTime(timezone=True)` back naive; treat naive as UTC so
    comparisons never blow up on aware-vs-naive."""
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def default_token_expiry(broker_name: str, now: Optional[datetime] = None) -> datetime:
    """The next daily invalidation instant for this broker strictly after `now`, as aware UTC."""
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
    return BrokerCredentials(**json.loads(decrypt_text(record.encrypted_payload)))


def build_adapter(record: BrokerCredentialRecord, client: Optional[httpx.AsyncClient] = None) -> BrokerInterface:
    return get_broker_adapter(record.broker_name, load_credentials(record), client)


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
    record.encrypted_payload = encrypt_text(credentials.model_dump_json())
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


def mark_expired(record: BrokerCredentialRecord) -> None:
    record.token_status = BrokerTokenStatus.EXPIRED.value


def _is_auth_failure(exc: Exception) -> bool:
    if isinstance(exc, BrokerAuthenticationError):
        return True
    if isinstance(exc, BrokerAPIError) and exc.status_code in (401, 403):
        return True
    return "token" in str(exc).lower() or "unauthorized" in str(exc).lower()


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
                            "from Settings - LIVE deployments are paused until then.",
                    severity=NotificationSeverity.CRITICAL, user_id=user_id,
                )
            logger.warning("Broker token rejected for tenant %s / %s: %s", record.tenant_id, record.broker_name, exc)
            return False, f"Token rejected by {record.broker_name}: {exc}"
        logger.warning("Broker token check inconclusive for tenant %s / %s: %s", record.tenant_id, record.broker_name, exc)
        return False, f"Could not reach {record.broker_name}: {exc}"

    mark_verified(record)
    return True, "Token verified"


# --- OAuth redirect flow (Upstox) -------------------------------------------------------------

def create_oauth_state(tenant_id: int, user_id: int, broker_name: str, now: Optional[datetime] = None) -> str:
    """A signed, short-lived state parameter. The broker echoes it back to the (unauthenticated)
    callback, which is the only way the callback can know which tenant's credentials to update -
    and the signature means a forged callback can't point the code at someone else's tenant."""
    now = now or _utcnow()
    payload: Dict[str, Any] = {
        "purpose": "broker_oauth", "tenant_id": tenant_id, "user_id": user_id, "broker": broker_name,
        "iat": now, "exp": now + timedelta(minutes=OAUTH_STATE_TTL_MINUTES),
    }
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
