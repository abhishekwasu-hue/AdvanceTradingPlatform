"""TM-2 / TM-23 / TT-6: the resting PAPER order book and the ONE trigger engine.

- the ticket's "wait for my price" keeps a limit / stop the price has not reached (idempotent on its reference) instead
  of refusing it; without it the order is refused as before;
- the engine fires an entry when the price reaches it, through the ticket's own pipeline: the same fill as an order
  placed now, and every entry check again (a kill switch engaged meanwhile refuses it, with a notification);
- an armed exit closes the user's own position when reached - stops first, the other exit cancelled - and is never
  blocked by a kill switch (ADR-0004); a close trigger waits for a closed candle of his timeframe;
- a day order expires; the engine writes its heartbeat (worker-status shows it) and a stall tells the owners once;
- the tick pass fires on the data hub's live ticks; only the owner sees or cancels his waiting orders."""
import asyncio
import random
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.db.models import NotificationRecord, PaperRestingOrderRecord, TradeRecord, WorkerHeartbeatRecord
from app.execution import resting_book, trigger_engine
from app.market_data import hub
from tests.test_auth_api import _session_factory, client
from tests.test_fno1_chain_ticket import _body, _contract, _expiry, _org_tenant, _place, _price

IST = timezone(timedelta(hours=5, minutes=30))


def _run(coro):
    return asyncio.run(coro)


def _ref() -> str:
    return uuid.uuid4().hex[:16]


@pytest.fixture
def setup(monkeypatch):
    t = _org_tenant("tm2")
    expiry = _expiry()
    strike = float(random.randrange(30000, 40000, 50))
    symbol = _contract(expiry, strike)
    _price(monkeypatch, 100.0)
    return t, expiry, strike, symbol


def _prices(table):
    async def lookup(symbol, exchange="NFO"):
        if symbol not in table:
            raise LookupError(symbol)
        return float(table[symbol])
    return lookup


def _row(row_id) -> PaperRestingOrderRecord:
    async def go():
        async with _session_factory() as session:
            return await session.get(PaperRestingOrderRecord, row_id)
    return _run(go())


def _entries(tenant_id, table, **kw):
    async def go():
        async with _session_factory() as session:
            return await trigger_engine.run_entries(session, tenant_id, _prices(table), **kw)
    return _run(go())


def _exits(tenant_id, table, **kw):
    async def go():
        async with _session_factory() as session:
            return await trigger_engine.run_exits(session, tenant_id, _prices(table), **kw)
    return _run(go())


def _trade(trade_id) -> TradeRecord:
    async def go():
        async with _session_factory() as session:
            return await session.get(TradeRecord, trade_id)
    return _run(go())


def _rest(t, expiry, strike, **kw):
    out = _place(t, _body(expiry, strike, order_type="LIMIT", price=95.0, rest_if_waiting=True, client_ref=_ref(), **kw))
    assert out["status"] == "RESTING", out
    return out


def test_a_waiting_limit_rests_once_and_is_refused_without_the_option(setup):
    t, expiry, strike, symbol = setup
    refused = _place(t, _body(expiry, strike, order_type="LIMIT", price=95.0, client_ref=_ref()))
    assert refused["status"] == "REFUSED" and any("waiting order" in r for r in refused["reasons"])   # as before
    ref = _ref()
    body = _body(expiry, strike, order_type="LIMIT", price=95.0, rest_if_waiting=True, client_ref=ref)
    first = _place(t, body)
    assert first["status"] == "RESTING" and first["resting"] is True and first["rest_level"] == 95.0
    assert first["confirm_label"].startswith("Place waiting PAPER order")
    order = first["resting_order"]
    assert order["symbol"] == symbol and order["trigger"]["side"] == "BELOW" and order["status_words"] == "Waiting for your price"
    again = _place(t, body)
    assert again["resting_order"]["id"] == order["id"]                     # the same reference never rests twice
    listed = client.get("/api/paper/fno/resting", headers=t["headers"]).json()
    assert [r["id"] for r in listed if r["id"] == order["id"]] == [order["id"]]
    no_ref = client.post("/api/paper/fno/order/preview", headers=t["headers"],
                         json=_body(expiry, strike, order_type="LIMIT", price=95.0, rest_if_waiting=True)).json()
    assert not no_ref["can_place"] and any("reference" in e for e in no_ref["errors"])


