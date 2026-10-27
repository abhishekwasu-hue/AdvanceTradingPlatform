"""Terminal T2 (P1-6): TT-9 "Exit part" + "Reverse" and TT-10 Modify / Cancel of a waiting PAPER order.

- Exit part closes N of M lots through the one exit path (the row split, TM-17's method); the rest stays open with its
  own stop; more lots than are open is refused in plain words and closes nothing; the same reference never exits twice;
- Reverse closes the whole position, then opens the other side with the same lots through the ticket; a double tap never
  exits or opens twice; a sold side without the user's own stop closes nothing; preview changes nothing;
- both exits pass with the kill switch ON and approval-only on (ADR-0004); Reverse's entry half is then refused, plainly;
- Modify changes the caller's own waiting entry / armed exit (the engine fires at the new level); another organisation
  or member gets 404; an order no longer waiting is not changed."""
import asyncio
import json
import uuid

import pytest
from sqlalchemy import select

from app.db.models import TradeRecord
from app.execution import approval, resting_book, trigger_engine
from tests.test_auth_api import _session_factory, client
from tests.test_fno1_chain_ticket import LOT, _body, _contract, _expiry, _org_tenant, _place
from tests.test_z3_approval_entries import _price
from tests.test_z3b_paper_account import _member
from tests.test_z5_p1d_risk_by_default import _flag


def _run(coro):
    return asyncio.run(coro)


def _ref() -> str:
    return uuid.uuid4().hex[:16]


@pytest.fixture
def setup(monkeypatch):
    t = _org_tenant("t2")
    expiry = _expiry()
    strike = float(30000 + 50 * (uuid.uuid4().int % 200))
    symbol = _contract(expiry, strike)
    _price(monkeypatch, 100.0)
    return t, expiry, strike, symbol


def _open(t, expiry, strike, lots=3) -> int:
    out = _place(t, _body(expiry, strike, lots=lots, stop_loss=90.0, client_ref=_ref()))
    assert out["status"] == "PLACED", out
    return out["trade_id"]


def _trades(tenant_id) -> list:
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(TradeRecord).where(TradeRecord.tenant_id == tenant_id).order_by(TradeRecord.id)))
    return _run(go())


def _part(t, trade_id, lots, ref=None, **kw):
    return client.post(f"/api/paper/fno/positions/{trade_id}/exit-part", headers=t["headers"],
                       json={"lots": lots, "client_ref": ref or _ref(), **kw})


def _reverse(t, trade_id, ref=None, **kw):
    return client.post(f"/api/paper/fno/positions/{trade_id}/reverse", headers=t["headers"], json={"client_ref": ref or _ref(), **kw})


# --- Exit part -----------------------------------------------------------------------------------------------------------

def test_exit_part_leaves_the_rest_open(setup):
    t, expiry, strike, symbol = setup
    trade_id = _open(t, expiry, strike)
    out = _part(t, trade_id, 1)
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["status"] == "CLOSED" and body["remaining_lots"] == 2 and body["closed"]["lots"] == 1
    rows = {r.id: r for r in _trades(t["tenant_id"])}
    rest, part = rows[trade_id], rows[body["closed"]["trade_id"]]
    assert rest.exit_time is None and rest.quantity == 2 * LOT and rest.stop_loss == 90.0     # its own stop stays
    assert part.exit_time is not None and part.quantity == LOT and part.pnl is not None
    assert part.exit_reason.startswith("Exit part") and part.entry_price == rest.entry_price and part.symbol == symbol
    assert json.loads(part.group_meta)["split_from"] == trade_id
    # All the lots that are left close the row itself.
    last = _part(t, trade_id, 2).json()
    assert last["status"] == "CLOSED" and last["remaining_lots"] == 0 and last["closed"]["trade_id"] == trade_id
    assert all(r.exit_time is not None for r in _trades(t["tenant_id"]))


def test_exit_part_over_the_open_lots_is_refused_in_plain_words(setup):
    t, expiry, strike, _ = setup
    trade_id = _open(t, expiry, strike)
    out = _part(t, trade_id, 4)
    assert out.status_code == 422
    assert out.json()["detail"] == "You hold 3 lots of " + _trades(t["tenant_id"])[0].symbol + ". Choose between 1 and 3 lots to exit - nothing was closed."
    assert [r.quantity for r in _trades(t["tenant_id"])] == [3 * LOT] and _trades(t["tenant_id"])[0].exit_time is None


