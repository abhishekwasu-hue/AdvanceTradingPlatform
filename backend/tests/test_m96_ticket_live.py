"""M96-Q1 (changed, owner): the F&O order ticket's LIVE path, on the same code path as PAPER.

- the mode is the user's own choice; not sent = PAPER (never LIVE by default);
- LIVE is refused - in plain words, and it never reaches a broker - while `live_trading` is off (or not granted to the
  organisation) and while the Phase 0 PAPER-only lock holds; the switches are only read here;
- with both switches open (in the test), a LIVE order reaches the user's own (mocked) broker exactly once, with his
  values (lots x lot size, product, Market), through the order chokepoint, with an order record; a double submit with the
  same ticket reference sends one order;
- a LIVE order is a Market order from the ticket (Limit / SL / SL-M and waiting orders are said plainly, not sent);
- the exit of a LIVE position is never blocked (lock on, kill switch on) and reaches the broker;
- PAPER is unchanged (test_fno1_chain_ticket stays green); only the ticket's own order route hands `place` a broker -
  the basket, Reverse, trigger plans and an approved proposal never can (tests/test_z10c_order_paths.py)."""
import asyncio
import random
import uuid

import pytest
from sqlalchemy import select

from app.brokers import token_lifecycle
from app.brokers.models import ORDER_INTENT_ENTRY
from app.core.enums import OrderSide
from app.db.models import BrokerCredentialRecord, OrderRecord, TradeRecord
from app.paper import fno_ticket
from app.platform import phase0_lock
from app.trading import position_monitor
from tests.test_auth_api import _session_factory, client
from tests.test_fno1_chain_ticket import LOT, _body, _contract, _expiry, _org_tenant, _place, _preview, _price
from tests.test_m55_s1_owner_live import _set_stored
from tests.test_trading_worker import _FakeBroker


def _run(coro):
    return asyncio.run(coro)


def _ref() -> str:
    return uuid.uuid4().hex[:16]


class _Spy(_FakeBroker):
    """The user's own broker (mocked): records every order; fills at its LTP."""

    def __init__(self):
        super().__init__(ltp=100.0)
        self.built = 0

    async def get_margins(self):
        return {"equity": {"available_margin": 10_000_000.0, "used_margin": 0.0}}


def _own_credential(t) -> None:
    """The organisation fixture links his own Upstox session (VALID); it is his, not a teammate's."""
    async def go():
        async with _session_factory() as session:
            rows = list(await session.scalars(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == t["tenant_id"])))
            assert [(r.user_id, r.token_status) for r in rows] == [(t["user_id"], "VALID")]
    _run(go())


@pytest.fixture
def setup(monkeypatch):
    t = _org_tenant("m96l")
    expiry = _expiry()
    strike = float(random.randrange(30000, 40000, 50))
    symbol = _contract(expiry, strike)
    _price(monkeypatch, 100.0)
    _own_credential(t)
    spy = _Spy()

    def build(record, client=None):
        spy.built += 1
        return spy
    monkeypatch.setattr(token_lifecycle, "build_adapter", build)
    monkeypatch.setattr(position_monitor, "build_adapter", build)
    phase0_lock.reset_cache()
    yield t, expiry, strike, symbol, spy
    _set_stored(None)
    phase0_lock.reset_cache()


def _switches(t, *, live_on: bool, granted: bool, lock: bool) -> None:
    _set_stored({"live_trading": {"on": live_on, "tenants": [t["tenant_id"]] if granted else []},
                 "phase0_paper_only": {"on": False, "tenants": [t["tenant_id"]] if lock else []}})


def _orders(tenant_id) -> list[OrderRecord]:
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(OrderRecord).where(OrderRecord.tenant_id == tenant_id)))
    return _run(go())


def _trade(trade_id) -> TradeRecord:
    async def go():
        async with _session_factory() as session:
            return await session.get(TradeRecord, trade_id)
    return _run(go())


def test_the_mode_is_paper_unless_he_picks_live(setup):
    from app.paper.fno_routes import OrderBody
    t, expiry, strike, _, spy = setup
    assert OrderBody.model_fields["mode"].default == "PAPER" and fno_ticket.OrderAsk.__dataclass_fields__["mode"].default == "PAPER"
    view = _preview(t, _body(expiry, strike))
    assert view["mode"] == "PAPER" and view["practice"] == fno_ticket.PRACTICE
    assert client.post("/api/paper/fno/order/preview", headers=t["headers"], json=_body(expiry, strike, mode="REAL")).status_code == 422
    assert spy.built == 0 and spy.placed == []


