"""P0.8-A (ATP_AI_COPILOT_FIX_PROMPT): the monitoring agent's approvals and the Copilot's hand-offs are safe.

A1 an approved exit the broker did not complete is FAILED, never EXECUTED; A2 a LIVE proposal needs the authenticator
on the web; A3 strategist / interview candidates are the server's and pass compliance + risk acceptance; A4 one
`custom:` spelling everywhere, legacy `custom_` still resolves; A5 one decision wins and one open proposal per rule;
A6 a Telegram decision needs an authorised sender and is recorded against that person; A7 news corroboration needs an
overlapping scope, a stock circuit is not a market halt, and a pause names a matching deployment or nothing.
"""
import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pyotp
import pytest
from sqlalchemy import select

from app.ai import monitor
from app.custom_strategies.resolver import normalize_strategy_id, resolve_strategy
from app.db.models import AiActionRecord, AiCandidateRecord, AuditLogRecord, Tenant, TradeRecord, User
from app.news_feed import classify as cl
from app.news_feed import service as nf
from app.telegram_inbound import service as tg
from app.trading.position_monitor import CloseOutcome
from tests.test_auth_api import _register, _session_factory, client
from tests.test_phase_be_telegram_inbound import _Telegram, _dispatch, _post, _propose
from tests.test_trading_worker import _deploy, _tenant

UTC = timezone.utc


def _run(coro):
    return asyncio.run(coro)


def _action(action_id):
    async def go():
        async with _session_factory() as session:
            return await session.get(AiActionRecord, action_id)
    return _run(go())


def _open_trade(t, dep_id, *, mode="PAPER"):
    async def go():
        async with _session_factory() as session:
            trade = TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], mode=mode, strategy_id="ema_rsi_scalper_1m", symbol="RELIANCE", direction="LONG",
                                entry_time=datetime.now(UTC) - timedelta(hours=2), entry_price=100.0, quantity=10, stop_loss=95.0, target1=110.0, target2=115.0,
                                deployment_id=dep_id)
            session.add(trade)
            await session.commit()
            return trade.id
    return _run(go())


def _raise(t, dep_id, trade_id, action="EXIT_POSITION", rule=None):
    async def go():
        async with _session_factory() as session:
            rows = await monitor.raise_proposals(session, t["tenant_id"], [monitor.Proposal(dep_id, trade_id, action, rule or f"T_{uuid.uuid4().hex[:8]}", "test", {})],
                                                 datetime.now(UTC))
            return rows[0].id if rows else None
    return _run(go())


# --- A1 -------------------------------------------------------------------------------------------------------------------
def test_exit_the_broker_did_not_complete_is_failed_not_executed_and_may_be_proposed_again(monkeypatch):
    t = _tenant("p08a-exit@example.com")
    dep_id = _deploy(t)
    trade_id = _open_trade(t, dep_id)
    action_id = _raise(t, dep_id, trade_id, rule="STALE_POSITION")

    async def not_closed(session, trade, price, reason, *, broker=None, user_id=None, now=None):
        return CloseOutcome(trade_id=trade.id, closed=False, exit_reason="broker rejected the square-off")
    import app.trading.position_monitor as pm
    monkeypatch.setattr(pm, "close_position", not_closed)

    async def approve_and_execute():
        async with _session_factory() as session:
            action = await session.get(AiActionRecord, action_id)
            user = await session.get(User, t["user_id"])
            action = await monitor.decide(session, action, user, approve=True, note="go")
            async def price(symbol):
                return 101.0
            return await monitor.execute(session, action, user, price_lookup=price)
    row = _run(approve_and_execute())
    assert row.status == "FAILED" and "Exit not completed" in row.result and "broker rejected" in row.result
    # FAILED is not an open state: the same rule may propose again for this deployment.
    assert _raise(t, dep_id, trade_id, rule="STALE_POSITION") is not None
    # A LIVE position without a broker session is refused, never booked closed in the database alone.
    live_dep = _deploy(t, mode="LIVE", symbol="NIFTY 50")
    live_trade = _open_trade(t, live_dep, mode="LIVE")
    live_action = _raise(t, live_dep, live_trade, rule="STALE_POSITION")

    async def approve_live():
        async with _session_factory() as session:
            action = await session.get(AiActionRecord, live_action)
            user = await session.get(User, t["user_id"])
            action = await monitor.decide(session, action, user, approve=True, note="go")
            async def price(symbol):
                return 101.0
            return await monitor.execute(session, action, user, price_lookup=price, broker=None)
    live_row = _run(approve_live())
    assert live_row.status == "FAILED" and "No broker session" in live_row.result
    assert _run(_still_open(live_trade))


