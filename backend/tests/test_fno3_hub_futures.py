"""FNO-3 (owner order ATP_SESSION_FNO_WORKSPACE.md, sections 1, 4 and 9): the symbol hub's F&O facts and the futures
chain, and a FUTURE through the one order ticket (PAPER only).

Every number here is hand-made (round values, no market data) and worked out by hand in the test:
- change = LTP - previous close (and % of it); basis = future LTP - spot LTP (and % of the spot);
- rollover = the next month's share of the near + next futures OI, and the spread next - near (compute_rollover_proxy);
- OI change = the change since the organisation's first reading of the day, "building" for the first minutes.
Plus: F&O availability from the instrument master, the routes behind `ui_v2`, a broker session (409 without one, never
another organisation's), and a future order placed PAPER with `phase0_lock.assert_paper` at the chokepoint."""
import asyncio
import json
import random
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.brokers.models import Quote
from app.cache import client as cache_client
from app.db.models import InstrumentRecord, OrderRecord, TradeRecord
from app.market_data import api_budget
from app.market_data.calendar import IST, SessionStatus
from app.option_chain import chain_view, futures_chain, futures_routes
from app.paper import fno_ticket
from app.platform import phase0_lock
from tests.test_amw2_watchlists import _set_flag
from tests.test_auth_api import _session_factory, client
from tests.test_fno1_chain_ticket import _lock, _org_tenant
from tests.test_oc1_option_chain_view import _MemoryCache
from tests.test_phase_aa_market_data_api import _Broker, _store_credentials
from tests.test_phase_k_commercial import _owner
from tests.test_z3_approval_entries import _price

run = asyncio.run
LOT = 50
NOW = datetime(2026, 10, 21, 11, 0, tzinfo=IST)
TODAY = NOW.date()


@pytest.fixture
def memory_cache(monkeypatch):
    cache = _MemoryCache()
    monkeypatch.setattr(cache_client, "_get_client", lambda: cache)
    return cache


