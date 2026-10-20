"""UI2-5: the broker field map, GET /api/broker/specs, server-side validation with the same map, the Zerodha (Kite) and
Fyers OAuth callbacks, and Shoonya's TOTP code.

- spec coverage: every broker in the registry has a spec; every field an adapter reads is in its spec; no spec field is
  unused; the map agrees with the login sets in token_lifecycle and with the registered callback routes;
- GET /api/broker/specs: login required, read-only, never a stored value, the Redirect URL from the setting;
- POST /api/broker/{name}/credentials: unknown fields, values ATP fills in itself, bad formats and missing required
  fields are refused in English without echoing a value; partial saves of a complete connection keep working;
- the callbacks: the real adapters' code exchange against a mocked broker HTTP transport, the session stored as the
  paste-the-code login stores it, and a state that works once, for the user who started it, within ten minutes;
- Shoonya: the stored base32 secret becomes the current 6-digit code (a made-up secret, generated at run time).

Every credential here is fake and built at run time (tests/broker_fakes.py); no network.
"""
import ast
import asyncio
import inspect
import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pyotp
import pytest
from sqlalchemy import select

from app.brokers import field_specs as fs
from app.brokers import routes as broker_routes
from app.brokers import token_lifecycle as tl
from app.brokers.fyers import API_URL as FYERS_API
from app.brokers.models import BrokerCredentials
from app.brokers.registry import BROKER_ADAPTERS, get_broker_adapter
from app.brokers.shoonya import ShoonyaBroker
from app.brokers.zerodha import ZerodhaBroker
from app.core import config as app_config
from app.db.models import AuditLogRecord, BrokerCredentialRecord
from app.main import app
from tests.route_inventory import iter_api_routes
from tests.broker_fakes import (ANGEL_CLIENT, ANGEL_KEY, ANGEL_PIN, COINDCX_KEY, DHAN_TOKEN, FYERS_APP_ID, FYERS_SECRET, KITE_KEY,
                                KITE_SECRET, TOTP_SECRET, UPSTOX_KEY, fake_credentials)
from tests.test_auth_api import _session_factory, client
from tests.test_bt1b_guard_all_reads import _byob, _flags, _member
from tests.test_phase_k_commercial import _owner

run = asyncio.run


def _email(tag: str) -> str:
    return f"ui25-{tag}-{uuid.uuid4().hex[:8]}@example.com"


def _fake(prefix: str) -> str:
    return f"fake-{prefix}-{uuid.uuid4().hex[:12]}"


@pytest.fixture(autouse=True)
def _reset_flags():
    yield
    _flags(byob_data_tier=[])


def _row(tenant_id: int, broker: str, label: str = "primary"):
    async def go():
        async with _session_factory() as session:
            rec = await session.scalar(select(BrokerCredentialRecord).where(
                BrokerCredentialRecord.tenant_id == tenant_id, BrokerCredentialRecord.broker_name == broker,
                BrokerCredentialRecord.account_label == label))
            return rec, (tl.load_credentials(rec) if rec else None)
    return run(go())


def _audit_text(tenant_id: int) -> str:
    async def go():
        async with _session_factory() as session:
            rows = await session.scalars(select(AuditLogRecord).where(AuditLogRecord.tenant_id == tenant_id))
            return " | ".join(f"{r.event} {r.detail}" for r in rows)
    return run(go())


def _store(headers, broker: str, body: dict, label: str = "primary"):
    return client.post(f"/api/broker/{broker}/credentials", params={"account_label": label}, headers=headers, json=body)


# --- spec coverage ---------------------------------------------------------------------------------------------------

def _adapter_reads(adapter_cls) -> set:
    """Every credential field the adapter's module reads: `<...>.credentials.<field>` and
    `getattr(<...>.credentials, "<field>", ...)`."""
    tree = ast.parse(inspect.getsource(inspect.getmodule(adapter_cls)))
    found = set()

    def is_credentials(node) -> bool:
        return (isinstance(node, ast.Attribute) and node.attr == "credentials") or (isinstance(node, ast.Name) and node.id == "credentials")

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and is_credentials(node.value):
            found.add(node.attr)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr" and len(node.args) >= 2
                and is_credentials(node.args[0]) and isinstance(node.args[1], ast.Constant)):
            found.add(node.args[1].value)
    return found - {"model_dump", "model_dump_json", "model_copy"}


def _login_route_reads() -> set:
    """Fields the platform's own login code reads for every broker (the redirect URL for the hosted login pages)."""
    found = set()
    for module in (broker_routes, tl):
        for node in ast.walk(ast.parse(inspect.getsource(module))):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "credentials":
                found.add(node.attr)
    return found


def test_every_registered_broker_has_a_spec_and_no_spec_without_an_adapter():
    assert set(fs.BROKER_FIELD_SPECS) == set(BROKER_ADAPTERS)
    for name, spec in fs.BROKER_FIELD_SPECS.items():
        assert spec.name == name and spec.display_name and spec.tile_line and spec.fields


def test_every_field_an_adapter_reads_is_in_its_spec():
    for name, adapter_cls in BROKER_ADAPTERS.items():
        reads = _adapter_reads(adapter_cls)
        assert reads, name
        missing = reads - set(fs.BROKER_FIELD_SPECS[name].keys) - fs.SERVER_WRITTEN_FIELDS
        assert not missing, f"{name} reads {sorted(missing)} but its field spec does not list them"


def test_no_spec_field_is_unused():
    login_reads = _login_route_reads()
    for name, spec in fs.BROKER_FIELD_SPECS.items():
        reads = _adapter_reads(BROKER_ADAPTERS[name])
        for field in spec.fields:
            used = field.key in reads or (field.entered_by == fs.BY_SERVER and field.key in login_reads)
            assert used, f"{name}.{field.key} is in the field map but nothing reads it"


