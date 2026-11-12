"""LIVE-2 (owner order LIVE-1, binding: `live_trading` is the one switch): the F&O basket and Reverse get LIVE on the
ticket's own code path.

- the mode is the user's own choice per action; not sent = PAPER (never LIVE by default);
- switch OFF: LIVE is refused with the switch's own sentence (`live_switch.LIVE_OFF`) and nothing reaches the broker
  (a spy: never built, nothing placed, no order record);
- switch ON: a LIVE basket reaches his own (spy) broker once per leg, hedge legs first, with the leg's idempotency key;
  a replay of the same basket places nothing twice;
- a LIVE leg that does not fill stops every later entry leg (reported "not placed"); what filled stays;
- Reverse with the switch OFF still exits (never blocked) and refuses the new LIVE side; with it ON the new side goes to
  his broker once, and a replay of the same reference places nothing twice.
"""
import asyncio
import random
import uuid

import pytest
from sqlalchemy import select

from app.ai import worker_bridge
from app.brokers import token_lifecycle
from app.brokers.models import ORDER_INTENT_ENTRY
from app.core.enums import OrderSide
from app.db.models import OrderRecord, TradeRecord
from app.paper import fno_ticket
from app.platform import live_switch, phase0_lock
from app.trading import position_monitor
from tests.test_auth_api import _session_factory, client
from tests.test_fno1_chain_ticket import LOT, _contract, _expiry, _org_tenant
from tests.test_m55_s1_owner_live import _set_stored
from tests.test_m96_ticket_live import _Spy


def _run(coro):
    return asyncio.run(coro)


def _ref() -> str:
    return uuid.uuid4().hex[:16]


@pytest.fixture
def desk(monkeypatch):
    """His own organisation (one signed-in broker of his own: the spy), a bull call spread's two contracts."""
    t = _org_tenant("live2")
    expiry = _expiry()
    base = float(random.randrange(30000, 40000, 50))
    hedge, short = _contract(expiry, base + 100, "CE"), _contract(expiry, base, "CE")
    table = {hedge: 40.0, short: 60.0}

    async def factory(session, who):
        async def lookup(symbol, exchange="NSE"):
            return float(table.get(symbol, 60.0))
        return lookup
    monkeypatch.setattr(worker_bridge, "price_lookup_for_user", factory)
    spy = _Spy()
    spy.ltp = 50.0

    def build(record, client=None):
        spy.built += 1
        return spy
    monkeypatch.setattr(token_lifecycle, "build_adapter", build)
    monkeypatch.setattr(position_monitor, "build_adapter", build)
    phase0_lock.reset_cache()
    yield t, expiry, base, hedge, short, spy
    _set_stored(None)
    phase0_lock.reset_cache()


def _switch(on: bool) -> None:
    _set_stored({"live_trading": {"on": on, "tenants": []}, "phase0_paper_only": {"on": False, "tenants": []}})


def _legs(expiry, base) -> list:
    """His own order of entry - the sold leg first; the basket still places the hedge first."""
    return [{"underlying": "NIFTY", "expiry": expiry.isoformat(), "strike": base, "right": "CE", "side": "SELL", "lots": 1, "price": 60.0,
             "stop_loss": 120.0},
            {"underlying": "NIFTY", "expiry": expiry.isoformat(), "strike": base + 100, "right": "CE", "side": "BUY", "lots": 1, "price": 40.0}]


def _post(t, path, body) -> dict:
    out = client.post(f"/api/paper/fno/{path}", headers=t["headers"], json=body)
    assert out.status_code == 200, out.text
    return out.json()


def _orders(tenant_id) -> list[OrderRecord]:
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(OrderRecord).where(OrderRecord.tenant_id == tenant_id).order_by(OrderRecord.id)))
    return _run(go())


def _trade(trade_id) -> TradeRecord:
    async def go():
        async with _session_factory() as session:
            return await session.get(TradeRecord, trade_id)
    return _run(go())


def _entries(spy) -> list:
    return [o for o in spy.placed if o.intent == ORDER_INTENT_ENTRY]


def test_the_mode_is_paper_unless_he_picks_live():
    from app.paper.fno_routes import ExecuteBody, PreviewBody, ReverseBody
    assert ExecuteBody.model_fields["mode"].default == "PAPER"
    assert PreviewBody.model_fields["mode"].default == "PAPER"
    assert ReverseBody.model_fields["mode"].default == "PAPER"


