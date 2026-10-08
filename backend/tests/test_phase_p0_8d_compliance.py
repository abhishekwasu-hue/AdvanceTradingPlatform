"""P0.8-D (ATP_AI_COPILOT_FIX_PROMPT section D): SEBI / DPDP compliance of the AI Copilot.

D1 the interview describes templates the trader chooses between - no recommendation, no match %, no capital allocation
   advice, no profit claim, no "improve on the pick" request to the LLM.
D2 the strategist has no "best" badge, no market-decided direction and no priced triggers on templates; the thesis of a
   single stock carries no targets or confidence % unless the operator's flag allows; the Copilot explains rules and data.
D3 the first-use acknowledgement (versioned, audited) gates every AI content route.
D4 every LLM input and output lands in `llm_calls`, which retention never touches.
D5 the owner confirms the data-sharing consent before an external provider is saved; AI-originated strategies stay off
   the marketplace until the operator's flag is on.
"""
import asyncio
import json

import httpx2
from sqlalchemy import select

from app.ai import compliance_terms as terms
from app.ai import copilot, interview, strategist
from app.ai import settings as ai_settings
from app.ai import thesis as th
from app.db.models import AiAcknowledgementRecord, AuditLogRecord, CustomStrategyRecord, LlmCallRecord, Tenant
from app.retention import service as retention
from app.retention.policy import NEVER_DELETED, load_policy
from tests.test_auth_api import _register, _session_factory, client
from tests.test_phase_ap_interview import _sessions
from tests.test_phase_l_ai import _anthropic_mock
from tests.test_trading_worker import _upgrade_plan

NOW = th.datetime(2026, 10, 7, 9, 0, tzinfo=th.timezone.utc)
FORBIDDEN_EN = ("Recommended", "recommended", "matches you", "Three options", "Consider waiting", "stay profitable", "keep ", "untouched as reserve",
                "Raise the allocation", "improve on it", "best match", "Closest to you")
FORBIDDEN_MR = ("शिफारस:", "शिफारस केलेल्या", "सर्वोत्तम", "सर्वात जुळणारा", "तुमच्यासाठी तीन पर्याय", "थांबणे चांगले", "हात लावायचा नाही")   # "शिफारस नाही" (not a recommendation) is allowed


def _run(coro):
    return asyncio.run(coro)


def _plan_text(plan):
    return json.dumps(plan, ensure_ascii=False)


# --- D1 ----------------------------------------------------------------------------------------------------------------------
def test_interview_describes_templates_the_trader_chooses_without_recommending():
    headers = {"Authorization": f"Bearer {_register('p08d-interview@example.com')}"}
    df = _sessions(days=5)
    candles = [{"timestamp": ts.isoformat(), "open": r.open, "high": r.high, "low": r.low, "close": r.close, "volume": r.volume} for ts, r in df.iterrows()]
    for lang in ("en", "mr"):
        plan = client.post("/api/ai/interview/plan", headers=headers, json={
            "answers": {"language": lang, "experience": "new", "capital": 300000, "risk": "moderate", "style": "intraday", "vehicle": "option_buy", "goal": "big_trends"},
            "base_timeframe": "5min", "candles": candles, "data_source": "broker:upstox"}).json()
        # The words the trader reads (sections, warnings, option labels); the JSON key `recommended` that carries the pick
        # stays for older clients and is not shown.
        text = _plan_text({"sections": plan["sections"], "warnings": plan["warnings"], "options": [{k: o["option"][k] for k in ("label", "summary")} for o in plan["options"]]
                           + [{"sections": o["sections"], "warnings": o["warnings"]} for o in plan["options"]]})
        for word in (FORBIDDEN_EN if lang == "en" else FORBIDDEN_MR):
            assert word not in text, (lang, word)
        assert plan["best_option"] is None and all("match" not in o["option"] and "match_reasons" not in o["option"] for o in plan["options"])
        assert plan["preferences"]["match_history"] == []
        # The trading capital is the trader's own figure - no allocation by experience.
        assert plan["risk_config"]["capital"] == 300000
        strategy = next(s for s in plan["sections"] if s["id"] == "strategy")
        assert ("This template:" in strategy["lines"][0] or "हे template:" in strategy["lines"][0])
        capital = next(s for s in plan["sections"] if s["id"] == "capital")
        assert any("your decision" in line or "निर्णय तुमचा" in line for line in capital["lines"])
        rr = next(s for s in plan["sections"] if s["id"] == "rr")
        assert any("before costs" in line or "खर्चाआधीचा" in line for line in rr["lines"])
        assert "improve" not in plan["ai_prompt"] and "not a recommendation" in plan["ai_prompt"]
    # The profile remembers the choice without a score.
    assert client.post("/api/ai/interview/choose", headers=headers, json={"answers": {"language": "en"}, "option_id": "safe", "strategy_id": None}).status_code == 200


