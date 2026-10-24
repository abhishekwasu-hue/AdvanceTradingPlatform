"""Part 1B / TM-26: brokerage per broker plan, as data - only what the broker itself states, with the source and the date.

Statutory charges (STT, exchange transaction charges, SEBI fee, stamp duty, GST) are the same at every broker and are
modelled by date in app/execution/india_costs.py; they apply on top of every row here. This table holds only the
broker's own brokerage for an order, per plan and channel ("api": an order placed through the broker's Trade API), from
the date the broker says it applies.

A row is never a guess: each carries the source URL, the date it was read and whether it was confirmed on the broker's
own page (`verified`) or only seen in a search extract of that page (the build machine could not open the broker's
site). A plan or channel with no row is "not stated" - the caller keeps the user's own brokerage setting
(`PaperBroker.brokerage_per_order`), never a number from here. Change a row only together with its source.

What is NOT modelled, on purpose:
* Kotak Neo Bracket Order square-off leg charges: the current official SDK (kotakneoapi 3.0.9, 15 Oct 2026) says Bracket
  and Cover orders are no longer supported and accepts only CNC / MIS / NRML / MTF, so ATP never places a BO there.
* Kotak Neo Trade Free plan app / web orders (not API): ATP places API orders only.
* Groww's auto square-off charge for an open intraday position (Rs 50 per position, Groww pricing page, search extract):
  ATP's own positions are closed by the user's settings; it is not an order brokerage.

Part 1B (Groww): a row may also be a percentage of the order value with a cap and a minimum (Groww's stock brokerage),
and may apply to some cost segments only (`segments`, the names of app/execution/india_costs.py: EQUITY_INTRADAY,
EQUITY_DELIVERY, FUTURE, OPTION; empty = all). `brokerage_for_order` works it out for one order; `brokerage_per_order`
keeps answering only for flat rows. A broker's subscription fee (Groww Trade API) is not an order charge: it is in
`PLATFORM_FEES`, shown to the user, never added to a trade.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

D = dt.date


@dataclass(frozen=True)
class BrokerageRow:
    broker: str
    plan: str                    # the broker's own plan name, lower-case with underscores
    channel: str                 # "api"
    since: dt.date               # the date the broker says the rate applies from
    per_order: float             # rupees per executed order
    source: str                  # URL of the broker's own page
    read_on: dt.date             # when ATP read it
    verified: bool               # True: read on the broker's page; False: a search extract of that page only
    note: str = ""
    segments: Tuple[str, ...] = ()           # india_costs segments this row applies to; () = every segment
    pct_of_value: Optional[float] = None     # percent of the order value; with it, `per_order` is the cap
    minimum: Optional[float] = None          # below this, the charge is the lower of `minimum` and `minimum_pct`
    minimum_pct: Optional[float] = None      # percent of the order value for the minimum rule


@dataclass(frozen=True)
class PlatformFee:
    broker: str
    plan: str
    per_month: float             # rupees, before tax
    tax: str                     # how the broker states the tax ("+ GST")
    source: str
    read_on: dt.date
    verified: bool
    note: str = ""


_GROWW_HELP = "https://groww.in/help/stocks,-f&o,-ipo-&-mtf/searchable/how-much-is-the-brokerage"
_GROWW_NOTE = ("Groww help page (search extract): 'Stock Trading: Rs 20 or 0.1% of trade value per order, whichever is "
               "lower. However, if the brokerage per trade is less than Rs 5, then the minimum charge will be the lower of "
               "Rs 5 or 2.5% of your trade value. F&O Trading: Rs 20 per order.' API orders pay the normal brokerage. "
               "Statutory charges apply. Re-read before LIVE.")


ROWS: Tuple[BrokerageRow, ...] = (
    BrokerageRow(
        broker="kotak_neo", plan="trade_free", channel="api", since=D(2025, 11, 1), per_order=0.0,
        source="https://www.kotakneo.com/about-us/media-and-press/kotak-neo-introduces-zero-brokerage-zero-fee-trade-apis-for-retail-traders/",
        read_on=D(2026, 10, 23), verified=False,
        note=("Kotak Neo press release (search extract): 'Effective 1st November 2025, all API orders placed under the Trade "
              "Free plans on the app will attract no per-order brokerage and API usage charges.' Statutory charges still "
              "apply. The broker may change this; re-read before LIVE."),
    ),
    # Groww: the normal brokerage applies to API orders (the Trade API is a separate monthly subscription, below). The
    # date the current rates apply from is not stated on the extract; the minimum rule was revised in June 2025 (a
    # third-party article), so the rows apply from 1 Jul 2025 and nothing earlier is modelled.
    BrokerageRow(
        broker="groww", plan="standard", channel="api", since=D(2025, 7, 1), per_order=20.0, source=_GROWW_HELP,
        read_on=D(2026, 10, 23), verified=False, note=_GROWW_NOTE, segments=("EQUITY_INTRADAY", "EQUITY_DELIVERY"),
        pct_of_value=0.1, minimum=5.0, minimum_pct=2.5,
    ),
    BrokerageRow(
        broker="groww", plan="standard", channel="api", since=D(2025, 7, 1), per_order=20.0, source=_GROWW_HELP,
        read_on=D(2026, 10, 23), verified=False, note=_GROWW_NOTE, segments=("FUTURE", "OPTION"),
    ),
)

PLATFORM_FEES: Tuple[PlatformFee, ...] = (
    PlatformFee(broker="groww", plan="trade_api_monthly", per_month=499.0, tax="+ GST",
                source="https://groww.in/blog/api-trading-on-groww", read_on=D(2026, 10, 23), verified=False,
                note=("Groww blog (search extract): the Trade API paid plan is Rs 499 a month plus taxes (or Rs 4,999 a year); "
                      "the free trial leaves out the live-data and backtesting APIs. Paid by the user to Groww.")),
)

_BY_KEY: Dict[Tuple[str, str, str], List[BrokerageRow]] = {}
for _row in ROWS:
    if _row.segments or _row.pct_of_value is not None:
        continue                     # segment or percent rows: brokerage_for_order only
    _BY_KEY.setdefault((_row.broker, _row.plan, _row.channel), []).append(_row)
for _rows in _BY_KEY.values():
    _rows.sort(key=lambda r: r.since)


def brokerage_row(broker: Optional[str], plan: Optional[str], day: dt.date, channel: str = "api") -> Optional[BrokerageRow]:
    """The row in force on `day` for this broker, plan and channel; None when the broker states nothing for it."""
    rows = _BY_KEY.get(((broker or "").lower(), (plan or "").lower(), channel), [])
    found = None
    for row in rows:
        if row.since <= day:
            found = row
    return found


def brokerage_per_order(broker: Optional[str], plan: Optional[str], day: dt.date, channel: str = "api") -> Optional[float]:
    """Rupees per executed order as the broker states it for `day`, or None (not stated: keep the user's own setting)."""
    row = brokerage_row(broker, plan, day, channel)
    return None if row is None else float(row.per_order)


def _row_charge(row: BrokerageRow, value: float) -> float:
    if row.pct_of_value is None:
        return float(row.per_order)
    charge = min(float(row.per_order), abs(float(value)) * row.pct_of_value / 100.0)
    if row.minimum is not None and charge < row.minimum:
        floor = row.minimum if row.minimum_pct is None else min(row.minimum, abs(float(value)) * row.minimum_pct / 100.0)
        charge = floor
    return round(charge, 2)


def brokerage_for_order(broker: Optional[str], plan: Optional[str], day: dt.date, segment: str, value: float,
                        channel: str = "api") -> Optional[float]:
    """Rupees of brokerage for ONE executed order of `value` (price x quantity) in an india_costs `segment`, as the broker
    states it for `day`; None when it states nothing for this plan / segment (keep the user's own setting)."""
    key = ((broker or "").lower(), (plan or "").lower(), channel)
    found: Optional[BrokerageRow] = None
    for row in ROWS:
        if (row.broker, row.plan, row.channel) != key or row.since > day:
            continue
        if row.segments and segment not in row.segments:
            continue
        if found is None or row.since >= found.since:
            found = row
    return None if found is None else _row_charge(found, value)


def platform_fee(broker: Optional[str]) -> Optional[PlatformFee]:
    return next((f for f in PLATFORM_FEES if f.broker == (broker or "").lower()), None)


def unverified() -> List[BrokerageRow]:
    """Rows seen only in a search extract: to confirm on the broker's own page before LIVE."""
    return [r for r in ROWS if not r.verified]
