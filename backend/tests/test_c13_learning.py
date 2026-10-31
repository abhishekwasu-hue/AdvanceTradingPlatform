"""Readiness C13 (learning and growth): the Learn section, the public marketing pages, the first-week onboarding e-mails,
the referral flow and the admin growth funnel.

Covered: the four new flags are registered OFF with their catalogue entries; Learn and the referral routes answer 404
naming the flag while it is off; the content guard over every lesson, page and e-mail line (and that it catches a bad
line); the operator's video links (https only, known lessons, admin + two-factor, audited without the addresses); the
public pages without a sign-in, read from the global flag; the onboarding e-mails through a fake mail server: nothing
while the flag, the mailer or the public address is missing, each step once at its hour, a step already taken is skipped,
only verified accounts in their first week, a failed send is retried, the unsubscribe page / one-click POST stops the rest
and a bad token is refused; the referral code (created once), the claim (once, new accounts of another organisation only)
and the counts; the growth funnel's order and its "active in week 1" step.
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import List

import pytest

from app.core import config
from app.db.models import AuditLogRecord, BrokerCredentialRecord, LoginEventRecord, StrategyDeploymentRecord, User
from app.growth import onboarding, referral
from app.idea_library.content import guard
from app.learn import content
from app.notifications import mailer
from app.platform import controls
from tests.test_admin_api import _admin
from tests.test_amw2_watchlists import _set_flag
from tests.test_auth_api import _register, _session_factory, client

run = asyncio.run
UTC = timezone.utc
# Far from every other suite's dates, so the onboarding window holds only this file's accounts.
SIGNED = datetime(2033, 5, 10, 6, 0, tzinfo=UTC)


def _who(email: str):
    headers = {"Authorization": f"Bearer {_register(email)}"}
    me = client.get("/api/auth/me", headers=headers).json()
    return headers, me


def _on(me, *flags):
    for name in flags:
        _set_flag(name, me["tenant_id"], on=True)


def _global(name: str, on: bool) -> None:
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            flags[name] = {"on": on, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    run(go())


def _edit_user(user_id: int, **fields):
    async def go():
        async with _session_factory() as session:
            user = await session.get(User, user_id)
            for k, v in fields.items():
                setattr(user, k, v)
            await session.commit()
    run(go())


def _add(*rows):
    async def go():
        async with _session_factory() as session:
            session.add_all(list(rows))
            await session.commit()
    run(go())


def _audit(event: str) -> List[str]:
    async def go():
        async with _session_factory() as session:
            from sqlalchemy import select
            return list(await session.scalars(select(AuditLogRecord.detail).where(AuditLogRecord.event == event)))
    return run(go())


# --- flags ----------------------------------------------------------------------------------------------------------------

def test_the_new_flags_are_registered_off_with_catalogue_entries():
    from app.platform.flag_catalog import FLAG_INFO
    for name in ("learn", "marketing_site", "onboarding_emails", "referrals"):
        assert name in controls.FEATURE_FLAGS and name in controls.DEFAULT_OFF_FLAGS
        assert name in FLAG_INFO
    assert FLAG_INFO["learn"].depends_on == ("ui_v2",)
    assert FLAG_INFO["referrals"].depends_on == ("ui_v2",)


def test_learn_is_404_naming_the_flag_until_both_are_on():
    headers, me = _who("c13-learn-flags@example.com")
    got = client.get("/api/learn", headers=headers)
    assert got.status_code == 404 and got.headers.get("X-Feature-Disabled") == "ui_v2"
    _on(me, "ui_v2")
    got = client.get("/api/learn", headers=headers)
    assert got.status_code == 404 and got.headers.get("X-Feature-Disabled") == "learn"
    _on(me, "learn")
    got = client.get("/api/learn", headers=headers)
    assert got.status_code == 200
    body = got.json()
    assert {g["id"] for g in body["groups"]} == {"structure", "risk", "paper", "atp"}
    assert all(lesson["video_url"] is None for lesson in body["lessons"])     # empty until Abhi adds his links
    assert body["course_url"] is None
    assert client.get("/api/learn").status_code in (401, 403)


# --- the content guard ------------------------------------------------------------------------------------------------------

def test_every_lesson_page_and_email_line_passes_the_content_guard():
    assert content.problems(content.readable_lines(content.load())) == []
    assert content.problems(content.site_lines(content.load_site())) == []
    assert content.problems(onboarding.readable_lines()) == []


def test_the_guard_catches_what_an_education_surface_must_never_say():
    for bad in ("Buy above the breakout", "Exit at the target", "Keep a stop-loss", "A strategy with a high win rate",
                "Nifty is at its area", "Price reached 23,450", "Guaranteed returns of a lifetime"):
        assert guard(bad), bad


def test_lessons_cover_the_order_and_point_inside_the_app():
    library = content.load()
    groups = {lesson.group for lesson in library.lessons}
    assert groups == {"structure", "risk", "paper", "atp"}
    for lesson in library.lessons:
        assert lesson.next is None or (lesson.next.path.startswith("/") and "//" not in lesson.next.path)
    with pytest.raises(ValueError):
        content.NextStep(label="Elsewhere", path="https://example.com")


# --- the video links ---------------------------------------------------------------------------------------------------------

def test_video_links_are_admin_only_https_only_and_audited_without_the_address():
    owner, me = _who("c13-videos-owner@example.com")
    assert client.get("/api/admin/learn/settings", headers=owner).status_code == 403
    assert client.put("/api/admin/learn/settings", headers=owner, json={"videos": {}}).status_code == 403
    admin, admin_me = _admin("c13-videos-admin@example.com")
    got = client.get("/api/admin/learn/settings", headers=admin)
    assert got.status_code == 200 and got.json()["videos"] == {} and got.json()["lessons"]
    for bad in ({"videos": {"structure-trend": "http://example.com/v"}}, {"videos": {"structure-trend": "javascript:alert(1)"}},
                {"videos": {"no-such-lesson": "https://example.com/v"}}, {"course_url": "ftp://example.com"}, {"extra": 1}):
        assert client.put("/api/admin/learn/settings", headers=admin, json=bad).status_code == 422, bad
    secret_url = "https://videos.example.com/watch/c13-structure"
    saved = client.put("/api/admin/learn/settings", headers=admin,
                       json={"videos": {"structure-trend": secret_url, "paper-why": ""}, "course_url": "https://course.example.com"})
    assert saved.status_code == 200 and saved.json()["videos"] == {"structure-trend": secret_url}
    _on(me, "ui_v2", "learn")
    lessons = {lesson["id"]: lesson for lesson in client.get("/api/learn", headers=owner).json()["lessons"]}
    assert lessons["structure-trend"]["video_url"] == secret_url and lessons["paper-why"]["video_url"] is None
    details = _audit("learn_settings_changed")
    assert details and all("example.com" not in d for d in details) and any("structure-trend" in d for d in details)
    client.put("/api/admin/learn/settings", headers=admin, json={"videos": {}, "course_url": ""})


# --- the public marketing pages ----------------------------------------------------------------------------------------------

def test_site_pages_are_public_and_follow_the_global_flag():
    assert client.get("/api/site/pages").status_code == 404
    _global("marketing_site", True)
    try:
        got = client.get("/api/site/pages")
        assert got.status_code == 200
        slugs = [p["slug"] for p in got.json()["pages"]]
        assert slugs == ["features", "how-it-works", "about"]
        for page in got.json()["pages"]:
            assert 20 <= len(page["description"]) <= 160
    finally:
        _global("marketing_site", False)
    assert client.get("/api/site/pages").status_code == 404


def test_the_front_page_has_seo_basics():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / "frontend"
    index = (root / "index.html").read_text()
    for tag in ('property="og:title"', 'property="og:description"', 'property="og:image"', 'name="twitter:card"',
                'rel="canonical"', 'name="description"'):
        assert tag in index, tag
    robots = (root / "public" / "robots.txt").read_text()
    assert "Sitemap: https://aialgodesk.tech/sitemap.xml" in robots and "Disallow: /api/" in robots
    sitemap = (root / "public" / "sitemap.xml").read_text()
    for page in ("/features", "/how-it-works", "/about"):
        assert f"<loc>https://aialgodesk.tech{page}</loc>" in sitemap
    for text in (index, sitemap):
        assert not guard_numbers_in_text(text)


def guard_numbers_in_text(text: str) -> List[str]:
    """No performance claim or security name in the public tags (the guard's words, not its number rule)."""
    lower = text.lower()
    return [w for w in ("win rate", "accuracy", "guaranteed", "returns of", "nifty", "sensex", "reliance") if w in lower]


# --- the onboarding e-mails --------------------------------------------------------------------------------------------------

@pytest.fixture()
def outbox(monkeypatch):
    """A fake mail server: every message handed to SMTP lands here."""
    sent: List[EmailMessage] = []
    monkeypatch.setattr(config, "PLATFORM_SMTP_HOST", "smtp.test")
    monkeypatch.setattr(config, "PLATFORM_SMTP_FROM", "hello@aialgodesk.test")
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://app.example.com")
    monkeypatch.setattr(mailer, "_deliver", lambda message: sent.append(message))
    return sent


def _newcomer(email: str, *, verified: bool = True, signed: datetime = SIGNED):
    headers, me = _who(email)
    _edit_user(me["id"], created_at=signed, email_verified_at=signed if verified else None)
    return headers, me


def _send(now: datetime) -> int:
    async def go():
        async with _session_factory() as session:
            return await onboarding.send_due(session, now)
    return run(go())


def _mine(sent: List[EmailMessage], address: str) -> List[EmailMessage]:
    """This person's onboarding e-mails (registration's own verification e-mail is not one of them)."""
    subjects = {t.subject for t in onboarding.TEMPLATES.values()}
    return [m for m in sent if m["To"] == address and m["Subject"] in subjects]


def test_nothing_is_sent_without_the_flag_the_mailer_or_the_public_address(outbox, monkeypatch):
    _, me = _newcomer("c13-mail-off@example.com")
    _send(SIGNED + timedelta(hours=3))
    assert not _mine(outbox, me["email"])                        # flag off
    _on(me, "onboarding_emails")
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "")
    monkeypatch.setattr(config, "FRONTEND_URL", "")
    _send(SIGNED + timedelta(hours=3))
    assert not _mine(outbox, me["email"])                        # no public address: no links, no mail
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://app.example.com")
    monkeypatch.setattr(config, "PLATFORM_SMTP_HOST", "")
    _send(SIGNED + timedelta(hours=3))
    assert not _mine(outbox, me["email"])                        # no mailer
    monkeypatch.setattr(config, "PLATFORM_SMTP_HOST", "smtp.test")
    _send(SIGNED + timedelta(hours=3))
    assert len(_mine(outbox, me["email"])) == 1                  # all in place: the first e-mail goes