# --- D2 ----------------------------------------------------------------------------------------------------------------------
def test_strategist_has_no_best_badge_no_market_chosen_direction_and_no_priced_triggers():
    from app.ai import market_study
    df = _sessions(days=6, minutes=1)
    study = market_study.study(df, "NIFTY 50", "en")
    out = strategist.build(df, study, "en", style="intraday")
    assert out["best"] is None and out["sides"] == ["both"]
    assert all(c["triggers"] == [] for c in out["candidates"])
    assert not any("professional choice" in n or "wait" in n.lower() for n in out["notes"])
    assert strategist._side_for({"bias": "BULLISH", "confidence": 90}, "auto") == ["both"]
    parsed = strategist.parse_request("काहीतरी वेगळं", default_symbol="HDFCBANK")
    assert parsed["direction"] == "both"
    assert "coaching" not in copilot.COPILOT_PROMPT and "explains the platform's rules" in copilot.COPILOT_PROMPT and "never recommend a" in copilot.COPILOT_PROMPT
    assert "Consider waiting" not in open(interview.__file__, encoding="utf-8").read()


def test_thesis_of_a_stock_shows_no_targets_or_confidence_unless_the_flag_allows():
    from tests.test_phase_bd_thesis import _snapshot
    snapshot = _snapshot("RELIANCE") if "symbol" in _snapshot.__code__.co_varnames else _snapshot()
    memory = {"symbols": [snapshot], "cues": [], "sentiment": None, "globals": []}
    stock = th.compose("RELIANCE", snapshot, memory, [], [], "en", NOW)
    assert stock["confidence"] is None and stock["detail_shown"] is False
    assert stock["scenarios"] == {}                          # P0.9: no price levels at all for a stock without the flag
    assert any("Price scenarios for a single stock are not shown" in line for line in stock["lines"])
    assert "not a view on what to do" in stock["lines"][0] and "%" not in stock["lines"][0]
    flagged = th.compose("RELIANCE", snapshot, memory, [], [], "en", NOW, stock_targets=True)
    assert flagged["confidence"] is not None and "target" in flagged["scenarios"]["bull"]
    index = th.compose("NIFTY 50", snapshot, memory, [], [], "mr", NOW)
    assert index["confidence"] is not None and "target" in index["scenarios"]["bear"] and "पुढची संदर्भ पातळी" in index["scenarios"]["bear"]["text"]
    assert "not investment advice" in th.NARRATIVE_PROMPT and "what to expect" in th.NARRATIVE_PROMPT


# --- D3 ----------------------------------------------------------------------------------------------------------------------
def test_first_use_acknowledgement_is_versioned_audited_and_gates_the_ai_routes():
    token = _register("p08d-ack@example.com", ai_terms=False)
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/api/auth/me", headers=headers).json()
    status = client.get("/api/ai/acknowledgement", headers=headers).json()
    assert status["accepted"] is False and status["version"] == terms.ACK_VERSION and "SEBI" in status["text"]["en"] and "SEBI" in status["text"]["mr"]
    refused = client.post("/api/ai/copilot", headers=headers, json={"message": "how is the market?", "language": "en"})
    assert refused.status_code == 428 and refused.json()["detail"]["code"] == "ai_acknowledgement_required"
    assert client.post("/api/ai/interview/start", headers=headers, json={"prompt": "strategy"}).status_code == 428
    assert client.post("/api/ai/strategist/parse", headers=headers, json={"text": "nifty intraday"}).status_code == 428
    # Safety routes never wait for it: proposals can still be listed / decided, the provider settings read.
    assert client.get("/api/ai/actions", headers=headers).status_code == 200
    assert client.get("/api/ai/provider", headers=headers).status_code == 200
    assert client.post("/api/ai/acknowledgement", headers=headers, json={"version": "2020-01-01"}).status_code == 400
    accepted = client.post("/api/ai/acknowledgement", headers=headers, json={"version": terms.ACK_VERSION, "language": "mr"}).json()
    assert accepted["accepted"] is True and accepted["accepted_by"] == me["id"]
    assert client.post("/api/ai/copilot", headers=headers, json={"message": "how is the market?", "language": "en"}).status_code == 200

    async def rows():
        async with _session_factory() as session:
            ack = await session.scalar(select(AiAcknowledgementRecord).where(AiAcknowledgementRecord.user_id == me["id"]))
            audit = await session.scalar(select(AuditLogRecord).where(AuditLogRecord.tenant_id == me["tenant_id"], AuditLogRecord.event == "ai_copilot_terms_accepted"))
            return ack, audit
    ack, audit = _run(rows())
    assert ack.version == terms.ACK_VERSION and ack.language == "mr" and ack.text_sha256 == terms.text_hash(terms.KIND_COPILOT, terms.ACK_VERSION)
    assert audit is not None and terms.ACK_VERSION in audit.detail
    assert "ai_acknowledgements" in NEVER_DELETED


