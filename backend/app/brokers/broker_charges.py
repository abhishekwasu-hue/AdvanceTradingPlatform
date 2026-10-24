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


ROWS: Tuple[BrokerageRow, ...] = (
    BrokerageRow(
        broker="kotak_neo", plan="trade_free", channel="api", since=D(2025, 11, 1), per_order=0.0,
        source="https://www.kotakneo.com/about-us/media-and-press/kotak-neo-introduces-zero-brokerage-zero-fee-trade-apis-for-retail-traders/",
        read_on=D(2026, 10, 23), verified=False,
        note=("Kotak Neo press release (search extract): 'Effective 1st November 2025, all API orders placed under the Trade "
              "Free plans on the app will attract no per-order brokerage and API usage charges.' Statutory charges still "
              "apply. The broker may change this; re-read before LIVE."),
    ),
)

_BY_KEY: Dict[Tuple[str, str, str], List[BrokerageRow]] = {}
for _row in ROWS:
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


def unverified() -> List[BrokerageRow]:
    """Rows seen only in a search extract: to confirm on the broker's own page before LIVE."""
    return [r for r in ROWS if not r.verified]
