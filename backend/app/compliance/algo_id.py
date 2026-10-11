"""Part D1 (rules IN-SEBI.algo_id.*): which algo id an order carries, in which format, and the audit row for it.

- Registered vs generic: below the exchange's OPS threshold a client may trade under the broker's *generic* algo id;
  above it the exchange-registered id (`tenants.algo_id`) is required. The platform only counts as "below" when its own
  OPS throttle (D2) is on and capped at or under `ops_threshold` - otherwise nothing guarantees the rate, so only the
  registered id will do. The registered id always wins when it is set. Generic ids are per broker, in the rule-set
  (`generic_ids`, OPEN_QUESTIONS D-2) - data, never code.
- Format: each broker's tag field has its own length and character set (`brokers` in IN-SEBI.algo_id.tag); unknown
  brokers keep today's format (letters, digits, `_`, `-`; `max_tag_length_default`).
- Audit: every LIVE order the platform places is written to the hash-chained audit log with the tag it carried.
"""
import re
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.compliance import rules
from app.core import config

RULE_TAG = "IN-SEBI.algo_id.tag"
RULE_REGISTERED = "IN-SEBI.algo_id.registered_above_ops"
RULE_OPS = "IN-SEBI.ops.throttle"
CHARSETS = {"alnum": re.compile(r"[^A-Za-z0-9]+"), "alnum_dash": re.compile(r"[^A-Za-z0-9_-]+")}


def tag_format(broker_name: Optional[str]) -> dict:
    """{"max_length": int or None, "charset": name} for this broker. Without a rule-set entry the adapter's own
    `max_tag_length` (or `max_tag_length_default`) stays the limit and the charset is today's."""
    per_broker = (rules.load().param(RULE_TAG, "brokers", {}) or {}).get((broker_name or "").lower(), {})
    fmt = {"max_length": None, "charset": "alnum_dash", **per_broker}
    if fmt["charset"] not in CHARSETS:
        raise ValueError(f"Unknown tag charset {fmt['charset']!r} for broker {broker_name!r}")
    return fmt


def default_max_length() -> int:
    return int(rules.load().param(RULE_TAG, "max_tag_length_default", 20))


def ops_within_threshold() -> bool:
    """True only when the D2 throttle is on and caps orders per second at or below the exchange threshold."""
    if not config.OPS_THROTTLE_ENABLED:
        return False
    ruleset = rules.load()
    rate = config.OPS_PER_SECOND or float(ruleset.param(RULE_OPS, "ops_per_second", 0) or 0)
    return 0 < rate <= float(ruleset.param(RULE_REGISTERED, "ops_threshold", 0) or 0)


def generic_id(broker_name: Optional[str]) -> Optional[str]:
    return ((rules.load().param(RULE_REGISTERED, "generic_ids", {}) or {}).get((broker_name or "").lower()) or None)


def order_algo_id(registered: Optional[str], broker_name: Optional[str]) -> Optional[str]:
    """The id an order to this broker carries: the registered one, else the broker's generic one when allowed."""
    if registered:
        return registered
    return generic_id(broker_name) if ops_within_threshold() else None


def live_problem(registered: Optional[str], broker_name: Optional[str]) -> Optional[str]:
    """Why a LIVE entry to this broker has no usable algo id (None = fine). Only consulted with ALGO_ID_REQUIRED_FOR_LIVE."""
    if order_algo_id(registered, broker_name):
        return None
    if generic_id(broker_name) and not ops_within_threshold():
        return ("Exchange algo id not set for this organisation - the broker's generic id is allowed only below the "
                "OPS threshold, which the OPS throttle is not enforcing - LIVE orders refused (SEBI algo tagging)")
    return "Exchange algo id not set for this organisation - LIVE orders refused (SEBI algo tagging)"


async def audit_order(session: AsyncSession, tenant_id: Optional[int], user_id: Optional[int], *, leg: str, broker_name: str,
                      symbol: str, tag: Optional[str], broker_order_id: Optional[str], ref: str = "") -> None:
    """One audit-log row per LIVE order placed: the tag the broker received, next to the broker's order id."""
    from app.audit.log import write_audit_log   # local: the audit module imports models that import execution
    await write_audit_log(session, tenant_id, user_id, "algo_order",
                          f"{leg} {symbol} via {broker_name}: broker order {broker_order_id or '-'} tag={tag or '(none)'}"
                          + (f" ({ref})" if ref else ""))
