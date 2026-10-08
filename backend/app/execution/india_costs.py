"""Indian trading costs by DATE - statutory charges per executed leg, with each rate as it was on the trade day.

Ported from Trade@0df3e09d0aa5942e3352327d5b998c5f6939ac61, elliott/costs.py (index options), extended to futures and
equity so one table drives every estimate in ATP (paper fills, backtests, the options backtester).

Per leg (buy or sell x price x quantity), the rates of THAT day:
  STT    options: sell premium only - 0.017% to 31 May 2016, 0.05% from 1 Jun 2016, 0.0625% from 1 Apr 2023, 0.1% from
         1 Oct 2024, 0.15% from 1 Apr 2026. Futures: sell side - 0.01%, 0.0125% from 1 Apr 2023, 0.02% from 1 Oct 2024,
         0.05% from 1 Apr 2026 (verify). Equity intraday: sell side 0.025%. Equity delivery: both sides 0.1%.
         Exercise of a long in-the-money option at expiry: on the intrinsic (settlement) value - 0.125%, 0.15% from
         1 Apr 2026 (verify).
  Exchange transaction charges (both sides; NSE, verify against circulars): options ~0.05% -> 0.053% (1 Apr 2023) ->
         0.0495% (1 Jan 2024) -> 0.03503% (1 Oct 2024); futures 0.0019% -> 0.00173% (1 Oct 2024); equity 0.00325% ->
         0.00297% (1 Oct 2024).
  SEBI turnover fee 0.0001% (Rs 10 / crore), both sides. Stamp duty (buy only): options 0.003%, futures 0.002%, equity
         intraday 0.003%, delivery 0.015% (from 1 Jul 2020; before that ~0.002%).
  GST 18% on brokerage + exchange + SEBI (service tax before Jul 2017: 12.36% -> 14% -> 14.5% -> 15%).
  Brokerage = per executed order (a setting). Slippage belongs in the fill price, not here.
"STT on the wrong side" is impossible by construction: STT is attached to the sell leg (or both, for delivery) of each
executed order, whichever leg opened the position.
"""
import datetime as dt
from typing import Dict, Optional, Sequence, Tuple

import pandas as pd

Table = Sequence[Tuple[dt.date, float]]
D = dt.date

STT_SELL: Dict[str, Table] = {
    "OPTION": [(D(2000, 1, 1), 0.00017), (D(2016, 6, 1), 0.0005), (D(2023, 4, 1), 0.000625), (D(2024, 10, 1), 0.001),
               (D(2026, 4, 1), 0.0015)],
    "FUTURE": [(D(2000, 1, 1), 0.0001), (D(2023, 4, 1), 0.000125), (D(2024, 10, 1), 0.0002), (D(2026, 4, 1), 0.0005)],
    "EQUITY_INTRADAY": [(D(2000, 1, 1), 0.00025)],
    "EQUITY_DELIVERY": [(D(2000, 1, 1), 0.001)],
}
STT_BUY: Dict[str, Table] = {"EQUITY_DELIVERY": [(D(2000, 1, 1), 0.001)]}
EXCHANGE: Dict[str, Table] = {
    "OPTION": [(D(2000, 1, 1), 0.0005), (D(2023, 4, 1), 0.00053), (D(2024, 1, 1), 0.000495), (D(2024, 10, 1), 0.0003503)],
    "FUTURE": [(D(2000, 1, 1), 0.000019), (D(2024, 10, 1), 0.0000173)],
    "EQUITY_INTRADAY": [(D(2000, 1, 1), 0.0000325), (D(2024, 10, 1), 0.0000297)],
    "EQUITY_DELIVERY": [(D(2000, 1, 1), 0.0000325), (D(2024, 10, 1), 0.0000297)],
}
STAMP_BUY: Dict[str, Table] = {
    "OPTION": [(D(2000, 1, 1), 0.00002), (D(2020, 7, 1), 0.00003)],
    "FUTURE": [(D(2000, 1, 1), 0.00002), (D(2020, 7, 1), 0.00002)],
    "EQUITY_INTRADAY": [(D(2000, 1, 1), 0.00002), (D(2020, 7, 1), 0.00003)],
    "EQUITY_DELIVERY": [(D(2000, 1, 1), 0.0001), (D(2020, 7, 1), 0.00015)],
}
TAX: Table = [(D(2000, 1, 1), 0.1236), (D(2015, 6, 1), 0.14), (D(2015, 11, 15), 0.145), (D(2016, 6, 1), 0.15), (D(2017, 7, 1), 0.18)]
SEBI = 0.000001
STT_EXERCISE: Table = [(D(2000, 1, 1), 0.00125), (D(2026, 4, 1), 0.0015)]
SEGMENTS = tuple(STT_SELL)