def _name() -> str:
    """An underlying no other test uses (the instrument table is shared)."""
    return "FNOT" + "".join(random.choice("ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(6))


def _add(rows):
    async def go():
        async with _session_factory() as session:
            for r in rows:
                session.add(r)
            await session.commit()
    run(go())


def _future(name: str, expiry: date, broker: str = "upstox", lot: int = LOT) -> InstrumentRecord:
    sym = f"{name}{expiry.strftime('%y%b').upper()}FUT"
    return InstrumentRecord(broker=broker, exchange="NFO", segment="NSE_FO", instrument_key=f"NSE_FO|{uuid.uuid4().hex[:10]}",
                            tradingsymbol=sym, name=name, underlying=name, instrument_type="FUT", expiry=expiry, strike=None,
                            lot_size=lot, tick_size=0.05)


def _option(name: str, expiry: date, strike: float, right: str = "CE") -> InstrumentRecord:
    return InstrumentRecord(broker="upstox", exchange="NFO", segment="NSE_FO", instrument_key=f"NSE_FO|{uuid.uuid4().hex[:10]}",
                            tradingsymbol=f"{name}{uuid.uuid4().hex[:4].upper()}{int(strike)}{right}", name=name, underlying=name,
                            instrument_type=right, expiry=expiry, strike=strike, lot_size=LOT, tick_size=0.05)


NEAR, NEXT, FAR = date(2026, 10, 27), date(2026, 11, 24), date(2026, 12, 29)


def _listed(name: str):
    """Three futures (plus an expired one and a second broker's copy of the near one, both left out) and options."""
    _add([_future(name, NEAR), _future(name, NEXT), _future(name, FAR), _future(name, date(2026, 9, 29)),
          _future(name, NEAR, broker="zerodha"), _option(name, NEAR, 1000.0), _option(name, NEXT, 1000.0), _option(name, NEXT, 1000.0, "PE")])


# --- the pure figures ----------------------------------------------------------------------------------------------------

def test_change_and_basis_by_hand():
    assert futures_chain.change_of(1010.0, 1000.0) == (10.0, 1.0)
    assert futures_chain.change_of(990.0, None) == (None, None)
    assert futures_chain.basis_of(1012.5, 1000.0) == (12.5, 1.25)            # the future trades 12.50 above the spot
    assert futures_chain.basis_of(995.0, 1000.0) == (-5.0, -0.5)             # below it
    assert futures_chain.basis_of(None, 1000.0) == (None, None) and futures_chain.basis_of(1000.0, None) == (None, None)


def _row(expiry: date, ltp, oi, name="NIFTY"):
    return {"expiry": expiry.isoformat(), "label": futures_chain.future_label(name, expiry), "ltp": ltp, "oi": oi}


def test_rollover_by_hand():
    # Near OI 300, next OI 100: the next month holds 100 / 400 = 25.0 %. Spread 1015 - 1005 = 10.00.
    out = futures_chain.rollover_of("NIFTY", _row(NEAR, 1005.0, 300.0), _row(NEXT, 1015.0, 100.0))
    assert out["next_share_pct"] == 25.0 and out["spread"] == 10.0 and out["spread_pct"] == pytest.approx(0.995, abs=1e-3)
    assert out["near_label"] == "NIFTY 27 OCT FUT" and out["next_label"] == "NIFTY 24 NOV FUT"
    assert out["caption"] == ("25.0% of the near and next month futures OI is in the next month; "
                              "the next month trades 10.00 above the near month.")
    below = futures_chain.rollover_of("NIFTY", _row(NEAR, 1005.0, None), _row(NEXT, 1000.5, 100.0))
    assert below["next_share_pct"] is None and below["spread"] == -4.5
    assert below["caption"] == "The next month trades 4.50 below the near month."
    assert futures_chain.rollover_of("NIFTY", _row(NEAR, None, None), _row(NEXT, None, None)) is None
    # Plain numbers only: no reading of direction, no advice words.
    for word in ("bull", "bear", "buy", "sell", "should"):
        assert word not in out["caption"].lower()


def test_future_label():
    assert futures_chain.future_label("NIFTY", date(2026, 10, 27)) == "NIFTY 27 OCT FUT"
    assert fno_ticket.contract_label("BANKNIFTY", date(2026, 11, 24), None, "FUT") == "BANKNIFTY 24 NOV FUT"


# --- F&O availability (the hub) ------------------------------------------------------------------------------------------

def _v2_owner(tag):
    headers, me = _owner(f"fno3-{tag}-{uuid.uuid4().hex[:6]}@example.com")
    _set_flag("ui_v2", me["tenant_id"], on=True)
    return headers, me


def test_hub_says_which_symbols_have_fno(monkeypatch):
    monkeypatch.setattr(chain_view, "today_ist", lambda now=None: TODAY)
    headers, _ = _v2_owner("hub")
    name = _name()
    _listed(name)
    body = client.get(f"/api/option-chain/{name.lower()}/hub", headers=headers).json()
    assert body["underlying"] == name and body["has_fno"] is True and body["has_futures"] is True and body["has_options"] is True
    assert body["lot_size"] == LOT
    assert body["future_expiries"] == [NEAR.isoformat(), NEXT.isoformat(), FAR.isoformat()]     # expired one left out, one per expiry
    assert [e["date"] for e in body["option_expiries"]] == [NEAR.isoformat(), NEXT.isoformat()]
    assert body["default_option_expiry"] == NEAR.isoformat()
    # A symbol the master does not list (an ordinary share without F&O): no F&O, no made-up lot size or expiry.
    other = _name()
    plain = client.get(f"/api/option-chain/{other}/hub", headers=headers).json()
    assert plain["has_fno"] is False and plain["has_futures"] is False and plain["has_options"] is False
    assert plain["lot_size"] is None and plain["future_expiries"] == [] and plain["option_expiries"] == []
    # An index under its chart name maps to its F&O underlying.
    assert client.get("/api/option-chain/NIFTY%2050/hub", headers=headers).json()["underlying"] == "NIFTY"
    assert client.get("/api/option-chain/%3Cx%3E/hub", headers=headers).status_code == 422


def test_routes_need_a_login_and_ui_v2():
    for path in ("/api/option-chain/NIFTY/hub", "/api/option-chain/NIFTY/futures"):
        assert client.get(path).status_code == 401
    headers, me = _owner(f"fno3-off-{uuid.uuid4().hex[:6]}@example.com")
    for path in ("/api/option-chain/NIFTY/hub", "/api/option-chain/NIFTY/futures"):
        res = client.get(path, headers=headers)
        assert res.status_code == 404 and res.headers.get("X-Feature-Disabled") == "ui_v2"


# --- the futures chain ---------------------------------------------------------------------------------------------------

class _QuoteBroker(_Broker):
    """Quotes for the spot and the three futures (hand-made). `multi` switches the one-call batch path on."""

    def __init__(self, name: str, multi: bool = False, oi_bump: float = 0.0):
        super().__init__()
        self.multi_quote = multi
        self.asked: list = []
        self.batches: list = []
        sym = lambda e: f"{name}{e.strftime('%y%b').upper()}FUT"   # noqa: E731
        ts = datetime.now(timezone.utc)
        self.quotes = {
            ("NSE", name): Quote(symbol=name, ltp=1000.0, prev_close=990.0, timestamp=ts),
            ("NFO", sym(NEAR)): Quote(symbol=sym(NEAR), ltp=1005.0, prev_close=1000.0, oi=300.0 + oi_bump, volume=1200, bid=1004.9, ask=1005.1, timestamp=ts),
            ("NFO", sym(NEXT)): Quote(symbol=sym(NEXT), ltp=1015.0, prev_close=1012.0, oi=100.0 + oi_bump, timestamp=ts),
            # The far month: no price from the broker.
        }

    async def get_quote_for_symbol(self, symbol, exchange="NSE"):
        self.asked.append((exchange, symbol))
        return self.quotes.get((exchange, symbol))

    async def get_quotes_batch(self, items):
        if not self.multi_quote:
            return None
        self.batches.append(list(items))
        return {p: q for p, q in self.quotes.items() if p in items}

    async def get_ltp_for_symbol(self, symbol, exchange="NSE"):
        return 0.0


def _setup(monkeypatch, broker, market_open=True):
    monkeypatch.setattr(futures_routes, "build_adapter", lambda record: broker)
    monkeypatch.setattr(chain_view, "today_ist", lambda now=None: TODAY)

    async def status(session, now=None, exchange="NSE"):
        return SessionStatus(market_open, "test", None)
    monkeypatch.setattr(futures_routes, "market_session_status", status)
    api_budget.reset()


def test_futures_chain_shape_and_figures(monkeypatch, memory_cache):
    headers, me = _v2_owner("chain")
    _store_credentials(me)
    name = _name()
    _listed(name)
    broker = _QuoteBroker(name)
    _setup(monkeypatch, broker)
    res = client.get(f"/api/option-chain/{name}/futures", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["underlying"] == name and body["source"]["broker"] == "upstox"
    assert body["spot"] == {"symbol": name, "exchange": "NSE", "ltp": 1000.0, "change": 10.0, "change_pct": 1.01}
    rows = body["rows"]
    assert [r["expiry"] for r in rows] == [NEAR.isoformat(), NEXT.isoformat(), FAR.isoformat()]
    near, nxt, far = rows
    assert near["label"] == f"{name} 27 OCT FUT" and near["lot_size"] == LOT and near["exchange"] == "NFO" and near["days_to_expiry"] == 6
    assert (near["ltp"], near["change"], near["change_pct"], near["oi"]) == (1005.0, 5.0, 0.5, 300.0)
    assert (near["basis"], near["basis_pct"]) == (5.0, 0.5)                    # 1005 - 1000, 0.5 % of the spot
    assert (nxt["basis"], nxt["basis_pct"]) == (15.0, 1.5) and nxt["change"] == 3.0
    assert near["bid"] == 1004.9 and near["ask"] == 1005.1 and near["volume"] == 1200
    # No price for the far month: shown as missing with the reason, never a made-up number.
    assert far["ltp"] is None and far["missing"] is True and far["basis"] is None and far["error"]
    assert body["rollover"]["next_share_pct"] == 25.0 and body["rollover"]["spread"] == 10.0
    assert body["rollover"]["caption"].startswith("25.0% of the near and next month futures OI")
    # OI change: the first reading of the day starts the count.
    assert body["oi_change"]["status"] == "building" and near["oi_change"] is None
    # One quote per contract (the adapter has no multi-quote call): spot + 3 futures.
    assert len(broker.asked) == 4 and body["broker_calls"] == 4


def test_futures_chain_uses_one_batch_call_and_counts_oi_change(monkeypatch, memory_cache):
    headers, me = _v2_owner("batch")
    _store_credentials(me)
    name = _name()
    _listed(name)
    broker = _QuoteBroker(name, multi=True)
    _setup(monkeypatch, broker)
    first = client.get(f"/api/option-chain/{name}/futures", headers=headers).json()
    assert len(broker.batches) == 1 and broker.asked == [] and first["broker_calls"] == 1
    # Ten minutes later the OI is 40 higher on both: the change is +40 against the first reading.
    key = futures_chain.oi_baseline_key(me["tenant_id"], name, TODAY)
    stored = json.loads(memory_cache.store[key])
    stored["at"] = (datetime.fromisoformat(stored["at"]) - timedelta(minutes=10)).isoformat()
    memory_cache.store[key] = json.dumps(stored)
    _setup(monkeypatch, _QuoteBroker(name, multi=True, oi_bump=40.0))
    body = client.get(f"/api/option-chain/{name}/futures", headers=headers).json()
    assert body["oi_change"]["status"] == "ready" and body["oi_change"]["source"] == "first_reading"
    assert [r["oi_change"] for r in body["rows"]] == [40.0, 40.0, None]


def test_market_closed_starts_no_oi_count(monkeypatch, memory_cache):
    headers, me = _v2_owner("closed")
    _store_credentials(me)
    name = _name()
    _listed(name)
    _setup(monkeypatch, _QuoteBroker(name), market_open=False)
    body = client.get(f"/api/option-chain/{name}/futures", headers=headers).json()
    assert body["market_open"] is False and body["oi_change"]["status"] == "none" and memory_cache.store == {}
    assert body["rows"][0]["ltp"] == 1005.0                         # the last session's prices still show


def test_a_symbol_without_futures_answers_empty(monkeypatch, memory_cache):
    headers, me = _v2_owner("nofut")
    _store_credentials(me)
    _setup(monkeypatch, _QuoteBroker("X"))
    name = _name()
    body = client.get(f"/api/option-chain/{name}/futures", headers=headers).json()
    assert body["rows"] == [] and body["rollover"] is None and "no futures" in body["message"]


def test_no_broker_session_is_409_and_never_another_organisations(monkeypatch, memory_cache):
    name = _name()
    _listed(name)
    a_headers, a = _v2_owner("tenant-a")
    _store_credentials(a)                                    # organisation A has a broker session
    b_headers, _ = _v2_owner("tenant-b")                     # organisation B has none
    broker = _QuoteBroker(name)
    _setup(monkeypatch, broker)
    assert client.get(f"/api/option-chain/{name}/futures", headers=a_headers).status_code == 200
    asked = len(broker.asked)
    res = client.get(f"/api/option-chain/{name}/futures", headers=b_headers)
    assert res.status_code == 409 and "Broker page" in res.json()["detail"]
    assert len(broker.asked) == asked                         # B's request made no broker call through A's session


def test_oi_first_reading_is_kept_per_organisation(memory_cache):
    async def go():
        state_a, _ = await futures_chain.oi_change_state(1, "NIFTY", {"NIFTYFUT": 100.0}, NOW, True)
        state_b, _ = await futures_chain.oi_change_state(2, "NIFTY", {"NIFTYFUT": 500.0}, NOW, True)
        later = NOW + timedelta(minutes=10)
        _, change_a = await futures_chain.oi_change_state(1, "NIFTY", {"NIFTYFUT": 130.0}, later, True)
        _, change_b = await futures_chain.oi_change_state(2, "NIFTY", {"NIFTYFUT": 450.0}, later, True)
        return state_a, state_b, change_a, change_b
    state_a, state_b, change_a, change_b = run(go())
    assert state_a["status"] == state_b["status"] == "building"
    assert change_a == {"NIFTYFUT": 30.0} and change_b == {"NIFTYFUT": -50.0}


def test_budget_used_is_429_not_a_queue(monkeypatch, memory_cache):
    headers, me = _v2_owner("budget")
    _store_credentials(me)
    name = _name()
    _listed(name)
    _setup(monkeypatch, _QuoteBroker(name))

    async def no_token(*a, **kw):
        return False
    monkeypatch.setattr(api_budget, "take", no_token)
    res = client.get(f"/api/option-chain/{name}/futures", headers=headers)
    assert res.status_code == 429 and res.headers.get("Retry-After") == "5"


# --- a FUTURE through the one order ticket, PAPER -----------------------------------------------------------------------

def _orders(tenant_id):
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(OrderRecord).where(OrderRecord.tenant_id == tenant_id)))
    return run(go())


def _trade(trade_id) -> TradeRecord:
    async def go():
        async with _session_factory() as session:
            return await session.get(TradeRecord, trade_id)
    return run(go())


@pytest.fixture
def fut(monkeypatch):
    t = _org_tenant("fut")
    expiry = datetime.now(IST).date() + timedelta(days=60 + random.randint(0, 2000))
    name = "NIFTY"
    row = _future(name, expiry)
    row.tradingsymbol = f"NIFTY{uuid.uuid4().hex[:6].upper()}FUT"
    _add([row])
    _price(monkeypatch, 1000.0)
    return t, expiry, row.tradingsymbol


def _fbody(expiry, **kw):
    body = {"underlying": "NIFTY", "expiry": expiry.isoformat(), "right": "FUT", "side": "BUY", "lots": 1, "order_type": "MARKET", "product": "NRML"}
    body.update(kw)
    return body


def test_the_contract_route_and_the_ticket_accept_a_future(fut):
    t, expiry, symbol = fut
    out = client.get("/api/paper/fno/contract", headers=t["headers"], params={"underlying": "NIFTY", "expiry": expiry.isoformat(), "right": "FUT"})
    assert out.status_code == 200, out.text
    c = out.json()
    assert c["tradingsymbol"] == symbol and c["lot_size"] == LOT and c["strike"] is None and c["label"].endswith(" FUT")
    # An option needs a strike, a future has none.
    assert client.get("/api/paper/fno/contract", headers=t["headers"],
                      params={"underlying": "NIFTY", "expiry": expiry.isoformat(), "right": "FUT", "strike": 100}).status_code == 422
    assert client.post("/api/paper/fno/order/preview", headers=t["headers"], json=_fbody(expiry, strike=100.0)).status_code == 422
    assert client.post("/api/paper/fno/order/preview", headers=t["headers"],
                       json={**_fbody(expiry), "right": "CE"}).status_code == 422

    view = client.post("/api/paper/fno/order/preview", headers=t["headers"], json=_fbody(expiry)).json()
    assert any("A future needs a stop-loss" in e for e in view["errors"]) and not view["can_place"]
    view = client.post("/api/paper/fno/order/preview", headers=t["headers"], json=_fbody(expiry, stop_loss=990.0)).json()
    assert view["can_place"] is True, view
    assert view["lot_size"] == LOT and view["units"] == LOT and view["fill_price"] == 1000.0
    assert view["max_loss"]["value"] == pytest.approx(10.0 * LOT)               # (1000 - 990) x 50
    assert view["margin"]["value"] is None and "SPAN" in view["margin"]["basis"] and "50,000" in view["margin"]["note"]
    sold = client.post("/api/paper/fno/order/preview", headers=t["headers"], json=_fbody(expiry, side="SELL", stop_loss=990.0)).json()
    assert any("must be above the price" in e and "sold future" in e for e in sold["errors"])
    sold = client.post("/api/paper/fno/order/preview", headers=t["headers"], json=_fbody(expiry, side="SELL", stop_loss=1008.0)).json()
    assert sold["can_place"] is True and sold["max_loss"]["value"] == pytest.approx(8.0 * LOT)


def test_a_future_order_is_paper_and_asserted_at_the_chokepoint(fut, monkeypatch):
    t, expiry, symbol = fut
    _lock(t["tenant_id"], True)
    try:
        asserted = []
        real = phase0_lock.assert_paper
        monkeypatch.setattr(phase0_lock, "assert_paper", lambda mode, tenant_id: asserted.append((mode, tenant_id)) or real(mode, tenant_id))
        placed = client.post("/api/paper/fno/order", headers=t["headers"], json=_fbody(expiry, stop_loss=990.0)).json()
        assert placed["status"] == "PLACED", placed
        assert placed["mode"] == "PAPER" and asserted == [("PAPER", t["tenant_id"])]
        orders = _orders(t["tenant_id"])
        assert len(orders) == 1 and orders[0].mode == "PAPER" and orders[0].symbol == symbol
        trade = _trade(placed["trade_id"])
        assert trade.mode == "PAPER" and trade.instrument_kind == "FUTURE" and trade.lot_size == LOT and trade.expiry == expiry
        assert trade.quantity == float(LOT) and trade.stop_loss == 990.0
        # Its exit runs under the lock (ADR-0004).
        out = client.post(f"/api/positions/{placed['trade_id']}/exit-now", headers=t["headers"])
        assert out.status_code == 200, out.text
    finally:
        _lock(t["tenant_id"], False)


# --- a FUTURE as a basket leg (FNO-2's panel; "Add to basket" on the futures chain), PAPER ----------------------------------

@pytest.fixture
def covered(monkeypatch):
    """A covered call on a unique expiry: a future at 30,000 and the 30,200 call at 100 (lot 75, as the FNO-2 tests)."""
    from tests.test_fno2_basket import LOT as BLOT, _contract, _expiry, _prices
    expiry = _expiry()
    fut = _future("NIFTY", expiry, lot=BLOT)
    fut.tradingsymbol = f"NIFTY{uuid.uuid4().hex[:6].upper()}FUT"
    _add([fut])
    call = _contract(expiry, 30200.0, "CE")
    _prices(monkeypatch, {fut.tradingsymbol: 30000.0, call: 100.0})
    return expiry, fut.tradingsymbol, call, BLOT


def _covered_legs(expiry, fut_stop=29960.0):
    future = {"underlying": "NIFTY", "expiry": expiry.isoformat(), "right": "FUT", "side": "BUY", "lots": 1, "price": 30000.0}
    if fut_stop is not None:
        future["stop_loss"] = fut_stop
    call = {"underlying": "NIFTY", "expiry": expiry.isoformat(), "strike": 30200.0, "right": "CE", "side": "SELL", "lots": 1, "price": 100.0,
            "stop_loss": 150.0}
    return [call, future]


def test_a_future_leg_in_the_basket_numbers(covered):
    from tests.test_fno2_basket import _org_tenant as basket_tenant, _post
    expiry, _, _, lot = covered
    t = basket_tenant("fut-eval")
    out = _post(t, "evaluate", {"legs": [{**leg, "iv": 0.15} if leg["right"] != "FUT" else leg for leg in _covered_legs(expiry)], "spot": 30000.0})
    assert out["ok"] is True, out
    assert out["labels"][1] == fno_ticket.contract_label("NIFTY", expiry, None, "FUT") and out["labels"][1].endswith(" FUT")
    assert out["lot_sizes"] == [lot, lot] and out["order"] == [1, 0]                # the bought future first, then the sold call
    assert out["net_premium"] == pytest.approx(100.0 * lot)                        # the future is left out of the premium
    # At expiry above the call: (30,200 - 30,000) on the future + the 100 kept = 300 a unit, by hand.
    assert out["extremes"]["max_profit"] == pytest.approx(300.0 * lot)
    assert out["margin"]["hedge"]["with_hedge"]["value"] is None                   # a future's margin is the broker's, never invented
    # A future (no strike) with a strike, or an option without one, is refused.
    bad = _covered_legs(expiry)
    assert client.post("/api/paper/fno/basket/evaluate", headers=t["headers"],
                       json={"legs": [{**bad[1], "strike": 30000.0}], "spot": 30000.0}).status_code == 422
    assert client.post("/api/paper/fno/basket/evaluate", headers=t["headers"],
                       json={"legs": [{k: v for k, v in bad[0].items() if k != "strike"}], "spot": 30000.0}).status_code == 422


def test_a_future_leg_needs_a_stop_and_is_placed_paper_first(covered, monkeypatch):
    from tests.test_fno2_basket import _lock as basket_lock, _orders as basket_orders, _org_tenant as basket_tenant, _post
    expiry, fut_symbol, call_symbol, lot = covered
    t = basket_tenant("fut-exec")
    view = _post(t, "preview", {"legs": _covered_legs(expiry, fut_stop=None), "spot": 30000.0})
    assert not view["can_execute"] and any("A future needs a stop-loss" in e for r in view["legs"] for e in r["errors"])
    basket_lock(t["tenant_id"], True)
    try:
        asserted = []
        real = phase0_lock.assert_paper
        monkeypatch.setattr(phase0_lock, "assert_paper", lambda mode, tenant_id: asserted.append((mode, tenant_id)) or real(mode, tenant_id))
        out = _post(t, "execute", {"legs": _covered_legs(expiry), "basket_ref": uuid.uuid4().hex, "product": "NRML"})
        assert out["status"] == "PLACED" and out["placed"] == 2, out
        assert asserted == [("PAPER", t["tenant_id"])] * 2
        orders = basket_orders(t["tenant_id"])
        assert [o.symbol for o in orders] == [fut_symbol, call_symbol] and all(o.mode == "PAPER" for o in orders)
        trade = _trade(out["legs"][0]["trade_id"])
        assert trade.instrument_kind == "FUTURE" and trade.mode == "PAPER" and trade.quantity == float(lot)
    finally:
        basket_lock(t["tenant_id"], False)


def test_a_saved_basket_holds_a_future_with_no_strike():
    from tests.test_trading_worker import _tenant
    a = _tenant(f"fno3-save-{uuid.uuid4().hex[:6]}@example.com")
    leg = {"expiry": "2026-10-27", "strike": None, "right": "FUT", "side": "BUY", "lots": 1, "price": 30000.0, "stop_loss": 29960.0}
    body = {"items": {"abcd1234": {"name": "Covered call", "underlying": "NIFTY", "product": "NRML", "legs": [leg]}}}
    out = client.put("/api/me/prefs/fno_baskets?version=0", headers=a["headers"], json=body)
    assert out.status_code == 200, out.text
    assert out.json()["value"]["items"]["abcd1234"]["legs"][0]["right"] == "FUT"
    for bad in ({**leg, "strike": 30000.0}, {**leg, "right": "CE"}):
        assert client.put("/api/me/prefs/fno_baskets?version=1", headers=a["headers"],
                          json={"items": {"abcd1234": {**body["items"]["abcd1234"], "legs": [bad]}}}).status_code == 422
