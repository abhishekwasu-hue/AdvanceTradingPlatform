"""TM-2 / TT-7: one function per trigger rule, shared by the trigger engine, the position monitor's level checks and both
backtest engines - touch / close, the stop first inside one bar, no look-ahead - and the PaperExecutionAdapter's
resting orders (same fill and cost model as an immediate order)."""
import asyncio
import inspect
import random
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.backtest import options_engine
from app.backtest.models import TouchFill
from app.brokers.models import BrokerOrderRequest
from app.core.enums import OrderSide
from app.execution.paper_adapter import PaperExecutionAdapter
from app.paper.fno_ticket import paper_fill
from app.trading import exit_logic, trigger_rules
from app.trading.trigger_rules import ABOVE, BELOW, closed_beyond, closed_candles, close_trigger, order_trigger_side, touched

IST = timezone(timedelta(hours=5, minutes=30))


def _run(coro):
    return asyncio.run(coro)


# --- the rules ---------------------------------------------------------------------------------------------------------

def test_touch_and_close_read_the_level_from_its_side():
    assert touched(100, ABOVE, 95, 100) and not touched(100, ABOVE, 95, 99.95)
    assert touched(100, BELOW, 100, 105) and not touched(100, BELOW, 100.05, 105)
    assert closed_beyond(100, ABOVE, 100) and not closed_beyond(100, ABOVE, 99.9)
    assert closed_beyond(100, BELOW, 99) and not closed_beyond(100, BELOW, 100.1)


def test_the_order_side_gives_the_trigger_side_as_an_exchange_does():
    assert order_trigger_side("BUY", "LIMIT") == BELOW and order_trigger_side("SELL", "LIMIT") == ABOVE
    assert order_trigger_side("BUY", "SL") == ABOVE and order_trigger_side("SELL", "SL-M") == BELOW
    with pytest.raises(ValueError):
        order_trigger_side("BUY", "MARKET")


def test_on_a_tick_the_touch_rule_is_exactly_the_paper_fill_rule():
    """Parity: the engine's 'reached' on one price == the ticket's / adapter's 'fills now' (paper_fill)."""
    rng = random.Random(7)
    for _ in range(2000):
        side = rng.choice(("BUY", "SELL"))
        kind = rng.choice(("LIMIT", "SL-M"))
        level = round(rng.uniform(50, 150), 2)
        ltp = round(rng.uniform(50, 150), 2)
        price, trigger = (level, None) if kind == "LIMIT" else (None, level)
        fills = paper_fill(kind, side, ltp, price, trigger)[0] is not None
        assert touched(level, order_trigger_side(side, kind), ltp, ltp) == fills, (side, kind, level, ltp)


def test_inside_one_bar_the_stop_counts_first_in_every_engine():
    # One bar reaches both the stop (95) and the target (110) of a long: the stop wins (pessimistic, TT-7).
    bar = {"open": 100.0, "low": 94.0, "high": 111.0, "close": 105.0}
    assert TouchFill().exit("LONG", 95.0, 110.0, None, bar) == ("Stop Loss", 95.0)
    assert exit_logic.determine_exit_price("SHORT", 105.0, 90.0, None, 89.0, 106.0) == ("Stop Loss", 105.0)
    assert exit_logic.underlying_exit("LONG", 95.0, 110.0, None, 94.0) == "Stop Loss"
    assert trigger_rules.stop_first(["LIMIT", "TARGET", "STOP"]) == ["STOP", "TARGET", "LIMIT"]


def test_the_monitor_and_backtest_level_checks_equal_the_shared_touch_rule():
    rng = random.Random(11)
    for _ in range(2000):
        direction = rng.choice(("LONG", "SHORT"))
        stop, t1 = (90.0, 110.0) if direction == "LONG" else (110.0, 90.0)
        low = rng.uniform(80, 120)
        high = low + rng.uniform(0, 20)
        got = exit_logic.determine_exit_price(direction, stop, t1, None, low, high)
        stop_side, target_side = (BELOW, ABOVE) if direction == "LONG" else (ABOVE, BELOW)
        want = ("Stop Loss" if touched(stop, stop_side, low, high) else "Target 1" if touched(t1, target_side, low, high) else None)
        assert (got[0] if got else None) == want


def test_both_backtests_call_the_shared_functions():
    assert "touched(" in inspect.getsource(exit_logic.determine_exit_price)
    assert "touched(" in inspect.getsource(exit_logic.underlying_exit)
    assert "closed_beyond(" in inspect.getsource(options_engine)
    assert "exit_logic" in inspect.getsource(__import__("app.backtest.models", fromlist=["x"]))


def _minutes(start: datetime, closes):
    return [SimpleNamespace(timestamp=start + timedelta(minutes=i), low=c - 1, high=c + 1, close=c) for i, c in enumerate(closes)]


