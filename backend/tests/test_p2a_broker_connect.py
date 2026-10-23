"""P2a: simple broker connect - the catalogue, connections, connect / renew, analytics-token detection, plain sentences,
tenancy, the trader role, and no secret in any response or log line.

Every broker call goes to a fake (httpx's transport is replaced for the module), so nothing leaves the process.
"""
import asyncio
import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List

import httpx
import jwt
import pytest
from sqlalchemy import select

from app.brokers import catalog as cat
from app.brokers import upstox_analytics as ua
from app.brokers.models import BrokerCredentials
from app.brokers.registry import available_brokers
from app.brokers.token_lifecycle import default_token_expiry, load_credentials
from app.db.models import BrokerCredentialRecord
from tests.test_auth_api import _register, _session_factory, client

# Distinctive fake values, built at run time so no credential-shaped literal sits in the source: any 5-character run of
# one of them in a response or a log is a leak. Letters only from a set that never spells hex or English runs, and all
# valid base32 (the TOTP one is turned into codes).
_LETTERS = str.maketrans("0123456789abcdef", "GHJKMNPQRSTVWXYZ")


def _sentinel(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()[:24].translate(_LETTERS)


DHAN_A = _sentinel("dhan-a")
DHAN_B = _sentinel("dhan-b")
DHAN_X = _sentinel("dhan-x")
KITE_APP = "kiteapp" + "q" * 3
KITE_S = _sentinel("kite-s")
KITE_CODE = _sentinel("kite-code")
KITE_SESSION = _sentinel("kite-session")
ANGEL_PIN = "4" * 4
ANGEL_SEED = _sentinel("angel-seed")
ANGEL_SESSION = _sentinel("angel-session")
OPAQUE_ANALYTICS = _sentinel("opaque-analytics")
OPAQUE_BAD = _sentinel("opaque-bad")
SHOONYA_PASS = "pass phrase " + "w" * 3
SHOONYA_SEED = _sentinel("shoonya-seed")
SHOONYA_SESSION = _sentinel("shoonya-session")
KOTAK_KEY = _sentinel("kotak-consumer")
KOTAK_SEED = _sentinel("kotak-seed")
KOTAK_TRADE = _sentinel("kotak-trade")
KOTAK_MPIN = "7" * 6
NOW = datetime.now(timezone.utc)


def _upstox_jwt(lifetime: timedelta, *, issued: datetime = NOW) -> str:
    return jwt.encode({"sub": "U1", "iat": int(issued.timestamp()), "exp": int((issued + lifetime).timestamp()),
                       "nonce": uuid.uuid4().hex}, "upstox-side-signing-key-for-tests-only", algorithm="HS256")


ANALYTICS_JWT = _upstox_jwt(timedelta(days=365))
DAILY_JWT = _upstox_jwt(timedelta(hours=20))

# Upstox's view of each token: what the fake answers for it.
UPSTOX_TOKENS: Dict[str, str] = {}
DOWN_HOSTS: set = set()
DHAN_GOOD: set = set()
SHOONYA_CALLS: List[str] = []


def _reply(status: int, body: dict) -> httpx.Response:
    return httpx.Response(status, json=body)


async def _fake_transport(self, request: httpx.Request) -> httpx.Response:
    host, path = request.url.host, request.url.path
    if host in DOWN_HOSTS:
        raise httpx.ConnectError("unreachable", request=request)
    if host == "api.upstox.com":
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        kind = UPSTOX_TOKENS.get(token, "bad")
        if path == "/v2/user/profile":
            if kind == "daily":
                return _reply(200, {"status": "success", "data": {"user_id": "U1", "user_name": "Abhi"}})
            return _reply(401, {"status": "error", "errors": [{"errorCode": "UDAPI100050", "message": "Invalid token"}]})
        cls = ua.classify(request.method, request.url)
        if cls is not None and kind in ("analytics", "daily"):
            data = {"authorized_redirect_uri": "wss://feed.upstox.com/v3/feed?code=x"} if cls == ua.WEBSOCKET else {}
            return _reply(200, {"status": "success", "data": data})
        return _reply(401, {"status": "error", "errors": [{"errorCode": "UDAPI100050", "message": "Invalid token"}]})
    if host == "api.dhan.co" and path == "/v2/profile":
        if request.headers.get("access-token") in (DHAN_A, DHAN_B, *DHAN_GOOD):
            return _reply(200, {"dhanClientId": "1000001", "name": "Abhi"})
        # A broker that echoes what it was sent: the audit row must not keep it.
        return _reply(401, {"errorCode": "DH-901", "errorMessage": "Invalid access token " + request.headers.get("access-token", "")})
    if host == "api.kite.trade":
        if path == "/session/token":
            if f"request_token={KITE_CODE}" in request.content.decode():
                return _reply(200, {"status": "success", "data": {"access_token": KITE_SESSION}})
            return _reply(403, {"status": "error", "message": "Token is invalid or has expired.", "error_type": "TokenException"})
        if path == "/user/profile" and request.headers.get("Authorization") == f"token {KITE_APP}:{KITE_SESSION}":
            return _reply(200, {"status": "success", "data": {"user_id": "AB1234"}})
        return _reply(403, {"status": "error", "message": "Incorrect api_key or access_token.", "error_type": "TokenException"})
    if host == "apiconnect.angelone.in":
        if path.endswith("/loginByPassword"):
            sent = json.loads(request.content)
            if sent.get("password") == ANGEL_PIN and len(sent.get("totp", "")) == 6:
                return _reply(200, {"status": True, "data": {"jwtToken": ANGEL_SESSION, "refreshToken": "r", "feedToken": "f"}})
            return _reply(200, {"status": False, "message": "Invalid totp", "errorcode": "AB1050"})
        if path.endswith("/getProfile") and request.headers.get("Authorization") == f"Bearer {ANGEL_SESSION}":
            return _reply(200, {"status": True, "data": {"clientcode": "A1", "name": "Abhi"}})
    if host == "api.shoonya.com":
        from urllib.parse import parse_qs
        form = parse_qs(request.content.decode())
        SHOONYA_CALLS.append(path.rsplit("/", 1)[-1])
        if path.endswith("/QuickAuth"):
            sent = json.loads(form["jData"][0])
            if len(sent.get("factor2", "")) == 6 and sent.get("uid") == "FA1":
                return _reply(200, {"stat": "Ok", "susertoken": SHOONYA_SESSION, "actid": "FA1", "uname": "Abhi"})
            return _reply(200, {"stat": "Not_Ok", "emsg": "Invalid Input : Wrong Password"})
        if form.get("jKey", [""])[0] == SHOONYA_SESSION:
            return _reply(200, {"stat": "Ok", "cash": "1000"})
        return _reply(200, {"stat": "Not_Ok", "emsg": "Session Expired :  Invalid Session Key"})
    if host == "mis.kotaksecurities.com":
        if request.headers.get("Authorization") != KOTAK_KEY:
            return _reply(401, {"message": "Invalid consumer key"})
        if path == "/login/1.0/tradeApiLogin":
            sent = json.loads(request.content)
            if len(sent.get("totp", "")) == 6 and sent.get("ucc") == "KUCC1":
                return _reply(200, {"data": {"token": "view" + "x" * 8, "sid": "view-sid", "ucc": "KUCC1"}})
            return _reply(200, {"stat": "Not_Ok", "stCode": 1001, "errMsg": "Invalid TOTP"})
        if path == "/login/1.0/tradeApiValidate":
            if json.loads(request.content).get("mpin") == KOTAK_MPIN:
                return _reply(200, {"data": {"token": KOTAK_TRADE, "sid": "trade-sid", "baseUrl": "https://e21.kotaksecurities.com",
                                             "ucc": "KUCC1", "greetingName": "Abhi"}})
            return _reply(200, {"stat": "Not_Ok", "stCode": 1002, "errMsg": "Invalid MPIN"})
    return _reply(404, {"status": "error"})


class _FakeWs:
    async def close(self):
        return None


@pytest.fixture(autouse=True)
def fake_brokers(monkeypatch):
    from app.core import rate_limit
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _fake_transport)

    async def fake_ws(url):
        return _FakeWs()
    monkeypatch.setattr(ua, "_default_ws_connect", fake_ws)
    UPSTOX_TOKENS.clear()
    UPSTOX_TOKENS.update({ANALYTICS_JWT: "analytics", DAILY_JWT: "daily", OPAQUE_ANALYTICS: "analytics"})
    DOWN_HOSTS.clear()
    DHAN_GOOD.clear()
    SHOONYA_CALLS.clear()
    rate_limit.reset("broker_connect")
    yield
    rate_limit.reset("broker_connect")


