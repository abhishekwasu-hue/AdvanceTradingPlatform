"""P0.2 (pro-grade upgrade plan, P0 security): the request limiter in Redis with a per-user flavour (S1), the
CAPTCHA hook (S1), egress checks for tenant-supplied URLs (S5), the SMS gateway body never echoed (S5),
TOTP replay (S8) and the refresh grace window (S9). The login delay itself is in test_login_protection.py."""
import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pyotp
import pytest

from app.alerts import dispatcher
from app.alerts.channels import SmsConfig, WebhookConfig
from app.auth import captcha, mfa
from app.core import config, egress, rate_limit
from app.db.models import NotificationRecord, User
from tests.test_auth_api import _register, client


def _run(coro):
    return asyncio.run(coro)


def test_rate_limiter_counts_in_redis_when_backed_and_falls_back_locally(monkeypatch):
    class _Redis:
        """Just the INCR+EXPIRE script the limiter sends (atomic in Redis, see rate_limit._HIT_LUA)."""
        def __init__(self):
            self.counts, self.ttl = {}, {}
        async def eval(self, script, numkeys, key, ttl, units=1):        # H-C1 b: INCRBY units
            assert "incr" in script and "expire" in script and numkeys == 1
            self.counts[key] = self.counts.get(key, 0) + units
            if self.counts[key] == 1:
                self.ttl[key] = ttl
            return self.counts[key]
    fake = _Redis()
    monkeypatch.setattr(rate_limit, "redis_backed", lambda: True)
    monkeypatch.setattr("app.cache.client._get_client", lambda: fake)
    rate_limit.reset("p0test")
    verdicts = [_run(rate_limit.allow("p0test", "1.2.3.4", 3, 60)) for _ in range(4)]
    assert verdicts == [True, True, True, False] and fake.ttl["rl:p0test:1.2.3.4"] == 60
    assert _run(rate_limit.allow("p0test", "5.6.7.8", 3, 60)) is True          # another key, its own count

    class _Down:
        async def eval(self, *a):
            raise ConnectionError("redis down")
    monkeypatch.setattr("app.cache.client._get_client", lambda: _Down())
    rate_limit.reset("p0test")
    assert [_run(rate_limit.allow("p0test", "x", 2, 60)) for _ in range(3)] == [True, True, False]   # local window, not fail-open
    monkeypatch.setattr(rate_limit, "redis_backed", lambda: False)
    assert rate_limit.redis_backed() is False


def test_analysis_endpoints_are_limited_per_user(monkeypatch):
    headers = {"Authorization": f"Bearer {_register('p02-limit@example.com')}"}
    rate_limit.reset("analysis")
    monkeypatch.setattr(rate_limit, "redis_backed", lambda: False)
    body = {"symbols": []}                                                      # nothing to scan: the cheapest valid call
    statuses = [client.post("/api/scanner/run", headers=headers, json=body).status_code for _ in range(61)]
    assert statuses.count(429) == 1 and statuses[-1] == 429
    rate_limit.reset("analysis")


def test_captcha_is_demanded_after_the_configured_failures_and_verified_with_the_provider(monkeypatch):
    _register("p02-captcha@example.com")
    monkeypatch.setattr(config, "CAPTCHA_PROVIDER", "turnstile")
    monkeypatch.setattr(config, "CAPTCHA_SECRET", "s3cret")
    monkeypatch.setattr(config, "LOGIN_CAPTCHA_AFTER_FAILURES", 2)
    monkeypatch.setattr(config, "LOGIN_DELAY_AFTER_FAILURES", 99)              # keep the delay out of this test
    from app.auth import lockout
    monkeypatch.setattr(lockout, "LOGIN_DELAY_AFTER_FAILURES", 99)
    posted = {}
    real_verify = captcha.verify

    async def fake_verify(token, remote_ip, client=None):
        posted["token"] = token
        return token == "good-token"
    monkeypatch.setattr(captcha, "verify", fake_verify)
    for _ in range(2):
        assert client.post("/api/auth/login", json={"email": "p02-captcha@example.com", "password": "nope"}).status_code == 401
    refused = client.post("/api/auth/login", json={"email": "p02-captcha@example.com", "password": "S3cur3Pass!"})
    assert refused.status_code == 403 and refused.json()["detail"] == "captcha_required" and refused.headers["x-captcha"] == "required"
    ok = client.post("/api/auth/login", json={"email": "p02-captcha@example.com", "password": "S3cur3Pass!", "captcha_token": "good-token"})
    assert ok.status_code == 200 and posted["token"] == "good-token"
    # The provider call itself: the secret goes to the provider only, never into an error, and a network failure refuses.
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["body"] = str(request.url), request.content.decode()
        return httpx.Response(200, json={"success": True})
    assert _run(real_verify("tok", "9.9.9.9", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))) is True
    assert seen["url"] == captcha.VERIFY_URLS["turnstile"] and "secret=s3cret" in seen["body"] and "remoteip=9.9.9.9" in seen["body"]

    def down(request):
        raise httpx.ConnectError("down")
    assert _run(real_verify("tok", None, client=httpx.AsyncClient(transport=httpx.MockTransport(down)))) is False
    monkeypatch.setattr(config, "CAPTCHA_PROVIDER", "")
    assert captcha.configured() is False and captcha.required(100) is False