async def _still_open(trade_id):
    async with _session_factory() as session:
        trade = await session.get(TradeRecord, trade_id)
        return trade.exit_time is None


# --- A2 -------------------------------------------------------------------------------------------------------------------
def test_live_proposal_approval_on_the_web_needs_the_authenticator():
    t = _tenant("p08a-live-mfa@example.com")

    async def require():
        async with _session_factory() as session:
            tenant = await session.get(Tenant, t["tenant_id"])
            tenant.require_mfa_for_live = True
            await session.commit()
    _run(require())
    live_dep = _deploy(t, mode="LIVE", symbol="NIFTY 50")
    paper_dep = _deploy(t, mode="PAPER", symbol="RELIANCE")
    live_action = _raise(t, live_dep, None, action="PAUSE_DEPLOYMENT", rule="LOSING_STREAK")
    paper_action = _raise(t, paper_dep, None, action="PAUSE_DEPLOYMENT", rule="LOSING_STREAK")
    # PAPER: no step-up needed. LIVE: 403 with the MFA code until the session has passed a TOTP check.
    assert client.post(f"/api/ai/actions/{paper_action}/approve", headers=t["headers"], json={}).json()["status"] == "EXECUTED"
    refused = client.post(f"/api/ai/actions/{live_action}/approve", headers=t["headers"], json={})
    assert refused.status_code == 403 and "two-factor" in refused.text
    assert _action(live_action).status == "PROPOSED"                       # nothing was decided
    enrolled = client.post("/api/auth/mfa/enrol", headers=t["headers"]).json()
    code = pyotp.TOTP(enrolled["secret"]).now()
    assert client.post("/api/auth/mfa/confirm", headers=t["headers"], json={"code": code}).status_code == 200
    approved = client.post(f"/api/ai/actions/{live_action}/approve", headers=t["headers"], json={"note": "checked"})
    assert approved.status_code == 200 and approved.json()["status"] == "EXECUTED"


# --- A4 -------------------------------------------------------------------------------------------------------------------
def test_custom_strategy_ids_use_one_spelling_and_legacy_ids_still_resolve():
    assert normalize_strategy_id("custom_12") == "custom:12" and normalize_strategy_id("custom:12") == "custom:12"
    assert normalize_strategy_id("ema_rsi_scalper_1m") == "ema_rsi_scalper_1m" and normalize_strategy_id("custom_x") == "custom_x"
    t = _tenant("p08a-ids@example.com")          # with a broker session: a deployment needs one even in PAPER
    headers = t["headers"]
    created = client.post("/api/custom-strategies", headers=headers, json={
        "name": "ids", "timeframe": "5min",
        "long_conditions": [{"left": {"type": "indicator", "indicator": "EMA", "period": 9}, "operator": "GT", "right": {"type": "indicator", "indicator": "EMA", "period": 21}}],
        "short_conditions": [], "stop_loss_atr_mult": 1.5, "atr_period": 14, "target_rr": [1.5, 2.5], "min_rr": 1.2})
    assert created.status_code == 201, created.text
    sid = created.json()["strategy_id"]
    assert sid.startswith("custom:")
    legacy = sid.replace("custom:", "custom_")

    async def resolve():
        async with _session_factory() as session:
            user = await session.get(User, t["user_id"])
            return (await resolve_strategy(legacy, user, session)).id, (await resolve_strategy(sid, user, session)).id
    a, b = _run(resolve())
    assert a == b == sid
    # A deployment created with the legacy spelling is stored normalised and resolves for the worker.
    dep = client.post("/api/deployments", headers=headers, json={"strategy_id": legacy, "symbol": "RELIANCE", "exchange": "NSE", "timeframe": "5min", "mode": "PAPER"})
    assert dep.status_code == 201, dep.text
    assert dep.json()["strategy_id"] == sid