def test_each_step_goes_once_at_its_hour_with_its_unsubscribe(outbox):
    _, me = _newcomer("c13-mail-steps@example.com")
    _on(me, "onboarding_emails")
    _send(SIGNED + timedelta(hours=1))
    assert not _mine(outbox, me["email"])                        # too early
    _send(SIGNED + timedelta(hours=3))
    _send(SIGNED + timedelta(hours=4))                           # a second run sends nothing again
    mine = _mine(outbox, me["email"])
    assert [m["Subject"] for m in mine] == [onboarding.TEMPLATES["broker"].subject]
    body = mine[0].get_content()
    assert "https://app.example.com/broker" in body and "https://app.example.com/api/growth/onboarding/unsubscribe?t=" in body
    assert mine[0]["List-Unsubscribe"].startswith("<https://app.example.com/api/growth/onboarding/unsubscribe?t=")
    assert mine[0]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    _send(SIGNED + timedelta(hours=49))
    _send(SIGNED + timedelta(hours=121))
    _send(SIGNED + timedelta(hours=130))
    subjects = [m["Subject"] for m in _mine(outbox, me["email"])]
    assert subjects == [onboarding.TEMPLATES[s].subject for s in onboarding.STEPS]
    for message in _mine(outbox, me["email"]):
        text = message.get_content()
        assert "Nothing in this e-mail tells you what to trade." in text
        assert not any(w in text.lower() for w in (" buy ", " sell ", "target", "stop-loss", "profit"))


