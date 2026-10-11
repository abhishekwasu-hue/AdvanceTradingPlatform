"""H-C1 a: approval evidence comes from server data. Client-posted candles are recorded as `sample` whatever the request
claims, and sample evidence can never approve an AI draft or deploy an interview option."""
import asyncio
import json

import pytest
from fastapi import HTTPException

from app.ai import evidence, generator
from app.core import config
from app.db.models import AiCandidateRecord, Tenant, User
from tests.server_evidence import serve_candles
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import VALID_ANSWER, _ScriptedProvider, _candles, _owner
from tests.utils import noisy_uptrend


def _run(coro):
    return asyncio.run(coro)


def _draft(me) -> int:
    async def gen():
        async with _session_factory() as session:
            tenant = await session.get(Tenant, me["tenant_id"])
            user = await session.get(User, me["id"])
            return (await generator.generate(session, tenant, user, "Buy pullbacks in an uptrend on 5 minute bars", provider=_ScriptedProvider([VALID_ANSWER]))).id
    return _run(gen())


def test_server_only_is_on_by_default_and_the_helpers_trust_only_server_sources():
    import os
    assert "AI_EVIDENCE_SERVER_ONLY" not in os.environ and config.AI_EVIDENCE_SERVER_ONLY is True     # the default, unset
    assert evidence.is_server("broker:upstox") and evidence.is_server("lake") and not evidence.is_server("sample")
    assert not evidence.is_server(None) and not evidence.is_server("uploaded") and not evidence.is_server("brokerish")
    # A client cannot label its own candles as broker data.
    assert evidence.client_source("broker:zerodha") == "sample"
    with pytest.raises(HTTPException) as err:
        evidence.require_server("sample", "Approving an AI draft")
    assert err.value.status_code == 400 and "server data" in err.value.detail
    evidence.require_server("broker:upstox", "Approving an AI draft")            # no raise


def test_client_candles_are_recorded_as_sample_and_cannot_approve():
    headers, me = _owner("hc1a-client@example.com")
    draft_id = _draft(me)
    candles = _candles(noisy_uptrend(400))
    bt = client.post(f"/api/ai/drafts/{draft_id}/backtest", headers=headers,
                     json={"symbol": "TEST", "base_timeframe": "1min", "candles": candles, "data_source": "broker:zerodha"})
    assert bt.status_code == 200, bt.text
    assert bt.json()["run"]["data_source"] == "sample"                        # the claimed label is not trusted
    refused = client.post(f"/api/ai/drafts/{draft_id}/approve", headers=headers, json={"accept_risk": True})
    assert refused.status_code == 400 and "server data" in refused.json()["detail"]
    assert client.get(f"/api/ai/drafts/{draft_id}", headers=headers).json()["status"] != "APPROVED"


def test_server_fetched_candles_can_approve(monkeypatch):
    headers, me = _owner("hc1a-server@example.com")
    draft_id = _draft(me)
    calls = serve_candles(monkeypatch, _candles(noisy_uptrend(400)), source="broker:upstox")
    bt = client.post(f"/api/ai/drafts/{draft_id}/backtest", headers=headers, json={"symbol": "test", "base_timeframe": "1min"})
    assert bt.status_code == 200, bt.text
    run = bt.json()["run"]
    assert run["data_source"] == "broker:upstox" and run["bars"] == 400 and run["data_from"] and run["data_to"]
    assert calls and calls[0]["tenant_id"] == me["tenant_id"] and calls[0]["timeframe"] == "1min"
    ok = client.post(f"/api/ai/drafts/{draft_id}/approve", headers=headers, json={"accept_risk": True})
    assert ok.status_code == 200, ok.text
    assert ok.json()["draft"]["status"] == "APPROVED"


def test_no_server_data_is_409_never_a_fallback_to_client_data():
    headers, me = _owner("hc1a-nobroker@example.com")
    draft_id = _draft(me)
    # The tenant has no broker session: the server cannot fetch, and nothing silently uses other data.
    r = client.post(f"/api/ai/drafts/{draft_id}/backtest", headers=headers, json={"symbol": "TEST", "base_timeframe": "1min"})
    assert r.status_code == 409, r.text


def test_interview_option_from_sample_candles_cannot_deploy():
    from tests.test_phase_ap_interview import _sessions
    headers, _me = _owner("hc1a-interview@example.com")
    df = _sessions(days=5)
    candles = [{"timestamp": ts.isoformat(), "open": r.open, "high": r.high, "low": r.low, "close": r.close, "volume": r.volume} for ts, r in df.iterrows()]
    plan = client.post("/api/ai/interview/plan", headers=headers, json={
        "answers": {"language": "en", "experience": "learning", "capital": 300000, "risk": "moderate", "style": "intraday", "vehicle": "option_buy", "goal": "big_trends"},
        "base_timeframe": "5min", "candles": candles, "data_source": "broker:zerodha"})
    assert plan.status_code == 200, plan.text
    cid = plan.json()["candidate_id"]
    assert cid

    # Even with evidence that would otherwise pass, a sample-data candidate is refused.
    async def with_evidence():
        async with _session_factory() as session:
            row = await session.get(AiCandidateRecord, cid)
            metrics = json.loads(row.metrics_json or "{}")
            assert metrics["data_source"] == "sample"
            metrics["evidence"] = {"tested": True, "total_trades": 18, "win_rate": 0.5}
            row.metrics_json = json.dumps(metrics)
            await session.commit()
    _run(with_evidence())
    refused = client.post("/api/ai/interview/deploy", headers=headers, json={"candidate_id": cid, "accept_risk": True})
    assert refused.status_code == 400 and "server data" in refused.json()["detail"]