SECRETS = (DHAN_A, DHAN_B, DHAN_X, KITE_S, KITE_CODE, KITE_SESSION, ANGEL_SEED, ANGEL_SESSION, OPAQUE_ANALYTICS,
           OPAQUE_BAD, SHOONYA_SEED, SHOONYA_SESSION, KOTAK_KEY, KOTAK_SEED, KOTAK_TRADE)


# Fernet ciphertext (the stored, encrypted credential, e.g. in SQLAlchemy's echo of an INSERT) is random base64: a
# 5-character run of a secret can appear in it by chance, and it reveals nothing. Plaintext stays fully checked.
_FERNET = re.compile(r"gAAAAA[A-Za-z0-9_=-]{40,}")


def _assert_no_secret(text: str) -> None:
    """No secret, nor any run of 5 or more of its characters (a log may name at most a 4-character suffix)."""
    text = _FERNET.sub("<ciphertext>", text)
    for secret in SECRETS:
        for i in range(len(secret) - 4):
            assert secret[i:i + 5] not in text, f"part of a secret leaked: ...{secret[i:i + 5]}..."
    for token in (ANALYTICS_JWT, DAILY_JWT):
        signature = token.rsplit(".", 1)[1]
        assert token not in text
        for i in range(len(signature) - 4):
            assert signature[i:i + 5] not in text, "part of an Upstox token leaked"


def _assert_plain(message: str) -> None:
    """ONE plain sentence: no codes, no developer text, no line breaks."""
    assert message == "Connected." or (message.endswith(".") and ". " not in message and "\n" not in message), message
    assert not re.search(r"[A-Z]{2,}-?\d|\d{3}|_|[{}\[\]<>]|\b(error|exception|http|none|null|status)\b", message, re.I), message
    assert len(message) < 220, message


def _org(prefix: str) -> Dict[str, str]:
    bearer = _register(f"p2a-{prefix}-{uuid.uuid4().hex[:8]}@example.com")
    return {"Authorization": f"Bearer {bearer}"}


