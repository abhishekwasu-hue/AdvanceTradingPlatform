"""Owner 14 Nov "Ho": GET /api/me/broker-book - "From your broker", read-only.

- 404 while `ui_v2` or `broker_book` is off; 401 without a sign-in;
- the caller's OWN broker sessions only (never a teammate's, never another organisation's);
- positions, holdings and today's orders as normalised rows; `parts` reads only the lists named;
- a broker error / static-IP refusal is a plain sentence per list, never an exception;
- only get_positions / get_holdings / get_order_book are ever called (no order, modify or cancel);
- the answer is kept briefly: a second request inside the window makes no broker call.
"""
from datetime import UTC, datetime

import pytest

from app.brokers import rate_budget
from app.brokers.exceptions import BrokerAPIError, BrokerAuthenticationError
from app.brokers.models import BrokerHolding, BrokerOrderStatus, BrokerPosition
from app.core.enums import OrderSide
from app.db.models import BrokerCredentialRecord
from app.me import broker_book
from tests.test_auth_api import _register, _session_factory, client
from tests.test_settings_a_connect_ai import _flags, _run

READS = {"get_positions", "get_holdings", "get_order_book"}


class FakeAdapter:
    """A broker adapter that records every method called on it."""

    def __init__(self, name="zerodha", fail=None):
        self.name = name
        self.calls = []
        self.fail = fail or {}

    async def _write(self, method):
        self.calls.append(method)
        raise AssertionError(f"{method} must never be called")

    async def place_order(self, *a, **k): return await self._write("place_order")
    async def modify_order(self, *a, **k): return await self._write("modify_order")
    async def cancel_order(self, *a, **k): return await self._write("cancel_order")
    async def place_gtt(self, *a, **k): return await self._write("place_gtt")

    async def _answer(self, method, value):
        self.calls.append(method)
        if method in self.fail:
            raise self.fail[method]
        return value

    async def get_positions(self):
        return await self._answer("get_positions", [
            BrokerPosition(symbol="TCS", exchange="NSE", product="CNC", quantity=0, average_price=3500, ltp=3510, pnl=20),
            BrokerPosition(symbol="NIFTY26NOVFUT", exchange="NFO", product="NRML", quantity=-75, average_price=24000.456, ltp=23900, pnl=7534.2),
        ])

    async def get_holdings(self):
        return await self._answer("get_holdings", [BrokerHolding(symbol="INFY", quantity=10, average_price=1500, ltp=1550, pnl=500)])

    async def get_order_book(self):
        return await self._answer("get_order_book", [
            BrokerOrderStatus(order_id="1", symbol="INFY", transaction_type=OrderSide.BUY, quantity=10, filled_quantity=10, order_type="MARKET",
                              status="complete", average_price=1500, placed_at=datetime(2026, 11, 14, 4, 0, tzinfo=UTC), product="CNC", exchange="NSE"),
            BrokerOrderStatus(order_id="2", symbol="TCS", transaction_type=OrderSide.SELL, quantity=5, order_type="LIMIT", status="OPEN", price=3600,
                              placed_at=datetime(2026, 11, 14, 5, 0, tzinfo=UTC)),
        ])


def _me(email):
    headers = {"Authorization": f"Bearer {_register(email)}"}
    me = client.get("/api/auth/me", headers=headers).json()
    return {"headers": headers, "tenant_id": me["tenant_id"], "user_id": me["id"]}


def _link(tenant_id, user_id, broker, label="primary", status="VALID"):
    async def go():
        async with _session_factory() as session:
            row = BrokerCredentialRecord(tenant_id=tenant_id, user_id=user_id, broker_name=broker, account_label=label,
                                         encrypted_payload="x", token_status=status)
            session.add(row)
            await session.commit()
            return row.id
    return _run(go())


@pytest.fixture
def book(monkeypatch):
    adapters = {}

    def build(record):
        return adapters.setdefault(record.id, FakeAdapter(record.broker_name, fail=FAILS.get(record.broker_name)))
    FAILS: dict = {}
    monkeypatch.setattr(broker_book, "build_adapter", build)
    monkeypatch.setattr(rate_budget.config, "BROKER_429_DATA_MAX_WAIT_SECONDS", 0)   # the budget's 429 wait, not slept here
    broker_book._CACHE.clear()
    broker_book._BUDGETS.clear()
    _flags(ui_v2=True, broker_book=True)
    yield adapters, FAILS
    _flags(ui_v2=False, broker_book=False)
    broker_book._CACHE.clear()


def test_flagged_and_signed_in_only():
    a = _me("bb-flags@example.com")
    _flags(ui_v2=True, broker_book=False)
    try:
        r = client.get("/api/me/broker-book", headers=a["headers"])
        assert r.status_code == 404 and r.headers["X-Feature-Disabled"] == "broker_book"
        _flags(ui_v2=False, broker_book=True)
        assert client.get("/api/me/broker-book", headers=a["headers"]).status_code == 404
        _flags(ui_v2=True, broker_book=True)
        assert client.get("/api/me/broker-book", headers=a["headers"]).json() == {"parts": ["positions", "holdings", "orders"], "accounts": []}
        assert client.get("/api/me/broker-book").status_code == 401
        assert client.get("/api/me/broker-book?parts=trades", headers=a["headers"]).status_code == 422
    finally:
        _flags(ui_v2=False, broker_book=False)


