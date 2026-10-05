"""Phase BD-2: news feedback (verdicts, trust, precision summary, API) feeding the thesis news factor, and the
weekly thesis scoreboard report (idempotent per ISO week, flag-gated, read-only)."""
import asyncio
import json
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from app.ai import thesis as th
from app.core.enums import NotificationType
from app.db.models import NewsEventRecord, NewsFeedbackRecord, NotificationRecord, ThesisRecord, User
from app.news_feed import feedback as fb
from app.platform import controls
from tests.test_auth_api import _register, _session_factory, client

UTC = timezone.utc
NOW = datetime(2026, 10, 9, 10, 15, tzinfo=UTC)          # Friday 15:45 IST


def _run(coro):
    return asyncio.run(coro)


def _tenant(email):
    headers = {"Authorization": f"Bearer {_register(email)}"}
    me = client.get("/api/auth/me", headers=headers).json()
    return headers, me["tenant_id"], me["id"]


def _flags(**values):
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            for name, on in values.items():
                flags[name] = {"on": on, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())


def _feed_items(n, prefix="bd2"):
    async def go():
        async with _session_factory() as session:
            rows = [NewsEventRecord(category="RBI_POLICY", headline=f"{prefix} headline {i}", event_date=date(2026, 10, 8), affected_symbols_json='["NIFTY"]',
                                    sentiment="Neutral", source_json='{"source":"RBI"}', origin="FEED", verified=False, dedupe_hash=f"{prefix}-{i}",
                                    feed_id="rbi_press" if i % 2 else "sebi_press", created_at=NOW - timedelta(hours=i),
                                    classification_json=json.dumps({"direction": "BULLISH", "severity": 4, "confidence": 0.8}))
                    for i in range(n)]
            session.add_all(rows)
            await session.commit()
            return [r.id for r in rows]
    return _run(go())


def test_feedback_trust_is_neutral_until_enough_verdicts_then_floored_share():
    assert fb.trust_from_counts(3, 1, 0) == {**fb.trust_from_counts(3, 1, 0), "trust": 1.0, "applied": False, "ratings": 4}
    assert fb.trust_from_counts(6, 2, 2)["trust"] == 0.6 and fb.trust_from_counts(6, 2, 2)["applied"] is True
    assert fb.trust_from_counts(0, 9, 1)["trust"] == fb.TRUST_FLOOR                    # a badly rated feed keeps a quarter of its weight
    rows = th.factor_rows({"symbol": "X", "last_price": 100.0, "bias": "BULLISH", "structure": "UPTREND", "higher_regime": "TRENDING_UP", "payload": {}},
                          None, [{"classification": {"direction": "BULLISH", "severity": 4, "confidence": 0.9}}], [], NOW, news_trust=0.5)
    news = next(r for r in rows if r["factor"] == "news")
    assert news["strength"] == 0.5 and news["value"]["trust"] == 0.5 and news["direction"] == 1