# --- D4 ----------------------------------------------------------------------------------------------------------------------
def test_every_llm_input_and_output_is_logged_in_a_never_deleted_table():
    headers = {"Authorization": f"Bearer {_register('p08d-llm@example.com')}"}
    me = client.get("/api/auth/me", headers=headers).json()
    _upgrade_plan(me["tenant_id"], "pro")
    assert client.put("/api/ai/provider", headers=headers, json={"provider": "anthropic", "api_key": "sk-ant-llm-log-key-1234", "data_consent": True}).status_code == 200

    def handler(request):
        body = json.loads(request.content)
        if b"cut me" in request.content:
            return httpx2.Response(200, json={"id": "m", "type": "message", "role": "assistant", "model": body["model"], "stop_sequence": None, "stop_reason": "max_tokens",
                                              "content": [{"type": "text", "text": "half"}], "usage": {"input_tokens": 10, "output_tokens": 5}})
        return httpx2.Response(200, json={"id": "m", "type": "message", "role": "assistant", "model": body["model"], "stop_sequence": None, "stop_reason": "end_turn",
                                          "content": [{"type": "text", "text": "The data says 25,000."}], "usage": {"input_tokens": 100, "output_tokens": 20}})

    async def go():
        async with _session_factory() as session:
            tenant = await session.get(Tenant, me["tenant_id"])
            provider = await ai_settings.provider_for(session, tenant, client=_anthropic_mock(handler), task="copilot", user_id=me["id"])
            provider.prompt_version = "test-v1"
            text = await provider.complete("SYSTEM PROMPT", "what is the level?", max_tokens=200)
            try:
                await provider.complete("SYSTEM PROMPT", "cut me", max_tokens=50)
            except Exception:  # noqa: BLE001
                pass
            await session.commit()
            rows = list(await session.scalars(select(LlmCallRecord).where(LlmCallRecord.tenant_id == tenant.id).order_by(LlmCallRecord.id)))
            return text, rows
    text, rows = _run(go())
    assert text == "The data says 25,000." and len(rows) == 2
    ok, cut = rows
    assert ok.status == "ok" and ok.user_id == me["id"] and ok.feature == "copilot" and ok.prompt_version == "test-v1" and ok.provider == "anthropic"
    assert ok.system_text == "SYSTEM PROMPT" and ok.user_text == "what is the level?" and ok.response_text == text
    assert ok.system_sha256 == terms.hashlib.sha256(b"SYSTEM PROMPT").hexdigest() and ok.input_tokens == 100 and ok.output_tokens == 20 and ok.cost_usd > 0
    assert cut.status.startswith("error:") and "cut off" in cut.status and cut.response_text is None and cut.output_tokens == 10
    assert "llm_calls" in NEVER_DELETED
    tables = {table for table, _, _ in retention._rules(NOW, load_policy())}
    assert tables.isdisjoint({"llm_calls", "ai_acknowledgements"})


# --- D5 ----------------------------------------------------------------------------------------------------------------------
def test_data_sharing_consent_is_required_before_an_external_provider_is_saved():
    headers = {"Authorization": f"Bearer {_register('p08d-consent@example.com')}"}
    me = client.get("/api/auth/me", headers=headers).json()
    _upgrade_plan(me["tenant_id"], "pro")
    before = client.get("/api/ai/provider", headers=headers).json()["data_consent"]
    assert before["accepted"] is False and before["version"] == terms.DATA_CONSENT_VERSION and "outside India" in before["text"]["en"] and "opt out" in before["text"]["en"]
    refused = client.put("/api/ai/provider", headers=headers, json={"provider": "anthropic", "api_key": "sk-ant-consent-key-1234"})
    assert refused.status_code == 400 and refused.json()["detail"]["code"] == "data_consent_required"
    saved = client.put("/api/ai/provider", headers=headers, json={"provider": "anthropic", "api_key": "sk-ant-consent-key-1234", "data_consent": True, "language": "mr"}).json()
    assert saved["data_consent"]["accepted"] is True and saved["data_consent"]["accepted_by"] == me["id"]
    assert client.put("/api/ai/provider", headers=headers, json={"provider": "rule_based"}).status_code == 200      # opting out needs no consent

    async def audit():
        async with _session_factory() as session:
            return await session.scalar(select(AuditLogRecord).where(AuditLogRecord.tenant_id == me["tenant_id"], AuditLogRecord.event == "ai_data_consent_accepted"))
    assert _run(audit()) is not None


