"""N3 S9 (spec §5): the screener in the Copilot (flag screener_copilot, off by default).

- the tools are offered and run only with the flag on; build_screen validates with the Screener's own gates;
- run_screen runs live on server bars (a stored Screener run) or as of a past date on the lake; explain_results gives
  the engine's own numbers per condition;
- propose_alert only creates a PROPOSED CREATE_ALERT for the person who asked - through the agent's injection guard -
  and the rule exists only after approval;
- the plain-language box: a validated draft (one repair turn), the output filter on the words, nothing run or stored."""
import asyncio
import json
import types
import uuid
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from app.ai import agent, tools
from app.ai.tools import ToolContext, proposals, run_tool
from app.ai.tools import screener as S
from app.core.models import OHLCVBar
from app.db.models import AiActionRecord, AlertRuleRecord, ScreenRunRecord, User
from app.platform.controls import DEFAULT_OFF_FLAGS
from app.screener import history as H
from app.screener import nl
from tests.test_auth_api import _session_factory, client
from tests.test_hc2a_agent import _Scripted
from tests.test_n1_e1_execution_core import _flag
from tests.test_n3_s6_history import _lake_rows, _seed, _universe
from tests.test_trading_worker import _tenant

NOW = datetime.now(timezone.utc)


def _run(coro):
    return asyncio.run(coro)


def _org(tag, on=True, extra=()):
    t = _tenant(f"n3s9-{tag}-{uuid.uuid4().hex[:6]}@example.com", with_credentials=False)
    for name in ("screener_v2",) + ((S.FLAG,) if on else ()) + tuple(extra):
        _flag(name, t["tenant_id"])
    return t


async def _with_ctx(user_id, fn):
    async with _session_factory() as session:
        user = await session.get(User, user_id)
        return await fn(ToolContext(session, user.tenant_id, user, NOW))


def _tool(t, name, args, **kw):
    return _run(_with_ctx(t["user_id"], lambda ctx: run_tool(name, args, ctx, **kw)))


def _broker(monkeypatch):
    from app.brokers import token_lifecycle
    from app.market_data import candles_routes, service

    async def pick(session, tenant_id, broker, label):
        return types.SimpleNamespace(broker_name="fake")

    async def candles(self, symbol, exchange="NSE", interval="1min", now=None):
        step = {"UP": 1.0, "DOWN": -1.0}.get(symbol)
        if step is None:
            raise RuntimeError("unknown symbol")
        idx = pd.date_range(end=pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta(days=2), periods=80, freq="1D")
        closes = 100 + np.arange(80) * step
        return [OHLCVBar(timestamp=t.to_pydatetime(), open=float(c) - 0.5, high=float(c) + 1, low=float(c) - 1, close=float(c), volume=1000.0)
                for t, c in zip(idx, closes)]
    monkeypatch.setattr(candles_routes, "_pick_record", pick)
    monkeypatch.setattr(token_lifecycle, "build_adapter", lambda record: object())
    monkeypatch.setattr(service.MarketDataService, "get_candles", candles)


# --- the flag and the registry ---------------------------------------------------------------------------------------------------

def test_the_tools_are_flagged_off_by_default_and_not_offered():
    assert S.FLAG in DEFAULT_OFF_FLAGS and nl.FLAG == S.FLAG
    reg = tools.registry()
    assert {reg[n].kind for n in ("build_screen", "run_screen", "explain_results")} == {"read"} and reg["propose_alert"].kind == "proposal"
    assert all(reg[n].flag == S.FLAG for n in ("build_screen", "run_screen", "explain_results", "propose_alert"))
    off, on = _org("off", on=False), _org("on")
    offered_off = _run(_with_ctx(off["user_id"], lambda ctx: tools.available(ctx, tools.names("read"))))
    offered_on = _run(_with_ctx(on["user_id"], lambda ctx: tools.available(ctx, tools.names("read"))))
    assert "build_screen" not in offered_off and "get_positions" in offered_off and "build_screen" in offered_on
    refused = _tool(off, "build_screen", {"text": "close > 1"})
    assert not refused.ok and S.FLAG in refused.error


def test_build_screen_validates_with_the_screener_s_gates():
    t = _org("build")
    ok = _tool(t, "build_screen", {"text": "close > SMA(close, 20) and RSI(14) > 50"})
    assert ok.ok and ok.data["ok"] and ok.data["stages"] == ["close > SMA(close, 20)", "RSI(14) > 50"] and ok.data["plan"]["lookback"]["1d"] >= 20
    bad = _tool(t, "build_screen", {"text": "close > NoSuchThing(3)"})
    assert bad.ok and not bad.data["ok"] and bad.data["problems"]
    gated = _tool(t, "build_screen", {"text": 'Holding("promoter") > 50'})            # its own flag (screener_ownership) is off
    assert not gated.data["ok"] and "screener_ownership" in json.dumps(gated.data["problems"])
    assert not _tool(t, "build_screen", {"text": "close > 1", "tenant_id": 1}).ok          # the model cannot pick the organisation


