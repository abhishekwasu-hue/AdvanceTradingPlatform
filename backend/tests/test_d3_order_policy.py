"""Part D3 (rule IN-SEBI.order_type.policy): the order types an algo entry may use, per broker, as rule-set data.

- the default policy (MARKET allowed, DAY/IOC) changes nothing;
- a broker set to `map_to_limit` gets a marketable LIMIT at the PROTECTED_LIMIT price instead of MARKET, with a note;
- a broker set to `refuse`, or a validity outside the allowed list, refuses the entry before the broker is called;
- other brokers keep the default; exits and protective stops are never touched (ADR-0004).
"""
import asyncio
import dataclasses

import pytest

from app.brokers.models import BrokerOrderRequest
from app.compliance import order_policy, rules
from app.core.enums import OrderSide
from app.execution.router import protected_limit_price
from app.risk_engine.risk_manager import TradingDayState
from tests.test_live_execution import _LiveBroker, _router, _signal


def _with_policy(monkeypatch, *, default=None, brokers=None):
    base = rules.load()
    rule = base.rule(order_policy.RULE)
    params = {"default": {**rule.params["default"], **(default or {})}, "brokers": brokers or {}}
    patched = dataclasses.replace(base, rules={**base.rules, order_policy.RULE: dataclasses.replace(rule, params=params)})
    monkeypatch.setattr(rules, "load", lambda ruleset_id=rules.DEFAULT_RULESET: patched)


def test_default_policy_changes_nothing():
    broker = _LiveBroker()
    result = asyncio.run(_router(broker).execute(_signal(), TradingDayState()))
    assert result.executed and broker.placed[0].order_type == "MARKET" and broker.placed[0].price is None
    assert not any("Order-type policy" in r or "marketable LIMIT" in r for r in result.reasons)


def test_market_entry_mapped_to_a_marketable_limit_for_one_broker(monkeypatch):
    _with_policy(monkeypatch, brokers={"fakelive": {"market_for_algo": "map_to_limit"}})
    broker = _LiveBroker()
    result = asyncio.run(_router(broker, market_protection_pct=0.5).execute(_signal(), TradingDayState()))
    entry = broker.placed[0]
    assert result.executed and entry.order_type == "LIMIT"
    assert entry.price == protected_limit_price(100.0, OrderSide.BUY, 0.5)
    assert any("marketable LIMIT" in r for r in result.reasons)
    other = order_policy.policy_for("upstox")
    assert other["market_for_algo"] == "allow"                                   # other brokers keep the default


@pytest.mark.parametrize("policy,validity,reason", [
    ({"market_for_algo": "refuse"}, "DAY", "does not take MARKET orders"),
    ({"allowed_validity": ["DAY"]}, "IOC", "validity IOC is not allowed"),
])
def test_refused_market_or_validity_never_reaches_the_broker(monkeypatch, policy, validity, reason):
    _with_policy(monkeypatch, brokers={"fakelive": policy})
    order = BrokerOrderRequest(symbol="RELIANCE", transaction_type=OrderSide.BUY, quantity=1, validity=validity)
    sent, why = order_policy.apply_entry_policy(order, "fakelive", lambda: 100.5)
    assert sent is None and reason in why and order_policy.RULE in why
    if validity == "DAY":
        broker = _LiveBroker()
        result = asyncio.run(_router(broker).execute(_signal(), TradingDayState()))
        assert not result.executed and broker.placed == [] and not result.system_failure
        assert any(reason in r for r in result.reasons)


def test_exits_and_stops_are_not_touched_by_the_policy(monkeypatch):
    _with_policy(monkeypatch, brokers={"fakelive": {"market_for_algo": "map_to_limit", "allowed_validity": ["DAY"]}})
    broker = _LiveBroker()
    result = asyncio.run(_router(broker).execute(_signal(), TradingDayState()))
    assert result.executed
    stop = broker.placed[1]
    assert stop.order_type in ("SL-M", "SL") and stop.tag.endswith("-SL")      # the protective stop is placed as before


def test_an_unknown_market_choice_is_a_configuration_error(monkeypatch):
    _with_policy(monkeypatch, default={"market_for_algo": "sometimes"})
    with pytest.raises(ValueError, match="market_for_algo"):
        order_policy.policy_for("fakelive")
