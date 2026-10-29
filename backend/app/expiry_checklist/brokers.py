"""AUD-T2: each broker's own rule for stock F&O held into expiry, in one place (data, not scattered through the code).

Each note is a short plain summary of the broker's own help page, and the link goes to that page - the broker's text is the
source, ATP never states the rule on its own. Index F&O is cash-settled at every broker, so these notes are shown only for
stock F&O. Keys are the platform's broker names (BrokerCredentialRecord.broker_name).
Sources (checked 29 Oct 2026): each URL below.
"""
from __future__ import annotations

from typing import Dict, List, NamedTuple, Optional


class BrokerHelp(NamedTuple):
    name: str
    note: str
    url: str


BROKER_HELP: Dict[str, BrokerHelp] = {
    "angel_one": BrokerHelp(
        "Angel One",
        "Angel One: an in-the-money stock option at expiry means taking or giving the shares.",
        "https://www.angelone.in/support/your-orders/options"),
    "dhan": BrokerHelp(
        "Dhan",
        "Dhan asks for margin equal to the full contract value on expiry day.",
        "https://knowledge.dhan.co/support/solutions/articles/82000879978-what-is-dhan-s-policy-on-the-physical-settlement-"),
    "upstox": BrokerHelp(
        "Upstox",
        "Upstox asks for your consent before expiry; without it the position is closed on expiry day.",
        "https://help.upstox.com/support/solutions/articles/251859-physical-settlement-on-all-stock-derivatives"),
    "zerodha": BrokerHelp(
        "Zerodha",
        "Zerodha raises the margin near expiry, up to the full contract value on expiry day.",
        "https://support.zerodha.com/category/trading-and-markets/margin-leverage-and-product-and-order-types/articles/policy-on-physical-settlement"),
}


def notes_for(brokers: Optional[List[str]]) -> List[Dict[str, str]]:
    """The notes of the user's own brokers that ATP knows; when he has none of them, every known broker's (sorted)."""
    mine = [b.lower() for b in (brokers or []) if b and b.lower() in BROKER_HELP]
    keys = sorted(set(mine)) or sorted(BROKER_HELP)
    return [{"broker": k, "name": BROKER_HELP[k].name, "note": BROKER_HELP[k].note, "url": BROKER_HELP[k].url} for k in keys]
