"""Phase AU: global cues from free public sources - parsing, fallback, cache, the India read, memory, guide, worker."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.ai import global_cues as gc
from app.ai import knowledge as k
from app.ai import market_memory as mm
from app.db.models import TraderProfileRecord
from tests.test_auth_api import _register, _session_factory, client

UTC = timezone.utc
NOW = datetime(2026, 9, 25, 3, 0, tzinfo=UTC)   # a Friday, 08:30 IST


def _yahoo(closes, price=None, when=NOW):
    stamps = [int((when - timedelta(days=len(closes) - 1 - i)).timestamp()) for i in range(len(closes))]
    return {"chart": {"result": [{"meta": {"regularMarketPrice": price if price is not None else closes[-1], "regularMarketTime": int(when.timestamp())},
                                  "timestamp": stamps, "indicators": {"quote": [{"close": closes}]}}], "error": None}}


STOOQ_CSV = "Date,Open,High,Low,Close,Volume\n2026-09-22,1,1,1,83.0,0\n2026-09-23,1,1,1,83.2,0\n2026-09-24,1,1,1,83.6,0\n"


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("GLOBAL_CUES_ENABLED", "true")
    gc.reset_cache()
    yield
    gc.reset_cache()


def test_parsers():
    q = gc.parse_yahoo(gc.BY_KEY["SP500_FUT"], _yahoo([5000.0, None, 5050.0, 5100.0], price=5049.0))
    assert q["prev_close"] == 5050.0 and q["change_pct"] == round((5049 / 5050 - 1) * 100, 2) and q["change_5d_pct"] == round((5049 / 5000 - 1) * 100, 2)
    assert q["source"] == "yahoo" and q["as_of"].startswith("2026-09-25")
    assert gc.parse_yahoo(gc.BY_KEY["SP500"], {"chart": {"result": None, "error": {"code": "Not Found"}}}) is None
    assert gc.parse_yahoo(gc.BY_KEY["SP500"], _yahoo([5000.0])) is None
    s = gc.parse_stooq(gc.BY_KEY["USDINR"], STOOQ_CSV)
    assert s["last"] == 83.6 and s["prev_close"] == 83.2 and s["source"] == "stooq"
    assert gc.parse_stooq(gc.BY_KEY["USDINR"], "No data") is None


def test_fetch_falls_back_to_stooq_caches_and_reports_misses(on):
    hits = []

    def handler(request: httpx.Request):
        hits.append(str(request.url))
        url = str(request.url)
        if "stooq" in url:
            return httpx.Response(200, text=STOOQ_CSV) if "usdinr" in url else httpx.Response(200, text="No data")
        if "INR%3DX" in url or "INR=X" in url or "%5ETNX" in url or "^TNX" in url:
            return httpx.Response(404, json={"chart": {"result": None}})
        return httpx.Response(200, json=_yahoo([100.0, 101.0]))

    async def go(**kw):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await gc.fetch_all(c, **kw)
    quotes, errors = asyncio.run(go())
    by = {q["key"]: q for q in quotes}
    assert by["USDINR"]["source"] == "stooq" and by["SP500_FUT"]["source"] == "yahoo"
    assert "US10Y" not in by and len(errors) == 1 and errors[0].startswith("US10Y: yahoo: HTTP 404")
    count = len(hits)
    asyncio.run(go())
    assert len(hits) == count                     # served from the shared cache
    asyncio.run(go(force=True))
    assert len(hits) > count


def test_disabled_fetches_nothing(monkeypatch):
    monkeypatch.setenv("GLOBAL_CUES_ENABLED", "false")
    assert asyncio.run(gc.fetch_all()) == ([], [])


def _g(key, change, as_of=NOW):
    return {"symbol": key, "change_pct": change, "last_price": 1.0, "payload": {"as_of": as_of.isoformat()}}


def test_the_india_read_of_the_world():
    risk_off = [_g("SP500_FUT", -1.2), _g("SP500", 0.4), _g("NIKKEI", -0.9), _g("BRENT", 2.6), _g("USDINR", 0.45), _g("GOLD", 1.5)]
    m = gc.mood(risk_off, NOW)
    assert m["label"] == "NEGATIVE" and "SP500" not in m["drivers"] and "GOLD" not in m["drivers"]
    text = " ".join(gc.view("mr", risk_off, NOW))
    assert "जागतिक संकेत: नकारात्मक" in text and "US futures खाली" in text and "Brent कच्चे तेल +2.60%" in text and "रुपया कमजोर" in text
    assert "buy" not in " ".join(gc.view("en", risk_off, NOW)).lower()
    risk_on = [_g("SP500_FUT", 0.8), _g("NASDAQ_FUT", 1.0), _g("HANG_SENG", 1.2), _g("BRENT", -2.0)]
    assert gc.mood(risk_on, NOW)["label"] == "POSITIVE" and "cheaper oil" in " ".join(gc.view("en", risk_on, NOW))
    stale = [_g("SP500_FUT", -3.0, as_of=NOW - timedelta(days=10))]
    assert gc.view("en", stale, NOW) == [] and gc.mood(stale, NOW)["label"] == "MIXED"


def _tenant(email):
    headers = {"Authorization": f"Bearer {_register(email)}"}
    me = client.get("/api/auth/me", headers=headers).json()
    return headers, me["tenant_id"], me["id"]


def _serve(monkeypatch, quotes):
    async def fetch_all(client=None, *, force=False):
        return quotes, ["US10Y: yahoo: HTTP 404"]
    monkeypatch.setattr(gc, "fetch_all", fetch_all)


FRESH = datetime.now(UTC).isoformat()   # the guide and the API judge freshness against the real clock
QUOTES = [{"key": "SP500_FUT", "label": "S&P 500 futures", "group": "US", "last": 5000.0, "prev_close": 5060.0, "change_pct": -1.19,
           "change_5d_pct": -2.0, "as_of": FRESH, "source": "yahoo"},
          {"key": "BRENT", "label": "Brent crude", "group": "COMMODITY", "last": 82.0, "prev_close": 80.0, "change_pct": 2.5,
           "change_5d_pct": 4.0, "as_of": FRESH, "source": "yahoo"}]


def test_memory_plan_and_guide_use_the_global_cues(monkeypatch):
    _serve(monkeypatch, QUOTES)
    _, tenant_id, _ = _tenant("global-memory@example.com")

    async def go():
        async with _session_factory() as session:
            report = await mm.capture_global(session, tenant_id, now=NOW)
            return report, await mm.latest(session, tenant_id, now=NOW + timedelta(minutes=5))
    report, memory = asyncio.run(go())
    assert report["globals"] == 2 and report["errors"] == ["global US10Y: yahoo: HTTP 404"]
    assert [g["symbol"] for g in memory["globals"]] == ["SP500_FUT", "BRENT"] and memory["globals"][0]["payload"]["source"] == "yahoo"
    lines = mm.describe("mr", memory, "NIFTY 50", now=NOW + timedelta(minutes=5))
    assert lines[0].startswith("जागतिक संकेत") and "मोफत सार्वजनिक माहिती" in lines[-1]

    ans = k.answer("आज crude चा भारतावर काय परिणाम?", "mr", memory)
    assert ans["used_market_memory"] and "Brent कच्चे तेल" in ans["answer"] and ans["concepts"][0]["id"] == "global_cues"
    assert "signal नाही" in ans["answer"]
    assert not k.answer("RSI म्हणजे काय?", "mr", memory)["used_market_memory"]
    assert "global BRENT: 82.0" in k.ai_context("en", [], memory, None)


def test_api_shows_the_global_view_and_refresh_works_without_a_broker(monkeypatch):
    _serve(monkeypatch, QUOTES)
    headers, _, _ = _tenant("global-api@example.com")
    r = client.post("/api/ai/market-memory/refresh", headers=headers, json={"language": "en"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["report"]["globals"] == 2 and body["report"]["errors"][0].startswith("broker:")
    assert body["global_view"] and "GIFT Nifty" in body["global_gift_note"]
    got = client.get("/api/ai/market-memory?language=mr", headers=headers).json()
    assert len(got["globals"]) == 2 and got["global_view"][0].startswith("जागतिक संकेत") and "मोफत" in got["global_source"]


def test_worker_reads_the_world_before_the_open_without_a_broker(monkeypatch):
    from app.workers import trading_worker as tw
    _serve(monkeypatch, QUOTES)
    _, tenant_id, user_id = _tenant("global-worker@example.com")

    async def seed():
        async with _session_factory() as session:
            session.add(TraderProfileRecord(tenant_id=tenant_id, user_id=user_id, answers_json="{}", preferences_json="{}"))
            await session.commit()
    asyncio.run(seed())
    worker = tw.TradingWorker(_session_factory, cycle_seconds=60)
    worker._last_memory = {t: NOW for t in range(1, tenant_id)}   # only this tenant is due

    async def go():
        async with _session_factory() as session:
            return await worker._market_memory(session, NOW, broker_reads=False)
    assert asyncio.run(go()) == 2
    assert tw._pre_open(NOW) and not tw._pre_open(NOW + timedelta(hours=2)) and not tw._pre_open(datetime(2026, 9, 26, 3, 0, tzinfo=UTC))
    stored = asyncio.run(_count(tenant_id))
    assert stored == 2


async def _count(tenant_id):
    from sqlalchemy import func, select
    from app.db.models import MarketSnapshotRecord
    async with _session_factory() as session:
        return await session.scalar(select(func.count()).select_from(MarketSnapshotRecord).where(
            MarketSnapshotRecord.tenant_id == tenant_id, MarketSnapshotRecord.kind == "GLOBAL"))


def test_snapshot_rows_round_trip():
    rows = gc.snapshot_rows(7, QUOTES, NOW)
    assert rows[0].kind == "GLOBAL" and rows[0].exchange == "GLOBAL" and json.loads(rows[1].payload_json)["key"] == "BRENT"
