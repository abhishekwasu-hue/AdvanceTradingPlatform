"""FNO-1 (owner order ATP_SESSION_FNO_WORKSPACE.md, sections 3, 6 and 9): the option chain's ONE order ticket, PAPER only.

- the routes exist only with `ui_v2` and the PAPER account on, and the table classifies them;
- the contract comes from the instrument master (lot size, tick size); quantity is in lots, units = lots x lot size;
- the order types (Market / Limit / SL / SL-M) fill in PAPER only when the price reaches them now; the confirm figures
  (margin, max loss) are there before anything is placed; the risk checks speak plain words;
- placing gives ONE PAPER order through the ordinary pipeline, the position shows on the chain ("P"), only to its holder;
- under the Phase 0 lock every order is PAPER: the ticket asserts it at the chokepoint, a LIVE entry is refused and never
  reaches a broker, and exits still run (ADR-0004)."""
import asyncio
import json
import random
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.brokers.models import ORDER_INTENT_ENTRY, BrokerOrderRequest
from app.brokers.rate_budget import RateBudget, RateLimitedBroker, RateLimits
from app.core.enums import OrderSide, SignalDirection, SignalGrade
from app.core.models import Signal
from app.db.models import InstrumentRecord, OrderRecord, TradeRecord, User
from app.execution.signal_execution import execute_signal_for_user
from app.market_data.calendar import IST
from app.paper import fno_ticket
from app.platform import phase0_lock
from tests.test_auth_api import _session_factory, client
from tests.test_trading_worker import _FakeBroker, _tenant
from tests.test_z3_approval_entries import _price
from tests.test_z3b_paper_account import _member, _paper_flag
from tests.test_z5_p1d_risk_by_default import _flag, _org
from tests.test_z10c_phase0_lock import _store_lock

LOT = 75


def _run(coro):
    return asyncio.run(coro)


def _expiry() -> date:
    """A future expiry no other test uses (the instrument table is shared)."""
    return datetime.now(IST).date() + timedelta(days=60 + random.randint(0, 2000))


def _contract(expiry: date, strike: float, right: str = "CE") -> str:
    symbol = f"NIFTY{uuid.uuid4().hex[:6].upper()}{int(strike)}{right}"

    async def go():
        async with _session_factory() as session:
            session.add(InstrumentRecord(broker="upstox", exchange="NFO", segment="NSE_FO", instrument_key=f"NSE_FO|{uuid.uuid4().hex[:10]}",
                                         tradingsymbol=symbol, name="NIFTY", underlying="NIFTY", instrument_type=right, expiry=expiry,
                                         strike=strike, lot_size=LOT, tick_size=0.05))
            await session.commit()
    _run(go())
    return symbol


def _org_tenant(tag: str) -> dict:
    t = _tenant(f"fno1-{tag}-{uuid.uuid4().hex[:6]}@example.com")
    _flag("ui_v2", t["tenant_id"])
    _paper_flag(t["tenant_id"])
    _org(t, capital=1_000_000.0, risk_pct=1.0)          # a risk per trade of 10,000
    return t


def _body(expiry: date, strike: float, **kw) -> dict:
    body = {"underlying": "NIFTY", "expiry": expiry.isoformat(), "strike": strike, "right": "CE", "side": "BUY", "lots": 1,
            "order_type": "MARKET", "product": "MIS"}
    body.update(kw)
    return body


def _preview(t, body) -> dict:
    out = client.post("/api/paper/fno/order/preview", headers=t["headers"], json=body)
    assert out.status_code == 200, out.text
    return out.json()


def _place(t, body) -> dict:
    out = client.post("/api/paper/fno/order", headers=t["headers"], json=body)
    assert out.status_code == 200, out.text
    return out.json()


@pytest.fixture
def setup(monkeypatch):
    t = _org_tenant("t")
    expiry = _expiry()
    strike = float(random.randrange(30000, 40000, 50))
    symbol = _contract(expiry, strike)
    _price(monkeypatch, 100.0)
    return t, expiry, strike, symbol