def test_each_field_is_well_formed():
    known = set(BrokerCredentials.model_fields) | {"vc", "imei", "public_ip", "local_ip", "mac"}
    kinds = {fs.KIND_TEXT, fs.KIND_SECRET, fs.KIND_TOTP_SECRET, fs.KIND_PIN, fs.KIND_CODE}
    sources = {fs.BY_USER, fs.BY_OAUTH_CALLBACK, fs.BY_LOGIN, fs.BY_SERVER}
    for name, spec in fs.BROKER_FIELD_SPECS.items():
        assert len(set(spec.keys)) == len(spec.keys), name
        for field in spec.fields:
            where = f"{name}.{field.key}"
            assert field.key in known and field.key not in fs.SERVER_WRITTEN_FIELDS, where
            assert field.label and field.help and field.kind in kinds and field.entered_by in sources, where
            assert set(field.required_unless) <= set(spec.keys), where
            assert field.pattern and field.pattern.startswith("^") and field.pattern.endswith("$") and field.format_hint, where
            re.compile(field.pattern)
            # The browser reads the same pattern: no Python-only syntax.
            assert not re.search(r"\(\?P|\\[AZz]|\(\?[aiLmsux]", field.pattern), where
            # Tokens and codes are never something the new form asks a person to type, except Dhan's token (UI-7).
            if field.kind == fs.KIND_CODE or field.key == "access_token":
                assert field.entered_by != fs.BY_USER or name == "dhan", where
    assert fs.BROKER_FIELD_SPECS["dhan"].get("access_token").label == "Paste your Dhan access token"


def test_the_map_agrees_with_the_login_sets_and_the_callback_routes():
    specs = fs.BROKER_FIELD_SPECS
    assert {n for n, s in specs.items() if s.login_method == fs.LOGIN_API_KEY} == tl.PERMANENT_KEY_BROKERS
    oauth = {n for n, s in specs.items() if s.login_method == fs.LOGIN_OAUTH}
    assert oauth == tl.OAUTH_BROKERS | set(tl.LOGIN_URL_BROKERS) | set(tl.OAUTH_CALLBACK_BROKERS)
    assert {n for n, s in specs.items() if s.login_method == fs.LOGIN_TOTP} == {"angel_one", "shoonya"}
    routes = {(r.method, r.path) for r in iter_api_routes(app)}
    for name in oauth:
        assert ("GET", specs[name].oauth_start_path) in routes and ("GET", specs[name].oauth_callback_path) in routes, name
    for name, path in tl.OAUTH_CALLBACK_BROKERS.items():
        assert specs[name].oauth_callback_path == path
    assert specs["upstox"].oauth_callback_path == broker_routes.UPSTOX_CALLBACK_PATH
    for name in set(specs) - tl.PERMANENT_KEY_BROKERS:
        assert name in tl.TOKEN_DAILY_EXPIRY_IST, name


def test_the_spec_absorbs_the_upstox_analytics_token_entry():
    field = fs.BROKER_FIELD_SPECS["upstox"].get("analytics_token")
    assert field is not None and field.kind == fs.KIND_SECRET and not field.required and field.advanced
    assert "Analytics" in field.help


# --- GET /api/broker/specs -------------------------------------------------------------------------------------------

def test_specs_need_a_login():
    assert client.get("/api/broker/specs").status_code in (401, 403)


def test_specs_list_every_broker_without_any_stored_value(monkeypatch):
    headers, _me = _owner(_email("specs"))
    secret = KITE_SECRET
    assert _store(headers, "zerodha", {"api_key": KITE_KEY, "api_secret": secret}).status_code == 204
    monkeypatch.setattr(app_config, "BROKER_OAUTH_REDIRECT_BASE_URL", "")
    monkeypatch.setattr(app_config, "SERVER_EGRESS_IP", "203.0.113.10")
    res = client.get("/api/broker/specs", headers=headers)
    assert res.status_code == 200
    assert KITE_KEY not in res.text and secret not in res.text
    by_name = {b["name"]: b for b in res.json()["brokers"]}
    assert list(by_name) == list(BROKER_ADAPTERS)
    zerodha = by_name["zerodha"]
    assert zerodha["login_method"] == "oauth" and zerodha["daily_expiry_ist"] == "06:00"
    assert zerodha["readonly"]["redirect_url"].endswith("/api/broker/zerodha/oauth/callback")
    assert zerodha["readonly"]["server_ip"] == "203.0.113.10"
    fields = {f["key"]: f for f in zerodha["fields"]}
    assert fields["api_key"]["pattern"] == r"^[\x21-\x7E]{4,128}$" and fields["api_key"]["entered_by"] == "user"
    assert fields["request_token"]["entered_by"] == "oauth_callback" and fields["request_token"]["kind"] == "code"
    assert by_name["upstox"]["daily_expiry_ist"] == "03:30" and by_name["coindcx"]["daily_expiry_ist"] is None
    assert by_name["coindcx"]["readonly"]["redirect_url"] is None
    assert {f["key"] for f in by_name["shoonya"]["fields"]} >= {"client_id", "api_secret", "api_key", "totp_secret", "vc", "imei"}
    assert by_name["angel_one"]["fields"][3]["kind"] == "totp-secret"


def test_specs_redirect_url_comes_from_the_setting(monkeypatch):
    headers, _me = _owner(_email("specs-setting"))
    monkeypatch.setattr(app_config, "BROKER_OAUTH_REDIRECT_BASE_URL", "https://atp.example.com")
    by_name = {b["name"]: b for b in client.get("/api/broker/specs", headers=headers).json()["brokers"]}
    assert by_name["fyers"]["readonly"]["redirect_url"] == "https://atp.example.com/api/broker/fyers/oauth/callback"
    assert by_name["upstox"]["readonly"]["redirect_url"] == "https://atp.example.com/api/broker/upstox/oauth/callback"


# --- server validation with the same map -------------------------------------------------------------------------------

