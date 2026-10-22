"""FNO-3 (owner order ATP_SESSION_FNO_WORKSPACE.md, sections 1 and 4): the symbol hub's facts and the futures chain.

* **The hub** (`hub_info`): whether a symbol has F&O at all, and what the hub tabs and the Overview show about it - from
  the platform's instrument master only (the broker's own listing, synced daily; no broker call): the F&O underlying
  name, the lot size, the next futures and option expiries. With no master rows, the NSE expiry file or the rule
  calendar (as the option chain page lists expiries, `chain_view.expiry_list`) still says the symbol has options.
* **The futures chain** (`futures_view`): every listed future of the underlying on or after today (IST), each with its
  last price and change from the previous close, its open interest (OI) and OI change, and its basis against the spot;
  then the rollover from the near month to the next. Prices come from the user's own broker session (the caller passes
  the quotes it read); a value that cannot be computed is None, never a guess, and never sample data.

The figures:
* **Change** = LTP - the previous session's close (as the broker's quote gives it); % of that close.
* **Basis** = the future's LTP - the spot's LTP; % of the spot. Positive = the future trades above the spot.
* **OI change** = the change since this organisation's first reading of the day for the underlying (the broker's quote
  has no OI change), kept in the cache while the page is open: "building" until that reading is
  `chain_view.BUILD_MINUTES` old, during market hours only (the option chain's own rule, `chain_view.oi_change_state`).
* **Rollover (near -> next)** = `oi_regime.compute_rollover_proxy` on the two futures: the share of the near + next
  futures OI that is in the next month (%), and the spread = next month's LTP - near month's LTP. Each future is given
  to it as a one-row chain whose call OI is the future's OI and whose synthetic price (strike + call - put) is the
  future's LTP, so the share and the spread are exactly the proxy's own arithmetic. The caption states the two numbers
  in plain words and nothing more (no reading of direction, no advice).

Nothing here places, changes or suggests an order.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.models import OptionChain, OptionChainRow, Quote
from app.cache.client import cache_get, cache_set
from app.db.models import InstrumentRecord
from app.instruments import master
from app.option_chain import chain_view
from app.option_chain.oi_regime import compute_rollover_proxy

MAX_FUTURES = 4                   # the near, next and far months (and a fourth while two overlap around an expiry)
MAX_HUB_EXPIRIES = 4              # the Overview lists the next few
MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
Pair = Tuple[str, str]            # (exchange, symbol)


@dataclass(frozen=True)
class Future:
    underlying: str
    tradingsymbol: str
    exchange: str
    instrument_key: str
    expiry: date
    lot_size: int
    tick_size: float

    @property
    def label(self) -> str:
        return future_label(self.underlying, self.expiry)

    def as_dict(self) -> Dict[str, Any]:
        return {"underlying": self.underlying, "tradingsymbol": self.tradingsymbol, "exchange": self.exchange,
                "instrument_key": self.instrument_key, "expiry": self.expiry.isoformat(), "lot_size": self.lot_size,
                "tick_size": self.tick_size, "label": self.label}


def future_label(underlying: str, expiry: date) -> str:
    """A future as traders write it: "NIFTY 28 OCT FUT"."""
    return f"{underlying} {expiry.day:02d} {MONTHS[expiry.month - 1]} FUT"


def _finite(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _price(v: Any) -> Optional[float]:
    f = _finite(v)
    return f if f is not None and f > 0 else None


# ---------------------------------------------------------------------------------------------------- the master

def _row_future(r: InstrumentRecord, underlying: str) -> Optional[Future]:
    if r.expiry is None or not r.tradingsymbol:
        return None
    return Future(underlying=underlying, tradingsymbol=r.tradingsymbol.upper(), exchange=(r.exchange or master.derivatives_exchange(underlying)).upper(),
                  instrument_key=r.instrument_key, expiry=r.expiry, lot_size=int(r.lot_size or 1), tick_size=float(r.tick_size or 0.05))


async def listed_futures(session: AsyncSession, underlying: str, today: date, limit: int = MAX_FUTURES) -> List[Future]:
    """The underlying's futures on or after `today`, nearest first, one per expiry: Upstox's master first (ATP's own
    data connection), else any broker's (as the order ticket finds a contract)."""
    rows = list(await session.scalars(select(InstrumentRecord).where(
        InstrumentRecord.underlying == underlying, InstrumentRecord.instrument_type == "FUT", InstrumentRecord.expiry.is_not(None),
        InstrumentRecord.expiry >= today,
    ).order_by(InstrumentRecord.expiry, InstrumentRecord.broker != master.UPSTOX_BROKER, InstrumentRecord.broker)))
    out: List[Future] = []
    seen: set = set()
    for r in rows:
        if r.expiry in seen:
            continue
        f = _row_future(r, underlying)
        if f is None:
            continue
        seen.add(r.expiry)
        out.append(f)
        if len(out) >= limit:
            break
    return out


