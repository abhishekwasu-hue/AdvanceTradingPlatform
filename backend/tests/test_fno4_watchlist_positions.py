"""FNO-4 (owner order ATP_SESSION_FNO_WORKSPACE.md, sections 2, 8 and 9): F&O contracts in watchlists and the positions
page's strategy grouping.

- a saved contract row (the existing watchlist item `{symbol, exchange}` on NFO / BFO) reads as the contract: its name as
  traders write it, the days left, "Expired" once its expiry has passed (or the expiry day's close), and the roll target
  (the same strike in the next listed expiry; the next monthly for a monthly contract) - from the instrument master, or
  from the trading symbol itself once the master has dropped it; nothing guessed;
- the lookup is read only: no order path is reached (a roll only edits the watchlist, in the frontend);
- the legs of one basket (their orders' keys) and of one structure are grouped for the combined P&L, the caller's own only;
- exits stay unblocked: a grouped leg still exits by itself.
"""
import asyncio
import random
import uuid
from datetime import date, datetime, time, timedelta

import pytest

from app.db.models import InstrumentRecord
from app.market_data.calendar import IST
from app.paper import fno_contracts
from app.platform import phase0_lock
from tests.test_auth_api import _session_factory, client
from tests.test_fno2_basket import _condor_legs, _lock, _org_tenant, _post, condor  # noqa: F401 - the fixture is used by name
from tests.test_trading_worker import _tenant
from tests.test_z3b_paper_account import _member
from tests.test_z5_p1d_risk_by_default import _flag


def _run(coro):
    return asyncio.run(coro)


def _name() -> str:
    """An underlying no other test uses (the instrument table is shared)."""
    return "ZQ" + "".join(random.choice("ABCDEFGHJKLMNPRSTUVWXY") for _ in range(6))


def _row(underlying: str, expiry: date, strike, right: str, *, weekly=False, symbol=None, broker="upstox") -> str:
    symbol = symbol or f"{underlying}{uuid.uuid4().hex[:6].upper()}{right}"

    async def go():
        async with _session_factory() as session:
            session.add(InstrumentRecord(broker=broker, exchange="NFO", segment="NSE_FO", instrument_key=f"NSE_FO|{uuid.uuid4().hex[:12]}",
                                         tradingsymbol=symbol, name=underlying, underlying=underlying, instrument_type=right, expiry=expiry,
                                         strike=strike, lot_size=50, tick_size=0.05, weekly=weekly))
            await session.commit()
    _run(go())
    return symbol


def _today() -> date:
    return datetime.now(IST).date()


def _ui_tenant(tag: str) -> dict:
    t = _tenant(f"fno4-{tag}-{uuid.uuid4().hex[:6]}@example.com")
    _flag("ui_v2", t["tenant_id"])
    return t


def _lookup(t, items) -> list:
    out = client.post("/api/paper/fno/contracts", headers=t["headers"], json={"items": items})
    assert out.status_code == 200, out.text
    return out.json()["items"]


# --- the trading symbol by itself ------------------------------------------------------------------------------------------

def test_upstox_and_kite_symbols_are_read():
    p = fno_contracts.parse_symbol("NIFTY 22800 CE 28 OCT 25")
    assert (p.underlying, p.right, p.strike, p.expiry, p.monthly) == ("NIFTY", "CE", 22800.0, date(2025, 10, 28), None)
    p = fno_contracts.parse_symbol("BANKNIFTY FUT 30 DEC 26")
    assert (p.underlying, p.right, p.strike, p.expiry, p.monthly) == ("BANKNIFTY", "FUT", None, date(2026, 12, 30), True)
    p = fno_contracts.parse_symbol("NIFTY25OCT22800PE")
    assert (p.underlying, p.right, p.strike, p.expiry, p.month, p.monthly) == ("NIFTY", "PE", 22800.0, None, (2025, 10), True)
    p = fno_contracts.parse_symbol("NIFTY25O2822800CE")
    assert (p.underlying, p.right, p.strike, p.expiry, p.monthly) == ("NIFTY", "CE", 22800.0, date(2025, 10, 28), False)
    p = fno_contracts.parse_symbol("M&M25NOVFUT")
    assert (p.underlying, p.right, p.month) == ("M&M", "FUT", (2025, 11))
    for bad in ("RELIANCE", "NIFTY 50", "NIFTY 22800 CE 31 FEB 25", "", "NIFTY25X2822800CE"):
        assert fno_contracts.parse_symbol(bad) is None, bad


