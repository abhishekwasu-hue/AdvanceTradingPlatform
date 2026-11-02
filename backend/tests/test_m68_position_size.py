"""M68 PR-2 (owner order M68, TS1-017 .. 024, TS1-088): Position size - no pre-chosen size.

Per value: Not set => a ticket asks / an automated entry places nothing and says "Needs your setting: Lots"; saved =>
used; the order's / the bot's own size wins. A size below one lot places nothing (never rounded up). A Secondary paper
account sizes only by the user's own setting. A derivative row of the instrument master with no lot size is never read
as one lot. The migration keeps every stored value (TS-6) and goes back down.
"""
import asyncio
import importlib.util
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import select

from app.accounts import fanout_plan as fp
from app.core.enums import NotificationType, SignalDirection, SignalGrade
from app.core.models import RiskConfig, Signal
from app.db.models import NotificationRecord, OrderRecord, PaperSubAccountRecord, RiskSettingsRecord, StrategyDeploymentRecord, TradeRecord, User
from app.deployments import why
from app.execution import position_size as ps_mod
from app.execution.position_size import PositionSize, quantity_for
from app.execution.signal_execution import execute_signal_for_user
from app.instruments import master
from app.me import trade_settings as ts
from app.paper import ticket as plan_ticket
from app.risk_engine import risk_manager
from app.risk_engine.risk_manager import RiskManager, TradingDayState
from tests.test_auth_api import _session_factory, client
from tests.test_fno1_chain_ticket import _org_tenant
from tests.test_z5_p1d_risk_by_default import _tenant_t  # an organisation with no risk settings (DEF-0)

BACKEND = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.real_position_size        # M68: each user's own (empty) Position size
URL = "/api/me/trade-settings"
NEEDS = risk_manager.NEEDS_LOTS
SIZE_KEYS = ("mode", "lots", "quantity", "capital_pct", "fanout_mode", "fanout_value")


def _run(coro):
    return asyncio.run(coro)


def _save(t: dict, **values) -> None:
    out = client.put(URL, headers=t["headers"], json={"values": {f"position_size.{k}": v for k, v in values.items()}})
    assert out.status_code == 200, out.text


def _load(t: dict) -> PositionSize:
    async def go():
        async with _session_factory() as session:
            return await ps_mod.load(session, await session.get(User, t["user_id"]))
    return _run(go())


def _signal(symbol="RELIANCE", entry=100.0) -> Signal:
    return Signal(symbol=symbol, strategy_id="m68ps", strategy_name="m68ps", timestamp=datetime.now(UTC), direction=SignalDirection.LONG,
                  entry=entry, stop_loss=entry - 5, target1=entry + 10, grade=SignalGrade.A1, confidence=0.8, reasons=["test"])


def _execute(t: dict, **kw):
    async def go():
        async with _session_factory() as session:
            user = await session.get(User, t["user_id"])
            result, order = await execute_signal_for_user(session, user, mode="PAPER", strategy_id="m68ps", signal=_signal(),
                                                          idempotency_key=f"m68ps:{uuid.uuid4().hex}", holding="INTRADAY",
                                                          order_style="MARKET", **kw)
            trade = await session.get(TradeRecord, order.trade_id) if order.trade_id else None
            return result, order, (float(trade.quantity) if trade is not None else None)
    return _run(go())


# --- the registry: every Position size control is wired and starts Not set ------------------------------------------------

def test_every_position_size_control_is_wired_and_not_set_for_a_new_user():
    t = _org_tenant("m68ps-reg")
    wired = {f.name for f in ts.FIELDS if f.section == "position_size" and f.wired}
    assert wired == set(SIZE_KEYS)
    assert ts.BY_KEY["position_size.lot_size"].fact and not ts.BY_KEY["position_size.lot_size"].wired     # a fact, shown only
    fields = {f["key"]: f for s in client.get(URL, headers=t["headers"]).json()["sections"] for f in s["fields"]}
    for name in SIZE_KEYS:
        f = fields[f"position_size.{name}"]
        assert f["value"] is None and f["saved"] is False and f["wired"] is True, name
    assert _load(t) == PositionSize()
    rows = {r for f in ts.FIELDS if f.section == "position_size" for r in f.rows}
    assert {f"TS1-{n:03d}" for n in (17, 18, 19, 20, 21, 22, 23, 24, 88)} <= rows


