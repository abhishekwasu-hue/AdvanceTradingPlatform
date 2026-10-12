"""Security fix (S10 review): the SMS gateway request template on main.

1. Substituted values are escaped by the body's content type, case-insensitively: JSON-escaped for JSON, percent-encoded
   for everything else - a rule title cannot add a parameter (`&To=...`) to a form body or a GET query, nor break out of
   a JSON string under `Application/JSON`.
2. The stored write-only headers (an API key, a bearer token) are not carried over to a gateway URL on another scheme /
   host / port - they must be typed again.
3. Header names and values are validated on save: no CR / LF / control characters, names are HTTP tokens."""
import json
from datetime import datetime, timezone
from typing import List
from urllib.parse import parse_qs

import httpx
import pytest

from app.alerts import dispatcher
from app.alerts.channels import merge_secrets, parse_config
from app.core.enums import NotificationSeverity, NotificationType
from app.notifications.service import notify
from tests.test_alerts import _auth, _dispatch, _put
from tests.test_auth_api import _session_factory

TWILIO = {"url": "https://api.twilio.com/2010-04-01/Accounts/AC1/Messages.json", "headers": {"Authorization": "Basic SECRET"},
          "content_type": "application/x-www-form-urlencoded", "body_template": "From=+15550000000&To={to}&Body={title}",
          "to_numbers": ["+919812345678"]}


class _N:
    title = "x&To=+15550001111&Body=spam"
    message = 'm"}],"to":"+1555'
    severity = "CRITICAL"
    event_type = "SCREEN_ALERT"
    id = 1
    created_at = datetime.now(timezone.utc)


def test_a_form_body_keeps_an_injected_title_as_one_value():
    body, _ = dispatcher.render_sms(parse_config("SMS", TWILIO), _N(), "+919812345678")
    params = parse_qs(body, keep_blank_values=True)
    assert params["To"] == ["+919812345678"] and params["Body"] == ["x&To=+15550001111&Body=spam"]


def test_mixed_case_json_is_still_json_escaped():
    cfg = parse_config("SMS", {**TWILIO, "content_type": "Application/JSON", "body_template": '{"to":"{to}","text":"{text}"}'})
    body, _ = dispatcher.render_sms(cfg, _N(), "+919812345678")
    parsed = json.loads(body)
    assert set(parsed) == {"to", "text"} and parsed["to"] == "+919812345678" and '"to":"+1555' in parsed["text"]


def test_the_get_path_sends_each_value_once_and_never_to_an_injected_number():
    seen: List[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})
    cfg = parse_config("SMS", {**TWILIO, "method": "GET"})
    import asyncio
    asyncio.run(dispatcher.send_sms(cfg, _N(), httpx.AsyncClient(transport=httpx.MockTransport(handler))))
    query = parse_qs(seen[0].url.query.decode(), keep_blank_values=True)
    assert query["To"] == ["+919812345678"] and query["Body"] == ["x&To=+15550001111&Body=spam"]


def test_stored_headers_never_follow_the_gateway_to_another_origin():
    existing = {**TWILIO}
    same = merge_secrets("SMS", {**TWILIO, "url": "https://api.twilio.com/other/path", "headers": {}}, existing)
    assert same["headers"] == {"Authorization": "Basic SECRET"}                       # same origin: kept
    for url in ("https://evil.example/collect", "https://api.twilio.com:8443/x", "http://api.twilio.com/x"):
        moved = merge_secrets("SMS", {**TWILIO, "url": url, "headers": {}}, existing)
        assert moved["headers"] == {}, url
    retyped = merge_secrets("SMS", {**TWILIO, "url": "https://evil.example/x", "headers": {"X-Key": "new"}}, existing)
    assert retyped["headers"] == {"X-Key": "new"}


def test_header_names_and_values_are_validated_on_save():
    for bad in ({"Authorization": "Basic a\r\nX-Evil: 1"}, {"Bad Name": "v"}, {"X-Null": "a\x00b"}, {"": "v"}):
        with pytest.raises(ValueError):
            parse_config("SMS", {**TWILIO, "headers": bad})
    assert parse_config("SMS", {**TWILIO, "headers": {"X-Api-Key": "abc def\tghi"}}).headers == {"X-Api-Key": "abc def\tghi"}