def _tenant(headers) -> int:
    return client.get("/api/auth/me", headers=headers).json()["tenant_id"]


def _connect(headers, broker: str, fields: dict, renew: bool = False, label=None) -> dict:
    from app.core import rate_limit
    rate_limit.reset("broker_connect")          # the limit has its own test; flows here make more than six calls
    body: dict = {"fields": fields}
    if label is not None:
        body["account_label"] = label
    response = client.post(f"/api/broker/{broker}/{'renew' if renew else 'connect'}", headers=headers, json=body)
    assert response.status_code == 200, response.text
    out = response.json()
    assert set(out) == {"status", "message"}
    _assert_plain(out["message"])
    _assert_no_secret(response.text)
    return out


def _connections(headers) -> List[dict]:
    response = client.get("/api/broker/connections", headers=headers)
    assert response.status_code == 200, response.text
    _assert_no_secret(response.text)
    for row in response.json():
        assert set(row) == {"broker", "account_label", "name", "status", "message", "data_only", "renew"}
        assert row["status"] in ("connected", "needs_login", "not_working")
        _assert_plain(row["message"])
    return response.json()


def _row(tenant_id: int, broker: str) -> BrokerCredentialRecord:
    async def go():
        async with _session_factory() as session:
            return await session.scalar(select(BrokerCredentialRecord).where(
                BrokerCredentialRecord.tenant_id == tenant_id, BrokerCredentialRecord.broker_name == broker))
    return asyncio.run(go())


# --- the catalogue ---------------------------------------------------------------------------------------

# What each method needs, from the adapters' own requirements - the catalogue may ask for nothing else.
NEEDED = {
    "upstox": {"analytics_token"},
    "dhan": {"client_id", "access_token"},
    "zerodha": {"api_key", "api_secret"},
    "fyers": {"api_key", "api_secret", "redirect_uri"},
    "angel_one": {"api_key", "client_id", "pin", "totp_secret"},
    "shoonya": {"client_id", "api_secret", "api_key", "totp_secret"},
    "coindcx": {"api_key", "api_secret"},
    "kotak_neo": {"api_key", "mobile_number", "client_id", "pin", "totp_secret"},
}
# Brokers whose official method cannot be done in one or two fields (docs/brokers/EASIEST_LOGIN.md says why).
MORE_THAN_TWO = {"fyers": 3, "angel_one": 4, "shoonya": 4, "kotak_neo": 5}
# Part 1B: listed only with ui_v2 on for the organisation.
UI_V2_ONLY = {"kotak_neo"}


def test_catalog_shape_fields_only_what_the_method_needs():
    headers = _org("catalog")
    assert client.get("/api/broker/catalog").status_code == 401
    response = client.get("/api/broker/catalog", headers=headers)
    assert response.status_code == 200
    rows = response.json()
    assert {r["broker"] for r in rows} == set(available_brokers()) - UI_V2_ONLY      # ui_v2 off: no Kotak Neo tile
    rows = cat.catalog()                                                             # ui_v2 on: every broker
    assert {r["broker"] for r in rows} == set(available_brokers())
    for row in rows:
        assert set(row) == {"broker", "name", "method", "fields", "how_to", "doc_url", "daily_renewal", "available", "renew_fields",
                            "gates", "requires_ui_v2", "guide"}
        assert row["requires_ui_v2"] is (row["broker"] in UI_V2_ONLY)
        assert row["method"] in ("analytics_token", "access_token", "login_code", "totp_auto", "api_key_secret")
        keys = [f["key"] for f in row["fields"]]
        assert set(keys) == NEEDED[row["broker"]] and len(keys) == len(set(keys))
        assert 1 <= len(keys) <= MORE_THAN_TWO.get(row["broker"], 2)
        for f in row["fields"] + row["renew_fields"]:
            assert set(f) <= {"key", "label", "kind", "placeholder"} and f["kind"] in ("secret", "text")
            assert f["key"] in BrokerCredentials.model_fields
        assert len(row["how_to"]) == 3 and all(s.endswith(".") and "_" not in s for s in row["how_to"])
        assert row["doc_url"].startswith("https://") and row["available"] is True
        assert row["daily_renewal"] is (row["broker"] not in ("upstox", "coindcx"))
        if not row["daily_renewal"]:
            assert row["renew_fields"] == []
        # Secrets are always masked; only identifiers and URLs are plain text.
        for f in row["fields"]:
            if f["key"] in ("access_token", "analytics_token", "api_secret", "pin", "totp_secret"):
                assert f["kind"] == "secret"
    upstox = next(r for r in rows if r["broker"] == "upstox")
    assert upstox["method"] == "analytics_token" and [f["key"] for f in upstox["fields"]] == ["analytics_token"]
    assert cat.entry("UPSTOX") is cat.entry("upstox")


# --- analytics-token detection -----------------------------------------------------------------------------

