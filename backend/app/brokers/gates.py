"""Part 1B (TM-5 / TM-6): per-broker gates for LIVE orders and live market data, closed until written confirmation.

A broker can be connected and read (login, profile, positions, holdings, funds) while two things stay off:

* **LIVE orders** - placing, changing or cancelling a real order at the broker. Closed for Kotak Neo and Groww until the lawyer
  and the broker confirm in writing that ATP's model is allowed (one ATP server IP for unrelated users, the static-IP
  rule, the SEBI retail-algo framework: docs/COMPLIANCE_IN.md, docs/DATA_SOURCES.md "For the lawyer").
* **Market data** - prices, quotes, candles, the option chain and the streaming feed read through that broker. Closed
  for Kotak Neo and Groww until its data terms are "confirmed" (app/market_data/broker_data_terms.json, docs/BROKER_DATA_TERMS.md)
  with the broker's written confirmation.

The gates are code, not a runtime switch: nothing in the admin console, an environment variable or a flag opens them.
Opening one is a reviewed change to `GATES` below, made only with the written confirmation recorded in
docs/design/BROKER_KOTAK_NEO.md (owner decision, 23 Oct 2026, DECISIONS.md: TM-3 / TM-5 / TM-6). A broker not listed
here keeps today's behaviour (both open), so the existing adapters are untouched.

The adapter itself checks the gate on every order and every market-data call (app/brokers/kotak_neo.py,
app/brokers/groww.py), so no code
path - the order router, the multi-leg executor, an exit, a stop, the worker - can reach the broker around it. Places,
changes and blocks nothing for any other broker.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from app.brokers.exceptions import BrokerAPIError, BrokerOrderRejected

OPEN = "open"
CLOSED = "closed"


@dataclass(frozen=True)
class BrokerGates:
    live_orders: str = OPEN
    market_data: str = OPEN
    # Plain words for the trader (shown on the Broker page while a gate is closed). No dates, ids or technical terms.
    note: str = ""
    # For the team: what opens each gate.
    opens_when: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {"live_orders": self.live_orders, "live_prices": self.market_data, "note": self.note}


_KOTAK_NOTE = ("Practice mode only for now. ATP can read your Kotak Neo positions, holdings and funds. Real orders and "
               "live prices through Kotak Neo stay off until Kotak Neo confirms in writing that they may be used through ATP.")

_GROWW_NOTE = ("Practice mode only for now. ATP can read your Groww positions, holdings, funds and orders. Real orders and "
               "live prices through Groww stay off until Groww confirms in writing that they may be used through ATP.")

GATES: Dict[str, BrokerGates] = {
    "kotak_neo": BrokerGates(
        live_orders=CLOSED, market_data=CLOSED, note=_KOTAK_NOTE,
        opens_when=("LIVE orders: the lawyer's opinion and Kotak Neo's written confirmation of the one-server-IP model "
                    "(TM-5). Market data: broker_data_terms.json row 'confirmed' with Kotak Neo's written confirmation (TM-6)."),
    ),
    # Part 1B (docs/design/BROKER_GROWW.md): the same two gates, closed for the same reasons (TM-5 / TM-6).
    "groww": BrokerGates(
        live_orders=CLOSED, market_data=CLOSED, note=_GROWW_NOTE,
        opens_when=("LIVE orders: the lawyer's opinion and Groww's written confirmation of the one-server-IP model and its "
                    "static-IP rule (TM-5). Market data: broker_data_terms.json row 'confirmed' with Groww's written "
                    "confirmation (TM-6)."),
    ),
}

_DEFAULT = BrokerGates()


def gates_for(broker: Optional[str]) -> BrokerGates:
    return GATES.get((broker or "").lower(), _DEFAULT)


def live_orders_open(broker: Optional[str]) -> bool:
    return gates_for(broker).live_orders == OPEN


def market_data_open(broker: Optional[str]) -> bool:
    """The broker's own market data may be read. A gated broker also needs its data terms confirmed: both must agree."""
    if gates_for(broker).market_data != OPEN:
        return False
    if (broker or "").lower() in GATES:
        from app.market_data.data_terms import terms_for  # local: data_terms is a leaf, keep this module light
        return terms_for(broker or "").tier_a_allowed
    return True


def data_sources(records: Iterable[Any]) -> List[Any]:
    """Credential rows whose broker's market data may be read (order kept). A row of a broker with its data gate closed
    is left out, so a price, chart or chain read never picks it ahead of another connection."""
    return [r for r in records if market_data_open(getattr(r, "broker_name", None))]


class LiveOrdersClosed(BrokerOrderRejected):
    """A real order through a broker whose LIVE-order gate is closed. Nothing was sent to the broker."""

    def __init__(self, broker_name: str) -> None:
        super().__init__(f"Real orders through {broker_name} are not offered yet. Nothing was sent to {broker_name}; "
                         "practice mode keeps working.")
        self.broker = broker_name


class MarketDataClosed(BrokerAPIError):
    """A price / chart / chain read through a broker whose market-data gate is closed. Nothing was asked of the broker."""

    def __init__(self, broker_name: str) -> None:
        super().__init__(f"Live prices from {broker_name} are not offered yet; prices come from your other data connection.")
        self.broker = broker_name


def require_live_orders(broker_key: str, broker_name: str) -> None:
    if not live_orders_open(broker_key):
        raise LiveOrdersClosed(broker_name)


def require_market_data(broker_key: str, broker_name: str) -> None:
    if not market_data_open(broker_key):
        raise MarketDataClosed(broker_name)
