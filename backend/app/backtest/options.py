"""Phase W (master prompt sections 31-32, V2.1-2.6): the option-pricing side of a historical
option backtest - what a leg would have cost at a moment in the past.

Two sources, one interface (`OptionPricer.price`):

* `SnapshotPricer` - real quotes: option-chain rows the platform recorded while the worker ran
  (`option_chain_snapshots`, app/backtest/chain_recorder.py) or rows the user uploaded. A leg is
  priced from the latest row at or before the bar, no older than `max_age_minutes`; a missing
  row is a refusal (no trade, counted) unless a fallback pricer is given.
* `SyntheticPricer` - Black-Scholes (app/option_chain/greeks.py) off the underlying bar, a
  volatility model and the time to the expiry's 15:30 IST close. Volatility is either the fixed
  implied volatility the user asks for or the realised volatility of the trailing closes. This
  is an approximation and every result built on it says so (`BacktestResult.options.pricing`):
  no smile, no vol-of-vol, no bid/ask - good for structure mechanics (strikes, expiries, exits,
  sizing), not for claiming an edge.

Contract conventions (lot sizes, strike steps, expiry weekdays) are the exchange's current ones
and each can be overridden per run, because they have changed over the years (NIFTY weeklies
moved from Thursday to Tuesday in September 2025; BANKNIFTY lost its weekly in November 2024).
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Protocol, Sequence, Tuple

import pandas as pd
from pydantic import BaseModel, Field

from app.core.enums import ExpiryRule
from app.instruments.contracts import select_expiry
from app.instruments.master import INDEX_SYMBOLS, underlying_of
from app.market_data.calendar import IST
from app.instruments import expiry_calendar, expiry_data
from app.option_chain.greeks import BSInputs, black_scholes
from app.option_chain.models import OptionType

TUESDAY, THURSDAY = 1, 3
SESSION_CLOSE = time(15, 30)
TICK = 0.05
MIN_PREMIUM = 0.05
MIN_IV, MAX_IV = 0.08, 1.50
DEFAULT_RISK_FREE_RATE = 0.07
MIN_TIME_TO_EXPIRY_YEARS = 1.0 / 365.0 / 24.0   # one hour, as the Greeks module floors it

# Current exchange conventions (September 2026). Overridable per run.
LOT_SIZES: Dict[str, int] = {"NIFTY": 65, "BANKNIFTY": 35, "FINNIFTY": 65, "MIDCPNIFTY": 140, "NIFTYNXT50": 25,
                             "SENSEX": 20, "BANKEX": 30}
STRIKE_STEPS: Dict[str, float] = {"NIFTY": 50.0, "BANKNIFTY": 100.0, "FINNIFTY": 50.0, "MIDCPNIFTY": 25.0, "NIFTYNXT50": 100.0,
                                  "SENSEX": 100.0, "BANKEX": 100.0}
# Weekly expiries only where the exchange still lists them; every other underlying is monthly.
WEEKLY_EXPIRY_WEEKDAY: Dict[str, int] = {"NIFTY": TUESDAY, "SENSEX": THURSDAY}
MONTHLY_EXPIRY_WEEKDAY: Dict[str, int] = {"SENSEX": THURSDAY, "BANKEX": THURSDAY}
DEFAULT_MONTHLY_WEEKDAY = TUESDAY   # NSE: last Tuesday of the month since September 2025
DEFAULT_STOCK_LOT = 1
LADDER_SPAN = 40                    # strikes listed either side of spot


def default_lot_size(underlying: str) -> int:
    return LOT_SIZES.get(underlying.upper(), DEFAULT_STOCK_LOT)


# P0.6 / B5: lot sizes that applied *before* a dated revision, keyed by the backtest's entry date. Each entry:
# the day from which the exchange's new lot applied to new contracts -> the lot that applied before it. Two stated
# approximations: (1) only the 20 Nov 2024 NSE index revision is dated here (NIFTY 25 -> 75, BANKNIFTY 15 -> 30,
# FINNIFTY 25 -> 65, MIDCPNIFTY 50 -> 120, NIFTYNXT50 10 -> 25); entries between that day and any later revision
# that `LOT_SIZES` above already reflects use the current lot, not the intermediate one; (2) history before the
# oldest dated entry uses that entry's "before" lot (BANKNIFTY in early 2023 was not 15 - the table has no older
# row). The exchange applied each revision per contract as new series were listed; this table applies it by entry
# date. Add a dated row from the exchange circular, never from memory.
LOT_SIZE_HISTORY: Dict[str, List[Tuple[date, int]]] = {
    "NIFTY": [(date(2024, 11, 20), 25)],
    "BANKNIFTY": [(date(2024, 11, 20), 15)],
    "FINNIFTY": [(date(2024, 11, 20), 25)],
    "MIDCPNIFTY": [(date(2024, 11, 20), 50)],
    "NIFTYNXT50": [(date(2024, 11, 20), 10)],
}


def lot_size_on(underlying: str, day: date) -> int:
    """The contract lot that applied to entries on `day`: the current lot after the last dated revision, the
    pre-revision lot before it. Underlyings with a full dated lot table in `app.instruments.expiry_calendar` (NIFTY,
    ported from Trade) use that table instead."""
    dated = expiry_calendar.dated_lot_size(underlying, day)
    if dated is not None:
        return dated
    key = underlying.upper()
    for effective_from, before in sorted(LOT_SIZE_HISTORY.get(key, []), key=lambda e: e[0]):
        if day < effective_from:
            return before
    return default_lot_size(key)


def lot_size_for(underlying: str, entry_day: date, expiry: Optional[date] = None) -> int:
    """The lot of the contract actually traded: the dated table is keyed by the contract's EXPIRY where it exists (a
    revision applies to the series expiring on or after its date); otherwise the entry-day lot (`lot_size_on`)."""
    if expiry is not None and expiry_calendar.dated_lot_size(underlying, expiry) is not None:
        return expiry_calendar.lot_size(expiry, underlying)
    return lot_size_on(underlying, entry_day)


def default_strike_step(underlying: str, spot: float) -> float:
    step = STRIKE_STEPS.get(underlying.upper())
    if step:
        return step
    # NSE stock options: the step scales with the price band (ITC ~400: 5; RELIANCE ~1450: 20).
    for ceiling, step in ((50, 1.0), (250, 2.5), (500, 5.0), (1000, 10.0), (2500, 20.0), (5000, 50.0)):
        if spot < ceiling:
            return step
    return 100.0


def round_tick(price: float) -> float:
    return max(MIN_PREMIUM, round(round(price / TICK) * TICK, 2))


def strike_ladder(spot: float, step: float, span: int = LADDER_SPAN) -> List[float]:
    """Listed strikes around `spot`: `span` steps either side of the nearest strike."""
    if spot <= 0 or step <= 0:
        return []
    centre = round(spot / step) * step
    return [round(centre + i * step, 2) for i in range(-span, span + 1) if centre + i * step > 0]


def bars_per_year(timeframe: str) -> float:
    """Annualisation factor for the realised-volatility estimate: the 375 one-minute bars of an
    NSE session times 250 sessions, scaled to the timeframe's minutes; 250 for daily bars."""
    tf = (timeframe or "1min").lower().strip()
    if tf in ("1d", "d", "day", "daily", "1day"):
        return 250.0
    digits = "".join(ch for ch in tf if ch.isdigit()) or "1"
    minutes = int(digits) * (60 if tf.endswith("h") else 1)
    return 250.0 * 375.0 / max(1, minutes)


