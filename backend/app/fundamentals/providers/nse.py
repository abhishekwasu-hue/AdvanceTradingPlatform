"""NSE India (National Stock Exchange) - the primary/official source the user explicitly asked
this platform to prioritize. NSE does not publish a documented, versioned public API; the
endpoints below are its actual public JSON endpoints (nseindia.com/api/...), the same ones
several well-known open-source community libraries (e.g. `nsepython`, `jugaad-data`) use, but
NSE can change response shapes or block automated access without notice, and requires an
anti-bot-friendly session (a same-origin cookie obtained by first visiting the site, plus a
browser-like User-Agent) rather than a plain API key.

**Honesty note (same as the Docker Hub situation in docs/ARCHITECTURE.md):** this development
sandbox's network egress policy blocks nseindia.com entirely (confirmed via a 403 policy denial
on a direct curl, not a transient error), so this adapter's real network behavior could not be
exercised end-to-end from here. The parsing logic is verified with realistic mocked responses
(tests/test_nse_provider.py) via `httpx.MockTransport`, exactly like the Zerodha/Upstox/Shoonya
broker adapters were before real credentials existed for them - please smoke-test this against
the live NSE site before relying on it, and expect to adjust endpoint paths/headers if NSE has
changed them.
"""
from datetime import date, datetime
from typing import Dict, List, Optional

import httpx

from app.core.enums import CapCategory
from app.fundamentals.models import CompanyProfile, CorporateAction, ShareholdingSnapshot, SourceCitation
from app.fundamentals.providers.base import FundamentalDataProvider
from app.fundamentals.providers.exceptions import FundamentalDataProviderError

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}


def _cap_category(market_cap_crore: Optional[float]) -> Optional[CapCategory]:
    """Rough SEBI-style large/mid/small-cap bands by market cap (in ₹ crore). SEBI's actual
    classification is by rank among all listed companies (top 100 / next 150 / rest), which
    this provider doesn't have the full universe to compute - this is a documented
    approximation, not the official ranking.
    """
    if market_cap_crore is None:
        return None
    if market_cap_crore >= 20_000:
        return CapCategory.LARGE_CAP
    if market_cap_crore >= 5_000:
        return CapCategory.MID_CAP
    if market_cap_crore >= 500:
        return CapCategory.SMALL_CAP
    return CapCategory.MICRO_CAP


class NSEProvider(FundamentalDataProvider):
    name = "nse"
    BASE_URL = "https://www.nseindia.com"

    def __init__(self, client: Optional[httpx.AsyncClient] = None) -> None:
        self._client = client or httpx.AsyncClient(base_url=self.BASE_URL, headers=_BROWSER_HEADERS, timeout=15.0)
        self._session_primed = False

    async def _prime_session(self) -> None:
        """NSE only serves its /api/* JSON endpoints to a client carrying cookies set by a
        prior visit to the public site - a bare API call gets a 401/403.
        """
        if self._session_primed:
            return
        response = await self._client.get("/get-quotes/equity", params={"symbol": "SBIN"})
        if response.status_code >= 400:
            raise FundamentalDataProviderError(
                "Could not establish an NSE session (priming request failed)",
                status_code=response.status_code, raw=response.text[:500],
            )
        self._session_primed = True

    async def _get_json(self, path: str, params: Optional[Dict[str, str]] = None) -> dict:
        await self._prime_session()
        response = await self._client.get(path, params=params)
        if response.status_code >= 400:
            raise FundamentalDataProviderError(
                f"NSE API call to {path} failed", status_code=response.status_code, raw=response.text[:500],
            )
        try:
            return response.json()
        except ValueError as exc:
            raise FundamentalDataProviderError(f"NSE API returned non-JSON for {path}", raw=response.text[:500]) from exc

    async def get_company_profile(self, symbol: str) -> CompanyProfile:
        payload = await self._get_json("/api/quote-equity", params={"symbol": symbol})
        info = payload.get("info", {})
        metadata = payload.get("metadata", {})
        security_info = payload.get("securityInfo", {})
        price_info = payload.get("priceInfo", {})
        industry_info = payload.get("industryInfo", {})

        last_price = price_info.get("lastPrice")
        issued_size = security_info.get("issuedSize")
        market_cap = (last_price * issued_size / 1e7) if (last_price and issued_size) else None  # ₹ crore

        source = SourceCitation(
            source="NSE India (nseindia.com/api/quote-equity)", source_url=f"{self.BASE_URL}/get-quotes/equity?symbol={symbol}",
            retrieved_date=datetime.now().date(),
        )

        return CompanyProfile(
            symbol=symbol,
            name=info.get("companyName", symbol),
            isin=info.get("isin"),
            sector=industry_info.get("macro") or industry_info.get("sector") or "Unknown",
            industry=industry_info.get("industry") or industry_info.get("sector") or "Unknown",
            sub_industry=industry_info.get("basicIndustry"),
            market_cap=market_cap,
            cap_category=_cap_category(market_cap),
            face_value=security_info.get("faceValue"),
            listing_date=self._parse_date(metadata.get("listingDate")),
            source=source,
        )

    async def get_shareholding_pattern(self, symbol: str) -> ShareholdingSnapshot:
        payload = await self._get_json("/api/corp-info", params={"symbol": symbol, "corpType": "shareholding_pattern"})
        rows = payload.get("data") or payload.get("shareholdingPatterns") or []
        latest = rows[0] if rows else {}

        source = SourceCitation(
            source="NSE India (nseindia.com/api/corp-info, shareholding_pattern)",
            source_url=f"{self.BASE_URL}/companies-listing/corporate-filings-shareholding-pattern?symbol={symbol}",
            retrieved_date=datetime.now().date(),
        )

        return ShareholdingSnapshot(
            as_of_date=self._parse_date(latest.get("date_end")) or datetime.now().date(),
            promoter_pct=float(latest.get("promoter", 0.0) or 0.0),
            promoter_pledge_pct=float(latest.get("promoterPledge", 0.0) or 0.0),
            fii_pct=self._maybe_float(latest.get("fii")),
            dii_pct=self._maybe_float(latest.get("dii")),
            public_pct=self._maybe_float(latest.get("public")),
            source=source,
        )

    async def get_corporate_announcements(self, symbol: str, limit: int = 20) -> List[CorporateAction]:
        payload = await self._get_json("/api/corporate-announcements", params={"index": "equities", "symbol": symbol})
        rows = payload if isinstance(payload, list) else payload.get("data", [])

        source = SourceCitation(
            source="NSE India (nseindia.com/api/corporate-announcements)",
            source_url=f"{self.BASE_URL}/companies-listing/corporate-filings-announcements?symbol={symbol}",
            retrieved_date=datetime.now().date(),
        )

        actions: List[CorporateAction] = []
        for row in rows[:limit]:
            announced = self._parse_date(row.get("an_dt") or row.get("attchmntDate"))
            if announced is None:
                continue
            actions.append(CorporateAction(
                action_type="ANNOUNCEMENT",
                announced_date=announced,
                headline=row.get("desc") or row.get("subject") or "NSE corporate announcement",
                description=row.get("attchmntText"),
                source=source,
            ))
        return actions

    @staticmethod
    def _parse_date(value: Optional[str]) -> Optional[date]:
        if not value:
            return None
        for fmt in ("%d-%b-%Y", "%d-%b-%Y %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
        return None

    @staticmethod
    def _maybe_float(value) -> Optional[float]:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None