# --- A5 -------------------------------------------------------------------------------------------------------------------
def test_one_decision_wins_and_the_database_keeps_one_open_proposal_per_rule():
    t = _tenant("p08a-race@example.com")
    dep_id = _deploy(t)
    action_id = _raise(t, dep_id, None, action="PAUSE_DEPLOYMENT", rule="LOSING_STREAK")

    async def race():
        async with _session_factory() as s1, _session_factory() as s2:
            a1, a2 = await s1.get(AiActionRecord, action_id), await s2.get(AiActionRecord, action_id)
            u1, u2 = await s1.get(User, t["user_id"]), await s2.get(User, t["user_id"])
            first = await monitor.decide(s1, a1, u1, approve=False, note="web")
            with pytest.raises(ValueError, match="decided concurrently|REJECTED"):
                await monitor.decide(s2, a2, u2, approve=True, note="telegram")
            return first.status
    assert _run(race()) == "REJECTED"
    assert _action(action_id).status == "REJECTED"
    # The partial unique index: a second open proposal for the same (tenant, deployment, rule) is refused and skipped.
    rule = f"UNIQ_{uuid.uuid4().hex[:6]}"
    assert _raise(t, dep_id, None, action="PAUSE_DEPLOYMENT", rule=rule) is not None

    async def dup():
        async with _session_factory() as session:
            session.add(AiActionRecord(tenant_id=t["tenant_id"], deployment_id=dep_id, action="PAUSE_DEPLOYMENT", rule=rule, reason="dup", evidence_json="{}",
                                       status="PROPOSED", expires_at=datetime.now(UTC) + timedelta(hours=1)))
            try:
                await session.commit()
                return "inserted"
            except Exception as exc:  # noqa: BLE001
                await session.rollback()
                return type(exc).__name__
    assert _run(dup()) == "IntegrityError"