# --- expiries ------------------------------------------------------------------------------

@dataclass(frozen=True)
class ExpiryCalendar:
    """Synthetic listing calendar: weekly expiries on `weekday` (or only the last such weekday
    of each month when `weekly` is False), moved to the previous trading day when they fall
    on a holiday - the exchange's own rule. With `dated_underlying` (an underlying with a dated
    weekday table in `app.instruments.expiry_calendar`, today SENSEX, and no pinned weekday) the
    weekday is the one in force on each date. With `data_symbol` (NIFTY, BANKNIFTY - an underlying
    in `expiry_data.DATA_DRIVEN`, no pinned weekday) the expiries are the ones NSE listed, read
    from its bhavcopies - no weekday rule; `weekly` False keeps the monthly ones only,
    otherwise every listed expiry counts (BANKNIFTY weeklies until they ended, monthlies after)."""
    weekday: int
    weekly: bool
    holidays: frozenset = field(default_factory=frozenset)
    dated_underlying: Optional[str] = None
    force_weekly: bool = False          # an explicit weekly=True: weeklies on every date, listed or not
    data_symbol: Optional[str] = None   # expiries from NSE data (app.instruments.expiry_data)

    @classmethod
    def for_underlying(cls, underlying: str, holidays: Iterable[date] = (), *, weekday: Optional[int] = None,
                       weekly: Optional[bool] = None) -> "ExpiryCalendar":
        key = underlying.upper()
        canonical = expiry_calendar.ALIASES.get(" ".join(key.split()), key)
        if weekday is None and expiry_data.data_driven(canonical):
            # Listed expiries only; `weekday` stays as a label fallback for a run that traded nothing.
            return cls(weekday=MONTHLY_EXPIRY_WEEKDAY.get(key, DEFAULT_MONTHLY_WEEKDAY), weekly=weekly is not False,
                       holidays=frozenset(holidays), data_symbol=canonical)
        default_weekly = key in WEEKLY_EXPIRY_WEEKDAY
        is_weekly = default_weekly if weekly is None else weekly
        dated = key if weekday is None and expiry_calendar.known(key) else None
        if weekday is None:
            weekday = WEEKLY_EXPIRY_WEEKDAY.get(key) if is_weekly and key in WEEKLY_EXPIRY_WEEKDAY else \
                MONTHLY_EXPIRY_WEEKDAY.get(key, DEFAULT_MONTHLY_WEEKDAY)
        return cls(weekday=weekday, weekly=is_weekly, holidays=frozenset(holidays), dated_underlying=dated,
                   force_weekly=bool(weekly))

    def _trading_day_on_or_before(self, day: date) -> date:
        for _ in range(15):
            if day.weekday() < 5 and day not in self.holidays:
                return day
            day -= timedelta(days=1)
        return day

    def _last_weekday_of_month(self, year: int, month: int) -> date:
        nxt = date(year + (month // 12), month % 12 + 1, 1)
        last = nxt - timedelta(days=1)
        return last - timedelta(days=(last.weekday() - self.weekday) % 7)

    def _dated_expiries(self, on_or_after: date, count: int) -> List[date]:
        """Day-by-day walk with the weekday of each date (expiry_calendar): a weekly where the
        underlying listed weeklies on that date, else only the last such weekday of the month."""
        assert self.dated_underlying is not None
        out: List[date] = []
        day = on_or_after - timedelta(days=7)
        for _ in range(400 * max(1, count)):
            if day.weekday() == expiry_calendar.expiry_weekday(day, self.dated_underlying):
                last_of_month = (day + timedelta(days=7)).month != day.month
                if last_of_month or (self.weekly and (self.force_weekly or expiry_calendar.weekly_listed(day, self.dated_underlying))):
                    actual = self._trading_day_on_or_before(day)
                    if actual >= on_or_after and actual not in out:
                        out.append(actual)
                        if len(out) >= count:
                            break
            day += timedelta(days=1)
        return sorted(out)

    def expiries(self, on_or_after: date, count: int = 6) -> List[date]:
        """The next `count` listed expiries on/after the date (post holiday shift)."""
        if self.data_symbol is not None:
            return expiry_data.expiries(self.data_symbol, on_or_after, count, monthly_only=not self.weekly)
        if self.dated_underlying is not None:
            return self._dated_expiries(on_or_after, count)
        out: List[date] = []
        if self.weekly:
            nominal = on_or_after + timedelta(days=(self.weekday - on_or_after.weekday()) % 7)
            # The shifted date of the *following* week's expiry can still be >= on_or_after when this
            # week's moved back before it; start one week earlier and filter.
            nominal -= timedelta(days=7)
            while len(out) < count:
                actual = self._trading_day_on_or_before(nominal)
                if actual >= on_or_after and actual not in out:
                    out.append(actual)
                nominal += timedelta(days=7)
            return out
        year, month = on_or_after.year, on_or_after.month
        # Same reasoning for months: last month's shifted expiry cannot be on/after today, but check.
        month -= 1
        if month == 0:
            year, month = year - 1, 12
        while len(out) < count:
            actual = self._trading_day_on_or_before(self._last_weekday_of_month(year, month))
            if actual >= on_or_after:
                out.append(actual)
            month += 1
            if month == 13:
                year, month = year + 1, 1
        return out

    def select(self, rule: ExpiryRule, today: date) -> Optional[date]:
        if self.data_symbol is not None and rule == ExpiryRule.MONTHLY:
            # a weekly can fall after the monthly in its month (BANKNIFTY 31 Jan 2024): MONTHLY means the monthly
            return select_expiry(expiry_data.expiries(self.data_symbol, today, monthly_only=True), rule, today)
        return select_expiry(self.expiries(today), rule, today)

    def far_expiry(self, near: date, as_of: Optional[date] = None) -> date:
        """The next expiry after `near`, as listed on `as_of` (the bar day; data calendars only). StopIteration if none."""
        if self.data_symbol is not None:
            listed = expiry_data.expiries(self.data_symbol, near + timedelta(days=1), monthly_only=not self.weekly,
                                          as_of=as_of or near)
            return next(e for e in listed if e > near)
        return next(e for e in self.expiries(near + timedelta(days=1)) if e > near)


def expiry_instant(expiry: date) -> datetime:
    """The moment a contract stops trading: the exchange close on expiry day, in UTC."""
    return datetime.combine(expiry, SESSION_CLOSE, tzinfo=IST).astimezone(timezone.utc)


def to_utc(ts) -> datetime:
    """Bar timestamps: naive ones are IST wall time (the way candles are uploaded and the
    strategies read them), aware ones are converted."""
    if isinstance(ts, pd.Timestamp):
        ts = ts.to_pydatetime()
    if ts.tzinfo is None:
        return ts.replace(tzinfo=IST).astimezone(timezone.utc)
    return ts.astimezone(timezone.utc)


def years_to_expiry(expiry: date, at: datetime) -> float:
    seconds = (expiry_instant(expiry) - at).total_seconds()
    return max(seconds / (365.0 * 86400.0), MIN_TIME_TO_EXPIRY_YEARS)


# --- pricers -------------------------------------------------------------------------------

class PricingUnavailable(ValueError):
    """No quote for the leg at that moment - the engine records the reason and takes no trade."""


class OptionPricer(Protocol):
    name: str

    def price(self, right: str, strike: float, expiry: date, *, spot: float, at: datetime) -> float: ...

    def describe(self) -> str: ...


@dataclass
class VolatilityModel:
    """Fixed implied volatility, or realised volatility of the trailing closes (annualised log-return
    standard deviation over `window` bars), floored/capped to stay sane on quiet or gapped data."""
    fixed_iv: Optional[float] = None
    window: int = 20
    annualisation: float = 250.0 * 375.0
    _current: float = field(default=0.20, init=False, repr=False)

    def update(self, closes: Sequence[float]) -> float:
        """Realised vol from the trailing closes (ignored under a fixed IV). Returns the vol in use."""
        if self.fixed_iv is not None:
            self._current = min(MAX_IV, max(MIN_IV, self.fixed_iv))
            return self._current
        series = [float(c) for c in closes[-(self.window + 1):] if c and c > 0]
        if len(series) >= 3:
            returns = [math.log(b / a) for a, b in zip(series, series[1:])]
            mean = sum(returns) / len(returns)
            var = sum((r - mean) ** 2 for r in returns) / max(1, len(returns) - 1)
            realised = math.sqrt(var * self.annualisation)
            # A flat window floors at MIN_IV rather than pricing options as worthless.
            self._current = min(MAX_IV, max(MIN_IV, realised))
        return self._current

    @property
    def current(self) -> float:
        return self._current

    def describe(self) -> str:
        return f"fixed IV {self.fixed_iv:.0%}" if self.fixed_iv is not None else f"realised volatility of the last {self.window} bars"


@dataclass
class SyntheticPricer:
    """Black-Scholes premiums off the underlying bar. `vol.update(closes)` is called by the
    engine on every bar so `price` reads the model's current volatility."""
    vol: VolatilityModel
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE
    name: str = "synthetic"

    def price(self, right: str, strike: float, expiry: date, *, spot: float, at: datetime) -> float:
        if spot <= 0 or strike <= 0:
            raise PricingUnavailable(f"cannot price {int(strike)} {right} off spot {spot}")
        t = years_to_expiry(expiry, at)
        if (expiry_instant(expiry) - at).total_seconds() <= 0:
            # At/after the close of expiry day the contract is worth its intrinsic value.
            return round_tick(max(spot - strike, 0.0) if right == "CE" else max(strike - spot, 0.0))
        result = black_scholes(BSInputs(spot, strike, t, self.risk_free_rate, self.vol.current,
                                        OptionType.CALL if right == "CE" else OptionType.PUT))
        return round_tick(result.theoretical_price)

    def describe(self) -> str:
        return f"Black-Scholes, {self.vol.describe()}, r={self.risk_free_rate:.1%}"


class OptionChainSnapshotRow(BaseModel):
    """One quoted option at one moment - the unit of recorded or uploaded chain history."""
    timestamp: datetime
    expiry: date
    strike: float = Field(gt=0)
    right: str = Field(pattern="^(CE|PE|ce|pe)$")
    ltp: float = Field(gt=0)
    iv: Optional[float] = None
    oi: Optional[float] = None
    underlying_ltp: Optional[float] = None


@dataclass
class SnapshotPricer:
    """Real quotes indexed by contract; the latest row at/before the bar within `max_age_minutes`.
    With `fallback` set, a leg without a fresh row is priced synthetically and counted."""
    rows: Sequence[OptionChainSnapshotRow]
    max_age_minutes: int = 15
    fallback: Optional[SyntheticPricer] = None
    name: str = "snapshots"
    fallbacks: int = field(default=0, init=False)
    hits: int = field(default=0, init=False)
    _index: Dict[Tuple[date, float, str], Tuple[List[datetime], List[float]]] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        grouped: Dict[Tuple[date, float, str], List[Tuple[datetime, float]]] = {}
        for row in self.rows:
            grouped.setdefault((row.expiry, float(row.strike), row.right.upper()), []).append((to_utc(row.timestamp), float(row.ltp)))
        for key, points in grouped.items():
            points.sort()
            self._index[key] = ([p[0] for p in points], [p[1] for p in points])

    @property
    def contracts(self) -> int:
        return len(self._index)

    def price(self, right: str, strike: float, expiry: date, *, spot: float, at: datetime) -> float:
        series = self._index.get((expiry, float(strike), right.upper()))
        if series is not None:
            times, prices = series
            i = bisect.bisect_right(times, at) - 1
            if i >= 0 and (at - times[i]) <= timedelta(minutes=self.max_age_minutes):
                self.hits += 1
                return prices[i]
        if self.fallback is not None:
            self.fallbacks += 1
            return self.fallback.price(right, strike, expiry, spot=spot, at=at)
        raise PricingUnavailable(f"no recorded quote for {int(strike)} {right} {expiry.isoformat()} within {self.max_age_minutes} min of {at.isoformat()}")

    def describe(self) -> str:
        base = f"recorded chain quotes ({self.contracts} contracts, max age {self.max_age_minutes} min)"
        return base + (f"; synthetic fallback ({self.fallback.describe()})" if self.fallback is not None else "; no fallback")


def underlying_name(symbol: str) -> Tuple[str, str]:
    """(master underlying, candle symbol) for a deployment/backtest symbol: NIFTY 50 -> (NIFTY, NIFTY 50)."""
    underlying = underlying_of(symbol)
    return underlying, INDEX_SYMBOLS.get(underlying, symbol.upper().strip())
