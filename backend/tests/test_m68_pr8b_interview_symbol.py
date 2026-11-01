"""M68 PR-8b (owner order M68): the strategy interview pre-chooses no trading value. Symbol, instrument, vehicle and
style are his own answers: not answered = None ("Not set"); a plan may preview with "Not set", a deploy asks for each
one by name (422 "Needs your setting: <field>"), and a non-swing bot takes the strategy's own base candles (_own_base),
never a hard-coded "1min".
"""
import asyncio
import json

import pytest

from app.ai import interview as iv
from app.db.models import AiCandidateRecord
from app.strategy_engine.registry import registry
from tests.server_evidence import serve_candles
from tests.test_auth_api import _session_factory, client
from tests.test_phase_ap_interview import _sessions
from tests.test_trading_worker import _tenant

ALL = {"language": "en", "experience": "learning", "capital": 300000, "risk": "moderate", "goal": "big_trends",
       "symbol": "RELIANCE", "instrument": "fno_stock", "vehicle": "underlying", "style": "intraday"}


def _serve(monkeypatch) -> None:
    df = _sessions(days=5)
    serve_candles(monkeypatch, [{"timestamp": ts.isoformat(), "open": r.open, "high": r.high, "low": r.low, "close": r.close,
                                 "volume": r.volume} for ts, r in df.iterrows()])


def _plan(headers, answers: dict, **extra):
    return client.post("/api/ai/interview/plan", headers=headers, json={"answers": answers, "base_timeframe": "5min", **extra})


def _with_evidence(cid: int) -> dict:
    """The server-side backtest evidence a longer run gives (five synthetic sessions give the templates no trades)."""
    async def go():
        async with _session_factory() as session:
            row = await session.get(AiCandidateRecord, cid)
            metrics = json.loads(row.metrics_json or "{}")
            metrics["evidence"] = {"tested": True, "total_trades": 18, "win_rate": 0.5}
            row.metrics_json = json.dumps(metrics)
            await session.commit()
            return json.loads(row.deployment_json or "{}")
    return asyncio.run(go())


def _deploy(headers, cid: int):
    return client.post("/api/ai/interview/deploy", headers=headers, json={"candidate_id": cid, "accept_risk": True, "accept_suggested": True})


# --- the answers ---------------------------------------------------------------------------------------------------------

def test_symbol_instrument_vehicle_and_style_have_no_default():
    a = iv.InterviewAnswers()
    assert (a.symbol, a.instrument, a.vehicle, a.style) == (None, None, None, None)
    assert iv.unanswered(a) == ["symbol", "instrument", "vehicle", "style"]
    assert iv.InterviewAnswers(symbol="  ", style="").symbol is None                  # a blank answer is Not set
    assert iv.unanswered(iv.InterviewAnswers(**ALL)) == []
    shown = {q.id: q.default for q in iv.QUESTIONS if q.id in iv.REQUIRED_ANSWERS}
    assert shown == {"symbol": None, "instrument": None, "vehicle": None, "style": None}   # nothing pre-selected in the UI


def test_a_plan_previews_what_is_not_set_and_its_bot_carries_none_of_it():
    a = iv.InterviewAnswers(language="en", experience="learning")
    plan = iv.build_plan(a, _sessions(days=6), "5min", read_symbol="NIFTY 50")       # the candles read: NIFTY 50
    assert plan["unanswered"] == ["symbol", "instrument", "vehicle", "style"]
    contract = next(s for s in plan["sections"] if s["id"] == "contract")["lines"]
    assert contract[0].startswith("the symbol (Not set - choose a symbol): how to take the trade is Not set")
    assert "Not set - answer before a deploy: symbol, instrument, vehicle, style." in contract
    dep = plan["deployment"]
    assert dep["symbol"] is None and dep["holding"] is None and "timeframe" not in dep and "instrument_kind" not in dep
    assert "Style: Not set. Instrument: Not set via Not set." in plan["ai_prompt"]
    with pytest.raises(ValueError, match="Needs your setting: symbol"):
        iv.build_plan(a, _sessions(days=6), "5min")                                  # no symbol at all: nothing to read


def test_the_plan_sends_no_base_candles_except_a_swings_daily_ones():
    a = iv.InterviewAnswers(**ALL)
    dep = iv.build_plan(a, _sessions(days=6), "5min")["deployment"]
    assert "timeframe" not in dep and dep["holding"] == "INTRADAY" and dep["symbol"] == "RELIANCE"
    market = iv.analyse_market(_sessions(days=6), "5min", "en")
    pick = {"strategy_id": "swing_ema_pullback_d", "name": "x", "family": "trend", "timeframes": ["day"], "description": "",
            "regime_fit": 3.0, "score": 1.0, "evidence": {"tested": False, "total_trades": 0, "net_pnl": 0.0, "text": ""}}
    swing = iv.compose_plan(iv.InterviewAnswers(**{**ALL, "style": "swing"}), market, [pick], pick, iv.risk_plan(a)[0], [], "sample")
    assert swing["deployment"]["timeframe"] == "day" and swing["deployment"]["holding"] == "SWING"


# --- the routes ----------------------------------------------------------------------------------------------------------

def test_a_plan_with_no_symbol_asks_for_it(monkeypatch):
    _serve(monkeypatch)
    headers = _tenant("m68-pr8b-nosymbol@example.com")["headers"]
    r = _plan(headers, {k: v for k, v in ALL.items() if k != "symbol"})
    assert r.status_code == 422 and r.json()["detail"] == "Needs your setting: symbol"


@pytest.mark.parametrize("field", ["symbol", "instrument", "vehicle", "style"])
def test_the_deploy_asks_for_each_unanswered_setting(monkeypatch, field):
    _serve(monkeypatch)
    headers = _tenant(f"m68-pr8b-missing-{field}@example.com")["headers"]
    answers = {k: v for k, v in ALL.items() if k != field}
    plan = _plan(headers, answers, **({"symbol": "RELIANCE"} if field == "symbol" else {}))    # the candles read, not his answer
    assert plan.status_code == 200, plan.text
    body = plan.json()
    assert body["unanswered"] == [field] and body["candidate_id"]
    _with_evidence(body["candidate_id"])
    refused = _deploy(headers, body["candidate_id"])
    assert refused.status_code == 422 and refused.json()["detail"] == f"Needs your setting: {field}"


def test_all_answered_deploys_on_the_strategys_own_base_candles(monkeypatch):
    _serve(monkeypatch)
    headers = _tenant("m68-pr8b-happy@example.com")["headers"]
    plan = _plan(headers, ALL)
    assert plan.status_code == 200, plan.text
    body = plan.json()
    assert body["unanswered"] == []
    payload = _with_evidence(body["candidate_id"])
    assert "timeframe" not in payload and payload["instrument_kind"] == "UNDERLYING"
    deployed = _deploy(headers, body["candidate_id"])
    assert deployed.status_code == 201, deployed.text
    dep = deployed.json()["deployment"]
    frames = registry.get(payload["strategy_id"]).timeframes
    own = min(frames, key=lambda tf: iv.timeframe_minutes(tf) or 10**9)
    assert (dep["symbol"], dep["mode"], dep["holding"], dep["timeframe"]) == ("RELIANCE", "PAPER", "INTRADAY", own)
