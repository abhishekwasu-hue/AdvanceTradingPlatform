"""CX PR 2 (owner, 24 Oct): the combined-exit routes under /api/paper/fno/group-exits.

The user sets, previews, edits and removes a combined stop-loss / target over his own open positions; the server reads
the value now itself (never a typed price) and a level's side from it; another user or organisation never sees or
changes one; the routes sit behind the same flags as the ticket; nothing here sends an order (the trigger engine does,
tests/test_cx_group_exits.py).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.db.models import GroupExitRecord, TradeRecord
from app.execution import group_exits as gx
from app.execution import trigger_engine
from tests.test_auth_api import _session_factory, client
from tests.test_fno1_chain_ticket import _org_tenant
from tests.test_trading_worker import OPEN_NOW, STRATEGY
from tests.test_z3b_paper_account import _member

BASE = "/api/paper/fno/group-exits"


def _run(coro):
    return asyncio.run(coro)


def _leg(t, symbol, direction, entry, *, quantity=50, underlying="NIFTY") -> int:
    async def go():
        async with _session_factory() as session:
            trade = TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], mode="PAPER", strategy_id=STRATEGY, symbol=symbol,
                                exchange="NFO", direction=direction, entry_price=entry, quantity=quantity,
                                stop_loss=entry * (3 if direction == "SHORT" else 0.1), target1=entry, target2=entry,
                                leg_role="SHORT" if direction == "SHORT" else "LONG", instrument_kind="OPTION",
                                underlying_symbol=underlying, entry_time=OPEN_NOW - timedelta(minutes=30))
            session.add(trade)
            await session.commit()
            return trade.id
    return _run(go())


@pytest.fixture
def prices(monkeypatch):
    """The person's own live price, from a table the test sets (what "Exit now" reads)."""
    table: dict = {}

    async def lookup_for(session, user):
        async def lookup(symbol, exchange="NSE"):
            if symbol not in table:
                raise LookupError("no price")
            return table[symbol]
        return lookup
    monkeypatch.setattr("app.ai.worker_bridge.price_lookup_for_user", lookup_for)
    return table


@pytest.fixture(autouse=True)
def _end_live_exits():
    yield

    async def end():
        from sqlalchemy import update
        async with _session_factory() as session:
            await session.execute(update(GroupExitRecord).where(GroupExitRecord.status.in_(gx.LIVE_STATUSES)).values(
                status=gx.CANCELLED))
            await session.commit()
    _run(end())


def _spread(t, tag):
    return _leg(t, f"{tag}SHORT", "SHORT", 100.0), _leg(t, f"{tag}HEDGE", "LONG", 40.0)


def _ref() -> str:
    return uuid.uuid4().hex[:16]


def test_preview_says_it_in_plain_words_with_the_value_now_and_stores_nothing(prices):
    t = _org_tenant("cx-api-prev")
    short, hedge = _spread(t, "CXAP")
    prices.update({"CXAPSHORT": 110.0, "CXAPHEDGE": 42.0})                       # MTM -500 + 100 = -400
    out = client.post(f"{BASE}/preview", headers=t["headers"], json={"trade_ids": [short, hedge], "measure": "MTM_INR",
                                                                      "sl_value": 1000, "target_value": 600, "client_ref": _ref()})
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["ok"] and body["value_now"] == -400.0
    assert body["words"] == "If combined loss reaches ₹1,000 or combined profit reaches ₹600, these 2 legs exit - sold legs first."
    assert body["distance"] == {"sl": 600.0, "target": 1000.0}
    assert client.get(BASE, headers=t["headers"]).json() == []


def test_a_spot_exit_reads_its_side_from_the_server_spot_and_lists_with_its_distance(prices):
    t = _org_tenant("cx-api-spot")
    short, hedge = _spread(t, "CXAS")
    prices.update({"NIFTY 50": 23800.0, "CXASSHORT": 110.0, "CXASHEDGE": 42.0})
    body = {"trade_ids": [short, hedge], "measure": "SPOT", "sl_value": 24000, "spot_symbol": "NIFTY 50",
            "spot_exchange": "NSE", "client_ref": _ref()}
    made = client.post(BASE, headers=t["headers"], json=body)
    assert made.status_code == 200, made.text
    row = made.json()
    assert row["sl_side"] == "ABOVE" and row["status"] == "ARMED" and row["distance"]["sl"] == 200.0
    again = client.post(BASE, headers=t["headers"], json=body)                    # the same tap never sets two
    assert again.json()["id"] == row["id"]
    listed = client.get(BASE, headers=t["headers"]).json()
    assert [r["id"] for r in listed] == [row["id"]] and "NIFTY 50 spot rises to 24,000.00" in listed[0]["words"]


def test_without_a_live_spot_a_level_exit_is_refused_in_plain_words(prices):
    t = _org_tenant("cx-api-nospot")
    short, hedge = _spread(t, "CXAN")
    out = client.post(BASE, headers=t["headers"], json={"trade_ids": [short, hedge], "measure": "SPOT", "sl_value": 24000,
                                                          "spot_symbol": "NIFTY 50", "spot_exchange": "NSE", "client_ref": _ref()})
    assert out.status_code == 422 and "No live price" in out.json()["detail"]