def test_the_engine_fills_a_reached_entry_like_an_order_placed_now(setup):
    t, expiry, strike, symbol = setup
    row_id = _rest(t, expiry, strike)["resting_order"]["id"]
    assert _entries(t["tenant_id"], {symbol: 96.0}).filled == 0 and _row(row_id).status == "RESTING"
    assert _row(row_id).last_price == 96.0
    report = _entries(t["tenant_id"], {symbol: 94.0})
    assert report.filled == 1, report
    row = _row(row_id)
    assert row.status == "FILLED" and row.order_id and row.fired_at is not None
    assert [s["status"] for s in resting_book.trail(row)] == ["RESTING", "FILLED"]
    rested_trade = _run(_trade_of_order(row.order_id))
    assert rested_trade.entry_price == pytest.approx(row.fill_price) and rested_trade.user_id is not None
    assert _entries(t["tenant_id"], {symbol: 94.0}).filled == 0             # fired once only


async def _trade_of_order(order_id):
    from app.db.models import OrderRecord
    async with _session_factory() as session:
        order = await session.get(OrderRecord, order_id)
        return await session.get(TradeRecord, order.trade_id)


def test_parity_a_fired_entry_fills_as_an_order_placed_now_at_that_price(setup, monkeypatch):
    t, expiry, strike, symbol = setup
    row_id = _rest(t, expiry, strike)["resting_order"]["id"]
    _entries(t["tenant_id"], {symbol: 94.0})
    rested = _row(row_id)
    _price(monkeypatch, 94.0)
    now = _place(t, _body(expiry, strike, order_type="MARKET", client_ref=_ref()))
    assert now["status"] == "PLACED" and now["filled"]["price"] == pytest.approx(rested.fill_price)


def test_a_kill_switch_refuses_the_entry_at_fire_time_with_a_notification(setup):
    t, expiry, strike, symbol = setup
    row_id = _rest(t, expiry, strike)["resting_order"]["id"]
    assert client.post("/api/kill-switch/tenant/engage", headers=t["headers"], json={"reason": "tm2 test"}).status_code == 200
    try:
        report = _entries(t["tenant_id"], {symbol: 94.0})
    finally:
        client.post("/api/kill-switch/tenant/disengage", headers=t["headers"])
    row = _row(row_id)
    assert report.rejected == 1 and row.status == "REJECTED" and row.order_id is None and row.status_note

    async def notes():
        async with _session_factory() as session:
            return list(await session.scalars(select(NotificationRecord).where(NotificationRecord.tenant_id == t["tenant_id"],
                                                                               NotificationRecord.title.like("Waiting order not placed%"))))
    assert _run(notes())


def _open_trade(t, expiry, strike, symbol):
    out = _place(t, _body(expiry, strike, order_type="MARKET", client_ref=_ref()))
    assert out["status"] == "PLACED", out
    return out["trade_id"]


def _arm(t, trade_id, **kw):
    body = {"trade_id": trade_id, "client_ref": _ref(), **kw}
    out = client.post("/api/paper/fno/resting/exit", headers=t["headers"], json=body)
    assert out.status_code == 200, out.text
    return out.json()


def test_an_armed_stop_closes_the_position_first_and_is_never_blocked(setup):
    t, expiry, strike, symbol = setup
    trade_id = _open_trade(t, expiry, strike, symbol)
    stop = _arm(t, trade_id, kind="STOP", order_type="SL-M", level=80.0)
    target = _arm(t, trade_id, kind="TARGET", order_type="LIMIT", level=120.0)
    assert stop["side"] == "SELL" and stop["trigger"]["side"] == "BELOW" and target["trigger"]["side"] == "ABOVE"
    assert _exits(t["tenant_id"], {symbol: 90.0}).filled == 0
    assert client.post("/api/kill-switch/tenant/engage", headers=t["headers"], json={"reason": "tm2 exit"}).status_code == 200
    try:
        report = _exits(t["tenant_id"], {symbol: 79.0})
    finally:
        client.post("/api/kill-switch/tenant/disengage", headers=t["headers"])
    assert report.filled == 1
    trade = _trade(trade_id)
    assert trade.exit_time is not None and trade.exit_reason == "Stop (your waiting order)"
    assert trade.exit_price == pytest.approx(_row(stop["id"]).fill_price) and trade.exit_price <= 79.0   # the adapter's slippage
    assert _row(stop["id"]).status == "FILLED" and _row(target["id"]).status == "CANCELLED"


