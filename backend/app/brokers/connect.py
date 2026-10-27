"""P2a: simple broker connect - one paste, plain-sentence results.

- GET  /api/broker/catalog              the easiest official method per broker and only the fields it needs (catalog.py)
- GET  /api/broker/connections          this organisation's broker connections: connected / needs_login / not_working
- POST /api/broker/{broker}/connect     store the pasted fields (the existing encrypted path) and check them with the broker
- POST /api/broker/{broker}/renew       the same, for the daily token of a broker whose method needs one

Everything here reuses app/brokers/routes.py: `save_credentials` (per-tenant encryption, BT1b holder rules, the
conditional write), `run_authentication` (the real login that marks the session VALID), the analytics-token probe, and
the same scope / e-mail verification / step-up checks the credential route makes. Removing a connection is the existing
DELETE /api/broker/{name}/credentials.

PAPER only: nothing here enables LIVE, touches a deployment or places an order. The checks are the broker's own login
or profile call and the analytics token's read-only GET probe.

A result is {status, message}: "Connected." or ONE plain sentence saying what is wrong and what to do. No codes, no
broker error text, never a token or secret (nor any part of one) in a response or a log line.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import current_session_id, ensure_live_step_up, get_current_user, require_trader
from app.auth.scopes import require_scope
from app.brokers import catalog as cat
from app.brokers import upstox_analytics as ua
from app.brokers.exceptions import BrokerAPIError, BrokerAuthenticationError
from app.brokers.models import BrokerCredentials
from app.brokers.routes import (_byob, _clean_label, _code_from_paste, _ensure_holder, _probe_quietly, run_authentication,
                                save_credentials)
from app.brokers.token_lifecycle import LOGIN_URL_BROKERS, _is_auth_failure, get_credential_record, load_credentials, token_is_usable
from app.core.enums import BrokerTokenStatus
from app.core.rate_limit import user_rate_limit
from app.db.models import BrokerCredentialRecord, User
from app.db.session import get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/broker", tags=["broker"])

CONNECTED = "connected"
NEEDS_LOGIN = "needs_login"
NOT_WORKING = "not_working"

# Each connect sends the broker a login or a handful of read-only GETs with the user's credentials; like the analytics
# check, six a minute is plenty. Connect and renew share the window.
connect_rate_limit = user_rate_limit("broker_connect", limit=6, window_seconds=60)

MAX_FIELD_LENGTH = 4096


class ConnectBody(BaseModel):
    fields: Dict[str, Any] = Field(default_factory=dict)
    account_label: Optional[str] = None


class ConnectResult(BaseModel):
    status: str
    message: str


class CatalogField(BaseModel):
    key: str
    label: str
    kind: str
    placeholder: Optional[str] = None


class CatalogEntry(BaseModel):
    broker: str
    name: str
    method: str
    fields: List[CatalogField]
    how_to: List[str]
    doc_url: str
    daily_renewal: bool
    available: bool
    renew_fields: List[CatalogField] = []
    # Part 1B (TM-5 / TM-6): {"live_orders": open|closed, "live_prices": open|closed, "note": plain words}.
    gates: Dict[str, str] = Field(default_factory=dict)
    requires_ui_v2: bool = False
    # Part 1B: the connect guide - {"points": [...], "links": [{"label", "url"}]} - or None.
    guide: Optional[Dict[str, Any]] = None


class Connection(BaseModel):
    broker: str
    account_label: str
    name: str
    status: str
    message: str
    data_only: bool
    renew: bool


class _Refusal(Exception):
    """A plain sentence for the user; nothing was checked with the broker (or nothing could be stored)."""

    def __init__(self, message: str, status: str = NOT_WORKING) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _result(status: str, message: str) -> ConnectResult:
    return ConnectResult(status=status, message=message)


# --- reading the pasted fields ----------------------------------------------------------------------

def _label(entry: cat.BrokerEntry, key: str) -> str:
    """A field's label inside a sentence: "Access token" reads "access token"; names and acronyms ("Analytics Token",
    "API key", "Client ID", "PIN") stay as they are."""
    text = entry.label_of(key)
    return text[0].lower() + text[1:] if text[1:] == text[1:].lower() else text


def _clean_fields(entry: cat.BrokerEntry, raw: Dict[str, Any], allowed: Tuple[str, ...]) -> Dict[str, str]:
    """The pasted values, trimmed, for the allowed keys only. A key the method does not use, a non-text value, or a
    value that is too long or spans lines is refused with a plain sentence (which never repeats the value)."""
    out: Dict[str, str] = {}
    for key, value in (raw or {}).items():
        if key not in allowed:
            if not allowed:
                raise _Refusal(f"{entry.name} logs in with the details already saved; send the form empty and try again.")
            shown = tuple(k for k in allowed if k in entry.field_keys() + entry.renew_keys()) or allowed
            wanted = " and ".join(_label(entry, k) for k in shown)
            raise _Refusal(f"{entry.name} needs only the {wanted}; remove the other fields and try again.")
        if value is None:
            continue
        if not isinstance(value, str):
            raise _Refusal(f"Paste the {_label(entry, key)} as text and try again.")
        text = value.strip()
        if not text:
            continue
        if len(text) > MAX_FIELD_LENGTH or "\n" in text or "\r" in text:
            raise _Refusal(f"That does not look like a valid {_label(entry, key)}; copy it again as one line and paste it.")
        out[key] = text
    return out


def _require(entry: cat.BrokerEntry, values: Dict[str, str], keys: Tuple[str, ...]) -> None:
    missing = [k for k in keys if not values.get(k)]
    if missing:
        raise _Refusal(f"Paste your {' and '.join(_label(entry, k) for k in missing)} and try again.")


def _no_spaces(entry: cat.BrokerEntry, values: Dict[str, str], keys: Tuple[str, ...]) -> None:
    """Tokens, keys and codes never contain spaces; a password (Shoonya) may, so only these keys are checked."""
    for key in keys:
        if key in values and any(c.isspace() for c in values[key]):
            raise _Refusal(f"That does not look like a valid {_label(entry, key)}; copy it again as one line and paste it.")


# --- messages ---------------------------------------------------------------------------------------

def _rejected_message(entry: cat.BrokerEntry, *, renewing: bool) -> str:
    if entry.method == cat.LOGIN_CODE and renewing:
        return (f"{entry.name} did not accept that login; press Log in again and paste the new address right away, as it "
                "works only once and only for a few minutes.")
    if entry.method == cat.ACCESS_TOKEN:
        return f"{entry.name} did not accept this token; generate a new one and paste it here."
    if entry.method == cat.TOTP_AUTO:
        return f"{entry.name} did not accept these details; check them, including the TOTP secret, and try again."
    return f"{entry.name} did not accept these details; check them and paste them again."


def _unreachable_message(entry: cat.BrokerEntry) -> str:
    return f"Could not reach {entry.name} just now; your details are saved, so try again in a minute."


def _failure_message(entry: cat.BrokerEntry, exc: BaseException, *, renewing: bool) -> str:
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, OSError)):
        return _unreachable_message(entry)
    if isinstance(exc, BrokerAPIError) and exc.status_code is not None and (exc.status_code >= 500 or exc.status_code == 429):
        return _unreachable_message(entry)
    if isinstance(exc, (BrokerAuthenticationError, BrokerAPIError)) or (isinstance(exc, Exception) and _is_auth_failure(exc)):
        return _rejected_message(entry, renewing=renewing)
    return f"{entry.name} did not answer as expected; try again in a minute, and if it keeps failing remove and connect again."


def _http_refusal(exc: HTTPException) -> str:
    """A plain sentence for an HTTPException the shared store / login helpers raise (their detail is not shown)."""
    if exc.status_code == 409:
        return "This connection changed at the same moment somewhere else; try again."
    if exc.status_code == 403:
        return "Another member of your organisation holds this connection; ask them or an owner to remove it first."
    if exc.status_code == 422:
        return "That account name is not valid; use letters, digits, dashes or underscores."
    return "These details could not be saved; check them and try again."


# --- the connection view ------------------------------------------------------------------------------

def _analytics_state(creds: Optional[BrokerCredentials], now: datetime) -> Optional[str]:
    """None (no analytics token), "expired", "refused" (Upstox refused it for every candle class), "works" (a candle
    class works: market data really goes through it, as the data gate requires) or "unverified"."""
    if creds is None or not creds.analytics_token:
        return None
    if ua.is_expired(creds, now):
        return "expired"
    coverage = (creds.analytics_meta or {}).get("coverage") or {}
    statuses = {c: (coverage.get(c) or {}).get("status") for c in ua.CLASSES}
    if any(statuses[c] == ua.WORKS for c in ua.CANDLE_CLASSES) and ua.expires_at(creds) is not None:
        return "works"
    if all(statuses[c] == ua.REFUSED for c in ua.CANDLE_CLASSES):
        return "refused"
    return "unverified"


def _session_message(entry: cat.BrokerEntry, started: bool) -> str:
    opening = f"Today's {entry.name} session has ended" if started else f"{entry.name} is not logged in today"
    if entry.method == cat.ACCESS_TOKEN:
        return f"{opening}; paste today's token to reconnect."
    if entry.method == cat.LOGIN_CODE:
        return f"{opening}; press Log in, sign in and paste the address you land on."
    return f"{opening}; press Log in to start today's session."


def connection_view(record: BrokerCredentialRecord, now: Optional[datetime] = None) -> Dict[str, Any]:
    """{status, message, data_only, renew} for one credential row - never a token or a secret."""
    now = now or _utcnow()
    entry = cat.entry(record.broker_name)
    name = cat.display_name(record.broker_name)
    session_ok = token_is_usable(record, now)

    def view(status: str, message: str, *, data_only: bool = False, renew: bool = False) -> Dict[str, Any]:
        return {"status": status, "message": message, "data_only": data_only, "renew": renew}

    if record.broker_name == ua.BROKER:
        try:
            creds: Optional[BrokerCredentials] = load_credentials(record)
        except Exception:  # noqa: BLE001 - an unreadable payload (key rotated): the row cannot be used
            return view(NOT_WORKING, f"The saved {name} details can no longer be read; remove this connection and connect again.")
        state = _analytics_state(creds, now)
        if state == "works":
            if session_ok:
                return view(CONNECTED, "Connected.")
            return view(CONNECTED, "Connected for market data, charts, the scanner, the option chain and paper trading.", data_only=True)
        if session_ok:
            return view(CONNECTED, "Connected.")
        if state == "expired":
            return view(NOT_WORKING, "Your Upstox Analytics Token has expired; generate a new one in Upstox Developer Apps and paste it here.")
        if state == "refused":
            return view(NOT_WORKING, "Upstox no longer accepts this Analytics Token; generate a new one in Upstox Developer Apps and paste it here.")
        if state == "unverified":
            return view(NOT_WORKING, "Upstox has not confirmed the Analytics Token yet; press Connect again in a minute.")
        return view(NEEDS_LOGIN, "Today's Upstox token has ended; paste your Analytics Token to stay connected for a year.", renew=True)

    if session_ok:
        return view(CONNECTED, "Connected.")
    if entry is None:
        return view(NOT_WORKING, f"{name} cannot be connected here yet; remove this connection.")
    if entry.daily_renewal:
        return view(NEEDS_LOGIN, _session_message(entry, record.last_verified_at is not None), renew=True)
    if record.token_status == BrokerTokenStatus.EXPIRED.value:
        return view(NOT_WORKING, f"{name} no longer accepts these details; create new ones and connect again.")
    return view(NEEDS_LOGIN, f"{name} has not confirmed these details yet; press Connect to check them.")


# --- routes -------------------------------------------------------------------------------------------

@router.get("/catalog", response_model=List[CatalogEntry])
async def broker_catalog(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> List[Dict[str, Any]]:
    """The easiest official way to connect each broker and only the fields it needs (static; see catalog.py). A broker
    added as new user-facing work (Part 1B: Kotak Neo) is listed only while `ui_v2` is on for the organisation."""
    rows = cat.catalog()
    if any(r.get("requires_ui_v2") for r in rows):
        from app.platform.controls import flag_enabled
        if not await flag_enabled(session, "ui_v2", user.tenant_id):
            rows = [r for r in rows if not r.get("requires_ui_v2")]
    return rows


@router.get("/connections", response_model=List[Connection])
async def broker_connections(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> List[Connection]:
    """This organisation's broker connections, each with a plain-sentence status. Never returns a token or a secret."""
    records = await session.scalars(
        select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == user.tenant_id)
        .order_by(BrokerCredentialRecord.broker_name, BrokerCredentialRecord.account_label))
    now = _utcnow()
    return [Connection(broker=r.broker_name, account_label=r.account_label or "primary", name=cat.display_name(r.broker_name),
                       **connection_view(r, now)) for r in records]