def test_another_users_position_is_refused_and_another_member_never_sees_or_removes_it(prices):
    t = _org_tenant("cx-api-own")
    short, hedge = _spread(t, "CXAO")
    member = _member(t)
    theirs = _leg(member, "CXAOTHEIRS", "SHORT", 100.0)
    refused = client.post(BASE, headers=t["headers"], json={"trade_ids": [short, theirs], "measure": "MTM_INR",
                                                             "sl_value": 500, "client_ref": _ref()})
    assert refused.status_code == 422 and "not your open positions" in refused.json()["detail"]
    mine = client.post(BASE, headers=t["headers"], json={"trade_ids": [short, hedge], "measure": "MTM_INR", "sl_value": 500,
                                                          "client_ref": _ref()}).json()
    assert client.get(BASE, headers=member["headers"]).json() == []
    assert client.post(f"{BASE}/{mine['id']}/cancel", headers=member["headers"]).status_code == 404
    assert client.post(f"{BASE}/{mine['id']}/edit", headers=member["headers"], json={"version": 1, "sl_value": 900}).status_code == 404
    other_org = _org_tenant("cx-api-other")
    assert client.post(f"{BASE}/{mine['id']}/cancel", headers=other_org["headers"]).status_code == 404


def test_all_open_positions_are_frozen_when_the_exit_is_set(prices):
    t = _org_tenant("cx-api-all")
    short, hedge = _spread(t, "CXAA")
    row = client.post(BASE, headers=t["headers"], json={"scope": "ALL_OPEN", "measure": "MTM_INR", "sl_value": 2000,
                                                         "client_ref": _ref()}).json()
    assert row["scope"] == "ALL_OPEN" and sorted(leg["trade_id"] for leg in row["legs"]) == sorted([short, hedge])
    later = _leg(t, "CXAALATER", "SHORT", 90.0)
    listed = client.get(BASE, headers=t["headers"]).json()[0]
    assert later not in [leg["trade_id"] for leg in listed["legs"]]


def test_edit_needs_the_version_shown_and_reads_the_side_again(prices):
    t = _org_tenant("cx-api-edit")
    short, hedge = _spread(t, "CXAE")
    prices.update({"NIFTY 50": 23800.0})
    row = client.post(BASE, headers=t["headers"], json={"trade_ids": [short, hedge], "measure": "SPOT", "sl_value": 24000,
                                                         "spot_symbol": "NIFTY 50", "spot_exchange": "NSE",
                                                         "client_ref": _ref()}).json()
    stale = client.post(f"{BASE}/{row['id']}/edit", headers=t["headers"], json={"version": 9, "sl_value": 24100})
    assert stale.status_code == 409 and "another screen" in stale.json()["detail"]
    prices["NIFTY 50"] = 24050.0                                                 # the spot moved above the old level
    moved = client.post(f"{BASE}/{row['id']}/edit", headers=t["headers"], json={"version": 1, "sl_value": 23900})
    assert moved.status_code == 200, moved.text
    assert moved.json()["version"] == 2 and moved.json()["sl_value"] == 23900.0 and moved.json()["sl_side"] == "BELOW"
    assert client.post(f"{BASE}/{row['id']}/edit", headers=t["headers"], json={"version": 2}).status_code == 422   # nothing set


def test_remove_an_armed_exit_but_not_one_that_is_closing_the_legs(prices):
    t = _org_tenant("cx-api-cancel")
    short, hedge = _spread(t, "CXAC")
    first = client.post(BASE, headers=t["headers"], json={"trade_ids": [short, hedge], "measure": "MTM_INR", "sl_value": 500,
                                                           "client_ref": _ref()}).json()
    gone = client.post(f"{BASE}/{first['id']}/cancel", headers=t["headers"])
    assert gone.status_code == 200 and gone.json()["status"] == "CANCELLED"
    assert client.post(f"{BASE}/{first['id']}/edit", headers=t["headers"], json={"version": 1, "sl_value": 900}).status_code == 422
    second = client.post(BASE, headers=t["headers"], json={"trade_ids": [short, hedge], "measure": "MTM_INR", "sl_value": 500,
                                                            "client_ref": _ref()}).json()

    async def fire():
        async with _session_factory() as session:
            row = await session.get(GroupExitRecord, second["id"])
            row.last_value = -600.0
            assert await trigger_engine._claim_group(session, row, gx.SL, OPEN_NOW)
    _run(fire())
    busy = client.post(f"{BASE}/{second['id']}/cancel", headers=t["headers"])
    assert busy.status_code == 409 and "Already exiting" in busy.json()["detail"]


def test_the_routes_follow_the_ticket_flags(prices):
    from tests.test_trading_worker import _tenant
    plain = _tenant(f"cx-api-off-{uuid.uuid4().hex[:6]}@example.com", with_credentials=False)
    assert client.get(BASE, headers=plain["headers"]).status_code == 404
    assert client.post(f"{BASE}/preview", headers=plain["headers"], json={"measure": "MTM_INR", "sl_value": 1,
                                                                           "client_ref": _ref()}).status_code == 404


def test_the_body_takes_no_price_and_no_mode_from_the_browser(prices):
    t = _org_tenant("cx-api-body")
    short, hedge = _spread(t, "CXAB")
    for extra in ({"value_now": 1.0}, {"mode": "LIVE"}, {"sl_side": "ABOVE"}):
        out = client.post(BASE, headers=t["headers"], json={"trade_ids": [short, hedge], "measure": "MTM_INR", "sl_value": 500,
                                                             "client_ref": _ref(), **extra})
        assert out.status_code == 422

    async def count():
        async with _session_factory() as session:
            return len(list(await session.scalars(select(GroupExitRecord).where(GroupExitRecord.tenant_id == t["tenant_id"]))))
    assert _run(count()) == 0
