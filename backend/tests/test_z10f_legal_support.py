"""Z10-f: the legal pages (public, versioned, draft-headed, advice-free), their acceptance (at sign-up, on the next
sign-in, never a lock-out) and the support bot (flag `support_bot`, off: 404; how-to only; refuses advice, account and
order questions; hands low confidence to the feedback form; the organisation's own AI provider only; rate-limited)."""
import asyncio
import re

import pytest
from sqlalchemy import select

from app.ai import compliance_terms, output_filter, wording
from app.ai import settings as ai_settings
from app.core import rate_limit
from app.db.models import AiAcknowledgementRecord, AuditLogRecord
from app.legal import acceptance, documents
from app.main import app
from app.platform import controls
from app.retention.policy import NEVER_DELETED, load_policy
from app.support import FLAG, bot, guard, knowledge
from app.support.routes import support_rate_limit
from tests.test_auth_api import _register, _session_factory, client
from tests.test_team_api import _invite, _owner as _team_owner, _token_from

PASSWORD = "S3cur3Pass!"
ASK = "/api/ai/support/ask"
app.dependency_overrides[support_rate_limit] = lambda: None


def _run(coro):
    return asyncio.run(coro)


def _headers(email: str) -> dict:
    return {"Authorization": f"Bearer {_register(email)}"}