def test_exit_part_is_idempotent_on_its_reference(setup):
    t, expiry, strike, _ = setup
    trade_id = _open(t, expiry, strike)
    ref = _ref()
    first = _part(t, trade_id, 1, ref).json()
    again = _part(t, trade_id, 1, ref).json()
    assert again["repeat"] is True and again["closed"]["trade_id"] == first["closed"]["trade_id"]
    rows = _trades(t["tenant_id"])
    assert len(rows) == 2 and sum(r.quantity for r in rows if r.exit_time is None) == 2 * LOT


def test_an_armed_exit_follows_the_rest_after_a_part_exit(setup):
    t, expiry, strike, _ = setup
    trade_id = _open(t, expiry, strike)
    stop = client.post("/api/paper/fno/resting/exit", headers=t["headers"], json={
        "trade_id": trade_id, "kind": "STOP", "order_type": "SL-M", "level": 80.0, "client_ref": _ref()}).json()
    assert _part(t, trade_id, 2).status_code == 200
    rows = client.get("/api/paper/fno/resting", headers=t["headers"]).json()
    assert [r["quantity"] for r in rows if r["id"] == stop["id"]] == [float(LOT)]


# --- the kill switch and approval-only never stop an exit ---------------------------------------------------------------

def test_exit_part_and_reverse_work_with_the_kill_switch_on(setup):
    t, expiry, strike, _ = setup
    first, second = _open(t, expiry, strike), _open(t, expiry, strike)
    _flag(approval.FLAG, t["tenant_id"])
    assert client.post("/api/kill-switch/tenant/engage", headers=t["headers"], json={"reason": "t2"}).status_code == 200
    try:
        part = _part(t, first, 1)
        assert part.status_code == 200 and part.json()["status"] == "CLOSED"
        rev = _reverse(t, second, stop_loss=130.0)
        assert rev.status_code == 200, rev.text
        body = rev.json()
        assert body["status"] == "EXITED" and body["closed"]["closed"] is True             # the exit half went through
        assert body["entry"]["status"] == "REFUSED" and body["entry"]["reasons"]
        assert body["text"].startswith(f"{body['closed']['symbol']} closed. The opposite position was not opened:")
    finally:
        client.post("/api/kill-switch/tenant/disengage", headers=t["headers"])
    rows = {r.id: r for r in _trades(t["tenant_id"])}
    assert rows[second].exit_time is not None and not any(r.direction == "SHORT" for r in rows.values())


# --- Reverse -------------------------------------------------------------------------------------------------------------

def test_reverse_closes_and_opens_the_other_side_once_on_a_double_tap(setup):
    t, expiry, strike, symbol = setup
    trade_id = _open(t, expiry, strike, lots=2)
    ref = _ref()
    first = _reverse(t, trade_id, ref, stop_loss=130.0)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "REVERSED" and first.json()["entry"]["status"] == "PLACED"
    again = _reverse(t, trade_id, ref, stop_loss=130.0)
    assert again.status_code == 200 and again.json()["repeat"] is True
    assert again.json()["entry"]["trade_id"] == first.json()["entry"]["trade_id"]
    rows = _trades(t["tenant_id"])
    assert len(rows) == 2
    old, new = rows
    assert old.exit_time is not None and old.exit_reason.startswith("Reverse")
    assert new.exit_time is None and new.direction == "SHORT" and new.quantity == 2 * LOT and new.symbol == symbol
    assert new.stop_loss == 130.0                                                            # the user's own stop


def test_reverse_without_the_new_sides_stop_closes_nothing(setup):
    t, expiry, strike, _ = setup
    trade_id = _open(t, expiry, strike)
    out = _reverse(t, trade_id)
    assert out.status_code == 422 and "stop-loss" in out.json()["detail"] and out.json()["detail"].endswith("Nothing was closed.")
    assert _trades(t["tenant_id"])[0].exit_time is None


def test_reverse_preview_changes_nothing(setup):
    t, expiry, strike, _ = setup
    trade_id = _open(t, expiry, strike)
    out = _reverse(t, trade_id, stop_loss=130.0, preview=True)
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["status"] == "PREVIEW" and body["lots"] == 3 and body["entry"]["side"] == "SELL" and body["exit"]["side"] == "Buy"
    assert len(_trades(t["tenant_id"])) == 1 and _trades(t["tenant_id"])[0].exit_time is None


# --- tenancy -------------------------------------------------------------------------------------------------------------