def test_saved_values_are_read_and_an_older_lots_for_a_new_bot_is_kept():
    t = _org_tenant("m68ps-load")
    # TS-6: "Lots for a new bot" he saved earlier in My trading defaults is his - kept; Trade settings wins once saved there
    assert client.put("/api/me/prefs/trading_defaults", headers=t["headers"], json={"max_lots": 4}).status_code == 200
    assert _load(t).lots == 4
    _save(t, mode="FIXED_QUANTITY", lots=2, quantity=30, capital_pct=5, fanout_mode="FIXED", fanout_value=3)
    assert _load(t) == PositionSize("FIXED_QUANTITY", 2, 30, 5.0, "FIXED", 3.0)


# --- the size of one entry (pure) -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("ps, unit, entry, capital, risk_set, want", [
    (PositionSize(), 1, 100, 1e6, False, (None, NEEDS)),                                  # nothing set: ask, never one lot
    (PositionSize(), 75, 100, 1e6, True, (None, None)),                                   # his risk per trade sizes it (as before)
    (PositionSize(lots=3), 75, 100, 1e6, False, (225.0, None)),                           # lots without a mode: fixed lots
    (PositionSize(mode="FIXED_LOTS", lots=2), 50, None, None, True, (100.0, None)),
    (PositionSize(mode="FIXED_QUANTITY", quantity=160), 75, 100, None, False, (150.0, None)),  # whole lots, rounded DOWN
    (PositionSize(quantity=40), 1, 100, None, False, (40.0, None)),                        # a cash share: its own count
    (PositionSize(mode="PCT_CAPITAL", capital_pct=10), 1, 250, 100_000, False, (40.0, None)),   # 10,000 / 250
    (PositionSize(mode="PCT_CAPITAL", capital_pct=10), 75, 200, 1_000_000, False, (450.0, None)),  # 1,00,000 / 15,000 = 6 lots
])
def test_the_size_from_his_own_setting(ps, unit, entry, capital, risk_set, want):
    assert quantity_for(ps, unit=unit, entry=entry, capital=capital, risk_set=risk_set) == want


@pytest.mark.parametrize("ps, unit, entry, capital, risk_set, words", [
    (PositionSize(mode="FIXED_LOTS"), 75, 100, 1e6, False, "Needs your setting: Lots (Settings › Trade settings › Position size)"),
    (PositionSize(mode="FIXED_QUANTITY"), 75, 100, 1e6, False, "Needs your setting: Quantity"),
    (PositionSize(mode="FIXED_QUANTITY", quantity=50), 75, 100, 1e6, False, "is less than one lot (75)"),
    (PositionSize(mode="PCT_CAPITAL"), 75, 100, 1e6, False, "Needs your setting: Share of capital"),
    (PositionSize(mode="PCT_CAPITAL", capital_pct=1), 75, 200, 100_000, False, "is less than one lot"),
    (PositionSize(mode="PCT_CAPITAL", capital_pct=5), 75, 100, None, False, "Your capital is not known"),
    (PositionSize(mode="PCT_CAPITAL", capital_pct=5), 75, None, 1e6, False, "has no single price"),
    (PositionSize(mode="RISK_BASED", lots=2), 75, 100, 1e6, False, "Needs your setting: Risk per trade (Settings › Risk limits)"),
])
def test_a_size_his_setting_cannot_give_places_nothing_and_says_why(ps, unit, entry, capital, risk_set, words):
    quantity, reason = quantity_for(ps, unit=unit, entry=entry, capital=capital, risk_set=risk_set)
    assert quantity is None and words in reason
    assert quantity_for(PositionSize(mode="RISK_BASED"), unit=75, entry=100, capital=1e6, risk_set=True) == (None, None)


def test_the_words_and_the_why_code():
    assert NEEDS == "Needs your setting: Lots (Settings › Trade settings › Position size) - nothing was placed"
    assert ps_mod.NEEDS_LOTS_TEXT == NEEDS and ps_mod.needs_setting("quantity").startswith("Needs your setting: Quantity (")
    assert why.held_back(f"x; {NEEDS}") == "needs_setting"


# --- the risk engine: no size of his own = refused, never one lot -----------------------------------------------------------

