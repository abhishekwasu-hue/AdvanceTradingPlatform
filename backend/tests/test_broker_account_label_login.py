"""Logging in to one of several accounts at the same broker: /authenticate, the Upstox OAuth start/callback and the
paste-the-code login act on the account label they are given (default "primary"), never on another account's
credential, and the label never reaches another organisation's or (with `byob_data_tier` on) another member's row.
No network: the broker adapter is replaced."""
import asyncio
import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select

from app.brokers import routes as broker_routes
from app.brokers import token_lifecycle as tl
from app.brokers.models import BrokerProfile
from app.db.models import BrokerCredentialRecord
from tests.test_auth_api import _session_factory, client
from tests.test_bt1b_guard_all_reads import _byob, _flags, _member
from tests.test_phase_k_commercial import _owner

run = asyncio.run


def _email(tag: str) -> str:
    return f"acctlabel-{tag}-{uuid.uuid4().hex[:8]}@example.com"


@pytest.fixture(autouse=True)
def _reset_flags():
    yield
    _flags(byob_data_tier=[])


class _Login:
    """Records the credentials it was built with; the login hands back a token named after the API key."""

    def __init__(self, seen: list, creds):
        self.creds, self.access_token = creds, None
        seen.append(creds)

    async def authenticate(self):
        self.access_token = f"token-for-{self.creds.api_key}"
        return BrokerProfile(broker="upstox", user_id=self.creds.api_key)


def _fake_broker(monkeypatch) -> list:
    seen: list = []
    monkeypatch.setattr(broker_routes, "get_broker_adapter", lambda name, creds: _Login(seen, creds))
    return seen


def _store(headers, broker: str, label: str, **fields) -> None:
    res = client.post(f"/api/broker/{broker}/credentials?account_label={label}", headers=headers, json=fields)
    assert res.status_code == 204, res.text


def _rows(tenant_id: int, broker: str) -> dict:
    async def go():
        async with _session_factory() as session:
            recs = await session.scalars(select(BrokerCredentialRecord).where(
                BrokerCredentialRecord.tenant_id == tenant_id, BrokerCredentialRecord.broker_name == broker))
            return {r.account_label: (r.token_status, tl.load_credentials(r)) for r in recs}
    return run(go())


def _two_accounts(tag: str, broker: str = "upstox"):
    headers, me = _owner(_email(tag))
    _store(headers, broker, "primary", api_key="k-primary", api_secret="s-primary")
    _store(headers, broker, "second", api_key="k-second", api_secret="s-second")
    return headers, me


# --- /authenticate ------------------------------------------------------------------------------------------------------

def test_authenticating_the_second_account_uses_the_second_credential(monkeypatch):
    headers, me = _two_accounts("auth2")
    seen = _fake_broker(monkeypatch)
    res = client.post("/api/broker/upstox/authenticate?account_label=second", headers=headers)
    assert res.status_code == 200, res.text
    assert [c.api_key for c in seen] == ["k-second"]
    rows = _rows(me["tenant_id"], "upstox")
    assert rows["second"][0] == "VALID" and rows["second"][1].access_token == "token-for-k-second"
    assert rows["primary"][0] == "UNKNOWN" and rows["primary"][1].access_token is None    # the primary is untouched
    # No secret in the response or the audit trail.
    assert "s-second" not in res.text and "token-for" not in res.text
    logs = client.get("/api/audit-logs", headers=headers).json()
    assert any(entry["event"] == "broker_authenticated" and entry["detail"] == "upstox/second" for entry in logs)
    assert not any("s-second" in (entry["detail"] or "") or "token-for" in (entry["detail"] or "") for entry in logs)


def test_authenticate_without_a_label_still_means_the_primary(monkeypatch):
    headers, me = _two_accounts("auth1")
    seen = _fake_broker(monkeypatch)
    assert client.post("/api/broker/upstox/authenticate", headers=headers).status_code == 200
    assert [c.api_key for c in seen] == ["k-primary"]
    rows = _rows(me["tenant_id"], "upstox")
    assert rows["primary"][0] == "VALID" and rows["second"][0] == "UNKNOWN"


def test_authenticate_refuses_an_unknown_or_malformed_label(monkeypatch):
    headers, _me = _two_accounts("auth-bad")
    seen = _fake_broker(monkeypatch)
    assert client.post("/api/broker/upstox/authenticate?account_label=third", headers=headers).status_code == 404
    assert client.post("/api/broker/upstox/authenticate?account_label=../primary", headers=headers).status_code == 400
    assert seen == []