def segment_for(instrument_kind: Optional[str], *, delivery: bool = False) -> str:
    """ATP instrument kinds -> cost segment. UNDERLYING is equity (intraday unless `delivery`)."""
    k = (instrument_kind or "UNDERLYING").upper()
    if k == "OPTION":
        return "OPTION"
    if k == "FUTURE":
        return "FUTURE"
    return "EQUITY_DELIVERY" if delivery else "EQUITY_INTRADAY"


def _at(table: Table, day) -> float:
    d = pd.Timestamp(day).date()
    v = table[0][1]
    for since, x in table:
        if d >= since:
            v = x
    return v


def rates(day, segment: str = "OPTION") -> Dict[str, float]:
    if segment not in SEGMENTS:
        raise ValueError(f"segment must be one of {SEGMENTS}")
    return {"stt_sell": _at(STT_SELL[segment], day), "stt_buy": _at(STT_BUY[segment], day) if segment in STT_BUY else 0.0,
            "exch": _at(EXCHANGE[segment], day), "stamp_buy": _at(STAMP_BUY[segment], day), "tax": _at(TAX, day), "sebi": SEBI}


def leg_cost(day, side: str, price: float, qty: float, segment: str = "OPTION", brokerage_per_order: float = 20.0) -> Dict[str, float]:
    """Total cost (Rs) of one executed order - slippage excluded (it is in the fill). side: "buy" | "sell"."""
    if side not in ("buy", "sell"):
        raise ValueError("side must be buy or sell")
    r = rates(day, segment)
    turnover = float(price) * float(qty)
    stt = (r["stt_sell"] if side == "sell" else r["stt_buy"]) * turnover
    exch = r["exch"] * turnover
    sebi = r["sebi"] * turnover
    stamp = r["stamp_buy"] * turnover if side == "buy" else 0.0
    brok = float(brokerage_per_order)
    tax = r["tax"] * (brok + exch + sebi)
    return {"stt": stt, "exch": exch, "sebi": sebi, "stamp": stamp, "brokerage": brok, "tax": tax,
            "total": stt + exch + sebi + stamp + brok + tax}


def round_trip(entry_day, exit_day, entry_price: float, exit_price: float, qty: float, segment: str, *, sold_first: bool = False,
               settled: bool = False, brokerage_per_order: float = 20.0) -> Dict[str, float]:
    """Open + close. `sold_first`: the position was opened with a sell (written option / short). `settled`: it ended by
    expiry settlement - no closing order (exercise STT is `exercise_cost`)."""
    open_side, close_side = ("sell", "buy") if sold_first else ("buy", "sell")
    a = leg_cost(entry_day, open_side, entry_price, qty, segment, brokerage_per_order)
    if settled:
        return a
    b = leg_cost(exit_day, close_side, exit_price, qty, segment, brokerage_per_order)
    return {k: a[k] + b[k] for k in a}


def spread_cost(day, short_px: float, long_px: float, qty: float, opening: bool = True, brokerage_per_order: float = 20.0) -> Dict[str, float]:
    """A two-leg option spread opened (short sell + long buy) or closed (short buy + long sell)."""
    if opening:
        a, b = leg_cost(day, "sell", short_px, qty, "OPTION", brokerage_per_order), leg_cost(day, "buy", long_px, qty, "OPTION", brokerage_per_order)
    else:
        a, b = leg_cost(day, "buy", short_px, qty, "OPTION", brokerage_per_order), leg_cost(day, "sell", long_px, qty, "OPTION", brokerage_per_order)
    return {k: a[k] + b[k] for k in a}


def exercise_cost(day, intrinsic_per_unit: float, settle_price: float, qty: float) -> float:
    """A long option settled in the money is exercised: STT on the intrinsic value (from 1 Jun 2016; before that on the
    settlement price x quantity)."""
    if intrinsic_per_unit <= 0 or qty <= 0:
        return 0.0
    base = intrinsic_per_unit if pd.Timestamp(day).date() >= D(2016, 6, 1) else settle_price
    return _at(STT_EXERCISE, day) * base * qty
