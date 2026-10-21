"""OC1: the automatic option chain page's server side - the expiry list with its default, and one chain ready to show.

Everything here is computed from real inputs only: the broker's chain (through the caller's own broker session, as
`POST /api/market-data/option-chains` reads it), the broker's last prices for the spot and the near-month future, the
NSE expiry file and the platform's instrument master. A value that cannot be computed is None, never a guess.
Nothing here places, changes or suggests an order.

* **Expiries.** The platform's instrument master (the broker's own listing, synced daily) when it has the underlying;
  otherwise the NSE expiry file (`app.instruments.expiry_data.expiries`) while it is current (up to its
  STALE_GRACE_DAYS after the last day it read; a stale or missing file is not used); otherwise, for SENSEX, the rule
  calendar (`app.instruments.expiry_calendar`, BSE holidays). With none of them the list is empty and the chain is read
  for the broker's own nearest expiry. Dates are IST. The default is the nearest expiry on or
  after today; **on the expiry day itself it is the next one** (the next weekly for an index with weeklies).
* **Summary.** ATM strike = the strike nearest the spot (a tie takes the lower strike); PCR (OI) = total put OI /
  total call OI; max pain = `analysis.compute_max_pain`; ATM IV = the mean of the call and put IV at the ATM strike
  (in percent); days to expiry = calendar days from today (IST) to the expiry; the strikes with the highest call OI
  and the highest put OI (a tie takes the lower strike).
* **IV.** `app.options_builder.chain.option_iv`: the broker's quote read both as a fraction and as a percentage and kept
  only if one reading reprices the option's last price (so a deep-ITM 2.5 % stays 2.5 %), else solved from that price;
  shown in percent. With no price to check against (no LTP, spot or expiry), a quote of 3 or more is a percentage and
  one under 3 gives no IV (it could be either reading). Greeks are Black-Scholes on that IV (`greeks.black_scholes`).
* **OI change.** The broker's change in OI since the previous session where it sends one. Otherwise it is the change
  since this organisation's first reading of the day for that underlying and expiry (kept in the cache while the page
  is open): "building" until that reading is BUILD_MINUTES old, during market hours only.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.models import OptionChain, OptionChainRow
from app.cache.client import cache_get, cache_set
from app.core.config import RISK_FREE_RATE
from app.instruments import expiry_calendar, expiry_data, master
from app.option_chain.analysis import compute_max_pain
from app.option_chain.greeks import BSInputs, black_scholes, time_to_expiry_years
from app.option_chain.models import OptionType
from app.option_chain.snapshots import IST, normalise_underlying
from app.options_builder.chain import option_iv

MAX_EXPIRIES = 10                 # the picker lists the next ten expiries at most
BUILD_MINUTES = 5                 # OI change from the first reading shows once that reading is this old
BASELINE_TTL_SECONDS = 3600       # the cache's cap; renewed on every read while the page is open
NO_PRICE_PERCENT_FLOOR = 3.0      # without a price to reprice: a quoted IV from here up can only be a percentage
MASTER_BROKER = master.UPSTOX_BROKER


def today_ist(now: Optional[datetime] = None) -> date:
    return (now or datetime.now(timezone.utc)).astimezone(IST).date()


def underlying_name(raw: str) -> str:
    """`nifty 50` -> `NIFTY`; `reliance` -> `RELIANCE`. ValueError for anything that is not a symbol."""
    return master.underlying_of(normalise_underlying(raw))


def spot_instrument(underlying: str) -> Tuple[str, str]:
    """(symbol, exchange) of the underlying's own price: `NIFTY 50` on NSE, `SENSEX` on BSE, a share on NSE."""
    if underlying in master.INDEX_SYMBOLS:
        return master.INDEX_SYMBOLS[underlying], master.INDEX_EXCHANGE.get(underlying, "NSE")
    return underlying, "NSE"


# ---------------------------------------------------------------------------------------------------- expiries