def test_expired_after_the_expiry_days_close():
    day = date(2026, 10, 28)
    at = lambda d, h, m: datetime.combine(d, time(h, m), tzinfo=IST)            # noqa: E731
    assert fno_contracts.is_expired(day, at(day - timedelta(days=1), 15, 0)) is False
    assert fno_contracts.is_expired(day, at(day, 15, 29)) is False
    assert fno_contracts.is_expired(day, at(day, 15, 30)) is True
    assert fno_contracts.is_expired(day, at(day + timedelta(days=1), 9, 0)) is True
    # Only the month known: past months expired, the month itself unknown.
    assert fno_contracts.is_expired(None, at(day, 10, 0), (2026, 9)) is True
    assert fno_contracts.is_expired(None, at(day, 10, 0), (2026, 10)) is None
    assert fno_contracts.days_left(day, at(day - timedelta(days=6), 10, 0)) == 6
    assert fno_contracts.days_left(day, at(day, 10, 0)) == 0
    assert fno_contracts.days_left(day, at(day + timedelta(days=1), 10, 0)) is None


def test_basket_ref_is_read_from_the_order_key():
    assert fno_contracts.basket_ref_of("fno:7:3f2a9c0e1b4d4e8f9a7b6c5d4e3f2a1b-3", 7) == "3f2a9c0e1b4d4e8f9a7b6c5d4e3f2a1b"
    assert fno_contracts.basket_ref_of("fno:7:1b4e28ba-2fa1-11d2-883f-0016d3cca427-12", 7) == "1b4e28ba-2fa1-11d2-883f-0016d3cca427"
    # A single ticket (a UUID ends in 12 hex characters), another user's key, anything else: no basket.
    assert fno_contracts.basket_ref_of("fno:7:1b4e28ba-2fa1-11d2-883f-0016d3cca427", 7) is None
    assert fno_contracts.basket_ref_of("fno:8:3f2a9c0e1b4d4e8f9a7b6c5d4e3f2a1b-3", 7) is None
    assert fno_contracts.basket_ref_of(None, 7) is None and fno_contracts.basket_ref_of("approval:5", 7) is None


# --- the route: flags, contract rows, the roll ------------------------------------------------------------------------------

def test_the_lookup_is_behind_ui_v2_and_needs_a_sign_in():
    t = _tenant(f"fno4-off-{uuid.uuid4().hex[:6]}@example.com")
    body = {"items": [{"symbol": "NIFTY25OCT22800CE", "exchange": "NFO"}]}
    out = client.post("/api/paper/fno/contracts", headers=t["headers"], json=body)
    assert out.status_code == 404 and out.headers["X-Feature-Disabled"] == "ui_v2"
    assert client.post("/api/paper/fno/contracts", json=body).status_code == 401
    _flag("ui_v2", t["tenant_id"])
    assert client.post("/api/paper/fno/contracts", headers=t["headers"], json=body).status_code == 200
    # No paper account needed (reference data); bounded.
    assert client.post("/api/paper/fno/contracts", headers=t["headers"], json={"items": []}).status_code == 422
    many = {"items": [{"symbol": f"X{i}", "exchange": "NFO"} for i in range(fno_contracts.MAX_ITEMS + 1)]}
    assert client.post("/api/paper/fno/contracts", headers=t["headers"], json=many).status_code == 422


def test_a_live_contract_row_reads_from_the_master():
    t = _ui_tenant("live")
    u = _name()
    expiry = _today() + timedelta(days=9)
    symbol = _row(u, expiry, 22800.0, "CE")
    fut = _row(u, expiry, None, "FUT")
    rows = _lookup(t, [{"symbol": symbol, "exchange": "NFO"}, {"symbol": fut, "exchange": "NFO"}, {"symbol": "RELIANCE", "exchange": "NSE"}])
    opt, future, plain = rows
    assert opt["known"] and opt["listed"] and opt["underlying"] == u and opt["right"] == "CE" and opt["strike"] == 22800.0
    assert opt["label"].startswith(f"{u} ") and opt["label"].endswith(" 22800 CE") and opt["lot_size"] == 50
    assert opt["expired"] is False and opt["days_left"] == 9 and opt["roll"] is None
    assert future["right"] == "FUT" and future["strike"] is None and future["label"].endswith(" FUT")
    assert plain == {"symbol": "RELIANCE", "exchange": "NSE", "known": False}