def test_a_step_already_taken_is_skipped_and_never_sent(outbox):
    _, me = _newcomer("c13-mail-skip@example.com")
    _on(me, "onboarding_emails")
    _add(BrokerCredentialRecord(tenant_id=me["tenant_id"], user_id=me["id"], broker_name="upstox", encrypted_payload="x",
                                created_at=SIGNED + timedelta(minutes=30)),
         StrategyDeploymentRecord(holding="INTRADAY", order_style="MARKET", tenant_id=me["tenant_id"], strategy_id="orb_15m_5m", symbol="TEST", mode="PAPER",
                                  created_by=me["id"], created_at=SIGNED + timedelta(hours=1)))
    _send(SIGNED + timedelta(hours=3))
    _send(SIGNED + timedelta(hours=50))
    assert not _mine(outbox, me["email"])
    _send(SIGNED + timedelta(hours=121))
    assert [m["Subject"] for m in _mine(outbox, me["email"])] == [onboarding.TEMPLATES["signal_review"].subject]


def test_only_verified_accounts_inside_their_first_week(outbox):
    _, unverified = _newcomer("c13-mail-unverified@example.com", verified=False)
    _, old = _newcomer("c13-mail-old@example.com", signed=SIGNED - timedelta(days=30))
    _on(unverified, "onboarding_emails")
    _on(old, "onboarding_emails")
    _send(SIGNED + timedelta(hours=3))
    assert not _mine(outbox, unverified["email"]) and not _mine(outbox, old["email"])
    _, late = _newcomer("c13-mail-late@example.com")
    _on(late, "onboarding_emails")
    _send(SIGNED + timedelta(days=8))                            # the flag came on after the first week: nothing
    assert not _mine(outbox, late["email"])