def _set_flags(**flags: bool) -> None:
    async def go():
        async with _session_factory() as session:
            stored = (await controls._get(session, controls.KEY_FEATURE_FLAGS)).get("flags") or {}
            for name, on in flags.items():
                stored[name] = {"on": on, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": stored}, None)
            await session.commit()
    _run(go())


@pytest.fixture
def bot_on():
    _set_flags(support_bot=True)
    yield
    _set_flags(support_bot=False)


def _rows(user_id: int):
    async def go():
        async with _session_factory() as session:
            return list((await session.scalars(select(AiAcknowledgementRecord).where(
                AiAcknowledgementRecord.user_id == user_id, AiAcknowledgementRecord.kind.like("legal_%")))).all())
    return _run(go())


def _me(headers) -> dict:
    return client.get("/api/auth/me", headers=headers).json()


CURRENT = acceptance.current_versions()


# --- the documents ----------------------------------------------------------------------------------------------------

def test_three_documents_parse_with_version_title_and_the_draft_header():
    docs = documents.documents()
    assert set(docs) == {"terms", "privacy", "risk"}
    for doc in docs.values():
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", doc.version) and doc.title
        assert doc.draft and doc.source.lstrip().startswith("> **" + documents.DRAFT_BANNER)
        assert len(doc.sha256) == 64 and doc.kind == f"legal_{doc.slug}" and len(doc.kind) <= 24


def test_documents_are_public_routes():
    r = client.get("/api/legal/documents")
    assert r.status_code == 200 and "public" in r.headers["cache-control"]
    assert [d["slug"] for d in r.json()["documents"]] == ["terms", "privacy", "risk"]
    for slug in documents.SLUGS:
        page = client.get(f"/api/legal/documents/{slug}")       # no Authorization header
        assert page.status_code == 200, page.text
        body = page.json()
        assert body["version"] == CURRENT[slug] and body["draft"] is True and documents.DRAFT_BANNER in body["body"]
        assert "{{" not in body["body"]
    assert client.get("/api/legal/documents/cookies").status_code == 404
    assert client.get("/api/legal/documents/../etc").status_code == 404


def test_privacy_notice_covers_the_dpdp_items_with_the_platforms_own_settings():
    body = client.get("/api/legal/documents/privacy").json()["body"]
    for heading in ("purpose", "What data we hold", "retention", "Your rights", "Grievance officer", "data breach"):
        assert heading.lower() in body.lower(), heading
    policy = load_policy()
    assert f"Sign-in attempts (IP address, browser): {policy.login_events_days} days" in body
    assert f"Sessions: {policy.sessions_days} days" in body
    from app.compliance import golive, rules
    hours = rules.load().param(golive.RULE, "dpdp_breach_notify_hours")
    assert f"target of {hours} hours" in body and "Data Protection Board of India" in body
    # No invented company facts: the company, its address and the grievance officer are placeholders until registration.
    assert {"COMPANY NAME", "REGISTERED ADDRESS", "GRIEVANCE OFFICER NAME", "GRIEVANCE OFFICER EMAIL"} <= set(
        client.get("/api/legal/documents/privacy").json()["open_placeholders"])


def test_retention_settings_flow_into_the_page_but_not_into_the_acceptance_hash(monkeypatch):
    before = documents.get("privacy").sha256
    monkeypatch.setenv("RETENTION_LOGIN_EVENTS_DAYS", "400")
    assert "Sign-in attempts (IP address, browser): 400 days" in client.get("/api/legal/documents/privacy").json()["body"]
    assert documents.get("privacy").sha256 == before


def test_risk_and_terms_say_paper_only_not_advice_no_research_analyst_service_market_risk():
    risk = documents.render(documents.get("risk")).lower()
    terms = documents.render(documents.get("terms")).lower()
    for text in (risk, terms):
        assert "paper" in text and "simulated" in text
        assert "not a sebi-registered investment adviser" in text or "not registered with sebi as an investment adviser" in text
        assert "research analyst" in text and "not a recommendation to buy, sell or hold any security" in text
    assert "no research-analyst service" in risk and "market risk" in risk and "lose" in risk


def test_legal_pages_carry_no_advice_or_guarantee_wording():
    """The lint: the Copilot's advice / guarantee filter, its banned-word check and the specific-call check all pass on
    every rendered page (negated mentions such as "not a recommendation" are allowed by both checks)."""
    for slug in documents.SLUGS:
        text = documents.render(documents.get(slug))
        assert output_filter.blocked_terms(text) == [], slug
        assert wording.banned_terms(text) == [], slug
        assert not output_filter.is_specific_call(text), slug


def test_legal_and_help_content_is_english_only():
    """The Devanagari check for this item: the legal pages and the help pages are English (the app's UI language); a
    Marathi question to the bot is still screened - see test_bot_refuses_advice."""
    devanagari = re.compile(r"[\u0900-\u097F]")
    for slug in documents.SLUGS:
        assert not devanagari.search(documents.get(slug).source), slug
    assert not devanagari.search(knowledge.HELP_FILE.read_text(encoding="utf-8"))


def test_a_malformed_document_fails_loudly():
    with pytest.raises(documents.LegalContentError):
        documents._parse("terms", "no front matter")
    with pytest.raises(documents.LegalContentError):
        documents._parse("terms", "---\nslug: terms\ntitle: T\nversion: soon\n---\nbody")
    with pytest.raises(documents.LegalContentError):
        documents._parse("terms", "---\nslug: privacy\ntitle: T\nversion: 2026-10-11\n---\nbody")
    bad = documents._parse("terms", "---\nslug: terms\ntitle: T\nversion: 2026-10-11\n---\n{{retention.nope}}")
    with pytest.raises(documents.LegalContentError):
        documents.render(bad)


def test_an_edit_without_a_version_bump_still_changes_the_hash():
    a = documents._parse("terms", "---\nslug: terms\ntitle: T\nversion: 2026-10-11\n---\nOne text.")
    b = documents._parse("terms", "---\nslug: terms\ntitle: T\nversion: 2026-10-11\n---\nAnother text.")
    c = documents._parse("terms", "---\nslug: terms\ntitle: T\nversion: 2026-10-12\n---\nOne text.")
    assert len({a.sha256, b.sha256, c.sha256}) == 3


# --- acceptance -------------------------------------------------------------------------------------------------------

def test_signup_records_acceptance_with_version_and_time():
    r = client.post("/api/auth/register", json={"email": "z10f-signup@example.com", "password": PASSWORD, "legal_acceptance": CURRENT},
                    headers={"User-Agent": "z10f-test-browser"})
    assert r.status_code == 201, r.text
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    me = _me(headers)
    rows = _rows(me["id"])
    assert sorted(row.kind for row in rows) == ["legal_privacy", "legal_risk", "legal_terms"]
    for row in rows:
        doc = documents.get(row.kind.removeprefix("legal_"))
        assert row.version == doc.version and row.text_sha256 == doc.sha256 and row.accepted_at is not None
        assert row.tenant_id == me["tenant_id"] and row.user_agent == "z10f-test-browser"
    status = client.get("/api/legal/acceptance", headers=headers).json()
    assert status["all_accepted"] is True and status["pending"] == []
    assert all(d["accepted_at"] for d in status["documents"])

    async def audit():
        async with _session_factory() as session:
            return list((await session.scalars(select(AuditLogRecord.detail).where(AuditLogRecord.user_id == me["id"], AuditLogRecord.event == "legal_accepted"))).all())
    details = _run(audit())
    assert len(details) == 3 and all("via signup" in d for d in details)


def test_signup_acceptance_must_name_every_document_at_its_current_version():
    partial = client.post("/api/auth/register", json={"email": "z10f-partial@example.com", "password": PASSWORD,
                                                      "legal_acceptance": {"terms": CURRENT["terms"]}})
    assert partial.status_code == 400 and "missing" in partial.json()["detail"]
    stale = client.post("/api/auth/register", json={"email": "z10f-stale@example.com", "password": PASSWORD,
                                                    "legal_acceptance": {**CURRENT, "risk": "2020-01-01"}})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "legal_version_changed"
    assert stale.json()["detail"]["current"] == CURRENT["risk"]
    unknown = client.post("/api/auth/register", json={"email": "z10f-unknown@example.com", "password": PASSWORD,
                                                      "legal_acceptance": {**CURRENT, "cookies": "1"}})
    assert unknown.status_code in (400, 422)
    # Nothing was created by the refused sign-ups: the same emails register fine afterwards.
    assert client.post("/api/auth/register", json={"email": "z10f-stale@example.com", "password": PASSWORD}).status_code == 201


def test_existing_user_is_prompted_and_accepts_on_next_login_without_being_locked_out():
    headers = _headers("z10f-existing@example.com")          # registered the old way: no acceptance sent
    status = client.get("/api/legal/acceptance", headers=headers).json()
    assert status["all_accepted"] is False and status["pending"] == ["terms", "privacy", "risk"]
    # Never locked out: positions, orders and the rest answer as before while the acceptance is pending.
    for path in ("/api/auth/me", "/api/positions", "/api/trades", "/api/notifications"):
        r = client.get(path, headers=headers)
        assert r.status_code not in (401, 403, 428, 451), (path, r.status_code)
    # Next login: still pending (a login does not accept anything), then the prompt records it.
    login = client.post("/api/auth/login", json={"email": "z10f-existing@example.com", "password": PASSWORD})
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert client.get("/api/legal/acceptance", headers=headers).json()["pending"] == ["terms", "privacy", "risk"]
    done = client.post("/api/legal/acceptance", headers=headers, json={"documents": CURRENT})
    assert done.status_code == 200 and done.json()["all_accepted"] is True
    # Idempotent: accepting again records nothing new.
    client.post("/api/legal/acceptance", headers=headers, json={"documents": CURRENT})
    assert len(_rows(_me(headers)["id"])) == 3


def test_a_new_version_asks_again(monkeypatch):
    headers = _headers("z10f-newversion@example.com")
    client.post("/api/legal/acceptance", headers=headers, json={"documents": CURRENT})
    orig = dict(documents.documents())
    old = orig["terms"]
    new = documents._parse("terms", f"---\nslug: terms\ntitle: {old.title}\nversion: 2099-01-01\n---\n{old.source}\nOne more line.")
    monkeypatch.setattr(documents, "documents", lambda: {**orig, "terms": new})
    status = client.get("/api/legal/acceptance", headers=headers).json()
    assert status["pending"] == ["terms"]
    stale = client.post("/api/legal/acceptance", headers=headers, json={"documents": {"terms": old.version}})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "legal_version_changed"
    ok = client.post("/api/legal/acceptance", headers=headers, json={"documents": {"terms": "2099-01-01"}})
    assert ok.status_code == 200 and ok.json()["all_accepted"] is True


def test_an_edited_text_with_the_same_version_asks_again(monkeypatch):
    headers = _headers("z10f-sameversion@example.com")
    client.post("/api/legal/acceptance", headers=headers, json={"documents": CURRENT})
    orig = dict(documents.documents())
    old = orig["risk"]
    edited = documents._parse("risk", f"---\nslug: risk\ntitle: {old.title}\nversion: {old.version}\n---\n{old.source}\nEdited.")
    monkeypatch.setattr(documents, "documents", lambda: {**orig, "risk": edited})
    assert client.get("/api/legal/acceptance", headers=headers).json()["pending"] == ["risk"]


def test_acceptance_needs_a_login_and_refuses_unknown_fields():
    assert client.get("/api/legal/acceptance").status_code == 401
    assert client.post("/api/legal/acceptance", json={"documents": CURRENT}).status_code == 401
    headers = _headers("z10f-badbody@example.com")
    assert client.post("/api/legal/acceptance", headers=headers, json={"documents": CURRENT, "user_id": 1}).status_code == 422
    assert client.post("/api/legal/acceptance", headers=headers, json={"documents": {}}).status_code == 422
    assert client.post("/api/legal/acceptance", headers=headers, json={"documents": {"cookies": "1"}}).status_code == 400


def test_invited_member_can_accept_at_join():
    owner_headers, _ = _team_owner("z10f-team-owner@example.com")
    invite = _invite(owner_headers, "z10f-team-member@example.com")
    assert invite.status_code in (200, 201), invite.text
    url = invite.json()["invite_url"]
    r = client.post(f"/api/auth/invite/{_token_from(url)}/accept", json={"password": PASSWORD, "legal_acceptance": CURRENT})
    assert r.status_code == 201, r.text
    me = _me({"Authorization": f"Bearer {r.json()['access_token']}"})
    assert len(_rows(me["id"])) == 3


def test_acceptances_are_kept_like_the_other_acknowledgements():
    assert "ai_acknowledgements" in NEVER_DELETED


# --- the support bot: flag ----------------------------------------------------------------------------------------------

def test_flag_is_registered_and_off_by_default():
    assert FLAG == "support_bot" and FLAG in controls.FEATURE_FLAGS and FLAG in controls.DEFAULT_OFF_FLAGS
    headers = _headers("z10f-flag@example.com")
    assert client.get("/api/system/features", headers=headers).json()[FLAG] is False


def test_flag_off_is_404():
    headers = _headers("z10f-flagoff@example.com")
    r = client.post(ASK, headers=headers, json={"question": "how do I reset my password?"})
    assert r.status_code == 404 and r.headers.get("X-Feature-Disabled") == FLAG


def test_flag_on_for_one_tenant_only():
    headers = _headers("z10f-tenant-a@example.com")
    other = _headers("z10f-tenant-b@example.com")
    tenant = _me(headers)["tenant_id"]

    async def go(on):
        async with _session_factory() as session:
            stored = (await controls._get(session, controls.KEY_FEATURE_FLAGS)).get("flags") or {}
            stored[FLAG] = {"on": False, "tenants": [tenant] if on else []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": stored}, None)
            await session.commit()
    _run(go(True))
    try:
        assert client.post(ASK, headers=headers, json={"question": "how do I reset my password?"}).status_code == 200
        assert client.post(ASK, headers=other, json={"question": "how do I reset my password?"}).status_code == 404
    finally:
        _run(go(False))


def test_bot_needs_a_login(bot_on):
    assert client.post(ASK, json={"question": "how do I reset my password?"}).status_code == 401


# --- the support bot: answers ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("question,title", [
    ("How do I reset my password?", "I forgot my password. How do I reset it?"),
    ("how do I turn on 2FA", "How do I turn on two-factor authentication?"),
    ("How do I connect Zerodha?", "How do I connect my broker?"),
    ("where do I set up telegram alerts", "How do I get alerts on Telegram, email or push?"),
    ("how do I close a position?", "How do I see and close my paper positions?"),
    ("does the platform place real orders?", "What is paper trading, and does the platform place real orders?"),
    ("how do I install the app on my iPhone", "How do I install the app on my phone or computer?"),
    ("who is the grievance officer?", "Privacy notice: Grievance officer"),
])
def test_how_to_questions_are_answered_from_the_help_and_legal_pages(bot_on, question, title):
    headers = _headers(f"z10f-howto-{abs(hash(question)) % 10**8}@example.com")
    r = client.post(ASK, headers=headers, json={"question": question})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["outcome"] == "answer" and body["source"] == "help" and body["handoff"] is False
    assert body["links"][0]["title"] == title and body["confidence"] >= knowledge.min_confidence()
    assert output_filter.blocked_terms(body["answer"]) == [] and wording.banned_terms(body["answer"]) == []


