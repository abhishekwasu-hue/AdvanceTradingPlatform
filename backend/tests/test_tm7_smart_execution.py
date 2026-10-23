"""Terminal master order Part 7 SMART EXECUTION (flag `smart_execution`, behind `ui_v2`; owner decisions TM-3, TM-9,
TM-10, TM-11 of 23 Oct).

- sequencing: entry hedges (BUY) first, then the SELL legs; exit the reverse (shorts closed first);
- TM-11: no SELL before every hedge filled; the first failure stops the run with the INCOMPLETE state and the two
  choices "Complete the rest" / "Close the filled legs"; exiting, a hedge is never closed while its short is open;
- freeze slicing: whole lots strictly below the exchange's freeze quantity; the slices add up; unknown limit = one order;
  the exchange's own file overrides the maintained table;
- TM-3: a marketable limit with the user's own band where market orders are not allowed;
- TM-10: the broker's basket margin through the user's own session where the adapter has one, else ATP's estimate,
  labelled so; the free balance after the order;
- the physical-settlement warning: thresholds from the user's settings (no fixed number), stock F&O only, the delivery
  value in rupees, once a day per position as a notification;
- through the pipeline: the kill switch refuses, a second tap never duplicates, exits are never blocked, routes are off
  without the flag and scoped to the caller (tenancy)."""
import asyncio
import random
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.brokers.models import BasketMargin
from app.core.enums import AssetClass, ExecutionMode, SignalDirection, SignalGrade
from app.core.models import RiskConfig, Signal
from app.db.models import NotificationRecord, OrderRecord, TradeRecord, User
from app.execution import smart_execution as se
from app.execution.router import OrderRouter
from app.instruments import freeze_limits
from app.instruments.models import ContractSpec
from app.me import prefs
from app.paper import fno_basket, fno_ticket, settlement_watch
from app.risk_engine.risk_manager import TradingDayState
from tests.test_auth_api import _session_factory, client
from tests.test_fno2_basket import _condor_legs, _leg, _org_tenant, _orders, _post, condor  # noqa: F401 - the fixture
from tests.test_z3b_paper_account import _member
from tests.test_z5_p1d_risk_by_default import _flag

SETTINGS = se.SmartSettings(settlement_days_before=4, settlement_band_pct=2.0, settlement_telegram=True, protection_band_pct=0.5)


def _run(coro):
    return asyncio.run(coro)


def _smart(t: dict) -> dict:
    _flag(se.FLAG, t["tenant_id"])
    return t


# --- sequencing and the run (pure) ----------------------------------------------------------------------------------

def test_entry_puts_hedges_first_and_exit_closes_shorts_first():
    sides = ["SELL", "BUY", "SELL", "BUY"]
    assert se.entry_sequence(sides) == [1, 3, 0, 2]
    assert se.exit_sequence(sides) == [0, 2, 1, 3]
    assert se.entry_sequence(["buy", "sell"]) == [0, 1] and se.exit_sequence(["buy", "sell"]) == [1, 0]


def _placer(fail: set, log: list):
    async def place(index, seq):
        log.append(index)
        ok = index not in fail
        return ok, [] if ok else ["refused"], {"index": index}
    return place


def test_all_legs_fill_complete():
    log: list = []
    out = _run(se.run(["SELL", "BUY"], _placer(set(), log)))
    assert out.state == se.COMPLETE and out.actions == [] and log == [1, 0]


def test_a_failed_hedge_means_no_short_is_ever_sent():
    log: list = []
    out = _run(se.run(["SELL", "BUY", "SELL", "BUY"], _placer({1}, log)))
    assert log == [1]                                                   # the first hedge failed: nothing else sent
    assert out.state == se.NOTHING_PLACED and [leg.status for leg in out.legs] == ["FAILED", "NOT_PLACED", "NOT_PLACED", "NOT_PLACED"]
    assert "uncovered" in out.legs[2].reasons[0]


