"""C12 support: the scrubber, the Help centre articles, "Report a problem", the ticket list and the contact link."""
import asyncio
import json
import re
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from app.db.models import AuditLogRecord, FeedbackRecord, MarketHolidayRecord, PlatformControlRecord, User
from app.main import app
from app.market_data.calendar import IST, SessionStatus
from app.observability import recent_errors
from app.platform import controls
from app.platform.flag_catalog import FLAG_INFO
from app.support import desk
from app.support.desk_routes import report_rate_limit
from app.support.scrub import REMOVED_EMAIL, REMOVED_NUMBER, REMOVED_TOKEN, REMOVED_VALUE, scrub
from app.beta.routes import feedback_rate_limit
from tests.test_admin_api import _admin, _owner
from tests.test_auth_api import _session_factory, client

app.dependency_overrides[report_rate_limit] = lambda: None
app.dependency_overrides[feedback_rate_limit] = lambda: None

ADMIN = "/api/admin/support"
OK_REPORT = {"message": "The chart does not load", "app_version": "0.1.0+abc1234", "screen": "/charts"}


def _run(coro):
    return asyncio.run(coro)


def _flags(*names: str) -> None:
    async def go():
        async with _session_factory() as session:
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": {n: {"on": True, "tenants": []} for n in names}}, None)
            await session.commit()
    _run(go())


@pytest.fixture
def desk_on():
    _flags("ui_v2", "help_centre")
    yield
    _flags()


@pytest.fixture(scope="module")
def admin_headers():
    headers, _ = _admin("c12-admin@example.com")
    return headers


# --- the scrubber -----------------------------------------------------------------------------------------------------

def test_scrub_removes_tokens_and_keys():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.abcDEF123_-xyz"
    out = scrub(f"my session {jwt} died")
    assert jwt not in out and REMOVED_TOKEN in out
    out = scrub("Authorization: Bearer abc.def-ghi_jkl123456")
    assert "abc.def-ghi_jkl123456" not in out
    # Fake keys are built at runtime so no secret-shaped literal sits in the repo (the secret scan).
    fake_key = "".join(["AbCdEf", "1234567890", "GhIjKl"])
    out = scrub(f"api key = {fake_key} and the secret: s3cr3t!")
    assert fake_key not in out and "s3cr3t!" not in out and "api key" in out and REMOVED_VALUE in out
    assert "hunter2" not in scrub("my password is hunter2")
    assert "482913" not in scrub("the otp: 482913 did not work")
    opaque = "".join(["x7Rk9QpL", "2mN4vB8c", "Z1aS6dF3"])
    assert opaque not in scrub(f"key {opaque} pasted")


def test_scrub_cuts_query_strings_of_links():
    out = scrub("this link fails https://aialgodesk.tech/account?reset=tok123abc#frag ok")
    assert out == "this link fails https://aialgodesk.tech/account ok"


def test_scrub_removes_other_emails_but_keeps_the_reporters_own():
    out = scrub("I am me@example.com, my friend friend@example.org sees it too", own_email="Me@Example.com")
    assert "me@example.com" in out and "friend@example.org" not in out and REMOVED_EMAIL in out
    assert "me@example.com" not in scrub("me@example.com")


def test_scrub_removes_long_numbers_and_ids():
    for secret in ("4111 1111 1111 1111", "9820000000", "+91 98200 00000", "1234-5678-9012", "ABCDE1234F"):
        out = scrub(f"value {secret} here")
        assert secret not in out and REMOVED_NUMBER in out, out


def test_scrub_leaves_plain_text_alone():
    for text in ("The option chain is slow after 10:45 and the page shows error 500.",
                 "My password reset link did not arrive", "Sign-in with the token from my authenticator fails",
                 "Charts take 3 seconds to load on 15min bars"):
        assert scrub(text) == text
    assert scrub(None) == "" and scrub(42) == ""


# --- flags ------------------------------------------------------------------------------------------------------------

def test_flag_is_registered_off_by_default_and_needs_ui_v2():
    assert "help_centre" in controls.FEATURE_FLAGS and "help_centre" in controls.DEFAULT_OFF_FLAGS
    assert FLAG_INFO["help_centre"].depends_on == ("ui_v2",)


