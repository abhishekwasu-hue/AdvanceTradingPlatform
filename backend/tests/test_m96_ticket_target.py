"""M96 Q2 (owner "Default yes"): the order ticket's optional Target, empty by default, armed after the fill through the
existing resting-exit route (kind TARGET).

- no target sent = no target armed, and nothing in the preview names one (never a platform value);
- a target is a positive price on the profitable side of the entry; the wrong side is refused with a plain sentence;
- after a PLACE fill it is armed as a TARGET exit (a limit at his price) on the new position, once per ticket reference;
- an order that waits for its price carries the target and arms it when it fills;
- exits are unaffected: a position with an armed target still exits now (kill switch on), and the engine then cancels the
  target (ADR-0004)."""
import asyncio
import random
import uuid

import pytest
from sqlalchemy import select

from app.db.models import PaperRestingOrderRecord
from app.execution import resting_book, trigger_engine
from app.paper import fno_ticket
from tests.test_auth_api import _session_factory, client
from tests.test_fno1_chain_ticket import _body, _contract, _expiry, _org_tenant, _place, _preview, _price


def _run(coro):
    return asyncio.run(coro)


def _ref() -> str:
    return uuid.uuid4().hex[:16]


@pytest.fixture
def setup(monkeypatch):
    t = _org_tenant("m96t")
    expiry = _expiry()
    strike = float(random.randrange(30000, 40000, 50))
    symbol = _contract(expiry, strike)
    _price(monkeypatch, 100.0)
    return t, expiry, strike, symbol


def _exits_of(trade_id) -> list[PaperRestingOrderRecord]:
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(PaperRestingOrderRecord).where(
                PaperRestingOrderRecord.trade_id == trade_id, PaperRestingOrderRecord.purpose == resting_book.EXIT)))
    return _run(go())


def test_no_target_by_default_and_none_is_armed(setup):
    t, expiry, strike, _ = setup
    assert fno_ticket.OrderAsk(underlying="NIFTY", expiry=expiry, strike=strike, right="CE", side="BUY", lots=1).target is None
    view = _preview(t, _body(expiry, strike))
    assert view["target"] is None and "target" not in (view["confirm_label"] or "").lower()
    placed = _place(t, _body(expiry, strike, client_ref=_ref()))
    assert placed["status"] == "PLACED" and placed["target_order"] is None
    assert _exits_of(placed["trade_id"]) == []


def test_the_preview_names_the_target(setup):
    t, expiry, strike, _ = setup
    view = _preview(t, _body(expiry, strike, target=130.0))
    assert view["can_place"], view
    assert view["target"] == 130.0 and view["confirm_label"].endswith(", target 130.00")


@pytest.mark.parametrize("side,target,words", [
    ("BUY", 90.0, "must be above the price"),
    ("SELL", 110.0, "must be below the price"),
])
def test_a_wrong_side_target_is_refused_with_a_sentence(setup, side, target, words):
    t, expiry, strike, _ = setup
    view = _preview(t, _body(expiry, strike, side=side, target=target))
    assert not view["can_place"] and any(words in e and e.startswith("The target (") for e in view["errors"]), view["errors"]
    placed = _place(t, _body(expiry, strike, side=side, target=target, client_ref=_ref()))
    assert placed["status"] == "REFUSED" and placed["order_id"] is None


def test_a_target_off_the_tick_or_not_positive_is_refused(setup):
    t, expiry, strike, _ = setup
    view = _preview(t, _body(expiry, strike, target=130.03))
    assert any("Target 130.03 is not a multiple of the tick size" in e for e in view["errors"])
    bad = client.post("/api/paper/fno/order/preview", headers=t["headers"], json=_body(expiry, strike, target=0))
    assert bad.status_code == 422