def _errors(res) -> str:
    assert res.status_code == 422, res.text
    detail = res.json()["detail"]
    assert detail["message"].endswith("were not saved")
    return " ".join(detail["errors"])


def test_unknown_field_is_refused_in_plain_english():
    headers, me = _owner(_email("unknown"))
    text = _errors(_store(headers, "zerodha", {"api_key": KITE_KEY, "api_secret": KITE_SECRET, "pin": ANGEL_PIN}))
    assert "Zerodha (Kite) does not use 'pin'" in text and ANGEL_PIN not in text
    text = _errors(_store(headers, "zerodha", {"api_key": KITE_KEY, "api_secret": KITE_SECRET, "favourite_colour": "blue"}))
    assert "does not use 'favourite_colour'" in text
    assert _row(me["tenant_id"], "zerodha")[0] is None          # nothing stored


def test_bad_format_is_refused_without_echoing_the_value():
    headers, _me = _owner(_email("format"))
    bad_key = KITE_KEY[:6] + " " + KITE_KEY[6:]
    res = _store(headers, "zerodha", {"api_key": bad_key, "api_secret": KITE_SECRET})
    text = _errors(res)
    assert "API key does not look right: expected 4 to 128 letters, digits or symbols with no spaces" in text
    assert bad_key not in res.text and KITE_SECRET not in res.text
    assert res.json()["detail"]["fields"].keys() == {"api_key"}
    text = _errors(_store(headers, "fyers", {"api_key": "not an app id", "api_secret": FYERS_SECRET}))
    assert "App ID does not look right" in text
    text = _errors(_store(headers, "dhan", {"client_id": "12 ab", "access_token": "too-short"}))
    assert "Client ID" in text and "Paste your Dhan access token" in text


def test_patterns_only_check_what_the_brokers_document():
    """A wrong pattern locks a real user out, so key and ID fields check only length and no spaces: keys shaped unlike
    the examples (not hex, mixed case, no dash suffix, a short numeric ID, a token that is not a JWT) still save."""
    loose = {
        "coindcx": {"api_key": "Ab" + _fake("ck").replace("-", "_") + "+/=", "api_secret": "S/" + _fake("cs") + "+="},
        "zerodha": {"api_key": "KiteKEY" + _fake("k")[-6:], "api_secret": "Mixed/Case+" + _fake("s")},
        "fyers": {"api_key": "app" + _fake("a")[-6:], "api_secret": "s" + _fake("b")[-6:]},
        "upstox": {"api_key": "not-a-uuid-" + _fake("u")[-6:], "api_secret": "sec" + _fake("v")[-4:]},
        "dhan": {"client_id": "AB12", "access_token": "opaque_" + _fake("dt")},
        "angel_one": {"api_key": ANGEL_KEY, "client_id": "a1", "pin": "12345678", "totp_secret": TOTP_SECRET + "AB2C" * 10},
    }
    for broker, body in loose.items():
        headers, _me = _owner(_email(f"loose-{broker}"))
        res = _store(headers, broker, body)
        assert res.status_code == 204, (broker, res.text)
    for broker, bspec in fs.BROKER_FIELD_SPECS.items():
        for spec in bspec.fields:
            if spec.kind in (fs.KIND_TEXT, fs.KIND_SECRET) and spec.key not in ("public_ip", "local_ip", "mac", "redirect_uri"):
                assert spec.pattern.startswith(r"^[\x21-\x7E]{") or spec.pattern == r"^[^\x00-\x1F\x7F]{1,128}$", (broker, spec.key, spec.pattern)


@pytest.mark.parametrize("bad", ["\x00", "\x1b", "\x7f", "\u200b", "\u00e9", "\t"])
def test_keys_must_be_printable_ascii(bad):
    """Loose, but not that loose: a control character, DEL, a zero-width space or a non-ASCII letter inside a key is
    refused (a pasted key that carries one would fail at the broker in a way nobody can see)."""
    headers, _me = _owner(_email("printable"))
    key = "Odd/Shape+" + _fake("k")
    res = _store(headers, "coindcx", {"api_key": key[:8] + bad + key[8:], "api_secret": "S/" + _fake("cs")})
    assert "API key does not look right" in _errors(res)
    assert _store(headers, "coindcx", {"api_key": key, "api_secret": "S/" + _fake("cs") + "~!"}).status_code == 204
    # The Shoonya password may hold spaces and non-ASCII letters, but no control character.
    spec = fs.BROKER_FIELD_SPECS["shoonya"].get("api_secret")
    assert re.fullmatch(spec.pattern, "pass word \u00e9") and not re.fullmatch(spec.pattern, "pass\x00word")


def test_missing_required_field_is_refused():
    headers, _me = _owner(_email("required"))
    assert "Zerodha (Kite) needs your API secret" in _errors(_store(headers, "zerodha", {"api_key": KITE_KEY}))
    text = _errors(_store(headers, "shoonya", {"client_id": "FA12345"}))
    assert all(f"needs your {label}" in text for label in ("Login password", "API key (vendor)", "TOTP secret"))


def test_a_token_atp_fills_in_itself_is_refused_where_nothing_ever_pasted_it():
    headers, _me = _owner(_email("filled"))
    text = _errors(_store(headers, "coindcx", fake_credentials("coindcx", access_token=_fake("session"))))
    assert "is filled in by ATP; it is never typed" in text


def test_a_six_digit_code_is_not_a_totp_secret_and_a_spaced_lowercase_secret_is_normalised():
    headers, me = _owner(_email("totp"))
    body = {"api_key": ANGEL_KEY, "client_id": ANGEL_CLIENT, "pin": ANGEL_PIN}
    assert "TOTP secret does not look right" in _errors(_store(headers, "angel_one", {**body, "totp_secret": "123456"}))
    spaced = " ".join(TOTP_SECRET[i:i + 4] for i in range(0, len(TOTP_SECRET), 4)).lower()
    assert _store(headers, "angel_one", {**body, "totp_secret": spaced}).status_code == 204
    assert _row(me["tenant_id"], "angel_one")[1].totp_secret == TOTP_SECRET