def test_token_kind_from_claims():
    assert ua.token_kind_from_claims(ANALYTICS_JWT) == ua.ANALYTICS_KIND
    assert ua.token_kind_from_claims(DAILY_JWT) == ua.DAILY_KIND
    # Issued months ago, a year long: still analytics (lifetime, not time left, decides).
    old = _upstox_jwt(timedelta(days=365), issued=NOW - timedelta(days=300))
    assert ua.token_kind_from_claims(old) == ua.ANALYTICS_KIND
    assert ua.token_kind_from_claims(_upstox_jwt(timedelta(hours=1), issued=NOW - timedelta(hours=3))) == ua.EXPIRED_KIND
    assert ua.token_kind_from_claims(_upstox_jwt(timedelta(days=4))) is None          # between the bounds: probe
    assert ua.token_kind_from_claims("not-a-jwt") is None
    assert ua.token_kind_from_claims(jwt.encode({"sub": "U1"}, "k" * 32, algorithm="HS256")) is None
    # No `iat`: the time left says nothing (a year-long token can have two days left) - the probe decides.
    no_iat = jwt.encode({"sub": "U1", "exp": int((NOW + timedelta(days=300)).timestamp())}, "k" * 32, algorithm="HS256")
    assert ua.token_kind_from_claims(no_iat) is None
    short_no_iat = jwt.encode({"sub": "U1", "exp": int((NOW + timedelta(hours=5)).timestamp())}, "k" * 32, algorithm="HS256")
    assert ua.token_kind_from_claims(short_no_iat) is None


def test_probe_token_kind_settles_an_opaque_token():
    UPSTOX_TOKENS[OPAQUE_BAD] = "bad"
    UPSTOX_TOKENS["opaque-daily"] = "daily"
    assert asyncio.run(ua.probe_token_kind(OPAQUE_ANALYTICS)) == ua.ANALYTICS_KIND
    assert asyncio.run(ua.probe_token_kind("opaque-daily")) == ua.EITHER_KIND      # both reads answer: the box decides
    assert asyncio.run(ua.probe_token_kind(OPAQUE_BAD)) == ua.REJECTED_KIND
    DOWN_HOSTS.add("api.upstox.com")
    assert asyncio.run(ua.probe_token_kind(OPAQUE_ANALYTICS)) is None


@pytest.mark.parametrize("token", [ANALYTICS_JWT, OPAQUE_ANALYTICS])
def test_analytics_token_pasted_in_the_daily_box_is_stored_as_the_analytics_token(token, caplog):
    caplog.set_level(logging.DEBUG)
    headers = _org("detect")
    out = _connect(headers, "upstox", {"access_token": token})
    assert out == {"status": "connected", "message": "Connected."}
    creds = load_credentials(_row(_tenant(headers), "upstox"))
    assert creds.analytics_token == token and creds.access_token is None
    assert creds.analytics_meta["coverage"][ua.HISTORICAL]["status"] == ua.WORKS
    _assert_no_secret(caplog.text)


def test_daily_token_pasted_in_the_analytics_box_is_stored_as_the_daily_token():
    headers = _org("daily")
    out = _connect(headers, "upstox", {"analytics_token": DAILY_JWT})
    assert out["status"] == "connected" and "today only" in out["message"]
    row = _row(_tenant(headers), "upstox")
    creds = load_credentials(row)
    assert creds.access_token == DAILY_JWT and creds.analytics_token is None and row.token_status == "VALID"
    [conn] = _connections(headers)
    assert conn["status"] == "connected" and conn["data_only"] is False


# --- analytics only = connected for data ------------------------------------------------------------------------

def test_analytics_only_is_connected_data_only_and_never_needs_login(caplog):
    caplog.set_level(logging.DEBUG)
    headers = _org("ao")
    out = _connect(headers, "upstox", {"analytics_token": ANALYTICS_JWT})
    assert out == {"status": "connected", "message": "Connected."}
    [conn] = _connections(headers)
    assert conn["broker"] == "upstox" and conn["name"] == "Upstox" and conn["account_label"] == "primary"
    assert conn["status"] == "connected" and conn["data_only"] is True and conn["renew"] is False
    assert "paper trading" in conn["message"]
    row = _row(_tenant(headers), "upstox")
    assert row.token_status != "VALID"                       # no daily session, and still connected for data
    _assert_no_secret(caplog.text)


def test_refused_or_expired_analytics_tokens_say_what_to_do():
    headers = _org("refused")
    UPSTOX_TOKENS[ANALYTICS_JWT] = "bad"                    # Upstox turns the (one-year) token down everywhere
    out = _connect(headers, "upstox", {"analytics_token": ANALYTICS_JWT})
    assert out["status"] == "not_working" and "generate a new one" in out["message"]
    [conn] = _connections(headers)
    assert conn["status"] == "not_working" and conn["renew"] is False

    expired = _upstox_jwt(timedelta(days=365), issued=NOW - timedelta(days=400))
    out = _connect(_org("expired"), "upstox", {"analytics_token": expired})
    assert out["status"] == "not_working" and "expired" in out["message"]

    UPSTOX_TOKENS[OPAQUE_BAD] = "bad"
    out = _connect(_org("bad"), "upstox", {"access_token": OPAQUE_BAD})
    assert out["status"] == "not_working" and "did not accept" in out["message"]


def test_upstox_unreachable_is_a_plain_sentence_and_nothing_is_stored():
    headers = _org("down")
    DOWN_HOSTS.add("api.upstox.com")
    out = _connect(headers, "upstox", {"analytics_token": OPAQUE_ANALYTICS})
    assert out["status"] == "not_working" and "try again" in out["message"]
    assert _row(_tenant(headers), "upstox") is None