def test_legal_answers_link_to_the_public_page(bot_on):
    headers = _headers("z10f-legal-answer@example.com")
    body = client.post(ASK, headers=headers, json={"question": "how long do you keep my login IP address data?"}).json()
    assert body["outcome"] == "answer" and body["links"][0]["url"] == "/legal/privacy"
    assert f"{load_policy().login_events_days} days" in body["answer"]


@pytest.mark.parametrize("question", [
    "Should I buy RELIANCE today?", "which stock should I buy tomorrow", "what is the target for NIFTY 25000 CE",
    "will HDFCBANK go up this week?", "give me intraday tips", "is it a good time to invest in IT stocks",
    "best stocks for long term", "can you recommend an option strike to sell", "how much should I invest in options",
    "is this a sure-shot trade", "guaranteed profit strategy please", "मला कोणता शेअर घ्यावा का",
])
def test_bot_refuses_advice(bot_on, question):
    headers = _headers(f"z10f-advice-{abs(hash(question)) % 10**8}@example.com")
    body = client.post(ASK, headers=headers, json={"question": question}).json()
    assert body["outcome"] == "refused" and body["refusal"] == "advice", question
    assert "no investment advice" in body["answer"] and body["links"][0]["url"] == "/legal/risk"


@pytest.mark.parametrize("question", [
    "what is my P&L today?", "show my positions", "how much did I lose this week", "am I in profit?",
    "did my order get filled?", "tell me my holdings",
])
def test_bot_refuses_to_read_positions_or_account_data(bot_on, question):
    headers = _headers(f"z10f-account-{abs(hash(question)) % 10**8}@example.com")
    body = client.post(ASK, headers=headers, json={"question": question}).json()
    assert body["outcome"] == "refused" and body["refusal"] == "account", question
    assert "can't see your account" in body["answer"]


