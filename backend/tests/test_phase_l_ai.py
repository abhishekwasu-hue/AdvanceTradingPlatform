"""Phase L: the AI layer - provider seam with encrypted per-tenant keys, the strategy generator
behind its review gate, the regime engine and filter, and the monitoring agent's action-state
machine with human approval."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import numpy as np
import pandas as pd
from sqlalchemy import select

from app.ai import generator, monitor
from app.ai.generator import parse_answer
from app.ai.providers import AnthropicProvider, OpenAIProvider, ProviderError, RuleBasedProvider, build_provider
from app.ai.regime import classify_regime, regime_blocks, validate_filter
from app.db.models import AiActionRecord, AiProviderConfigRecord, CustomStrategyRecord, StrategyDeploymentRecord, Tenant, TradeRecord, User
from app.secrets_store.encryption import decrypt_text
from tests.test_auth_api import _register, _session_factory, client
from tests.test_trading_worker import OPEN_NOW, _FakeBroker, _deploy, _force_signal, _get, _signal, _tenant as _worker_tenant, _trades, _worker
from tests.utils import decline_then_rally, make_series, noisy_uptrend


def _run(coro):
    return asyncio.run(coro)


def _owner(email: str, plan: str = "pro"):
    headers = {"Authorization": f"Bearer {_register(email)}"}
    me = client.get("/api/auth/me", headers=headers).json()

    async def go():
        async with _session_factory() as session:
            tenant = await session.get(Tenant, me["tenant_id"])
            tenant.plan = plan
            await session.commit()
    _run(go())
    return headers, me


VALID_ANSWER = json.dumps({
    "config": {"name": "AI EMA pullback", "timeframe": "5min",
               "long_conditions": [{"left": {"type": "indicator", "indicator": "EMA", "period": 20}, "operator": "GT", "right": {"type": "indicator", "indicator": "EMA", "period": 50}},
                                   {"left": {"type": "indicator", "indicator": "RSI", "period": 14}, "operator": "CROSSES_ABOVE", "right": {"type": "value", "value": 40}}],
               "short_conditions": [], "stop_loss_atr_mult": 1.5, "atr_period": 14, "target_rr": [1.5, 2.5], "min_rr": 1.2},
    "explanation": "Buys pullbacks in an uptrend when RSI turns back up.",
    "warnings": ["Chops in ranges", "Costs matter on 5-minute bars"],
})


def _candles(prices):
    df = make_series(prices)
    return [{"timestamp": ts.isoformat(), "open": r.open, "high": r.high, "low": r.low, "close": r.close, "volume": r.volume} for ts, r in df.iterrows()]


# --- L1 providers + settings ---------------------------------------------------------------------

def _anthropic_mock(handler):
    import httpx2
    from anthropic import DefaultAsyncHttpxClient
    return DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler))


def test_anthropic_provider_uses_the_sdk_and_maps_errors():
    import httpx2
    seen = []

    def handler(request):
        seen.append(request)
        assert request.url.path == "/v1/messages" and request.headers["x-api-key"] == "sk-ant-test"
        if request.headers["x-api-key"] == "sk-ant-test" and b"refuse me" in request.content:
            return httpx2.Response(200, json={"id": "msg_2", "type": "message", "role": "assistant", "model": "claude-opus-5", "content": [],
                                              "stop_reason": "refusal", "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1},
                                              "stop_details": {"type": "refusal", "category": "cyber", "explanation": "declined"}})
        return httpx2.Response(200, json={"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5",
                                          "content": [{"type": "thinking", "thinking": "", "signature": "x"}, {"type": "text", "text": "hello from claude"}],
                                          "stop_reason": "end_turn", "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}})
    provider = AnthropicProvider("sk-ant-test", http_client=_anthropic_mock(handler))
    assert provider.model == "claude-opus-5"
    assert _run(provider.complete("sys", "make a strategy")) == "hello from claude"
    body = json.loads(seen[0].content)
    assert body["model"] == "claude-opus-5" and body["system"] == "sys" and body["messages"] == [{"role": "user", "content": "make a strategy"}]
    assert body["thinking"] == {"type": "adaptive"} and body["output_config"] == {"effort": "medium"} and body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in seen[0].headers["anthropic-beta"]
    assert set(body) == {"model", "max_tokens", "system", "messages", "thinking", "output_config", "fallbacks"}   # prompt text only, no credentials
    try:
        _run(AnthropicProvider("sk-ant-test", http_client=_anthropic_mock(handler)).complete("sys", "refuse me"))
        assert False, "expected ProviderError"
    except ProviderError as exc:
        assert "declined" in str(exc)

    def unauthorized(request):
        return httpx2.Response(401, json={"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}})
    try:
        _run(AnthropicProvider("sk-ant-bad", http_client=_anthropic_mock(unauthorized)).complete("s", "u"))
        assert False
    except ProviderError as exc:
        assert "401" in str(exc)


def test_openai_provider_parsing_and_error_mapping():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("Authorization") == "Bearer bad":
            return httpx.Response(401, json={})
        return httpx.Response(200, json={"choices": [{"message": {"content": "hello from gpt"}}]})
    mock = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert _run(OpenAIProvider("sk-openai", client=mock).complete("s", "u")) == "hello from gpt"
    try:
        _run(OpenAIProvider("bad", client=mock).complete("s", "u"))
        assert False, "expected ProviderError"
    except ProviderError as exc:
        assert "401" in str(exc)
    assert isinstance(build_provider("rule_based", None, None), RuleBasedProvider)
    try:
        build_provider("anthropic", None, None)
        assert False
    except ProviderError:
        pass


def test_provider_config_is_owner_only_encrypted_and_never_returned():
    headers, me = _owner("ai-provider@example.com")
    default = client.get("/api/ai/provider", headers=headers).json()
    assert default["provider"] == "rule_based" and default["configured"] is False and default["ai_features_allowed"] is True
    saved = client.put("/api/ai/provider", headers=headers, json={"provider": "anthropic", "api_key": "sk-ant-secret-value-123", "model": "claude-opus-5"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["api_key_set"] is True and "sk-ant-secret" not in saved.text

    async def stored():
        async with _session_factory() as session:
            rec = await session.scalar(select(AiProviderConfigRecord).where(AiProviderConfigRecord.tenant_id == me["tenant_id"]))
            return rec.encrypted_api_key
    cipher = _run(stored())
    assert cipher != "sk-ant-secret-value-123" and decrypt_text(cipher) == "sk-ant-secret-value-123"
    # Switching model keeps the stored key; switching provider without a key is refused.
    assert client.put("/api/ai/provider", headers=headers, json={"provider": "anthropic", "model": "claude-sonnet-5"}).json()["api_key_set"] is True
    assert client.put("/api/ai/provider", headers=headers, json={"provider": "openai"}).status_code == 400
    assert any(l["event"] == "ai_provider_configured" for l in client.get("/api/audit-logs", headers=headers).json())
    assert client.delete("/api/ai/provider", headers=headers).status_code == 204
    assert client.get("/api/ai/provider", headers=headers).json()["configured"] is False

    free_headers, _ = _owner("ai-provider-free@example.com", plan="free")
    assert client.put("/api/ai/provider", headers=free_headers, json={"provider": "openai", "api_key": "sk-openai-xxxxxxxx"}).status_code == 402
    assert client.put("/api/ai/provider", headers=free_headers, json={"provider": "rule_based"}).status_code == 200


# --- L2 generator + review gate -------------------------------------------------------------------

def test_parse_answer_strips_fences_and_validates():
    config, explanation, warnings = parse_answer("```json\n" + VALID_ANSWER + "\n```")
    assert config.name == "AI EMA pullback" and len(config.long_conditions) == 2 and warnings[0] == "Chops in ranges"
    try:
        parse_answer(json.dumps({"config": {"name": "x", "timeframe": "5min", "long_conditions": [], "short_conditions": []}}))
        assert False
    except Exception as exc:
        assert "at least one" in str(exc)


class _ScriptedProvider:
    name, model = "anthropic", "test-model"

    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    async def complete(self, system, user, *, max_tokens=2000):
        self.calls.append(user)
        return self.answers.pop(0)


def test_generator_retries_once_then_review_gate_requires_backtest_before_approval():
    headers, me = _owner("ai-gen@example.com")

    async def gen(provider):
        async with _session_factory() as session:
            tenant = await session.get(Tenant, me["tenant_id"])
            user = await session.get(User, me["id"])
            return (await generator.generate(session, tenant, user, "Buy pullbacks in an uptrend on 5 minute bars", provider=provider)).id
    provider = _ScriptedProvider(["not json at all", VALID_ANSWER])
    draft_id = _run(gen(provider))
    assert len(provider.calls) == 2 and "previous answer was invalid" in provider.calls[1]
    draft = client.get(f"/api/ai/drafts/{draft_id}", headers=headers).json()
    assert draft["status"] == "DRAFT" and draft["config"]["name"] == "AI EMA pullback" and draft["lineage"]["model"] == "test-model"
    assert draft["raw_response"] == VALID_ANSWER and "not advice" in draft["disclaimer"]

    # Approval without a backtest is refused (safety rule 16).
    refused = client.post(f"/api/ai/drafts/{draft_id}/approve", headers=headers, json={})
    assert refused.status_code == 400 and "Backtest" in refused.json()["detail"]

    bt = client.post(f"/api/ai/drafts/{draft_id}/backtest", headers=headers, json={"symbol": "TEST", "base_timeframe": "1min", "candles": _candles(noisy_uptrend(400))})
    assert bt.status_code == 200, bt.text
    assert bt.json()["draft"]["status"] == "BACKTESTED" and bt.json()["run"]["strategy_id"] == f"ai_draft_{draft_id}"
    assert bt.json()["run"]["id"] == bt.json()["draft"]["backtest_run_id"]

    approved = client.post(f"/api/ai/drafts/{draft_id}/approve", headers=headers, json={"name": "Pullback (AI, reviewed)"})
    assert approved.status_code == 200, approved.text
    strategy_id = approved.json()["custom_strategy_id"]
    assert approved.json()["origin"] == f"ai:{draft_id}"

    async def record():
        async with _session_factory() as session:
            return await session.get(CustomStrategyRecord, strategy_id)
    rec = _run(record())
    assert rec.ai_approved_by == me["id"] and rec.origin == f"ai:{draft_id}" and rec.name == "Pullback (AI, reviewed)"
    assert any(s["id"] == strategy_id for s in client.get("/api/custom-strategies", headers=headers).json())
    drafts = client.get("/api/ai/drafts", headers=headers).json()
    assert drafts[0]["status"] == "APPROVED" and drafts[0]["strategy_id"] == f"custom_{strategy_id}"
    events = {l["event"] for l in client.get("/api/audit-logs", headers=headers).json()}
    assert {"ai_strategy_generated", "ai_strategy_approved"} <= events

    # A failed generation is recorded, not raised; rejecting leaves a note.
    failed_id = _run(gen(_ScriptedProvider(["garbage", "still garbage"])))
    assert client.get(f"/api/ai/drafts/{failed_id}", headers=headers).json()["status"] == "FAILED"
    ok_id = _run(gen(_ScriptedProvider([VALID_ANSWER])))
    rejected = client.post(f"/api/ai/drafts/{ok_id}/reject", headers=headers, json={"note": "Too many conditions"}).json()
    assert rejected["status"] == "REJECTED" and "Too many conditions" in rejected["explanation"]


def test_generate_endpoint_uses_rule_based_provider_when_none_configured():
    headers, _ = _owner("ai-gen-rule@example.com")
    draft = client.post("/api/ai/drafts", headers=headers, json={"prompt": "Go long when RSI(14) crosses above 30 on 5min"})
    assert draft.status_code == 201, draft.text
    assert draft.json()["provider"] == "rule_based" and draft.json()["status"] == "DRAFT"
    assert draft.json()["config"]["long_conditions"][0]["left"]["indicator"] == "RSI"


# --- L3 regime -------------------------------------------------------------------------------------

def _trend_df(n=300, up=True):
    idx = np.arange(n)
    prices = 100 + (idx * 0.15 if up else -idx * 0.15) + np.sin(idx / 6) * 0.2
    return make_series(list(prices))


def _range_prices(n=300):
    rng = np.random.default_rng(3)
    idx = np.arange(n)
    return list(100 + np.sin(idx / 2.5) * 0.8 + rng.normal(0, 0.1, n))


def _range_df(n=300):
    return make_series(_range_prices(n))


def test_regime_classifier_distinguishes_trend_range_and_volatility():
    up = classify_regime(_trend_df(up=True))
    down = classify_regime(_trend_df(up=False))
    flat = classify_regime(_range_df())
    assert up.kind == "TRENDING_UP" and down.kind == "TRENDING_DOWN", (up, down)
    assert flat.kind in ("RANGING", "QUIET"), flat
    spike_prices = _range_prices(280) + list(np.linspace(100, 112, 20))
    volatile = classify_regime(make_series(spike_prices))
    assert volatile.kind == "VOLATILE" and volatile.atr_ratio > 1.6, volatile
    assert classify_regime(_range_df(30)).kind == "UNKNOWN"
    assert regime_blocks(up, ["TRENDING_UP"]) is None
    assert "not in RANGING" in regime_blocks(up, ["RANGING"])
    assert regime_blocks(up, []) is None
    assert validate_filter(["trending_up".upper(), "RANGING"]) == ["RANGING", "TRENDING_UP"]
    try:
        validate_filter(["SIDEWAYS"])
        assert False
    except ValueError:
        pass


def test_regime_endpoint_and_deployment_filter_blocks_entries(monkeypatch):
    headers, _ = _owner("ai-regime@example.com")
    body = client.post("/api/ai/regime", headers=headers, json={"candles": _candles(list(100 + np.arange(300) * 0.15))}).json()
    assert body["kind"] == "TRENDING_UP" and body["adx"] > 25
    assert client.get("/api/ai/regimes").json()["regimes"][0] == "TRENDING_UP"

    # Worker: the fake broker's candles are a mild random walk -> not TRENDING_*; a filter that
    # demands a trend skips the forced signal and records why.
    t = _worker_tenant("w-regime@example.com")
    dep_id = _deploy(t)

    async def set_filter():
        async with _session_factory() as session:
            dep = await session.get(StrategyDeploymentRecord, dep_id)
            dep.regime_filter = "TRENDING_UP,TRENDING_DOWN"
            await session.commit()
    _run(set_filter())
    _force_signal(monkeypatch, _signal)
    worker = _worker(monkeypatch, _FakeBroker(ltp=101.0))
    monkeypatch.setattr("app.workers.trading_worker.classify_regime", lambda df: type("R", (), {"kind": "RANGING", "confidence": 0.8, "reasons": ["ADX 12.0", "flat"]})())
    report = _run(worker.run_cycle(now=OPEN_NOW))
    assert report.signals_executed == 0 and _trades(t["tenant_id"]) == []
    assert "Regime RANGING" in _get(StrategyDeploymentRecord, dep_id).last_error

    created = client.post("/api/deployments", headers=t["headers"], json={"strategy_id": "ema_rsi_scalper_1m", "symbol": "TCS", "regime_filter": ["trending_up"]})
    assert created.status_code == 201, created.text
    assert created.json()["regime_filter"] == ["TRENDING_UP"] and "trending up" in created.json()["contract_rules"]
    assert client.post("/api/deployments", headers=t["headers"], json={"strategy_id": "ema_rsi_scalper_1m", "symbol": "TCS", "regime_filter": ["sideways"]}).status_code == 422


# --- L4 monitoring agent ------------------------------------------------------------------------

def _seed_trades(tenant_id: int, dep_id: int, user_id: int, pnls, *, open_position=False, now=None):
    now = now or datetime.now(timezone.utc)

    async def go():
        async with _session_factory() as session:
            for i, pnl in enumerate(pnls):
                session.add(TradeRecord(tenant_id=tenant_id, user_id=user_id, mode="PAPER", strategy_id="ema_rsi_scalper_1m", symbol="RELIANCE",
                                        direction="LONG", entry_time=now - timedelta(minutes=60 - i), entry_price=100.0, quantity=10, stop_loss=99.0,
                                        target1=102.0, target2=104.0, exit_time=now - timedelta(minutes=50 - i), exit_price=100.0 + pnl / 10,
                                        exit_reason="TEST", pnl=pnl, deployment_id=dep_id))
            if open_position:
                session.add(TradeRecord(tenant_id=tenant_id, user_id=user_id, mode="PAPER", strategy_id="ema_rsi_scalper_1m", symbol="RELIANCE",
                                        direction="LONG", entry_time=now - timedelta(minutes=200), entry_price=100.0, quantity=10, stop_loss=95.0,
                                        target1=110.0, target2=115.0, deployment_id=dep_id))
            await session.commit()
    _run(go())


def test_monitor_proposes_pause_on_losing_streak_and_executes_only_after_approval():
    t = _worker_tenant("w-ai-monitor@example.com")
    dep_id = _deploy(t)
    now = datetime.now(timezone.utc)
    _seed_trades(t["tenant_id"], dep_id, t["user_id"], [-300, -250, -400], now=now)

    async def observe_and_raise():
        async with _session_factory() as session:
            deps = list(await session.scalars(select(StrategyDeploymentRecord).where(StrategyDeploymentRecord.tenant_id == t["tenant_id"])))
            proposals = await monitor.observe(session, t["tenant_id"], deps, now)
            created = await monitor.raise_proposals(session, t["tenant_id"], proposals, now)
            again = await monitor.raise_proposals(session, t["tenant_id"], await monitor.observe(session, t["tenant_id"], deps, now), now)
            return [(p.rule, p.action) for p in proposals], len(created), len(again)
    rules, created, again = _run(observe_and_raise())
    assert ("LOSING_STREAK", "PAUSE_DEPLOYMENT") in rules and created >= 1 and again == 0   # de-duplicated

    actions = client.get("/api/ai/actions", headers=t["headers"]).json()
    streak = next(a for a in actions if a["rule"] == "LOSING_STREAK")
    assert streak["status"] == "PROPOSED" and streak["deployment_id"] == dep_id and streak["evidence"]["losses"] == [-300, -250, -400]
    assert _get(StrategyDeploymentRecord, dep_id).status == "ACTIVE"     # nothing happened yet
    notes = client.get("/api/notifications", headers=t["headers"]).json()
    assert any(n["event_type"] == "AI_PROPOSAL" for n in (notes if isinstance(notes, list) else notes.get("items", [])))

    approved = client.post(f"/api/ai/actions/{streak['id']}/approve", headers=t["headers"], json={"note": "agreed"})
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "EXECUTED" and "paused" in approved.json()["result"]
    dep = _get(StrategyDeploymentRecord, dep_id)
    assert dep.status == "PAUSED" and f"AI proposal #{streak['id']}" in dep.pause_reason
    assert client.post(f"/api/ai/actions/{streak['id']}/approve", headers=t["headers"], json={}).status_code == 409   # already decided

    # Other tenants cannot see or decide it.
    other, _ = _owner("ai-monitor-other@example.com")
    assert client.post(f"/api/ai/actions/{streak['id']}/reject", headers=other, json={}).status_code == 404


def test_monitor_rejection_expiry_and_drawdown_rule():
    t = _worker_tenant("w-ai-monitor2@example.com")
    dep_id = _deploy(t)
    now = datetime.now(timezone.utc)
    _seed_trades(t["tenant_id"], dep_id, t["user_id"], [-1500, 200, -1200], now=now)   # -2500 on 100k capital = -2.5%

    async def go():
        async with _session_factory() as session:
            deps = list(await session.scalars(select(StrategyDeploymentRecord).where(StrategyDeploymentRecord.id == dep_id)))
            proposals = await monitor.observe(session, t["tenant_id"], deps, now)
            created = await monitor.raise_proposals(session, t["tenant_id"], proposals, now)
            return [p.rule for p in proposals], [c.id for c in created]
    rules, ids = _run(go())
    assert rules == ["DAY_DRAWDOWN"]
    rejected = client.post(f"/api/ai/actions/{ids[0]}/reject", headers=t["headers"], json={"note": "acceptable variance"}).json()
    assert rejected["status"] == "REJECTED" and rejected["decision_note"] == "acceptable variance"
    assert _get(StrategyDeploymentRecord, dep_id).status == "ACTIVE"

    async def expire():
        async with _session_factory() as session:
            row = AiActionRecord(tenant_id=t["tenant_id"], deployment_id=dep_id, action="REVIEW_STRATEGY", rule="WIN_RATE_DRIFT", reason="x",
                                 status="PROPOSED", expires_at=now - timedelta(hours=1))
            session.add(row)
            await session.commit()
            count = await monitor.expire_stale(session, now)
            return row.id, count
    row_id, count = _run(expire())
    assert count >= 1 and _get(AiActionRecord, row_id).status == "EXPIRED"


def test_worker_cycle_raises_proposals_for_error_streaks(monkeypatch):
    t = _worker_tenant("w-ai-worker@example.com")
    dep_id = _deploy(t)

    async def break_it():
        async with _session_factory() as session:
            dep = await session.get(StrategyDeploymentRecord, dep_id)
            dep.consecutive_failures = 2   # the failing cycle below makes it 3 = the agent's threshold
            dep.last_error = "boom"
            await session.commit()
    _run(break_it())
    worker = _worker(monkeypatch, _FakeBroker(ltp=101.0))
    monkeypatch.setattr("app.strategy_engine.registry.registry.get", lambda sid: (_ for _ in ()).throw(RuntimeError("engine down")))
    report = _run(worker.run_cycle(now=OPEN_NOW))
    assert report.ai_proposals >= 1
    actions = client.get("/api/ai/actions?status=PROPOSED", headers=t["headers"]).json()
    assert any(a["rule"] == "ERROR_STREAK" and a["deployment_id"] == dep_id for a in actions)