# --- run and explain -------------------------------------------------------------------------------------------------------------

def test_run_screen_live_is_a_stored_screener_run(monkeypatch):
    t = _org("run")
    _broker(monkeypatch)
    res = _tool(t, "run_screen", {"text": "close > SMA(close, 20)", "symbols": ["UP", "DOWN", "NOPE"]})
    assert res.ok, res.error
    assert res.data["matched"] == ["UP"] and "NOPE" in res.data["not_evaluated"] and res.data["stages"][0]["survivors"] == 1
    assert "recommendation" in res.data["disclaimer"] and res.source == "screener_run"

    async def runs():
        async with _session_factory() as session:
            return list(await session.scalars(select(ScreenRunRecord).where(ScreenRunRecord.tenant_id == t["tenant_id"])))
    assert [r.id for r in _run(runs())] == [res.data["run_id"]]
    off_screener = _tenant(f"n3s9-noscr-{uuid.uuid4().hex[:6]}@example.com", with_credentials=False)
    _flag(S.FLAG, off_screener["tenant_id"])
    assert "screener_v2" in _tool(off_screener, "run_screen", {"text": "close > 1", "symbols": ["UP"]}).error


def test_run_screen_as_of_reads_the_lake_on_that_date():
    t = _org("asof", extra=(H.FLAG,))
    stem = f"C{uuid.uuid4().hex[:4].upper()}"
    frames, days = _universe(n=4, bars=120, seed=5)
    renamed = {f"{stem}{i}": f for i, f in enumerate(frames.values())}
    for s, f in renamed.items():
        _seed(*_lake_rows(s, f))
    d = days[100]
    res = _tool(t, "run_screen", {"text": "close > SMA(close, 20)", "symbols": list(renamed), "as_of": str(d)})
    assert res.ok, res.error
    from app.screener import compile_screen
    ast, validated = compile_screen("close > SMA(close, 20)", base_tf="1d")
    assert res.data["matched"] == H.passers_on(ast, validated, renamed, d)["matched"] and res.as_of == str(d)
    assert "base_tf 1d" in _tool(t, "run_screen", {"text": "close > 1", "symbols": ["X"], "as_of": str(d), "base_tf": "15m"}).error


def test_explain_results_gives_the_engine_s_own_numbers(monkeypatch):
    t = _org("explain")
    _broker(monkeypatch)
    res = _tool(t, "explain_results", {"text": "close > SMA(close, 20) AND RSI(14) > 50", "symbols": ["UP", "DOWN"]})
    assert res.ok, res.error
    up, down = res.data["symbols"]["UP"], res.data["symbols"]["DOWN"]
    assert up["matched"] is True and down["matched"] is False
    assert [s["passed"] for s in down["stages"]] == [False, False] and len(up["conditions"]) == 2
    assert up["conditions"][0]["verify"].startswith("close 179.00 > SMA(close, 20)") and res.data_timestamps["UP"] == up["bar_close"]


# --- propose_alert -----------------------------------------------------------------------------------------------------------------

def _agent(t, provider, question):
    async def go():
        async with _session_factory() as session:
            user = await session.get(User, t["user_id"])
            return await agent.run_agent(provider, ToolContext(session, user.tenant_id, user, NOW), question)
    return _run(go())


ALERT = {"reason": "The trader asked to be told when this condition holds.", "name": "UP above its 20-day average", "symbol": "UP",
         "condition": "close > SMA(close, 20)"}


def test_an_asked_alert_is_a_proposal_for_the_asker_and_exists_only_after_approval():
    t = _org("alert")
    question = "Alert me when UP closes above its 20-day average"
    assert "propose_alert" in proposals.allowed_for(question)
    provider = _Scripted([("call", [("propose_alert", {**ALERT, "quote": "Alert me when UP closes above"})]),
                          ("text", json.dumps({"text": "Proposed the alert; approve it under AI Copilot.", "claims": [], "disclaimers": []}))])
    answer = _agent(t, provider, question)
    assert [p["action"] for p in answer.proposals] == ["CREATE_ALERT"]

    async def rows(model, *where):
        async with _session_factory() as session:
            return list(await session.scalars(select(model).where(*where)))
    [action] = _run(rows(AiActionRecord, AiActionRecord.tenant_id == t["tenant_id"]))
    assert action.status == "PROPOSED" and action.owner_user_id == t["user_id"] and action.rule.startswith("AGENT_ALERT:")
    assert json.loads(action.evidence_json)["rule"]["condition"] == "close > SMA(close, 20)"
    assert _run(rows(AlertRuleRecord, AlertRuleRecord.tenant_id == t["tenant_id"])) == []
    approved = client.post(f"/api/ai/actions/{action.id}/approve", headers=t["headers"], json={})
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "EXECUTED" and "Alert rule #" in approved.json()["result"]
    [rule] = _run(rows(AlertRuleRecord, AlertRuleRecord.tenant_id == t["tenant_id"]))
    assert rule.created_by == t["user_id"] and rule.kind == "instrument" and rule.symbol == "UP" and rule.status == "active"


