"""M55 A18 (follow-up to #749): Redis down means no replica lock - the trigger engine's waiting ENTRIES pause too.

- a reached entry does not fire while Redis is unreachable (the worker cycle's pass and the tick pass alike): it stays
  waiting - not fired, not cancelled - and fires on the first pass after Redis is back; the expiry rules still apply;
- an armed EXIT still fires with Redis down (ADR-0004: exits are never blocked);
- one WARNING per outage, not one per pass.
"""
import logging
import random
from datetime import UTC, datetime, timedelta

import pytest

from app.cache import client as cache_client
from app.execution import trigger_engine
from app.market_data import hub
from tests.test_auth_api import _session_factory
from tests.test_fno1_chain_ticket import _contract, _expiry, _org_tenant, _price
from tests.test_tm2_trigger_engine import _arm, _entries, _exits, _open_trade, _rest, _row, _run, _trade


@pytest.fixture
def setup(monkeypatch):
    """The TM-2 tests' own set-up: an organisation, an option contract and its price."""
    t = _org_tenant("m55a18")
    expiry = _expiry()
    strike = float(random.randrange(30000, 40000, 50))
    symbol = _contract(expiry, strike)
    _price(monkeypatch, 100.0)
    return t, expiry, strike, symbol


class _DownRedis:
    """Every Redis call fails, as with the server stopped (the cache client reports 'unreachable')."""

    def __getattr__(self, name):
        async def fail(*args, **kwargs):
            raise ConnectionError("Redis is down")
        return fail


def _redis_down(monkeypatch):
    monkeypatch.setattr(cache_client, "_get_client", lambda: _DownRedis())


def _fresh_outage_state(monkeypatch):
    monkeypatch.setattr(trigger_engine, "_entries_paused_warned", False)


def test_a_reached_entry_waits_while_redis_is_down_and_fires_after_recovery(setup, monkeypatch):
    t, expiry, strike, symbol = setup
    row_id = _rest(t, expiry, strike)["resting_order"]["id"]
    _fresh_outage_state(monkeypatch)
    with monkeypatch.context() as down:
        _redis_down(down)
        report = _entries(t["tenant_id"], {symbol: 94.0})          # the price reached it
        assert report.filled == 0 and report.rejected == 0 and report.waiting == 1
        row = _row(row_id)
        assert row.status == "RESTING" and row.fired_at is None and row.order_id is None   # armed: not fired, not cancelled
    report = _entries(t["tenant_id"], {symbol: 94.0})              # Redis is back
    assert report.filled == 1 and _row(row_id).status == "FILLED"


def test_the_expiry_rules_still_apply_while_entries_are_paused(setup, monkeypatch):
    t, expiry, strike, symbol = setup
    row_id = _rest(t, expiry, strike)["resting_order"]["id"]
    _fresh_outage_state(monkeypatch)
    with monkeypatch.context() as down:
        _redis_down(down)
        report = _entries(t["tenant_id"], {symbol: 94.0}, now=datetime.now(UTC) + timedelta(days=2))
    assert report.expired >= 1 and report.filled == 0 and _row(row_id).status == "EXPIRED"


def test_an_exit_still_fires_with_redis_down_and_the_tick_pass_holds_only_the_entry(setup, monkeypatch):
    t, expiry, strike, symbol = setup
    trade_id = _open_trade(t, expiry, strike, symbol)
    stop = _arm(t, trade_id, kind="STOP", order_type="SL-M", level=80.0)
    entry_id = _rest(t, expiry, strike)["resting_order"]["id"]
    _fresh_outage_state(monkeypatch)
    now = datetime.now(UTC)
    monkeypatch.setattr(hub, "live_ticks", lambda ns: {f"NFO:{symbol}": [79.0, now.isoformat()]}
                        if ns == hub.org_namespace(t["tenant_id"]) else {})

    async def tick():
        async with _session_factory() as session:
            return await trigger_engine.tick_pass(session, now)
    with monkeypatch.context() as down:
        _redis_down(down)
        report = _run(tick())                                       # 79 reaches both the stop (80) and the entry (95)
        assert report.filled == 1                                   # the exit only (ADR-0004)
        assert _trade(trade_id).exit_time is not None and _row(stop["id"]).status == "FILLED"
        assert _row(entry_id).status == "RESTING"
    assert _run(tick()).filled == 1 and _row(entry_id).status == "FILLED"   # Redis back: the entry fires


def test_the_cycle_exit_pass_fires_with_redis_down(setup, monkeypatch):
    t, expiry, strike, symbol = setup
    trade_id = _open_trade(t, expiry, strike, symbol)
    stop = _arm(t, trade_id, kind="STOP", order_type="SL-M", level=80.0)
    with monkeypatch.context() as down:
        _redis_down(down)
        assert _exits(t["tenant_id"], {symbol: 79.0}).filled == 1
    assert _row(stop["id"]).status == "FILLED" and _trade(trade_id).exit_time is not None


def test_one_warning_per_outage(setup, monkeypatch, caplog):
    t, expiry, strike, symbol = setup
    _rest(t, expiry, strike)
    _fresh_outage_state(monkeypatch)
    caplog.set_level(logging.INFO, logger=trigger_engine.logger.name)

    def warnings():
        return [r for r in caplog.records if r.levelno == logging.WARNING and trigger_engine.ENTRIES_PAUSED_NOTE in r.getMessage()]
    with monkeypatch.context() as down:
        _redis_down(down)
        for _ in range(3):
            _entries(t["tenant_id"], {symbol: 96.0})
        assert len(warnings()) == 1
    _entries(t["tenant_id"], {symbol: 96.0})                        # back: one INFO, no warning
    assert len(warnings()) == 1 and any("Redis is back" in r.getMessage() for r in caplog.records)
    with monkeypatch.context() as down:                             # a second outage warns once more
        _redis_down(down)
        _entries(t["tenant_id"], {symbol: 96.0})
        _entries(t["tenant_id"], {symbol: 96.0})
    assert len(warnings()) == 2
