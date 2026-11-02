"""M68 PR-1 (owner order M68, TS1-001 .. 016): Order defaults - no pre-chosen product, order type, holding, price
protection, stop-limit gap or trigger.

Per value: Not set => the ticket asks / a bot skips with "Needs your setting: <field>" and places nothing; saved => used;
the order's / bot's own value wins. Exits are never blocked (ADR-0004): an exit with nothing saved uses the position's
own product as a market close. The migration keeps every stored bot value (TS-6) and goes back down.
"""
import asyncio
import importlib.util
import json
import random
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy import select

from app.brokers.base import BrokerCapabilities, stop_order_params
from app.core.enums import OrderSide, SignalDirection, SignalGrade
from app.core.models import RiskConfig, Signal
from app.db.models import OrderRecord, StrategyDeploymentRecord, TradeRecord, User
from app.deployments import why
from app.execution import order_defaults
from app.execution import router as router_mod
from app.execution.products import HoldingNotSet, product_for, product_for_trade
from app.execution.signal_execution import execute_signal_for_user
from app.me import trade_settings as ts
from app.risk_engine.risk_manager import TradingDayState
from tests.test_auth_api import _session_factory, client
from tests.test_fno1_chain_ticket import _contract, _expiry, _org_tenant
from tests.test_z3_approval_entries import _price

BACKEND = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.real_order_defaults      # M68: each user's own (empty) Order defaults
URL = "/api/me/trade-settings"
ORDER_KEYS = ("holding", "product", "order_type", "price_protection_pct", "stop_limit_band_pct", "trigger_mode", "trigger_source")


def _run(coro):
    return asyncio.run(coro)


def _save(t: dict, **values) -> None:
    out = client.put(URL, headers=t["headers"], json={"values": {f"order_defaults.{k}": v for k, v in values.items()}})
    assert out.status_code == 200, out.text


def _user(t: dict) -> User:
    async def go():
        async with _session_factory() as session:
            return await session.get(User, t["user_id"])
    return _run(go())


def _defaults(t: dict) -> order_defaults.OrderDefaults:
    async def go():
        async with _session_factory() as session:
            return await order_defaults.load(session, await session.get(User, t["user_id"]))
    return _run(go())


@pytest.fixture
def setup(monkeypatch):
    t = _org_tenant("m68")
    expiry = _expiry()
    strike = float(random.randrange(30000, 40000, 50))
    _contract(expiry, strike)
    _price(monkeypatch, 100.0)
    return t, expiry, strike


def _ticket(expiry, strike, **kw) -> dict:
    body = {"underlying": "NIFTY", "expiry": expiry.isoformat(), "strike": strike, "right": "CE", "side": "BUY", "lots": 1}
    body.update(kw)
    return body


# --- the registry: every Order default is wired and starts Not set --------------------------------------------------------

def test_every_order_default_is_wired_and_not_set_for_a_new_user(setup):
    t, _, _ = setup
    wired = {f.name for f in ts.FIELDS if f.section == "order_defaults" and f.wired}
    assert wired == set(ORDER_KEYS)
    fields = {f["key"]: f for s in client.get(URL, headers=t["headers"]).json()["sections"] for f in s["fields"]}
    for name in ORDER_KEYS:
        f = fields[f"order_defaults.{name}"]
        assert f["value"] is None and f["saved"] is False and f["wired"] is True, name
    assert _defaults(t) == order_defaults.OrderDefaults()
    rows = {r for f in ts.FIELDS if f.section == "order_defaults" for r in f.rows}
    assert {f"TS1-{n:03d}" for n in range(1, 17) if n != 9} <= rows       # TS1-009 is a delete (no field)


def test_saved_values_are_read_and_an_older_saved_ticket_default_is_kept(setup):
    t, _, _ = setup
    _save(t, holding="SWING", order_type="PROTECTED_LIMIT", price_protection_pct=0.7, stop_limit_band_pct=1.5,
          trigger_mode="CLOSE", trigger_source="SPOT")
    got = _defaults(t)
    assert (got.holding, got.order_type, got.price_protection_pct, got.stop_limit_band_pct, got.trigger_mode, got.trigger_source) == \
        ("SWING", "PROTECTED_LIMIT", 0.7, 1.5, "CLOSE", "SPOT")
    assert got.bot_order_style == "PROTECTED_LIMIT" and got.ticket_order_type is None    # a ticket asks for its own type
    assert got.product is None
    # TS-6: a product he saved earlier in My trading defaults is his - kept; Trade settings wins once he saves one there.
    assert client.put("/api/me/prefs/trading_defaults", headers=t["headers"], json={"ticket": {"product": "NRML"}}).status_code == 200
    assert _defaults(t).product == "NRML"
    _save(t, product="MIS")
    assert _defaults(t).product == "MIS"