def test_the_target_is_armed_after_the_fill_through_the_resting_exit_route(setup):
    t, expiry, strike, symbol = setup
    ref = _ref()
    placed = _place(t, _body(expiry, strike, target=130.0, stop_loss=70.0, client_ref=ref))
    assert placed["status"] == "PLACED", placed
    armed = placed["target_order"]
    assert armed["armed"] is True and armed["text"] == "Target armed at 130.00."
    row = armed["resting_order"]
    assert row["purpose"] == "EXIT" and row["kind"] == "TARGET" and row["side"] == "SELL" and row["trigger"]["side"] == "ABOVE"
    exits = _exits_of(placed["trade_id"])
    assert len(exits) == 1 and exits[0].order_type == "LIMIT" and exits[0].price == 130.0 and exits[0].mode == "PAPER"
    assert exits[0].client_ref == f"exit:target:{ref}"
    again = _place(t, _body(expiry, strike, target=130.0, stop_loss=70.0, client_ref=ref))     # a second tap
    assert again["order_id"] == placed["order_id"] and len(_exits_of(placed["trade_id"])) == 1
    # It is the same exit the route arms: the trigger engine closes the position at the target.
    async def go():
        async with _session_factory() as session:
            async def lookup(sym, exchange="NFO"):
                return 131.0
            return await trigger_engine.run_exits(session, t["tenant_id"], lookup)
    assert _run(go()).filled == 1
    assert client.get("/api/paper/fno/held", headers=t["headers"], params={"underlying": "NIFTY", "expiry": expiry.isoformat()}).json() == []


def test_a_waiting_order_arms_its_target_when_it_fills(setup):
    t, expiry, strike, symbol = setup
    out = _place(t, _body(expiry, strike, order_type="LIMIT", price=95.0, rest_if_waiting=True, trigger_mode="TOUCH",
                          target=120.0, client_ref=_ref()))
    assert out["status"] == "RESTING" and out["target"] == 120.0, out

    async def entries(price):
        async with _session_factory() as session:
            async def lookup(sym, exchange="NFO"):
                return price
            return await trigger_engine.run_entries(session, t["tenant_id"], lookup)
    assert _run(entries(94.0)).filled == 1
    async def trade_id_of(row_id):
        from app.db.models import OrderRecord
        async with _session_factory() as session:
            row = await session.get(PaperRestingOrderRecord, row_id)
            return (await session.get(OrderRecord, row.order_id)).trade_id
    trade_id = _run(trade_id_of(out["resting_order"]["id"]))
    exits = _exits_of(trade_id)
    assert [(e.kind, e.order_type, e.price, e.status) for e in exits] == [("TARGET", "LIMIT", 120.0, "RESTING")]


def test_exits_are_unaffected_by_an_armed_target(setup):
    t, expiry, strike, _ = setup
    placed = _place(t, _body(expiry, strike, target=130.0, client_ref=_ref()))
    assert placed["target_order"]["armed"] is True
    assert client.post("/api/kill-switch/tenant/engage", headers=t["headers"], json={"reason": "m96 exit"}).status_code == 200
    try:
        out = client.post(f"/api/positions/{placed['trade_id']}/exit-now", headers=t["headers"])
    finally:
        client.post("/api/kill-switch/tenant/disengage", headers=t["headers"])
    assert out.status_code == 200, out.text
    # The engine's next pass finds the position closed and cancels the armed target (the existing behaviour).
    async def go():
        async with _session_factory() as session:
            async def lookup(sym, exchange="NFO"):
                return 131.0
            return await trigger_engine.run_exits(session, t["tenant_id"], lookup)
    assert _run(go()).filled == 0
    assert [e.status for e in _exits_of(placed["trade_id"])] == ["CANCELLED"]


def test_a_target_that_cannot_be_kept_never_undoes_the_order(setup, monkeypatch):
    t, expiry, strike, _ = setup

    async def refuse(*args, **kwargs):
        raise resting_book.RestingOrderError("This position is already closed.")
    monkeypatch.setattr(resting_book, "arm_exit", refuse)
    placed = _place(t, _body(expiry, strike, target=130.0, client_ref=_ref()))
    assert placed["status"] == "PLACED" and placed["trade_id"]
    assert placed["target_order"] == {"armed": False, "text": "The target was not armed: This position is already closed. Arm it from the position."}
    held = client.get("/api/paper/fno/held", headers=t["headers"], params={"underlying": "NIFTY", "expiry": expiry.isoformat()}).json()
    assert len(held) == 1
