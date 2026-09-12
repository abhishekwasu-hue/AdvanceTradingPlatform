import asyncio
from typing import Callable

import httpx
import pytest

from app.fundamentals.providers.exceptions import FundamentalDataProviderError
from app.fundamentals.providers.nse import NSEProvider


def _mock_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://www.nseindia.com")


def test_get_company_profile_parses_real_shaped_nse_payload():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/get-quotes/equity":
            return httpx.Response(200, text="<html>ok</html>")
        if request.url.path == "/api/quote-equity":
            assert request.url.params["symbol"] == "TCS"
            return httpx.Response(200, json={
                "info": {"companyName": "Tata Consultancy Services Limited", "isin": "INE467B01029"},
                "metadata": {"listingDate": "25-Aug-2004"},
                "securityInfo": {"faceValue": 1, "issuedSize": 3659444370},
                "priceInfo": {"lastPrice": 3800.5},
                "industryInfo": {"macro": "Information Technology", "industry": "IT - Software", "sector": "IT", "basicIndustry": "Computers - Software"},
            })
        raise AssertionError(f"unexpected path {request.url.path}")

    provider = NSEProvider(client=_mock_client(handler))
    profile = asyncio.run(provider.get_company_profile("TCS"))

    assert profile.name == "Tata Consultancy Services Limited"
    assert profile.isin == "INE467B01029"
    assert profile.sector == "Information Technology"
    assert profile.sub_industry == "Computers - Software"
    assert profile.face_value == 1
    assert profile.market_cap is not None and profile.market_cap > 0
    assert profile.source is not None
    assert profile.source.source.startswith("NSE India")


def test_get_shareholding_pattern_parses_latest_row():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/get-quotes/equity":
            return httpx.Response(200, text="ok")
        if request.url.path == "/api/corp-info":
            return httpx.Response(200, json={"data": [
                {"date_end": "31-Dec-2024", "promoter": "45.5", "promoterPledge": "1.2", "fii": "20.0", "dii": "15.0", "public": "19.3"},
            ]})
        raise AssertionError

    provider = NSEProvider(client=_mock_client(handler))
    snapshot = asyncio.run(provider.get_shareholding_pattern("TCS"))
    assert snapshot.promoter_pct == 45.5
    assert snapshot.promoter_pledge_pct == 1.2
    assert snapshot.fii_pct == 20.0


def test_get_corporate_announcements_parses_list():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/get-quotes/equity":
            return httpx.Response(200, text="ok")
        if request.url.path == "/api/corporate-announcements":
            return httpx.Response(200, json=[
                {"an_dt": "2024-01-15", "desc": "Board Meeting Intimation", "attchmntText": "Q3 results"},
            ])
        raise AssertionError

    provider = NSEProvider(client=_mock_client(handler))
    actions = asyncio.run(provider.get_corporate_announcements("TCS"))
    assert len(actions) == 1
    assert actions[0].headline == "Board Meeting Intimation"


def test_session_priming_failure_raises_provider_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="blocked")

    provider = NSEProvider(client=_mock_client(handler))
    with pytest.raises(FundamentalDataProviderError):
        asyncio.run(provider.get_company_profile("TCS"))


def test_api_error_after_successful_priming_raises_provider_error():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/get-quotes/equity":
            return httpx.Response(200, text="ok")
        return httpx.Response(500, text="server error")

    provider = NSEProvider(client=_mock_client(handler))
    with pytest.raises(FundamentalDataProviderError):
        asyncio.run(provider.get_company_profile("TCS"))
