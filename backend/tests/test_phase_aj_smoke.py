"""Phase AJ: the read-only broker smoke test."""
import asyncio
from datetime import date

from app.brokers import routes as broker_routes
from app.brokers.contract_symbols import wrap_contract_symbols
from app.brokers.exceptions import BrokerAuthenticationError
from app.brokers.smoke import run_smoke
from tests.test_auth_api import client
from tests.test_deployments_api import _auth, _store_broker
from tests.test_phase_ai_contract_symbols import _KiteLike

TODAY = date(2026, 10, 1)


class _Calls(_KiteLike):
    """Records every method touched so the test can prove nothing was placed, modified or cancelled."""
    def __init__(self):
        super().__init__()
        self.touched = []
    async def get_profile(self):
        self.touched.append("get_profile"); return await super().get_profile()
    async def place_order(self, order):
        self.touched.append("place_order"); return await super().place_order(order)
    async def modify_order(self, *a, **k):
        self.touched.append("modify_order"); return await super().modify_order(*a, **k)
    async def cancel_order(self, order_id):
        self.touched.append("cancel_order"); return await super().cancel_order(order_id)


def test_run_smoke_reports_every_probe_and_places_nothing():
    inner = _Calls()
    report = asyncio.run(run_smoke(wrap_contract_symbols(inner), account_label="primary", today=TODAY))
    by_name = {s.name: s for s in report.steps}
    assert [s.name for s in report.steps] == ["profile", "funds", "instruments", "quote", "derivatives", "contract_quote", "option_chain", "positions", "orders"]
    assert report.ok and by_name["profile"].status == "ok" and "zerodha account u" in by_name["profile"].detail
    assert "available cash 100,000.00" in by_name["funds"].detail
    assert by_name["instruments"].detail == "1 NSE instruments"
    assert by_name["quote"].status == "ok" and by_name["quote"].detail.startswith("NIFTY 50 ")
    assert "nearest NIFTY expiry 2026-10-30" in by_name["derivatives"].detail and "NIFTY 26000 CE 30 OCT 26" in by_name["derivatives"].detail
    assert by_name["contract_quote"].status == "ok" and "via the broker's own symbol" in by_name["contract_quote"].detail
    assert by_name["option_chain"].status == "skip" and "not offered" in by_name["option_chain"].detail      # the double has no chain endpoint
    assert inner.ltp_calls[-1] == ["NFO:NIFTY26OCT26000CE"]                     # the worker's translation path, on Kite's spelling
    assert by_name["positions"].detail == "2 open position(s): NIFTY 26000 CE 30 OCT 26 75, RELIANCE 1"     # restored to the platform spelling
    assert by_name["orders"].detail == "2 order(s) in today's book"
    assert "place_order" not in inner.touched and "modify_order" not in inner.touched and "cancel_order" not in inner.touched
    d = report.as_dict()
    assert d["read_only"] is True and d["ok"] is True and d["summary"].startswith("8 ok, 0 failed, 1 skipped")


def test_run_smoke_isolates_failures_and_skips_the_contract_probe():
    class _Rejecting(_KiteLike):
        async def get_profile(self):
            raise BrokerAuthenticationError("token expired")
        async def get_instruments(self, exchange=None):
            if exchange == "NFO":
                raise TimeoutError("scrip master unreachable")
            return await super().get_instruments(exchange)

    report = asyncio.run(run_smoke(wrap_contract_symbols(_Rejecting()), today=TODAY))
    by_name = {s.name: s for s in report.steps}
    assert not report.ok
    assert by_name["profile"].status == "fail" and "BrokerAuthenticationError" in by_name["profile"].detail and "token expired" in by_name["profile"].detail
    assert by_name["funds"].status == "ok" and by_name["quote"].status == "ok"                  # one failure never stops the next probe
    assert by_name["derivatives"].status == "fail" and "scrip master unreachable" in by_name["derivatives"].detail
    assert by_name["contract_quote"].status == "skip"
    assert "failed: profile, derivatives" in report.summary

    class _Slow(_KiteLike):
        async def get_balance(self):
            await asyncio.sleep(0.2)
            return await super().get_balance()
    slow = asyncio.run(run_smoke(_Slow(), today=TODAY, timeout=0.05))
    assert {s.name: s for s in slow.steps}["funds"].detail.startswith("no answer within")


def test_smoke_test_route(monkeypatch):
    headers = _auth("smoke-owner@example.com")
    assert client.post("/api/broker/zerodha/smoke-test", headers=headers).status_code == 404
    assert client.post("/api/broker/nosuchbroker/smoke-test", headers=headers).status_code in (400, 404)
    _store_broker(headers, "zerodha", "VALID")
    fake = _KiteLike()
    monkeypatch.setattr(broker_routes, "build_adapter", lambda record, client=None: wrap_contract_symbols(fake))

    res = client.post("/api/broker/zerodha/smoke-test", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["broker"] == "zerodha" and body["account_label"] == "primary" and body["ok"] is True and body["read_only"] is True
    assert [s["name"] for s in body["steps"]][:2] == ["profile", "funds"] and all(s["status"] in ("ok", "skip") for s in body["steps"])
    assert not fake.orders                                                                     # nothing placed

    audit = client.get("/api/audit/logs", headers=headers)
    if audit.status_code == 200:
        assert any(e.get("action") == "broker_smoke_test" for e in (audit.json() if isinstance(audit.json(), list) else audit.json().get("items", [])))