def test_switch_off_refuses_a_live_basket_and_nothing_reaches_the_broker(desk):
    t, expiry, base, _, _, spy = desk
    _switch(False)
    view = _post(t, "basket/preview", {"legs": _legs(expiry, base), "product": "MIS", "mode": "LIVE"})
    assert view["mode"] == "LIVE" and not view["can_execute"] and live_switch.LIVE_OFF in view["refusals"]
    out = _post(t, "basket/execute", {"legs": _legs(expiry, base), "product": "MIS", "mode": "LIVE", "basket_ref": _ref()})
    assert out["status"] == "REFUSED" and out["placed"] == 0 and out["reasons"] == [live_switch.LIVE_OFF]
    assert all(r["status"] == "NOT_PLACED" for r in out["legs"])
    assert spy.built == 0 and spy.placed == [] and _orders(t["tenant_id"]) == []


def test_switch_on_a_live_basket_reaches_his_broker_once_per_leg_hedge_first(desk):
    t, expiry, base, hedge, short, spy = desk
    _switch(True)
    ref = _ref()
    body = {"legs": _legs(expiry, base), "product": "NRML", "mode": "LIVE", "basket_ref": ref}
    view = _post(t, "basket/preview", {k: v for k, v in body.items() if k != "basket_ref"})
    assert view["can_execute"], (view["refusals"], [(r["errors"], r["refusals"]) for r in view["legs"]])
    assert view["mode"] == "LIVE" and view["practice"] == fno_ticket.LIVE_TEXT and view["confirm_label"] == "Place 2 LIVE orders, hedge legs first"
    assert spy.placed == []                                                     # the confirm step sends nothing
    out = _post(t, "basket/execute", body)
    assert out["status"] == "PLACED" and out["placed"] == 2 and out["mode"] == "LIVE", out
    entries = _entries(spy)
    assert [o.symbol for o in entries] == [hedge, short]                       # the hedge first
    assert [o.transaction_type for o in entries] == [OrderSide.BUY, OrderSide.SELL] and all(o.quantity == LOT for o in entries)
    rows = _orders(t["tenant_id"])
    assert [r.mode for r in rows] == ["LIVE", "LIVE"]
    assert [r.idempotency_key for r in rows] == [f"fno:{t['user_id']}:{ref}-1", f"fno:{t['user_id']}:{ref}-2"]
    assert all(_trade(r["trade_id"]).mode == "LIVE" for r in out["legs"])
    # The same basket again (a double tap): nothing more reaches the broker, no new order record.
    _post(t, "basket/execute", body)
    assert len(_entries(spy)) == 2 and len(_orders(t["tenant_id"])) == 2


def test_a_live_leg_that_does_not_fill_stops_the_remaining_entries(desk, monkeypatch):
    t, expiry, base, hedge, _, spy = desk
    _switch(True)
    real = fno_ticket.place
    calls = []

    async def first_short_fails(session, user, ask, **kw):
        calls.append((ask.side, ask.strike, ask.mode))
        if len(calls) == 2:                                                     # the broker said no to the first sold leg
            return {"status": "FAILED", "reasons": ["Not placed: TimeoutError"], "label": "x", "order_id": None, "trade_id": None, "filled": None}
        return await real(session, user, ask, **kw)
    monkeypatch.setattr(fno_ticket, "place", first_short_fails)
    _contract(expiry, base + 200, "CE")
    legs = _legs(expiry, base) + [{"underlying": "NIFTY", "expiry": expiry.isoformat(), "strike": base + 200, "right": "CE", "side": "SELL",
                                   "lots": 1, "price": 40.0, "stop_loss": 120.0}]
    out = _post(t, "basket/execute", {"legs": legs, "product": "MIS", "mode": "LIVE", "basket_ref": _ref()})
    assert out["status"] == "PARTIAL" and out["placed"] == 1, out
    assert [r["status"] for r in out["legs"]] == ["PLACED", "FAILED", "NOT_PLACED"]
    # PAPER would still send the second sold leg (its hedge filled); LIVE stops at the first leg that did not fill.
    assert calls == [("BUY", base + 100, "LIVE"), ("SELL", base, "LIVE")]
    assert out["legs"][2]["reasons"] == ["Not placed: an earlier leg did not fill, so nothing more was sent."]
    assert [o.symbol for o in _entries(spy)] == [hedge]                         # what filled stays; nothing else went
    assert "at your broker (LIVE)" in out["next_steps"][0]