def test_marketplace_refuses_ai_originated_strategies_until_the_flag_is_on():
    from tests.test_phase_bd2_thesis_eval import _flags
    headers = {"Authorization": f"Bearer {_register('p08d-market@example.com')}"}
    me = client.get("/api/auth/me", headers=headers).json()
    _upgrade_plan(me["tenant_id"], "business")

    async def seed(origin):
        async with _session_factory() as session:
            row = CustomStrategyRecord(tenant_id=me["tenant_id"], user_id=me["id"], name=f"p08d {origin}", config_json=json.dumps({
                "name": f"p08d {origin}", "timeframe": "5min", "long_conditions": [], "short_conditions": [], "stop_loss_atr_mult": 1.5, "target_rr": [1.5, 2.5]}),
                origin=origin)
            session.add(row)
            await session.commit()
            return row.id
    ai_id, user_id = _run(seed("ai-strategist")), _run(seed("user"))
    body = {"title": "AI made", "description": "An AI-drafted rule set with a long enough description for the marketplace gate.", "methodology": "m"}
    _flags(marketplace_ai_listings=False)
    refused = client.post("/api/marketplace/listings", headers=headers, json={**body, "custom_strategy_id": ai_id})
    assert refused.status_code == 400 and "marketplace_ai_listings" in refused.text
    assert client.post("/api/marketplace/listings", headers=headers, json={**body, "custom_strategy_id": user_id}).status_code == 201
    _flags(marketplace_ai_listings=True)
    assert client.post("/api/marketplace/listings", headers=headers, json={**body, "custom_strategy_id": ai_id}).status_code == 201
    _flags(marketplace_ai_listings=False)


# --- review follow-ups --------------------------------------------------------------------------------------------------
def test_the_acknowledgement_is_asked_again_when_its_version_or_text_changes_and_gates_drafts_and_the_scanner(monkeypatch):
    headers = {"Authorization": f"Bearer {_register('p08d-ack-bump@example.com')}"}
    assert client.post("/api/ai/copilot", headers=headers, json={"message": "how is the market?", "language": "en"}).status_code == 200
    monkeypatch.setattr(terms, "ACK_TEXT", {**terms.ACK_TEXT, "en": terms.ACK_TEXT["en"] + " (edited)"})        # same version label, new text
    assert client.post("/api/ai/copilot", headers=headers, json={"message": "how is the market?", "language": "en"}).status_code == 428
    monkeypatch.setattr(terms, "ACK_VERSION", "2099-01-01")
    status = client.get("/api/ai/acknowledgement", headers=headers).json()
    assert status["accepted"] is False and status["version"] == "2099-01-01"
    # Every AI content path waits for it: draft read / approve and both AI scanner routes.
    assert client.get("/api/ai/drafts/1", headers=headers).status_code == 428
    assert client.post("/api/ai/drafts/1/approve", headers=headers, json={}).status_code == 428
    assert client.post("/api/scanner/ai/plan", headers=headers, json={"text": "stocks above ema 20"}).status_code == 428
    assert client.post("/api/scanner/ai/read", headers=headers, json={"request": {"symbols": []}, "result": {"scanned_count": 0, "matched_count": 0, "matches": []}}).status_code == 428
    assert client.get("/api/ai/actions", headers=headers).status_code == 200        # the proposals (exits) never wait
    assert client.post("/api/ai/acknowledgement", headers=headers, json={"version": "2099-01-01"}).json()["accepted"] is True
    assert client.post("/api/ai/copilot", headers=headers, json={"message": "how is the market?", "language": "en"}).status_code == 200


def test_telegram_ai_answers_wait_for_the_acknowledgement_but_plain_commands_do_not():
    from app.db.models import User
    from app.telegram_inbound import service as tg
    token = _register("p08d-telegram@example.com", ai_terms=False)
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).json()

    async def ask(text):
        async with _session_factory() as session:
            return await tg.answer_text(session, await session.get(Tenant, me["tenant_id"]), await session.get(User, me["id"]), text)
    for text in ("how is the market today?", "/brief", "/thesis NIFTY 50"):
        assert "accept its acknowledgement" in _run(ask(text)), text
    assert "accept" not in _run(ask("/positions"))
    client.post("/api/ai/acknowledgement", headers={"Authorization": f"Bearer {token}"}, json={"version": terms.ACK_VERSION})
    assert "accept its acknowledgement" not in _run(ask("how is the market today?"))