async def _checks(session: AsyncSession, user: User, session_id: Optional[int]) -> None:
    """The same gates the existing credential route runs before storing anything (not weakened, not caught)."""
    from app.auth.scopes import has_scope, scope_denied
    from app.auth.verification import ensure_verified
    if not has_scope(user, "brokers:write"):
        raise scope_denied(user, "brokers:write")
    ensure_verified(user, "Storing broker credentials")
    await ensure_live_step_up(session, user, session_id, "Storing broker credentials")


@router.post("/{broker}/connect", response_model=ConnectResult,
             dependencies=[Depends(require_scope("brokers:write")), Depends(connect_rate_limit)])
async def connect_broker(broker: str, body: ConnectBody, user: User = Depends(require_trader),
                         session: AsyncSession = Depends(get_session),
                         session_id: Optional[int] = Depends(current_session_id)) -> ConnectResult:
    """Store the pasted fields (encrypted, per tenant) and check them with the broker. PAPER only - places nothing."""
    return await _connect(broker, body, user, session, session_id, renewing=False)


@router.post("/{broker}/renew", response_model=ConnectResult,
             dependencies=[Depends(require_scope("brokers:write")), Depends(connect_rate_limit)])
async def renew_broker(broker: str, body: ConnectBody, user: User = Depends(require_trader),
                       session: AsyncSession = Depends(get_session),
                       session_id: Optional[int] = Depends(current_session_id)) -> ConnectResult:
    """Today's login for a broker whose method renews daily: the pasted token or login address (or nothing, for a TOTP
    login), checked with the broker. PAPER only - places nothing."""
    return await _connect(broker, body, user, session, session_id, renewing=True)