def test_existing_flows_keep_working():
    headers, me = _owner(_email("flows"))
    # A complete connection, then only today's token (the other fields are kept).
    assert _store(headers, "dhan", fake_credentials("dhan")).status_code == 204
    assert _store(headers, "dhan", {"access_token": DHAN_TOKEN.replace("payload", "payload2")}).status_code == 204
    assert _row(me["tenant_id"], "dhan")[1].client_id == fake_credentials("dhan")["client_id"]
    # Kite with a pasted access token and no secret (the old Settings form's paste box).
    assert _store(headers, "zerodha", {"api_key": KITE_KEY, "access_token": _fake("kite-token")}).status_code == 204
    # Fyers' paste-the-code flow: keys, then the redirect URL, then a pasted code.
    assert _store(headers, "fyers", {"api_key": FYERS_APP_ID, "api_secret": FYERS_SECRET}).status_code == 204
    assert _store(headers, "fyers", {"redirect_uri": "https://trade.example.com/redirect"}).status_code == 204
    assert _store(headers, "fyers", {"request_token": _fake("auth-code")}).status_code == 204
    # Upstox: a data-only connection (analytics token alone), and blank fields are ignored.
    assert _store(headers, "upstox", {"analytics_token": _fake("analytics"), "pin": ""}).status_code == 204
    assert _store(headers, "coindcx", fake_credentials("coindcx")).status_code == 204
    assert _store(headers, "shoonya", fake_credentials("shoonya", vc="FA12345_U", imei="abc-1")).status_code == 204


def test_a_client_cannot_plant_an_oauth_state():
    headers, me = _owner(_email("plant"))
    planted = {"broker": "zerodha", "user_id": me["id"], "nonce_sha256": "0" * 64, "expires_at": "2999-01-01T00:00:00+00:00"}
    assert _store(headers, "zerodha", {"api_key": KITE_KEY, "api_secret": KITE_SECRET, "oauth_pending": planted}).status_code == 204
    assert _row(me["tenant_id"], "zerodha")[1].oauth_pending is None


# --- Zerodha (Kite) and Fyers OAuth callbacks -------------------------------------------------------------------------

class _BrokerHttp:
    """A mocked broker: Kite's /session/token and /user/profile, Fyers' /validate-authcode and /profile."""

    def __init__(self, *, accept: bool = True):
        self.accept, self.requests, self.token = accept, [], _fake("access")

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/session/token"):
            if not self.accept:
                return httpx.Response(403, json={"status": "error", "message": "Token is invalid or has expired."})
            return httpx.Response(200, json={"status": "success", "data": {"access_token": self.token}})
        if path.endswith("/user/profile"):
            return httpx.Response(200, json={"status": "success", "data": {"user_id": "AB1234", "user_name": "Fake User"}})
        if path.endswith("/validate-authcode"):
            if not self.accept:
                return httpx.Response(400, json={"s": "error", "code": -413, "message": "Invalid auth code"})
            return httpx.Response(200, json={"s": "ok", "access_token": self.token})
        if path.endswith("/profile"):
            return httpx.Response(200, json={"s": "ok", "data": {"fy_id": "XY0001", "name": "Fake User"}})
        return httpx.Response(404, json={})


def _mock_broker(monkeypatch, broker: _BrokerHttp) -> None:
    def build(name, credentials):
        base = ZerodhaBroker.BASE_URL if name == "zerodha" else FYERS_API
        return get_broker_adapter(name, credentials, httpx.AsyncClient(transport=httpx.MockTransport(broker.handler), base_url=base))
    monkeypatch.setattr(broker_routes, "get_broker_adapter", build)


def _connect(tag: str, broker: str):
    headers, me = _owner(_email(tag))
    assert _store(headers, broker, fake_credentials(broker)).status_code == 204
    return headers, me


def _start(headers, broker: str, label: str = "primary") -> dict:
    res = client.get(f"/api/broker/{broker}/oauth/start", params={"account_label": label}, headers=headers)
    assert res.status_code == 200, res.text
    url = urlparse(res.json()["authorization_url"])
    query = {k: v[0] for k, v in parse_qs(url.query).items()}
    if broker == "zerodha":
        query["state"] = parse_qs(query["redirect_params"])["state"][0]
    return {"host": url.netloc, **query}


def _kite_callback(state: str, code: str, status: str = "success"):
    return client.get("/api/broker/zerodha/oauth/callback", follow_redirects=False,
                      params={"action": "login", "type": "login", "status": status, "request_token": code, "state": state})


def _fyers_callback(state: str, code: str, s: str = "ok"):
    return client.get("/api/broker/fyers/oauth/callback", follow_redirects=False,
                      params={"s": s, "code": "200", "auth_code": code, "state": state})


def _outcome(res) -> dict:
    assert res.status_code == 302
    return {k: v[0] for k, v in parse_qs(urlparse(res.headers["location"]).query).items()}