def test_the_risk_engine_refuses_an_entry_with_no_size_and_no_risk_per_trade():
    for direction, stop in ((SignalDirection.LONG, 95.0), (SignalDirection.SHORT, 0.0)):        # a short with no stop too (M64 RM-2)
        sig = _signal().model_copy(update={"direction": direction, "stop_loss": stop, "target1": None})
        decision = RiskManager(RiskConfig()).validate_and_size(sig, TradingDayState())
        assert not decision.approved and decision.reasons == [NEEDS] and decision.quantity == 0
        assert RiskManager(RiskConfig()).validate_and_size(sig, TradingDayState(), requested_quantity=6).quantity == 6
    # risk per trade of his own: sized by it, as before (no size needed)
    sized = RiskManager(RiskConfig(capital=100_000, risk_per_trade_pct=1.0)).validate_and_size(_signal(), TradingDayState())
    assert sized.approved and sized.quantity == 200                                      # 1,000 / 5
    # a backtest only (a test on past prices, M68Q-PR2-1) keeps the old one lot
    assert RiskManager(RiskConfig(), one_lot_if_unsized=True).validate_and_size(_signal(), TradingDayState()).quantity == 1
    assert RiskConfig().lot_size is None                                                 # TS1-018: no cash unit pre-set


# --- the order path: nothing set => not placed and said; his size => placed at it ------------------------------------------

def test_an_automated_entry_with_no_size_places_nothing_and_says_which_setting():
    t = _tenant_t("m68ps-none")
    result, order, quantity = _execute(t)
    assert result.executed is False and NEEDS in result.reasons and quantity is None
    assert order.status == "REJECTED" and json.loads(order.reasons_json) == [NEEDS]

    async def notes():
        async with _session_factory() as session:
            return list(await session.scalars(select(NotificationRecord).where(
                NotificationRecord.tenant_id == t["tenant_id"], NotificationRecord.related_order_id == order.id)))
    found = _run(notes())
    assert found and found[0].event_type == NotificationType.REJECTION.value and NEEDS in found[0].message


def test_his_saved_size_is_used_and_the_orders_own_size_wins():
    t = _tenant_t("m68ps-own")
    _save(t, lots=3)
    assert _execute(t)[2] == 3.0                                                         # a cash share: 3 x 1
    _save(t, mode="FIXED_QUANTITY", quantity=7)
    assert _execute(t)[2] == 7.0
    assert _execute(t, max_quantity=2)[2] == 2.0                                         # the order's own size wins


def test_a_bots_own_lots_size_a_bot_that_trades_the_symbol_itself():
    t = _tenant_t("m68ps-bot")

    async def bot(lots):
        async with _session_factory() as session:
            dep = StrategyDeploymentRecord(tenant_id=t["tenant_id"], strategy_id=f"m68ps-{lots}", symbol="RELIANCE", mode="PAPER",
                                           holding="INTRADAY", order_style="MARKET", instrument_kind="UNDERLYING", max_lots=lots,
                                           status="PAUSED", created_by=t["user_id"])
            session.add(dep)
            await session.commit()
            return dep.id
    assert _execute(t, deployment_id=_run(bot(5)))[2] == 5.0
    result = _execute(t, deployment_id=_run(bot(None)))[0]                               # no lots on the bot, none saved
    assert result.executed is False and NEEDS in result.reasons


# --- the plan ticket: no cap and no quantity => it asks -------------------------------------------------------------------

def test_the_plan_ticket_asks_for_the_quantity_when_nothing_sizes_it():
    from app.risk_engine import by_default
    from tests.test_z5_p1d_risk_by_default import _flag as _org_flag
    from tests.test_z5_p1d_risk_by_default import _preview
    t = _tenant_t("m68ps-plan")
    _org_flag(by_default.FLAG, t["tenant_id"])
    view = _preview(t)
    assert view["sizing"]["cap"] is None and view["sizing"]["quantity"] == 0 and view["can_place"] is False
    assert plan_ticket.QUANTITY_ASK in view["errors"]
    typed = _preview(t, quantity=4)
    assert typed["sizing"]["quantity"] == 4 and plan_ticket.QUANTITY_ASK not in typed["errors"]
    _save(t, quantity=9)
    assert _preview(t)["sizing"]["quantity"] == 9


# --- extra paper accounts (TS1-024) ---------------------------------------------------------------------------------------