async def _connect(broker: str, body: ConnectBody, user: User, session: AsyncSession, session_id: Optional[int], *,
                   renewing: bool) -> ConnectResult:
    entry = cat.entry(broker)
    if entry is None or not entry.as_dict()["available"]:
        raise HTTPException(status_code=404, detail="This broker cannot be connected here.")
    await _checks(session, user, session_id)
    try:
        label = _clean_label(body.account_label)
    except HTTPException as exc:
        return _result(NOT_WORKING, _http_refusal(exc))
    try:
        if entry.broker == ua.BROKER:
            status, message = await _connect_upstox(entry, body, user, session, label)
        else:
            status, message = await _connect_other(entry, body, user, session, label, renewing=renewing)
    except _Refusal as refusal:
        status, message = refusal.status, refusal.message
    except HTTPException as exc:
        status, message = NOT_WORKING, _http_refusal(exc)
    logger.info("Broker %s %s for tenant %s (%s): %s", "renew" if renewing else "connect", entry.broker, user.tenant_id, label, status)
    return _result(status, message)


async def _holder_check(session: AsyncSession, user: User, broker: str, label: str, values: Dict[str, str], *,
                        login: bool) -> Optional[BrokerCredentialRecord]:
    """BT1b, as the existing routes apply it: with `byob_data_tier` on, a connection belongs to the member who linked it.
    A login on it (renew) is theirs alone (`_ensure_holder`); storing over it is a take-over that needs a full set of
    one's own credentials including the API key (`save_credentials`) - refused up front here with a plain sentence when
    the pasted fields cannot be one. Returns the existing row, if any."""
    record = await get_credential_record(session, user.tenant_id, broker, label)
    if record is None or record.user_id == user.id:
        return record
    if login:
        await _ensure_holder(session, record, user)
    elif not values.get("api_key") and await _byob(session, user.tenant_id):
        raise _Refusal("Another member of your organisation holds this connection; ask them or an owner to remove it first.")
    return record