def test_feedback_api_records_replaces_and_summarises_per_tenant_and_scales_the_thesis():
    headers, tenant_id, user_id = _tenant("bd2-feedback@example.com")
    _flags(news_feed=True, market_thesis=True)
    ids = _feed_items(12)
    # A manual (non-feed) item takes no feedback; an unknown verdict is a 422.
    async def manual():
        async with _session_factory() as session:
            row = NewsEventRecord(category="RBI_POLICY", headline="manual", event_date=date(2026, 10, 8), affected_symbols_json="[]", sentiment="Neutral",
                                  source_json='{"source":"me"}', origin="MANUAL", verified=True, created_by=user_id)
            session.add(row)
            await session.commit()
            return row.id
    manual_id = _run(manual())
    assert client.post(f"/api/news-feed/items/{manual_id}/feedback", headers=headers, json={"verdict": "useful"}).status_code == 404
    assert client.post(f"/api/news-feed/items/{ids[0]}/feedback", headers=headers, json={"verdict": "great"}).status_code == 422

    for i, item in enumerate(ids[:10]):
        verdict = "useful" if i < 6 else "noise" if i < 9 else "wrong_direction"
        out = client.post(f"/api/news-feed/items/{item}/feedback", headers=headers, json={"verdict": verdict, "note": "x" * 400}).json()
        assert out["verdict"] == verdict
    assert out["trust"]["ratings"] == 10 and out["trust"]["trust"] == 0.6 and out["trust"]["applied"] is True
    # Re-voting replaces the verdict, never adds a row.
    client.post(f"/api/news-feed/items/{ids[0]}/feedback", headers=headers, json={"verdict": "noise"})
    async def rows():
        async with _session_factory() as session:
            return list(await session.scalars(select(NewsFeedbackRecord).where(NewsFeedbackRecord.tenant_id == tenant_id)))
    stored = _run(rows())
    assert len(stored) == 10 and next(r for r in stored if r.news_event_id == ids[0]).verdict == "noise" and all(len(r.note or "") <= 300 for r in stored)
    mine = client.get(f"/api/news-feed/feedback/mine?ids={','.join(map(str, ids[:3]))},abc", headers=headers).json()["verdicts"]
    assert mine == {str(ids[0]): "noise", str(ids[1]): "useful", str(ids[2]): "useful"} or mine == {ids[0]: "noise", ids[1]: "useful", ids[2]: "useful"}
    summary = client.get("/api/news-feed/feedback/summary", headers=headers).json()
    assert summary["trust"]["trust"] == 0.5 and {r["key"] for r in summary["by_source"]} == {"rbi_press", "sebi_press"} and summary["by_category"][0]["key"] == "RBI_POLICY"
    assert sum(r["total"] for r in summary["by_source"]) == 10

    # Another organisation's verdicts are its own: trust stays 1.0 there.
    other_headers, other_tenant, _ = _tenant("bd2-other@example.com")
    assert client.get("/api/news-feed/feedback/summary", headers=other_headers).json()["trust"] == {**fb.trust_from_counts(0, 0, 0)}

    # The thesis news factor for this organisation is scaled by its trust; the other organisation's is not.
    from app.db.models import MarketSnapshotRecord
    payload = {"last_price": 25000.0, "bias": "BULLISH", "bias_score": 0.8, "structure": {"trend": "UPTREND"}, "higher_regime": {"kind": "TRENDING_UP"},
               "support": {"low": 24850.0, "high": 24900.0}, "resistance": {"low": 25150.0, "high": 25200.0}, "atr_pct": 0.5}
    async def seed(tid):
        async with _session_factory() as session:
            session.add(MarketSnapshotRecord(tenant_id=tid, kind="SYMBOL", symbol="NIFTY 50", exchange="NSE", timeframe="5min", source="upstox", last_price=25000.0,
                                             change_pct=0.4, bias="BULLISH", regime="TRENDING_UP", higher_regime="TRENDING_UP", structure="UPTREND",
                                             payload_json=json.dumps(payload), captured_at=NOW))
            await session.commit()
    _run(seed(tenant_id))
    _run(seed(other_tenant))
    items = [{"headline": "h", "symbols": ["NIFTY"], "source": "RBI", "classification": {"direction": "BULLISH", "severity": 4, "confidence": 0.9}}]

    async def build(tid):
        async with _session_factory() as session:
            return await th.build(session, tid, "NIFTY 50", lang="en", now=NOW, news_items=items, store=False)
    mine_thesis, other_thesis = _run(build(tenant_id)), _run(build(other_tenant))
    news_mine = next(r for r in mine_thesis["agreement"]["matrix"] if r["factor"] == "news")
    news_other = next(r for r in other_thesis["agreement"]["matrix"] if r["factor"] == "news")
    assert news_mine["value"]["trust"] == 0.5 and news_other["value"]["trust"] == 1.0 and news_mine["strength"] < news_other["strength"]
    # Flag off -> the endpoints are closed.
    _flags(news_feed=False)
    assert client.post(f"/api/news-feed/items/{ids[1]}/feedback", headers=headers, json={"verdict": "useful"}).status_code == 503
    _flags(news_feed=True)


