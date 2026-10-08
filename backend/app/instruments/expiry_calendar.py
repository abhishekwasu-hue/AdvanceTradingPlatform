"""Index option contract facts by date - expiry calendar, expiry choice, days to expiry, lot size and strike step.

Ported from Trade@0df3e09d0aa5942e3352327d5b998c5f6939ac61, elliott/contracts.py (NIFTY only there), generalised to
several underlyings through the `UNDERLYINGS` table.

An expiry is never "next Tuesday": a holiday moves it to the previous trading day. BANKNIFTY has no rule here at all:
its expiries are the dates NSE printed in its bhavcopies (`app.instruments.expiry_data`). Sources, in order:
  1. a listed-contracts calendar (the broker instrument master / bhavcopy: expiries with the day they were first listed),
     used causally - a contract counts only from its listing day;
  2. the rule calendar below (for backtests and as a fallback): the weekly weekday by date, the monthly = the last such
     weekday of the month, holiday -> previous trading day.
Trading days are full sessions; a muhurat / special session (well under a normal day's bars) is not a trading day.
Lot sizes: the live instrument master always wins in ATP; the NIFTY table here (from Trade) is the dated fallback for
backtests and is keyed by the contract's EXPIRY (a lot change applies to the series expiring on or after the date); other
underlyings use ATP's own lot table. Values marked "verify" are estimates to be checked against the exchange circulars;
they change rupee figures only, not R-multiples.
"""
import bisect
import datetime as dt
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

SESSION_MIN = 375                                   # NSE / BSE equity derivatives 09:15-15:30
CLOSE = dt.time(15, 30)
FULL_SESSION_MIN_BARS = 300                         # a muhurat / DR / half session (60-120 bars) is not a trading day

# weekdays: Monday = 0 ... Friday = 4
UNDERLYINGS: Dict[str, Dict] = {
    "NIFTY": {
        # (from date, weekday of the expiry) - Thursday until 31 Aug 2025, Tuesday from 1 Sep 2025.
        "weekday": [(dt.date(2000, 1, 1), 3), (dt.date(2025, 9, 1), 1)],
        "weekly_start": dt.date(2019, 2, 11),       # NIFTY weekly options listed (first weekly expiry 14 Feb 2019)
        "weekly_end": None,
        "lots": [(dt.date(2000, 1, 1), 25), (dt.date(2015, 10, 30), 75), (dt.date(2021, 7, 30), 50),
                 (dt.date(2024, 4, 26), 25), (dt.date(2024, 11, 20), 75), (dt.date(2026, 1, 6), 65)],
        "strike_step": 50,
    },
    "SENSEX": {
        # BSE: Friday, then Tuesday from 1 Jan 2025, Thursday from 1 Sep 2025 (verify against BSE circulars).
        "weekday": [(dt.date(2000, 1, 1), 4), (dt.date(2025, 1, 1), 1), (dt.date(2025, 9, 1), 3)],
        "weekly_start": dt.date(2023, 5, 15),       # verify
        "weekly_end": None,
        "lots": None,                               # no dated history here: app.backtest.options LOT_SIZES / LOT_SIZE_HISTORY
        "strike_step": 100,
    },
    "BANKNIFTY": {
        # No weekday rule: every expiry (weekly and monthly, with each change of weekday) comes from NSE's bhavcopies -
        # app.instruments.expiry_data. The rule helpers below refuse it.
        "weekday": None,
        "weekly_start": None,
        "weekly_end": None,
        "lots": None,                               # no dated history here: app.backtest.options LOT_SIZES / LOT_SIZE_HISTORY
        "strike_step": 100,
    },
}


ALIASES = {"NIFTY": "NIFTY", "NIFTY 50": "NIFTY", "NIFTY50": "NIFTY", "BANKNIFTY": "BANKNIFTY", "NIFTY BANK": "BANKNIFTY",
           "BANK NIFTY": "BANKNIFTY", "SENSEX": "SENSEX", "BSE SENSEX": "SENSEX"}


def _spec(underlying: str) -> Dict:
    key = ALIASES.get(" ".join(str(underlying).upper().split()))
    if key is None:
        raise KeyError(f"no contract calendar for {underlying!r}; known: {sorted(UNDERLYINGS)}")
    return UNDERLYINGS[key]