def test_needs_setting_words_and_the_why_code():
    text = order_defaults.needs_setting("holding")
    assert text == "Needs your setting: Holding (Settings › Trade settings › Order defaults)"
    assert why.held_back(f"x; {text}") == "needs_setting" and why.held_back("Daily loss limit") == "risk_setting"
    assert "needs_setting" in why.CODES


# --- products: no INTRADAY fallback for an entry; an exit uses the position's own product ---------------------------------

def test_an_entry_needs_a_holding_and_an_exit_uses_the_position_record():
    with pytest.raises(HoldingNotSet):
        product_for(None, "OPTION", "NFO")
    assert product_for("SWING", "OPTION", "NFO") == "NRML"
    assert product_for_trade(TradeRecord(holding="SWING", instrument_kind="FUTURE", exchange="NFO")) == "NRML"
    assert product_for_trade(TradeRecord(holding=None, instrument_kind="OPTION", exchange="NFO")) == "MIS"   # exit never blocked


# --- the router: no MIS / MARKET / 0.5 % chosen for an entry --------------------------------------------------------------

def _signal(symbol="RELIANCE", entry=100.0) -> Signal:
    return Signal(symbol=symbol, strategy_id="m68", strategy_name="m68", timestamp=datetime.now(UTC), direction=SignalDirection.LONG,
                  entry=entry, stop_loss=entry - 5, target1=entry + 10, grade=SignalGrade.A1, confidence=0.8, reasons=["test"])


@pytest.mark.parametrize("kw, words", [
    ({"order_style": "MARKET"}, "Holding"),
    ({"product": "MIS"}, "Order type"),
    ({"product": "MIS", "order_style": "PROTECTED_LIMIT"}, "Price protection"),
])
def test_the_router_places_no_entry_without_his_values(kw, words):
    router = router_mod.OrderRouter(mode=router_mod.ExecutionMode.PAPER, risk_config=RiskConfig(), **kw)
    result = _run(router.execute(_signal(), TradingDayState(), max_quantity=1))
    assert result.executed is False and any(r.startswith(f"Needs your setting: {words}") for r in result.reasons), result.reasons


def test_the_router_uses_his_values_when_set():
    router = router_mod.OrderRouter(mode=router_mod.ExecutionMode.PAPER, risk_config=RiskConfig(), product="MIS",
                                    order_style="PROTECTED_LIMIT", market_protection_pct=0.7)
    result = _run(router.execute(_signal(), TradingDayState(), max_quantity=1))
    assert result.executed is True, result.reasons
    assert any("0.7% past 100" in r for r in result.reasons)


def test_a_venue_without_market_orders_asks_for_his_band():
    class NoMarket:
        name, paper = "nomarket", True
        capabilities = BrokerCapabilities(market_orders_allowed=False)
    router = router_mod.OrderRouter(mode=router_mod.ExecutionMode.PAPER, risk_config=RiskConfig(), product="MIS", order_style="MARKET")
    router.paper_venue = NoMarket()       # type: ignore[assignment]
    result = _run(router._route(_signal(), TradingDayState(), 1, [], None, 0.0))
    assert result.executed is False and result.reasons == [order_defaults.needs_setting("price_protection_pct")]


def test_a_protective_stop_uses_his_gap_else_the_exit_fallback():
    caps = BrokerCapabilities(stop_market=True, stop_market_on_options=False)
    _, fallback = stop_order_params(caps, "NIFTY24OCT25000CE", OrderSide.SELL, 100.0, is_option=True, limit_band_pct=None)
    assert fallback == 99.0                                              # 1 %: the exit fallback (never blocked)

    class Adapter:
        capabilities = caps
        stop_order_params = router_mod.BrokerInterface.stop_order_params
    with order_defaults.stop_band(5.0):
        kind, limit = Adapter().stop_order_params("NIFTY24OCT25000CE", OrderSide.SELL, 100.0, is_option=True)
    assert kind == "SL" and limit == 95.0
    assert order_defaults.STOP_BAND.get() is None
    assert order_defaults.EXIT_CLOSE_BAND_PCT == router_mod.DEFAULT_MARKET_PROTECTION_PCT


