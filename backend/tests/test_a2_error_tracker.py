"""Production readiness A2: the error tracker is OFF without a DSN, and nothing personal or secret leaves in an event.

The scrubber runs as the SDK's `before_send` / `before_breadcrumb`. Pinned: tokens (JWT, bearer, Telegram bot, API
keys in key=value form, long hex / base64), e-mail addresses, phone numbers, cookies, credential headers, query
strings, request bodies, local variables, the user block (only a numeric id stays) and URL credentials are gone; an
ordinary error message and the route stay readable.
"""
import json

import pytest

from app.core import config
from app.observability import error_tracker
from app.observability.error_tracker import scrub_breadcrumb, scrub_event, scrub_text

JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMiLCJ0ZW5hbnQiOjF9.c2lnbmF0dXJlLXZhbHVlLWhlcmU"
BOT = "123456789:AAFakeTelegramBotTokenValue_abcdefghijklmn"
SECRETS = [JWT, BOT, "abhi.wasu@example.com", "9876543210", "+91 98765 43210", "+44 20 7946 0958", "sk-ant-api03-FAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE",
           "0123456789abcdef0123456789abcdef01234567", "hunter2-password", "s3cr3t-cookie-value", "pa55w0rd"]


def _event():
    return {
        "message": f"Login failed for abhi.wasu@example.com with token={JWT} from +91 98765 43210",
        "exception": {"values": [{"type": "BrokerAPIError", "value": f"upstox said 401 for Bearer {JWT}; bot {BOT}",
                                  "stacktrace": {"frames": [{"function": "place", "vars": {"access_token": JWT, "password": "hunter2-password"}}]}}]},
        "request": {"method": "POST", "url": "https://aialgodesk.tech/api/auth/login?email=abhi.wasu@example.com&code=123456",
                    "query_string": "email=abhi.wasu@example.com", "data": {"password": "hunter2-password"},
                    "cookies": {"atp_refresh": "s3cr3t-cookie-value"},
                    "headers": {"Authorization": f"Bearer {JWT}", "Cookie": "atp_refresh=s3cr3t-cookie-value", "X-Api-Key": "k",
                                "User-Agent": "Mozilla/5.0", "X-Request-ID": "req-1", "X-Forwarded-For": "203.0.113.7"}},
        "user": {"id": 42, "email": "abhi.wasu@example.com", "ip_address": "203.0.113.7", "username": "abhi"},
        "extra": {"api_key": "sk-ant-api03-FAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE", "phone": "9876543210", "note": "call 9876543210",
                  "hash": "0123456789abcdef0123456789abcdef01234567", "dsn": "https://pub@glitchtip.example/1"},
        "contexts": {"environment": {"POSTGRES_PASSWORD": "pa55w0rd"}, "runtime": {"name": "CPython"}},
        "breadcrumbs": {"values": [{"category": "httpx", "data": {"url": "https://api.upstox.com/v2/x?api_key=abc&token=def", "method": "GET",
                                                                  "status_code": 401, "headers": {"Authorization": "Bearer x"}}},
                                   {"category": "log", "message": "sent OTP to +44 20 7946 0958"}]},
        "server_name": "atp-host-7",
        "tags": {"route": "/api/auth/login"},
    }


def test_nothing_personal_or_secret_survives_an_event():
    out = scrub_event(_event(), {})
    dumped = json.dumps(out)
    for secret in SECRETS + ["atp-host-7", "203.0.113.7", "email=", "code=123456", "abhi"]:
        assert secret not in dumped, secret
    assert out["user"] == {"id": "42"}
    assert out["request"] == {"method": "POST", "url": "https://aialgodesk.tech/api/auth/login",
                              "headers": {"User-Agent": "Mozilla/5.0", "X-Request-ID": "req-1"}}
    assert "vars" not in out["exception"]["values"][0]["stacktrace"]["frames"][0]
    assert out["exception"]["values"][0]["type"] == "BrokerAPIError" and "upstox said 401" in out["exception"]["values"][0]["value"]
    assert out["tags"] == {"route": "/api/auth/login"} and out["contexts"] == {"runtime": {"name": "CPython"}}
    crumb = out["breadcrumbs"]["values"][0]
    assert crumb["data"] == {"url": "https://api.upstox.com/v2/x", "method": "GET", "status_code": 401}


def test_text_patterns():
    assert scrub_text("mail me at a.b+c@example.com") == "mail me at [email]"
    assert scrub_text("call 98765 43210 or +91-9876543210") == "call [phone] or [phone]"
    assert scrub_text(f"Authorization: Bearer {JWT}") == "Authorization: Bearer [jwt]"
    assert scrub_text("Authorization: Bearer opaque.Token_1234") == "Authorization: Bearer [filtered]"
    assert scrub_text("postgres://atp:pa55w0rd@db:5432/x") == "postgres://[filtered]@db:5432/x"
    assert scrub_text("client_secret='abc' api_key=xyz") == "client_secret=[filtered] api_key=[filtered]"
    assert scrub_text("order 12345 failed after 3 tries at 10:42") == "order 12345 failed after 3 tries at 10:42"   # ordinary numbers stay


def test_breadcrumbs_keep_method_path_and_status_only():
    crumb = scrub_breadcrumb({"category": "http", "data": {"url": "https://x.example/a?token=1", "method": "POST", "status_code": 500,
                                                           "body": "password=hunter2-password"}})
    assert crumb == {"category": "http", "data": {"url": "https://x.example/a", "method": "POST", "status_code": 500}}
    assert scrub_event("not a dict") is None and scrub_breadcrumb(None) is None


def test_off_without_a_dsn(monkeypatch):
    monkeypatch.setattr(config, "ERROR_TRACKER_DSN", "", raising=False)
    monkeypatch.setitem(error_tracker._state, "on", False)
    assert error_tracker.init("api") is False and error_tracker.enabled() is False


def test_on_with_a_dsn_wires_the_scrubber_and_no_pii(monkeypatch):
    sentry_sdk = pytest.importorskip("sentry_sdk")
    seen = {}
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: seen.update(kw))
    monkeypatch.setattr(sentry_sdk, "set_tag", lambda *a: None)
    monkeypatch.setattr(config, "ERROR_TRACKER_DSN", "https://public@glitchtip.example/1", raising=False)
    monkeypatch.setitem(error_tracker._state, "on", False)
    assert error_tracker.init("worker") is True and error_tracker.enabled() is True
    assert seen["before_send"] is scrub_event and seen["before_breadcrumb"] is scrub_breadcrumb
    assert seen["send_default_pii"] is False and seen["include_local_variables"] is False
    assert seen["max_request_body_size"] == "never" and seen["traces_sample_rate"] == 0.0


def test_a_bad_dsn_never_stops_the_process(monkeypatch):
    sentry_sdk = pytest.importorskip("sentry_sdk")

    def boom(**_kw):
        raise ValueError("bad dsn")
    monkeypatch.setattr(sentry_sdk, "init", boom)
    monkeypatch.setattr(config, "ERROR_TRACKER_DSN", "not a dsn", raising=False)
    monkeypatch.setitem(error_tracker._state, "on", False)
    assert error_tracker.init("api") is False and error_tracker.enabled() is False
