"""Security fix: contributed reference rows belong to the organisation that entered them.

Before, any trading member of any organisation could write shareholding patterns, calendar events, financial periods,
corporate actions, qualitative factors, sector metrics, company profiles and manual news events that every other
organisation read. Now:
- a contributed row is visible to its organisation only; provider-ingested rows (and the news feed) stay shared;
- a shared company profile changes only through the provider refresh (or a platform administrator);
- a contributed row's dates are checked: no future period / quarter end, no publication date in the future or before
  the period it reports;
- the readers (the fundamentals pages and analyses, the Factor Lab fill, the news events list) read shared rows plus the
  caller organisation's own; an anonymous read sees shared rows only."""
import asyncio
from datetime import date, timedelta

from app.db.models import CompanyRecord, ShareholdingSnapshotRecord
from app.fundamentals import ingest
from tests.test_auth_api import _register, _session_factory, client
from tests.test_fundamentals_api import _sample_period, _sample_profile
from tests.test_phase_ah_fundamentals_ingest import _mock_nse


def _org(email):
    return {"Authorization": f"Bearer {_register(email)}"}


def _shared_company(symbol):
    async def go():
        async with _session_factory() as session:
            session.add(CompanyRecord(symbol=symbol, name=f"{symbol} Ltd", sector="Banking", industry="Banks"))   # tenant_id NULL
            await session.commit()
    asyncio.run(go())


def _cite(day):
    return {"source": "exchange filing", "publication_date": day.isoformat()}


def test_one_organisation_cannot_rewrite_what_another_reads():
    a, b = _org("scope-a@example.com"), _org("scope-b@example.com")
    _shared_company("SHRD")
    base = "/api/fundamentals/companies/SHRD"
    q_end = date(2026, 6, 30)
    # org A tries every contributed kind of row on a shared company
    assert client.post(f"{base}/shareholding", headers=a, json={"as_of_date": q_end.isoformat(), "promoter_pct": 99.0,
                                                                  "source": _cite(q_end + timedelta(days=20))}).status_code == 201
    assert client.post(f"{base}/calendar", headers=a, json={"event_type": "RESULTS", "event_date": "2099-01-01"}).status_code == 201
    assert client.post(f"{base}/financials", headers=a, json=_sample_period("FY24", pat=1.0)).status_code == 201
    assert client.post(f"{base}/corporate-actions", headers=a, json={"action_type": "BUYBACK", "announced_date": "2026-01-05",
                                                                       "headline": "fake buyback"}).status_code == 201
    assert client.post(f"{base}/qualitative-factors", headers=a, json={"category": "BUSINESS_QUALITY_MOAT", "label": "x",
                                                                         "score": 1}).status_code == 201
    assert client.post(f"{base}/sector-metrics", headers=a, json={"period_label": "FY24", "metric_code": "NIM_PCT",
                                                                    "value": 0.1}).status_code == 201
    assert client.put(base, headers=a, json={**_sample_profile("SHRD"), "name": "Poisoned"}).status_code == 403
    # org A sees its own rows ...
    assert [r["promoter_pct"] for r in client.get(f"{base}/shareholding", headers=a).json()] == [99.0]
    # ... org B and an anonymous reader see none of them
    for who in (b, {}):
        assert client.get(f"{base}/shareholding", headers=who).json() == []
        assert client.get(f"{base}/calendar", headers=who).json() == []
        assert client.get(f"{base}/financials", headers=who).json() == []
        assert client.get(f"{base}/corporate-actions", headers=who).json() == []
        assert client.get(f"{base}/qualitative-factors", headers=who).json() == []
        assert client.get(f"{base}/sector-metrics", headers=who).json() == []
        assert client.get(base, headers=who).json()["name"] == "SHRD Ltd"
        assert not any(e["symbol"] == "SHRD" for e in client.get("/api/fundamentals/calendar/upcoming", headers=who).json())
        assert client.get(f"{base}/analysis/growth", headers=who).status_code == 422          # no financials it may read
    # B's own figures for the same period do not collide with A's, and A's import never overwrites B's
    assert client.post(f"{base}/financials", headers=b, json=_sample_period("FY24", pat=7.0)).status_code == 201
    assert client.post(f"{base}/financials/import", headers=a, json={"periods": [_sample_period("FY24", pat=2.0)]}).json()["updated"] == 1
    assert [p["pat"] for p in client.get(f"{base}/financials", headers=b).json()] == [7.0]
    assert [p["pat"] for p in client.get(f"{base}/financials", headers=a).json()] == [2.0]


def test_contributed_dates_are_checked():
    a = _org("scope-dates@example.com")
    _shared_company("DATES")
    base = "/api/fundamentals/companies/DATES"
    future = (date.today() + timedelta(days=30)).isoformat()
    bad = [
        (f"{base}/shareholding", {"as_of_date": future, "promoter_pct": 50.0}, "after today"),
        (f"{base}/shareholding", {"as_of_date": "2026-06-30", "promoter_pct": 50.0, "source": _cite(date(2026, 6, 1))}, "before the period"),
        (f"{base}/shareholding", {"as_of_date": "2026-03-31", "promoter_pct": 50.0, "source": {"source": "x", "publication_date": future}}, "after today"),
        (f"{base}/financials", {**_sample_period("FY99"), "period_end_date": future}, "after today"),
        (f"{base}/financials", {**_sample_period("FY23"), "source": _cite(date(2023, 1, 1))}, "before the period"),
        (f"{base}/corporate-actions", {"action_type": "X", "announced_date": future, "headline": "h"}, "after today"),
        (f"{base}/calendar", {"event_type": "RESULTS", "event_date": "2099-01-01", "source": {"source": "x", "publication_date": future}}, "after today"),
    ]
    for path, body, why in bad:
        r = client.post(path, headers=a, json=body)
        assert r.status_code == 422 and why in r.text, (path, body, r.text)
    # a future event date is fine (that is what a calendar is for)
    assert client.post(f"{base}/calendar", headers=a, json={"event_type": "RESULTS", "event_date": "2099-01-01"}).status_code == 201