def test_an_extra_account_sizes_by_its_own_else_his_setting_else_it_is_skipped():
    assert ps_mod.fanout_sizing(PositionSize(), None, None) == (None, None)
    assert ps_mod.fanout_sizing(PositionSize(fanout_mode="FIXED", fanout_value=2.0), None, None) == ("FIXED", 2.0)
    assert ps_mod.fanout_sizing(PositionSize(fanout_mode="FIXED", fanout_value=2.0), "MULTIPLIER", 0.5) == ("MULTIPLIER", 0.5)
    secondary, primary = fp.Account(id=2, role=fp.SECONDARY), fp.Account(id=1, role=fp.PRIMARY)
    assert fp.lots_for(secondary, fp.Sizing(), primary_lots=4, margin_per_lot=None) == (0, fp.NEEDS_SIZING)
    assert fp.lots_for(primary, fp.Sizing(), primary_lots=4, margin_per_lot=None) == (4, None)      # the order's own lots
    assert fp.lots_for(secondary, fp.Sizing(fp.FIXED, 2), primary_lots=4, margin_per_lot=None) == (2, None)
    table = PaperSubAccountRecord.__table__
    for name in ("sizing_mode", "sizing_value"):
        assert table.c[name].nullable and table.c[name].default is None and table.c[name].server_default is None, name


# --- the instrument master (TS1-088) --------------------------------------------------------------------------------------

def test_a_derivative_with_no_lot_size_is_never_read_as_one_lot(caplog):
    assert master._lot_of(75, "CE") == 75 and master._lot_of("50", "FUT") == 50
    for missing in (None, "", 0, "x"):
        assert master._lot_of(missing, "PE") is None and master._lot_of(missing, "EQ") == 1     # a cash share: one (fact)
    rows = [{"instrument_key": "NSE_FO|1", "trading_symbol": "NIFTY X CE", "segment": "NSE_FO", "instrument_type": "CE",
             "strike_price": 24500, "lot_size": None, "underlying_symbol": "NIFTY", "expiry": 1798934400000},
            {"instrument_key": "NSE_FO|2", "trading_symbol": "NIFTY Y CE", "segment": "NSE_FO", "instrument_type": "CE",
             "strike_price": 24600, "lot_size": 75, "underlying_symbol": "NIFTY", "expiry": 1798934400000},
            {"instrument_key": "NSE_EQ|3", "trading_symbol": "RELIANCE", "segment": "NSE_EQ", "instrument_type": "EQ"}]
    with caplog.at_level("WARNING"):
        out = master.parse_upstox_master(rows, "NSE")
    assert [(r.tradingsymbol, r.lot_size) for r in out] == [("NIFTY Y CE", 75), ("RELIANCE", 1)]
    assert "1 futures / options rows had no lot size" in caplog.text


# --- the migration (up / down on a test DB; existing rows keep their values) ----------------------------------------------

def _migration():
    path = BACKEND / "alembic/versions/b4e6f8a0c2d5_m68_position_size_not_set.py"
    spec = importlib.util.spec_from_file_location("m68_pr2_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_keeps_every_stored_value_and_goes_down():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    module = _migration()
    assert module.down_revision == "aaa362c38a32" and module.ROLLBACK_SAFE is False
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE risk_settings (id INTEGER PRIMARY KEY, lot_size INTEGER NOT NULL)"))
        conn.execute(sa.text("CREATE TABLE paper_sub_accounts (id INTEGER PRIMARY KEY, label VARCHAR(50), "
                             "sizing_mode VARCHAR(12) NOT NULL DEFAULT 'MULTIPLIER', sizing_value FLOAT NOT NULL DEFAULT '1')"))
        conn.execute(sa.text("INSERT INTO risk_settings (id, lot_size) VALUES (1, 10), (2, 1)"))
        conn.execute(sa.text("INSERT INTO paper_sub_accounts (id, label, sizing_mode, sizing_value) VALUES (1, 'A', 'FIXED', 3), (2, 'B', 'MULTIPLIER', 1)"))
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
        assert [tuple(r) for r in conn.execute(sa.text("SELECT id, lot_size FROM risk_settings ORDER BY id"))] == [(1, 10), (2, 1)]
        assert [tuple(r) for r in conn.execute(sa.text("SELECT id, sizing_mode, sizing_value FROM paper_sub_accounts ORDER BY id"))] == \
            [(1, "FIXED", 3.0), (2, "MULTIPLIER", 1.0)]                                                     # TS-6: kept
        conn.execute(sa.text("INSERT INTO risk_settings (id) VALUES (3)"))
        conn.execute(sa.text("INSERT INTO paper_sub_accounts (id, label) VALUES (3, 'C')"))
        assert conn.execute(sa.text("SELECT lot_size FROM risk_settings WHERE id = 3")).scalar() is None
        assert tuple(conn.execute(sa.text("SELECT sizing_mode, sizing_value FROM paper_sub_accounts WHERE id = 3")).one()) == (None, None)
        with Operations.context(MigrationContext.configure(conn)):
            module.downgrade()
        assert conn.execute(sa.text("SELECT lot_size FROM risk_settings WHERE id = 3")).scalar() == 1
        assert tuple(conn.execute(sa.text("SELECT sizing_mode, sizing_value FROM paper_sub_accounts WHERE id = 3")).one()) == ("MULTIPLIER", 1.0)
        cols = {c["name"]: c for c in sa.inspect(conn).get_columns("paper_sub_accounts")}
        assert not cols["sizing_mode"]["nullable"] and not cols["sizing_value"]["nullable"]


def test_the_model_carries_no_size_default():
    col = RiskSettingsRecord.__table__.c["lot_size"]
    assert col.nullable and col.default is None and col.server_default is None


def test_no_order_row_of_a_refused_entry_carries_a_trade():
    t = _tenant_t("m68ps-trail")
    _execute(t)

    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(OrderRecord).where(OrderRecord.tenant_id == t["tenant_id"])))
    assert all(o.trade_id is None for o in _run(go()))


