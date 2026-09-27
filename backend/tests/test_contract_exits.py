"""Phase F4: exits for derived-contract trades - the strategy's underlying levels decide, the
contract price books; premium floor/ceiling as the safety net; futures on their own levels;
per-kind charge profiles; the monitor's two quotes; LIVE exit on the derivatives exchange."""
import asyncio
from datetime import date, datetime, timezone
from typing import Dict, Optional

from app.core.enums import OrderSide
from app.db.models import TradeRecord
from app.execution.paper_broker import PaperBroker
from app.trading.exit_logic import check_contract_exit, underlying_exit
from app.trading.position_monitor import close_position, monitor_open_positions, underlying_exchange
from tests.test_auth_api import _session_factory
from tests.test_position_monitor import _Broker, _load, _trade, _user


def _run(coro):
    return asyncio.run(coro)


def _option(user, *, bought=True, position="BUY", **overrides) -> int:
    """A NIFTY 24500 CE bought at 120 (floor 84) on a LONG NIFTY 50 signal: stop 24460, T1 24610,
    T2 24700 - or the written mirror (sold PE at 100, ceiling 150) on the same LONG signal."""
    fields = dict(
        symbol="NIFTY 24500 CE 01 OCT 26" if bought else "NIFTY 24500 PE 01 OCT 26", direction="LONG" if bought else "SHORT",
        entry_price=120.0 if bought else 100.0, quantity=75, stop_loss=84.0 if bought else 150.0, target1=None, target2=None,
        instrument_kind="OPTION", exchange="NFO", lot_size=75, option_position=position, premium_stop_pct=30.0 if bought else 50.0,
        underlying_symbol="NIFTY 50", underlying_direction="LONG", underlying_stop_loss=24460.0, underlying_target1=24610.0,
        underlying_target2=24700.0, expiry=date(2026, 10, 1),
    )
    fields.update(overrides)
    return _trade(user, **fields)


def _rec(**kw) -> TradeRecord:
    base = dict(direction="LONG", stop_loss=84.0, target1=None, target2=None, instrument_kind="OPTION", underlying_direction="LONG",
                underlying_stop_loss=24460.0, underlying_target1=24610.0, underlying_target2=24700.0)
    base.update(kw)
    return TradeRecord(**base)


def test_underlying_exit_priority_and_missing_targets():
    assert underlying_exit("LONG", 24460.0, 24610.0, 24700.0, 24450.0) == "Stop Loss"
    assert underlying_exit("LONG", 24460.0, 24610.0, 24700.0, 24705.0) == "Target 2"
    assert underlying_exit("LONG", 24460.0, 24610.0, 24700.0, 24620.0) == "Target 1"
    assert underlying_exit("LONG", 24460.0, 24610.0, 24700.0, 24550.0) is None
    assert underlying_exit("SHORT", 24564.0, 24414.0, None, 24570.0) == "Stop Loss"
    assert underlying_exit("SHORT", 24564.0, 24414.0, None, 24400.0) == "Target 1"
    assert underlying_exit("LONG", 24460.0, None, None, 25000.0) is None       # stop only, never a target exit
    assert underlying_exit("LONG", None, 24610.0, None, 24000.0) is None


def test_bought_option_exits_on_underlying_levels_at_contract_price():
    t = _rec()
    assert check_contract_exit(t, 95.0, 24455.0) == ("Stop Loss (underlying)", 95.0)
    assert check_contract_exit(t, 210.0, 24615.0) == ("Target 1 (underlying)", 210.0)
    assert check_contract_exit(t, 260.0, 24720.0) == ("Target 2 (underlying)", 260.0)
    assert check_contract_exit(t, 130.0, 24550.0) is None
    # Premium floor fires regardless of the underlying, including with no underlying quote.
    assert check_contract_exit(t, 83.5, 24550.0) == ("Premium floor", 83.5)
    assert check_contract_exit(t, 80.0, None) == ("Premium floor", 80.0)
    assert check_contract_exit(t, 130.0, None) is None


def test_written_option_exits_on_ceiling_or_underlying_levels():
    t = _rec(direction="SHORT", stop_loss=150.0)   # sold PE on a LONG signal
    assert check_contract_exit(t, 151.0, 24550.0) == ("Premium ceiling", 151.0)
    assert check_contract_exit(t, 140.0, 24450.0) == ("Stop Loss (underlying)", 140.0)
    assert check_contract_exit(t, 40.0, 24615.0) == ("Target 1 (underlying)", 40.0)
    assert check_contract_exit(t, 90.0, 24550.0) is None


def test_future_and_cash_use_their_own_levels():
    fut = TradeRecord(direction="SHORT", stop_loss=24632.0, target1=24482.0, target2=None, instrument_kind="FUTURE")
    assert check_contract_exit(fut, 24640.0, None) == ("Stop Loss", 24632.0)
    assert check_contract_exit(fut, 24480.0, 99999.0) == ("Target 1", 24482.0)
    cash = TradeRecord(direction="LONG", stop_loss=98.0, target1=104.0, target2=None, instrument_kind="UNDERLYING")
    assert check_contract_exit(cash, 104.5, None) == ("Target 1", 104.0)


