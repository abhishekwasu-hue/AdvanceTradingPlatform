"""Upstox Analytics Token: an optional, data-only credential next to the daily login.

Upstox lets an account generate one long-lived (one year), read-only, GET-only token in Developer Apps -> Analytics.
ATP stores it as `analytics_token` inside the Upstox credential's encrypted payload (the same per-tenant encryption as
every other credential field; the value is never returned or logged) and uses it for market data only, so charts, the
scanner and the option chain keep working without the daily login. Orders, and anything that is not a GET, always go
with the daily login token.

Which market-data endpoints accept the token is not taken on trust from the docs: when the token is saved, and on
"Check connection", `probe()` sends one cheap GET per endpoint class (plus the market-data websocket's authorize call
and a bare connect/close) and records a coverage map. The Upstox adapter then uses the analytics token only for the
classes marked `works`, and falls back to the daily token for everything else. A data call the broker refuses with the
analytics token (401/403) stops using it for that class at once and schedules a re-probe of that class, which records
`refused` if the refusal repeats.

Metadata kept with the token (inside the same encrypted payload, under `analytics_meta`): the expiry (the JWT `exp`
claim when the token is a JWT - read for display only, never trusted for auth - else the save date + 365 days, flagged
as an estimate), the save time and the coverage map. After the expiry the token is not used any more. Seven days before
it, and once more on the last day, the credential's holder gets one notification each (`send_expiry_warnings`).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time as _time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, Iterable, Optional, Set, Tuple
from urllib.parse import quote

import httpx
import jwt
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.models import BrokerCredentials

logger = logging.getLogger(__name__)

BROKER = "upstox"
FIELD = "analytics_token"
META = "analytics_meta"
VALIDITY_DAYS = 365
WARN_DAYS = 7
MAX_TOKEN_LENGTH = 4096

# The five endpoint classes ATP's market data uses (app/brokers/upstox.py, app/market_data/stream.py).
HISTORICAL = "historical_candles"
INTRADAY = "intraday_candles"
QUOTES = "quotes_ltp"
OPTION_CHAIN = "option_chain"
WEBSOCKET = "market_data_websocket"
CLASSES: Tuple[str, ...] = (HISTORICAL, INTRADAY, QUOTES, OPTION_CHAIN, WEBSOCKET)

WORKS = "works"
REFUSED = "refused"
UNVERIFIED = "unverified"

API_ROOT = "https://api.upstox.com"
PROBE_INSTRUMENT = "NSE_INDEX|Nifty 50"      # a known liquid instrument: every class can be asked about it
PROBE_TIMEOUT_SECONDS = 8.0
REPROBE_COOLDOWN_SECONDS = 600

# For UI2-6 (the Connect flow). UI2-5's broker field map (BROKER_FIELD_SPECS / GET /api/broker/specs) absorbs this entry
# into Upstox's field list; until then it is the one place the label, hint and kind are spelt.
FIELD_SPEC: Dict[str, Any] = {
    "name": FIELD, "label": "Analytics token (read-only, valid one year)",
    "hint": "Generate it in Upstox Developer Apps → Analytics", "kind": "secret", "required": False,
}
UPSTOX_FIELD_SPECS: Tuple[Dict[str, Any], ...] = (FIELD_SPEC,)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# --- classifying a request ----------------------------------------------------------------------

def classify(method: str, path: str) -> Optional[str]:
    """The endpoint class of one Upstox request, or None (not market data, or not a GET - never the analytics token)."""
    if (method or "").upper() != "GET":
        return None
    p = path or ""
    if "/feed/market-data-feed/authorize" in p:
        return WEBSOCKET
    if "/historical-candle/intraday/" in p:
        return INTRADAY
    if "/historical-candle/" in p:
        return HISTORICAL
    if "/market-quote/" in p:
        return QUOTES
    if "/option/chain" in p or "/option/contract" in p:
        return OPTION_CHAIN
    return None


# --- expiry and metadata ------------------------------------------------------------------------

def token_expiry(token: str, saved_at: datetime) -> Tuple[datetime, bool]:
    """(expiry, estimated). A JWT's `exp` claim, read without verifying the signature - for display and for the
    "stop using it after" date only, never for authentication; anything else: the save date + 365 days (estimated)."""
    try:
        claims = jwt.decode(token, options={"verify_signature": False, "verify_exp": False})
        exp = claims.get("exp") if isinstance(claims, dict) else None
        if isinstance(exp, (int, float)) and exp > 0:
            return datetime.fromtimestamp(float(exp), tz=timezone.utc), False
    except Exception:  # noqa: BLE001 - not a JWT (or a malformed one): fall back to the documented one-year validity
        pass
    return saved_at + timedelta(days=VALIDITY_DAYS), True


def new_meta(token: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or _utcnow()
    expires_at, estimated = token_expiry(token, now)
    return {"saved_at": now.isoformat(), "expires_at": expires_at.isoformat(), "expiry_estimated": estimated,
            "probed_at": None, "coverage": {c: {"status": UNVERIFIED} for c in CLASSES}}


def expires_at(credentials: BrokerCredentials) -> Optional[datetime]:
    return _parse_dt((credentials.analytics_meta or {}).get("expires_at"))


def is_expired(credentials: BrokerCredentials, now: Optional[datetime] = None) -> bool:
    exp = expires_at(credentials)
    return exp is not None and exp <= (now or _utcnow())


def working_classes(credentials: BrokerCredentials, now: Optional[datetime] = None) -> Set[str]:
    """The endpoint classes the analytics token may serve right now: a token is stored, it has not expired, and the
    last probe marked the class `works`. Empty otherwise (no token, expired, never probed, refused)."""
    if not credentials.analytics_token or not credentials.analytics_meta or is_expired(credentials, now):
        return set()
    if expires_at(credentials) is None:
        return set()
    coverage = credentials.analytics_meta.get("coverage") or {}
    return {c for c in CLASSES if (coverage.get(c) or {}).get("status") == WORKS}


def _credentials_of(record: Any) -> Optional[BrokerCredentials]:
    from app.brokers.token_lifecycle import load_credentials
    if getattr(record, "broker_name", None) != BROKER:
        return None
    try:
        return load_credentials(record)
    except Exception:  # noqa: BLE001 - an unreadable payload simply has no analytics token
        return None


REST_CLASSES = frozenset({HISTORICAL, INTRADAY, QUOTES, OPTION_CHAIN})


def analytics_usable(record: Any, now: Optional[datetime] = None) -> bool:
    """True when this credential row can serve some REST market data (candles, quotes, option chain) through its
    analytics token alone, without the daily login."""
    creds = _credentials_of(record)
    return bool(creds and working_classes(creds, now) & REST_CLASSES)


def public_view(credentials: Optional[BrokerCredentials], now: Optional[datetime] = None) -> Dict[str, Any]:
    """What an API response may say about the analytics token: whether one is set, its expiry and the coverage map -
    never the value."""
    if credentials is None or not credentials.analytics_token:
        return {"analytics_token_set": False, "analytics_token_expires_at": None, "analytics_token_expiry_estimated": None,
                "analytics_token_expired": None, "analytics_coverage": None, "analytics_probed_at": None}
    meta = credentials.analytics_meta or {}
    coverage = {c: dict((meta.get("coverage") or {}).get(c) or {"status": UNVERIFIED}) for c in CLASSES}
    return {"analytics_token_set": True, "analytics_token_expires_at": meta.get("expires_at"),
            "analytics_token_expiry_estimated": meta.get("expiry_estimated"),
            "analytics_token_expired": is_expired(credentials, now), "analytics_coverage": coverage,
            "analytics_probed_at": meta.get("probed_at")}


def record_view(record: Any, now: Optional[datetime] = None) -> Dict[str, Any]:
    return public_view(_credentials_of(record), now)


# --- the read-only probe ------------------------------------------------------------------------

def _next_weekday(today: date, weekday: int) -> date:
    return today + timedelta(days=(weekday - today.weekday()) % 7)


def probe_requests(today: date) -> Dict[str, Tuple[str, Dict[str, str]]]:
    """One cheap GET per REST class (the smallest range) against PROBE_INSTRUMENT. The websocket's authorize call is
    issued separately, then a bare connect/close."""
    key = quote(PROBE_INSTRUMENT, safe="")
    to_day = today - timedelta(days=1)
    from_day = to_day - timedelta(days=4)            # a few calendar days so a weekend still returns a bar
    expiry = _next_weekday(today, 1)                 # NIFTY weekly options expire on Tuesdays
    return {
        HISTORICAL: (f"/v2/historical-candle/{key}/day/{to_day.isoformat()}/{from_day.isoformat()}", {}),
        INTRADAY: (f"/v2/historical-candle/intraday/{key}/30minute", {}),
        QUOTES: ("/v2/market-quote/ltp", {"instrument_key": PROBE_INSTRUMENT}),
        OPTION_CHAIN: ("/v2/option/chain", {"instrument_key": PROBE_INSTRUMENT, "expiry_date": expiry.isoformat()}),
        WEBSOCKET: ("/v3/feed/market-data-feed/authorize", {}),
    }


def _error_code(response: httpx.Response) -> Optional[str]:
    try:
        body = response.json()
    except ValueError:
        return None
    errors = body.get("errors") if isinstance(body, dict) else None
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        code = errors[0].get("errorCode") or errors[0].get("error_code")
        return str(code)[:64] if code else None
    return None


def _body_ok(response: httpx.Response) -> bool:
    try:
        body = response.json()
    except ValueError:
        return False
    return not (isinstance(body, dict) and body.get("status") == "error")


def outcome(response: httpx.Response, now: datetime) -> Dict[str, Any]:
    """works: 2xx and not an error body. refused: 401/403 (the broker turned the token down for this endpoint).
    unverified: anything else (429, 5xx, a 400 about the probe's own parameters) - the call does not settle it."""
    status = response.status_code
    entry: Dict[str, Any] = {"http_status": status, "error_code": _error_code(response), "probed_at": now.isoformat()}
    if 200 <= status < 300 and _body_ok(response):
        entry["status"] = WORKS
    elif status in (401, 403):
        entry["status"] = REFUSED
    else:
        entry["status"] = UNVERIFIED
    return entry


def _failure(exc: BaseException, now: datetime) -> Dict[str, Any]:
    return {"status": UNVERIFIED, "http_status": None, "error_code": type(exc).__name__, "probed_at": now.isoformat()}


async def _default_ws_connect(url: str) -> Any:
    from websockets.asyncio.client import connect
    return await connect(url, open_timeout=PROBE_TIMEOUT_SECONDS, max_size=2 ** 20)


def _client() -> httpx.AsyncClient:
    """The probe's HTTP client (tests replace this with a mocked transport)."""
    return httpx.AsyncClient(base_url=API_ROOT, timeout=PROBE_TIMEOUT_SECONDS)


WsConnector = Callable[[str], Awaitable[Any]]


async def _probe_ws(client: httpx.AsyncClient, headers: Dict[str, str], path: str, now: datetime,
                    connector: Optional[WsConnector]) -> Dict[str, Any]:
    response = await client.get(path, headers=headers)
    entry = outcome(response, now)
    if entry["status"] != WORKS:
        return entry
    try:
        data = (response.json() or {}).get("data") or {}
        url = data.get("authorized_redirect_uri") or data.get("authorizedRedirectUri")
    except (ValueError, AttributeError):
        url = None
    if not url:
        return {**entry, "status": UNVERIFIED, "error_code": "no_feed_url"}
    try:
        conn = await asyncio.wait_for((connector or _default_ws_connect)(url), PROBE_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 - classified, never raised; the URL (one-time code) is never logged
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (401, 403):
            return {**entry, "status": REFUSED, "http_status": status, "error_code": "websocket_handshake"}
        return {**entry, "status": UNVERIFIED, "error_code": f"websocket_{type(exc).__name__}"}
    try:
        await conn.close()
    except Exception:  # noqa: BLE001 - the handshake is what was being checked
        pass
    return entry


async def probe(token: str, *, classes: Optional[Iterable[str]] = None, client: Optional[httpx.AsyncClient] = None,
                ws_connector: Optional[WsConnector] = None, now: Optional[datetime] = None) -> Dict[str, Dict[str, Any]]:
    """Read-only coverage probe: GET requests only (plus the websocket handshake, then close). Never places, modifies
    or cancels anything, never logs or returns the token. Returns {class: {status, http_status, error_code, probed_at}}."""
    from app.market_data.calendar import IST
    now = now or _utcnow()
    wanted = [c for c in (classes or CLASSES) if c in CLASSES]
    requests = probe_requests(now.astimezone(IST).date())
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    own = client is None
    http = client or _client()

    async def one(cls: str) -> Tuple[str, Dict[str, Any]]:
        path, params = requests[cls]
        try:
            if cls == WEBSOCKET:
                return cls, await _probe_ws(http, headers, path, now, ws_connector)
            return cls, outcome(await http.get(path, params=params or None, headers=headers), now)
        except Exception as exc:  # noqa: BLE001 - a network failure leaves the class unverified
            return cls, _failure(exc, now)

    try:
        results = dict(await asyncio.gather(*(one(c) for c in wanted)))
    finally:
        if own:
            await http.aclose()
    logger.info("Upstox analytics token probe: %s", {c: r["status"] for c, r in results.items()})
    return results


def apply_probe(credentials: BrokerCredentials, results: Dict[str, Dict[str, Any]], now: Optional[datetime] = None) -> None:
    now = now or _utcnow()
    meta = dict(credentials.analytics_meta or new_meta(credentials.analytics_token or "", now))
    coverage = dict(meta.get("coverage") or {})
    coverage.update(results)
    meta["coverage"] = {c: coverage.get(c) or {"status": UNVERIFIED} for c in CLASSES}
    meta["probed_at"] = now.isoformat()
    credentials.analytics_meta = meta


async def refresh_coverage(session: AsyncSession, record: Any, *, classes: Optional[Iterable[str]] = None,
                           client: Optional[httpx.AsyncClient] = None, ws_connector: Optional[WsConnector] = None,
                           now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """Probe this credential row's analytics token and store the coverage map on it (re-encrypted). The write is
    conditional on the row being unchanged since it was read, so a concurrent save or login is never overwritten; then
    commits. Returns the public view, or None when the row has no (unexpired) analytics token."""
    from app.brokers.token_lifecycle import load_credentials
    from app.db.models import BrokerCredentialRecord
    from app.secrets_store.encryption import encrypt_text
    from app.secrets_store.envelope import PURPOSE_BROKER_CREDENTIAL

    if record.broker_name != BROKER:
        return None
    creds = load_credentials(record)
    now = now or _utcnow()
    if not creds.analytics_token or is_expired(creds, now):
        return public_view(creds, now) if creds.analytics_token else None
    expected_payload, record_id = record.encrypted_payload, record.id
    if session.in_transaction() and not (session.new or session.dirty or session.deleted):
        await session.commit()                        # hold no pooled connection across the broker round-trips
    results = await probe(creds.analytics_token, classes=classes, client=client, ws_connector=ws_connector, now=now)
    apply_probe(creds, results, now)
    payload = encrypt_text(creds.model_dump_json(), record.tenant_id, PURPOSE_BROKER_CREDENTIAL)
    result = await session.execute(update(BrokerCredentialRecord).where(
        BrokerCredentialRecord.id == record_id, BrokerCredentialRecord.encrypted_payload == expected_payload,
    ).values(encrypted_payload=payload).execution_options(synchronize_session=False))
    if int(getattr(result, "rowcount", 0) or 0) != 1:
        await session.rollback()
        logger.info("Upstox analytics probe result not stored: credential %s changed meanwhile", record_id)
        return public_view(creds, now)
    await session.commit()
    try:
        await session.refresh(record)
    except Exception:  # noqa: BLE001 - a detached record is fine; the row is written
        pass
    return public_view(creds, now)


# --- re-probe after a refusal seen in a real data call -------------------------------------------

_last_reprobe: Dict[Tuple[int, str], float] = {}
_tasks: Set[asyncio.Task] = set()


async def reprobe_class(record_id: int, tenant_id: int, cls: str, *, session_factory: Optional[Callable[[], Any]] = None,
                        client: Optional[httpx.AsyncClient] = None) -> Optional[Dict[str, Any]]:
    """Re-probe one class of one credential row in its own session. Tenant-scoped: the row must belong to `tenant_id`."""
    from app.db.models import BrokerCredentialRecord
    from app.db.session import _session_factory
    factory = session_factory or _session_factory
    async with factory() as session:
        record = await session.scalar(select(BrokerCredentialRecord).where(
            BrokerCredentialRecord.id == record_id, BrokerCredentialRecord.tenant_id == tenant_id))
        if record is None:
            return None
        return await refresh_coverage(session, record, classes=[cls], client=client)


def schedule_reprobe(record_id: int, tenant_id: int, cls: str) -> bool:
    """Fire-and-forget re-probe of one class after the broker refused the analytics token on a real data call; at most
    once per credential and class every REPROBE_COOLDOWN_SECONDS. False when skipped (cooldown, no running loop)."""
    key = (record_id, cls)
    mono = _time.monotonic()
    if mono - _last_reprobe.get(key, -1e12) < REPROBE_COOLDOWN_SECONDS:
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    _last_reprobe[key] = mono

    async def run() -> None:
        try:
            await reprobe_class(record_id, tenant_id, cls)
        except Exception as exc:  # noqa: BLE001 - background bookkeeping never surfaces
            logger.warning("Upstox analytics re-probe failed for credential %s/%s: %s", record_id, cls, type(exc).__name__)

    task = loop.create_task(run())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return True


def attach_refusal_hook(adapter: Any, record: Any) -> None:
    """Called by token_lifecycle.build_adapter: a refusal seen by this tenant's adapter re-probes this tenant's row."""
    if getattr(adapter, "name", "") != BROKER or getattr(record, "id", None) is None:
        return
    record_id, tenant_id = record.id, record.tenant_id
    adapter.on_analytics_refusal = lambda cls: schedule_reprobe(record_id, tenant_id, cls)


# --- the expiry warning -------------------------------------------------------------------------

TITLE_PREFIX = "Upstox analytics token"


def _warning_for(label: str, expiry: datetime, estimated: bool, today: date) -> Optional[Tuple[str, str]]:
    from app.market_data.calendar import IST
    exp_day = expiry.astimezone(IST).date()
    days_left = (exp_day - today).days
    if days_left < 0 or days_left > WARN_DAYS:
        return None
    note = " (estimated from the save date)" if estimated else ""
    how = ("Generate a new one in Upstox Developer Apps → Analytics and save it under Settings > Brokers > Upstox. "
           "After it expires, charts, the scanner and the option chain use the daily Upstox login again.")
    if days_left == 0:
        return f"{TITLE_PREFIX} ({label}) expires today ({exp_day.isoformat()})", f"Your Upstox analytics token expires today{note}. {how}"
    return (f"{TITLE_PREFIX} ({label}) expires on {exp_day.isoformat()}",
            f"Your Upstox analytics token expires in {days_left} day{'s' if days_left != 1 else ''}, on {exp_day.isoformat()}{note}. {how}")


async def send_expiry_warnings(session: AsyncSession, now: Optional[datetime] = None) -> int:
    """One WARNING to the credential's holder when the analytics token is within WARN_DAYS of its expiry, and one more
    on the last day. Deduped on the notification title (it names the account and the expiry date), so a worker restart
    or a second run the same day sends nothing new, and a replaced token with a new expiry warns afresh."""
    from app.core.enums import NotificationSeverity, NotificationType
    from app.db.models import BrokerCredentialRecord, NotificationRecord
    from app.market_data.calendar import IST
    from app.notifications.service import notify

    now = now or _utcnow()
    today = now.astimezone(IST).date()
    sent = 0
    records = list(await session.scalars(select(BrokerCredentialRecord).where(BrokerCredentialRecord.broker_name == BROKER)))
    for record in records:
        creds = _credentials_of(record)
        exp = expires_at(creds) if creds is not None and creds.analytics_token else None
        if creds is None or exp is None:
            continue
        warning = _warning_for(record.account_label or "primary", exp, bool((creds.analytics_meta or {}).get("expiry_estimated")), today)
        if warning is None:
            continue
        title, message = warning
        seen = await session.scalar(select(NotificationRecord.id).where(
            NotificationRecord.tenant_id == record.tenant_id, NotificationRecord.user_id == record.user_id,
            NotificationRecord.event_type == NotificationType.TOKEN_EXPIRED.value, NotificationRecord.title == title).limit(1))
        if seen is not None:
            continue
        await notify(session, record.tenant_id, NotificationType.TOKEN_EXPIRED, title, message,
                     severity=NotificationSeverity.WARNING, user_id=record.user_id,
                     metadata_json=json.dumps({"kind": "upstox_analytics_token_expiry", "account_label": record.account_label,
                                               "expires_at": exp.isoformat()}))
        sent += 1
    return sent