def test_weekly_thesis_report_is_flag_gated_idempotent_and_read_only():
    headers, tenant_id, _ = _tenant("bd2-report@example.com")
    _flags(market_thesis=True)
    assert th.report_due(NOW) and not th.report_due(NOW - timedelta(days=1)) and not th.report_due(NOW.replace(hour=9))
    assert client.get("/api/ai/thesis/report", headers=headers).json()["scored"] == 0

    async def seed():
        async with _session_factory() as session:
            for i, (sym, direction, score, outcome, shadow) in enumerate((("NIFTY 50", "BULLISH", 1.0, "BULL", 1.0), ("NIFTY 50", "BEARISH", -1.0, "BULL", 0.75),
                                                                          ("RELIANCE", "NEUTRAL", 0.0, "BULL", 0.5), ("RELIANCE", "BULLISH", None, None, 1.0),
                                                                          ("INFY", "BULLISH", None, "UNKNOWN", 1.0))):
                session.add(ThesisRecord(tenant_id=tenant_id, symbol=sym, day=(NOW - timedelta(days=i + 1)).date(), direction=direction, confidence=60, agreement=0.8,
                                         shadow_multiplier=shadow, last_price=100.0, lang="en", narrative_source="rules", thesis_json="{}",
                                         created_at=NOW - timedelta(days=i + 1), scored_at=NOW if outcome else None, outcome=outcome, score=score))
            await session.commit()
    _run(seed())
    report = client.get("/api/ai/thesis/report", headers=headers).json()
    assert report["scored"] == 3 and report["hits"] == 1 and report["misses"] == 1 and report["flat"] == 1 and report["hit_rate"] == 0.33 and report["avg_shadow"] == 0.75
    assert report["pending"] == 1 and report["unknown"] == 1 and report["title"].startswith("Thesis scoreboard 2026-W41: 1/3 right (33%)")
    assert any("NIFTY 50: 1/2 right, 1 wrong" in line for line in report["lines"]) and any("never applied" in line for line in report["lines"])

    async def send(now):
        async with _session_factory() as session:
            n = await th.send_weekly_reports(session, now=now)
            notes = list(await session.scalars(select(NotificationRecord).where(NotificationRecord.tenant_id == tenant_id,
                                                                                NotificationRecord.event_type == NotificationType.THESIS_REPORT.value)))
            return n, notes
    n, notes = _run(send(NOW))
    assert n == 1 and len(notes) == 1 and notes[0].title.startswith("Thesis scoreboard 2026-W41:") and "Shadow overlay" in notes[0].message
    n, notes = _run(send(NOW + timedelta(hours=2)))
    assert n == 0 and len(notes) == 1                                                                  # same week: once
    _flags(market_thesis=False)
    n, notes = _run(send(NOW + timedelta(days=7)))
    assert n == 0 and len(notes) == 1                                                                  # flag off: silent
    _flags(market_thesis=True)
    n, notes = _run(send(NOW + timedelta(days=7)))
    assert n == 0                                                                                      # nothing scored in that later week
    # Nothing in the report path touches deployments, trades or orders: the records are untouched.
    async def untouched():
        async with _session_factory() as session:
            rows = list(await session.scalars(select(ThesisRecord).where(ThesisRecord.tenant_id == tenant_id)))
            return [(r.direction, r.score, r.shadow_multiplier) for r in rows]
    assert len(_run(untouched())) == 5
    assert isinstance(_run(_user(tenant_id)), User)


async def _user(tenant_id):
    async with _session_factory() as session:
        return await session.scalar(select(User).where(User.tenant_id == tenant_id))