def test_a_later_leg_failing_gives_the_incomplete_state_with_two_choices():
    log: list = []
    out = _run(se.run(["SELL", "BUY", "SELL", "BUY"], _placer({2}, log)))
    assert log == [1, 3, 0, 2]
    assert out.state == se.INCOMPLETE and out.actions == [se.ACTION_COMPLETE_REST, se.ACTION_CLOSE_FILLED]
    assert [leg.index for leg in out.filled] == [1, 3, 0] and [leg.index for leg in out.pending] == [2]
    assert "Complete the rest" in out.message and "close the filled legs" in out.message


def test_exiting_a_short_that_does_not_close_keeps_its_hedge_open():
    log: list = []
    out = _run(se.run(["SELL", "BUY"], _placer({0}, log), exiting=True))
    assert log == [0] and out.state == se.NOTHING_PLACED and out.legs[1].status == "NOT_PLACED"
    log2: list = []
    out2 = _run(se.run(["SELL", "SELL", "BUY"], _placer({1}, log2), exiting=True))
    assert log2 == [0, 1] and out2.state == se.INCOMPLETE and out2.actions == [se.ACTION_CLOSE_REST]
    assert "uncovered" in out2.legs[2].reasons[0]


# --- freeze slicing ---------------------------------------------------------------------------------------------------

def test_slices_are_whole_lots_strictly_below_the_freeze_and_add_up():
    assert freeze_limits.max_slice_units(3510, 65) == 53 * 65
    assert se.slice_units(100 * 65, 65, 3510) == [3445, 3055]
    assert se.slice_units(10 * 65, 65, 3510) == [650]
    assert se.slice_units(54 * 65, 65, 3510) == [3445, 65]                # exactly the limit is not sent as one order
    assert se.slice_units(500 * 30, 30, None) == [15000]                  # no known limit: never sliced
    rng = random.Random(7)
    for _ in range(300):
        lot = rng.choice([15, 25, 30, 50, 65, 75, 120])
        freeze = rng.randint(lot + 1, lot * 80)
        units = lot * rng.randint(1, 500)
        parts = se.slice_units(units, lot, freeze)
        assert sum(parts) == units and all(p % lot == 0 and 0 < p < freeze for p in parts)


def test_the_freeze_table_has_its_source_and_the_exchange_file_overrides_it(tmp_path, monkeypatch):
    freeze_limits.table.cache_clear()
    data = freeze_limits.table()
    assert "NSE" in str(data["source"]) and data["as_of"] and freeze_limits.freeze_limit("NIFTY 50") == freeze_limits.freeze_limit("NIFTY")
    assert freeze_limits.freeze_limit("SOMESTOCK") is None
    sheet = tmp_path / "qtyfreeze.csv"
    sheet.write_text("Quantity freeze limits\nSR.NO.,SYMBOL,VOL_FRZ_QTY\n1,RELIANCE,\"10,000\"\n2,NIFTY,3000\n3,BAD,x\n", encoding="utf-8")
    monkeypatch.setenv(freeze_limits.ENV_FILE, str(sheet))
    freeze_limits.table.cache_clear()
    try:
        assert freeze_limits.freeze_limit("RELIANCE") == 10000 and freeze_limits.freeze_limit("NIFTY") == 3000
        assert freeze_limits.freeze_limit("BAD") is None and "qtyfreeze.csv" in str(freeze_limits.table()["source"])
    finally:
        monkeypatch.delenv(freeze_limits.ENV_FILE)
        freeze_limits.table.cache_clear()


def _router(freeze, liquidity=None, monkeypatch=None):
    if liquidity is not None:
        from app.execution import paper_adapter
        real = paper_adapter.PaperExecutionAdapter.__init__

        def init(self, *a, **k):
            real(self, *a, **k)
            self.liquidity = liquidity
        monkeypatch.setattr(paper_adapter.PaperExecutionAdapter, "__init__", init)
    return OrderRouter(ExecutionMode.PAPER, RiskConfig(capital=100_000_000.0, risk_per_trade_pct=5.0, max_position_size_pct=100.0),
                       exchange="NFO", product="NRML", paper_execution="adapter", freeze_quantity=freeze)