def test_kite_login_round_trip_stores_the_session_like_the_paste_flow(monkeypatch, caplog):
    headers, me = _connect("kite", "zerodha")
    monkeypatch.setattr(app_config, "BROKER_OAUTH_REDIRECT_BASE_URL", "https://atp.example.com")
    started = _start(headers, "zerodha")
    assert started["host"] == "kite.zerodha.com" and started["api_key"] == KITE_KEY and started["v"] == "3"
    claims = tl.parse_oauth_state(started["state"])
    assert (claims["tenant_id"], claims["user_id"], claims["broker"], claims["account_label"]) == (me["tenant_id"], me["id"], "zerodha", "primary")
    pending = _row(me["tenant_id"], "zerodha")[1].oauth_pending
    assert pending["user_id"] == me["id"] and claims["nonce"] not in json.dumps(pending)      # only its hash is stored

    broker = _BrokerHttp()
    _mock_broker(monkeypatch, broker)
    code = _fake("request-token")
    caplog.set_level(logging.DEBUG)
    assert _outcome(_kite_callback(started["state"], code)) == {"broker": "zerodha", "connected": "1"}

    record, creds = _row(me["tenant_id"], "zerodha")
    assert record.token_status == "VALID" and record.token_expires_at is not None and record.user_id == me["id"]
    assert creds.access_token == broker.token and creds.request_token is None and creds.oauth_pending is None
    assert creds.api_key == KITE_KEY and creds.api_secret == KITE_SECRET
    exchange = broker.requests[0]
    assert exchange.url.path == "/session/token" and code in exchange.content.decode()       # the real adapter's exchange
    assert "broker_authenticated zerodha/primary (oauth callback)" in _audit_text(me["tenant_id"])
    # The server's own log lines (the test client's request line is the browser's side, not the server's).
    server_logs = "\n".join(r.getMessage() for r in caplog.records if not r.name.startswith(("httpx", "httpcore")))
    for text in (_audit_text(me["tenant_id"]), server_logs):
        assert code not in text and broker.token not in text and KITE_SECRET not in text


def test_the_access_log_never_carries_a_callback_code():
    """uvicorn's access log prints the request path with its query; on the callback paths the query is redacted."""
    from app.core.logging_config import CallbackQueryRedactionFilter
    code = _fake("request-token")
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d', (
        "198.51.100.7:5000", "GET", f"/api/broker/zerodha/oauth/callback?status=success&request_token={code}&state=s", "1.1", 302), None)
    assert CallbackQueryRedactionFilter().filter(record)
    line = record.getMessage()
    assert code not in line and "/api/broker/zerodha/oauth/callback?[redacted]" in line and line.endswith("302")
    other = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, "%s", ("/api/broker/specs?x=1",), None)
    CallbackQueryRedactionFilter().filter(other)
    assert other.getMessage() == "/api/broker/specs?x=1"
    assert any(isinstance(f, CallbackQueryRedactionFilter) for f in logging.getLogger("uvicorn.access").filters)


def test_a_state_works_once(monkeypatch):
    headers, me = _connect("kite-once", "zerodha")
    state = _start(headers, "zerodha")["state"]
    broker = _BrokerHttp()
    _mock_broker(monkeypatch, broker)
    assert _outcome(_kite_callback(state, _fake("code")))["connected"] == "1"
    calls = len(broker.requests)
    assert _outcome(_kite_callback(state, _fake("code"))) == {"broker": "zerodha", "error": "invalid_state"}
    assert len(broker.requests) == calls                                   # the replay never reached the broker


def test_a_newer_login_replaces_the_older_state(monkeypatch):
    headers, _me = _connect("kite-newer", "zerodha")
    first = _start(headers, "zerodha")["state"]
    second = _start(headers, "zerodha")["state"]
    _mock_broker(monkeypatch, _BrokerHttp())
    assert _outcome(_kite_callback(first, _fake("code")))["error"] == "invalid_state"
    assert _outcome(_kite_callback(second, _fake("code")))["connected"] == "1"


def test_an_expired_or_forged_or_foreign_state_is_refused(monkeypatch):
    headers, me = _connect("kite-bad", "zerodha")
    broker = _BrokerHttp()
    _mock_broker(monkeypatch, broker)
    # Signed but older than ten minutes.
    old = datetime.now(timezone.utc) - timedelta(minutes=tl.OAUTH_STATE_TTL_MINUTES + 1)
    stale = tl.create_oauth_state(me["tenant_id"], me["id"], "zerodha", now=old, nonce="n")
    assert _outcome(_kite_callback(stale, _fake("code")))["error"] == "invalid_state"
    # Signed and fresh, but no login was started (no nonce waiting) - a login-fixation link.
    forged = tl.create_oauth_state(me["tenant_id"], me["id"], "zerodha", nonce=_fake("nonce"))
    assert _outcome(_kite_callback(forged, _fake("code")))["error"] == "invalid_state"
    # Without a nonce at all (an Upstox-style state), or for the other broker.
    assert _outcome(_kite_callback(tl.create_oauth_state(me["tenant_id"], me["id"], "zerodha"), _fake("code")))["error"] == "invalid_state"
    started = _start(headers, "zerodha")["state"]
    assert _outcome(_fyers_callback(started, _fake("code")))["error"] == "invalid_state"
    # A state naming someone who is not a user of this organisation cannot finish this user's login with the nonce.
    claims = tl.parse_oauth_state(started)
    other_user = tl.create_oauth_state(me["tenant_id"], me["id"] + 1000, "zerodha", nonce=claims["nonce"])
    assert _outcome(_kite_callback(other_user, _fake("code")))["error"] == "not_allowed"
    assert _outcome(_kite_callback("not-a-state", _fake("code")))["error"] == "invalid_state"
    assert broker.requests == []
    assert _row(me["tenant_id"], "zerodha")[0].token_status == "UNKNOWN"


def test_the_pending_login_lapses_after_ten_minutes():
    creds = BrokerCredentials()
    start = datetime(2026, 10, 20, 3, 0, tzinfo=timezone.utc)
    state = tl.begin_oauth_login(creds, 1, 2, "fyers", now=start)
    claims = _claims_unverified(state)
    assert tl.oauth_pending_matches(creds, claims, now=start + timedelta(minutes=9))
    assert not tl.oauth_pending_matches(creds, claims, now=start + timedelta(minutes=10, seconds=1))
    assert not tl.oauth_pending_matches(creds, {**claims, "nonce": claims["nonce"] + "x"}, now=start)


def _claims_unverified(state: str) -> dict:
    import jwt
    return jwt.decode(state, options={"verify_signature": False})


