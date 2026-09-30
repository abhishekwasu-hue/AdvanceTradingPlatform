"""Phase AH: bulk financials import and provider refresh for the Fundamentals module."""
from datetime import date

import httpx

from app.core.enums import PeriodType
from app.fundamentals import ingest
from app.fundamentals.providers.nse import NSEProvider
from tests.test_auth_api import _register, client
from tests.test_fundamentals_api import _sample_period, _sample_profile

CSV = """Period Type,Period,End Date,Sales,EBITDA,Net Profit,EPS,Shares,Total Debt,Equity,Ignored
FY,FY23,31-03-2023,"1,000",250,80,16,50,300,450,x
annual,FY24,2024-03-31,1200,300,100,20,50,280,500,y
Q,Q1FY25,Jun 2024,320,80,(5),-1,50,,510,z
FY,FY25,not-a-date,1300,320,110,22,50,270,540,
FY,,2026-03-31,1400,330,120,24,50,260,560,
"""


def test_parse_financials_csv_maps_aliases_numbers_dates_and_reports_bad_rows():
    parsed = ingest.parse_financials_csv(CSV)
    assert [p.period_label for p in parsed.periods] == ["FY23", "FY24", "Q1FY25"]
    fy23, fy24, q1 = parsed.periods
    assert fy23.period_type == PeriodType.ANNUAL and fy23.revenue == 1000.0 and fy23.total_debt == 300.0 and fy23.shareholders_equity == 450.0
    assert fy23.period_end_date == date(2023, 3, 31) and fy24.period_end_date == date(2024, 3, 31)
    assert q1.period_type == PeriodType.QUARTER and q1.period_end_date == date(2024, 6, 30) and q1.pat == -5.0 and q1.total_debt is None
    assert len(parsed.errors) == 2 and parsed.errors[0].startswith("line 5:") and "date" in parsed.errors[0] and parsed.errors[1].startswith("line 6:")
    assert "ignored" in parsed.columns

    tsv = "period_type\tperiod_label\tperiod_end_date\trevenue\tebitda\tpat\nANNUAL\tFY24\t2024-03-31\t10\t2\t1\n"
    assert len(ingest.parse_financials_csv(tsv).periods) == 1
    missing = ingest.parse_financials_csv("period_label,revenue\nFY24,10\n")
    assert missing.periods == [] and "missing required column" in missing.errors[0]
    assert ingest.parse_financials_csv("   ").errors == ["empty input"]