@dataclass(frozen=True)
class ExpiryItem:
    day: date
    kind: str                     # "weekly" | "monthly"

    def as_json(self) -> Dict[str, str]:
        return {"date": self.day.isoformat(), "kind": self.kind}


def default_expiry(expiries: Sequence[date], today: date) -> Optional[date]:
    """The nearest expiry on or after `today`; on an expiry day, the next one after it (when there is one)."""
    ahead = sorted(e for e in set(expiries) if e >= today)
    if not ahead:
        return None
    if ahead[0] == today and len(ahead) > 1:
        return ahead[1]
    return ahead[0]


def _kinds_by_month(days: Iterable[date]) -> List[ExpiryItem]:
    """The last expiry of a calendar month is the monthly one; the others are weeklies."""
    ordered = sorted(set(days))
    last_of_month: Dict[Tuple[int, int], date] = {}
    for d in ordered:
        last_of_month[(d.year, d.month)] = d
    return [ExpiryItem(d, "monthly" if last_of_month[(d.year, d.month)] == d else "weekly") for d in ordered]


def from_master(days: Iterable[date], today: date) -> List[ExpiryItem]:
    return _kinds_by_month(d for d in days if d >= today)[:MAX_EXPIRIES]


def from_expiry_file(underlying: str, today: date) -> List[ExpiryItem]:
    """The NSE expiry file's contracts on or after today, listed by today (`expiry_data.expiries`, with its own weekly /
    monthly kind). Empty when the file has no data for the underlying or is stale (more than STALE_GRACE_DAYS after the
    last day it read) - never an expiry the exchange did not list."""
    try:
        days = expiry_data.expiries(underlying, today, count=MAX_EXPIRIES)
    except expiry_data.ExpiryDataMissing:                  # ExpiryDataStale is one too
        return []
    monthly = {e for e, kind, _ in expiry_data.listed(underlying) if kind == "monthly"}
    return [ExpiryItem(e, "monthly" if e in monthly else "weekly") for e in days]


def from_rules(underlying: str, today: date, holidays: Iterable[date]) -> List[ExpiryItem]:
    """The rule calendar (SENSEX): the weekly weekday, monthly = the month's last, holiday -> the trading day before."""
    try:
        cal = expiry_calendar.TradingCalendar([], holidays)
        items = expiry_calendar.rule_expiries(cal, today, today + timedelta(days=70), underlying)
    except (KeyError, ValueError):
        return []
    return [ExpiryItem(d, k) for d, k in items if d >= today][:MAX_EXPIRIES]


async def master_expiries(session: AsyncSession, underlying: str, today: date) -> List[date]:
    return await master.expiries(session, underlying, broker=MASTER_BROKER, instrument_type="CE", on_or_after=today)


async def expiry_list(session: AsyncSession, underlying: str, today: date, holidays: Iterable[date] = ()) -> Tuple[List[ExpiryItem], str]:
    """(expiries, source) - source is "master", "nse_file", "rules" or "none"."""
    listed = await master_expiries(session, underlying, today)
    if listed:
        return from_master(listed, today), "master"
    filed = from_expiry_file(underlying, today)
    if filed:
        return filed, "nse_file"
    if expiry_calendar.known(underlying):
        ruled = from_rules(underlying, today, holidays)
        if ruled:
            return ruled, "rules"
    return [], "none"


@dataclass(frozen=True)
class FutureContract:
    symbol: str
    exchange: str
    expiry: date


async def near_future(session: AsyncSession, underlying: str, today: date) -> Optional[FutureContract]:
    """The underlying's near-month future in the instrument master (the first FUT expiry on or after today)."""
    expiries = await master.expiries(session, underlying, broker=MASTER_BROKER, instrument_type="FUT", on_or_after=today)
    if not expiries:
        return None
    row = await master.find_future(session, underlying, expiries[0], broker=MASTER_BROKER)
    if row is None or not row.tradingsymbol:
        return None
    return FutureContract(symbol=row.tradingsymbol.upper(), exchange=master.derivatives_exchange(underlying), expiry=expiries[0])