def test_broker_side_cancel_missing_code_and_a_rejected_code(monkeypatch):
    headers, me = _connect("kite-fail", "zerodha")
    state = _start(headers, "zerodha")["state"]
    broker = _BrokerHttp(accept=False)
    _mock_broker(monkeypatch, broker)
    assert _outcome(_kite_callback(state, _fake("code"), status="cancelled"))["error"] == "login_cancelled"
    assert _outcome(client.get("/api/broker/zerodha/oauth/callback", params={"state": state}, follow_redirects=False))["error"] == "missing_code_or_state"
    code = _fake("rejected")
    assert _outcome(_kite_callback(state, code))["error"] == "exchange_failed"
    assert _row(me["tenant_id"], "zerodha")[0].token_status == "UNKNOWN"
    assert code not in _audit_text(me["tenant_id"]) and "broker_oauth_failed" in _audit_text(me["tenant_id"])
    # The state was used by that attempt: it does not work a second time.
    assert _outcome(_kite_callback(state, _fake("code")))["error"] == "invalid_state"


def test_start_needs_stored_keys_and_a_login(monkeypatch):
    headers, _me = _owner(_email("kite-nokeys"))
    assert client.get("/api/broker/zerodha/oauth/start", headers=headers).status_code == 404
    assert _store(headers, "zerodha", {"api_key": KITE_KEY, "access_token": _fake("tok")}).status_code == 204
    res = client.get("/api/broker/zerodha/oauth/start", headers=headers)
    assert res.status_code == 400 and "API secret" in res.json()["detail"]
    assert client.get("/api/broker/zerodha/oauth/start").status_code in (401, 403)
    assert client.get("/api/broker/fyers/oauth/start").status_code in (401, 403)


def test_with_the_data_tier_on_only_the_holder_logs_in(monkeypatch):
    owner_headers, owner = _connect("kite-holder", "zerodha")
    member_headers, member = _member(owner_headers)
    _byob(owner["tenant_id"])
    assert client.get("/api/broker/zerodha/oauth/start", headers=member_headers).status_code == 403
    state = tl.create_oauth_state(owner["tenant_id"], member["id"], "zerodha", nonce=_fake("nonce"))
    _mock_broker(monkeypatch, _BrokerHttp())
    assert _outcome(_kite_callback(state, _fake("code")))["error"] == "not_holder"


def test_fyers_login_round_trip(monkeypatch):
    headers, me = _connect("fyers", "fyers")
    monkeypatch.setattr(app_config, "BROKER_OAUTH_REDIRECT_BASE_URL", "https://atp.example.com/")
    started = _start(headers, "fyers")
    assert started["host"] == "api-t1.fyers.in" and started["client_id"] == FYERS_APP_ID and started["response_type"] == "code"
    assert started["redirect_uri"] == "https://atp.example.com/api/broker/fyers/oauth/callback"
    assert _row(me["tenant_id"], "fyers")[1].redirect_uri == started["redirect_uri"]      # remembered, as Upstox does

    broker = _BrokerHttp()
    _mock_broker(monkeypatch, broker)
    code = _fake("auth-code")
    assert _outcome(_fyers_callback(started["state"], code, s="error"))["error"] == "login_cancelled"
    assert _outcome(_fyers_callback(started["state"], code)) == {"broker": "fyers", "connected": "1"}
    record, creds = _row(me["tenant_id"], "fyers")
    assert record.token_status == "VALID" and creds.access_token == broker.token and creds.request_token is None
    exchange = json.loads(broker.requests[0].content)
    assert broker.requests[0].url.path.endswith("/validate-authcode") and exchange["code"] == code
    assert code not in _audit_text(me["tenant_id"])


def test_fyers_keeps_a_redirect_url_the_paste_flow_already_uses(monkeypatch):
    headers, me = _connect("fyers-keep", "fyers")
    assert _store(headers, "fyers", {"redirect_uri": "https://trade.example.com/redirect"}).status_code == 204
    started = _start(headers, "fyers")
    assert started["redirect_uri"].endswith("/api/broker/fyers/oauth/callback")
    assert _row(me["tenant_id"], "fyers")[1].redirect_uri == "https://trade.example.com/redirect"
    # The paste-the-code flow of the old layout still opens its page with the stored URL.
    assert "trade.example.com" in client.get("/api/broker/fyers/login-url", headers=headers).json()["authorization_url"]


def test_the_second_account_logs_in_on_its_own_row(monkeypatch):
    headers, me = _connect("kite-second", "zerodha")
    assert _store(headers, "zerodha", fake_credentials("zerodha"), label="second").status_code == 204
    state = _start(headers, "zerodha", label="second")["state"]
    _mock_broker(monkeypatch, _BrokerHttp())
    assert _outcome(_kite_callback(state, _fake("code")))["connected"] == "1"
    assert _row(me["tenant_id"], "zerodha", "second")[0].token_status == "VALID"
    assert _row(me["tenant_id"], "zerodha", "primary")[0].token_status == "UNKNOWN"


# --- Shoonya's TOTP code -------------------------------------------------------------------------------------------------

def test_shoonya_sends_the_current_code_from_a_made_up_secret():
    secret = pyotp.random_base32()                       # made up at run time
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.content.decode())
        return httpx.Response(200, json={"stat": "Ok", "susertoken": _fake("session"), "actid": "FA12345", "uname": "Fake"})

    creds = BrokerCredentials(**fake_credentials("shoonya", totp_secret=secret))
    broker = ShoonyaBroker(creds, client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=ShoonyaBroker.BASE_URL))
    before = pyotp.TOTP(secret).now()
    run(broker.authenticate())
    after = pyotp.TOTP(secret).now()
    body = json.loads(parse_qs(sent[0])["jData"][0])
    assert body["factor2"] in (before, after) and re.fullmatch(r"[0-9]{6}", body["factor2"])
    assert secret not in sent[0]
    assert fs.validate_credentials("shoonya", {"totp_secret": secret}, {}, check_required=False) == []