@pytest.mark.parametrize("question", [
    "sell my INFY shares", "buy 50 TCS", "exit my position now", "can you place an order for me?",
    "please cancel all my orders", "square off everything for me", "close my NIFTY position",
])
def test_bot_refuses_order_requests_and_points_to_the_exit(bot_on, question):
    headers = _headers(f"z10f-orders-{abs(hash(question)) % 10**8}@example.com")
    body = client.post(ASK, headers=headers, json={"question": question}).json()
    assert body["outcome"] == "refused" and body["refusal"] in ("orders", "advice"), question
    if body["refusal"] == "orders":
        assert "can't place, change, cancel or exit orders" in body["answer"] and body["links"][0]["url"] == "/positions"


def test_how_to_wording_about_orders_and_positions_is_not_refused():
    for q in ("how do I close a position?", "where do I see my orders?", "how can I cancel a proposal?",
              "what is a stop-loss order?", "how do I see my P&L?", "how do I set a target for my strategy?",
              "will my alerts fall back to email?", "can you suggest how to build a strategy?",
              "exit now button is not working", "close the help dialog", "how do I buy or sell in paper mode?"):
        assert guard.classify(q) is None, q


def test_low_confidence_hands_off_to_the_feedback_form(bot_on):
    headers = _headers("z10f-handoff@example.com")
    for q in ("what is the weather in Pune", "my screen flickers when I rotate the tablet on Firefox nightly"):
        body = client.post(ASK, headers=headers, json={"question": q}).json()
        assert body["outcome"] == "handoff" and body["handoff"] is True, (q, body)
        assert "Send feedback" in body["answer"] and body["feedback_draft"] == q and body["links"] == []


