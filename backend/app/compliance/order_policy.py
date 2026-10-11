"""Part D3 (rule IN-SEBI.order_type.policy): which order types an algo entry may use, per broker.

The rule-set holds a default policy and per-broker overrides (`params.default`, `params.brokers.<name>`):
- `market_for_algo`: `allow` (today's behaviour), `map_to_limit` (a marketable limit `market_protection_pct` past the
  signal price - the same price PROTECTED_LIMIT uses), or `refuse`;
- `allowed_validity`: the validities an algo entry may carry (e.g. no IOC where the broker bars it for algos).
It applies to **entries only**. Exits keep their market orders with the broker-side market protection (G-LIVE, rule
IN-SEBI.order_type.market_protection): an exit turned into a limit could stay unfilled, and exits are never blocked
(ADR-0004). The default policy changes nothing; the owner switches a broker by editing the rule-set.
"""
from typing import Dict, Optional, Tuple

from app.brokers.models import BrokerOrderRequest
from app.compliance import rules

RULE = "IN-SEBI.order_type.policy"
MARKET_CHOICES = ("allow", "map_to_limit", "refuse")


def policy_for(broker_name: Optional[str]) -> Dict:
    params = rules.load().rule(RULE).params
    policy = dict(params.get("default") or {})
    policy.update((params.get("brokers") or {}).get((broker_name or "").lower(), {}))
    if policy.get("market_for_algo", "allow") not in MARKET_CHOICES:
        raise ValueError(f"{RULE}: market_for_algo must be one of {MARKET_CHOICES}")
    return policy


def apply_entry_policy(order: BrokerOrderRequest, broker_name: Optional[str], limit_price) -> Tuple[Optional[BrokerOrderRequest], str]:
    """(order to send, note) or (None, refusal reason). `limit_price()` gives the marketable limit for a mapped order."""
    policy = policy_for(broker_name)
    allowed_validity = [v.upper() for v in policy.get("allowed_validity") or []]
    if allowed_validity and (order.validity or "DAY").upper() not in allowed_validity:
        return None, f"Order-type policy ({RULE}): validity {order.validity} is not allowed for algo orders on {broker_name}"
    if order.order_type.upper() == "MARKET":
        choice = policy.get("market_for_algo", "allow")
        if choice == "refuse":
            return None, f"Order-type policy ({RULE}): {broker_name} does not take MARKET orders from an algo - use PROTECTED_LIMIT"
        if choice == "map_to_limit":
            price = limit_price()
            return order.model_copy(update={"order_type": "LIMIT", "price": price}), \
                f"MARKET entry sent as a marketable LIMIT at {price} ({RULE}, {broker_name})"
    return order, ""