def _sig(entry=100.0):
    return Signal(symbol="NIFTY30OCT2625000CE", strategy_id="manual_fno", strategy_name="x", direction=SignalDirection.LONG,
                  timestamp=datetime.now(timezone.utc), entry=entry, stop_loss=entry * 0.7, grade=SignalGrade.VALID)


def _spec(lot=65.0):
    return ContractSpec(symbol="NIFTY30OCT2625000CE", exchange="NFO", asset_class=AssetClass.INDEX_OPTION, description="x", lot_size=lot, tick_size=0.05)


def test_the_router_slices_a_large_order_into_one_position_at_the_same_fill():
    out = _run(_router(3510).execute(_sig(), TradingDayState(), contract_spec=_spec(), max_quantity=6500.0))
    plain = _run(_router(None).execute(_sig(), TradingDayState(), contract_spec=_spec(), max_quantity=6500.0))
    assert out.executed and out.trade.quantity == 6500.0 and out.trade.entry_price == plain.trade.entry_price
    assert "Sent as 2 orders below the exchange's freeze limit (3510 qty)" in out.reasons
    assert plain.reasons == [r for r in out.reasons if not r.startswith("Sent as")]


def test_a_slice_that_does_not_fill_stops_the_rest_and_the_position_is_what_filled(monkeypatch):
    calls = []

    def liquidity(order):
        calls.append(order.quantity)
        return order.quantity if len(calls) == 1 else 0.0
    out = _run(_router(3510, liquidity, monkeypatch).execute(_sig(), TradingDayState(), contract_spec=_spec(), max_quantity=6500.0 * 2))
    assert calls == [3445.0, 3445.0]                                     # the third slice was never sent
    assert out.executed and out.partial_fill and out.trade.quantity == 3445.0
    assert any("later slices were not sent" in r for r in out.reasons)


# --- TM-3: the order type where market orders are not allowed -------------------------------------------------------

def test_market_where_allowed_else_a_marketable_limit_with_the_users_band():
    assert se.order_type_for(True, "BUY", 100.0, 0.5) == ("MARKET", None)
    assert se.order_type_for(False, "BUY", 100.0, 0.5) == ("LIMIT", 100.5)
    assert se.order_type_for(False, "SELL", 100.0, 0.5) == ("LIMIT", 99.5)
    assert se.order_type_for(False, "BUY", 100.0, 2.0) == ("LIMIT", 102.0)   # the band is the setting, not a constant
    assert se.order_type_for(False, "SELL", 0.05, 5.0)[1] == 0.05           # never below one tick


# --- TM-10: margin -------------------------------------------------------------------------------------------------

ESTIMATE = {"with_hedge": {"value": 20000.0}, "without_hedge": {"value": None}, "benefit": None}


def test_the_brokers_margin_where_it_answers_else_the_labelled_estimate():
    broker = se.margin_view({"required": 30000.0, "without_hedge": 120000.0}, ESTIMATE, 100000.0, free_source="practice", broker_name="upstox")
    assert broker.source == "broker" and "upstox" in broker.label and broker.benefit == 90000.0 and broker.free_after == 70000.0
    est = se.margin_view(None, ESTIMATE, 100000.0, free_source="practice")
    assert est.source == "estimate" and est.label == se.ESTIMATE_LABEL and est.required == 20000.0 and est.free_after == 80000.0
    failed = se.margin_view({"error": "TimeoutError"}, ESTIMATE, None, free_source="practice")
    assert failed.source == "estimate" and failed.free_after is None and any("could not be read" in n for n in failed.notes)


class _MarginBroker:
    name = "upstox"

    def __init__(self, answer=None, boom=False):
        self.answer, self.boom, self.seen = answer, boom, None

    async def get_basket_margin(self, orders):
        self.seen = orders
        if self.boom:
            raise TimeoutError()
        return self.answer