# --- multi-leg structures (TS1-017): no size = refused and said; his size is the ceiling of a risk-based size ---------------

def _structure(t: dict, risk: RiskConfig, key: str):
    from app.core.enums import OptionStrategy
    from app.execution.multileg import execute_structure
    from app.instruments.spreads import resolve_structure
    from tests.test_contract_rules import TODAY, _load_master
    from tests.test_multileg import SPOT, _OptionBroker, _rules
    from tests.test_trading_worker import _signal as worker_signal
    _load_master()

    async def go():
        async with _session_factory() as session:
            user = await session.get(User, t["user_id"])
            structure = await resolve_structure(session, "NIFTY 50", _rules(), OptionStrategy.BULL_PUT_SPREAD, SignalDirection.LONG,
                                                spread_width=2, spot=SPOT, today=TODAY)
            return await execute_structure(session, user, mode="PAPER", strategy_id="ema_rsi_scalper_1m", signal=worker_signal(),
                                           structure=structure, rules=_rules(), target_credit_pct=None, stop_credit_pct=None,
                                           idempotency_key=key, quote_broker=_OptionBroker(), risk_config=risk, holding="INTRADAY")
    return _run(go())


@pytest.mark.usefixtures("no_open_positions_from_earlier_tests", "owner_switched_live_on")
def test_a_structure_with_no_size_is_refused_with_the_setting_it_needs_and_a_notice():
    t = _tenant_t("m68ps-ml-none")
    result = _structure(t, RiskConfig(), f"m68ps-ml-{uuid.uuid4().hex}")
    assert result.executed is False and result.reasons[-1] == NEEDS
    assert all(o.status == "REJECTED" for o in result.orders)

    async def notes():
        async with _session_factory() as session:
            return list(await session.scalars(select(NotificationRecord).where(
                NotificationRecord.tenant_id == t["tenant_id"], NotificationRecord.message == NEEDS)))
    assert len(_run(notes())) == 1


@pytest.mark.usefixtures("no_open_positions_from_earlier_tests", "owner_switched_live_on")
def test_his_fixed_lots_cap_a_risk_based_structure_size():
    from tests.master_fixture import NIFTY_LOT
    t = _tenant_t("m68ps-ml-cap")
    _save(t, mode="FIXED_LOTS", lots=1)
    # risk 10,000 / max loss per lot 4,500 -> 2 lots by risk; his own 1 lot is the ceiling (as on the single-leg path)
    result = _structure(t, RiskConfig(capital=1_000_000, risk_per_trade_pct=1.0), f"m68ps-ml-{uuid.uuid4().hex}")
    assert result.executed, result.reasons

    async def legs():
        async with _session_factory() as session:
            return list(await session.scalars(select(TradeRecord).where(TradeRecord.tenant_id == t["tenant_id"])))
    rows = _run(legs())
    assert len(rows) == 2 and all(float(r.quantity) == NIFTY_LOT for r in rows)


# --- the adapters' instrument dumps (TS1-088): a derivative with no lot size never becomes one lot -------------------------

def _no_lot_rows(instruments) -> list[tuple[str, float | None]]:
    return [(i.tradingsymbol, i.lot_size) for i in instruments]