def test_import_endpoint_upserts_and_duplicate_form_post_is_409():
    token = _register("ingest1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    assert client.post("/api/fundamentals/companies", headers=headers, json=_sample_profile("INGEST")).status_code == 201
    assert client.post("/api/fundamentals/companies/INGEST/financials", headers=headers, json=_sample_period("FY24")).status_code == 201
    assert client.post("/api/fundamentals/companies/INGEST/financials", headers=headers, json=_sample_period("FY24")).status_code == 409

    assert client.post("/api/fundamentals/companies/INGEST/financials/import", json={"csv": CSV}).status_code in (401, 403)
    assert client.post("/api/fundamentals/companies/NOPE/financials/import", headers=headers, json={"csv": CSV}).status_code == 404
    assert client.post("/api/fundamentals/companies/INGEST/financials/import", headers=headers, json={}).status_code == 400

    res = client.post("/api/fundamentals/companies/INGEST/financials/import", headers=headers, json={"csv": CSV})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["created"] == 2 and body["updated"] == 1 and len(body["errors"]) == 2 and body["total_rows"] == 5
    periods = {p["period_label"]: p for p in client.get("/api/fundamentals/companies/INGEST/financials").json()}
    assert set(periods) == {"FY23", "FY24", "Q1FY25"}
    assert periods["FY24"]["revenue"] == 1200.0 and periods["FY24"]["shareholders_equity"] == 500.0   # form value 1000 -> corrected by the import

    # JSON list path and a re-import are idempotent updates.
    again = client.post("/api/fundamentals/companies/INGEST/financials/import", headers=headers,
                        json={"periods": [_sample_period("FY24", pat=123.0)]}).json()
    assert again["created"] == 0 and again["updated"] == 1
    assert {p["period_label"]: p for p in client.get("/api/fundamentals/companies/INGEST/financials").json()}["FY24"]["pat"] == 123.0

    # The analysis endpoints and the Factor Lab bridge read the imported periods straight away.
    growth = client.get("/api/fundamentals/companies/INGEST/analysis/growth").json()
    assert growth["latest_period"] == "FY24"


def _mock_nse(profile_status=200, announcements=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/get-quotes/equity":
            return httpx.Response(200, text="ok")
        if request.url.path == "/api/quote-equity":
            if profile_status != 200:
                return httpx.Response(profile_status, text="blocked")
            return httpx.Response(200, json={
                "info": {"companyName": "Refreshed Co Ltd", "isin": "INE000A01001"}, "metadata": {"listingDate": "25-Aug-2004"},
                "securityInfo": {"faceValue": 2, "issuedSize": 100_000_000}, "priceInfo": {"lastPrice": 500.0},
                "industryInfo": {"macro": "Financial Services", "industry": "Banks", "basicIndustry": "Private Sector Bank"},
            })
        if request.url.path == "/api/corp-info":
            return httpx.Response(200, json={"data": [{"date_end": "30-Jun-2026", "promoter": "51.2", "promoterPledge": "0", "fii": "20", "dii": "15", "public": "13.8"}]})
        if request.url.path == "/api/corporate-announcements":
            return httpx.Response(200, json=announcements if announcements is not None else [
                {"an_dt": "2026-07-15", "desc": "Board Meeting Intimation", "attchmntText": "Q1 results"},
                {"an_dt": "2026-07-20", "desc": "Dividend", "attchmntText": "Rs 5"},
            ])
        raise AssertionError(request.url.path)
    return lambda: NSEProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://www.nseindia.com"))


def test_refresh_endpoints_pull_profile_shareholding_and_announcements(monkeypatch):
    token = _register("ingest2@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    ingest.set_provider_factory("nse", _mock_nse())
    try:
        assert client.get("/api/fundamentals/providers").json()["providers"] == ["nse"]
        assert client.post("/api/fundamentals/companies/REFR/refresh", headers=headers, json={}).status_code == 404
        assert client.post("/api/fundamentals/companies", headers=headers, json={**_sample_profile("REFR"), "website": "https://refr.example", "market_cap": None}).status_code == 201
        assert client.post("/api/fundamentals/companies/REFR/refresh", headers=headers, json={"provider": "nope"}).status_code == 400

        res = client.post("/api/fundamentals/companies/REFR/refresh", headers=headers, json={})
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["provider"] == "nse" and body["errors"] == [] and body["shareholding_added"] is True and body["announcements_added"] == 2
        assert {"name", "isin", "market_cap", "promoter_holding_pct"} <= set(body["profile_changed"])
        company = client.get("/api/fundamentals/companies/REFR").json()
        assert company["name"] == "Refreshed Co Ltd" and company["website"] == "https://refr.example"     # hand-entered field survives
        assert company["promoter_holding_pct"] == 51.2 and company["market_cap"] == 5000.0 and company["source"]["source"].startswith("NSE India")
        assert len(client.get("/api/fundamentals/companies/REFR/shareholding").json()) == 1
        assert len(client.get("/api/fundamentals/companies/REFR/corporate-actions").json()) == 2

        # Idempotent: the same snapshot and announcements are not stored twice.
        again = client.post("/api/fundamentals/companies/REFR/refresh", headers=headers, json={}).json()
        assert again["shareholding_added"] is False and again["announcements_added"] == 0 and again["profile_changed"] == []
        assert len(client.get("/api/fundamentals/companies/REFR/corporate-actions").json()) == 2

        # Bulk: creates the missing company from the provider, refreshes the known one, keeps going past a failure.
        ingest.set_provider_factory("nse", _mock_nse(profile_status=403))
        blocked = client.post("/api/fundamentals/refresh", headers=headers, json={"symbols": ["NEWCO"]}).json()
        assert blocked["created"] == 0 and blocked["failed"] == 1 and blocked["results"][0]["errors"][0].startswith("profile:")
        assert client.get("/api/fundamentals/companies/NEWCO").status_code == 404
        ingest.set_provider_factory("nse", _mock_nse())
        bulk = client.post("/api/fundamentals/refresh", headers=headers, json={"symbols": ["newco", "REFR", "NEWCO"]}).json()
        assert bulk["created"] == 1 and bulk["failed"] == 0 and [r["symbol"] for r in bulk["results"]] == ["NEWCO", "REFR"]
        newco = client.get("/api/fundamentals/companies/NEWCO").json()
        assert newco["name"] == "Refreshed Co Ltd" and newco["sector"] == "Financial Services" and newco["promoter_holding_pct"] == 51.2

        # A provider that fails everything is a 502 on the single-company route.
        ingest.set_provider_factory("nse", _mock_nse(profile_status=403))
        failed = client.post("/api/fundamentals/companies/REFR/refresh", headers=headers, json={"shareholding": False, "announcements": False})
        assert failed.status_code == 502 and "profile:" in failed.json()["detail"]["errors"][0]
    finally:
        ingest.set_provider_factory("nse", ingest._nse_factory)