def test_broker_basket_margin_through_the_adapter_and_its_failure():
    good = _MarginBroker(BasketMargin(required=1.0, without_hedge=2.0))
    assert _run(se.broker_basket_margin(good, ["o"])) == {"required": 1.0, "without_hedge": 2.0} and good.seen == ["o"]
    assert _run(se.broker_basket_margin(_MarginBroker(None), ["o"])) is None
    assert _run(se.broker_basket_margin(_MarginBroker(boom=True), ["o"])) == {"error": "TimeoutError"}
    assert _run(se.broker_basket_margin(None, ["o"])) is None


def test_the_base_adapter_has_no_basket_margin_and_the_wrappers_forward_it():
    from app.brokers.rate_budget import RateBudget, RateLimitedBroker, RateLimits
    from app.execution.paper_adapter import PaperExecutionAdapter
    assert _run(PaperExecutionAdapter().get_basket_margin([])) is None
    inner = _MarginBroker(BasketMargin(required=5.0))
    wrapped = RateLimitedBroker(inner, RateBudget(RateLimits(100.0, 100, 1000)))  # type: ignore[arg-type]
    assert _run(wrapped.get_basket_margin(["o"])).required == 5.0


# --- physical settlement ------------------------------------------------------------------------------------------

TODAY = date(2026, 10, 23)


def _h(right, strike, days, side="SELL", underlying="RELIANCE", qty=500.0):
    return se.Holding(trade_id=1, underlying=underlying, right=right, strike=strike, expiry=TODAY + timedelta(days=days), quantity=qty,
                      side=side, label=f"{underlying} {strike} {right}")


def test_settlement_flags_follow_the_users_settings_not_fixed_numbers():
    spots = {"RELIANCE": 2950.0}
    itm = se.settlement_flags([_h("CE", 2900.0, 2)], spots, TODAY, SETTINGS)
    assert itm[0]["state"] == "ITM" and itm[0]["delivery_value"] == 2900.0 * 500 and "deliver" in itm[0]["message"]
    assert "₹1,450,000" in itm[0]["message"]
    near = [_h("CE", 3000.0, 2)]                                          # 1.7% below the strike
    assert se.settlement_flags(near, spots, TODAY, SETTINGS)[0]["state"] == "NEAR_ITM"
    tight = se.SmartSettings(4, 1.0, True, 0.5)
    assert se.settlement_flags(near, spots, TODAY, tight) == []           # his band, not a constant
    far = [_h("CE", 2900.0, 6)]
    assert se.settlement_flags(far, spots, TODAY, SETTINGS) == []
    assert se.settlement_flags(far, spots, TODAY, se.SmartSettings(7, 2.0, True, 0.5))[0]["days_to_expiry"] == 6
    assert se.settlement_flags([_h("CE", 2900.0, -1)], spots, TODAY, SETTINGS) == []          # expired
    assert se.settlement_flags([_h("CE", 2900.0, 1, underlying="NIFTY")], {"NIFTY": 30000.0}, TODAY, SETTINGS) == []  # cash-settled index
    fut = se.settlement_flags([_h("FUT", None, 0, side="BUY")], spots, TODAY, SETTINGS)
    assert fut[0]["state"] == "FUTURE" and fut[0]["delivery_value"] == 2950.0 * 500 and "take delivery" in fut[0]["message"]
    put = se.settlement_flags([_h("PE", 3000.0, 1, side="BUY")], spots, TODAY, SETTINGS)
    assert put[0]["state"] == "ITM" and put[0]["obligation"] == "deliver"
    assert se.settlement_flags([_h("CE", 2900.0, 1)], {}, TODAY, SETTINGS) == []             # no price: not judged


def test_the_settings_are_the_users_own_prefs_with_bounds():
    d = prefs.defaults(se.NAMESPACE)
    assert se.SmartSettings.from_prefs(d) == SETTINGS
    for bad in ({"settlement_days_before": 31}, {"settlement_band_pct": -1.0}, {"protection_band_pct": 0.0}, {"other": 1}):
        with pytest.raises(Exception):
            prefs.SmartExecutionPrefs.model_validate({**d, **bad})