def test_end_to_end_a_moved_gateway_gets_no_stored_header():
    headers, me = _auth("gw-moved@example.com")
    assert _put(headers, "sms", TWILIO).status_code == 200
    assert _put(headers, "sms", {**TWILIO, "url": "https://collector.example/sms", "headers": {}}).status_code == 200
    seen: List[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    async def raise_alert():
        async with _session_factory() as session:
            await notify(session, me["tenant_id"], NotificationType.SYSTEM_FAILURE, title="T", message="m", severity=NotificationSeverity.CRITICAL)
    import asyncio
    asyncio.run(raise_alert())
    _dispatch(me["tenant_id"], httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert seen and all("authorization" not in {k.lower() for k in r.headers.keys()} for r in seen)
    assert all("collector.example" in str(r.url) for r in seen)
    assert _put(headers, "sms", {**TWILIO, "headers": {"Authorization": "Basic x\r\nInjected: 1"}}).status_code == 400


# --- review round: WHATWG vs urllib parsing, the SMTP password, header / content-type hygiene ---------------------------

def test_a_backslash_url_cannot_carry_stored_headers_to_another_host():
    existing = {**TWILIO}
    for url in ("https://evil.example\\@api.twilio.com/x", "https://evil.example\\\\@api.twilio.com/x",
                "https://evil.example/\\@api.twilio.com/x"):
        with pytest.raises(ValueError):
            parse_config("SMS", {**TWILIO, "url": url})              # refused on save
        with pytest.raises(ValueError):
            parse_config("WEBHOOK", {"url": url, "secret": "s" * 20})
        moved = merge_secrets("SMS", {**TWILIO, "url": url, "headers": {}}, existing)
        assert moved["headers"] == {}, url                          # and never treated as the same origin
    # an origin is read by the parser that validated it: userinfo does not change the host
    assert merge_secrets("SMS", {**TWILIO, "url": "https://user@api.twilio.com/x", "headers": {}}, existing)["headers"]
    assert merge_secrets("SMS", {**TWILIO, "url": "https://api.twilio.com:443/y", "headers": {}}, existing)["headers"]


def test_end_to_end_a_backslash_url_is_refused():
    headers, _ = _auth("gw-backslash@example.com")
    assert _put(headers, "sms", TWILIO).status_code == 200
    assert _put(headers, "sms", {**TWILIO, "url": "https://evil.example\\@api.twilio.com/x", "headers": {}}).status_code == 400


EMAIL = {"smtp_host": "smtp.example.com", "smtp_port": 587, "username": "alerts", "password": "pw", "use_tls": True,
         "from_address": "alerts@example.com", "to_addresses": ["ops@example.com"]}


def test_the_smtp_password_never_follows_a_changed_server_login_or_dropped_tls():
    existing = parse_config("EMAIL", EMAIL).model_dump(mode="json")
    blank = {**EMAIL, "password": None}
    kept = merge_secrets("EMAIL", {**blank, "smtp_host": "SMTP.example.com", "to_addresses": ["x@example.com"]}, existing)
    assert kept["password"] == "pw"                                  # same server (case-insensitive), new recipients: kept
    for change in ({"smtp_host": "smtp.evil.example"}, {"smtp_port": 2525}, {"username": "other"}, {"use_tls": False}):
        assert merge_secrets("EMAIL", {**blank, **change}, existing)["password"] is None, change
    assert merge_secrets("EMAIL", {**blank, "smtp_host": "smtp.evil.example", "password": "typed"}, existing)["password"] == "typed"


def test_the_smtp_host_goes_through_the_egress_policy(monkeypatch):
    for bad in ("smtp.example.com\r\nX: 1", "smtp example.com", "a/b", "user@host"):
        with pytest.raises(ValueError):
            parse_config("EMAIL", {**EMAIL, "smtp_host": bad})
    from app.core import egress
    monkeypatch.setattr(egress, "ENVIRONMENT", "production")
    for private in ("127.0.0.1", "10.0.0.5", "169.254.169.254", "localhost", "::1"):
        with pytest.raises(ValueError):
            parse_config("EMAIL", {**EMAIL, "smtp_host": private})
    assert parse_config("EMAIL", {**EMAIL, "smtp_host": "SMTP.Example.com"}).smtp_host == "smtp.example.com"
    # on send the resolved address is checked again (DNS can point a public name at a private address)
    sent: List[str] = []
    monkeypatch.setattr(dispatcher, "_smtp_send", lambda config, message: sent.append(config.smtp_host))
    monkeypatch.setattr(egress, "resolve", lambda host: ["10.1.2.3"])
    import asyncio
    with pytest.raises(egress.EgressBlocked):
        asyncio.run(dispatcher.send_email(parse_config("EMAIL", EMAIL), "s", "b"))   # type: ignore[arg-type]
    monkeypatch.setattr(egress, "resolve", lambda host: ["93.184.216.34"])
    asyncio.run(dispatcher.send_email(parse_config("EMAIL", EMAIL), "s", "b"))       # type: ignore[arg-type]
    assert sent == ["smtp.example.com"]


def test_header_hygiene_reserved_names_ascii_values_and_content_type():
    for bad in ({"Host": "api.twilio.com"}, {"content-length": "0"}, {"Transfer-Encoding": "chunked"}, {"Connection": "close"},
                {"X-Key": "caf\u00e9"}, {"X-Key": "\u2028"}, {"X-Key": "v\n"}, {"X-Key\n": "v"}):
        with pytest.raises(ValueError):
            parse_config("SMS", {**TWILIO, "headers": bad})
    for bad_ct in ("application/json\r\nX-Evil: 1", "text/plain\n", "", "caf\u00e9/x"):
        with pytest.raises(ValueError):
            parse_config("SMS", {**TWILIO, "content_type": bad_ct})
    assert parse_config("SMS", {**TWILIO, "content_type": "application/json; charset=utf-8"}).content_type


def test_one_recipient_failing_in_any_way_does_not_stop_the_others():
    seen: List[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        to = parse_qs(request.content.decode())["To"][0]
        if to == "+919800000001":
            raise UnicodeEncodeError("ascii", "x", 0, 1, "boom")
        seen.append(to)
        return httpx.Response(200, json={"ok": True})
    cfg = parse_config("SMS", {**TWILIO, "to_numbers": ["+919800000001", "+919800000002"]})
    import asyncio
    with pytest.raises(RuntimeError, match="UnicodeEncodeError"):
        asyncio.run(dispatcher.send_sms(cfg, _N(), httpx.AsyncClient(transport=httpx.MockTransport(handler))))
    assert seen == ["+919800000002"]


def test_smtp_starttls_verifies_the_server_certificate(monkeypatch):
    """Gateway review LOW-2: STARTTLS runs with a default (verifying) SSL context, never an unverified one."""
    import ssl

    from app.alerts import dispatcher
    from app.alerts.channels import EmailConfig

    seen = {}

    class _FakeSMTP:
        def __init__(self, host, port, timeout=None):
            seen["host"] = host

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def ehlo(self):
            pass

        def starttls(self, context=None):
            seen["context"] = context

        def login(self, user, password):
            seen["login"] = user

        def send_message(self, message):
            seen["sent"] = True

    monkeypatch.setattr(dispatcher.smtplib, "SMTP", _FakeSMTP)
    config = EmailConfig.model_validate({"smtp_host": "smtp.example.com", "smtp_port": 587, "use_tls": True,
                                         "username": "u", "password": "p", "from_address": "alerts@example.com",
                                         "to_addresses": ["ops@example.com"]})
    dispatcher._smtp_send(config, dispatcher.EmailMessage())
    context = seen["context"]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname is True
    assert seen.get("sent") is True