def test_input_mistakes_are_plain_sentences():
    headers = _org("input")
    for broker, fields in [("upstox", {}), ("upstox", {"analytics_token": "a b"}),
                           ("upstox", {"analytics_token": ANALYTICS_JWT, "access_token": DAILY_JWT}),
                           ("upstox", {"api_key": "k"}), ("dhan", {"access_token": DHAN_A}),
                           ("dhan", {"client_id": "1", "access_token": DHAN_A, "api_secret": "x"}),
                           ("dhan", {"client_id": "1", "access_token": 5}),
                           ("coindcx", {"api_key": "k", "api_secret": "x" * 5000})]:
        out = _connect(headers, broker, fields)
        assert out["status"] == "not_working", (broker, fields, out)
    assert _connections(headers) == []
    bad_label = _connect(headers, "dhan", {"client_id": "1", "access_token": DHAN_A}, label="no spaces allowed")
    assert bad_label["status"] == "not_working"
    unknown = client.post("/api/broker/nope/connect", headers=headers, json={"fields": {}})
    assert unknown.status_code == 404


# --- other brokers -----------------------------------------------------------------------------------------

def test_dhan_connect_renew_and_failures(caplog):
    caplog.set_level(logging.DEBUG)
    headers = _org("dhan")
    tenant_id = _tenant(headers)
    out = _connect(headers, "dhan", {"client_id": "1000001", "access_token": DHAN_X})
    assert out["status"] == "not_working" and out["message"] == "Dhan did not accept this token; generate a new one and paste it here."
    out = _connect(headers, "dhan", {"client_id": "1000001", "access_token": DHAN_A})
    assert out == {"status": "connected", "message": "Connected."}
    [conn] = _connections(headers)
    assert conn["status"] == "connected" and conn["data_only"] is False and conn["renew"] is False

    async def expire():
        async with _session_factory() as session:
            row = await session.scalar(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == tenant_id))
            row.token_expires_at = NOW - timedelta(minutes=1)
            await session.commit()
    asyncio.run(expire())
    [conn] = _connections(headers)
    assert conn["status"] == "needs_login" and conn["renew"] is True and "paste today's token" in conn["message"]

    # Renew takes only today's token; the client ID stays stored.
    assert _connect(headers, "dhan", {"client_id": "1", "access_token": DHAN_B}, renew=True)["status"] == "not_working"
    out = _connect(headers, "dhan", {"access_token": DHAN_B}, renew=True)
    assert out == {"status": "connected", "message": "Connected."}
    creds = load_credentials(_row(tenant_id, "dhan"))
    assert creds.access_token == DHAN_B and creds.client_id == "1000001"

    DOWN_HOSTS.add("api.dhan.co")
    out = _connect(headers, "dhan", {"access_token": DHAN_A}, renew=True)
    assert out["status"] == "not_working" and out["message"].startswith("Could not reach Dhan")
    _assert_no_secret(caplog.text)


def test_zerodha_connect_then_daily_login_address(caplog):
    caplog.set_level(logging.DEBUG)
    headers = _org("kite")
    tenant_id = _tenant(headers)
    assert _connect(headers, "zerodha", {"request_token": KITE_CODE}, renew=True)["message"] == "Connect Zerodha (Kite) first, then log in."
    out = _connect(headers, "zerodha", {"api_key": KITE_APP, "api_secret": KITE_S})
    assert out["status"] == "needs_login" and "press Log in" in out["message"]
    [conn] = _connections(headers)
    assert conn["status"] == "needs_login" and conn["renew"] is True

    out = _connect(headers, "zerodha", {"request_token": "https://example.com/cb?status=success&request_token=WRONGCODE"}, renew=True)
    assert out["status"] == "not_working" and "did not accept that login" in out["message"]
    out = _connect(headers, "zerodha", {"request_token": f"https://example.com/cb?action=login&status=success&request_token={KITE_CODE}"}, renew=True)
    assert out == {"status": "connected", "message": "Connected."}
    row = _row(tenant_id, "zerodha")
    creds = load_credentials(row)
    assert row.token_status == "VALID" and creds.access_token == KITE_SESSION and creds.request_token is None
    assert creds.api_secret == KITE_S
    _assert_no_secret(caplog.text)


def test_totp_login_renews_with_one_click():
    headers = _org("angel")
    fields = {"api_key": "smartkey", "client_id": "A1", "pin": ANGEL_PIN, "totp_secret": ANGEL_SEED}
    out = _connect(headers, "angel_one", {**fields, "pin": "0000"})
    assert out["status"] == "not_working" and "TOTP secret" in out["message"]
    assert _connect(headers, "angel_one", fields) == {"status": "connected", "message": "Connected."}
    assert _connect(headers, "angel_one", {}, renew=True) == {"status": "connected", "message": "Connected."}
    assert _connect(headers, "angel_one", {"pin": ANGEL_PIN}, renew=True)["status"] == "not_working"
    assert _connect(headers, "coindcx", {}, renew=True)["message"] == "CoinDCX needs no daily login; use Connect to change its details."