# --- through the pipeline: the basket with the flag ------------------------------------------------------------------

def test_routes_are_off_without_the_flag(condor):  # noqa: F811 - the fixture
    t = _org_tenant("se-off")
    out = client.post("/api/paper/fno/basket/close-filled", headers=t["headers"], json={"trade_ids": [1]})
    assert out.status_code == 404 and out.headers["X-Feature-Disabled"] == se.FLAG
    out = client.get("/api/paper/fno/settlement", headers=t["headers"])
    assert out.status_code == 404 and out.headers["X-Feature-Disabled"] == se.FLAG
    expiry, _, legs = condor
    view = _post(t, "preview", {"legs": _condor_legs(expiry, legs)})
    assert view["smart"] is None


def test_the_confirm_step_shows_the_order_slices_and_the_estimate_margin(condor):  # noqa: F811 - the fixture
    expiry, _, legs = condor
    t = _smart(_org_tenant("se-prev"))
    view = _post(t, "preview", {"legs": _condor_legs(expiry, legs)})
    smart = view["smart"]
    assert [s["side"] for s in smart["sequence"]] == ["BUY", "BUY", "SELL", "SELL"]
    assert [s["role"] for s in smart["sequence"]] == ["hedge", "hedge", "short", "short"]
    assert all(s["slices"] == [1] for s in smart["sequence"])
    margin = smart["margin"]
    assert margin["source"] == "estimate" and margin["label"] == se.ESTIMATE_LABEL and margin["free_source"] == "practice"
    assert margin["free_before"] is not None and margin["required"] is not None
    assert margin["free_after"] == pytest.approx(margin["free_before"] - margin["required"], abs=0.01)
    assert "sold legs close first" in smart["exit_rule"]


def test_the_brokers_own_margin_through_his_own_session(condor, monkeypatch):  # noqa: F811 - the fixture
    expiry, _, legs = condor
    t = _smart(_org_tenant("se-brk"))
    broker = _MarginBroker(BasketMargin(required=12345.0, without_hedge=99999.0))

    async def own(session, user):
        assert user.id == t["user_id"]
        return broker
    monkeypatch.setattr(fno_basket, "own_broker", own)
    margin = _post(t, "preview", {"legs": _condor_legs(expiry, legs)})["smart"]["margin"]
    assert margin["source"] == "broker" and margin["required"] == 12345.0 and margin["benefit"] == 99999.0 - 12345.0
    assert [o.transaction_type.value for o in broker.seen] == ["BUY", "BUY", "SELL", "SELL"]


def _failing_sell_call(monkeypatch):
    real = fno_ticket.place
    calls = []

    async def place(session, user, ask, **kw):
        calls.append((ask.side, ask.right))
        if ask.side == "SELL" and ask.right == "CE":
            return {"status": "REFUSED", "reasons": ["Broker refused"], "label": "x", "order_id": None, "trade_id": None, "filled": None}
        return await real(session, user, ask, **kw)
    monkeypatch.setattr(fno_ticket, "place", place)
    return calls


def test_a_failed_later_leg_gives_incomplete_then_close_the_filled_legs_shorts_first(condor, monkeypatch):  # noqa: F811 - the fixture
    expiry, _, legs = condor
    t = _smart(_org_tenant("se-inc"))
    calls = _failing_sell_call(monkeypatch)
    ref = uuid.uuid4().hex
    out = _post(t, "execute", {"legs": _condor_legs(expiry, legs), "basket_ref": ref})
    assert out["state"] == se.INCOMPLETE and out["status"] == "PARTIAL" and out["actions"] == ["complete_rest", "close_filled"]
    assert calls == [("BUY", "PE"), ("BUY", "CE"), ("SELL", "PE"), ("SELL", "CE")]
    assert [r["status"] for r in out["legs"]] == ["PLACED", "PLACED", "PLACED", "REFUSED"] and len(out["filled_trade_ids"]) == 3
    # A second tap never duplicates.
    again = _post(t, "execute", {"legs": _condor_legs(expiry, legs), "basket_ref": ref})
    assert again["state"] == se.INCOMPLETE and len(_orders(t["tenant_id"])) == 3

    closing = client.post("/api/paper/fno/basket/close-filled", headers=t["headers"], json={"trade_ids": out["filled_trade_ids"]})
    assert closing.status_code == 200, closing.text
    body = closing.json()
    assert body["state"] == se.COMPLETE and [r["side"] for r in body["legs"]] == ["SELL", "BUY", "BUY"]     # the short first
    assert all(r["status"] == "CLOSED" and r["exit_price"] is not None for r in body["legs"])

    async def open_count():
        async with _session_factory() as session:
            rows = await session.scalars(select(TradeRecord).where(TradeRecord.tenant_id == t["tenant_id"], TradeRecord.exit_time.is_(None)))
            return len(list(rows))
    assert _run(open_count()) == 0