# --- flags and the contract -----------------------------------------------------------------------------------------

def test_routes_are_off_without_ui_v2_and_the_paper_account():
    t = _tenant(f"fno1-off-{uuid.uuid4().hex[:6]}@example.com")
    expiry = _expiry()
    for flags in ((), ("ui_v2",)):
        for name in flags:
            _flag(name, t["tenant_id"])
        out = client.post("/api/paper/fno/order/preview", headers=t["headers"], json=_body(expiry, 30000.0))
        assert out.status_code == 404
        assert out.headers["X-Feature-Disabled"] == ("ui_v2" if not flags else "paper_account")
    assert client.get("/api/paper/fno/held", params={"underlying": "NIFTY", "expiry": expiry.isoformat()}).status_code == 401


def test_the_contract_comes_from_the_instrument_master(setup):
    t, expiry, strike, symbol = setup
    out = client.get("/api/paper/fno/contract", headers=t["headers"],
                     params={"underlying": "nifty", "expiry": expiry.isoformat(), "strike": strike, "right": "CE"})
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["tradingsymbol"] == symbol and body["exchange"] == "NFO" and body["lot_size"] == LOT and body["tick_size"] == 0.05
    assert body["label"] == fno_ticket.contract_label("NIFTY", expiry, strike, "CE")
    assert body["label"].endswith(f" {int(strike)} CE") and body["freeze_quantity"] is None   # not in the master yet (FNO-1-2)
    missing = client.get("/api/paper/fno/contract", headers=t["headers"],
                         params={"underlying": "NIFTY", "expiry": expiry.isoformat(), "strike": strike + 25, "right": "CE"})
    assert missing.status_code == 404 and "contract list" in missing.json()["detail"] and "try again later" not in missing.json()["detail"].lower()


def test_contract_label_is_written_as_traders_write_it():
    assert fno_ticket.contract_label("NIFTY", date(2026, 10, 28), 22800.0, "CE") == "NIFTY 28 OCT 22800 CE"
    assert fno_ticket.contract_label("BANKNIFTY", date(2026, 11, 3), 51250.5, "PE") == "BANKNIFTY 03 NOV 51250.5 PE"


# --- the ticket: lots, order types, confirm figures, plain words --------------------------------------------------------

def test_lots_become_units_and_the_confirm_figures_come_first(setup):
    t, expiry, strike, _ = setup
    view = _preview(t, _body(expiry, strike, lots=1))
    assert view["mode"] == "PAPER" and "Practice mode (PAPER)" in view["practice"]
    assert view["lot_size"] == LOT and view["units"] == LOT and view["fill_price"] == 100.0 and view["price_source"] == "live"
    assert view["margin"]["value"] == pytest.approx(100.0 * LOT) and view["margin"]["estimated"] is True
    assert view["max_loss"]["value"] == pytest.approx(100.0 * LOT) and "expires worthless" in view["max_loss"]["basis"]
    assert view["charges"]["total"] > 0 and view["can_place"] is True
    assert view["confirm_label"] == f"Place PAPER order: Buy 1 lot ({LOT} qty)"
    # A stop-loss narrows the max loss to the stop.
    with_stop = _preview(t, _body(expiry, strike, lots=1, stop_loss=80.0))
    assert with_stop["max_loss"]["value"] == pytest.approx(20.0 * LOT) and "80.00" in with_stop["max_loss"]["basis"]


def test_the_risk_per_trade_says_in_plain_words_how_many_lots_fit(setup):
    t, expiry, strike, _ = setup
    view = _preview(t, _body(expiry, strike, lots=3))            # 3 x 75 x 100 = 22,500 at risk > 10,000
    assert not view["can_place"]
    assert any("allows 1 lot(s) at this stop-loss" in r for r in view["refusals"]), view["refusals"]
    assert view["risk"]["allowed_lots"] == 1
    # A closer stop lets more lots fit (75 x 30 = 2,250 per lot -> 4 lots).
    assert _preview(t, _body(expiry, strike, lots=3, stop_loss=70.0))["can_place"] is True


