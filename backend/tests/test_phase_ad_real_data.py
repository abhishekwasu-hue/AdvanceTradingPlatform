"""Phase AD: broker option chains for the research pages (per-underlying success/failure, stub
brokers, metering) and the exchange-holiday list by year that the Admin console page uses."""
from app.brokers.exceptions import BrokerAPIError
from app.brokers.models import OptionChain, OptionChainRow
from app.market_data import candles_routes
from tests.test_admin_api import _admin
from tests.test_auth_api import client
from tests.test_phase_aa_market_data_api import _store_credentials
from tests.test_phase_k_commercial import _owner


class _ChainBroker:
    name = "fake"

    def __init__(self, implemented=True):
        self.implemented = implemented
        self.calls = []

    async def get_option_chain(self, underlying, expiry=None):
        self.calls.append((underlying, expiry))
        if not self.implemented:
            raise NotImplementedError("stub")
        if underlying == "BAD":
            raise BrokerAPIError("No option contracts for BAD")
        return OptionChain(underlying=underlying, expiry=str(expiry or "2026-10-30"), underlying_ltp=26050.0,
                           rows=[OptionChainRow(strike=26000, call_ltp=210.5, put_ltp=180.0, call_oi=5000, put_oi=7000)] if underlying != "EMPTY" else [])


def test_option_chains_per_underlying_and_metered(monkeypatch):
    headers, me = _owner("ad-chains@example.com")
    assert client.post("/api/market-data/option-chains", json={"underlyings": ["NIFTY"]}).status_code == 401
    assert client.post("/api/market-data/option-chains", headers=headers, json={"underlyings": ["NIFTY"]}).status_code == 409
    _store_credentials(me)
    broker = _ChainBroker()
    monkeypatch.setattr(candles_routes, "build_adapter", lambda record: broker)
    res = client.post("/api/market-data/option-chains", headers=headers, json={"underlyings": ["nifty", "BAD", "EMPTY", "NIFTY"], "expiry": "2026-11-27"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["source"]["broker"] == "upstox" and body["expiry"] == "2026-11-27" and set(body["symbols"]) == {"NIFTY", "BAD", "EMPTY"}
    nifty = body["symbols"]["NIFTY"]
    assert nifty["rows"] == 1 and nifty["chain"]["rows"][0]["call_ltp"] == 210.5 and nifty["chain"]["underlying_ltp"] == 26050.0 and nifty["chain"]["expiry"] == "2026-11-27"
    assert body["symbols"]["BAD"]["error"].startswith("BrokerAPIError") and body["symbols"]["BAD"]["chain"] is None
    assert any("EMPTY" in w for w in body["warnings"]) and broker.calls[0][1].isoformat() == "2026-11-27"
    usage = client.get("/api/billing/usage", headers=headers).json()
    assert usage["metrics"]["market_data_chains"] == 2                                   # NIFTY + EMPTY

    monkeypatch.setattr(candles_routes, "build_adapter", lambda record: _ChainBroker(implemented=False))
    body = client.post("/api/market-data/option-chains", headers=headers, json={"underlyings": ["NIFTY"]}).json()
    assert "no option-chain endpoint" in body["symbols"]["NIFTY"]["error"] and any("every underlying failed" in w for w in body["warnings"])


def test_holidays_listed_by_year_for_the_admin_page():
    admin, _ = _admin("ad-holidays@example.com")
    owner, _ = _owner("ad-holidays-owner@example.com")
    a = client.post("/api/market-holidays", headers=admin, json={"exchange": "NSE", "holiday_date": "2031-10-20", "description": "Diwali (test)"})
    b = client.post("/api/market-holidays", headers=admin, json={"exchange": "NSE", "holiday_date": "2032-01-26", "description": "Republic Day (test)"})
    assert a.status_code == 201 and b.status_code == 201
    listed = client.get("/api/market-holidays?year=2031", headers=owner).json()             # any user can read
    assert [h["holiday_date"] for h in listed] == ["2031-10-20"] and listed[0]["description"] == "Diwali (test)"
    assert client.delete(f"/api/market-holidays/{a.json()['id']}", headers=owner).status_code == 403
    assert client.delete(f"/api/market-holidays/{a.json()['id']}", headers=admin).status_code == 204
    assert client.delete(f"/api/market-holidays/{b.json()['id']}", headers=admin).status_code == 204
    assert client.get("/api/market-holidays?year=2031", headers=owner).json() == []