def test_complete_the_rest_places_only_the_remaining_leg(condor, monkeypatch):  # noqa: F811 - the fixture
    expiry, _, legs = condor
    t = _smart(_org_tenant("se-rest"))
    _failing_sell_call(monkeypatch)
    out = _post(t, "execute", {"legs": _condor_legs(expiry, legs), "basket_ref": uuid.uuid4().hex})
    assert out["state"] == se.INCOMPLETE
    monkeypatch.undo()
    remaining = [_condor_legs(expiry, legs)[r["leg"]] for r in out["legs"] if r["status"] != "PLACED"]
    from tests.test_fno2_basket import _prices
    _prices(monkeypatch, {sym: p for _, (_, _, sym, p) in legs.items()})
    _flag(se.FLAG, t["tenant_id"])
    rest = _post(t, "execute", {"legs": remaining, "basket_ref": uuid.uuid4().hex})
    assert rest["state"] == se.COMPLETE and rest["placed"] == 1 and len(_orders(t["tenant_id"])) == 4


def test_the_kill_switch_refuses_the_basket_and_exits_still_run(condor):  # noqa: F811 - the fixture
    expiry, _, legs = condor
    t = _smart(_org_tenant("se-kill"))
    out = _post(t, "execute", {"legs": _condor_legs(expiry, legs)[2:], "basket_ref": uuid.uuid4().hex})   # the two hedges
    assert out["state"] == se.COMPLETE
    assert client.post("/api/kill-switch/tenant/engage", headers=t["headers"], json={"reason": "test"}).status_code == 200
    try:
        refused = _post(t, "execute", {"legs": _condor_legs(expiry, legs), "basket_ref": uuid.uuid4().hex})
        assert refused["status"] == "REFUSED" and len(_orders(t["tenant_id"])) == 2
        closing = client.post("/api/paper/fno/basket/close-filled", headers=t["headers"], json={"trade_ids": out["filled_trade_ids"]})
        assert closing.status_code == 200 and closing.json()["state"] == se.COMPLETE                 # exits are never blocked
    finally:
        client.post("/api/kill-switch/tenant/disengage", headers=t["headers"])


def test_close_filled_is_the_callers_own(condor):  # noqa: F811 - the fixture
    expiry, _, legs = condor
    t = _smart(_org_tenant("se-ten"))
    out = _post(t, "execute", {"legs": _condor_legs(expiry, legs)[2:3], "basket_ref": uuid.uuid4().hex})
    trade_id = out["filled_trade_ids"][0]
    other_org = _smart(_org_tenant("se-ten2"))
    assert client.post("/api/paper/fno/basket/close-filled", headers=other_org["headers"], json={"trade_ids": [trade_id]}).status_code == 404
    member = _member(t)
    assert client.post("/api/paper/fno/basket/close-filled", headers=member["headers"], json={"trade_ids": [trade_id]}).status_code == 403
    assert client.post("/api/paper/fno/basket/close-filled", json={"trade_ids": [trade_id]}).status_code == 401


# --- the settlement warning through the API and the worker step -----------------------------------------------------