def test_rows_are_normalised_and_only_reads_are_called(book):
    adapters, _ = book
    a = _me("bb-rows@example.com")
    cid = _link(a["tenant_id"], a["user_id"], "zerodha")
    body = client.get("/api/me/broker-book", headers=a["headers"]).json()
    (acct,) = body["accounts"]
    assert (acct["broker"], acct["broker_label"], acct["account_label"]) == ("zerodha", "Zerodha (Kite)", None)
    pos = acct["positions"]["rows"]
    assert acct["positions"]["message"] is None
    assert [p["symbol"] for p in pos] == ["NIFTY26NOVFUT", "TCS"]          # open first, closed last
    assert pos[0]["side"] == "Short" and pos[0]["quantity"] == -75 and pos[0]["average_price"] == 24000.46 and pos[1]["side"] == "Closed"
    (h,) = acct["holdings"]["rows"]
    assert (h["symbol"], h["quantity"], h["value"], h["pnl"]) == ("INFY", 10, 15500.0, 500.0)
    orders = acct["orders"]["rows"]
    assert [o["symbol"] for o in orders] == ["TCS", "INFY"]                 # newest first
    assert orders[0]["side"] == "Sell" and orders[0]["status"] == "OPEN" and orders[1]["status"] == "COMPLETE"
    assert orders[1]["placed_at"] == "2026-11-14T04:00:00+00:00"
    assert "order_id" not in orders[0]
    assert set(adapters[cid].calls) == READS                                  # nothing but the three reads


def test_parts_and_the_short_cache(book):
    adapters, _ = book
    a = _me("bb-cache@example.com")
    cid = _link(a["tenant_id"], a["user_id"], "dhan")
    body = client.get("/api/me/broker-book?parts=holdings", headers=a["headers"]).json()
    assert body["parts"] == ["holdings"] and "positions" not in body["accounts"][0]
    assert adapters[cid].calls == ["get_holdings"]
    client.get("/api/me/broker-book?parts=holdings", headers=a["headers"])
    assert adapters[cid].calls == ["get_holdings"]                            # kept: no second broker call
    client.get("/api/me/broker-book?parts=holdings&refresh=true", headers=a["headers"])
    assert adapters[cid].calls == ["get_holdings"]                            # refresh inside REFRESH_MIN_SECONDS: still kept
    real = broker_book._clock
    try:
        broker_book._clock = lambda: real() + broker_book.REFRESH_MIN_SECONDS + 1
        client.get("/api/me/broker-book?parts=holdings&refresh=true", headers=a["headers"])
        assert adapters[cid].calls == ["get_holdings", "get_holdings"]
    finally:
        broker_book._clock = real


def test_refusals_are_plain_sentences(book):
    adapters, fails = book
    fails["upstox"] = {"get_positions": BrokerAPIError("Forbidden", 403), "get_holdings": BrokerAPIError("UDAPI100074: Static IP not registered", 400),
                       "get_order_book": BrokerAuthenticationError("token invalid")}
    fails["fyers"] = {"get_positions": BrokerAPIError("Too many requests", 429), "get_holdings": RuntimeError("boom"),
                      "get_order_book": NotImplementedError()}
    a = _me("bb-refusal@example.com")
    _link(a["tenant_id"], a["user_id"], "upstox")
    _link(a["tenant_id"], a["user_id"], "fyers")
    r = client.get("/api/me/broker-book", headers=a["headers"])
    assert r.status_code == 200
    by = {x["broker"]: x for x in r.json()["accounts"]}
    up, fy = by["upstox"], by["fyers"]
    assert up["positions"] == {"rows": [], "message": "Upstox needs your static IP for this list. Register ATP's static IP with Upstox (steps on the Broker page)."}
    assert up["holdings"]["message"].startswith("Upstox needs your static IP")
    assert up["orders"]["message"] == "Your Upstox session has ended. Sign in to Upstox again to see this list."
    assert fy["positions"]["message"] == "Fyers is busy right now. Try again in a minute."
    assert fy["holdings"]["message"] == "Fyers did not send your holdings just now. Try again in a minute."
    assert fy["orders"]["message"] == "Fyers does not offer its order list to ATP."
    for x in (up, fy):
        for part in broker_book.PARTS:
            assert "boom" not in x[part]["message"] and "UDAPI" not in x[part]["message"]


def test_a_403_elsewhere_is_not_called_a_static_ip_refusal():
    msg = broker_book.refusal_message("zerodha", "positions", BrokerAPIError("Forbidden", 403))
    assert "static IP" not in msg
    assert "static IP" in broker_book.refusal_message("shoonya", "orders", BrokerAPIError("Forbidden", 403))


def test_his_own_sessions_only(book):
    adapters, _ = book
    a, b = _me("bb-own-a@example.com"), _me("bb-own-b@example.com")
    _link(a["tenant_id"], a["user_id"], "zerodha")
    _link(b["tenant_id"], b["user_id"], "angelone")                          # another organisation
    _link(a["tenant_id"], b["user_id"], "kotak_neo")                         # another user's row in a's organisation
    expired = _link(a["tenant_id"], a["user_id"], "dhan", status="EXPIRED")
    body = client.get("/api/me/broker-book", headers=a["headers"]).json()
    assert sorted(x["broker"] for x in body["accounts"]) == ["dhan", "zerodha"]
    dhan = next(x for x in body["accounts"] if x["broker"] == "dhan")
    assert dhan["positions"] == {"rows": [], "message": "Sign in to Dhan to see this list."}
    assert expired not in adapters                                            # an expired session builds nothing
    body_b = client.get("/api/me/broker-book", headers=b["headers"]).json()
    assert [x["broker"] for x in body_b["accounts"]] == ["angelone"]


def test_no_write_route():
    paths = [(r.path, sorted(r.methods)) for r in broker_book.router.routes]
    assert paths == [("/api/me/broker-book", ["GET"])]
