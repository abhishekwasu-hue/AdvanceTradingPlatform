from tests.test_auth_api import _register, client


def _sample_profile(symbol="TESTCO"):
    return {
        "symbol": symbol, "name": "Test Company Ltd", "sector": "Information Technology",
        "industry": "IT Services", "promoter_holding_pct": 55.0,
    }


def _sample_period(label="FY24", pat=100.0):
    return {
        "period_type": "ANNUAL", "period_label": label, "period_end_date": "2024-03-31",
        "revenue": 1000.0, "ebitda": 250.0, "ebit": 200.0, "pat": pat, "eps": 20.0,
        "shares_outstanding": 50.0, "cfo": 90.0, "capex": 40.0, "total_debt": 300.0,
        "cash_and_equivalents": 50.0, "current_assets": 400.0, "current_liabilities": 300.0,
        "shareholders_equity": 500.0, "total_assets": 900.0, "interest_expense": 20.0,
    }


def test_create_and_get_company_requires_auth_for_write():
    assert client.post("/api/fundamentals/companies", json=_sample_profile("NOAUTH")).status_code in (401, 403)


def test_company_crud_flow():
    token = _register("analyst1@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    create = client.post("/api/fundamentals/companies", headers=headers, json=_sample_profile("ACME"))
    assert create.status_code == 201, create.text

    get_resp = client.get("/api/fundamentals/companies/ACME")
    assert get_resp.status_code == 200
    assert get_resp.json()["name"] == "Test Company Ltd"

    listing = client.get("/api/fundamentals/companies")
    assert any(c["symbol"] == "ACME" for c in listing.json())

    duplicate = client.post("/api/fundamentals/companies", headers=headers, json=_sample_profile("ACME"))
    assert duplicate.status_code == 409

    unknown = client.get("/api/fundamentals/companies/NOSUCHCO")
    assert unknown.status_code == 404


def test_financial_period_and_analysis_endpoints():
    token = _register("analyst2@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/api/fundamentals/companies", headers=headers, json=_sample_profile("BETA"))

    add_period = client.post("/api/fundamentals/companies/BETA/financials", headers=headers, json=_sample_period())
    assert add_period.status_code == 201, add_period.text

    growth = client.get("/api/fundamentals/companies/BETA/analysis/growth")
    assert growth.status_code == 200
    assert growth.json()["latest_period"] == "FY24"

    profitability = client.get("/api/fundamentals/companies/BETA/analysis/profitability")
    assert profitability.status_code == 200
    assert profitability.json()["ebitda_margin_pct"] == 25.0

    earnings_quality = client.get("/api/fundamentals/companies/BETA/analysis/earnings-quality")
    assert earnings_quality.status_code == 200

    balance_sheet = client.get("/api/fundamentals/companies/BETA/analysis/balance-sheet")
    assert balance_sheet.status_code == 200

    cash_flow = client.get("/api/fundamentals/companies/BETA/analysis/cash-flow")
    assert cash_flow.status_code == 200
    assert cash_flow.json()["fcf"] == 50.0

    red_flags = client.get("/api/fundamentals/companies/BETA/analysis/red-flags")
    assert red_flags.status_code == 200

    scenario = client.get("/api/fundamentals/companies/BETA/analysis/scenario")
    assert scenario.status_code == 200
    assert scenario.json()["bull"]["revenue"] > scenario.json()["bear"]["revenue"]


def test_analysis_endpoints_422_when_no_financials_entered():
    token = _register("analyst3@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/api/fundamentals/companies", headers=headers, json=_sample_profile("EMPTYCO"))

    response = client.get("/api/fundamentals/companies/EMPTYCO/analysis/growth")
    assert response.status_code == 422


def test_valuation_and_dcf_endpoints():
    token = _register("analyst4@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/api/fundamentals/companies", headers=headers, json=_sample_profile("GAMMA"))
    client.post("/api/fundamentals/companies/GAMMA/financials", headers=headers, json=_sample_period())

    valuation = client.post(
        "/api/fundamentals/companies/GAMMA/analysis/valuation",
        json={"market_price": 300.0, "book_value_per_share": 100.0, "historical_pe_avg_5y": 12.0},
    )
    assert valuation.status_code == 200
    assert valuation.json()["pe"] == 15.0  # 300 / eps(20)

    dcf = client.post(
        "/api/fundamentals/companies/GAMMA/analysis/dcf",
        json={
            "base_revenue": 1000.0, "revenue_growth_pct": [10, 10, 8], "ebitda_margin_pct": 25.0,
            "wacc_pct": 11.0, "terminal_growth_pct": 4.0, "net_debt": 300.0, "shares_outstanding": 50.0,
        },
    )
    assert dcf.status_code == 200
    assert dcf.json()["bull"]["intrinsic_value_per_share"] > dcf.json()["bear"]["intrinsic_value_per_share"]


def test_fundamental_score_and_fusion_endpoints():
    token = _register("analyst5@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/api/fundamentals/companies", headers=headers, json=_sample_profile("DELTA"))
    client.post("/api/fundamentals/companies/DELTA/financials", headers=headers, json=_sample_period())
    client.post(
        "/api/fundamentals/companies/DELTA/qualitative-factors", headers=headers,
        json={"category": "BUSINESS_QUALITY_MOAT", "label": "Pricing Power", "score": 80},
    )

    score = client.post(
        "/api/fundamentals/companies/DELTA/analysis/score",
        json={"sector_outlook_0_100": 70, "macro_event_risk_0_100": 60, "management_score_0_100": 75},
    )
    assert score.status_code == 200
    body = score.json()
    assert 0 <= body["score"] <= 100
    assert len(body["breakdown"]) == 10

    fusion = client.post(
        "/api/fundamentals/fusion",
        json={
            "fundamental_score": body["score"], "technical_score": 88,
            "technical_direction": "LONG", "horizon": "Swing",
        },
    )
    assert fusion.status_code == 200
    assert fusion.json()["horizon"] == "Swing"


def test_company_intelligence_card():
    token = _register("analyst6@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/api/fundamentals/companies", headers=headers, json=_sample_profile("EPSILON"))
    client.post("/api/fundamentals/companies/EPSILON/financials", headers=headers, json=_sample_period())

    card = client.get("/api/fundamentals/companies/EPSILON/card")
    assert card.status_code == 200
    body = card.json()
    assert body["symbol"] == "EPSILON"
    assert "fundamental_grade" in body


def test_screener_filters_by_persisted_companies():
    token = _register("analyst7@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/api/fundamentals/companies", headers=headers, json=_sample_profile("ZETA"))
    client.post("/api/fundamentals/companies/ZETA/financials", headers=headers, json=_sample_period())

    response = client.post("/api/fundamentals/screener", json={"min_promoter_holding_pct": 50.0})
    assert response.status_code == 200
    assert any(c["symbol"] == "ZETA" for c in response.json())

    response2 = client.post("/api/fundamentals/screener", json={"min_promoter_holding_pct": 99.0})
    assert not any(c["symbol"] == "ZETA" for c in response2.json())


def test_sector_rotation_aggregates_by_sector():
    token = _register("analyst8@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/api/fundamentals/companies", headers=headers, json=_sample_profile("ETA"))
    client.post("/api/fundamentals/companies/ETA/financials", headers=headers, json=_sample_period())

    response = client.get("/api/fundamentals/sectors")
    assert response.status_code == 200
    sector_row = next(r for r in response.json() if r["sector"] == "Information Technology")
    assert sector_row["companies_tracked"] >= 1


def test_earnings_calendar_crud_and_upcoming_feed():
    token = _register("analyst9@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/api/fundamentals/companies", headers=headers, json=_sample_profile("THETA"))

    unauth = client.post("/api/fundamentals/companies/THETA/calendar", json={"event_type": "RESULTS", "event_date": "2099-01-01"})
    assert unauth.status_code in (401, 403)

    add = client.post(
        "/api/fundamentals/companies/THETA/calendar", headers=headers,
        json={"event_type": "RESULTS", "event_date": "2099-01-01", "description": "Q3 FY99 results"},
    )
    assert add.status_code == 201, add.text

    listing = client.get("/api/fundamentals/companies/THETA/calendar")
    assert listing.status_code == 200
    assert any(e["event_type"] == "RESULTS" for e in listing.json())

    upcoming = client.get("/api/fundamentals/calendar/upcoming")
    assert upcoming.status_code == 200
    assert any(e["symbol"] == "THETA" for e in upcoming.json())


def test_peer_comparison_ranks_companies_in_same_sector():
    token = _register("analyst10@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/api/fundamentals/companies", headers=headers, json=_sample_profile("IOTA"))
    client.post("/api/fundamentals/companies/IOTA/financials", headers=headers, json=_sample_period(pat=100.0))

    weaker = _sample_profile("KAPPA")
    client.post("/api/fundamentals/companies", headers=headers, json=weaker)
    weak_period = _sample_period(pat=10.0)
    weak_period["ebitda"] = 50.0
    weak_period["ebit"] = 20.0
    client.post("/api/fundamentals/companies/KAPPA/financials", headers=headers, json=weak_period)

    response = client.get("/api/fundamentals/sectors/Information Technology/peers")
    assert response.status_code == 200
    symbols = [r["symbol"] for r in response.json()]
    assert "IOTA" in symbols and "KAPPA" in symbols
    assert symbols.index("IOTA") < symbols.index("KAPPA")


def test_pre_earnings_requires_upcoming_event_then_analyzes():
    token = _register("analyst11@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/api/fundamentals/companies", headers=headers, json=_sample_profile("LAMBDA"))
    client.post("/api/fundamentals/companies/LAMBDA/financials", headers=headers, json=_sample_period())

    missing = client.get("/api/fundamentals/companies/LAMBDA/analysis/pre-earnings")
    assert missing.status_code == 404

    client.post(
        "/api/fundamentals/companies/LAMBDA/calendar", headers=headers,
        json={"event_type": "RESULTS", "event_date": "2099-06-30"},
    )

    response = client.get("/api/fundamentals/companies/LAMBDA/analysis/pre-earnings")
    assert response.status_code == 200
    body = response.json()
    assert body["upcoming_event_date"] == "2099-06-30"
    assert "earnings_bias" in body and "risk_level" in body
