"""Part D1 (rules IN-SEBI.algo_id.*): the algo id on every order, in the broker's format, and in the audit log.

- a broker entry in the rule-set (`brokers`) makes the tag stricter (length, `alnum` charset without separators); a broker
  without one keeps today's tag exactly;
- the registered id (`tenants.algo_id`) always wins; the broker's generic id (`generic_ids`) is used only while the D2 OPS
  throttle is on and capped at or below `ops_threshold` - above it, or with the throttle off, only the registered id will do;
- with ALGO_ID_REQUIRED_FOR_LIVE a LIVE entry without a usable id is refused before any broker call, and one with the
  generic id goes through carrying it;
- every LIVE entry and exit writes an `algo_order` audit row with the tag the broker received.
"""
import asyncio

import pytest
from sqlalchemy import select

from app.compliance import algo_id as ids
from app.compliance import rules
from app.core import config
from app.db.models import AuditLogRecord, TradeRecord
from app.execution.tagging import LEG_ENTRY, LEG_STOP, build_order_tag
from tests.test_algo_tagging import _set_algo_id, _user
from tests.test_auth_api import _session_factory
from tests.test_live_execution import _LiveBroker, _execute


@pytest.fixture
def ruleset(monkeypatch):
    """Crafted rule-set parameters for one test (the loaded rule-set is cached; params are restored afterwards)."""
    loaded = rules.load()

    def set_param(rule_id, name, value):
        monkeypatch.setitem(loaded.rule(rule_id).params, name, value)
    return set_param


def _throttle(monkeypatch, on: bool, rate=None):
    monkeypatch.setattr(config, "OPS_THROTTLE_ENABLED", on)
    monkeypatch.setattr(config, "OPS_PER_SECOND", rate)


def _audit_rows(tenant_id: int):
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(AuditLogRecord).where(
                AuditLogRecord.tenant_id == tenant_id, AuditLogRecord.event == "algo_order")))
    return asyncio.run(go())


def test_broker_format_from_the_rule_set_and_unknown_brokers_unchanged(ruleset):
    plain = build_order_tag(strategy_id="ema_rsi_scalper", leg=LEG_ENTRY, algo_id="NSE-77", max_length=20)
    assert build_order_tag(strategy_id="ema_rsi_scalper", leg=LEG_ENTRY, algo_id="NSE-77", max_length=20, broker="nobroker") == plain
    ruleset(ids.RULE_TAG, "brokers", {"strictco": {"max_length": 12, "charset": "alnum"}})
    tag = build_order_tag(strategy_id="ema_rsi_scalper", leg=LEG_STOP, algo_id="NSE-77", max_length=20, broker="StrictCo")
    assert tag.startswith("NSE77") and tag.endswith("SL") and len(tag) <= 12 and tag.isalnum()
    ruleset(ids.RULE_TAG, "brokers", {"strictco": {"charset": "emoji"}})
    with pytest.raises(ValueError):
        build_order_tag(strategy_id="s", leg=LEG_ENTRY, broker="strictco")


def test_registered_id_wins_generic_only_under_an_enforced_threshold(monkeypatch, ruleset):
    ruleset(ids.RULE_REGISTERED, "generic_ids", {"fakelive": "GEN1"})
    threshold = ids.rules.load().param(ids.RULE_REGISTERED, "ops_threshold")
    _throttle(monkeypatch, False)
    assert ids.order_algo_id("REG9", "fakelive") == "REG9"
    assert ids.order_algo_id(None, "fakelive") is None                         # throttle off: nothing caps the rate
    _throttle(monkeypatch, True, threshold)
    assert ids.order_algo_id(None, "FakeLive") == "GEN1"
    assert ids.order_algo_id(None, "otherbroker") is None                      # no generic id for that broker
    assert ids.order_algo_id("REG9", "fakelive") == "REG9"
    _throttle(monkeypatch, True, threshold + 1)
    assert ids.order_algo_id(None, "fakelive") is None
    assert "OPS threshold" in ids.live_problem(None, "fakelive")
    assert ids.live_problem("REG9", "fakelive") is None


def test_live_refused_without_a_usable_id_and_generic_id_goes_through(monkeypatch, ruleset):
    from app.execution.router import OrderRouter
    monkeypatch.setattr(OrderRouter, "fill_poll_delay_seconds", 0)
    monkeypatch.setattr(config, "ALGO_ID_REQUIRED_FOR_LIVE", True)
    ruleset(ids.RULE_REGISTERED, "generic_ids", {"fakelive": "GEN1"})
    user = _user("d1-generic@example.com")
    broker = _LiveBroker()
    _throttle(monkeypatch, False)
    result, order, _, _ = _execute(user, broker)
    assert not result.executed and broker.placed == []
    assert any("OPS threshold" in r for r in result.reasons)
    _throttle(monkeypatch, True, ids.rules.load().param(ids.RULE_REGISTERED, "ops_threshold"))
    result, order, _, _ = _execute(user, broker)
    assert result.executed and order.algo_tag.startswith("GEN1-") and broker.placed[0].tag == order.algo_tag


def test_live_entry_writes_an_audit_row_with_the_tag(monkeypatch):
    from app.execution.router import OrderRouter
    monkeypatch.setattr(OrderRouter, "fill_poll_delay_seconds", 0)
    user = _user("d1-audit@example.com")
    _set_algo_id(user.tenant_id, "NSE4242")
    result, order, _, _ = _execute(user, _LiveBroker())
    assert result.executed
    rows = _audit_rows(user.tenant_id)
    assert len(rows) == 1 and f"tag={order.algo_tag}" in rows[0].detail and result.broker_order_id in rows[0].detail
    assert rows[0].detail.startswith("ENTRY ")


def test_live_exit_writes_an_audit_row_with_the_tag():
    from app.trading.position_monitor import close_position
    from tests.test_position_monitor import _Broker as _ExitBroker, _trade, _user as _pm_user
    user = _pm_user("d1-exit@example.com")
    _set_algo_id(user.tenant_id, "NSE56")
    trade_id = _trade(user, mode="LIVE")
    broker = _ExitBroker()

    async def go():
        async with _session_factory() as session:
            return await close_position(session, await session.get(TradeRecord, trade_id), 104.0, "Target 1", broker=broker)
    outcome = asyncio.run(go())
    assert outcome.closed and outcome.exit_tag and outcome.exit_tag.startswith("NSE56-")
    rows = [r for r in _audit_rows(user.tenant_id) if r.detail.startswith("EXIT ")]
    assert len(rows) == 1 and f"tag={outcome.exit_tag}" in rows[0].detail and outcome.broker_exit_order_id in rows[0].detail