def test_a_spot_close_trigger_waits_for_a_closed_candle(setup):
    t, expiry, strike, symbol = setup
    trade_id = _open_trade(t, expiry, strike, symbol)

    async def underlying():
        async with _session_factory() as session:
            trade = await session.get(TradeRecord, trade_id)
            return trade.underlying_symbol
    spot = _run(underlying()) or "NIFTY"
    armed = _arm(t, trade_id, kind="STOP", order_type="MARKET", trigger_source="SPOT", spot_level=24000.0, spot_side="BELOW",
                 trigger_mode="CLOSE", timeframe_minutes=5)
    assert armed["trigger"]["symbol"] == spot and armed["trigger"]["mode"] == "CLOSE"
    base = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start = base + timedelta(minutes=5 - base.minute % 5 + 5)   # a 5-minute candle boundary (counted from 09:15 IST)
    bars = [SimpleNamespace(timestamp=start + timedelta(minutes=i), low=23990.0, high=24010.0, close=23990.0) for i in range(12)]

    async def candles(symbol, exchange):
        return bars
    forming = start + timedelta(minutes=2)                    # nothing closed after arming yet (and no future bar is read)
    assert _exits(t["tenant_id"], {symbol: 90.0, spot: 23990.0}, candles=candles, now=forming).filled == 0
    later = start + timedelta(minutes=12)
    assert _exits(t["tenant_id"], {symbol: 90.0, spot: 23990.0}, candles=candles, now=later).filled == 1
    assert _trade(trade_id).exit_time is not None


def test_a_day_order_expires_and_the_heartbeat_is_on_worker_status(setup):
    t, expiry, strike, symbol = setup
    row_id = _rest(t, expiry, strike)["resting_order"]["id"]
    report = _entries(t["tenant_id"], {symbol: 96.0}, now=datetime.now(timezone.utc) + timedelta(days=2))
    assert report.expired >= 1 and _row(row_id).status == "EXPIRED"
    status = client.get("/api/system/worker-status", headers=t["headers"]).json()
    assert status["trigger_engine_last_run_at"] is not None and status["trigger_engine_seconds_since"] is not None


def test_a_stall_tells_the_owners_once(setup):
    t, expiry, strike, symbol = setup
    _rest(t, expiry, strike)
    trigger_engine._stall_alerted.clear()
    now = datetime.now(timezone.utc)

    async def go():
        async with _session_factory() as session:
            await trigger_engine.beat(session, now - timedelta(hours=1))
            first = await trigger_engine.check_stall(session, now)
            second = await trigger_engine.check_stall(session, now)
            closed = await trigger_engine.check_stall(session, now, market_open=False)
            mine = list(await session.scalars(select(NotificationRecord).where(
                NotificationRecord.tenant_id == t["tenant_id"], NotificationRecord.title == trigger_engine.STALL_TITLE)))
            await trigger_engine.beat(session, now)
            fresh = await trigger_engine.check_stall(session, now)
            return first, second, closed, mine, fresh
    first, second, closed, mine, fresh = _run(go())
    assert first >= 1 and second == 0 and closed == 0 and fresh == 0
    assert len(mine) == 1 and mine[0].severity == "CRITICAL"


def test_the_tick_pass_fires_on_live_hub_ticks(setup, monkeypatch):
    t, expiry, strike, symbol = setup
    row_id = _rest(t, expiry, strike)["resting_order"]["id"]
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(hub, "live_ticks", lambda ns: {f"NFO:{symbol}": [94.0, now.isoformat()]}
                        if ns == hub.org_namespace(t["tenant_id"]) else {})

    async def go():
        async with _session_factory() as session:
            return await trigger_engine.tick_pass(session, now)
    assert _run(go()).filled >= 1 and _row(row_id).status == "FILLED"


def test_only_the_owner_sees_and_cancels_his_waiting_orders(setup):
    t, expiry, strike, symbol = setup
    row_id = _rest(t, expiry, strike)["resting_order"]["id"]
    other = _org_tenant("tm2-other")
    assert client.post(f"/api/paper/fno/resting/{row_id}/cancel", headers=other["headers"]).status_code == 404
    assert row_id not in [r["id"] for r in client.get("/api/paper/fno/resting", headers=other["headers"]).json()]
    done = client.post(f"/api/paper/fno/resting/{row_id}/cancel", headers=t["headers"]).json()
    assert done["status"] == "CANCELLED" and done["trail"][-1]["note"] == "Cancelled by you"
    assert client.post(f"/api/paper/fno/resting/{row_id}/cancel", headers=t["headers"]).json()["status"] == "CANCELLED"


def test_stops_are_ranked_before_targets_before_entries():
    rows = [SimpleNamespace(purpose="ENTRY", kind="LIMIT", id=1), SimpleNamespace(purpose="EXIT", kind="TARGET", id=2),
            SimpleNamespace(purpose="EXIT", kind="STOP", id=3)]
    assert [r.id for r in sorted(rows, key=trigger_engine._rank)] == [3, 2, 1]


def test_heartbeat_row_name():
    async def go():
        async with _session_factory() as session:
            await trigger_engine.beat(session, datetime.now(timezone.utc))
            return await session.scalar(select(WorkerHeartbeatRecord).where(WorkerHeartbeatRecord.worker_name == "trigger_engine"))
    assert _run(go()) is not None