def test_an_expired_contract_offers_the_same_strike_in_the_next_expiry():
    t = _ui_tenant("roll")
    u = _name()
    today = _today()
    old = _row(u, today - timedelta(days=3), 22800.0, "CE", weekly=True)
    near = _row(u, today + timedelta(days=4), 22800.0, "CE", weekly=True)
    _row(u, today + timedelta(days=4), 22850.0, "CE", weekly=True)                 # another strike: never the roll
    _row(u, today + timedelta(days=4), 22800.0, "PE", weekly=True)                 # another right: never the roll
    monthly = _row(u, today + timedelta(days=25), 22800.0, "CE", weekly=False)
    row = _lookup(t, [{"symbol": old, "exchange": "NFO"}])[0]
    assert row["expired"] is True and row["days_left"] is None
    assert row["roll"]["symbol"] == near and row["roll"]["strike"] == 22800.0 and row["roll"]["right"] == "CE"
    assert row["roll"]["exchange"] == "NFO" and row["roll_note"] is None
    # A monthly contract rolls to the next monthly.
    old_monthly = _row(u, today - timedelta(days=2), 22800.0, "CE", weekly=False)
    assert _lookup(t, [{"symbol": old_monthly, "exchange": "NFO"}])[0]["roll"]["symbol"] == monthly


def test_a_contract_the_master_dropped_is_read_from_its_symbol():
    t = _ui_tenant("dropped")
    u = _name()
    today = _today()
    gone = today - timedelta(days=5)
    symbol = f"{u} 22800 CE {gone.strftime('%d %b %y').upper()}"                  # Upstox's spelling, not in the master
    nxt = _row(u, today + timedelta(days=2), 22800.0, "CE", weekly=True)
    row = _lookup(t, [{"symbol": symbol, "exchange": "NFO"}])[0]
    assert row["known"] and row["listed"] is False and row["expired"] is True
    assert row["label"].endswith(" 22800 CE") and row["roll"]["symbol"] == nxt
    # No next expiry of that strike in the master: no roll, said plainly - never a guessed contract.
    other = f"{u} 99900 PE {gone.strftime('%d %b %y').upper()}"
    row = _lookup(t, [{"symbol": other, "exchange": "NFO"}])[0]
    assert row["expired"] is True and row["roll"] is None and "not in the instrument list yet" in row["roll_note"]


def test_the_lookup_never_reaches_an_order(monkeypatch):
    import app.execution.signal_execution as execution

    async def boom(*a, **kw):
        raise AssertionError("no order from a watchlist lookup")
    monkeypatch.setattr(execution, "execute_signal_for_user", boom)
    monkeypatch.setattr(phase0_lock, "assert_paper", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("no order")))
    t = _ui_tenant("noorder")
    u = _name()
    old = _row(u, _today() - timedelta(days=1), 100.0, "CE")
    _row(u, _today() + timedelta(days=6), 100.0, "CE")
    assert _lookup(t, [{"symbol": old, "exchange": "NFO"}])[0]["roll"] is not None


# --- the positions page: legs grouped, exits never blocked ---------------------------------------------------------------