def _at(table: Sequence[Tuple[dt.date, object]], day: dt.date):
    v = table[0][1]
    for since, x in table:
        if day >= since:
            v = x
    return v


def known(underlying: str) -> bool:
    return ALIASES.get(" ".join(str(underlying).upper().split())) is not None


def dated_lot_size(underlying: str, day) -> Optional[int]:
    """The dated lot on `day` where this table has a full lot history for the underlying, else None."""
    if not known(underlying):
        return None
    lots = _spec(underlying)["lots"]
    return int(_at(lots, pd.Timestamp(day).date())) if lots else None


def lot_size(expiry, underlying: str = "NIFTY", listed_lots: Optional[Dict[dt.date, float]] = None) -> int:
    """The lot of the contract expiring on `expiry`. `listed_lots` {expiry date: lot} (instrument master / UDiFF) wins;
    an underlying without a dated history here uses ATP's lot table (`app.backtest.options.lot_size_on`)."""
    day = pd.Timestamp(expiry).date()
    if listed_lots and day in listed_lots and np.isfinite(listed_lots[day]):
        return int(listed_lots[day])
    dated = dated_lot_size(underlying, day)
    if dated is not None:
        return dated
    from app.backtest.options import lot_size_on      # lazy: options imports this module
    return int(lot_size_on(ALIASES[" ".join(str(underlying).upper().split())], day))


def weekly_listed(day, underlying: str = "NIFTY") -> bool:
    """Whether the underlying had weekly expiries on `day` (the first weekly expiry is a few days after the listing)."""
    spec = _rule(underlying)
    d = pd.Timestamp(day).date()
    ws, we = spec["weekly_start"], spec["weekly_end"]
    return ws is not None and d >= ws + dt.timedelta(days=3) and (we is None or d <= we)


def strike_step(underlying: str = "NIFTY") -> int:
    return int(_spec(underlying)["strike_step"])


def _rule(underlying: str) -> Dict:
    spec = _spec(underlying)
    if spec["weekday"] is None:
        from app.instruments.expiry_data import ExpiryDataMissing
        raise ExpiryDataMissing(f"{underlying} expiries come from NSE data (app.instruments.expiry_data), not a weekday rule")
    return spec


def expiry_weekday(day, underlying: str = "NIFTY") -> int:
    return int(_at(_rule(underlying)["weekday"], pd.Timestamp(day).date()))


class TradingCalendar:
    """Trading days (sorted). `days` = full sessions; outside their range: weekdays minus `extra_holidays`."""

    @classmethod
    def from_spot(cls, df1m: pd.DataFrame, holidays: Iterable = (), min_bars: int = FULL_SESSION_MIN_BARS) -> "TradingCalendar":
        """From 1-minute spot data: weekdays with at least `min_bars` bars (muhurat / weekend / half sessions dropped -
        otherwise an expiry and DTE come out wrong around Diwali). Days after the data: weekdays minus `holidays`."""
        ts = pd.to_datetime(df1m["timestamp"])
        n = ts.dt.normalize().value_counts()
        days = [d.date() for d, c in n.items() if c >= min_bars and d.weekday() < 5]
        return cls(days, holidays)

    def __init__(self, days: Iterable, extra_holidays: Iterable = ()) -> None:
        self.days = sorted({pd.Timestamp(d).date() for d in days})
        self.holidays = {pd.Timestamp(h).date() for h in extra_holidays}
        self._set = set(self.days)

    def is_trading(self, day: dt.date) -> bool:
        if self.days and self.days[0] <= day <= self.days[-1]:
            return day in self._set
        return day.weekday() < 5 and day not in self.holidays

    def prev_or_same(self, day: dt.date) -> dt.date:
        while not self.is_trading(day):
            day -= dt.timedelta(days=1)
        return day

    def sessions_between(self, a: dt.date, b: dt.date) -> int:
        """Trading sessions after a, up to and including b."""
        n, d = 0, a + dt.timedelta(days=1)
        while d <= b:
            n += self.is_trading(d)
            d += dt.timedelta(days=1)
        return n