def test_every_fixed_reply_passes_the_advice_filter():
    for text in list(guard.REPLIES.values()) + [bot.HANDOFF_TEXT]:
        assert output_filter.blocked_terms(text) == [] and wording.banned_terms(text) == [] and not output_filter.is_specific_call(text)
    for p in knowledge.index().passages:
        assert output_filter.blocked_terms(p.text) == [], p.id
        assert wording.banned_terms(p.text) == [], p.id
        assert not output_filter.is_specific_call(p.text), p.id


def test_help_entries_are_well_formed_and_point_at_real_pages():
    from app.support.knowledge import parse_help
    entries = parse_help(knowledge.HELP_FILE.read_text(encoding="utf-8"))
    assert len(entries) >= 20 and len({e.id for e in entries}) == len(entries)
    pages = {"/", "/account", "/team", "/settings", "/notifications", "/positions", "/copilot/watchtower", "/risk-management",
             "/backtest", "/strategy-builder", "/deployments", "/screener", "/system-logs", "/legal/privacy", "/legal/risk"}
    assert {e.url for e in entries} <= pages
    with pytest.raises(ValueError):
        parse_help("## A question\npage: settings\n\nText.")


# --- the support bot: AI wording (BYOK rule) -----------------------------------------------------------------------------

class _FakeProvider:
    name, model = "anthropic", "test-model"

    def __init__(self, answer):
        self.answer, self.prompt_version, self.calls = answer, None, []

    async def complete(self, system, user, *, max_tokens=2000):
        self.calls.append((system, user))
        return self.answer


