"""Phase AS: which broker product an order uses.

* INTRADAY positions use MIS: the broker squares them off near the close and margin is intraday.
* SWING positions are held overnight: CNC (delivery) for cash equity, NRML (carry forward) for
  futures and options. Exits and protective stops must use the same product as the entry, or the
  broker would treat the exit as a fresh intraday position.
"""
from typing import Optional

INTRADAY = "INTRADAY"
SWING = "SWING"
HOLDINGS = (INTRADAY, SWING)
CASH_EXCHANGES = ("NSE", "BSE")


def product_for(holding: Optional[str], instrument_kind: Optional[str], exchange: Optional[str]) -> str:
    if (holding or INTRADAY).upper() != SWING:
        return "MIS"
    kind = (instrument_kind or "UNDERLYING").upper()
    if kind == "UNDERLYING" and (exchange or "NSE").upper() in CASH_EXCHANGES:
        return "CNC"
    return "NRML"


def product_for_trade(trade) -> str:
    return product_for(getattr(trade, "holding", None), getattr(trade, "instrument_kind", None), getattr(trade, "exchange", None))