def test_a_failed_send_is_not_marked_and_is_tried_again(outbox, monkeypatch):
    _, me = _newcomer("c13-mail-retry@example.com")
    _on(me, "onboarding_emails")

    def broken(message):
        raise OSError("connection refused")
    monkeypatch.setattr(mailer, "_deliver", broken)
    _send(SIGNED + timedelta(hours=3))
    monkeypatch.setattr(mailer, "_deliver", lambda message: outbox.append(message))
    _send(SIGNED + timedelta(hours=4))
    assert [m["Subject"] for m in _mine(outbox, me["email"])] == [onboarding.TEMPLATES["broker"].subject]


def test_unsubscribe_page_then_one_click_stops_the_rest(outbox):
    _, me = _newcomer("c13-mail-unsub@example.com")
    _on(me, "onboarding_emails")
    _send(SIGNED + timedelta(hours=3))
    token = onboarding.unsubscribe_token(me["tenant_id"], me["email"])
    page = client.get(f"/api/growth/onboarding/unsubscribe?t={token}")
    assert page.status_code == 200 and "<form method='post'" in page.text
    assert not run(_opted(me))                                   # the page alone unsubscribes nobody
    assert client.post(f"/api/growth/onboarding/unsubscribe?t={token}").status_code == 200
    assert client.post(f"/api/growth/onboarding/unsubscribe?t={token}").status_code == 200     # twice is fine
    assert run(_opted(me))
    _send(SIGNED + timedelta(hours=60))
    assert len(_mine(outbox, me["email"])) == 1
    assert client.post("/api/growth/onboarding/unsubscribe?t=bad.token").status_code == 400
    assert "not valid" in client.get("/api/growth/onboarding/unsubscribe?t=bad.token").text
    other = onboarding.unsubscribe_token(me["tenant_id"], me["email"]).split(".")[0] + ".0123456789abcdef0123456789abcdef"
    assert client.post(f"/api/growth/onboarding/unsubscribe?t={other}").status_code == 400


async def _opted(me) -> bool:
    async with _session_factory() as session:
        return await onboarding.opted_out(session, me["tenant_id"], me["email"])