def test_kotak_neo_connects_renews_with_one_click_and_stays_practice_only(caplog):
    """Part 1B: the TOTP login through the connect flow; the session is stored encrypted as one string; LIVE orders and
    live prices stay off (app/brokers/gates.py) whatever the connection's state."""
    caplog.set_level(logging.DEBUG)
    from app.brokers import gates
    from app.brokers.kotak_neo import decode_session
    from app.brokers.token_lifecycle import build_adapter
    from app.core.enums import OrderSide
    headers = _org("kotak")
    fields = {"api_key": KOTAK_KEY, "mobile_number": "+91" + "9" * 10, "client_id": "KUCC1", "pin": KOTAK_MPIN, "totp_secret": KOTAK_SEED}
    out = _connect(headers, "kotak_neo", {**fields, "pin": "0000"})
    assert out["status"] == "not_working" and "TOTP secret" in out["message"]
    assert _connect(headers, "kotak_neo", fields) == {"status": "connected", "message": "Connected."}
    row = _row(_tenant(headers), "kotak_neo")
    session = decode_session(load_credentials(row).access_token)
    assert row.token_status == "VALID" and session["t"] == KOTAK_TRADE and session["b"] == "https://e21.kotaksecurities.com"
    assert _connect(headers, "kotak_neo", {}, renew=True) == {"status": "connected", "message": "Connected."}
    adapter = build_adapter(row)
    with pytest.raises(gates.LiveOrdersClosed):
        asyncio.run(adapter.exit_position("RELIANCE", "NSE", 1, OrderSide.BUY))
    with pytest.raises(gates.MarketDataClosed):
        asyncio.run(adapter.get_ltp_for_symbol("RELIANCE"))
    _assert_no_secret(caplog.text)


def test_the_kotak_neo_tile_is_listed_only_with_ui_v2_on():
    from tests.test_bt1b_guard_all_reads import _flags
    headers = _org("kotak-ui")
    assert "kotak_neo" not in {r["broker"] for r in client.get("/api/broker/catalog", headers=headers).json()}
    _flags(ui_v2=[_tenant(headers)])
    try:
        rows = {r["broker"]: r for r in client.get("/api/broker/catalog", headers=headers).json()}
    finally:
        _flags(ui_v2=[])
    kotak = rows["kotak_neo"]
    assert kotak["gates"]["live_orders"] == "closed" and kotak["gates"]["live_prices"] == "closed" and kotak["guide"]["links"]
    assert rows["upstox"]["gates"]["live_orders"] == "open" and rows["upstox"]["guide"] is None


# --- who may connect, tenancy, rate limit, removal ------------------------------------------------------------------

def test_trader_role_and_scope_are_required():
    from tests.test_z10c_tenancy_fixes import _member
    owner = _org("roles")
    tenant_id = _tenant(owner)
    viewer = _member(f"p2a-viewer-{uuid.uuid4().hex[:6]}@example.com", tenant_id, "VIEWER")
    denied = _member(f"p2a-denied-{uuid.uuid4().hex[:6]}@example.com", tenant_id, "USER", deny=("brokers:write",))
    for headers in (viewer, denied):
        for path in ("/api/broker/upstox/connect", "/api/broker/dhan/renew"):
            response = client.post(path, headers=headers, json={"fields": {"analytics_token": ANALYTICS_JWT}})
            assert response.status_code == 403, response.text
        assert client.get("/api/broker/catalog", headers=headers).status_code == 200      # any member reads the catalogue
        assert client.get("/api/broker/connections", headers=headers).status_code == 200
    assert client.post("/api/broker/upstox/connect", json={"fields": {}}).status_code == 401
    assert _row(tenant_id, "upstox") is None
    trader = _member(f"p2a-trader-{uuid.uuid4().hex[:6]}@example.com", tenant_id, "USER")
    assert _connect(trader, "upstox", {"analytics_token": ANALYTICS_JWT})["status"] == "connected"


def test_another_organisation_sees_and_touches_nothing():
    a, b = _org("ta"), _org("tb")
    _connect(a, "upstox", {"analytics_token": ANALYTICS_JWT})
    _connect(a, "dhan", {"client_id": "1000001", "access_token": DHAN_A})
    assert {c["broker"] for c in _connections(a)} == {"upstox", "dhan"}
    assert _connections(b) == []
    assert _connect(b, "dhan", {"access_token": DHAN_B}, renew=True)["message"] == "Connect Dhan first, then log in."
    _connect(b, "upstox", {"analytics_token": OPAQUE_ANALYTICS})
    assert [c["broker"] for c in _connections(b)] == ["upstox"]
    creds_a = load_credentials(_row(_tenant(a), "upstox"))
    assert creds_a.analytics_token == ANALYTICS_JWT                      # B's connect wrote only B's row
    assert load_credentials(_row(_tenant(a), "dhan")).access_token == DHAN_A


def test_connect_is_rate_limited_per_user():
    headers = _org("rate")
    codes = [client.post("/api/broker/dhan/connect", headers=headers, json={"fields": {}}).status_code for _ in range(7)]
    assert codes[:6] == [200] * 6 and codes[6] == 429


def test_removal_is_the_existing_delete_route():
    headers = _org("remove")
    _connect(headers, "upstox", {"analytics_token": ANALYTICS_JWT})
    assert client.delete("/api/broker/upstox/credentials", headers=headers).status_code == 204
    assert _connections(headers) == []