def test_an_organisation_s_own_company_is_private_until_the_provider_makes_it_shared():
    a, b = _org("scope-co-a@example.com"), _org("scope-co-b@example.com")
    assert client.post("/api/fundamentals/companies", headers=a, json={**_sample_profile("REFR2"), "website": "https://fake.example",
                                                                       "business_description": "made up"}).status_code == 201
    assert client.get("/api/fundamentals/companies/REFR2", headers=a).status_code == 200
    assert client.put("/api/fundamentals/companies/REFR2", headers=a, json={**_sample_profile("REFR2"), "name": "Mine"}).status_code == 200
    assert client.get("/api/fundamentals/companies/REFR2", headers=b).status_code == 404
    assert client.get("/api/fundamentals/companies/REFR2").status_code == 404
    assert not any(c["symbol"] == "REFR2" for c in client.get("/api/fundamentals/companies", headers=b).json())
    taken = client.post("/api/fundamentals/companies", headers=b, json=_sample_profile("REFR2"))
    assert taken.status_code == 409 and "refresh it from the provider" in taken.text
    ingest.set_provider_factory("nse", _mock_nse())
    try:
        res = client.post("/api/fundamentals/refresh", headers=b, json={"symbols": ["REFR2"]})
        assert res.status_code == 200, res.text
    finally:
        ingest.set_provider_factory("nse", None)
        ingest.set_provider_factory("nse", ingest._nse_factory)
    shared = client.get("/api/fundamentals/companies/REFR2", headers=b).json()
    assert shared["name"] == "Refreshed Co Ltd" and shared["website"] is None and shared["business_description"] is None
    # the provider's shareholding is shared reference data: both organisations (and anonymous readers) see it
    for who in (a, b, {}):
        assert len(client.get("/api/fundamentals/companies/REFR2/shareholding", headers=who).json()) == 1
    # and now nobody but the provider (or an administrator) edits the shared profile
    assert client.put("/api/fundamentals/companies/REFR2", headers=a, json={**_sample_profile("REFR2"), "name": "Mine"}).status_code == 403

    async def rows():
        async with _session_factory() as session:
            from sqlalchemy import select
            company = await session.scalar(select(CompanyRecord).where(CompanyRecord.symbol == "REFR2"))
            snaps = list(await session.scalars(select(ShareholdingSnapshotRecord).where(ShareholdingSnapshotRecord.company_id == company.id)))
            return company.tenant_id, [s.tenant_id for s in snaps]
    assert asyncio.run(rows()) == (None, [None])


def test_the_factor_lab_fill_reads_only_shared_and_own_financials():
    a, b = _org("scope-q-a@example.com"), _org("scope-q-b@example.com")
    _shared_company("QSCOPE")
    fy23 = {**_sample_period("FY23", pat=80.0), "period_end_date": "2023-03-31"}
    for body in (fy23, _sample_period("FY24")):
        assert client.post("/api/fundamentals/companies/QSCOPE/financials", headers=a, json=body).status_code == 201

    async def fill(viewer):
        from app.quant import fundamentals_bridge
        async with _session_factory() as session:
            return await fundamentals_bridge.fundamentals_for(session, ["QSCOPE"], {"QSCOPE": 300.0}, viewer=viewer)
    me = client.get("/api/auth/me", headers=a).json()
    assert asyncio.run(fill(me["tenant_id"]))["QSCOPE"].values
    other = client.get("/api/auth/me", headers=b).json()
    assert not asyncio.run(fill(other["tenant_id"]))["QSCOPE"].values
    assert not asyncio.run(fill(None))["QSCOPE"].values


def test_a_manual_news_event_is_the_organisation_s_own():
    a, b = _org("scope-news-a@example.com"), _org("scope-news-b@example.com")
    event = {"category": "RBI_POLICY", "headline": "Emergency rate cut", "event_date": "2026-10-01", "affected_symbols": ["HDFCBANK"],
             "sentiment": "Bullish", "source": {"source": "rbi.org.in"}}
    made = client.post("/api/news-events", headers=a, json=event)
    assert made.status_code == 201, made.text
    event_id = made.json()["id"]
    assert any(e["id"] == event_id for e in client.get("/api/news-events", headers=a).json())
    for who in (b, {}):
        assert not any(e["id"] == event_id for e in client.get("/api/news-events", headers=who).json())
        assert client.get(f"/api/news-events/{event_id}", headers=who).status_code == 404
    assert client.delete(f"/api/news-events/{event_id}", headers=b).status_code == 404
    future = {**event, "source": {"source": "x", "publication_date": (date.today() + timedelta(days=9)).isoformat()}}
    assert client.post("/api/news-events", headers=a, json=future).status_code == 422