def test_no_data_leaves_for_an_outside_model_without_the_current_consent():
    from app.db.models import User
    headers = {"Authorization": f"Bearer {_register('p08d-consent-use@example.com')}"}
    me = client.get("/api/auth/me", headers=headers).json()
    _upgrade_plan(me["tenant_id"], "pro")

    async def provider():
        async with _session_factory() as session:
            tenant, user = await session.get(Tenant, me["tenant_id"]), await session.get(User, me["id"])
            if await ai_settings.get_config(session, tenant.id) is None:          # a provider saved before the consent existed
                await ai_settings.save_config(session, user, provider="anthropic", model=None, api_key="sk-ant-old-config-1234")
            return await ai_settings.provider_for(session, tenant, task="copilot", user_id=user.id)
    before = _run(provider())
    assert before.name == "rule_based" and "data-sharing consent" in before.reason
    # One consent, then later saves (a model change) do not ask again.
    assert client.put("/api/ai/provider", headers=headers, json={"provider": "anthropic", "api_key": "sk-ant-old-config-1234", "data_consent": True}).status_code == 200
    assert _run(provider()).name != "rule_based"
    assert client.put("/api/ai/provider", headers=headers, json={"provider": "anthropic", "model": "claude-sonnet-5-5"}).status_code == 200


def test_the_market_study_and_thesis_api_hide_single_stock_confidence_and_targets():
    from app.ai import market_study
    from tests.test_phase_bd_thesis import _snapshot
    df = _sessions(days=6, minutes=1)
    stock = market_study.study(df, "RELIANCE", "en")
    assert stock["confidence"] is None and stock["detail_shown"] is False
    assert all("target" not in s and "next reference level" not in s["text"] for s in stock["scenarios"] if s["id"] in ("bull", "bear"))
    index = market_study.study(df, "NIFTY 50", "en")
    assert index["confidence"] is not None and any("target" in s for s in index["scenarios"])
    flagged = market_study.study(df, "RELIANCE", "en", stock_detail=True)
    assert flagged["confidence"] is not None
    snapshot = _snapshot("RELIANCE") if "symbol" in _snapshot.__code__.co_varnames else _snapshot()
    thesis = th.compose("RELIANCE", snapshot, {"symbols": [snapshot], "cues": [], "sentiment": None, "globals": []}, [], [], "en", NOW)
    assert "confidence" not in thesis["agreement"] and thesis["confidence"] is None


def test_marketplace_also_refuses_publishing_and_subscribing_ai_listings_while_the_flag_is_off():
    from app.db.models import MarketplaceListingRecord, User
    from app.marketplace import service as mkt
    from tests.test_phase_bd2_thesis_eval import _flags
    headers = {"Authorization": f"Bearer {_register('p08d-market2@example.com')}"}
    me = client.get("/api/auth/me", headers=headers).json()

    async def seed():
        async with _session_factory() as session:
            cfg = {"name": "p08d pending", "timeframe": "5min", "long_conditions": [], "short_conditions": [], "stop_loss_atr_mult": 1.5, "target_rr": [1.5, 2.5]}
            strategy = CustomStrategyRecord(tenant_id=me["tenant_id"], user_id=me["id"], name="p08d pending", config_json=json.dumps(cfg), origin="ai-strategist")
            session.add(strategy)
            await session.flush()
            listing = MarketplaceListingRecord(tenant_id=me["tenant_id"], created_by=me["id"], custom_strategy_id=strategy.id, title="pending AI", description="d" * 50,
                                               methodology="m", config_json=json.dumps(cfg), status="PENDING_REVIEW", version_number=1)
            session.add(listing)
            await session.commit()
            return listing.id

    async def attempt(fn):
        async with _session_factory() as session:
            listing, user = await session.get(MarketplaceListingRecord, listing_id), await session.get(User, me["id"])
            try:
                await fn(session, user, listing)
                return "ok"
            except mkt.MarketplaceError as exc:
                return str(exc)
    listing_id = _run(seed())                      # submitted while the flag was on (or before it existed)
    _flags(marketplace_ai_listings=False)
    assert "marketplace_ai_listings" in _run(attempt(lambda s, u, listing: mkt.review(s, u, listing, publish=True, note=None)))
    assert "marketplace_ai_listings" in _run(attempt(lambda s, u, listing: mkt.activate(s, u, listing, None)))