# --- A6 -------------------------------------------------------------------------------------------------------------------
def test_telegram_group_members_cannot_decide_and_the_real_approver_is_recorded(monkeypatch):
    telegram = _Telegram()
    # The alert channel posts to a *group* (chat -100777); the owner's private chat 987654 is whitelisted too.
    from tests.test_phase_be_telegram_inbound import _flag
    _flag(True)
    t = _tenant("p08a-tg@example.com")
    assert client.put("/api/alert-channels/telegram", headers=t["headers"], json={"config": {"bot_token": "123456:ABC-DEF-ghi", "chat_id": "-100777"}}).status_code == 200
    monkeypatch.setattr(tg, "http_client", telegram.client)
    assert client.put("/api/telegram/inbound", headers=t["headers"], json={"enabled": True, "allowed_chat_ids": ["987654"]}).json()["inbound_enabled"] is True

    async def secret_and_token():
        async with _session_factory() as session:
            from app.alerts.channels import decrypt_raw
            from app.db.models import AlertChannelRecord
            rec = await session.scalar(select(AlertChannelRecord).where(AlertChannelRecord.tenant_id == t["tenant_id"]))
            tenant = await session.get(Tenant, t["tenant_id"])
            return decrypt_raw(rec)["inbound_secret"], tenant.webhook_token_hash
    t["secret"], t["token"] = _run(secret_and_token())
    dep_id = _deploy(t, symbol="NIFTY 50")
    action_id = _propose(t, dep_id=dep_id)
    _dispatch(t, telegram)
    msg = [c for c in telegram.calls if c["method"] == "sendMessage" and "reply_markup" in c][-1]
    approve, _reject = (b["callback_data"] for b in msg["reply_markup"]["inline_keyboard"][0])
    # Legacy mode (no approvers): a member of the whitelisted group is nobody - the button is refused and audited.
    group_press = {"update_id": 9, "callback_query": {"id": "cq9", "from": {"id": 31337}, "data": approve,
                                                       "message": {"message_id": 42, "chat": {"id": -100777}, "text": "AI proposes pause"}}}
    out = _post(t, group_press).json()
    assert out["handled"] == "callback" and "not an authorised approver" in out["reply"]
    assert _action(action_id).status == "PROPOSED"
    refused_msg = _post(t, {"update_id": 10, "message": {"message_id": 2, "chat": {"id": -100777}, "from": {"id": 31337}, "text": "/positions"}}).json()
    assert refused_msg["handled"] == "refused"
    # The owner invites a member and lists that member's Telegram user id as an approver.
    inv = client.post("/api/team/invites", headers=t["headers"], json={"email": "p08a-tg-member@example.com", "role": "USER"}).json()
    token = parse_qs(urlparse(inv["invite_url"]).query)["invite"][0]
    member = client.post(f"/api/auth/invite/{token}/accept", json={"password": "Member-Pass-2026"}).json()
    member_id = client.get("/api/auth/me", headers={"Authorization": f"Bearer {member['access_token']}"}).json()["id"]
    bad = client.put("/api/telegram/inbound", headers=t["headers"], json={"enabled": True, "allowed_chat_ids": ["987654"],
                                                                           "approvers": [{"telegram_user_id": "31337", "email": "nobody@example.com"}]})
    assert bad.status_code == 400
    status = client.put("/api/telegram/inbound", headers=t["headers"], json={"enabled": True, "allowed_chat_ids": ["987654"],
                                                                              "approvers": [{"telegram_user_id": "31337", "email": "p08a-tg-member@example.com"}]}).json()
    assert status["approvers"] == [{"telegram_user_id": "31337", "user_id": member_id, "email": "p08a-tg-member@example.com"}]
    # Now the same press decides, and the decision is recorded against the member, not the owner.
    out = _post(t, group_press).json()
    assert out["handled"] == "callback" and "EXECUTED" in out["reply"]
    row = _action(action_id)
    assert row.status == "EXECUTED" and row.decided_by == member_id and "by user 31337" in (row.decision_note or "")
    # With approvers configured, the owner's own private chat no longer acts unless listed.
    dep2 = _deploy(t, symbol="RELIANCE")
    action2 = _propose(t, dep_id=dep2)
    _dispatch(t, telegram)
    msg2 = [c for c in telegram.calls if c["method"] == "sendMessage" and "reply_markup" in c][-1]
    approve2 = msg2["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    owner_press = {"update_id": 11, "callback_query": {"id": "cq11", "from": {"id": -100777}, "data": approve2,
                                                        "message": {"message_id": 43, "chat": {"id": -100777}, "text": "AI proposes pause"}}}
    assert "not an authorised approver" in _post(t, owner_press).json()["reply"]
    assert _action(action2).status == "PROPOSED"

    async def audits():
        async with _session_factory() as session:
            rows = await session.scalars(select(AuditLogRecord).where(AuditLogRecord.tenant_id == t["tenant_id"]))
            return [r.event for r in rows]
    acts = _run(audits())
    assert "telegram_callback_refused" in acts and "telegram_inbound_refused" in acts and "telegram_decision" in acts


def test_telegram_rate_limit_counts_in_redis_when_available(monkeypatch):
    calls = {}

    async def incr(key, ttl):
        calls[key] = calls.get(key, 0) + 1
        return calls[key]
    monkeypatch.setattr(tg, "cache_incr_window", incr)
    tenant_id = 424242
    assert all(_run(tg.rate_limited(tenant_id, "c", now=1000.0)) is False for _ in range(tg.RATE_LIMIT))
    assert _run(tg.rate_limited(tenant_id, "c", now=1000.0)) is True
    assert _run(tg.rate_limited(tenant_id, "c", now=1000.0 + tg.RATE_WINDOW_SECONDS)) is False          # next window

    async def down(key, ttl):
        return None
    monkeypatch.setattr(tg, "cache_incr_window", down)
    tg._rate.clear()
    assert _run(tg.rate_limited(tenant_id, "d", now=5.0)) is False                                     # in-process fallback
    tg._rate.clear()