# --- the signal pipeline: automated entries skip with plain words; his values / the caller's win --------------------------

def _execute(t: dict, **kw):
    async def go():
        async with _session_factory() as session:
            user = await session.get(User, t["user_id"])
            return await execute_signal_for_user(session, user, mode="PAPER", strategy_id="m68", signal=_signal(),
                                                 idempotency_key=f"m68:{uuid.uuid4().hex}", max_quantity=1, **kw)
    return _run(go())


def _trade_holding(trade_id: int) -> str:
    async def go():
        async with _session_factory() as session:
            return (await session.get(TradeRecord, trade_id)).holding
    return _run(go())


def test_an_automated_entry_with_nothing_set_places_nothing_and_says_which_setting(setup):
    t, _, _ = setup
    result, order = _execute(t)
    assert result.executed is False and order.status == "REJECTED" and order.trade_id is None
    reasons = json.loads(order.reasons_json)
    assert order_defaults.needs_setting("holding") in reasons and order_defaults.needs_setting("order_type") in reasons


def test_saved_values_are_used_and_the_callers_own_value_wins(setup):
    t, _, _ = setup
    _save(t, holding="SWING", order_type="MARKET")
    result, order = _execute(t)
    assert result.executed is True, result.reasons
    assert _trade_holding(order.trade_id) == "SWING"                          # his Trade setting
    result, order = _execute(t, holding="INTRADAY")
    assert result.executed is True and _trade_holding(order.trade_id) == "INTRADAY"   # the bot's / order's own wins


def test_a_bot_order_style_he_set_for_tickets_only_is_not_used_for_a_bot(setup):
    t, _, _ = setup
    _save(t, holding="INTRADAY", order_type="LIMIT")
    result, order = _execute(t)
    assert result.executed is False and order_defaults.needs_setting("order_type") in json.loads(order.reasons_json)


# --- the chain ticket: asks for what is Not set; his saved values fill it; the ticket's own value wins ---------------------

def test_the_ticket_asks_for_product_and_order_type_when_not_set(setup):
    t, expiry, strike = setup
    out = client.post("/api/paper/fno/order/preview", headers=t["headers"], json=_ticket(expiry, strike))
    assert out.status_code == 200, out.text
    view = out.json()
    assert view["missing"] == ["product", "order_type"] and view["can_place"] is False
    assert "Choose Market, Limit, SL or SL-M" in view["errors"] and "Choose MIS (intraday) or NRML (carry forward)" in view["errors"]
    placed = client.post("/api/paper/fno/order", headers=t["headers"], json=_ticket(expiry, strike, client_ref=uuid.uuid4().hex)).json()
    assert placed["status"] == "REFUSED" and placed["order_id"] is None


def test_the_ticket_uses_his_saved_values_and_its_own_value_wins(setup):
    t, expiry, strike = setup
    _save(t, product="NRML", order_type="MARKET")
    view = client.post("/api/paper/fno/order/preview", headers=t["headers"], json=_ticket(expiry, strike)).json()
    assert view["missing"] == [] and view["product"] == "NRML" and view["order_type"] == "MARKET" and view["can_place"], view
    placed = client.post("/api/paper/fno/order", headers=t["headers"],
                         json=_ticket(expiry, strike, product="MIS", client_ref=uuid.uuid4().hex)).json()
    assert placed["status"] == "PLACED", placed
    assert _trade_holding(placed["trade_id"]) == "INTRADAY"                  # the ticket's MIS, not his NRML


def test_a_waiting_order_asks_for_its_trigger_when_not_set(setup):
    t, expiry, strike = setup
    body = _ticket(expiry, strike, order_type="LIMIT", product="MIS", price=50.0, rest_if_waiting=True, client_ref=uuid.uuid4().hex)
    view = client.post("/api/paper/fno/order/preview", headers=t["headers"], json=body).json()
    assert "Choose whether the order fires on a touch or on a candle close." in view["errors"]
    _save(t, trigger_mode="TOUCH")
    view = client.post("/api/paper/fno/order/preview", headers=t["headers"], json=body).json()
    assert view["errors"] == [] and view["resting"] is True and view["trigger_mode"] == "TOUCH", view