def _stock_option(t: dict, *, strike: float, right: str, days: int, side: str = "SELL") -> int:
    from app.db.models import InstrumentRecord
    symbol = f"RELIANCE{uuid.uuid4().hex[:5].upper()}{int(strike)}{right}"
    expiry = datetime.now(timezone.utc).astimezone(__import__("zoneinfo").ZoneInfo("Asia/Kolkata")).date() + timedelta(days=days)

    async def go():
        async with _session_factory() as session:
            session.add(InstrumentRecord(broker="upstox", exchange="NFO", segment="NSE_FO", instrument_key=f"NSE_FO|{uuid.uuid4().hex[:10]}",
                                         tradingsymbol=symbol, name="RELIANCE", underlying="RELIANCE", instrument_type=right, expiry=expiry,
                                         strike=strike, lot_size=500, tick_size=0.05))
            trade = TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], mode="PAPER", symbol=symbol, strategy_id="manual_fno",
                                direction="SHORT" if side == "SELL" else "LONG", entry_time=datetime.now(timezone.utc), entry_price=20.0,
                                quantity=500.0, stop_loss=40.0, instrument_kind="OPTION", exchange="NFO", lot_size=500, expiry=expiry,
                                option_position="WRITE" if side == "SELL" else "BUY", underlying_symbol="RELIANCE")
            session.add(trade)
            await session.commit()
            return trade.id
    return _run(go())


def test_the_settlement_warning_on_positions_and_once_a_day_as_a_notification(monkeypatch):
    from tests.test_z3_approval_entries import _price
    t = _smart(_org_tenant("se-settle"))
    _price(monkeypatch, 2950.0)
    flagged = _stock_option(t, strike=2900.0, right="CE", days=2)
    _stock_option(t, strike=3500.0, right="CE", days=2)                  # far out of the money: not flagged
    out = client.get("/api/paper/fno/settlement", headers=t["headers"])
    assert out.status_code == 200, out.text
    body = out.json()
    assert [w["trade_id"] for w in body["warnings"]] == [flagged] and body["warnings"][0]["delivery_value"] == 2900.0 * 500
    assert body["settings"] == {"days_before": 4, "band_pct": 2.0, "telegram": True}

    async def send():
        async with _session_factory() as session:
            return await settlement_watch.send_due(session)
    assert _run(send()) >= 1
    _run(send())                                                          # the same day: no second notification

    async def notes():
        async with _session_factory() as session:
            return list(await session.scalars(select(NotificationRecord).where(
                NotificationRecord.tenant_id == t["tenant_id"], NotificationRecord.event_type == "SETTLEMENT_WARNING")))
    rows = _run(notes())
    assert len(rows) == 1 and rows[0].related_trade_id == flagged and rows[0].severity == "WARNING"

    # His own setting decides: days_before 1 hides a position expiring in 2 days.
    put = client.put("/api/me/prefs/smart_execution", headers=t["headers"],
                     json={**prefs.defaults(se.NAMESPACE), "settlement_days_before": 1})
    assert put.status_code == 200, put.text
    assert client.get("/api/paper/fno/settlement", headers=t["headers"]).json()["warnings"] == []
    # Another member sees none of it.
    member = _member(t)
    _flag(se.FLAG, t["tenant_id"])
    assert client.get("/api/paper/fno/settlement", headers=member["headers"]).json()["warnings"] == []


def test_nothing_here_places_or_blocks_an_order():
    """ADR-0006 / ADR-0004: the settlement watch and the margin view are information only."""
    import inspect
    for module in (settlement_watch, se):
        source = inspect.getsource(module)
        assert "place_order" not in source and "exit_now" not in source
    assert User is not None and OrderRecord is not None


def _creds():
    from app.brokers.models import BrokerCredentials
    return BrokerCredentials(api_key="k", api_secret="s", access_token="-".join(["t", "x"]))