# --- A7 -------------------------------------------------------------------------------------------------------------------
def test_news_scope_circuit_and_pause_target():
    assert cl.keyword_classify("Suzlon hits upper circuit after order win")["type"] == "CORPORATE"
    assert cl.keyword_classify("Suzlon hits upper circuit after order win")["severity"] == 3
    assert cl.keyword_classify("Vodafone Idea at lower circuit")["direction"] == "BEARISH"
    halt = cl.keyword_classify("NSE trading halt: market-wide circuit breaker triggered")
    assert halt["type"] == "LIQUIDITY" and halt["severity"] == 5
    assert cl.keyword_classify("Short circuit at Bandra substation")["severity"] < 5
    t = _tenant("p08a-news@example.com")
    nifty = _deploy(t, symbol="NIFTY 50")
    reliance = _deploy(t, symbol="RELIANCE")
    infy = _deploy(t, symbol="INFY")
    # Corroboration: two items whose classifications share no scope do not confirm each other.
    now = datetime.now(UTC)

    async def seed(headline, feed, scope, kind="RATE_DECISION", severity=4, symbols=()):
        async with _session_factory() as session:
            from app.db.models import NewsEventRecord
            row = NewsEventRecord(category="RBI_POLICY", headline=headline, event_date=now.date(), affected_symbols_json="[]", sentiment="Neutral",
                                  source_json='{"source":"x"}', origin="FEED", verified=False, dedupe_hash=f"p08a-{uuid.uuid4().hex[:8]}", feed_id=feed,
                                  created_at=now, published_at=now,
                                  classification_json=json.dumps({"type": kind, "scope": list(scope), "symbols": list(symbols), "direction": "NEUTRAL", "severity": severity}))
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row
    _run(seed("p08a A", "rbi_press", []))
    b = _run(seed("p08a B", "sebi_press", []))

    async def confirmed(row):
        async with _session_factory() as session:
            row = await session.get(type(row), row.id)
            return await nf.confirmed_by_second_source(session, row, nf.classification_of(row))
    assert _run(confirmed(b)) is False
    _run(seed("p08a C", "rbi_press", ["INDEX"]))
    d = _run(seed("p08a D", "sebi_press", ["INDEX"]))
    assert _run(confirmed(d)) is True
    # A severity-5 pause names the deployment the news is about - never the first active one.
    async def propose(row, severity, classification):
        async with _session_factory() as session:
            row = await session.get(type(row), row.id)
            return await nf.propose(session, t["tenant_id"], row, severity, "AI classification", now, classification=classification)
    stock_news = _run(seed("p08a INFY probe", "sebi_press", ["IT"], kind="CORPORATE", severity=5, symbols=["INFY"]))
    assert _run(propose(stock_news, 5, {"type": "CORPORATE", "scope": ["IT"], "symbols": ["INFY"], "severity": 5, "direction": "BEARISH"})) == 1

    async def actions():
        async with _session_factory() as session:
            return list(await session.scalars(select(AiActionRecord).where(AiActionRecord.tenant_id == t["tenant_id"])))
    rows = _run(actions())
    assert len(rows) == 1 and rows[0].action == "PAUSE_DEPLOYMENT" and rows[0].deployment_id == infy
    unrelated = _run(seed("p08a Adani raid", "sebi_press", ["ENERGY"], kind="CORPORATE", severity=5, symbols=["ADANIENT"]))
    # News about another stock (even in RELIANCE's sector) pauses nothing - no deployment is named.
    assert _run(propose(unrelated, 5, {"type": "CORPORATE", "scope": ["ENERGY"], "symbols": ["ADANIENT"], "severity": 5, "direction": "BEARISH"})) == 0
    halt_row = _run(seed("p08a halt", "rbi_press", ["INDEX"], kind="LIQUIDITY", severity=5))
    assert _run(propose(halt_row, 5, {"type": "LIQUIDITY", "scope": ["INDEX"], "symbols": [], "severity": 5, "direction": "BEARISH"})) == 1
    rows = _run(actions())
    assert {r.deployment_id for r in rows if r.action == "PAUSE_DEPLOYMENT"} == {infy, nifty} and reliance not in {r.deployment_id for r in rows}