def test_a_basket_without_a_product_asks_and_his_saved_product_fills_it(setup):
    t, expiry, strike = setup
    leg = {"underlying": "NIFTY", "expiry": expiry.isoformat(), "strike": strike, "right": "CE", "side": "BUY", "lots": 1}
    view = client.post("/api/paper/fno/basket/preview", headers=t["headers"], json={"legs": [leg]}).json()
    assert view["product"] is None and view["can_execute"] is False
    assert any("Choose MIS (intraday) or NRML (carry forward)" in e for row in view["legs"] for e in row["errors"])
    _save(t, product="MIS")
    view = client.post("/api/paper/fno/basket/preview", headers=t["headers"], json={"legs": [leg]}).json()
    assert view["product"] == "MIS"


# --- exits are never blocked ----------------------------------------------------------------------------------------------

@pytest.mark.real_position_size        # M68 PR-2: with an EMPTY Position size the exit still closes (ADR-0004)
def test_an_exit_with_nothing_set_still_closes_and_an_armed_exit_watches_its_own_price(setup):
    t, expiry, strike = setup
    placed = client.post("/api/paper/fno/order", headers=t["headers"],
                         json=_ticket(expiry, strike, product="NRML", order_type="MARKET", client_ref=uuid.uuid4().hex)).json()
    assert placed["status"] == "PLACED", placed
    assert _defaults(t) == order_defaults.OrderDefaults()                      # nothing saved at all
    armed = client.post("/api/paper/fno/resting/exit", headers=t["headers"],
                        json={"trade_id": placed["trade_id"], "kind": "STOP", "order_type": "SL-M", "level": 60.0,
                              "client_ref": uuid.uuid4().hex})
    assert armed.status_code == 200, armed.text
    assert armed.json()["trigger"]["mode"] == "TOUCH" and armed.json()["trigger"]["source"] == "PREMIUM"
    out = client.post(f"/api/positions/{placed['trade_id']}/exit-now", headers=t["headers"])
    assert out.status_code == 200, out.text

    async def closed():
        async with _session_factory() as session:
            return (await session.get(TradeRecord, placed["trade_id"])).exit_time
    assert _run(closed()) is not None


# --- bots: the form's value, else his Trade settings, else Not set (skips with the reason) ---------------------------------

def _bot(t: dict, **kw) -> dict:
    body = {"strategy_id": "rsi_adx_momentum_5m", "symbol": f"M68{uuid.uuid4().hex[:5].upper()}", "exchange": "NSE", "timeframe": "1min",
            "mode": "PAPER"}
    body.update(kw)
    out = client.post("/api/deployments", headers=t["headers"], json=body)
    assert out.status_code in (200, 201), out.text
    created = out.json()
    client.post(f"/api/deployments/{created['id']}/stop", headers=t["headers"], json={"reason": "test done"})
    return created


def test_a_new_bot_keeps_not_set_or_takes_his_own_settings_and_its_own_value_wins(setup):
    t, _, _ = setup
    bare = _bot(t)
    assert bare["holding"] is None and bare["order_style"] is None
    _save(t, holding="INTRADAY", order_type="PROTECTED_LIMIT", price_protection_pct=0.4)
    mine = _bot(t)
    assert (mine["holding"], mine["order_style"], mine["market_protection_pct"]) == ("INTRADAY", "PROTECTED_LIMIT", 0.4)
    own = _bot(t, order_style="MARKET")
    assert own["order_style"] == "MARKET"


def test_a_saved_carry_forward_that_does_not_fit_the_bot_leaves_its_holding_not_set(setup):
    t, _, _ = setup
    _save(t, holding="SWING", order_type="MARKET")
    bot = _bot(t)                                     # an intraday-candle strategy: Carry forward does not fit it
    assert bot["holding"] is None and bot["order_style"] == "MARKET"


def test_a_protected_limit_bot_with_no_band_anywhere_is_asked_for_it(setup):
    t, _, _ = setup
    out = client.post("/api/deployments", headers=t["headers"], json={
        "strategy_id": "rsi_adx_momentum_5m", "symbol": "M68BAND", "exchange": "NSE", "timeframe": "1min", "mode": "PAPER",
        "order_style": "PROTECTED_LIMIT"})
    assert out.status_code == 422 and out.json()["detail"][0]["loc"] == ["body", "market_protection_pct"]