async def find_future(session: AsyncSession, underlying: str, expiry: date) -> Optional[Future]:
    """One future from the master (the order ticket's contract for a FUT order)."""
    row = await master.find_future(session, underlying, expiry)
    if row is None:
        row = await session.scalar(select(InstrumentRecord).where(
            InstrumentRecord.underlying == underlying, InstrumentRecord.instrument_type == "FUT", InstrumentRecord.expiry == expiry,
        ).order_by(InstrumentRecord.broker).limit(1))
    return _row_future(row, underlying) if row is not None else None


async def _option_lot(session: AsyncSession, underlying: str, today: date) -> Optional[int]:
    row = await session.scalar(select(InstrumentRecord.lot_size).where(
        InstrumentRecord.underlying == underlying, InstrumentRecord.instrument_type.in_(master.OPTION_TYPES),
        InstrumentRecord.expiry.is_not(None), InstrumentRecord.expiry >= today,
    ).order_by(InstrumentRecord.expiry, InstrumentRecord.broker != master.UPSTOX_BROKER).limit(1))
    return int(row) if row else None


async def hub_info(session: AsyncSession, symbol: str, today: date, holidays: Sequence[date] = ()) -> Dict[str, Any]:
    """What the hub needs to know about a symbol (see the module docstring). `symbol` is the chart's spelling."""
    underlying = chain_view.underlying_name(symbol)
    futures = await listed_futures(session, underlying, today)
    items, source = await chain_view.expiry_list(session, underlying, today, holidays)
    has_options = bool(items)
    has_futures = bool(futures)
    lot = futures[0].lot_size if futures else await _option_lot(session, underlying, today)
    spot_symbol, spot_exchange = chain_view.spot_instrument(underlying)
    return {
        "symbol": " ".join(symbol.upper().split()), "underlying": underlying, "spot_symbol": spot_symbol, "spot_exchange": spot_exchange,
        "has_fno": has_options or has_futures, "has_options": has_options, "has_futures": has_futures,
        "lot_size": lot,
        "option_expiries": [i.as_json() for i in items[:MAX_HUB_EXPIRIES]],
        "default_option_expiry": (d.isoformat() if (d := chain_view.default_expiry([i.day for i in items], today)) else None),
        "future_expiries": [f.expiry.isoformat() for f in futures],
        "expiry_source": source, "today": today.isoformat(),
    }


# ---------------------------------------------------------------------------------------------------- pure figures

def change_of(ltp: Optional[float], prev_close: Optional[float]) -> Tuple[Optional[float], Optional[float]]:
    if ltp is None or prev_close is None or not prev_close > 0:
        return None, None
    change = round(ltp - prev_close, 2)
    return change, round(change / prev_close * 100.0, 2)


def basis_of(future_ltp: Optional[float], spot: Optional[float]) -> Tuple[Optional[float], Optional[float]]:
    """(future - spot, as % of the spot); None without both prices."""
    if future_ltp is None or spot is None or not spot > 0:
        return None, None
    value = round(future_ltp - spot, 2)
    return value, round(value / spot * 100.0, 3)


def _as_chain(underlying: str, expiry: date, oi: Optional[float], ltp: Optional[float]) -> OptionChain:
    """A future as a one-row chain for `compute_rollover_proxy`: total OI = the future's OI; the synthetic price at
    strike 0 (0 + call - put) = the future's LTP."""
    return OptionChain(underlying=underlying, expiry=expiry.isoformat(),
                       rows=[OptionChainRow(strike=0.0, call_oi=oi or 0.0, put_oi=0.0, call_ltp=ltp, put_ltp=0.0 if ltp is not None else None)])


