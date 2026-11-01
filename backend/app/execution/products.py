"""Phase AS: which broker product an order uses.

* INTRADAY positions use MIS: the broker squares them off near the close and margin is intraday.
* SWING positions are held overnight: CNC (delivery) for cash equity, NRML (carry forward) for
  futures and options. Exits and protective stops must use the same product as the entry, or the
  broker would treat the exit as a fresh intraday position.

M68 PR-1 (TS1-005): ATP never picks the holding of a NEW entry. `product_for` needs the holding the user chose (the
order's, the bot's or his Trade settings, app/execution/order_defaults.py); None is refused (`HoldingNotSet`), never read
as INTRADAY. The MIS / CNC / NRML codes are broker facts (TS1-091). `product_for_trade` is the EXIT side: the position's
own record (`trades.holding`, written for every trade) - an exit always uses the product the position was opened with.
"""
from typing import Optional

INTRADAY = "INTRADAY"
SWING = "SWING"
HOLDINGS = (INTRADAY, SWING)
CASH_EXCHANGES = ("NSE", "BSE")


class HoldingNotSet(ValueError):
    """A new entry with no holding chosen by the user (M68: ATP does not choose one)."""


def product_for(holding: Optional[str], instrument_kind: Optional[str], exchange: Optional[str]) -> str:
    if not holding:
        raise HoldingNotSet("holding not set")
    if holding.upper() != SWING:
        return "MIS"
    kind = (instrument_kind or "UNDERLYING").upper()
    if kind == "UNDERLYING" and (exchange or "NSE").upper() in CASH_EXCHANGES:
        return "CNC"
    return "NRML"


def product_for_trade(trade) -> str:
    """The product of an open position (its exits and protective stops use it). M68 allow-list (exit never blocked,
    ADR-0004): a position record always carries its holding (`trades.holding` NOT NULL, the record of how it was opened);
    a record without one - only an older in-memory object - was opened as an intraday position, so it closes as one."""
    return product_for(getattr(trade, "holding", None) or INTRADAY, getattr(trade, "instrument_kind", None),
                       getattr(trade, "exchange", None))