def _paper_position(t, expiry, base) -> int:
    out = _post(t, "order", {"underlying": "NIFTY", "expiry": expiry.isoformat(), "strike": base + 100, "right": "CE", "side": "BUY",
                             "lots": 1, "order_type": "MARKET", "product": "MIS", "client_ref": _ref()})
    assert out["status"] == "PLACED", out
    return int(out["trade_id"])


def test_reverse_with_the_switch_off_still_exits_and_refuses_the_new_live_side(desk):
    t, expiry, base, _, _, spy = desk
    _switch(False)
    trade_id = _paper_position(t, expiry, base)
    ref = _ref()
    out = _post(t, f"positions/{trade_id}/reverse", {"client_ref": ref, "stop_loss": 80.0, "mode": "LIVE"})
    assert out["status"] == "EXITED" and out["mode"] == "LIVE", out
    assert out["entry"]["status"] == "REFUSED" and out["entry"]["reasons"] == [live_switch.LIVE_OFF]
    assert _trade(trade_id).exit_time is not None                               # the exit ran (never blocked)
    assert spy.built == 0 and spy.placed == []
    assert [o.mode for o in _orders(t["tenant_id"])] == ["PAPER"]               # only the PAPER position's own order
    again = _post(t, f"positions/{trade_id}/reverse", {"client_ref": ref, "stop_loss": 80.0, "mode": "LIVE"})
    assert again["entry"]["status"] == "REFUSED" and spy.placed == [] and len(_orders(t["tenant_id"])) == 1


def test_reverse_with_the_switch_on_sends_the_new_side_to_his_broker_once(desk):
    t, expiry, base, hedge, _, spy = desk
    _switch(True)
    trade_id = _paper_position(t, expiry, base)
    ref = _ref()
    out = _post(t, f"positions/{trade_id}/reverse", {"client_ref": ref, "stop_loss": 80.0, "mode": "LIVE"})
    assert out["status"] == "REVERSED" and out["entry"]["status"] == "PLACED", out
    entries = _entries(spy)
    assert len(entries) == 1 and entries[0].symbol == hedge and entries[0].transaction_type == OrderSide.SELL and entries[0].quantity == LOT
    assert _trade(out["entry"]["trade_id"]).mode == "LIVE" and _trade(trade_id).exit_time is not None
    again = _post(t, f"positions/{trade_id}/reverse", {"client_ref": ref, "stop_loss": 80.0, "mode": "LIVE"})
    assert again["repeat"] is True and again["status"] == "REVERSED"
    assert len(_entries(spy)) == 1 and len([o for o in _orders(t["tenant_id"]) if o.mode == "LIVE"]) == 1


def test_a_paper_reverse_never_gets_a_broker(desk):
    t, expiry, base, _, _, spy = desk
    _switch(True)
    trade_id = _paper_position(t, expiry, base)
    out = _post(t, f"positions/{trade_id}/reverse", {"client_ref": _ref(), "stop_loss": 80.0})
    assert out["status"] == "REVERSED" and out["mode"] == "PAPER", out
    assert _trade(out["entry"]["trade_id"]).mode == "PAPER" and spy.built == 0 and spy.placed == []


def test_with_smart_execution_a_live_basket_goes_the_same_way(desk):
    """Part 7 smart execution (its own flag): the same LIVE legs, hedge first, through his broker; the run stops at the
    first leg that does not fill (smart_execution.run)."""
    from app.execution import smart_execution as se
    from tests.test_z5_p1d_risk_by_default import _flag
    t, expiry, base, hedge, short, spy = desk
    _flag(se.FLAG, t["tenant_id"])
    _switch(True)
    out = _post(t, "basket/execute", {"legs": _legs(expiry, base), "product": "MIS", "mode": "LIVE", "basket_ref": _ref()})
    assert out["status"] == "PLACED" and out["mode"] == "LIVE" and out["placed"] == 2, out
    assert [o.symbol for o in _entries(spy)] == [hedge, short]
    assert [r.mode for r in _orders(t["tenant_id"])] == ["LIVE", "LIVE"]