def test_a_label_never_reaches_another_organisations_credential(monkeypatch):
    _a_headers, a = _two_accounts("org-a")
    b_headers, _b = _owner(_email("org-b"))
    seen = _fake_broker(monkeypatch)
    res = client.post("/api/broker/upstox/authenticate?account_label=second", headers=b_headers)
    assert res.status_code == 404
    assert client.get("/api/broker/upstox/oauth/start?account_label=second", headers=b_headers).status_code == 404
    assert seen == []
    rows = _rows(a["tenant_id"], "upstox")
    assert rows["second"][0] == "UNKNOWN" and rows["second"][1].access_token is None


def test_a_label_never_reaches_another_members_credential_with_byob_on(monkeypatch):
    owner_headers, owner = _two_accounts("holder")
    member_headers, _member_me = _member(owner_headers)
    _byob(owner["tenant_id"])
    seen = _fake_broker(monkeypatch)
    res = client.post("/api/broker/upstox/authenticate?account_label=second", headers=member_headers)
    assert res.status_code == 403
    assert client.get("/api/broker/upstox/oauth/start?account_label=second", headers=member_headers).status_code == 403
    assert seen == []
    assert _rows(owner["tenant_id"], "upstox")["second"][0] == "UNKNOWN"


# --- Upstox OAuth -------------------------------------------------------------------------------------------------------

def test_upstox_oauth_for_the_second_account_updates_the_second_row(monkeypatch):
    headers, me = _two_accounts("oauth2")
    res = client.get("/api/broker/upstox/oauth/start?account_label=second", headers=headers)
    assert res.status_code == 200, res.text
    q = parse_qs(urlparse(res.json()["authorization_url"]).query)
    assert q["client_id"] == ["k-second"]
    assert tl.parse_oauth_state(q["state"][0])["account_label"] == "second"

    seen = _fake_broker(monkeypatch)
    cb = client.get("/api/broker/upstox/oauth/callback", params={"code": "the-code", "state": q["state"][0]}, follow_redirects=False)
    assert cb.status_code == 302 and "connected=1" in cb.headers["location"]
    assert [(c.api_key, c.request_token) for c in seen] == [("k-second", "the-code")]
    rows = _rows(me["tenant_id"], "upstox")
    assert rows["second"][0] == "VALID" and rows["second"][1].access_token == "token-for-k-second"
    assert rows["primary"][0] == "UNKNOWN" and rows["primary"][1].access_token is None


def test_upstox_oauth_state_without_a_label_means_the_primary(monkeypatch):
    headers, me = _two_accounts("oauth1")
    q = parse_qs(urlparse(client.get("/api/broker/upstox/oauth/start", headers=headers).json()["authorization_url"]).query)
    assert q["client_id"] == ["k-primary"]
    seen = _fake_broker(monkeypatch)
    cb = client.get("/api/broker/upstox/oauth/callback", params={"code": "c", "state": q["state"][0]}, follow_redirects=False)
    assert cb.status_code == 302 and "connected=1" in cb.headers["location"]
    assert [c.api_key for c in seen] == ["k-primary"]
    assert _rows(me["tenant_id"], "upstox")["second"][0] == "UNKNOWN"


def test_upstox_oauth_callback_rejects_a_malformed_label_in_the_state(monkeypatch):
    _headers, me = _two_accounts("oauth-bad")
    seen = _fake_broker(monkeypatch)
    state = tl.create_oauth_state(me["tenant_id"], me["id"], "upstox", account_label="../second")
    cb = client.get("/api/broker/upstox/oauth/callback", params={"code": "c", "state": state}, follow_redirects=False)
    assert cb.status_code == 302 and "error=invalid_state" in cb.headers["location"]
    assert seen == []


# --- paste-the-code login (already label-aware; kept that way) ----------------------------------------------------------

def test_login_url_and_code_for_the_second_kite_account(monkeypatch):
    headers, me = _two_accounts("kite2", broker="zerodha")
    res = client.get("/api/broker/zerodha/login-url?account_label=second", headers=headers)
    assert res.status_code == 200 and "api_key=k-second" in res.json()["authorization_url"]
    seen = _fake_broker(monkeypatch)
    res = client.post("/api/broker/zerodha/login-code?account_label=second", headers=headers, json={"code": "request_token=abc"})
    assert res.status_code == 200, res.text
    assert res.json()["account_label"] == "second"
    assert [c.api_key for c in seen] == ["k-second"]
    rows = _rows(me["tenant_id"], "zerodha")
    assert rows["second"][0] == "VALID" and rows["primary"][0] == "UNKNOWN"