def test_the_hours_setting_falls_back_when_malformed(monkeypatch):
    monkeypatch.setattr(config, "ONBOARDING_EMAIL_AFTER_HOURS", "1,2")
    assert onboarding.after_hours() == onboarding.DEFAULT_HOURS
    monkeypatch.setattr(config, "ONBOARDING_EMAIL_AFTER_HOURS", "x,y,z")
    assert onboarding.after_hours() == onboarding.DEFAULT_HOURS
    monkeypatch.setattr(config, "ONBOARDING_EMAIL_AFTER_HOURS", "0,24,72")
    assert onboarding.after_hours() == (0, 24, 72)


# --- the referral flow -------------------------------------------------------------------------------------------------------

def test_referral_routes_are_404_naming_the_flag():
    headers, me = _who("c13-ref-flags@example.com")
    _on(me, "ui_v2")
    for method, path in (("get", "/api/growth/referral"), ("post", "/api/growth/referral/code")):
        got = getattr(client, method)(path, headers=headers)
        assert got.status_code == 404 and got.headers.get("X-Feature-Disabled") == "referrals"
    got = client.post("/api/growth/referral/claim", headers=headers, json={"code": "ABCDEFGH"})
    assert got.status_code == 404


def test_the_code_is_made_once_and_a_new_account_claims_it_once():
    host, host_me = _who("c13-ref-host@example.com")
    _on(host_me, "ui_v2", "referrals")
    card = client.get("/api/growth/referral", headers=host).json()
    assert card["code"] is None and card["joined"] == 0
    first = client.post("/api/growth/referral/code", headers=host).json()
    again = client.post("/api/growth/referral/code", headers=host).json()
    code = first["code"]
    assert code and again["code"] == code and referral.valid_code(code)

    friend, friend_me = _who("c13-ref-friend@example.com")
    _on(friend_me, "ui_v2", "referrals")
    assert client.post("/api/growth/referral/claim", headers=friend, json={"code": "nope"}).status_code == 409
    assert client.post("/api/growth/referral/claim", headers=friend, json={"code": "ZZZZZZZZ"}).status_code == 409
    got = client.post("/api/growth/referral/claim", headers=friend, json={"code": code.lower()})
    assert got.status_code == 200 and got.json() == {"claimed": True}
    again = client.post("/api/growth/referral/claim", headers=friend, json={"code": code})
    assert again.status_code == 409 and "already" in again.json()["detail"]
    assert client.get("/api/growth/referral", headers=host).json()["joined"] == 1

    # his own code, and an old account
    assert client.post("/api/growth/referral/claim", headers=host, json={"code": code}).status_code == 409
    old, old_me = _who("c13-ref-old@example.com")
    _on(old_me, "ui_v2", "referrals")
    _edit_user(old_me["id"], created_at=datetime.now(UTC) - timedelta(days=30))
    late = client.post("/api/growth/referral/claim", headers=old, json={"code": code})
    assert late.status_code == 409 and "new account" in late.json()["detail"]
    assert client.post("/api/growth/referral/claim", headers=friend, json={"code": code, "extra": 1}).status_code == 422


def test_referral_settings_are_admin_only_and_shown_on_the_card():
    owner, me = _who("c13-ref-settings-owner@example.com")
    assert client.get("/api/admin/growth/referral-settings", headers=owner).status_code == 403
    assert client.put("/api/admin/growth/referral-settings", headers=owner, json={"reward_text": "x"}).status_code == 403
    admin, _ = _admin("c13-ref-settings-admin@example.com")
    assert client.put("/api/admin/growth/referral-settings", headers=admin, json={"reward_text": "<b>x</b>"}).status_code == 422
    saved = client.put("/api/admin/growth/referral-settings", headers=admin, json={"reward_text": "  One month of Pro  for you both "})
    assert saved.status_code == 200 and saved.json()["reward_text"] == "One month of Pro for you both"
    assert set(saved.json()["totals"]) == {"joined", "referrers", "links_created"}
    _on(me, "ui_v2", "referrals")
    assert client.get("/api/growth/referral", headers=owner).json()["reward_text"] == "One month of Pro for you both"
    client.put("/api/admin/growth/referral-settings", headers=admin, json={"reward_text": ""})
    assert client.get("/api/growth/referral", headers=owner).json()["reward_text"] is None