def test_user_routes_are_404_while_a_flag_is_off():
    headers, _ = _owner("c12-off@example.com")
    for flags in ((), ("ui_v2",), ("help_centre",)):
        _flags(*flags)
        assert client.get("/api/support/help", headers=headers).status_code == 404
        assert client.get("/api/support/contact", headers=headers).status_code == 404
        assert client.post("/api/support/report", headers=headers, json=OK_REPORT).status_code == 404
    _flags()
    assert client.get("/api/support/help").status_code in (401, 403)


# --- Help centre ------------------------------------------------------------------------------------------------------

SECURITY_NAMES = re.compile(r"\b(NIFTY|BANKNIFTY|SENSEX|FINNIFTY|RELIANCE|INFY|TCS|HDFC\w*|ICICI\w*|SBIN)\b", re.I)
PRICE = re.compile(r"(₹|\bRs\.?\s?\d|\bINR\s?\d)")


def test_help_articles_are_how_to_text_without_security_names_or_prices(desk_on):
    headers, _ = _owner("c12-help@example.com")
    r = client.get("/api/support/help", headers=headers)
    assert r.status_code == 200, r.text
    articles = r.json()["articles"]
    assert len(articles) >= 20
    for a in articles:
        assert a["title"] and a["text"] and a["page"].startswith("/")
        blob = f"{a['title']} {a['text']} {a['keywords']}"
        assert not SECURITY_NAMES.search(blob), a["title"]
        assert not PRICE.search(blob), a["title"]
    # The entries about the legal pages only while `legal_pages` is on (it is off here).
    assert all(not a["page"].startswith("/legal/") for a in articles)


# --- Report a problem -------------------------------------------------------------------------------------------------

def _row(feedback_id: int):
    async def go():
        async with _session_factory() as session:
            row = await session.get(FeedbackRecord, feedback_id)
            state = await session.get(PlatformControlRecord, desk.ticket_key(feedback_id))
            return row, json.loads(state.value_json) if state else None
    return _run(go())


def test_report_stores_the_scrubbed_message_and_the_context(desk_on):
    headers, me = _owner("c12-report@example.com")
    recent_errors.clear()
    recent_errors.record("req-c12-1", "GET", "/api/positions", 503, 12.0)
    now = datetime.now(timezone.utc)
    body = {**OK_REPORT, "message": "It broke. My token: abc123secret and friend@example.net saw it. me: c12-report@example.com",
            "screen": "/charts?symbol=X&reset=tok#y", "client_time": now.isoformat(), "error_id": "req-c12-1",
            "error_screen": "/positions?x=1", "error_at": (now - timedelta(minutes=2)).isoformat()}
    r = client.post("/api/support/report", headers={**headers, "User-Agent": "Agent/1"}, json=body)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["status"] == "open" and out["attached"]["error_id"] == "req-c12-1" and out["attached"]["error_screen"] == "/positions"
    assert "error_route" not in json.dumps(out)            # the server's error details stay in the admin view
    row, state = _row(out["id"])
    assert (row.tenant_id, row.user_id) == (me["tenant_id"], me["id"])
    assert row.screen == "/charts" and "abc123secret" not in row.message and "friend@example.net" not in row.message
    assert "c12-report@example.com" in row.message
    assert state["kind"] == "problem" and state["status"] == "open"
    ctx = state["context"]
    assert ctx["error_id"] == "req-c12-1" and ctx["error_route"] == "/api/positions" and ctx["error_status"] == 503 and ctx["error_found"]
    assert ctx["error_screen"] == "/positions" and ctx["client_time"]


def test_report_refuses_any_other_field_and_bad_values(desk_on):
    headers, _ = _owner("c12-report-bad@example.com")
    for extra in ({"form": {"password": "x"}}, {"tenant_id": 99}, {"user_id": 1}, {"headers": {"Authorization": "x"}}):
        assert client.post("/api/support/report", headers=headers, json={**OK_REPORT, **extra}).status_code == 422
    assert client.post("/api/support/report", headers=headers, json={**OK_REPORT, "error_id": "bad id <x>"}).status_code == 422
    assert client.post("/api/support/report", headers=headers, json={**OK_REPORT, "screen": "charts"}).status_code == 422
    assert client.post("/api/support/report", headers=headers, json={**OK_REPORT, "message": "  "}).status_code == 422