def test_a_sold_option_needs_a_stop_and_shows_no_made_up_margin(setup):
    t, expiry, strike, _ = setup
    # DEF-0: no built-in "a sold option needs a stop" - only the user's own my_limits setting asks for it.
    view = _preview(t, _body(expiry, strike, side="SELL"))
    assert not any("stop-loss on a sold option" in e or "needs a stop-loss" in e for e in view["errors"]), view["errors"]
    assert client.put("/api/me/prefs/my_limits", headers=t["headers"], json={"stop_required_on_sold": True}).status_code == 200
    view = _preview(t, _body(expiry, strike, side="SELL"))
    assert any("Your setting asks for a stop-loss on a sold option" in e for e in view["errors"]) and not view["can_place"]
    view = _preview(t, _body(expiry, strike, side="SELL", stop_loss=90.0))
    assert any("must be above the price" in e for e in view["errors"])
    view = _preview(t, _body(expiry, strike, side="SELL", stop_loss=120.0))
    assert view["max_loss"]["value"] == pytest.approx(20.0 * LOT) and "gap" in view["max_loss"]["basis"]
    # DEF-0 D8: no broker answer - one honest line, never "-" beside "comes with the order ticket"
    assert view["margin"]["value"] is None and view["margin"]["basis"].startswith("Margin not available")


@pytest.mark.parametrize("order_type,side,price,trigger,fills", [
    ("MARKET", "BUY", None, None, 100.0),
    ("LIMIT", "BUY", 101.0, None, 100.0), ("LIMIT", "BUY", 99.0, None, None),
    ("LIMIT", "SELL", 99.0, None, 100.0), ("LIMIT", "SELL", 101.0, None, None),
    ("SL-M", "BUY", None, 99.0, 100.0), ("SL-M", "BUY", None, 101.0, None),
    ("SL", "BUY", 100.5, 99.0, 100.0), ("SL", "BUY", 99.5, 99.0, None),
    ("SL-M", "SELL", None, 101.0, 100.0), ("SL", "SELL", 99.0, 100.5, 100.0),
])
def test_paper_fills_only_what_the_price_reaches_now(order_type, side, price, trigger, fills):
    fill, why = fno_ticket.paper_fill(order_type, side, 100.0, price, trigger)
    assert fill == fills
    assert why and (("Practice mode" in why or "would not fill" in why) if fills is None else "fills at" in why.lower())


def test_ticket_errors_are_plain_words():
    ask = fno_ticket.OrderAsk(underlying="NIFTY", expiry=date(2030, 1, 1), strike=22800, right="CE", side="BUY", lots=1, order_type="SL")
    assert fno_ticket.ask_errors(ask, 0.05) == ["Enter the limit price", "Enter the trigger price"]
    ask.price, ask.trigger_price = 100.02, 100.0
    assert "not a multiple of the tick size" in fno_ticket.ask_errors(ask, 0.05)[0]
    ask.price = 99.0
    assert fno_ticket.ask_errors(ask, 0.05) == ["For a buy SL order the limit price must be at or above the trigger"]
    assert fno_ticket.plain("Max open positions reached (3)") == "You already hold 3 positions, your limit. Exit one before a new entry."
    assert fno_ticket.plain("Daily loss limit breached (P&L -5000.00)").startswith("Today's loss limit is reached")
    assert fno_ticket.plain("Some other reason") == "Some other reason"


def test_a_resting_limit_is_refused_with_plain_words(setup):
    t, expiry, strike, _ = setup
    view = _preview(t, _body(expiry, strike, order_type="LIMIT", price=95.0))
    assert not view["can_place"] and any("does not keep a waiting order" in r for r in view["refusals"])
    placed = _place(t, _body(expiry, strike, order_type="LIMIT", price=95.0))
    assert placed["status"] == "REFUSED" and placed["order_id"] is None