def test_egress_rules_block_private_destinations_and_resolve_before_sending(monkeypatch):
    assert egress.is_public_address("93.184.216.34") and egress.is_public_address("2606:2800:220:1:248:1893:25c8:1946")
    for bad in ("127.0.0.1", "10.0.0.5", "172.16.3.4", "192.168.1.1", "169.254.169.254", "0.0.0.0", "::1", "fe80::1", "::ffff:10.1.1.1", "224.0.0.1"):
        assert not egress.is_public_address(bad), bad
    # Literal checks: scheme, obvious local targets (strict only), allowlist.
    egress.check_url_literal("https://hooks.example.com/x")
    with pytest.raises(egress.EgressBlocked):
        egress.check_url_literal("http://hooks.example.com/x")                  # plain http to the internet
    with pytest.raises(egress.EgressBlocked):
        egress.check_url_literal("ftp://hooks.example.com/x")
    egress.check_url_literal("http://localhost:9000/hook", environment="development")
    for url in ("http://localhost:9000/hook", "http://169.254.169.254/latest/meta-data", "https://10.0.0.9/", "https://[::1]/"):
        with pytest.raises(egress.EgressBlocked):
            egress.check_url_literal(url, environment="production")
    assert egress.host_allowed("api.msg91.com", ["api.msg91.com"]) and egress.host_allowed("a.b.twilio.com", [".twilio.com"])
    assert not egress.host_allowed("evil.com", ["api.msg91.com"]) and egress.host_allowed("anything", [])
    monkeypatch.setattr(egress, "EGRESS_ALLOWED_HOSTS", ["hooks.example.com"])
    egress.check_url_literal("https://hooks.example.com/x")
    with pytest.raises(egress.EgressBlocked):
        egress.check_url_literal("https://other.example.com/x")
    monkeypatch.setattr(egress, "EGRESS_ALLOWED_HOSTS", [])
    # Resolved check: a public name that rebinds to a private address is refused in production, allowed in dev.
    _run(egress.check_url_resolved("https://hooks.example.com/x", environment="production", resolver=lambda h: ["93.184.216.34"]))
    with pytest.raises(egress.EgressBlocked):
        _run(egress.check_url_resolved("https://hooks.example.com/x", environment="production", resolver=lambda h: ["93.184.216.34", "10.0.0.1"]))
    with pytest.raises(egress.EgressBlocked):
        _run(egress.check_url_resolved("https://hooks.example.com/x", environment="production", resolver=lambda h: []))
    _run(egress.check_url_resolved("https://hooks.example.com/x", environment="development", resolver=lambda h: ["10.0.0.1"]))
    # The channel models carry the same rule at save time.
    WebhookConfig(url="https://hooks.example.com/x", secret="s3cr3t-webhook-secret-0123")
    with pytest.raises(ValueError):
        WebhookConfig(url="http://hooks.example.com/x", secret="s3cr3t-webhook-secret-0123")
    monkeypatch.setattr(egress, "ENVIRONMENT", "production")
    with pytest.raises(ValueError):
        WebhookConfig(url="http://localhost:9000/x", secret="s3cr3t-webhook-secret-0123")
    with pytest.raises(ValueError):
        SmsConfig(url="https://192.168.0.10/send", body_template='{"to":"{to}"}', to_numbers=["+919812345678"])
    monkeypatch.setattr(egress, "ENVIRONMENT", "development")


def test_sms_gateway_errors_never_echo_the_response_body_and_senders_check_egress(monkeypatch):
    checked = []

    async def fake_check(url, **kw):
        checked.append(url)
    monkeypatch.setattr(dispatcher, "check_url_resolved", fake_check)
    cfg = SmsConfig(url="https://sms.example.com/send", body_template='{"to":"{to}","text":"{text}"}', to_numbers=["+919812345678"])
    note = NotificationRecord(tenant_id=1, user_id=None, event_type="SYSTEM_FAILURE", severity="INFO", title="t", message="m",
                              created_at=datetime.now(timezone.utc))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal secret dump: api_key=XYZ")
    with pytest.raises(RuntimeError) as exc:
        _run(dispatcher.send_sms(cfg, note, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))))
    assert "HTTP 500" in str(exc.value) and "XYZ" not in str(exc.value) and checked == ["https://sms.example.com/send"]
    web = WebhookConfig(url="https://hooks.example.com/x", secret="s3cr3t-webhook-secret-0123")
    _run(dispatcher.send_webhook(web, note, client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))))
    assert checked[-1] == "https://hooks.example.com/x"


def test_totp_codes_are_accepted_once_per_step():
    secret = pyotp.random_base32()
    now = datetime(2026, 10, 8, 4, 0, 15, tzinfo=timezone.utc)
    user = User(email="x@example.com", hashed_password="h", tenant_id=1, mfa_last_step=None)
    code = pyotp.TOTP(secret).at(now)
    step = mfa.verify_totp(secret, code, now=now)
    assert step == int(now.timestamp()) // 30
    assert mfa.accept_totp(user, secret, code, now=now) is True and user.mfa_last_step == step
    assert mfa.accept_totp(user, secret, code, now=now) is False                                   # replay inside the window
    assert mfa.accept_totp(user, secret, code, now=now + timedelta(seconds=20)) is False            # still the same step
    previous = pyotp.TOTP(secret).at(now - timedelta(seconds=30))
    assert mfa.accept_totp(user, secret, previous, now=now) is False                               # an older step never comes back
    following = pyotp.TOTP(secret).at(now + timedelta(seconds=30))
    assert mfa.accept_totp(user, secret, following, now=now) is True and user.mfa_last_step == step + 1
    assert mfa.verify_totp(secret, "12345", now=now) is None and mfa.verify_totp(secret, "000000", now=now) is None
