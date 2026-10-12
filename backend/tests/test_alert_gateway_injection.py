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
