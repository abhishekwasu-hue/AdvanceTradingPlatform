"""PW6: the closed-beta cohort gate (invite codes + cap), PWA install/update metrics and "Send feedback"."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from prometheus_client import REGISTRY
from sqlalchemy import select

from app.admin import bootstrap
from app.beta import invites
from app.beta.routes import clean_screen, feedback_rate_limit
from app.core import config as app_config
from app.core import rate_limit
from app.db.models import BetaInviteCodeRecord, FeedbackRecord
from app.main import app
from app.platform import controls
from tests.test_admin_api import _admin
from tests.test_auth_api import _register, _session_factory, client

app.dependency_overrides[feedback_rate_limit] = lambda: None

PASSWORD = "S3cur3Pass!"
ADMIN = "/api/admin/beta"


def _run(coro):
    return asyncio.run(coro)


def _headers(email: str) -> dict:
    return {"Authorization": f"Bearer {_register(email)}"}


def _set_gate(on: bool) -> None:
    async def go():
        async with _session_factory() as session:
            stored = (await controls._get(session, controls.KEY_FEATURE_FLAGS)).get("flags") or {}
            stored.pop("beta_cohort_gate", None)
            if on:
                stored["beta_cohort_gate"] = {"on": True, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": stored}, None)
            await session.commit()
    _run(go())


@pytest.fixture(scope="module")
def admin_headers():
    headers, _ = _admin("pw6-admin@example.com")
    return headers


@pytest.fixture
def gate(admin_headers):
    _set_gate(True)
    yield admin_headers
    _set_gate(False)


def _new_code(headers, **body) -> dict:
    r = client.post(f"{ADMIN}/invite-codes", headers=headers, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _signup(email: str, code=None):
    body = {"email": email, "password": PASSWORD}
    if code is not None:
        body["invite_code"] = code
    return client.post("/api/auth/register", json=body)


def _refusal(response) -> str:
    assert response.status_code == 403, response.text
    return response.json()["detail"]["code"]


# --- the flag ---------------------------------------------------------------------------------------------------------

def test_flag_is_registered_and_off_by_default():
    assert "beta_cohort_gate" in controls.FEATURE_FLAGS and "beta_cohort_gate" in controls.DEFAULT_OFF_FLAGS
    headers = _headers("pw6-flag-owner@example.com")
    assert client.get("/api/system/features", headers=headers).json()["beta_cohort_gate"] is False
    assert app_config.BETA_COHORT_CAP == 500


def test_flag_off_register_is_unchanged():
    assert _signup("pw6-off-plain@example.com").status_code == 201
    # An invite code sent while the gate is off is ignored: not checked, not spent.
    assert _signup("pw6-off-junk@example.com", code="not-a-code").status_code == 201
    assert _signup("pw6-off-plain@example.com").status_code == 409


# --- the gate ---------------------------------------------------------------------------------------------------------

def test_gate_on_requires_a_valid_code(gate):
    assert _refusal(_signup("pw6-nocode@example.com")) == "beta_invite_required"
    assert _refusal(_signup("pw6-blank@example.com", code="  - ")) == "beta_invite_required"
    assert _refusal(_signup("pw6-garbage@example.com", code="hello")) == "beta_invite_invalid"
    assert _refusal(_signup("pw6-unknown@example.com", code=invites.new_code())) == "beta_invite_invalid"

    issued = _new_code(gate, note="first wave")
    assert len(issued["code"]) == 19 and issued["used_count"] == 0 and issued["state"] == "active"
    # Dashes, spaces and case do not matter.
    typed = issued["code"].replace("-", " ").lower()
    r = _signup("pw6-ok@example.com", code=typed)
    assert r.status_code == 201, r.text
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}).json()["email"] == "pw6-ok@example.com"
    # Single use by default: spent now.
    assert _refusal(_signup("pw6-ok-2@example.com", code=issued["code"])) == "beta_invite_used"


def test_used_count_increments_and_failed_signup_does_not_spend(gate):
    issued = _new_code(gate, max_uses=3)
    assert _signup("pw6-multi-1@example.com", code=issued["code"]).status_code == 201
    assert _signup("pw6-multi-2@example.com", code=issued["code"]).status_code == 201
    # A sign-up refused after the code check (weak password) leaves the code untouched.
    weak = client.post("/api/auth/register", json={"email": "pw6-multi-weak@example.com", "password": "password123", "invite_code": issued["code"]})
    assert weak.status_code == 400
    # So does a duplicate email.
    assert _signup("pw6-multi-1@example.com", code=issued["code"]).status_code == 409
    row = next(c for c in client.get(f"{ADMIN}/invite-codes", headers=gate).json() if c["id"] == issued["id"])
    assert row["used_count"] == 2 and row["state"] == "active"
    assert _signup("pw6-multi-3@example.com", code=issued["code"]).status_code == 201
    row = next(c for c in client.get(f"{ADMIN}/invite-codes", headers=gate).json() if c["id"] == issued["id"])
    assert row["used_count"] == 3 and row["state"] == "exhausted"
    assert _refusal(_signup("pw6-multi-4@example.com", code=issued["code"])) == "beta_invite_used"


def test_expired_and_revoked_codes_are_refused(gate):
    expiring = _new_code(gate, expires_in_days=1)

    async def backdate():
        async with _session_factory() as session:
            row = await session.get(BetaInviteCodeRecord, expiring["id"])
            row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            await session.commit()
    _run(backdate())
    assert _refusal(_signup("pw6-expired@example.com", code=expiring["code"])) == "beta_invite_expired"

    revoked = _new_code(gate, max_uses=5)
    r = client.post(f"{ADMIN}/invite-codes/{revoked['id']}/revoke", headers=gate)
    assert r.status_code == 200 and r.json()["state"] == "revoked" and r.json()["revoked_at"]
    assert _refusal(_signup("pw6-revoked@example.com", code=revoked["code"])) == "beta_invite_revoked"
    assert client.post(f"{ADMIN}/invite-codes/999999/revoke", headers=gate).status_code == 404


def test_cohort_cap_is_enforced(gate, monkeypatch):
    issued = _new_code(gate, max_uses=10)
    used = client.get(f"{ADMIN}/cohort", headers=gate).json()["used"]
    monkeypatch.setattr(app_config, "BETA_COHORT_CAP", used + 1)
    cohort = client.get(f"{ADMIN}/cohort", headers=gate).json()
    assert cohort == {"gate_on": True, "cap": used + 1, "used": used, "remaining": 1}
    assert _signup("pw6-cap-last@example.com", code=issued["code"]).status_code == 201
    assert _refusal(_signup("pw6-cap-over@example.com", code=issued["code"])) == "beta_cohort_full"
    assert client.get(f"{ADMIN}/cohort", headers=gate).json()["remaining"] == 0
    # The refused sign-up did not spend a use.
    row = next(c for c in client.get(f"{ADMIN}/invite-codes", headers=gate).json() if c["id"] == issued["id"])
    assert row["used_count"] == 1


def test_configured_super_admin_needs_no_code(gate, monkeypatch):
    monkeypatch.setattr(bootstrap, "SUPER_ADMIN_EMAILS", {"pw6-ops@example.com"})
    assert _signup("pw6-ops@example.com").status_code == 201


def test_only_super_admin_manages_codes(admin_headers):
    owner = _headers("pw6-owner-only@example.com")
    issued = _new_code(admin_headers)
    for method, path in (("post", f"{ADMIN}/invite-codes"), ("get", f"{ADMIN}/invite-codes"), ("post", f"{ADMIN}/invite-codes/{issued['id']}/revoke"),
                         ("get", f"{ADMIN}/cohort"), ("get", f"{ADMIN}/feedback")):
        kwargs = {"json": {}} if method == "post" else {}
        assert getattr(client, method)(path, **kwargs).status_code in (401, 403)
        assert getattr(client, method)(path, headers=owner, **kwargs).status_code == 403


def test_plain_code_is_never_stored_or_listed(admin_headers):
    issued = _new_code(admin_headers, note="press")
    listing = client.get(f"{ADMIN}/invite-codes", headers=admin_headers)
    assert listing.status_code == 200
    assert issued["code"] not in listing.text and issued["code"].replace("-", "") not in listing.text
    assert all("code" not in c and "code_hash" not in c for c in listing.json())

    async def stored():
        async with _session_factory() as session:
            return await session.get(BetaInviteCodeRecord, issued["id"])
    row = _run(stored())
    assert row.code_hash == invites.hash_code(issued["code"]) and len(row.code_hash) == 64
    assert issued["code"].replace("-", "") not in row.code_hash and row.note == "press"


def test_invite_code_helpers():
    code = invites.new_code()
    assert len(code) == 19 and code.count("-") == 3
    assert set(code.replace("-", "")) <= set(invites.ALPHABET)
    assert invites.normalise(" ab-cd ef ") == "ABCDEF"
    assert invites.hash_code(code) == invites.hash_code(code.lower().replace("-", " "))


def test_invite_create_body_is_bounded(admin_headers):
    assert client.post(f"{ADMIN}/invite-codes", headers=admin_headers, json={"max_uses": 0}).status_code == 422
    assert client.post(f"{ADMIN}/invite-codes", headers=admin_headers, json={"max_uses": 1001}).status_code == 422
    assert client.post(f"{ADMIN}/invite-codes", headers=admin_headers, json={"code": "MYOWNCODE"}).status_code == 422


# --- PWA install/update metrics ----------------------------------------------------------------------------------------

def _count(event: str, platform: str) -> float:
    return REGISTRY.get_sample_value("atp_pwa_events_total", {"event": event, "platform": platform}) or 0.0


def test_pwa_events_increment_counters_and_refuse_unknown_values():
    rate_limit.reset("pwa_events")
    before = _count("installed", "android")
    assert client.post("/api/pwa/events", json={"event": "installed", "platform": "android"}).status_code == 204
    assert _count("installed", "android") == before + 1
    for event in ("launched_standalone", "update_applied", "install_prompt_shown", "install_prompt_accepted", "install_prompt_dismissed"):
        b = _count(event, "desktop")
        assert client.post("/api/pwa/events", json={"event": event, "platform": "desktop"}).status_code == 204
        assert _count(event, "desktop") == b + 1
    other = _count("update_applied", "other")
    assert client.post("/api/pwa/events", json={"event": "update_applied"}).status_code == 204       # platform defaults to other
    assert _count("update_applied", "other") == other + 1

    assert client.post("/api/pwa/events", json={"event": "uninstalled", "platform": "android"}).status_code == 422
    assert client.post("/api/pwa/events", json={"event": "installed", "platform": "windows-11"}).status_code == 422
    # No identifiers or free text ride along.
    assert client.post("/api/pwa/events", json={"event": "installed", "platform": "ios", "user_id": 1}).status_code == 422
    assert client.post("/api/pwa/events", json={"event": "installed", "platform": "ios", "note": "hi"}).status_code == 422
    assert _count("uninstalled", "android") == 0.0
    rate_limit.reset("pwa_events")


def test_pwa_events_are_rate_limited():
    rate_limit.reset("pwa_events")
    codes = [client.post("/api/pwa/events", json={"event": "launched_standalone", "platform": "ios"}).status_code for _ in range(31)]
    assert codes[:30] == [204] * 30 and codes[30] == 429
    rate_limit.reset("pwa_events")


# --- Send feedback -----------------------------------------------------------------------------------------------------

def test_clean_screen():
    assert clean_screen("/option-chain?token=secret#frag") == "/option-chain"
    assert clean_screen("/copilot/study") == "/copilot/study"
    for bad in ("option-chain", "https://evil.example/x", "", "/" + "a" * 200, "/a b", "/<script>"):
        with pytest.raises(ValueError):
            clean_screen(bad)


def test_feedback_is_stored_sanitised():
    headers = _headers("pw6-fb-store@example.com")
    me = client.get("/api/auth/me", headers=headers).json()
    r = client.post("/api/feedback", headers={**headers, "User-Agent": "Agent/" + "x" * 500},
                    json={"message": "  The option chain is slow  ", "app_version": "0.1.0+abc1234", "screen": "/option-chain?reset=tok123#top"})
    assert r.status_code == 201, r.text

    async def stored():
        async with _session_factory() as session:
            return await session.get(FeedbackRecord, r.json()["id"])
    row = _run(stored())
    assert (row.tenant_id, row.user_id) == (me["tenant_id"], me["id"])
    assert row.message == "The option chain is slow" and row.app_version == "0.1.0+abc1234" and row.screen == "/option-chain"
    assert row.user_agent is not None and len(row.user_agent) == 200 and "tok123" not in (row.screen + row.message)



def test_erasure_removes_the_users_feedback():
    from app.db.models import User
    from app.retention.service import erase_user
    headers = _headers("pw6-fb-erase@example.com")
    me = client.get("/api/auth/me", headers=headers).json()
    r = client.post("/api/feedback", headers=headers, json={"message": "My phone is 98200 00000", "app_version": "dev", "screen": "/"})
    assert r.status_code == 201, r.text

    async def go():
        async with _session_factory() as session:
            user = await session.get(User, me["id"])
            user.is_active = False
            await erase_user(session, user, actor_id=None)
            await session.commit()
            return list(await session.scalars(select(FeedbackRecord).where(FeedbackRecord.user_id == me["id"])))
    assert _run(go()) == []

def test_feedback_validation():
    headers = _headers("pw6-fb-bad@example.com")
    ok = {"message": "hi", "app_version": "dev", "screen": "/"}
    assert client.post("/api/feedback", json=ok).status_code in (401, 403)
    assert client.post("/api/feedback", headers=headers, json=ok).status_code == 201
    assert client.post("/api/feedback", headers=headers, json={**ok, "message": "x" * 2000}).status_code == 201
    assert client.post("/api/feedback", headers=headers, json={**ok, "message": "x" * 2001}).status_code == 422
    assert client.post("/api/feedback", headers=headers, json={**ok, "message": "   "}).status_code == 422
    assert client.post("/api/feedback", headers=headers, json={**ok, "screen": "option-chain"}).status_code == 422
    assert client.post("/api/feedback", headers=headers, json={**ok, "screen": "/" + "a" * 200}).status_code == 422
    assert client.post("/api/feedback", headers=headers, json={**ok, "app_version": "v1 <script>"}).status_code == 422
    assert client.post("/api/feedback", headers=headers, json={**ok, "app_version": "x" * 41}).status_code == 422
    # The tenant and the user come from the session only.
    assert client.post("/api/feedback", headers=headers, json={**ok, "tenant_id": 1}).status_code == 422
    assert client.post("/api/feedback", headers=headers, json={**ok, "user_id": 1}).status_code == 422


def test_feedback_listing_is_super_admin_only_and_filters_by_tenant(admin_headers):
    a = _headers("pw6-fb-a@example.com")
    b = _headers("pw6-fb-b@example.com")
    tenant_a = client.get("/api/auth/me", headers=a).json()["tenant_id"]
    tenant_b = client.get("/api/auth/me", headers=b).json()["tenant_id"]
    assert client.post("/api/feedback", headers=a, json={"message": "from A", "app_version": "dev", "screen": "/positions"}).status_code == 201
    assert client.post("/api/feedback", headers=b, json={"message": "from B", "app_version": "dev", "screen": "/orders"}).status_code == 201

    # A tenant owner cannot read anyone's feedback, their own organisation's included.
    assert client.get(f"{ADMIN}/feedback", headers=a).status_code == 403
    assert client.get(f"{ADMIN}/feedback?tenant_id={tenant_a}", headers=a).status_code == 403

    only_a = client.get(f"{ADMIN}/feedback?tenant_id={tenant_a}", headers=admin_headers).json()
    assert [f["message"] for f in only_a] == ["from A"] and only_a[0]["email"] == "pw6-fb-a@example.com"
    assert set(only_a[0]) == {"id", "tenant_id", "user_id", "email", "message", "app_version", "screen", "user_agent", "created_at"}
    everything = client.get(f"{ADMIN}/feedback", headers=admin_headers).json()
    assert {"from A", "from B"} <= {f["message"] for f in everything}
    assert {f["tenant_id"] for f in everything if f["message"] in ("from A", "from B")} == {tenant_a, tenant_b}

    async def count_b():
        async with _session_factory() as session:
            return len((await session.scalars(select(FeedbackRecord).where(FeedbackRecord.tenant_id == tenant_b))).all())
    assert _run(count_b()) == 1