# ---------------------------------------------------------------------------------------------------- the chain

def _finite(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def atm_strike(strikes: Sequence[float], spot: Optional[float]) -> Optional[float]:
    if spot is None or not strikes:
        return None
    return min(strikes, key=lambda s: (abs(s - spot), s))


def pcr_oi(rows: Sequence[OptionChainRow]) -> Optional[float]:
    calls = sum(r.call_oi or 0.0 for r in rows)
    puts = sum(r.put_oi or 0.0 for r in rows)
    return puts / calls if calls > 0 else None


def highest_oi_strike(rows: Sequence[OptionChainRow], side: str) -> Optional[float]:
    best: Optional[Tuple[float, float]] = None
    for r in rows:
        oi = r.call_oi if side == "call" else r.put_oi
        if oi is None or oi <= 0:
            continue
        if best is None or oi > best[1] or (oi == best[1] and r.strike < best[0]):
            best = (r.strike, oi)
    return best[0] if best else None


def _leg(row: OptionChainRow, side: str, spot: Optional[float], years: Optional[float], change: Optional[float]) -> Dict[str, Optional[float]]:
    p = "call_" if side == "call" else "put_"
    ltp = _finite(getattr(row, p + "ltp"))
    quoted = _finite(getattr(row, p + "iv"))
    quoted = quoted if quoted is not None and quoted > 0 else None
    kind = OptionType.CALL if side == "call" else OptionType.PUT
    if ltp is not None and ltp > 0 and spot is not None and years is not None and years > 0:
        sigma = option_iv(quoted, ltp, spot, row.strike, years, RISK_FREE_RATE, kind)
    elif quoted is not None and quoted >= NO_PRICE_PERCENT_FLOOR:
        # Nothing to reprice against: only a quote that can only be a percentage (3 and up) is shown; under 3 it could
        # be a fraction (0.14) or a real low percentage (2.5), so no IV is shown rather than a guess.
        sigma = quoted / 100.0
    else:
        sigma = None
    iv = sigma * 100.0 if sigma is not None else None
    greeks: Dict[str, Optional[float]] = {"delta": _finite(getattr(row, p + "delta")), "gamma": None, "theta": None, "vega": None}
    if iv is not None and spot is not None and years is not None:
        try:
            bs = black_scholes(BSInputs(spot, row.strike, years, RISK_FREE_RATE, iv / 100.0, kind))
            greeks = {"delta": bs.delta, "gamma": bs.gamma, "theta": bs.theta, "vega": bs.vega}
        except ValueError:
            pass
    return {"oi": _finite(getattr(row, p + "oi")), "oi_change": change, "ltp": ltp, "iv": iv,
            "volume": _finite(getattr(row, p + "volume")), "bid": _finite(getattr(row, p + "bid")),
            "ask": _finite(getattr(row, p + "ask")), **greeks}


def broker_has_oi_change(rows: Sequence[OptionChainRow]) -> bool:
    return any(r.call_change_oi is not None or r.put_change_oi is not None for r in rows)


@dataclass
class OiChange:
    status: str                                        # "ready" | "building" | "none"
    source: Optional[str]                              # "broker" | "first_reading" | None
    since: Optional[datetime] = None
    baseline: Optional[Dict[float, Tuple[Optional[float], Optional[float]]]] = None

    def as_json(self) -> Dict[str, Optional[str]]:
        return {"status": self.status, "source": self.source, "since": self.since.isoformat() if self.since else None}


def baseline_key(tenant_id: int, underlying: str, expiry: str, day: date) -> str:
    return f"oc1:oi_first:{tenant_id}:{underlying}:{expiry}:{day.isoformat()}"


def _baseline_json(rows: Sequence[OptionChainRow], at: datetime) -> str:
    return json.dumps({"at": at.isoformat(), "oi": [[r.strike, r.call_oi, r.put_oi] for r in rows]})


def _read_baseline(text: Optional[str]) -> Optional[Tuple[datetime, Dict[float, Tuple[Optional[float], Optional[float]]]]]:
    if not text:
        return None
    try:
        data = json.loads(text)
        at = datetime.fromisoformat(data["at"])
        return at, {float(s): (_finite(c), _finite(p)) for s, c, p in data["oi"]}
    except (ValueError, KeyError, TypeError):
        return None


async def oi_change_state(chain: OptionChain, *, tenant_id: int, underlying: str, now: datetime, market_open: bool) -> OiChange:
    """Where the OI change comes from (see the module docstring); stores the first reading of the day when needed."""
    if broker_has_oi_change(chain.rows):
        return OiChange("ready", "broker")
    key = baseline_key(tenant_id, underlying, chain.expiry, today_ist(now))
    stored = _read_baseline(await cache_get(key))
    if stored is None:
        if not market_open or not chain.rows:
            return OiChange("none", None)
        await cache_set(key, _baseline_json(chain.rows, now), BASELINE_TTL_SECONDS)
        return OiChange("building", "first_reading", since=now)
    at, oi = stored
    await cache_set(key, _baseline_json([OptionChainRow(strike=s, call_oi=c, put_oi=p) for s, (c, p) in oi.items()], at), BASELINE_TTL_SECONDS)
    if now - at < timedelta(minutes=BUILD_MINUTES):
        return OiChange("building", "first_reading", since=at)
    return OiChange("ready", "first_reading", since=at, baseline=oi)


def _change(now_oi: Optional[float], then: Optional[float]) -> Optional[float]:
    return now_oi - then if now_oi is not None and then is not None else None


def build_view(chain: OptionChain, *, spot: Optional[float], future: Optional[Dict[str, Any]], expiry: Optional[date],
               now: datetime, oi: OiChange) -> Dict[str, Any]:
    """The page's whole answer for one chain: summary, rows (both legs with IV and Greeks) and the OI-change state."""
    rows = sorted(chain.rows, key=lambda r: r.strike)
    today = today_ist(now)
    years = time_to_expiry_years(expiry, now) if expiry is not None else None
    atm = atm_strike([r.strike for r in rows], spot)
    out_rows: List[Dict[str, Any]] = []
    for r in rows:
        if oi.status != "ready":
            call_change = put_change = None
        elif oi.source == "broker":
            call_change, put_change = _finite(r.call_change_oi), _finite(r.put_change_oi)
        else:
            base = (oi.baseline or {}).get(r.strike, (None, None))
            call_change, put_change = _change(_finite(r.call_oi), base[0]), _change(_finite(r.put_oi), base[1])
        out_rows.append({"strike": r.strike, "call": _leg(r, "call", spot, years, call_change),
                         "put": _leg(r, "put", spot, years, put_change)})
    atm_ivs = [v for row in out_rows if row["strike"] == atm for v in (row["call"]["iv"], row["put"]["iv"]) if v is not None]
    summary = {
        "spot": spot,
        "futures": future,
        "atm_strike": atm,
        "pcr_oi": pcr_oi(rows),
        "max_pain": compute_max_pain(rows),
        "atm_iv": sum(atm_ivs) / len(atm_ivs) if atm_ivs else None,
        "days_to_expiry": (expiry - today).days if expiry is not None else None,
        "max_call_oi_strike": highest_oi_strike(rows, "call"),
        "max_put_oi_strike": highest_oi_strike(rows, "put"),
        "total_call_oi": sum(r.call_oi or 0.0 for r in rows),
        "total_put_oi": sum(r.put_oi or 0.0 for r in rows),
    }
    return {"underlying": chain.underlying, "expiry": expiry.isoformat() if expiry else (chain.expiry or None),
            "summary": summary, "oi_change": oi.as_json(), "rows": out_rows, "fetched_at": now.isoformat()}


def parse_expiry(text: Optional[str]) -> Optional[date]:
    try:
        return date.fromisoformat(text) if text else None
    except ValueError:
        return None