def test_bt1b_another_members_connection_is_not_stored_over_or_logged_in_to():
    """With the bring-your-own-broker tier on (test tenant only), a connection is its holder's: another member's connect
    without a full take-over and their renew are refused with a plain sentence, and the holder's row is unchanged."""
    from tests.test_bt1_byob_data_tier import _flag
    from tests.test_z10c_tenancy_fixes import _member
    holder = _org("holder")
    tenant_id = _tenant(holder)
    other = _member(f"p2a-other-{uuid.uuid4().hex[:6]}@example.com", tenant_id, "USER")
    _connect(holder, "upstox", {"analytics_token": ANALYTICS_JWT})
    _connect(holder, "dhan", {"client_id": "1000001", "access_token": DHAN_A})
    _flag(True, [tenant_id])
    try:
        for broker, fields, renew in [("upstox", {"analytics_token": OPAQUE_ANALYTICS}, False),
                                      ("dhan", {"client_id": "2", "access_token": DHAN_B}, False),
                                      ("dhan", {"access_token": DHAN_B}, True)]:
            out = _connect(other, broker, fields, renew=renew)
            assert out["status"] == "not_working" and "Another member" in out["message"], (broker, out)
    finally:
        _flag(False)
    assert load_credentials(_row(tenant_id, "upstox")).analytics_token == ANALYTICS_JWT
    assert load_credentials(_row(tenant_id, "dhan")).access_token == DHAN_A


# --- review fixes -----------------------------------------------------------------------------------------------

def test_an_opaque_token_both_reads_accept_is_stored_as_the_box_says():
    UPSTOX_TOKENS["opaque-both"] = "daily"                 # the profile and a quote both answer
    a, b = _org("box-a"), _org("box-b")
    assert _connect(a, "upstox", {"analytics_token": "opaque-both"})["status"] == "connected"
    creds = load_credentials(_row(_tenant(a), "upstox"))
    assert creds.analytics_token == "opaque-both" and creds.access_token is None
    assert _connect(b, "upstox", {"access_token": "opaque-both"})["status"] == "connected"
    creds = load_credentials(_row(_tenant(b), "upstox"))
    assert creds.access_token == "opaque-both" and creds.analytics_token is None


def test_shoonya_session_is_stored_and_reused_without_a_new_login(caplog):
    caplog.set_level(logging.DEBUG)
    from app.brokers.token_lifecycle import build_adapter
    headers = _org("shoonya")
    fields = {"client_id": "FA1", "api_secret": SHOONYA_PASS, "api_key": "vendorkey" + "z" * 3, "totp_secret": SHOONYA_SEED}
    assert _connect(headers, "shoonya", fields) == {"status": "connected", "message": "Connected."}
    row = _row(_tenant(headers), "shoonya")
    assert row.token_status == "VALID" and load_credentials(row).access_token == SHOONYA_SESSION
    assert SHOONYA_CALLS == ["QuickAuth"]

    adapter = build_adapter(row)
    assert adapter.access_token == SHOONYA_SESSION
    profile = asyncio.run(adapter.get_profile())             # the stored session, proven with one read - no new login
    assert profile.user_id == "FA1" and SHOONYA_CALLS == ["QuickAuth", "Limits"]
    assert _connect(headers, "shoonya", {}, renew=True) == {"status": "connected", "message": "Connected."}
    assert SHOONYA_CALLS[-1] == "QuickAuth"
    _assert_no_secret(caplog.text)


def test_a_login_address_without_the_code_never_replaces_the_session():
    headers = _org("nocode")
    tenant_id = _tenant(headers)
    _connect(headers, "zerodha", {"api_key": KITE_APP, "api_secret": KITE_S})
    good = f"https://example.com/cb?action=login&status=success&request_token={KITE_CODE}"
    assert _connect(headers, "zerodha", {"request_token": good}, renew=True)["status"] == "connected"
    for paste in ("https://example.com/cb?action=login&status=cancelled", "status=success", "https://example.com/cb"):
        out = _connect(headers, "zerodha", {"request_token": paste}, renew=True)
        assert out == {"status": "not_working", "message": "Paste the whole address you land on after logging in."}, paste
    row = _row(tenant_id, "zerodha")
    creds = load_credentials(row)
    assert row.token_status == "VALID" and creds.access_token == KITE_SESSION and creds.request_token is None


def test_dhan_session_ends_at_the_tokens_own_exp():
    headers = _org("dhanexp")
    schedule = default_token_expiry("dhan", NOW)
    assert schedule is not None
    exp = NOW + (schedule - NOW) / 2          # before the daily schedule at any time of day, so the token's own exp wins
    pasted = jwt.encode({"sub": "1000001", "exp": int(exp.timestamp())}, "dhan-side-signing-key-for-tests-only", algorithm="HS256")
    DHAN_GOOD.add(pasted)
    assert _connect(headers, "dhan", {"client_id": "1000001", "access_token": pasted})["status"] == "connected"
    row = _row(_tenant(headers), "dhan")
    stored = row.token_expires_at if row.token_expires_at.tzinfo else row.token_expires_at.replace(tzinfo=timezone.utc)
    assert stored == datetime.fromtimestamp(int(exp.timestamp()), tz=timezone.utc)
    # A token that outlives the daily schedule never lengthens it.
    later = jwt.encode({"sub": "1000001", "exp": int((NOW + timedelta(days=30)).timestamp())}, "dhan-side-signing-key-for-tests-only",
                       algorithm="HS256")
    DHAN_GOOD.add(later)
    assert _connect(headers, "dhan", {"access_token": later}, renew=True)["status"] == "connected"
    row = _row(_tenant(headers), "dhan")
    stored = row.token_expires_at if row.token_expires_at.tzinfo else row.token_expires_at.replace(tzinfo=timezone.utc)
    assert stored < NOW + timedelta(days=2)