def test_validate_credentials_unit():
    assert fs.validate_credentials("nope", {}, {})[0].message == "Unknown broker 'nope'."
    merged = fake_credentials("coindcx")
    assert fs.validate_credentials("coindcx", merged, merged) == []
    bad = fs.validate_credentials("coindcx", {"api_key": COINDCX_KEY + " zz"}, merged)
    assert [e.field for e in bad] == ["api_key"] and COINDCX_KEY not in bad[0].message
    assert fs.validate_credentials("upstox", {"api_key": UPSTOX_KEY}, {"api_key": UPSTOX_KEY})[0].message == "Upstox needs your API secret."


# --- security review follow-ups (UI2-5 review) ---------------------------------------------------------------------------

class _TokenlessLogin:
    """An adapter whose exchange 'succeeds' but hands back no session token."""

    def __init__(self, *_args):
        self.access_token = None

    async def authenticate(self):
        return None


class _UpstoxLogin:
    calls = 0

    def __init__(self, _name, creds):
        self.creds, self.access_token = creds, None

    async def authenticate(self):
        type(self).calls += 1
        self.access_token = _fake("upstox-access")


def _upstox_callback(state: str, code: str):
    return client.get("/api/broker/upstox/oauth/callback", params={"code": code, "state": state}, follow_redirects=False)


def _set_user(user_id: int, **values) -> None:
    from sqlalchemy import update as sa_update
    from app.db.models import User

    async def go():
        async with _session_factory() as session:
            await session.execute(sa_update(User).where(User.id == user_id).values(**values))
            await session.commit()
    run(go())


@pytest.mark.parametrize("broker", ["zerodha", "fyers", "upstox"])
def test_callbacks_are_rate_limited_per_ip_and_still_redirect(monkeypatch, broker):
    from app.core import rate_limit
    monkeypatch.delitem(app.dependency_overrides, broker_routes.oauth_callback_rate_limit)
    monkeypatch.setattr(broker_routes, "OAUTH_CALLBACK_LIMIT", 2)
    rate_limit.reset("broker_oauth_callback")
    called = []
    monkeypatch.setattr(broker_routes, "get_broker_adapter", lambda *a: called.append(a))
    path = f"/api/broker/{broker}/oauth/callback"
    try:
        outcomes = [client.get(path, params={"state": "forged", "code": "c", "auth_code": "c", "request_token": "c"},
                               follow_redirects=False) for _ in range(3)]
    finally:
        rate_limit.reset("broker_oauth_callback")
    assert [o.status_code for o in outcomes] == [302, 302, 302]                       # never a JSON 429
    assert [_outcome(o)["error"] for o in outcomes] == ["invalid_state", "invalid_state", "too_many_attempts"]
    assert _outcome(outcomes[2])["broker"] == broker and called == []


def test_the_redaction_covers_a_trailing_slash_and_any_path_prefix():
    from app.core.logging_config import CallbackQueryRedactionFilter, _redact_callback_query
    code = _fake("code")
    for path in (f"/api/broker/zerodha/oauth/callback/?request_token={code}&state=s",
                 f"/api/v1/broker/fyers/oauth/callback?auth_code={code}&state=s",
                 f"/proxy/api/v2/broker/upstox/oauth/callback/?code={code}"):
        assert code not in _redact_callback_query(path) and _redact_callback_query(path).endswith("?[redacted]")
        record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                                   ("198.51.100.7:5000", "GET", path, "1.1", 302), None)
        CallbackQueryRedactionFilter().filter(record)
        assert code not in record.getMessage()
    plain = logging.LogRecord("httpx", logging.INFO, __file__, 1, "%s", ("/api/broker/zerodha/oauth/start?account_label=x",), None)
    CallbackQueryRedactionFilter().filter(plain)
    assert plain.getMessage() == "/api/broker/zerodha/oauth/start?account_label=x"


def test_production_wants_an_https_redirect_base_url(monkeypatch, caplog):
    assert app_config.broker_oauth_redirect_problems("development", "http://localhost:8000") == []
    assert app_config.broker_oauth_redirect_warnings("development", "") == []
    for env in ("production", "staging"):
        assert app_config.broker_oauth_redirect_problems(env, "https://atp.example.com") == []
        assert app_config.broker_oauth_redirect_warnings(env, "https://atp.example.com") == []
        assert "must start with https://" in app_config.broker_oauth_redirect_problems(env, "http://atp.example.com")[0]
        assert "is not set" in app_config.broker_oauth_redirect_warnings(env, "")[0]
    # Unset in production: a loud warning, not a refusal to start (existing .env files do not have it yet).
    monkeypatch.setattr(app_config, "ENVIRONMENT", "production")
    monkeypatch.setattr(app_config, "JWT_SECRET_KEY", "a-real-" + uuid.uuid4().hex)
    monkeypatch.setattr(app_config, "SECRETS_ENCRYPTION_KEY", "set-" + uuid.uuid4().hex)
    monkeypatch.setattr(app_config, "ALLOWED_ORIGINS", ["https://atp.example.com"])
    monkeypatch.setattr(app_config, "METRICS_TOKEN", "m-" + uuid.uuid4().hex)
    monkeypatch.delenv("EMAIL_VERIFICATION_REQUIRED", raising=False)
    monkeypatch.setattr(app_config, "BROKER_OAUTH_REDIRECT_BASE_URL", "")
    with caplog.at_level(logging.WARNING, logger="app.core.config"):
        app_config.validate_production_config()
    assert "CONFIGURATION ACTION NEEDED: BROKER_OAUTH_REDIRECT_BASE_URL is not set" in caplog.text
    monkeypatch.setattr(app_config, "BROKER_OAUTH_REDIRECT_BASE_URL", "http://atp.example.com")
    with pytest.raises(RuntimeError, match="BROKER_OAUTH_REDIRECT_BASE_URL must start with https://"):
        app_config.validate_production_config()
    monkeypatch.setattr(app_config, "BROKER_OAUTH_REDIRECT_BASE_URL", "https://atp.example.com")
    app_config.validate_production_config()