def _with_org_provider(monkeypatch, provider, tenant_id=None):
    seen = []

    async def provider_for(session, tenant, **kwargs):
        seen.append(tenant.id)
        return provider if tenant_id is None or tenant.id == tenant_id else _rule_based()
    monkeypatch.setattr(ai_settings, "provider_for", provider_for)
    return seen


def _rule_based():
    from app.ai.providers import RuleBasedProvider
    return RuleBasedProvider()


def test_ai_rewording_uses_the_callers_organisation_provider_and_is_screened(bot_on, monkeypatch):
    headers = _headers("z10f-ai-org@example.com")
    tenant = _me(headers)["tenant_id"]
    good = _FakeProvider("Open the Account page, choose Forgot password? and enter your email to get a reset link.")
    seen = _with_org_provider(monkeypatch, good)
    body = client.post(ASK, headers=headers, json={"question": "how do I reset my password?"}).json()
    assert body["source"] == "ai" and body["answer"].startswith("Open the Account page")
    assert seen == [tenant] and good.prompt_version == bot.PROMPT_VERSION
    system, user = good.calls[0]
    assert "HELP PASSAGES" in system and "reset" in user.lower()


@pytest.mark.parametrize("bad", [
    "I recommend you reset your password and then buy RELIANCE at 2900.",
    "Reset it from the Account page; this is guaranteed to work.",
    "Use the link within 48 hours.",                      # a number the passages do not have
    "NO_ANSWER",
])
def test_ai_text_that_fails_a_check_falls_back_to_the_help_text(bot_on, monkeypatch, bad):
    headers = _headers(f"z10f-ai-bad-{abs(hash(bad)) % 10**8}@example.com")
    _with_org_provider(monkeypatch, _FakeProvider(bad))
    body = client.post(ASK, headers=headers, json={"question": "how do I reset my password?"}).json()
    assert body["outcome"] == "answer" and body["source"] == "help" and body["answer"] != bad