def test_close_reads_closed_candles_of_his_timeframe_only():
    day = datetime(2026, 10, 23, 9, 15, tzinfo=IST).astimezone(timezone.utc)
    bars = _minutes(day, [100, 101, 102, 103, 104, 105, 106, 107])        # 09:15 .. 09:22
    now = day + timedelta(minutes=7, seconds=30)                           # 09:22:30: the 09:22 bar is forming
    candles = closed_candles(bars, 5, now)
    assert [c.close for c in candles] == [104]                             # 09:15-09:20 closed; 09:20-09:25 forming
    assert candles[0].low == 99 and candles[0].high == 105
    assert closed_candles(bars, 1, now)[-1].close == 106                   # the forming 09:22 bar is never read


def test_close_has_no_look_ahead():
    day = datetime(2026, 10, 23, 9, 15, tzinfo=IST).astimezone(timezone.utc)
    now = day + timedelta(minutes=5)
    past = _minutes(day, [100, 100, 100, 100, 100])
    future = past + _minutes(day + timedelta(minutes=5), [130, 130, 130, 130, 130])
    assert closed_candles(past, 5, now) == closed_candles(future, 5, now)
    assert close_trigger(120, ABOVE, future, 5, now) is None               # the future candle is not visible yet
    assert close_trigger(120, ABOVE, future, 5, now + timedelta(minutes=5)).close == 130


def test_close_ignores_a_candle_that_closed_before_the_order_was_armed():
    day = datetime(2026, 10, 23, 9, 15, tzinfo=IST).astimezone(timezone.utc)
    bars = _minutes(day, [130] * 5 + [100] * 5)
    now = day + timedelta(minutes=10)
    assert close_trigger(120, ABOVE, bars, 5, now, armed_at=None).close == 130
    assert close_trigger(120, ABOVE, bars, 5, now, armed_at=day + timedelta(minutes=6)) is None


# --- the PAPER adapter's resting orders --------------------------------------------------------------------------------

def _limit(side=OrderSide.BUY, price=95.0, **kw):
    return BrokerOrderRequest(symbol="NIFTYX", exchange="NFO", transaction_type=side, quantity=75, order_type="LIMIT", price=price, **kw)


def test_an_immediate_order_fills_exactly_as_before_with_resting_on():
    for request in (BrokerOrderRequest(symbol="NIFTYX", exchange="NFO", transaction_type=OrderSide.BUY, quantity=75),
                    _limit(price=101.0), _limit(side=OrderSide.SELL, price=99.0)):
        old, new = PaperExecutionAdapter(), PaperExecutionAdapter(keep_resting=True)
        for a in (old, new):
            a.mark("NIFTYX", 100.0, "NFO")
        r_old, r_new = _run(old.place_order(request)), _run(new.place_order(request))
        assert (r_old.status, r_old.raw) == (r_new.status, r_new.raw) and r_old.status == "COMPLETE"
        assert old.charges(r_old.order_id) == new.charges(r_new.order_id)


def test_a_waiting_order_is_refused_without_resting_and_rests_with_it():
    old = PaperExecutionAdapter()
    old.mark("NIFTYX", 100.0, "NFO")
    assert _run(old.place_order(_limit())).status == "REJECTED"           # unchanged for every caller before TM-2
    new = PaperExecutionAdapter(keep_resting=True)
    new.mark("NIFTYX", 100.0, "NFO")
    rested = _run(new.place_order(_limit()))
    assert rested.status == "OPEN" and rested.raw == {"resting": True} and new.resting() == [rested.order_id]
    assert _run(new.on_price("NIFTYX", 96.0, "NFO")) == [] and new.order(rested.order_id).status == "OPEN"
    filled = _run(new.on_price("NIFTYX", 94.5, "NFO"))
    assert [f.order_id for f in filled] == [rested.order_id] and filled[0].status == "COMPLETE" and new.resting() == []
    # Parity: the same fill and charges as an order placed immediately at that price.
    fresh = PaperExecutionAdapter()
    fresh.mark("NIFTYX", 94.5, "NFO")
    now = _run(fresh.place_order(_limit()))
    assert filled[0].raw["average_price"] == now.raw["average_price"] <= 95.0
    assert new.charges(rested.order_id) == fresh.charges(now.order_id)


def test_a_triggered_stop_past_its_limit_keeps_waiting_and_cancel_ends_it():
    a = PaperExecutionAdapter(keep_resting=True)
    a.mark("NIFTYX", 100.0, "NFO")
    stop = BrokerOrderRequest(symbol="NIFTYX", exchange="NFO", transaction_type=OrderSide.SELL, quantity=75, order_type="SL",
                              price=89.0, trigger_price=90.0)
    rested = _run(a.place_order(stop))
    assert rested.status == "OPEN"
    assert _run(a.on_price("NIFTYX", 88.0, "NFO")) == []                  # triggered, but below its limit 89: waits
    assert _run(a.cancel_order(rested.order_id)).status == "CANCELLED" and a.resting() == []
    incomplete = BrokerOrderRequest(symbol="NIFTYX", exchange="NFO", transaction_type=OrderSide.BUY, quantity=75, order_type="LIMIT")
    assert _run(a.place_order(incomplete)).status == "REJECTED"           # a limit without its price never rests