def test_an_alert_nobody_asked_for_is_refused_by_the_guard():
    t = _org("noask")
    question = "What does UP look like today?"
    provider = _Scripted([("call", [("propose_alert", {**ALERT, "quote": "UP look like"})]),
                          ("text", json.dumps({"text": "Nothing proposed.", "claims": [], "disclaimers": []}))])
    answer = _agent(t, provider, question)
    assert answer.proposals == [] and "propose_alert" not in proposals.allowed_for(question)

    async def actions():
        async with _session_factory() as session:
            return list(await session.scalars(select(AiActionRecord).where(AiActionRecord.tenant_id == t["tenant_id"])))
    assert _run(actions()) == []
    direct = _tool(t, "propose_alert", {**ALERT, "quote": "alert me"})                # not through the loop's guard
    assert not direct.ok and "guard" in direct.error


def test_an_invalid_alert_is_refused_before_anything_is_proposed():
    t = _org("badalert")
    res = _tool(t, "propose_alert", {**ALERT, "quote": "alert me", "condition": "Rank(close) <= 3"}, guard_cleared=True)
    assert not res.ok and "cannot rank" in res.error


# --- the plain-language box --------------------------------------------------------------------------------------------------------

class _LLM:
    name, model = "fake", "fake-1"

    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    async def complete(self, system, user, *, max_tokens=2000):
        self.calls.append((system, user))
        return json.dumps(self.answers.pop(0))


def _provider(monkeypatch, fake):
    from app.ai import settings as ai_settings

    async def provider_for(session, tenant, **kw):
        return fake
    monkeypatch.setattr(ai_settings, "provider_for", provider_for)


def test_the_box_returns_a_validated_draft_after_one_repair(monkeypatch):
    t = _org("nl")
    fake = _LLM([{"text": "close > SMA(close, 50) AND RSI(14 > 55", "explanation": "x", "assumptions": []},
                 {"text": "close > SMA(close, 50) AND RSI(14) > 55", "explanation": "Stocks above their 50-day average with RSI over 55.",
                  "assumptions": ["50-day average as the trend", "RSI 14"]}])
    _provider(monkeypatch, fake)
    r = client.post("/api/screener/copilot/nl", headers=t["headers"], json={"text": "uptrend stocks with strong momentum"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ok"] and out["stages"] == ["close > SMA(close, 50)", "RSI(14) > 55"] and out["ast"] is not None
    assert out["explanation"].startswith("Stocks above") and out["assumptions"] == ["50-day average as the trend", "RSI 14"]
    assert out["prompt_version"] == nl.PROMPT_VERSION and "not recommendations" in out["disclaimer"]
    assert len(fake.calls) == 2 and "has problems" in fake.calls[1][1]
    assert "Holding(" not in fake.calls[0][0] and "SMA(x, n)" in fake.calls[0][0]           # a flagged-off family is not offered

    async def runs():
        async with _session_factory() as session:
            return list(await session.scalars(select(ScreenRunRecord).where(ScreenRunRecord.tenant_id == t["tenant_id"])))
    assert _run(runs()) == []                                                                # nothing ran


def test_the_box_withholds_advice_and_reports_what_stays_invalid(monkeypatch):
    t = _org("nl2")
    _provider(monkeypatch, _LLM([{"text": "close > Nope(1)", "explanation": "Guaranteed profit - buy these now.", "assumptions": []},
                                 {"text": "close > Nope(2)", "explanation": "Guaranteed profit - buy these now.", "assumptions": []}]))
    out = client.post("/api/screener/copilot/nl", headers=t["headers"], json={"text": "the best stocks to buy"}).json()
    assert out["ok"] is False and out["problems"] and out["ast"] is None
    assert out["explanation"] is None and out["explanation_withheld"] is True


def test_the_box_needs_its_flag_and_a_provider(monkeypatch):
    off = _org("nloff", on=False)
    r = client.post("/api/screener/copilot/nl", headers=off["headers"], json={"text": "uptrend stocks please"})
    assert r.status_code == 503 and r.headers.get("X-Feature-Disabled") == S.FLAG
    t = _org("nlrule")
    r = client.post("/api/screener/copilot/nl", headers=t["headers"], json={"text": "uptrend stocks please"})
    assert r.status_code == 409 and "No AI provider" in r.text
    assert client.post("/api/screener/copilot/nl", json={"text": "uptrend stocks please"}).status_code == 401


def test_the_registry_text_lists_every_entry_but_flagged_off_ones():
    full = nl.registry_text()
    assert "- close: num (price)" in full and "RankScore(system)" in full and "Holding(holder='promoter')" in full
    assert "Holding(" not in nl.registry_text(["screener_ownership"])
    assert "{registry}" not in nl.system_prompt("en") and "Answer in English" in nl.system_prompt("en")
    with pytest.raises(ValueError):
        nl.parse("no json here")
    assert nl.parse('```json\n{"text": " close > 1 ", "assumptions": "x"}\n```') == {"text": "close > 1", "explanation": "", "assumptions": []}