def test_report_drops_a_time_from_a_wrong_device_clock_and_an_unknown_error(desk_on):
    headers, _ = _owner("c12-report-clock@example.com")
    recent_errors.clear()
    body = {**OK_REPORT, "client_time": "2001-01-01T00:00:00Z", "error_id": "not-in-store"}
    r = client.post("/api/support/report", headers=headers, json=body)
    assert r.status_code == 201, r.text
    _, state = _row(r.json()["id"])
    assert state["context"]["client_time"] is None and state["context"]["error_found"] is False


def test_send_feedback_is_scrubbed_too():
    headers, _ = _owner("c12-fb@example.com")
    r = client.post("/api/feedback", headers=headers, json={"message": "password: hunter2 and other@example.net", "app_version": "dev", "screen": "/"})
    assert r.status_code == 201
    row, _ = _row(r.json()["id"])
    assert "hunter2" not in row.message and "other@example.net" not in row.message


# --- the admin ticket list ----------------------------------------------------------------------------------------------

def test_admin_routes_are_super_admin_only():
    owner, _ = _owner("c12-owner@example.com")
    for method, path in (("get", f"{ADMIN}/tickets"), ("put", f"{ADMIN}/tickets/1"), ("get", f"{ADMIN}/settings"), ("put", f"{ADMIN}/settings")):
        kwargs = {"json": {}} if method == "put" else {}
        assert getattr(client, method)(path, **kwargs).status_code in (401, 403)
        assert getattr(client, method)(path, headers=owner, **kwargs).status_code == 403


def _audit(event: str):
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(AuditLogRecord.detail).where(AuditLogRecord.event == event)))
    return _run(go())