def test_the_worker_reads_the_bots_holding_else_his_setting_else_skips(setup):
    from app.workers.trading_worker import TradingWorker
    t, _, _ = setup
    worker = TradingWorker(_session_factory, cycle_seconds=60)
    user = _user(t)

    def holding(own=None, timeframe="5min"):
        dep = SimpleNamespace(id=random.randint(10**6, 10**7), holding=own, timeframe=timeframe, option_strategy="SINGLE")

        async def go():
            async with _session_factory() as session:
                return await worker._bot_holding(session, dep, user)
        return _run(go())
    assert holding() == (None, order_defaults.needs_setting("holding"))         # Not set anywhere: the bot skips
    _save(t, holding="SWING")
    got, text = holding()
    assert got is None and "does not fit this bot" in text                      # Carry forward on an intraday-candle bot
    assert holding(timeframe="day") == ("SWING", "")                           # his setting on a daily bot
    assert holding(own="INTRADAY") == ("INTRADAY", "")                         # the bot's own value wins


# --- the plan ticket ------------------------------------------------------------------------------------------------------

def test_the_plan_ticket_takes_its_holding_from_the_plan_else_his_setting():
    from app.paper import ticket
    assert ticket.Plan(symbol="X", direction="LONG", setup="OTHER", entry=1, stop_loss=0.9, target=1.2).holding is None
    from app.paper.ticket_routes import PlanBody
    body = PlanBody(symbol="X", direction="LONG", setup="OTHER", entry=1, stop_loss=0.9, target=1.2, holding="SWING")
    assert body.plan().holding == "SWING"


# --- the migration (up / down on a test DB; existing rows keep their values) ----------------------------------------------

def _migration():
    path = BACKEND / "alembic/versions/8d2f4a6c0e19_m68_order_defaults_not_set.py"
    spec = importlib.util.spec_from_file_location("m68_pr1_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_keeps_every_stored_value_and_goes_down():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    module = _migration()
    assert module.down_revision == "a7d3e5c9f214" and module.ROLLBACK_SAFE is False
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE strategy_deployments (id INTEGER PRIMARY KEY, symbol VARCHAR(20), "
                             "holding VARCHAR(10) NOT NULL DEFAULT 'INTRADAY', order_style VARCHAR(20) NOT NULL DEFAULT 'MARKET')"))
        conn.execute(sa.text("INSERT INTO strategy_deployments (id, symbol, holding, order_style) VALUES "
                             "(1, 'A', 'SWING', 'PROTECTED_LIMIT'), (2, 'B', 'INTRADAY', 'MARKET')"))
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
        rows = conn.execute(sa.text("SELECT id, holding, order_style FROM strategy_deployments ORDER BY id")).all()
        assert [tuple(r) for r in rows] == [(1, "SWING", "PROTECTED_LIMIT"), (2, "INTRADAY", "MARKET")]     # TS-6: kept
        conn.execute(sa.text("INSERT INTO strategy_deployments (id, symbol) VALUES (3, 'C')"))
        assert tuple(conn.execute(sa.text("SELECT holding, order_style FROM strategy_deployments WHERE id = 3")).one()) == (None, None)
        cols = {c["name"]: c for c in sa.inspect(conn).get_columns("strategy_deployments")}
        assert cols["holding"]["nullable"] and cols["order_style"]["nullable"] and cols["holding"]["default"] is None
        with Operations.context(MigrationContext.configure(conn)):
            module.downgrade()
        rows = conn.execute(sa.text("SELECT id, holding, order_style FROM strategy_deployments ORDER BY id")).all()
        assert [tuple(r) for r in rows] == [(1, "SWING", "PROTECTED_LIMIT"), (2, "INTRADAY", "MARKET"), (3, "INTRADAY", "MARKET")]
        cols = {c["name"]: c for c in sa.inspect(conn).get_columns("strategy_deployments")}
        assert not cols["holding"]["nullable"] and not cols["order_style"]["nullable"]


def test_the_model_carries_no_default_for_holding_or_order_style():
    table = StrategyDeploymentRecord.__table__
    for name in ("holding", "order_style"):
        col = table.c[name]
        assert col.nullable and col.default is None and col.server_default is None, name
    assert TradeRecord.__table__.c["holding"].server_default is not None     # a trade's holding is its record (TS1-101)


def test_no_order_row_is_left_with_a_guessed_product(setup):
    """Nothing in this PR guesses: the orders of a refused entry carry no trade (a quick sanity read of the trail)."""
    t, _, _ = setup
    _execute(t)

    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(OrderRecord).where(OrderRecord.tenant_id == t["tenant_id"])))
    assert all(o.trade_id is None for o in _run(go()))