def test_basket_legs_group_for_the_combined_pnl_and_each_leg_still_exits(condor):  # noqa: F811
    expiry, base, legs = condor
    t = _org_tenant("groups")
    out = client.get("/api/paper/fno/positions/groups", headers=t["headers"])
    assert out.status_code == 200 and out.json() == {"groups": []}
    _lock(t["tenant_id"], True)
    try:
        placed = _post(t, "execute", {"legs": _condor_legs(expiry, legs), "basket_ref": uuid.uuid4().hex, "product": "NRML"})
        assert placed["placed"] == 4 and all(r["mode"] == "PAPER" for r in placed["legs"])
        groups = client.get("/api/paper/fno/positions/groups", headers=t["headers"]).json()["groups"]
        assert len(groups) == 1 and groups[0]["kind"] == "basket" and sorted(groups[0]["trade_ids"]) == sorted(r["trade_id"] for r in placed["legs"])
        # Another member of the organisation sees none of them.
        member = _member(t)
        assert client.get("/api/paper/fno/positions/groups", headers=member["headers"]).json() == {"groups": []}
        # A grouped leg exits by itself, under the Phase 0 lock too (ADR-0004); the group keeps the others.
        first = groups[0]["trade_ids"][0]
        assert client.post(f"/api/positions/{first}/exit-now", headers=t["headers"]).status_code == 200
        left = client.get("/api/paper/fno/positions/groups", headers=t["headers"]).json()["groups"]
        assert len(left) == 1 and first not in left[0]["trade_ids"] and len(left[0]["trade_ids"]) == 3
    finally:
        _lock(t["tenant_id"], False)


def test_position_groups_are_behind_ui_v2_and_the_paper_account():
    t = _tenant(f"fno4-groups-off-{uuid.uuid4().hex[:6]}@example.com")
    out = client.get("/api/paper/fno/positions/groups", headers=t["headers"])
    assert out.status_code == 404 and out.headers["X-Feature-Disabled"] == "ui_v2"
    _flag("ui_v2", t["tenant_id"])
    out = client.get("/api/paper/fno/positions/groups", headers=t["headers"])
    assert out.status_code == 404 and out.headers["X-Feature-Disabled"] == "paper_account"
    assert client.get("/api/paper/fno/positions/groups").status_code == 401


@pytest.mark.parametrize("oi,expected", [(1250000.0, 1250000.0), (0, None), (None, None), ("x", None)])
def test_a_quote_row_carries_open_interest_only_when_the_broker_gives_it(oi, expected):
    from datetime import timezone

    from app.market_data import quotes_routes
    now = datetime.now(timezone.utc)
    row = quotes_routes._priced(("NFO", "NIFTY25OCT22800CE"), 101.5, prev_close=100.0, timestamp=now, source="quote", now=now,
                                unknown_age_is_stale=False, oi=oi)
    assert row["oi"] == expected and row["ltp"] == 101.5
    assert quotes_routes._missing(("NFO", "X"), "none")["oi"] is None


def test_add_from_a_position_is_one_more_paper_ticket_order_and_never_a_group(monkeypatch):
    """Add on the positions page opens the same ticket: one more PAPER order through `/api/paper/fno/order`, asserted at
    the chokepoint. Two single tickets are not a group; each position still exits by itself."""
    from tests.test_fno1_chain_ticket import _body, _contract, _expiry, _org_tenant as _ticket_tenant, _place
    from tests.test_z3_approval_entries import _price
    t = _ticket_tenant("add")
    expiry = _expiry()
    strike = float(random.randrange(30000, 40000, 50))
    _contract(expiry, strike)
    _price(monkeypatch, 100.0)
    asserted = []
    real = phase0_lock.assert_paper
    monkeypatch.setattr(phase0_lock, "assert_paper", lambda mode, tenant_id: asserted.append((mode, tenant_id)) or real(mode, tenant_id))
    first = _place(t, _body(expiry, strike, client_ref=str(uuid.uuid4())))
    added = _place(t, _body(expiry, strike, client_ref=str(uuid.uuid4())))
    assert first["status"] == "PLACED" and added["status"] == "PLACED" and added["mode"] == "PAPER"
    assert asserted == [("PAPER", t["tenant_id"])] * 2
    assert client.get("/api/paper/fno/positions/groups", headers=t["headers"]).json() == {"groups": []}
    contract = _lookup(t, [{"symbol": added["contract"]["tradingsymbol"], "exchange": "NFO"}])[0]
    assert contract["expired"] is False and contract["strike"] == strike and contract["right"] == "CE"
    for trade_id in (first["trade_id"], added["trade_id"]):
        assert client.post(f"/api/positions/{trade_id}/exit-now", headers=t["headers"]).status_code == 200