def test_ticket_list_status_assignee_and_targets(desk_on, admin_headers):
    headers, _ = _owner("c12-tickets@example.com")
    old = client.post("/api/feedback", headers=headers, json={"message": "old style feedback", "app_version": "dev", "screen": "/"}).json()["id"]
    new = client.post("/api/support/report", headers=headers, json=OK_REPORT).json()["id"]

    listing = client.get(f"{ADMIN}/tickets", headers=admin_headers).json()
    by_id = {t["id"]: t for t in listing["tickets"]}
    assert by_id[old]["kind"] == "feedback" and by_id[old]["status"] == "open"
    assert by_id[new]["kind"] == "problem" and by_id[new]["status"] == "open"
    assert listing["targets"] == {"first_response_hours": None, "resolve_hours": None}     # nothing hard-coded
    assert by_id[new]["first_response_due_at"] is None and by_id[new]["first_response_late"] is None

    me = client.get("/api/auth/me", headers=admin_headers).json()
    assert me["id"] in [p["id"] for p in listing["assignees"]]
    r = client.put(f"{ADMIN}/tickets/{new}", headers=admin_headers, json={"status": "answered", "assignee_id": me["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "answered" and r.json()["assignee_id"] == me["id"] and r.json()["first_response_at"]
    r = client.put(f"{ADMIN}/tickets/{new}", headers=admin_headers, json={"status": "closed"})
    assert r.json()["closed_at"] and r.json()["assignee_id"] == me["id"]          # assignee untouched when not sent
    r = client.put(f"{ADMIN}/tickets/{new}", headers=admin_headers, json={"assignee_id": None})
    assert r.json()["assignee_id"] is None and r.json()["status"] == "closed"
    r = client.put(f"{ADMIN}/tickets/{new}", headers=admin_headers, json={"status": "open"})
    assert r.json()["closed_at"] is None and r.json()["first_response_at"]           # the first response stays recorded
    assert any(f"ticket #{new} status open->answered" in d for d in _audit("support_ticket_updated"))

    closed = client.get(f"{ADMIN}/tickets?ticket_status=open", headers=admin_headers).json()["tickets"]
    assert new in [t["id"] for t in closed]
    assert client.get(f"{ADMIN}/tickets?ticket_status=nope", headers=admin_headers).status_code == 400


def test_ticket_update_refusals(desk_on, admin_headers):
    headers, plain = _owner("c12-refuse@example.com")
    new = client.post("/api/support/report", headers=headers, json=OK_REPORT).json()["id"]
    assert client.put(f"{ADMIN}/tickets/{new}", headers=admin_headers, json={"status": "done"}).status_code == 400
    assert client.put(f"{ADMIN}/tickets/{new}", headers=admin_headers, json={"assignee_id": plain["id"]}).status_code == 400
    assert client.put(f"{ADMIN}/tickets/999999", headers=admin_headers, json={"status": "closed"}).status_code == 404
    assert client.put(f"{ADMIN}/tickets/{new}", headers=admin_headers, json={"status": "closed", "x": 1}).status_code == 422


def test_desk_settings_are_empty_by_default_validated_and_audited_without_the_number(admin_headers):
    r = client.get(f"{ADMIN}/settings", headers=admin_headers)
    assert r.json() == {"whatsapp": "", "email": "", "first_response_hours": None, "resolve_hours": None}
    r = client.put(f"{ADMIN}/settings", headers=admin_headers,
                   json={"whatsapp": "+91 98200-00000", "email": "help@example.com", "first_response_hours": 4, "resolve_hours": 48})
    assert r.status_code == 200, r.text
    assert r.json() == {"whatsapp": "+919820000000", "email": "help@example.com", "first_response_hours": 4.0, "resolve_hours": 48.0}
    assert client.put(f"{ADMIN}/settings", headers=admin_headers, json={"whatsapp": "12"}).status_code == 400
    assert client.put(f"{ADMIN}/settings", headers=admin_headers, json={"email": "not-an-email"}).status_code == 400
    assert client.put(f"{ADMIN}/settings", headers=admin_headers, json={"first_response_hours": 0}).status_code == 400
    assert client.put(f"{ADMIN}/settings", headers=admin_headers, json={"resolve_hours": 10_000}).status_code == 400
    r = client.put(f"{ADMIN}/settings", headers=admin_headers, json={"resolve_hours": None})
    assert r.json()["resolve_hours"] is None and r.json()["first_response_hours"] == 4.0    # only the field sent changes
    details = " ".join(_audit("support_settings_updated"))
    assert "whatsapp set" in details and "9820000000" not in details and "help@example.com" not in details
    client.put(f"{ADMIN}/settings", headers=admin_headers, json={"whatsapp": "", "email": "", "first_response_hours": None})


def test_timing_against_the_targets():
    created = datetime(2026, 10, 20, 4, 0, tzinfo=timezone.utc)
    targets = {"first_response_hours": 2.0, "resolve_hours": 24.0}
    open_state = {"first_response_at": None, "closed_at": None}
    t = desk.timing(created, open_state, targets, now=created + timedelta(hours=1))
    assert t["first_response_late"] is False and t["resolve_late"] is False
    assert t["first_response_due_at"] == (created + timedelta(hours=2)).isoformat()
    t = desk.timing(created, open_state, targets, now=created + timedelta(hours=3))
    assert t["first_response_late"] is True and t["resolve_late"] is False
    answered = {"first_response_at": (created + timedelta(hours=1)).isoformat(), "closed_at": (created + timedelta(hours=30)).isoformat()}
    t = desk.timing(created, answered, targets, now=created + timedelta(days=5))
    assert t["first_response_late"] is False and t["resolve_late"] is True
    t = desk.timing(created, open_state, {"first_response_hours": None, "resolve_hours": None})
    assert t == {"first_response_due_at": None, "first_response_late": None, "resolve_due_at": None, "resolve_late": None}


def test_list_counts_late_open_tickets(desk_on, admin_headers):
    client.put(f"{ADMIN}/settings", headers=admin_headers, json={"first_response_hours": 1})
    headers, _ = _owner("c12-late@example.com")
    client.post("/api/support/report", headers=headers, json=OK_REPORT)

    async def go():
        async with _session_factory() as session:
            return await desk.list_tickets(session, now=datetime.now(timezone.utc) + timedelta(hours=2))
    listing = _run(go())
    assert listing["late"] >= 1 and listing["targets"]["first_response_hours"] == 1.0
    client.put(f"{ADMIN}/settings", headers=admin_headers, json={"first_response_hours": None})


def test_a_reused_message_id_never_inherits_an_old_ticket(desk_on, admin_headers):
    headers, _ = _owner("c12-reuse@example.com")
    fid = client.post("/api/support/report", headers=headers, json=OK_REPORT).json()["id"]
    client.put(f"{ADMIN}/tickets/{fid}", headers=admin_headers, json={"status": "closed"})

    async def stale():
        async with _session_factory() as session:
            row = await session.get(PlatformControlRecord, desk.ticket_key(fid))
            value = json.loads(row.value_json)
            value["for"] = f"{fid}:999:2001-01-01T00:00:00+00:00"
            row.value_json = json.dumps(value)
            await session.commit()
    _run(stale())
    listing = client.get(f"{ADMIN}/tickets", headers=admin_headers).json()
    ticket = next(t for t in listing["tickets"] if t["id"] == fid)
    assert ticket["status"] == "open" and ticket["kind"] == "feedback"


def test_erasure_removes_the_ticket_state(desk_on):
    from app.retention.service import erase_user
    headers, me = _owner("c12-erase@example.com")
    fid = client.post("/api/support/report", headers=headers, json=OK_REPORT).json()["id"]

    async def go():
        async with _session_factory() as session:
            user = await session.get(User, me["id"])
            user.is_active = False
            await erase_user(session, user, actor_id=None)
            await session.commit()
            return await session.get(PlatformControlRecord, desk.ticket_key(fid))
    assert _run(go()) is None


# --- the contact link ---------------------------------------------------------------------------------------------------

def _set_contact(whatsapp: str, email: str) -> None:
    async def go():
        async with _session_factory() as session:
            await session.merge(PlatformControlRecord(key=desk.SETTINGS_KEY, value_json=json.dumps({"whatsapp": whatsapp, "email": email})))
            await session.commit()
    _run(go())


def _contact(now):
    async def go():
        async with _session_factory() as session:
            return await desk.contact(session, now)
    return _run(go())


def test_contact_link_only_in_market_hours_and_only_when_set():
    tuesday_open = datetime(2026, 10, 20, 11, 0, tzinfo=IST)
    _set_contact("", "")
    out = _contact(tuesday_open)
    assert out["configured"] is False and out["whatsapp_url"] is None and out["email"] is None and out["market_open"] is True

    _set_contact("+919820000000", "help@example.com")
    out = _contact(tuesday_open)
    assert out["whatsapp_url"] == "https://wa.me/919820000000" and out["email"] == "help@example.com"
    for closed in (datetime(2026, 10, 20, 8, 0, tzinfo=IST), datetime(2026, 10, 20, 16, 0, tzinfo=IST), datetime(2026, 10, 25, 11, 0, tzinfo=IST)):
        out = _contact(closed)
        assert out["market_open"] is False and out["whatsapp_url"] is None and out["email"] is None and out["next_open"]

    async def holiday():
        async with _session_factory() as session:
            session.add(MarketHolidayRecord(exchange="NSE", holiday_date=tuesday_open.date(), description="test holiday"))
            await session.commit()
    _run(holiday())
    assert _contact(tuesday_open)["whatsapp_url"] is None              # the app's own holiday calendar closes it too

    async def unholiday():
        async with _session_factory() as session:
            await session.execute(delete(MarketHolidayRecord).where(MarketHolidayRecord.description == "test holiday"))
            await session.commit()
    _run(unholiday())
    _set_contact("", "")


def test_contact_route(desk_on, monkeypatch):
    from app.market_data import calendar

    async def open_now(session, now=None, exchange="NSE"):
        return SessionStatus(True, "open", None)
    monkeypatch.setattr(calendar, "market_session_status", open_now)
    _set_contact("+919820000000", "")
    headers, _ = _owner("c12-contact@example.com")
    r = client.get("/api/support/contact", headers=headers)
    assert r.status_code == 200 and r.json()["whatsapp_url"] == "https://wa.me/919820000000" and r.json()["email"] is None
    _set_contact("", "")