@pytest.mark.parametrize("live_on,granted,lock", [
    (False, False, False),      # the defaults: live_trading off
    (True, False, False),       # on, but not granted to this organisation
    (True, True, True),         # on and granted, but the Phase 0 lock holds for it
])
def test_live_is_refused_while_the_switches_say_so_and_never_reaches_a_broker(setup, live_on, granted, lock):
    t, expiry, strike, _, spy = setup
    _switches(t, live_on=live_on, granted=granted, lock=lock)
    view = _preview(t, _body(expiry, strike, mode="LIVE", client_ref=_ref()))
    assert view["mode"] == "LIVE" and not view["can_place"] and fno_ticket.LIVE_OFF in view["refusals"]
    placed = _place(t, _body(expiry, strike, mode="LIVE", client_ref=_ref()))
    assert placed["status"] == "REFUSED" and placed["order_id"] is None and fno_ticket.LIVE_OFF in placed["reasons"]
    assert spy.built == 0 and spy.placed == []
    assert _orders(t["tenant_id"]) == []


def test_at_place_the_lock_assertion_still_refuses_live(setup, monkeypatch):
    """Belt and braces: even if the preview's read said open, the chokepoint assertion refuses LIVE under the lock."""
    t, expiry, strike, _, spy = setup
    _switches(t, live_on=True, granted=True, lock=False)

    async def open_status(session, user):
        return fno_ticket.LiveRoute(refusals=[], broker_name="upstox")

    async def open_broker(session, user):
        return fno_ticket.LiveRoute(refusals=[], broker=spy, account_id=None, broker_name="upstox")
    monkeypatch.setattr(fno_ticket, "live_status", open_status)
    monkeypatch.setattr(fno_ticket, "live_broker", open_broker)
    phase0_lock.remember(False, ())
    monkeypatch.setattr(phase0_lock, "is_on", lambda session, tenant_id: _locked())
    placed = _place(t, _body(expiry, strike, mode="LIVE", client_ref=_ref()))
    # Refused (the platform's entry blocks and, at Place, the lock assertion) - and nothing reached the broker.
    assert placed["status"] == "REFUSED" and placed["order_id"] is None and spy.placed == []
    assert any(r in (fno_ticket.LIVE_OFF, phase0_lock.ENTRY_REASON) for r in placed["reasons"]), placed["reasons"]


async def _locked():
    phase0_lock.remember(True, ())
    return True


def test_with_both_switches_open_live_reaches_his_broker_once_with_his_values(setup, monkeypatch):
    t, expiry, strike, symbol, spy = setup
    _price(monkeypatch, 60.0)                          # 2 lots fit his own risk per trade (the fixture's 10,000)
    spy.ltp = 60.0
    _switches(t, live_on=True, granted=True, lock=False)
    ref = _ref()
    body = _body(expiry, strike, mode="LIVE", lots=2, product="NRML", order_type="MARKET", client_ref=ref)
    view = _preview(t, body)
    assert view["can_place"], (view["errors"], view["refusals"])
    assert view["mode"] == "LIVE" and view["practice"] == fno_ticket.LIVE_TEXT and view["confirm_label"].startswith("Place LIVE order: Buy 2 lots")
    assert spy.placed == []                                         # a preview sends nothing
    placed = _place(t, body)
    assert placed["status"] == "PLACED", placed
    assert placed["mode"] == "LIVE"
    assert len(spy.placed) == 1                                     # one entry, and no broker stop he did not set
    order = spy.placed[0]
    assert order.symbol == symbol and order.quantity == 2 * LOT and order.transaction_type == OrderSide.BUY
    assert order.product == "NRML" and order.order_type in ("MARKET", "LIMIT") and order.intent == ORDER_INTENT_ENTRY
    rows = _orders(t["tenant_id"])
    assert len(rows) == 1 and rows[0].mode == "LIVE" and rows[0].idempotency_key == f"fno:{t['user_id']}:{ref}"
    trade = _trade(placed["trade_id"])
    assert trade.mode == "LIVE" and trade.quantity == 2 * LOT and trade.broker_account_id is not None and trade.sl_order_id is None
    # A double submit with the same ticket reference sends nothing more.
    again = _place(t, body)
    assert again["order_id"] == placed["order_id"] and len(spy.placed) == 1 and len(_orders(t["tenant_id"])) == 1


def test_his_own_stop_goes_to_the_broker_as_his_stop(setup):
    t, expiry, strike, _, spy = setup
    _switches(t, live_on=True, granted=True, lock=False)
    placed = _place(t, _body(expiry, strike, mode="LIVE", stop_loss=70.0, client_ref=_ref()))
    assert placed["status"] == "PLACED", placed
    stops = [o for o in spy.placed if o.intent != ORDER_INTENT_ENTRY]
    assert len(spy.placed) == 2 and len(stops) == 1 and stops[0].trigger_price == 70.0