@pytest.mark.parametrize("change", ["deactivated", "viewer", "brokers_scope_denied"])
def test_a_login_started_before_a_removal_or_demotion_does_not_finish(monkeypatch, change):
    headers, me = _connect(f"kite-gone-{change}", "zerodha")
    broker = _BrokerHttp()
    _mock_broker(monkeypatch, broker)
    state = _start(headers, "zerodha")["state"]
    if change == "deactivated":
        _set_user(me["id"], is_active=False)
    elif change == "viewer":
        _set_user(me["id"], role="VIEWER")
    else:
        _set_user(me["id"], scope_overrides=json.dumps({"deny": ["brokers:write"], "grant": []}))
    assert _outcome(_kite_callback(state, _fake("code")))["error"] == "not_allowed"
    assert broker.requests == []
    assert _row(me["tenant_id"], "zerodha")[0].token_status == "UNKNOWN"
    assert "may no longer change broker connections" in _audit_text(me["tenant_id"])


def test_a_trading_member_still_cannot_use_another_members_nonce(monkeypatch):
    headers, me = _connect("kite-member-nonce", "zerodha")
    _member_headers, member = _member(headers)
    broker = _BrokerHttp()
    _mock_broker(monkeypatch, broker)
    claims = tl.parse_oauth_state(_start(headers, "zerodha")["state"])
    theirs = tl.create_oauth_state(me["tenant_id"], member["id"], "zerodha", nonce=claims["nonce"])
    assert _outcome(_kite_callback(theirs, _fake("code")))["error"] == "invalid_state"
    assert broker.requests == []


def test_upstox_callback_uses_the_single_use_nonce(monkeypatch):
    headers, me = _connect("upstox-nonce", "upstox")
    _UpstoxLogin.calls = 0
    monkeypatch.setattr(broker_routes, "get_broker_adapter", _UpstoxLogin)
    # A signed state that no start route recorded (the pre-review Upstox state, no nonce) is refused before any exchange.
    assert _outcome(_upstox_callback(tl.create_oauth_state(me["tenant_id"], me["id"], "upstox"), "c"))["error"] == "invalid_state"
    assert _UpstoxLogin.calls == 0
    first = _start(headers, "upstox")["state"]
    second = _start(headers, "upstox")["state"]
    assert _outcome(_upstox_callback(first, "c"))["error"] == "invalid_state"          # replaced by the newer login
    code = _fake("upstox-code")
    assert _outcome(_upstox_callback(second, code)) == {"broker": "upstox", "connected": "1"}
    assert _outcome(_upstox_callback(second, code))["error"] == "invalid_state"        # works once
    assert _UpstoxLogin.calls == 1
    record, creds = _row(me["tenant_id"], "upstox")
    assert record.token_status == "VALID" and creds.oauth_pending is None and creds.request_token is None
    assert creds.redirect_uri.endswith("/api/broker/upstox/oauth/callback") and code not in _audit_text(me["tenant_id"])
    # The broker's own error word is relayed only when it is a plain word.
    res = client.get("/api/broker/upstox/oauth/callback", params={"error": "<script>"}, follow_redirects=False)
    assert _outcome(res)["error"] == "login_cancelled"


@pytest.mark.parametrize("broker", ["zerodha", "fyers", "upstox"])
def test_no_token_returned_is_audited(monkeypatch, broker):
    headers, me = _connect(f"no-token-{broker}", broker)
    monkeypatch.setattr(broker_routes, "get_broker_adapter", _TokenlessLogin)
    state = _start(headers, broker)["state"]
    code = _fake("code")
    res = {"zerodha": _kite_callback, "fyers": _fyers_callback, "upstox": _upstox_callback}[broker](state, code)
    assert _outcome(res)["error"] == "no_token_returned"
    audit = _audit_text(me["tenant_id"])
    assert f"broker_oauth_failed {broker}/primary: the broker returned no session token" in audit and code not in audit
    assert _row(me["tenant_id"], broker)[0].token_status == "UNKNOWN"


@pytest.mark.parametrize("broker", ["upstox", "zerodha", "fyers"])
def test_every_oauth_start_needs_brokers_write_and_a_verified_email(monkeypatch, broker):
    headers, me = _connect(f"start-gates-{broker}", broker)
    _set_user(me["id"], scope_overrides=json.dumps({"deny": ["brokers:write"], "grant": []}))
    res = client.get(f"/api/broker/{broker}/oauth/start", headers=headers)
    assert res.status_code == 403 and "brokers:write" in res.text
    _set_user(me["id"], scope_overrides=None, email_verified_at=None)
    monkeypatch.setattr(app_config, "EMAIL_VERIFICATION_REQUIRED", True)
    res = client.get(f"/api/broker/{broker}/oauth/start", headers=headers)
    assert res.status_code == 403 and res.headers.get("X-Step-Up") == "email"
    assert _row(me["tenant_id"], broker)[1].oauth_pending is None          # nothing was started


@pytest.mark.parametrize("broker", ["upstox", "zerodha", "fyers"])
def test_an_unverified_user_cannot_finish_an_oauth_login(monkeypatch, broker):
    headers, me = _connect(f"finish-unverified-{broker}", broker)
    called = []
    monkeypatch.setattr(broker_routes, "get_broker_adapter", lambda *a: called.append(a))
    state = _start(headers, broker)["state"]
    _set_user(me["id"], email_verified_at=None)
    monkeypatch.setattr(app_config, "EMAIL_VERIFICATION_REQUIRED", True)
    res = {"zerodha": _kite_callback, "fyers": _fyers_callback, "upstox": _upstox_callback}[broker](state, _fake("code"))
    assert _outcome(res)["error"] == "not_allowed" and called == []
    assert _row(me["tenant_id"], broker)[0].token_status == "UNKNOWN"