def test_upstox_and_kite_basket_margin_parse_the_hedge_benefit(monkeypatch):
    from app.brokers.models import BrokerOrderRequest, Instrument
    from app.brokers.upstox import UpstoxBroker
    from app.brokers.zerodha import ZerodhaBroker
    from app.core.enums import OrderSide
    orders = [BrokerOrderRequest(symbol="A", exchange="NFO", transaction_type=OrderSide.BUY, quantity=75, product="NRML"),
              BrokerOrderRequest(symbol="B", exchange="NFO", transaction_type=OrderSide.SELL, quantity=75, product="NRML")]
    up = UpstoxBroker(_creds())
    sent = {}

    async def resolve(symbol, exchange):
        return Instrument(instrument_token=f"NSE_FO|{symbol}", exchange=exchange, tradingsymbol=symbol)

    async def up_request(method, path, **kw):
        sent["up"] = (method, path, kw["json"])
        return {"required_margin": 150000.0, "final_margin": 40000.0}
    monkeypatch.setattr(up, "_resolve_instrument", resolve)
    monkeypatch.setattr(up, "_request", up_request)
    got = _run(up.get_basket_margin(orders))
    assert got == BasketMargin(required=40000.0, without_hedge=150000.0)
    assert sent["up"][1] == "/charges/margin" and [i["instrument_key"] for i in sent["up"][2]["instruments"]] == ["NSE_FO|A", "NSE_FO|B"]

    kite = ZerodhaBroker(_creds())

    async def kite_request(method, path, **kw):
        sent["kite"] = (method, path, kw["json"])
        return {"initial": {"total": 160000.0}, "final": {"total": 42000.0}}
    monkeypatch.setattr(kite, "_request", kite_request)
    assert _run(kite.get_basket_margin(orders)) == BasketMargin(required=42000.0, without_hedge=160000.0)
    assert sent["kite"][1] == "/margins/basket" and [o["transaction_type"] for o in sent["kite"][2]] == ["BUY", "SELL"]

    async def empty(method, path, **kw):
        return {}
    monkeypatch.setattr(kite, "_request", empty)
    assert _run(kite.get_basket_margin(orders)) is None


def test_where_the_venue_refuses_market_orders_the_router_sends_a_marketable_limit(monkeypatch):
    from app.brokers.base import BrokerCapabilities
    from app.execution import paper_adapter
    seen = []
    real = paper_adapter.PaperExecutionAdapter.place_order

    async def spy(self, order):
        seen.append((order.order_type, order.price))
        return await real(self, order)
    monkeypatch.setattr(paper_adapter.PaperExecutionAdapter, "capabilities", BrokerCapabilities(market_orders_allowed=False))
    monkeypatch.setattr(paper_adapter.PaperExecutionAdapter, "place_order", spy)
    router = OrderRouter(ExecutionMode.PAPER, RiskConfig(capital=10_000_000.0, risk_per_trade_pct=5.0), exchange="NFO", product="NRML",
                         paper_execution="adapter", market_protection_pct=2.0)
    out = _run(router.execute(_sig(100.0), TradingDayState(), contract_spec=_spec(), max_quantity=65.0))
    assert seen == [("LIMIT", 102.0)] and out.executed and out.trade.entry_price <= 102.0


def test_the_ticket_passes_the_users_own_protection_band(condor, monkeypatch):  # noqa: F811 - the fixture
    from app.execution import signal_execution
    expiry, _, legs = condor
    t = _smart(_org_tenant("se-band"))
    assert client.put("/api/me/prefs/smart_execution", headers=t["headers"],
                      json={**prefs.defaults(se.NAMESPACE), "protection_band_pct": 1.5}).status_code == 200
    seen = {}
    real = signal_execution.execute_signal_for_user

    async def spy(*a, **kw):
        seen.update(kw)
        return await real(*a, **kw)
    monkeypatch.setattr(signal_execution, "execute_signal_for_user", spy)
    out = _post(t, "execute", {"legs": _condor_legs(expiry, legs)[2:3], "basket_ref": uuid.uuid4().hex})
    assert out["state"] == se.COMPLETE and seen["market_protection_pct"] == 1.5 and seen["paper_execution"] == "adapter"