async def _verify(entry: cat.BrokerEntry, session: AsyncSession, user: User, record: BrokerCredentialRecord, label: str, *,
                  renewing: bool) -> Tuple[str, str]:
    try:
        profile, exc = await run_authentication(session, user, record, label)
    except HTTPException:
        raise
    except Exception as construct_exc:  # noqa: BLE001 - the adapter refused the stored fields before any call
        logger.info("Broker %s login could not start for tenant %s: %s", entry.broker, user.tenant_id, type(construct_exc).__name__)
        return NOT_WORKING, "Something went wrong on our side; try again in a minute."
    if exc is None:
        return CONNECTED, "Connected."
    logger.info("Broker %s login failed for tenant %s: %s", entry.broker, user.tenant_id, type(exc).__name__)
    return NOT_WORKING, _failure_message(entry, exc, renewing=renewing)


async def _connect_other(entry: cat.BrokerEntry, body: ConnectBody, user: User, session: AsyncSession, label: str, *,
                         renewing: bool) -> Tuple[str, str]:
    if renewing and not entry.daily_renewal:
        raise _Refusal(f"{entry.name} needs no daily login; use Connect to change its details.")
    keys = entry.renew_keys() if renewing else entry.field_keys()
    values = _clean_fields(entry, body.fields, keys)
    _require(entry, values, keys)
    # A Shoonya password may contain spaces; every key, token, code and address may not.
    _no_spaces(entry, values, tuple(k for k in values if not (entry.broker == "shoonya" and k == "api_secret")))

    if not renewing:
        await _holder_check(session, user, entry.broker, label, values, login=False)
        record, _ = await save_credentials(session, user, entry.broker, label, BrokerCredentials.model_validate(values))
        if entry.method == cat.LOGIN_CODE:
            # The key and secret cannot be proven without a login; the next step is the broker's own login page.
            return NEEDS_LOGIN, f"Saved; now press Log in, sign in to {entry.name} and paste the address you land on."
        return await _verify(entry, session, user, record, label, renewing=False)

    existing = await _holder_check(session, user, entry.broker, label, values, login=True)
    if existing is None:
        raise _Refusal(f"Connect {entry.name} first, then log in.")
    if entry.method == cat.LOGIN_CODE:
        stored = _reload_credentials(existing)
        if stored is None or not stored.api_key or not stored.api_secret:
            raise _Refusal(f"Connect {entry.name} with its key and secret first, then log in.")
        spec = LOGIN_URL_BROKERS.get(entry.broker) or {}
        pasted = values["request_token"]
        code = _code_from_paste(pasted, spec.get("code_param", "code"))
        # An address without the code (a wrong page, a cancelled login) must never be saved as the code: it would
        # replace a working session with nothing.
        address_without_code = any(t in pasted for t in ("://", "?", "=")) and code == pasted.strip()
        if address_without_code or not code or any(c.isspace() for c in code) or len(code) > MAX_FIELD_LENGTH:
            raise _Refusal("Paste the whole address you land on after logging in.")
        values = {"request_token": code}      # replaces yesterday's access token (save_credentials drops it)
    if values:
        record, _ = await save_credentials(session, user, entry.broker, label, BrokerCredentials.model_validate(values))
    else:
        record = existing                     # a TOTP login: the stored secret makes today's code
    return await _verify(entry, session, user, record, label, renewing=True)