def test_cost_profiles_differ_by_instrument_kind():
    pb = PaperBroker()
    equity = pb.estimate_round_trip_costs(100.0, 104.0, 100)
    option = pb.estimate_round_trip_costs(120.0, 150.0, 75, "OPTION")
    future = pb.estimate_round_trip_costs(24500.0, 24600.0, 75, "FUTURE")
    assert equity == pb.estimate_round_trip_costs(100.0, 104.0, 100, "UNDERLYING")
    # Options: 0.1% STT on the sell leg (150*75=11250 -> 11.25) + 0.035% exchange on 20250 turnover (7.09) + brokerage 40 + GST...
    assert 55 < option < 70
    # Futures: 0.02% STT on the sell leg (24600*75 -> 369) dominates.
    assert 480 < future < 580


def test_underlying_exchange_mapping():
    assert underlying_exchange("NIFTY 50") == "NSE" and underlying_exchange("RELIANCE") == "NSE"
    assert underlying_exchange("SENSEX") == "BSE"


def test_monitor_quotes_underlying_and_contract_for_option_trades():
    user = _user("fx-monitor@example.com")
    ce = _option(user)                                  # exits when NIFTY >= 24610 or premium <= 84
    pe_written = _option(user, bought=False, position="WRITE")
    cash = _trade(user, symbol="RELIANCE")              # LONG at 100, T1 104
    prices: Dict[str, float] = {"NIFTY 50": 24615.0, "NIFTY 24500 CE 01 OCT 26": 205.0, "NIFTY 24500 PE 01 OCT 26": 60.0, "RELIANCE": 101.0}
    seen = []

    async def lookup(symbol, exchange):
        seen.append((symbol, exchange))
        return prices[symbol]

    async def go():
        async with _session_factory() as session:
            return await monitor_open_positions(session, user.tenant_id, lookup)
    outcomes = {o.trade_id: o for o in _run(go())}
    assert outcomes[ce].closed and outcomes[ce].exit_reason == "Target 1 (underlying)" and outcomes[ce].exit_price == 205.0
    assert outcomes[pe_written].closed and outcomes[pe_written].exit_reason == "Target 1 (underlying)" and outcomes[pe_written].exit_price == 60.0
    assert cash not in outcomes or not outcomes[cash].closed
    assert ("NIFTY 24500 CE 01 OCT 26", "NFO") in seen and ("NIFTY 50", "NSE") in seen
    closed = _load(ce)
    assert closed.pnl == round((205.0 - 120.0) * 75 - closed.charges, 2) and closed.charges > 0
    written = _load(pe_written)
    assert written.pnl == round((100.0 - 60.0) * 75 - written.charges, 2)


def test_monitor_falls_back_to_premium_check_when_underlying_quote_fails():
    user = _user("fx-monitor-fallback@example.com")
    ce = _option(user)
    calls = {"underlying": 0}

    async def lookup(symbol, exchange):
        if symbol == "NIFTY 50":
            calls["underlying"] += 1
            raise ConnectionError("index feed down")
        return 80.0   # premium at the floor

    async def go():
        async with _session_factory() as session:
            return await monitor_open_positions(session, user.tenant_id, lookup)
    outcomes = {o.trade_id: o for o in _run(go())}
    assert calls["underlying"] == 1
    assert outcomes[ce].closed and outcomes[ce].exit_reason == "Premium floor" and outcomes[ce].exit_price == 80.0


def test_live_option_exit_places_sell_on_nfo_after_cancelling_the_floor_stop():
    user = _user("fx-live-exit@example.com")
    ce = _option(user, mode="LIVE", broker_order_id="ORD-ENTRY", sl_order_id="ORD-SL")
    broker = _Broker(exit_fill=204.0)

    async def go():
        async with _session_factory() as session:
            trade = await session.get(TradeRecord, ce)
            return await close_position(session, trade, 205.0, "Target 1 (underlying)", broker=broker)
    outcome = _run(go())
    assert outcome.closed and outcome.exit_price == 204.0
    exits = [o for o in broker.placed if o.order_type == "MARKET"]
    assert exits and exits[-1].exchange == "NFO" and exits[-1].transaction_type == OrderSide.SELL and exits[-1].quantity == 75
    assert "ORD-SL" in broker.cancelled


def test_mark_price_endpoint_takes_underlying_price_for_options():
    from tests.test_auth_api import client
    user = _user("fx-mark@example.com")
    ce = _option(user)
    token = client.post("/api/auth/login", json={"email": user.email, "password": "S3cur3Pass!"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    untouched = client.post(f"/api/positions/{ce}/mark-price", headers=headers, json={"current_price": 130.0, "underlying_price": 24550.0}).json()
    assert untouched["closed"] is False
    closed = client.post(f"/api/positions/{ce}/mark-price", headers=headers, json={"current_price": 95.0, "underlying_price": 24450.0}).json()
    assert closed["closed"] is True and closed["exit_reason"] == "Stop Loss (underlying)" and closed["exit_price"] == 95.0