# --- the growth funnel -------------------------------------------------------------------------------------------------------

def _funnel(admin) -> dict:
    got = client.get("/api/admin/console/activation?days=365&limit=500", headers=admin)
    assert got.status_code == 200
    return got.json()


def test_growth_funnel_counts_in_order_with_active_week_one():
    admin, admin_me = _admin("c13-funnel-admin@example.com")
    _on(admin_me, "growth_v2")
    before = _funnel(admin)["growth_funnel"]
    now = datetime.now(UTC)
    ten_days = now - timedelta(days=10)
    _, full = _who("c13-funnel-full@example.com")        # broker, bot, came back on day 3
    _, idle = _who("c13-funnel-idle@example.com")        # broker, bot, never came back
    _, skip = _who("c13-funnel-skip@example.com")        # a bot but no broker: stops at "signed up"
    _, fresh = _who("c13-funnel-fresh@example.com")      # still in week 1
    for who in (full, idle, skip):
        _edit_user(who["id"], created_at=ten_days)
    for who in (full, idle):
        _add(BrokerCredentialRecord(tenant_id=who["tenant_id"], user_id=who["id"], broker_name="upstox", encrypted_payload="x",
                                    created_at=ten_days + timedelta(hours=1)))
    for who in (full, idle, skip):
        _add(StrategyDeploymentRecord(holding="INTRADAY", order_style="MARKET", tenant_id=who["tenant_id"], strategy_id="orb_15m_5m", symbol="TEST", mode="PAPER",
                                      created_by=who["id"], created_at=ten_days + timedelta(hours=2)))
    _add(LoginEventRecord(user_id=full["id"], tenant_id=full["tenant_id"], email=full["email"], success=True,
                          created_at=ten_days + timedelta(days=3)),
         LoginEventRecord(user_id=idle["id"], tenant_id=idle["tenant_id"], email=idle["email"], success=False,
                          created_at=ten_days + timedelta(days=3)))
    # the sessions their registration opened are from today (outside their first week) - move them to sign-up time
    from sqlalchemy import update

    from app.db.models import UserSessionRecord

    async def sessions_at_signup():
        async with _session_factory() as session:
            await session.execute(update(UserSessionRecord).where(UserSessionRecord.user_id.in_([full["id"], idle["id"], skip["id"]]))
                                  .values(created_at=ten_days, last_used_at=ten_days))
            await session.commit()
    run(sessions_at_signup())

    after = _funnel(admin)
    steps_before = {s["key"]: s["count"] for s in before["steps"]}
    steps_after = {s["key"]: s["count"] for s in after["growth_funnel"]["steps"]}
    assert [s["key"] for s in after["growth_funnel"]["steps"]] == ["signed_up", "broker", "paper_bot", "active_week1"]
    delta = {k: steps_after[k] - steps_before[k] for k in steps_after}
    assert delta == {"signed_up": 3, "broker": 2, "paper_bot": 2, "active_week1": 1}
    assert after["growth_funnel"]["week1_open"] >= before["week1_open"] + 1
    people = {p["id"]: p for p in after["people"]}
    assert people[full["id"]]["active_week1"] is True
    assert people[idle["id"]]["active_week1"] is False
    assert people[fresh["id"]]["active_week1"] is None
    assert all("*" in p["email"] for p in after["people"] if p["email"])          # masked, as before


def test_the_frontend_fixtures_are_the_servers_own_payloads():
    """e2e/fixtures/learn_v2.json and site_v2.json (the vitest and e2e data) are exactly what the server sends."""
    from pathlib import Path
    fixtures = Path(__file__).resolve().parents[2] / "frontend" / "e2e" / "fixtures"
    learn = json.loads((fixtures / "learn_v2.json").read_text())
    assert learn == {**content.lessons_payload(content.load(), {}), "course_url": None}
    assert json.loads((fixtures / "site_v2.json").read_text()) == content.site_payload(content.load_site())