def test_zerodha_dump_with_no_lot_size_is_left_out_of_the_master(caplog):
    import httpx

    from app.brokers.models import BrokerCredentials
    from app.brokers.zerodha import ZerodhaBroker
    from tests.test_brokers import _mock_client
    csv_text = ("instrument_token,exchange,tradingsymbol,name,segment,instrument_type,lot_size,tick_size,expiry,strike\n"
                "1,NFO,NIFTY24DEC22000CE,NIFTY,NFO-OPT,CE,,0.05,2024-12-26,22000\n"
                "2,NFO,NIFTY24DEC22100CE,NIFTY,NFO-OPT,CE,50,0.05,2024-12-26,22100\n"
                "3,NSE,RELIANCE,RELIANCE,NSE,EQ,,0.05,,0\n")
    broker = ZerodhaBroker(BrokerCredentials(api_key="k", access_token="t"),
                           client=_mock_client(lambda r: httpx.Response(200, text=csv_text), ZerodhaBroker.BASE_URL))
    got = _run(broker.get_instruments("NFO"))
    assert _no_lot_rows(got) == [("NIFTY24DEC22000CE", None), ("NIFTY24DEC22100CE", 50.0), ("RELIANCE", None)]
    with caplog.at_level("WARNING"):
        rows = master.parse_broker_instruments("zerodha", got)
    assert [(r.tradingsymbol, r.lot_size) for r in rows] == [("NIFTY24DEC22100CE", 50), ("RELIANCE", 1)]
    assert "1 futures / options rows had no lot size" in caplog.text


def test_shoonya_dump_with_no_lot_size_is_left_out_of_the_master():
    from app.brokers.models import BrokerCredentials
    from app.brokers.shoonya import ShoonyaBroker
    broker = ShoonyaBroker(BrokerCredentials(client_id="FA12345", api_secret="pw", api_key="k", access_token="t"))

    async def post(path, body):
        return {"values": [{"token": "1", "exch": "NFO", "tsym": "NIFTY26DECFUT", "instname": "FUTIDX", "ls": ""},
                           {"token": "2", "exch": "NFO", "tsym": "NIFTY26JANFUT", "instname": "FUTIDX", "ls": "75"}]}
    broker._post = post
    got = _run(broker.get_instruments("NFO"))
    assert _no_lot_rows(got) == [("NIFTY26DECFUT", None), ("NIFTY26JANFUT", 75.0)]
    from app.brokers.models import Instrument
    fut = [Instrument(**{**i.model_dump(), "instrument_type": "FUT"}) for i in got]
    assert [(r.tradingsymbol, r.lot_size) for r in master.parse_broker_instruments("shoonya", fut)] == [("NIFTY26JANFUT", 75)]


def test_upstox_dump_with_no_lot_size_is_left_out_of_the_master():
    import gzip

    import httpx

    from app.brokers.models import BrokerCredentials
    from app.brokers.upstox import UpstoxBroker
    from tests.test_brokers import _mock_client
    dump = gzip.compress(json.dumps([
        {"instrument_key": "NSE_FO|1", "exchange": "NSE", "trading_symbol": "NIFTY X CE", "segment": "NSE_FO", "instrument_type": "CE",
         "strike_price": 24500},
        {"instrument_key": "NSE_FO|2", "exchange": "NSE", "trading_symbol": "NIFTY Y CE", "segment": "NSE_FO", "instrument_type": "CE",
         "strike_price": 24600, "lot_size": 75},
    ]).encode())
    broker = UpstoxBroker(BrokerCredentials(api_key="k", access_token="t"),
                          client=_mock_client(lambda r: httpx.Response(200, content=dump), UpstoxBroker.BASE_URL))
    got = _run(broker._download_instruments("NSE"))
    assert _no_lot_rows(got) == [("NIFTY X CE", None), ("NIFTY Y CE", 75.0)]
    assert [(r.tradingsymbol, r.lot_size) for r in master.parse_broker_instruments("upstox", got)] == [("NIFTY Y CE", 75)]


def test_kotak_neo_scrip_row_with_no_lot_size_is_left_out_of_the_master():
    from app.brokers.kotak_neo import instrument_from_row
    base = {"pSymbol": "1", "pTrdSymbol": "NIFTY26DEC24500CE", "pSymbolName": "NIFTY", "pOptionType": "CE", "dStrikePrice": "2450000",
            "dTickSize": "5", "pExpiryDate": ""}
    none = instrument_from_row({**base, "lLotSize": ""}, "nse_fo")
    own = instrument_from_row({**base, "pSymbol": "2", "pTrdSymbol": "NIFTY26DEC24600CE", "lLotSize": "75"}, "nse_fo")
    assert none is not None and none.lot_size is None and own is not None and own.lot_size == 75.0
    assert [(r.tradingsymbol, r.lot_size) for r in master.parse_broker_instruments("kotak_neo", [none, own])] == [(own.tradingsymbol, 75)]