def test_no_live_price_uses_the_chain_price_in_a_preview_but_never_places(monkeypatch, setup):
    t, expiry, strike, _ = setup
    _price(monkeypatch, None)
    view = _preview(t, _body(expiry, strike, chain_ltp=98.5))
    assert view["price_source"] == "chain" and view["fill_price"] == 98.5 and any("chain showed" in w for w in view["warnings"])
    placed = _place(t, _body(expiry, strike, chain_ltp=98.5))
    assert placed["status"] == "REFUSED" and any("No live price" in r for r in placed["reasons"])


# --- placing, the "P" marker, per-user isolation, exits -----------------------------------------------------------------

def _orders(tenant_id):
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(OrderRecord).where(OrderRecord.tenant_id == tenant_id)))
    return _run(go())


def _trade(trade_id) -> TradeRecord:
    async def go():
        async with _session_factory() as session:
            return await session.get(TradeRecord, trade_id)
    return _run(go())


def test_placing_gives_one_paper_order_and_the_position_shows_on_the_chain(setup):
    t, expiry, strike, symbol = setup
    ref = uuid.uuid4().hex
    placed = _place(t, _body(expiry, strike, lots=1, stop_loss=70.0, product="NRML", client_ref=ref))
    assert placed["status"] == "PLACED", placed
    assert placed["mode"] == "PAPER" and placed["filled"]["quantity"] == float(LOT) and placed["filled"]["lots"] == 1.0
    assert placed["filled"]["price"] == pytest.approx(100.0, abs=0.5)          # the paper broker's own slippage model
    again = _place(t, _body(expiry, strike, lots=1, stop_loss=70.0, product="NRML", client_ref=ref))   # a second tap
    assert again["order_id"] == placed["order_id"]
    orders = _orders(t["tenant_id"])
    assert len(orders) == 1 and orders[0].mode == "PAPER" and orders[0].symbol == symbol and orders[0].strategy_id == "manual_fno"
    trade = _trade(placed["trade_id"])
    assert trade.mode == "PAPER" and trade.instrument_kind == "OPTION" and trade.exchange == "NFO" and trade.lot_size == LOT
    assert trade.expiry == expiry and trade.option_position == "BUY" and trade.stop_loss == 70.0 and trade.holding == "SWING"
    assert trade.underlying_direction is None            # no underlying levels: it exits on its own stop or by hand

    held = client.get("/api/paper/fno/held", headers=t["headers"], params={"underlying": "NIFTY", "expiry": expiry.isoformat()}).json()
    assert [(h["strike"], h["right"], h["side"], h["lots"]) for h in held] == [(strike, "CE", "BUY", 1.0)]
    # Another member of the same organisation never sees it.
    other = _member(t)
    assert client.get("/api/paper/fno/held", headers=other["headers"], params={"underlying": "NIFTY", "expiry": expiry.isoformat()}).json() == []
    # Exit uses the existing path and works.
    out = client.post(f"/api/positions/{placed['trade_id']}/exit-now", headers=t["headers"])
    assert out.status_code == 200, out.text
    assert client.get("/api/paper/fno/held", headers=t["headers"], params={"underlying": "NIFTY", "expiry": expiry.isoformat()}).json() == []


def test_a_viewer_cannot_place(setup):
    t, expiry, strike, _ = setup
    viewer_email = f"fno1-viewer-{uuid.uuid4().hex[:6]}@example.com"
    invite = client.post("/api/team/invites", headers=t["headers"], json={"email": viewer_email, "role": "VIEWER"})
    assert invite.status_code in (200, 201), invite.text
    from urllib.parse import parse_qs, urlparse
    token = parse_qs(urlparse(invite.json()["invite_url"]).query)["invite"][0]
    access = client.post(f"/api/auth/invite/{token}/accept", json={"password": "Viewer-Pass-2026"}).json()["access_token"]
    out = client.post("/api/paper/fno/order", headers={"Authorization": f"Bearer {access}"}, json=_body(expiry, strike))
    assert out.status_code == 403