def test_failed_login_audit_keeps_no_broker_text_or_secret():
    from app.brokers.routes import audit_error_text
    from app.db.models import AuditLogRecord
    headers = _org("audit")
    tenant_id = _tenant(headers)
    out = _connect(headers, "dhan", {"client_id": "1000001", "access_token": DHAN_X})
    assert out["status"] == "not_working"

    async def details():
        async with _session_factory() as session:
            return list(await session.scalars(select(AuditLogRecord.detail).where(
                AuditLogRecord.tenant_id == tenant_id, AuditLogRecord.event == "broker_authentication_failed")))
    [detail] = asyncio.run(details())
    assert detail.startswith("dhan/primary: BrokerAuthenticationError: ") and "[redacted]" in detail
    _assert_no_secret(detail)

    secretish = "sk_live_" + "Q" * 30
    text = audit_error_text(RuntimeError(f"upstream said: bad key {secretish} and pin 98765 " + "x " * 200),
                            BrokerCredentials(pin="98765"))
    assert secretish not in text and "98765" not in text and text.startswith("RuntimeError: ") and len(text) < 160


# --- re-review fixes ------------------------------------------------------------------------------------------------

def test_a_fyers_error_address_is_never_taken_for_the_login_code():
    from app.brokers.routes import _code_from_paste
    from app.brokers.token_lifecycle import store_access_token
    error_address = "https://example.com/fyers/cb?s=error&code=-413&message=Invalid%20request"
    assert _code_from_paste(error_address, "auth_code") == error_address          # no auth_code: no code at all
    assert _code_from_paste("https://example.com/cb?s=ok&code=200&auth_code=abcde", "auth_code") == "abcde"
    assert _code_from_paste("https://example.com/cb?code=xyz", "code") == "xyz"     # a broker whose parameter is `code`

    headers = _org("fyers")
    tenant_id = _tenant(headers)
    fields = {"api_key": "ABCD1234-100", "api_secret": _sentinel("fyers-s"), "redirect_uri": "https://example.com/fyers/cb"}
    assert _connect(headers, "fyers", fields)["status"] == "needs_login"
    working = _sentinel("fyers-session")

    async def give_a_working_session():
        async with _session_factory() as session:
            row = await session.scalar(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == tenant_id))
            store_access_token(row, working)
            await session.commit()
    asyncio.run(give_a_working_session())
    out = _connect(headers, "fyers", {"request_token": error_address}, renew=True)
    assert out == {"status": "not_working", "message": "Paste the whole address you land on after logging in."}
    row = _row(tenant_id, "fyers")
    creds = load_credentials(row)
    assert row.token_status == "VALID" and creds.access_token == working and creds.request_token is None


def test_expiry_cap_ignores_a_past_exp_and_never_lengthens():
    from app.brokers.token_lifecycle import cap_expiry_at_token_exp
    scheduled = NOW + timedelta(hours=10)

    def row() -> BrokerCredentialRecord:
        return BrokerCredentialRecord(tenant_id=1, user_id=1, broker_name="dhan", token_status="VALID", token_expires_at=scheduled)

    def token(exp: datetime) -> str:
        return jwt.encode({"sub": "1", "exp": int(exp.timestamp())}, "k" * 32, algorithm="HS256")
    past = row()
    cap_expiry_at_token_exp(past, token(NOW - timedelta(minutes=5)))      # the broker just accepted it: not believed
    assert past.token_expires_at == scheduled
    later = row()
    cap_expiry_at_token_exp(later, token(NOW + timedelta(days=3)))
    assert later.token_expires_at == scheduled
    sooner = row()
    cap_expiry_at_token_exp(sooner, token(NOW + timedelta(hours=2)))
    assert sooner.token_expires_at == datetime.fromtimestamp(int((NOW + timedelta(hours=2)).timestamp()), tz=timezone.utc)
    opaque = row()
    cap_expiry_at_token_exp(opaque, "not-a-jwt")
    assert opaque.token_expires_at == scheduled


def test_a_dead_stored_shoonya_session_is_marked_expired():
    from app.brokers.exceptions import BrokerAPIError
    from app.brokers.token_lifecycle import _is_auth_failure, build_adapter, verify_token
    headers = _org("shoonya-dead")
    tenant_id = _tenant(headers)
    fields = {"client_id": "FA1", "api_secret": SHOONYA_PASS, "api_key": "vendorkey" + "z" * 3, "totp_secret": SHOONYA_SEED}
    assert _connect(headers, "shoonya", fields)["status"] == "connected"

    async def kill_and_verify():
        async with _session_factory() as session:
            row = await session.scalar(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == tenant_id))
            from app.secrets_store.encryption import encrypt_text
            from app.secrets_store.envelope import PURPOSE_BROKER_CREDENTIAL
            creds = load_credentials(row)
            creds.access_token = _sentinel("shoonya-dead-session")      # a session Shoonya no longer knows
            row.encrypted_payload = encrypt_text(creds.model_dump_json(), tenant_id, PURPOSE_BROKER_CREDENTIAL)
            with pytest.raises(BrokerAPIError) as dead:                # HTTP 200, `stat: Not_Ok`, "Session Expired"
                await build_adapter(row).get_profile()
            assert _is_auth_failure(dead.value)
            ok, _ = await verify_token(session, row)
            await session.commit()
            return ok, row.token_status
    ok, token_status = asyncio.run(kill_and_verify())
    assert ok is False and token_status == "EXPIRED"
    [conn] = _connections(headers)
    assert conn["status"] == "needs_login" and conn["renew"] is True