async def _connect_upstox(entry: cat.BrokerEntry, body: ConnectBody, user: User, session: AsyncSession,
                          label: str) -> Tuple[str, str]:
    """Either field may carry either kind of token: the claims (or, when they do not say, a read-only probe) decide
    whether it is stored as the analytics token or as the daily access token."""
    allowed = entry.field_keys() + tuple(f.key for f in cat.UPSTOX_ALTERNATIVE_FIELDS)
    values = _clean_fields(entry, body.fields, allowed)
    if len(values) > 1:
        raise _Refusal("Paste one Upstox token at a time and try again.")
    if not values:
        raise _Refusal("Paste your Upstox Analytics Token and try again.")
    pasted_as, token = next(iter(values.items()))
    _no_spaces(entry, values, (pasted_as,))
    if len(token) > ua.MAX_TOKEN_LENGTH:
        raise _Refusal("That does not look like an Upstox token; copy it again as one line and paste it.")

    await _holder_check(session, user, entry.broker, label, {}, login=False)    # before any call to Upstox
    kind = ua.token_kind_from_claims(token)
    if kind is None:
        kind = await ua.probe_token_kind(token)
        if kind is None:
            raise _Refusal("Could not reach Upstox to check this token; nothing was saved, so try again in a minute.")
        if kind == ua.EITHER_KIND:
            # Both reads answered (an analytics token used from a registered static IP does that too): the box decides,
            # and the default box is the Analytics Token.
            kind = ua.DAILY_KIND if pasted_as == cat.ACCESS_TOKEN else ua.ANALYTICS_KIND
    if kind == ua.EXPIRED_KIND:
        raise _Refusal("This Upstox token has already expired; generate a new one and paste it here.")
    if kind == ua.REJECTED_KIND:
        raise _Refusal("Upstox did not accept this token; copy it again from Upstox Developer Apps and paste it here.")

    if kind == ua.ANALYTICS_KIND:
        record, _ = await save_credentials(session, user, entry.broker, label, BrokerCredentials(analytics_token=token))
        if pasted_as != cat.ANALYTICS_TOKEN:
            logger.info("Upstox token pasted as a daily token for tenant %s is an analytics token; stored as one", user.tenant_id)
        view = await _probe_quietly(session, record)       # always re-checked, so "Connect again" re-probes
        state = _analytics_state(_reload_credentials(record), _utcnow()) if view is not None else "unverified"
        if state == "works":
            return CONNECTED, "Connected."
        if state == "refused":
            return NOT_WORKING, "Upstox did not accept this Analytics Token; generate a new one in Upstox Developer Apps and paste it here."
        if state == "expired":
            return NOT_WORKING, "This Upstox Analytics Token has expired; generate a new one in Upstox Developer Apps and paste it here."
        return NOT_WORKING, "Could not reach Upstox to check the Analytics Token; it is saved, so press Connect again in a minute."

    # A daily access token (pasted in either box): stored as the session token and proven with the profile call.
    if pasted_as == cat.ANALYTICS_TOKEN:
        logger.info("Upstox token pasted as an analytics token for tenant %s is a daily token; stored as one", user.tenant_id)
    record, _ = await save_credentials(session, user, entry.broker, label, BrokerCredentials(access_token=token))
    status, message = await _verify(entry, session, user, record, label, renewing=False)
    if status == CONNECTED and pasted_as == cat.ANALYTICS_TOKEN:
        return CONNECTED, "Connected for today only, because this is a daily token; paste your Analytics Token to stay connected for a year."
    if status == NOT_WORKING and message.startswith(entry.name + " did not accept"):
        return NOT_WORKING, "Upstox did not accept this token; paste your Analytics Token from Upstox Developer Apps instead."
    return status, message


def _reload_credentials(record: BrokerCredentialRecord) -> Optional[BrokerCredentials]:
    try:
        return load_credentials(record)
    except Exception:  # noqa: BLE001
        return None