# --- Phase 0: every order is PAPER --------------------------------------------------------------------------------------

def _lock(tenant_id, on: bool) -> None:
    async def go():
        async with _session_factory() as session:
            await _store_lock(session, False, [tenant_id] if on else [])
    _run(go())
    if not on:
        phase0_lock.reset_cache()


def test_assert_paper_refuses_live_only_while_the_lock_holds():
    phase0_lock.remember(False, (41,))
    try:
        assert phase0_lock.assert_paper("PAPER", 41) == "PAPER"
        with pytest.raises(phase0_lock.LiveOrderRefused):
            phase0_lock.assert_paper("LIVE", 41)
        assert phase0_lock.assert_paper("LIVE", 42) == "LIVE"      # not locked for that organisation
    finally:
        phase0_lock.reset_cache()


def test_under_the_lock_the_ticket_places_paper_and_a_live_entry_never_reaches_a_broker(setup, monkeypatch):
    t, expiry, strike, symbol = setup
    _lock(t["tenant_id"], True)
    try:
        asserted = []
        real = phase0_lock.assert_paper
        monkeypatch.setattr(phase0_lock, "assert_paper", lambda mode, tenant_id: asserted.append((mode, tenant_id)) or real(mode, tenant_id))
        placed = _place(t, _body(expiry, strike, stop_loss=70.0))
        assert placed["status"] == "PLACED" and placed["mode"] == "PAPER"
        assert asserted == [("PAPER", t["tenant_id"])]                       # asserted at the chokepoint, right before the pipeline
        assert all(o.mode == "PAPER" for o in _orders(t["tenant_id"]))

        # A LIVE entry attempt through the pipeline: refused on the trail, never sent to the broker.
        broker = _FakeBroker()

        async def live():
            async with _session_factory() as session:
                user = await session.get(User, t["user_id"])
                sig = Signal(symbol=symbol, strategy_id="manual_fno", strategy_name="x", direction=SignalDirection.LONG,
                             timestamp=datetime.now(timezone.utc), entry=100.0, stop_loss=70.0, grade=SignalGrade.VALID)
                result, order = await execute_signal_for_user(session, user, mode="LIVE", strategy_id="manual_fno", signal=sig,
                                                              broker=broker, idempotency_key=f"fno1-live:{uuid.uuid4().hex}")
                return result.executed, order.status, json.loads(order.reasons_json or "[]")
        executed, status, reasons = _run(live())
        assert not executed and status == "REJECTED" and any(phase0_lock.ENTRY_REASON in r for r in reasons)
        assert broker.placed == []

        # The broker-order chokepoint: an entry is refused, the exit of an existing position still passes (ADR-0004).
        wrapped = RateLimitedBroker(broker, RateBudget(RateLimits(100.0, 100, 1000)), tenant_id=t["tenant_id"])  # type: ignore[arg-type]
        entry = BrokerOrderRequest(symbol=symbol, exchange="NFO", transaction_type=OrderSide.BUY, quantity=LOT, intent=ORDER_INTENT_ENTRY)
        exit_ = BrokerOrderRequest(symbol=symbol, exchange="NFO", transaction_type=OrderSide.SELL, quantity=LOT)
        assert _run(wrapped.place_order(entry)).status == "REJECTED"
        assert _run(wrapped.place_order(exit_)).status == "OPEN" and len(broker.placed) == 1

        # And the PAPER position's exit runs under the lock too.
        out = client.post(f"/api/positions/{placed['trade_id']}/exit-now", headers=t["headers"])
        assert out.status_code == 200, out.text
    finally:
        _lock(t["tenant_id"], False)


def test_the_ticket_has_no_mode_and_no_live_path():
    from app.paper.fno_routes import OrderBody
    assert "mode" not in OrderBody.model_fields
    assert fno_ticket.MODE == "PAPER"
    bad = client.post("/api/paper/fno/order", json={"mode": "LIVE"})
    assert bad.status_code in (401, 422)