def rule_expiries(cal: TradingCalendar, start, end, underlying: str = "NIFTY") -> List[Tuple[dt.date, str]]:
    """Rule calendar: [(expiry date, "weekly" | "monthly")]; holiday -> the previous trading day."""
    _rule(underlying)                                   # unknown underlying -> KeyError; data-only -> ExpiryDataMissing
    out: Dict[dt.date, str] = {}
    d = pd.Timestamp(start).date() - dt.timedelta(days=7)
    stop = pd.Timestamp(end).date() + dt.timedelta(days=40)
    while d <= stop:
        if d.weekday() == expiry_weekday(d, underlying):
            last_of_month = (d + dt.timedelta(days=7)).month != d.month
            if last_of_month or weekly_listed(d, underlying):
                e = cal.prev_or_same(d)
                out[e] = "monthly" if last_of_month else out.get(e, "weekly")
        d += dt.timedelta(days=1)
    return sorted(out.items())


class ExpiryBook:
    """Expiry choice. `listed` = DataFrame (expiry, kind, first_seen) from the instrument master / bhavcopy; an underlying
    without a rule (BANKNIFTY) reads `app.instruments.expiry_data`; else the rules."""

    def __init__(self, cal: TradingCalendar, listed: Optional[pd.DataFrame] = None, start=None, end=None, underlying: str = "NIFTY") -> None:
        self.cal = cal
        spec = _spec(underlying)
        if (listed is None or not len(listed)) and spec["weekday"] is None:
            from app.instruments import expiry_data
            rows = expiry_data.listed(ALIASES[" ".join(str(underlying).upper().split())])
            if not rows:
                raise expiry_data.ExpiryDataMissing(f"no NSE expiry data for {underlying}")
            listed = pd.DataFrame(rows, columns=["expiry", "kind", "first_seen"])
        if listed is not None and len(listed):
            b = listed.sort_values("expiry")
            self.items = [(pd.Timestamp(e).date(), k, pd.Timestamp(f).date()) for e, k, f in zip(b["expiry"], b["kind"], b["first_seen"])]
            self.source = "listed"
        else:
            ws = spec["weekly_start"]
            self.items = [(e, k, ws if k == "weekly" else None)
                          for e, k in rule_expiries(cal, start or cal.days[0], end or cal.days[-1], underlying)]
            self.source = "rule"
        self._dates = [x[0] for x in self.items]

    def listed_on(self, day: dt.date):
        """Expiries listed by `day` (first_seen <= day) and not yet expired."""
        i = bisect.bisect_left(self._dates, day)
        return [x for x in self.items[i:] if x[2] is None or x[2] <= day]

    def choose(self, fill_day, min_dte: int = 0, skip: int = 0) -> Optional[Tuple[dt.date, str]]:
        """The first expiry AFTER the fill day (an expiry today -> the next one); `min_dte` sessions at least; `skip`
        further ones (try the next weekly). (expiry date, kind) or None."""
        day = pd.Timestamp(fill_day).date()
        cands = [x for x in self.listed_on(day) if x[0] > day]
        if min_dte > 0:
            cands = [x for x in cands if self.cal.sessions_between(day, x[0]) >= min_dte]
        if len(cands) <= skip:
            return None
        e = cands[skip]
        return e[0], e[1]


def dte_days(cal: TradingCalendar, day, expiry) -> int:
    """Sessions after `day` up to the expiry (Monday -> Tuesday expiry = 1)."""
    return cal.sessions_between(pd.Timestamp(day).date(), pd.Timestamp(expiry).date())


def dte_frac(cal: TradingCalendar, ts, expiry, mode: str = "session_fraction") -> float:
    """session_fraction: (minutes left today + dte_days x 375) / 375; whole_days: dte_days."""
    ts = pd.Timestamp(ts)
    n = dte_days(cal, ts.date(), expiry)
    if mode == "whole_days":
        return float(n)
    close = pd.Timestamp.combine(ts.date(), CLOSE)
    left = max(0.0, min(SESSION_MIN, (close - ts).total_seconds() / 60.0))
    return (left + n * SESSION_MIN) / SESSION_MIN


def floor_grid(x: float, step: int) -> int:
    return int(np.floor(x / step) * step)


def ceil_grid(x: float, step: int) -> int:
    return int(np.ceil(x / step) * step)