# --- A3 (interview) ------------------------------------------------------------------------------------------------------------
def test_interview_deploy_goes_through_the_candidate_gate():
    from tests.test_phase_ap_interview import _sessions
    t = _tenant("p08a-interview@example.com")
    headers = t["headers"]
    df = _sessions(days=5)
    candles = [{"timestamp": ts.isoformat(), "open": r.open, "high": r.high, "low": r.low, "close": r.close, "volume": r.volume} for ts, r in df.iterrows()]
    plan = client.post("/api/ai/interview/plan", headers=headers, json={
        "answers": {"language": "en", "experience": "learning", "capital": 300000, "risk": "moderate", "style": "intraday", "vehicle": "option_buy", "goal": "big_trends"},
        "base_timeframe": "5min", "candles": candles, "data_source": "broker:upstox"}).json()
    assert plan["candidate_id"] and all(o["candidate_id"] for o in plan["options"])
    cid = plan["candidate_id"]
    # Five sessions of synthetic candles give the templates no trades: the gate refuses before anything else is looked at.
    refused = client.post("/api/ai/interview/deploy", headers=headers, json={"candidate_id": cid, "accept_risk": True})
    assert refused.status_code == 400 and "backtest evidence" in refused.text

    # With server-side evidence (as a longer run produces) on a cash deployment, the risk statement must still be accepted.
    async def with_evidence():
        async with _session_factory() as session:
            row = await session.get(AiCandidateRecord, cid)
            metrics = json.loads(row.metrics_json or "{}")
            metrics["evidence"] = {"tested": True, "total_trades": 18, "win_rate": 0.5}
            payload = json.loads(row.deployment_json or "{}")
            for key in ("option_position", "expiry_rule", "strike_rule", "premium_stop_pct", "premium_ceiling_pct", "strike_offset", "strike_filters", "max_lots"):
                payload.pop(key, None)
            payload.update({"symbol": "RELIANCE", "exchange": "NSE", "instrument_kind": "UNDERLYING", "mode": "LIVE"})
            row.metrics_json, row.deployment_json = json.dumps(metrics), json.dumps(payload)
            await session.commit()
            return payload["strategy_id"]
    strategy_id = _run(with_evidence())
    refused = client.post("/api/ai/interview/deploy", headers=headers, json={"candidate_id": cid})
    assert refused.status_code == 400 and "accept" in refused.text and "maximum loss per trade" in refused.text
    deployed = client.post("/api/ai/interview/deploy", headers=headers, json={"candidate_id": cid, "accept_risk": True})
    assert deployed.status_code == 201, deployed.text
    body = deployed.json()
    # The stored payload said LIVE; the hand-off forces PAPER regardless.
    assert body["mode"] == "PAPER" and body["deployment"]["mode"] == "PAPER" and body["deployment"]["symbol"] == "RELIANCE"
    assert body["deployment"]["strategy_id"] == strategy_id
    assert client.post("/api/ai/interview/deploy", headers=headers, json={"candidate_id": cid, "accept_risk": True}).status_code == 409
    other = {"Authorization": f"Bearer {_register('p08a-interview-other@example.com')}"}
    assert client.post("/api/ai/interview/deploy", headers=other, json={"candidate_id": cid, "accept_risk": True}).status_code == 404

    async def status():
        async with _session_factory() as session:
            row = await session.get(AiCandidateRecord, cid)
            audit = await session.scalar(select(AuditLogRecord).where(AuditLogRecord.tenant_id == t["tenant_id"], AuditLogRecord.event == "ai_candidate_deployed"))
            return row.status, row.deployment_id, audit is not None
    st, dep_id, audited = _run(status())
    assert st == "DEPLOYED" and dep_id == body["deployment"]["id"] and audited