@pytest.mark.parametrize("extra,words", [
    ({"order_type": "LIMIT", "price": 101.0}, fno_ticket.LIVE_MARKET_ONLY),
    ({"order_type": "LIMIT", "price": 95.0, "rest_if_waiting": True, "trigger_mode": "TOUCH"}, fno_ticket.LIVE_NO_WAIT),
    ({"client_ref": None}, fno_ticket.LIVE_NO_REF),
])
def test_what_the_ticket_cannot_send_live_is_said_plainly(setup, extra, words):
    t, expiry, strike, _, spy = setup
    _switches(t, live_on=True, granted=True, lock=False)
    body = {**_body(expiry, strike, mode="LIVE", client_ref=_ref()), **extra}
    view = _preview(t, body)
    assert not view["can_place"] and words in view["errors"]
    assert _place(t, body)["status"] == "REFUSED" and spy.placed == []


def test_without_his_own_broker_session_live_is_refused(setup):
    t, expiry, strike, _, spy = setup
    _switches(t, live_on=True, granted=True, lock=False)

    async def expire():
        async with _session_factory() as session:
            for row in await session.scalars(select(BrokerCredentialRecord).where(BrokerCredentialRecord.user_id == t["user_id"])):
                row.token_status = "EXPIRED"
            await session.commit()
    _run(expire())
    view = _preview(t, _body(expiry, strike, mode="LIVE", client_ref=_ref()))
    assert fno_ticket.LIVE_NO_BROKER in view["refusals"] and not view["can_place"]
    assert _place(t, _body(expiry, strike, mode="LIVE", client_ref=_ref()))["status"] == "REFUSED" and spy.placed == []


def test_the_kill_switch_refuses_a_live_entry(setup):
    t, expiry, strike, _, spy = setup
    _switches(t, live_on=True, granted=True, lock=False)
    assert client.post("/api/kill-switch/tenant/engage", headers=t["headers"], json={"reason": "m96 live"}).status_code == 200
    try:
        placed = _place(t, _body(expiry, strike, mode="LIVE", client_ref=_ref()))
    finally:
        client.post("/api/kill-switch/tenant/disengage", headers=t["headers"])
    assert placed["status"] == "REFUSED" and spy.placed == []


def test_a_live_exit_is_never_blocked(setup):
    t, expiry, strike, _, spy = setup
    _switches(t, live_on=True, granted=True, lock=False)
    placed = _place(t, _body(expiry, strike, mode="LIVE", lots=1, client_ref=_ref()))
    assert placed["status"] == "PLACED", placed
    # The owner turns LIVE off and the lock on, and the kill switch is engaged: the exit still goes to his broker.
    _switches(t, live_on=False, granted=False, lock=True)
    assert client.post("/api/kill-switch/tenant/engage", headers=t["headers"], json={"reason": "m96 exit"}).status_code == 200
    try:
        out = client.post(f"/api/paper/fno/positions/{placed['trade_id']}/exit-part", headers=t["headers"],
                          json={"lots": 1, "client_ref": _ref(), "confirm": True})
    finally:
        client.post("/api/kill-switch/tenant/disengage", headers=t["headers"])
    assert out.status_code == 200, out.text
    exits = [o for o in spy.placed if o.intent != ORDER_INTENT_ENTRY]
    assert len(exits) == 1 and exits[0].transaction_type == OrderSide.SELL and exits[0].quantity == LOT
    assert _trade(placed["trade_id"]).exit_time is not None


def test_no_other_caller_of_place_can_send_live(setup):
    """`place` never builds a broker: an ask with mode LIVE and no broker handed in is refused, nothing is sent."""
    t, expiry, strike, _, spy = setup
    _switches(t, live_on=True, granted=True, lock=False)

    async def go():
        from app.db.models import User
        async with _session_factory() as session:
            user = await session.get(User, t["user_id"])
            ask = fno_ticket.OrderAsk(underlying="NIFTY", expiry=expiry, strike=strike, right="CE", side="BUY", lots=1, order_type="MARKET",
                                      product="MIS", client_ref=_ref(), mode="LIVE")

            async def lookup(symbol, exchange=""):
                return 100.0
            return await fno_ticket.place(session, user, ask, price_lookup=lookup)
    out = _run(go())
    assert out["status"] == "REFUSED" and out["reasons"] == [fno_ticket.LIVE_NO_BROKER] and spy.placed == [] and spy.built == 0


def test_the_live_status_says_why_not_and_reads_only(setup):
    t, _, _, _, spy = setup
    out = client.get("/api/paper/fno/live-status", headers=t["headers"])
    assert out.status_code == 200 and out.json() == {"open": False, "text": fno_ticket.LIVE_OFF}
    _switches(t, live_on=True, granted=True, lock=False)
    assert client.get("/api/paper/fno/live-status", headers=t["headers"]).json() == {"open": True, "text": None}
    assert spy.built == 0 and spy.placed == []
    assert client.get("/api/paper/fno/live-status").status_code == 401
