"""Phase BC: the deterministic market sentiment score - component scorers, weighting with missing inputs,
the separate news score, capture through a fake broker into the market memory, the API and the brief."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.ai import market_memory as mm
from app.ai import sentiment as se
from app.brokers.models import OptionChain, OptionChainRow, Quote
from app.db.models import MarketSnapshotRecord, TraderProfileRecord
from tests.test_auth_api import _register, _session_factory, client

UTC = timezone.utc
NOW = datetime(2026, 10, 6, 5, 0, tzinfo=UTC)


def _run(coro):
    return asyncio.run(coro)


def _tenant(email):
    headers = {"Authorization": f"Bearer {_register(email)}"}
    me = client.get("/api/auth/me", headers=headers).json()
    return headers, me["tenant_id"], me["id"]


class _Broker:
    name = "upstox"

    def __init__(self, pcr_rows=True, quotes=True):
        self.pcr_rows, self.quotes, self.calls = pcr_rows, quotes, []

    async def get_option_chain(self, underlying, expiry=None):
        self.calls.append(("chain", underlying))
        if not self.pcr_rows:
            raise RuntimeError("chain unavailable")
        rows = [OptionChainRow(strike=s, call_oi=1000.0, call_change_oi=-100.0, put_oi=1300.0, put_change_oi=300.0) for s in (24900, 25000, 25100)]
        return OptionChain(underlying="NIFTY", expiry="2026-10-08", underlying_ltp=25010.0, rows=rows)

    async def get_quote(self, symbols):
        self.calls.append(("quotes", len(symbols)))
        if not self.quotes:
            raise RuntimeError("quotes unavailable")
        out = {}
        for i, s in enumerate(symbols):
            out[s] = Quote(symbol=s, ltp=101.0 if i % 4 else 99.0, close=100.0)       # 3 of 4 advance
        return out


def test_component_scorers_are_bounded_and_monotonic():
    assert se.pcr_score(1.0) == 0.0 and se.pcr_score(1.5) == 100.0 and se.pcr_score(0.5) == -100.0 and se.pcr_score(None) is None
    assert se.pcr_score(1.1, call_oi_change=-100, put_oi_change=300) > se.pcr_score(1.1) > se.pcr_score(1.1, call_oi_change=300, put_oi_change=-100)
    assert se.vix_score(16.0) == 0.0 and se.vix_score(12.0) == 50.0 and se.vix_score(20.0) == -50.0 and se.vix_score(30.0) == -100.0
    assert se.vix_score(16.0, change_pct=10.0) == -30.0 and se.vix_score(None) is None
    assert se.breadth_score(3, 1) == 50.0 and se.breadth_score(0, 0) is None and se.breadth_score(0, 5) == -100.0
    assert se.global_score({"score": 1.5}) == 100.0 and se.global_score({"score": -0.75}) == -50.0 and se.global_score(None) is None
    assert 75 < se.fii_dii_score(2000) < 77 and se.fii_dii_score(-2000) == -se.fii_dii_score(2000) and se.fii_dii_score(None) is None
    assert se.label_for(25) == "RISK_ON" and se.label_for(-25) == "RISK_OFF" and se.label_for(0) == "NEUTRAL"


def test_compute_renormalises_over_present_components_and_weights_come_from_config(monkeypatch):
    full = se.compute({"pcr": 40, "vix": 40, "breadth": 40, "global": 40, "fii_dii": 40})
    assert full["score"] == 40.0 and full["coverage"] == 1.0 and full["missing"] == [] and full["label"] == "RISK_ON"
    partial = se.compute({"pcr": 60, "vix": None, "breadth": 0, "global": None, "fii_dii": None})
    assert partial["missing"] == ["vix", "global", "fii_dii"] and partial["coverage"] == 0.45
    assert partial["score"] == round((0.25 * 60 + 0.20 * 0) / 0.45, 1) and partial["components"]["pcr"]["weight"] == round(0.25 / 0.45, 3)
    assert partial["components"]["vix"]["weight"] == 0.0 and partial["components"]["vix"]["score"] is None
    empty = se.compute({k: None for k in se.COMPONENTS})
    assert empty["label"] == "UNKNOWN" and empty["score"] == 0.0 and empty["coverage"] == 0.0
    monkeypatch.setenv("SENTIMENT_WEIGHTS", json.dumps({"vix": 0.6, "bogus": 9, "pcr": -1}))
    w = se.weights()
    assert w["vix"] == 0.6 and w["pcr"] == 0.25 and "bogus" not in w                      # unknown keys and negatives ignored
    monkeypatch.setenv("SENTIMENT_WEIGHTS", "not json")
    assert se.weights() == se.DEFAULT_WEIGHTS


def test_news_score_is_separate_and_weighted_by_severity_and_confidence():
    items = [{"classification": {"direction": "BULLISH", "severity": 4, "confidence": 0.9}},
             {"classification": {"direction": "BEARISH", "severity": 2, "confidence": 0.5}},
             {"classification": {"direction": "NEUTRAL", "severity": 3, "confidence": 0.6}}]
    n = se.news_score(items)
    assert n["items"] == 3 and n["score"] == round((3.6 - 1.0) / (3.6 + 1.0 + 1.8) * 100, 1) and n["label"] == "RISK_ON"
    assert se.news_score([]) is None and se.news_score([{"classification": {}}]) is None


def test_capture_reads_the_broker_and_memory_and_stores_a_snapshot_the_memory_returns():
    _, tenant_id, _ = _tenant("bc-capture@example.com")
    memory = {"cues": [{"symbol": "INDIA VIX", "last_price": 12.0, "change_pct": -5.0}],
              "globals": [{"symbol": "SP500_FUT", "change_pct": 1.2, "payload": {"as_of": NOW.isoformat()}}]}
    broker = _Broker()

    async def go():
        async with _session_factory() as session:
            result = await se.capture(session, tenant_id, broker, memory, now=NOW, news_items=[{"classification": {"direction": "BEARISH", "severity": 4, "confidence": 0.8}}])
            latest = await mm.latest(session, tenant_id, now=NOW + timedelta(minutes=1))
            rows = list(await session.scalars(select(MarketSnapshotRecord).where(MarketSnapshotRecord.tenant_id == tenant_id, MarketSnapshotRecord.kind == "SENTIMENT")))
            return result, latest, rows
    result, latest, rows = _run(go())
    assert broker.calls[0] == ("chain", "NIFTY") and broker.calls[1][0] == "quotes" and broker.calls[1][1] == len(se.BREADTH_SYMBOLS)
    assert result["components"]["pcr"]["score"] > 50 and result["components"]["vix"]["score"] > 50 and result["components"]["breadth"]["score"] == 50.0
    assert result["components"]["global"]["score"] > 0 and result["components"]["fii_dii"]["score"] is None and result["missing"] == ["fii_dii"]
    assert result["label"] == "RISK_ON" and result["news"]["label"] == "RISK_OFF" and result["components"]["fii_dii"]["input"]["status"] == "off"
    assert len(rows) == 1 and rows[0].last_price == result["score"] and rows[0].bias == "RISK_ON" and rows[0].symbol == "MARKET"
    assert latest["sentiment"]["score"] == result["score"] and [s["symbol"] for s in latest["symbols"]] == []      # not mistaken for a watched symbol
    lines = se.view("mr", latest["sentiment"])
    assert lines[0].startswith("Market sentiment +") and "तेजीचा कल" in lines[0] and any("FII/DII" in line for line in lines) and any("News score" in line for line in lines)
    assert "never a signal" in se.view("en", latest["sentiment"])[-1]


def test_capture_survives_a_dead_chain_and_quotes_with_lower_coverage():
    _, tenant_id, _ = _tenant("bc-degraded@example.com")
    memory = {"cues": [{"symbol": "INDIA VIX", "last_price": 22.0, "change_pct": 8.0}], "globals": []}

    async def go():
        async with _session_factory() as session:
            return await se.capture(session, tenant_id, _Broker(pcr_rows=False, quotes=False), memory, now=NOW)
    result = _run(go())
    assert result["missing"] == ["pcr", "breadth", "global", "fii_dii"] and result["coverage"] == 0.2 and result["label"] == "RISK_OFF"
    assert result["components"]["vix"]["weight"] == 1.0 and result["news"] is None
    assert se.view("en", None)[0].startswith("Market sentiment: not read yet")


def test_market_memory_api_and_brief_carry_the_sentiment(monkeypatch):
    headers, tenant_id, user_id = _tenant("bc-api@example.com")
    payload = se.compute({"pcr": -60, "vix": -40, "breadth": -20, "global": None, "fii_dii": None})
    payload.update({"news": None, "as_of": NOW.isoformat(), "source": "upstox"})

    async def seed():
        async with _session_factory() as session:
            session.add(TraderProfileRecord(tenant_id=tenant_id, user_id=user_id, answers_json="{}", preferences_json="{}"))
            session.add(MarketSnapshotRecord(tenant_id=tenant_id, kind="SENTIMENT", symbol="MARKET", exchange="NSE", timeframe="day", source="upstox",
                                             last_price=payload["score"], bias=payload["label"], payload_json=json.dumps(payload), captured_at=datetime.now(UTC)))
            await session.commit()
    _run(seed())
    memory = client.get("/api/ai/market-memory?language=mr", headers=headers).json()
    assert memory["sentiment"]["label"] == "RISK_OFF" and memory["sentiment_view"][0].startswith("Market sentiment -") and "सावधगिरीचा" in memory["sentiment_view"][0]
    brief = client.get("/api/ai/brief?language=en", headers=headers).json()
    assert brief["sentiment"]["score"] == payload["score"] and brief["sentiment_view"][0].startswith("Market sentiment -") and "risk-off" in brief["sentiment_view"][0]
    other_headers, _, _ = _tenant("bc-api-other@example.com")
    assert client.get("/api/ai/market-memory", headers=other_headers).json()["sentiment"] is None          # tenant-scoped