def rollover_of(underlying: str, near: Dict[str, Any], nxt: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The rollover from the near future to the next (module docstring), or None when neither number can be had."""
    near_exp, next_exp = date.fromisoformat(near["expiry"]), date.fromisoformat(nxt["expiry"])
    proxy = compute_rollover_proxy(_as_chain(underlying, near_exp, near.get("oi"), near.get("ltp")),
                                   _as_chain(underlying, next_exp, nxt.get("oi"), nxt.get("ltp")), 0.0)
    if proxy is None:
        return None
    share = proxy.rollover_pct if near.get("oi") is not None and nxt.get("oi") is not None else None
    spread = proxy.cost_of_carry
    if share is None and spread is None:
        return None
    return {"near_expiry": near["expiry"], "next_expiry": nxt["expiry"], "near_label": near["label"], "next_label": nxt["label"],
            "next_share_pct": share, "spread": spread, "spread_pct": (round(spread / near["ltp"] * 100.0, 3) if spread is not None and near.get("ltp") else None),
            "near_oi": near.get("oi"), "next_oi": nxt.get("oi"), "caption": rollover_caption(share, spread)}


def rollover_caption(share: Optional[float], spread: Optional[float]) -> Optional[str]:
    """One plain line: what share of the near + next futures OI is in the next month, and where the next month trades
    against the near one. Only the numbers - no reading of what they mean for a trade."""
    parts: List[str] = []
    if share is not None:
        parts.append(f"{share:.1f}% of the near and next month futures OI is in the next month")
    if spread is not None:
        if abs(spread) < 0.005:
            parts.append("the next month trades level with the near month")
        else:
            parts.append(f"the next month trades {abs(spread):,.2f} {'above' if spread > 0 else 'below'} the near month")
    if not parts:
        return None
    text = "; ".join(parts)
    return text[0].upper() + text[1:] + "."


# ---------------------------------------------------------------------------------------------------- OI change

def oi_baseline_key(tenant_id: int, underlying: str, day: date) -> str:
    return f"fno3:fut_oi_first:{tenant_id}:{underlying}:{day.isoformat()}"


async def oi_change_state(tenant_id: int, underlying: str, ois: Dict[str, Optional[float]], now: datetime,
                          market_open: bool) -> Tuple[Dict[str, Any], Dict[str, Optional[float]]]:
    """(the state as JSON, the change per trading symbol). The first reading of the day is stored per organisation and
    underlying; a contract the first reading did not have gets its own first reading added."""
    key = oi_baseline_key(tenant_id, underlying, chain_view.today_ist(now))
    stored: Optional[Dict[str, Any]] = None
    text = await cache_get(key)
    if text:
        try:
            data = json.loads(text)
            stored = {"at": datetime.fromisoformat(data["at"]), "oi": {str(k): _finite(v) for k, v in dict(data["oi"]).items()}}
        except (ValueError, KeyError, TypeError):
            stored = None
    have = {s: v for s, v in ois.items() if v is not None}
    if stored is None:
        if not market_open or not have:
            return {"status": "none", "source": None, "since": None}, {}
        await cache_set(key, json.dumps({"at": now.isoformat(), "oi": have}), chain_view.BASELINE_TTL_SECONDS)
        return {"status": "building", "source": "first_reading", "since": now.isoformat()}, {}
    at, base = stored["at"], dict(stored["oi"])
    for s, v in have.items():
        base.setdefault(s, v)
    await cache_set(key, json.dumps({"at": at.isoformat(), "oi": base}), chain_view.BASELINE_TTL_SECONDS)
    state = {"status": "building" if now - at < timedelta(minutes=chain_view.BUILD_MINUTES) else "ready",
             "source": "first_reading", "since": at.isoformat()}
    if state["status"] != "ready":
        return state, {}
    changes = {s: (round(v - base[s], 2) if base.get(s) is not None else None) for s, v in have.items()}
    return state, changes


# ---------------------------------------------------------------------------------------------------- the view

def future_row(f: Future, quote: Optional[Quote], spot: Optional[float], today: date, error: Optional[str]) -> Dict[str, Any]:
    ltp = _price(quote.ltp) if quote is not None else None
    prev = _price(quote.prev_close) if quote is not None else None
    change, change_pct = change_of(ltp, prev)
    basis, basis_pct = basis_of(ltp, spot)
    oi = _finite(quote.oi) if quote is not None and quote.oi is not None and quote.oi >= 0 else None
    return {**f.as_dict(), "days_to_expiry": (f.expiry - today).days, "ltp": ltp, "prev_close": prev, "change": change,
            "change_pct": change_pct, "oi": oi, "oi_change": None, "volume": (_finite(quote.volume) or None) if quote is not None else None,
            "bid": _price(quote.bid) if quote is not None else None, "ask": _price(quote.ask) if quote is not None else None,
            "basis": basis, "basis_pct": basis_pct, "missing": ltp is None,
            "error": None if ltp is not None else (error or "No price from your broker for this contract just now.")}


def futures_view(underlying: str, futures: Sequence[Future], quotes: Dict[Pair, Quote], spot_pair: Pair, *, now: datetime,
                 errors: Optional[Dict[Pair, str]] = None) -> Dict[str, Any]:
    """The futures chain from the quotes read (keyed by (exchange, symbol)); OI change is added by the caller."""
    errors = errors or {}
    today = chain_view.today_ist(now)
    spot_quote = quotes.get(spot_pair)
    spot_ltp = _price(spot_quote.ltp) if spot_quote is not None else None
    spot_prev = _price(spot_quote.prev_close) if spot_quote is not None else None
    spot_change, spot_change_pct = change_of(spot_ltp, spot_prev)
    rows = [future_row(f, quotes.get((f.exchange, f.tradingsymbol)), spot_ltp, today, errors.get((f.exchange, f.tradingsymbol))) for f in futures]
    rollover = rollover_of(underlying, rows[0], rows[1]) if len(rows) >= 2 else None
    return {"underlying": underlying,
            "spot": {"symbol": spot_pair[1], "exchange": spot_pair[0], "ltp": spot_ltp, "change": spot_change, "change_pct": spot_change_pct},
            "rows": rows, "rollover": rollover, "fetched_at": now.isoformat()}