def test_exit_part_and_reverse_are_the_callers_own(setup):
    t, expiry, strike, _ = setup
    trade_id = _open(t, expiry, strike)
    other, member = _org_tenant("t2-other"), _member(t)
    assert _part(other, trade_id, 1).status_code == 404 and _reverse(other, trade_id, stop_loss=130.0).status_code == 404
    assert _part(member, trade_id, 1).status_code == 403 and _reverse(member, trade_id, stop_loss=130.0).status_code == 403
    assert client.post(f"/api/paper/fno/positions/{trade_id}/exit-part", json={"lots": 1, "client_ref": _ref()}).status_code == 401
    assert [r.exit_time for r in _trades(t["tenant_id"])] == [None]


# --- Modify / Cancel (TT-10) ---------------------------------------------------------------------------------------------

def _rest(t, expiry, strike):
    out = _place(t, _body(expiry, strike, order_type="LIMIT", price=95.0, stop_loss=85.0, rest_if_waiting=True, client_ref=_ref()))
    assert out["status"] == "RESTING", out
    return out["resting_order"]


def _modify(t, row_id, **body):
    return client.post(f"/api/paper/fno/resting/{row_id}/modify", headers=t["headers"], json=body)


def _entries(tenant_id, table):
    async def lookup(symbol, exchange="NFO"):
        if symbol not in table:
            raise LookupError(symbol)
        return float(table[symbol])

    async def go():
        async with _session_factory() as session:
            return await trigger_engine.run_entries(session, tenant_id, lookup)
    return _run(go())


def test_modify_a_waiting_entry_then_it_fires_at_the_new_price(setup):
    t, expiry, strike, symbol = setup
    row = _rest(t, expiry, strike)
    out = _modify(t, row["id"], price=92.0, lots=2)
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["price"] == 92.0 and body["trigger"]["level"] == 92.0 and body["quantity"] == 2 * LOT
    assert body["trail"][-1]["note"].startswith("Changed by you: 2 lot(s)")
    assert _entries(t["tenant_id"], {symbol: 94.0}).filled == 0                    # the old price no longer fires it
    assert _entries(t["tenant_id"], {symbol: 91.5}).filled == 1
    assert [r.quantity for r in _trades(t["tenant_id"])] == [2 * LOT]


def test_modify_refuses_bad_values_in_plain_words(setup):
    t, expiry, strike, _ = setup
    row = _rest(t, expiry, strike)
    off_tick = _modify(t, row["id"], price=92.03)
    assert off_tick.status_code == 422 and "tick size" in off_tick.json()["detail"]
    assert _modify(t, row["id"], level=90.0).status_code == 422


def test_modify_an_armed_stop_level(setup):
    t, expiry, strike, _ = setup
    trade_id = _open(t, expiry, strike)
    stop = client.post("/api/paper/fno/resting/exit", headers=t["headers"], json={
        "trade_id": trade_id, "kind": "STOP", "order_type": "SL-M", "level": 80.0, "client_ref": _ref()}).json()
    out = _modify(t, stop["id"], level=85.0)
    assert out.status_code == 200, out.text
    assert out.json()["trigger_price"] == 85.0 and out.json()["trigger"]["level"] == 85.0
    lots = _modify(t, stop["id"], lots=1)
    assert lots.status_code == 422 and "Exit part" in lots.json()["detail"]


def test_modify_and_cancel_are_the_callers_own(setup):
    t, expiry, strike, _ = setup
    row = _rest(t, expiry, strike)
    other, member = _org_tenant("t2-mod"), _member(t)
    for who in (other, member):
        assert _modify(who, row["id"], price=90.0).status_code == 404
        assert client.post(f"/api/paper/fno/resting/{row['id']}/cancel", headers=who["headers"]).status_code == 404
    assert client.get("/api/paper/fno/resting", headers=t["headers"]).json()[0]["price"] == 95.0
    assert client.post(f"/api/paper/fno/resting/{row['id']}/cancel", headers=t["headers"]).json()["status"] == resting_book.CANCELLED
    late = _modify(t, row["id"], price=90.0)
    assert late.status_code == 409 and "can no longer be changed" in late.json()["detail"]


def test_a_part_exit_is_not_a_new_trade_for_the_trades_per_day_count(setup):
    """The closed part of a split is the same trade: trades-per-day (day state + the risk hierarchy) counts it once."""
    from datetime import datetime, timezone

    from app.db.models import User
    from app.trading.persistence import build_trading_day_state
    t, expiry, strike, _ = setup
    trade_id = _open(t, expiry, strike)

    async def count():
        async with _session_factory() as session:
            user = await session.get(User, t["user_id"])
            return (await build_trading_day_state(session, user, now=datetime.now(timezone.utc))).trades_today
    before = _run(count())
    assert _part(t, trade_id, 1).status_code == 200
    assert _run(count()) == before