def test_refused_questions_never_reach_the_provider(bot_on, monkeypatch):
    headers = _headers("z10f-ai-refused@example.com")
    spy = _FakeProvider("unused")
    _with_org_provider(monkeypatch, spy)
    for q in ("should I buy TCS?", "what is my P&L?", "sell my INFY"):
        assert client.post(ASK, headers=headers, json={"question": q}).json()["outcome"] == "refused"
    assert spy.calls == []


def test_no_provider_without_the_copilot_acknowledgement_or_with_the_copilot_off(bot_on, monkeypatch):
    spy = _FakeProvider("Open the Account page.")
    _with_org_provider(monkeypatch, spy)
    token = _register("z10f-ai-noack@example.com", ai_terms=False)
    body = client.post(ASK, headers={"Authorization": f"Bearer {token}"}, json={"question": "how do I reset my password?"}).json()
    assert body["outcome"] == "answer" and body["source"] == "help"      # help never waits on the AI acknowledgement
    _set_flags(ai_copilot=False)
    try:
        headers = _headers("z10f-ai-copilot-off@example.com")
        assert client.post(ASK, headers=headers, json={"question": "how do I reset my password?"}).json()["source"] == "help"
    finally:
        _set_flags(ai_copilot=True)
    assert spy.calls == []


def test_one_organisations_provider_is_never_used_for_another(bot_on, monkeypatch):
    a = _headers("z10f-byok-a@example.com")
    b = _headers("z10f-byok-b@example.com")
    spy = _FakeProvider("Open the Account page and choose Forgot password? to get a reset link.")
    _with_org_provider(monkeypatch, spy, tenant_id=_me(a)["tenant_id"])
    assert client.post(ASK, headers=b, json={"question": "how do I reset my password?"}).json()["source"] == "help"
    assert spy.calls == []
    assert client.post(ASK, headers=a, json={"question": "how do I reset my password?"}).json()["source"] == "ai"


def test_real_provider_lookup_without_a_configured_provider_answers_from_help(bot_on):
    # No monkeypatch: the organisation has no provider, so `provider_for` gives the rule-based one and no model runs.
    headers = _headers("z10f-ai-none@example.com")
    assert client.post(ASK, headers=headers, json={"question": "how do I reset my password?"}).json()["source"] == "help"
    assert compliance_terms.KIND_COPILOT     # the acknowledgement gate the AI path checks


# --- the support bot: limits ---------------------------------------------------------------------------------------------

def test_question_length_and_body_are_bounded(bot_on):
    headers = _headers("z10f-bounds@example.com")
    assert client.post(ASK, headers=headers, json={"question": "x"}).status_code == 422
    assert client.post(ASK, headers=headers, json={"question": "a" * 501}).status_code == 422
    assert client.post(ASK, headers=headers, json={"question": "how do I reset my password?", "user_id": 1}).status_code == 422


def test_bot_is_rate_limited_per_user(bot_on):
    headers = _headers("z10f-rate@example.com")
    app.dependency_overrides.pop(support_rate_limit, None)
    rate_limit.reset("support_bot")
    try:
        codes = [client.post(ASK, headers=headers, json={"question": "how do I reset my password?"}).status_code for _ in range(21)]
        assert codes[:20] == [200] * 20 and codes[20] == 429
    finally:
        app.dependency_overrides[support_rate_limit] = lambda: None
        rate_limit.reset("support_bot")


def test_bot_module_has_no_tools_that_read_positions_or_touch_orders():
    """Static check: the bot's modules import nothing from trading, execution, brokers or the order/trade tables."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "app" / "support"
    forbidden = re.compile(r"^\s*(?:from|import)\s+app\.(?:trading|execution|brokers|kill_switch|deployments|portfolio|reconciliation|live)\b"
                           r"|TradeRecord|OrderRecord|PositionRecord|\.tools\b", re.M)
    for path in root.glob("*.py"):
        assert not forbidden.search(path.read_text(encoding="utf-8")), path.name
