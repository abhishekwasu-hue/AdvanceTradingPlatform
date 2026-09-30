"""Phase AI: one contract, six spellings.

Derived contracts are resolved from the Upstox instrument master (Phase F), so a resolved
contract's `tradingsymbol` is spelt Upstox's way: `NIFTY 26000 CE 30 OCT 26`, `NIFTY FUT 30 OCT 26`.
Every other broker names the same contract differently and only accepts its own spelling:

    Zerodha   NIFTY26OCT26000CE          Angel One  NIFTY30OCT2626000CE
    Fyers     NIFTY26OCT26000CE          Dhan       NIFTY-OCT2026-26000-CE
    Shoonya   NIFTY30OCT26C26000         (futures: NIFTY26OCTFUT / NIFTY30OCT26F / ...)

Until this phase the Upstox spelling was sent as-is to whichever broker the tenant trades
through, so an F&O order on any non-Upstox account failed at the adapter's symbol lookup.

`ContractSymbolBroker` wraps a non-Upstox adapter and translates at the boundary, in both
directions, without knowing any broker's format: an outbound symbol that parses as an Upstox
contract is matched to the broker's own instrument list by *attributes* (underlying, expiry,
strike, right) and the broker's `tradingsymbol` is sent; inbound positions, orders and trades
whose symbol is one of those instruments come back spelt the platform's way, so the position
monitor, reconciliation and the journal keep matching on one string. A contract the broker does
not list is refused with a clear error, never guessed. Symbols that are not contracts (RELIANCE,
NIFTY 50) pass through untouched, so the wrapper is safe on every code path (`build_adapter`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.brokers.base import BrokerInterface
from app.brokers.exceptions import BrokerAPIError
from app.brokers.models import BrokerOrderRequest, Instrument, Quote
from app.core.enums import OrderSide
from app.instruments.master import derivatives_exchange, normalise_expiry, underlying_of

UPSTOX = "upstox"
DERIVATIVE_EXCHANGES = ("NFO", "BFO", "MCX", "CDS")

_OPTION_RE = re.compile(r"^(?P<u>.+?) (?P<strike>\d+(?:\.\d+)?) (?P<right>CE|PE) (?P<d>\d{1,2}) (?P<m>[A-Z]{3}) (?P<y>\d{2})$")
_FUTURE_RE = re.compile(r"^(?P<u>.+?) FUT (?P<d>\d{1,2}) (?P<m>[A-Z]{3}) (?P<y>\d{2})$")
_SUFFIX_RIGHT_RE = re.compile(r"(CE|PE)$")
_SHOONYA_RIGHT_RE = re.compile(r"(?P<r>[CP])(?P<strike>\d+(?:\.\d+)?)$")
_PREFIX_RE = re.compile(r"^(?P<u>[A-Z&]+)")
_EXPIRY_FORMATS = ("%d-%b-%Y", "%d%b%Y", "%d-%m-%Y", "%d/%m/%Y", "%Y%m%d")


@dataclass(frozen=True)
class ContractKey:
    underlying: str          # master name: NIFTY, BANKNIFTY, RELIANCE
    expiry: date
    right: str               # CE, PE or FUT
    strike: Optional[float]  # None for futures

    @property
    def canonical(self) -> str:
        """The Upstox master spelling the platform uses everywhere."""
        when = self.expiry.strftime("%d %b %y").upper()
        if self.right == "FUT":
            return f"{self.underlying} FUT {when}"
        return f"{self.underlying} {_fmt_strike(self.strike or 0.0)} {self.right} {when}"


def _fmt_strike(strike: float) -> str:
    return f"{strike:g}" if abs(strike - round(strike)) > 1e-9 else str(int(round(strike)))


def _norm_strike(strike: Optional[float]) -> Optional[float]:
    return None if strike is None else round(float(strike), 2)


def parse_contract(symbol: str) -> Optional[ContractKey]:
    """Upstox master spelling -> ContractKey; None for anything that is not a contract."""
    text = (symbol or "").strip().upper()
    if not text:
        return None
    m = _OPTION_RE.match(text)
    if m:
        expiry = _expiry_from(m.group("d"), m.group("m"), m.group("y"))
        if expiry is None:
            return None
        return ContractKey(underlying_of(m.group("u")), expiry, m.group("right"), _norm_strike(float(m.group("strike"))))
    m = _FUTURE_RE.match(text)
    if m:
        expiry = _expiry_from(m.group("d"), m.group("m"), m.group("y"))
        if expiry is None:
            return None
        return ContractKey(underlying_of(m.group("u")), expiry, "FUT", None)
    return None


def _expiry_from(d: str, mon: str, yy: str) -> Optional[date]:
    try:
        return datetime.strptime(f"{int(d):02d} {mon} {yy}", "%d %b %y").date()
    except ValueError:
        return None


def _parse_expiry(value: Any) -> Optional[date]:
    parsed = normalise_expiry(value)
    if parsed is not None:
        return parsed
    text = str(value or "").strip()
    for fmt in _EXPIRY_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _right_of(instrument: Instrument) -> Optional[str]:
    itype = (instrument.instrument_type or "").upper()
    if itype in ("CE", "PE"):
        return itype
    if itype.startswith("FUT"):
        return "FUT"
    symbol = (instrument.tradingsymbol or "").upper()
    m = _SUFFIX_RIGHT_RE.search(symbol)
    if m:
        return m.group(1)
    if symbol.endswith("FUT"):
        return "FUT"
    if itype.startswith("OPT"):
        m = _SHOONYA_RIGHT_RE.search(symbol)
        if m:
            return "CE" if m.group("r") == "C" else "PE"
    if re.search(r"\d{2}[A-Z]{3}\d{2}F$", symbol):
        return "FUT"
    return None


def _underlyings_of(instrument: Instrument) -> List[str]:
    """Candidate master names for this instrument: the adapter's `name` and the symbol's alpha prefix."""
    out: List[str] = []
    name = (instrument.name or "").strip().upper()
    if name:
        out.append(underlying_of(name))
    m = _PREFIX_RE.match((instrument.tradingsymbol or "").upper())
    if m and m.group("u") not in out:
        out.append(m.group("u"))
    return out


def contract_keys(instrument: Instrument) -> List[ContractKey]:
    """Every ContractKey this instrument may answer to (usually one); [] for equities/indices."""
    expiry = _parse_expiry(instrument.expiry)
    if expiry is None:
        return []
    right = _right_of(instrument)
    if right is None:
        return []
    strike = _norm_strike(instrument.strike) if right != "FUT" else None
    if right != "FUT" and not strike:
        m = _SHOONYA_RIGHT_RE.search((instrument.tradingsymbol or "").upper())
        if m:
            strike = _norm_strike(float(m.group("strike")))
        else:
            return []
    return [ContractKey(u, expiry, right, strike) for u in _underlyings_of(instrument)]


class _Index:
    def __init__(self, instruments: List[Instrument]) -> None:
        self.source = instruments
        self.forward: Dict[ContractKey, Instrument] = {}
        self.reverse: Dict[str, ContractKey] = {}
        for instrument in instruments:
            keys = contract_keys(instrument)
            for key in keys:
                self.forward.setdefault(key, instrument)
            if keys:
                self.reverse.setdefault((instrument.tradingsymbol or "").upper(), keys[0])


class ContractSymbolBroker(BrokerInterface):
    """Transparent wrapper (same interface): contract symbols in the platform's spelling go out in
    the broker's, and come back in the platform's. Everything else is delegated untouched."""

    def __init__(self, inner: BrokerInterface) -> None:
        self.inner = inner
        self.name = inner.name
        self.max_tag_length = getattr(inner, "max_tag_length", 20)
        self._indexes: Dict[str, _Index] = {}
        self.translations = 0

    def __getattr__(self, item: str) -> Any:          # adapter-specific attributes (streams read them)
        return getattr(self.inner, item)

    @property
    def access_token(self) -> Optional[str]:
        return self.inner.access_token

    # --- index -----------------------------------------------------------------------------
    async def _index(self, exchange: str, *, refresh: bool = False) -> _Index:
        exchange = (exchange or "NFO").upper()
        instruments = await self.inner.get_instruments(exchange)
        cached = self._indexes.get(exchange)
        if cached is not None and cached.source is instruments and not refresh:
            return cached
        index = self._indexes[exchange] = _Index(instruments)
        return index

    async def _out(self, symbol: str, exchange: Optional[str]) -> str:
        key = parse_contract(symbol)
        if key is None:
            return symbol
        exch = (exchange or derivatives_exchange(key.underlying)).upper()
        index = await self._index(exch)
        hit = index.forward.get(key)
        if hit is None:
            hit = (await self._index(exch, refresh=True)).forward.get(key)
        if hit is None:
            raise BrokerAPIError(f"{self.name} lists no instrument for {key.canonical} on {exch}")
        self.translations += 1
        return hit.tradingsymbol

    async def _back(self, symbol: str, exchange: Optional[str]) -> str:
        """Broker spelling -> platform spelling. With an exchange, that exchange's index (built on demand);
        without one (order and trade books carry none), only the indexes already built for this session,
        so a book read never triggers a master download."""
        text = (symbol or "").upper()
        exch = (exchange or "").upper()
        if exch:
            if exch not in DERIVATIVE_EXCHANGES:
                return symbol
            try:
                indexes = [await self._index(exch)]
            except Exception:  # noqa: BLE001 - a failed master download must not hide the position itself
                return symbol
        else:
            indexes = list(self._indexes.values())
        for index in indexes:
            key = index.reverse.get(text)
            if key is not None:
                return key.canonical
        return symbol

    async def _out_keys(self, symbols: List[str]) -> Tuple[List[str], Dict[str, str]]:
        """`EXCH:SYMBOL` or plain keys -> translated keys, plus the map back to the caller's keys."""
        translated: List[str] = []
        back: Dict[str, str] = {}
        for original in symbols:
            exchange, plain = (original.split(":", 1) if ":" in original else (None, original))
            out = await self._out(plain, exchange)
            key = f"{exchange}:{out}" if exchange else out
            translated.append(key)
            back[key] = original
        return translated, back

    # --- outbound --------------------------------------------------------------------------
    async def place_order(self, order: BrokerOrderRequest):
        symbol = await self._out(order.symbol, order.exchange)
        return await self.inner.place_order(order if symbol == order.symbol else order.model_copy(update={"symbol": symbol}))

    async def get_order_margin(self, order: BrokerOrderRequest):
        symbol = await self._out(order.symbol, order.exchange)
        return await self.inner.get_order_margin(order if symbol == order.symbol else order.model_copy(update={"symbol": symbol}))

    async def place_stop_loss_order(self, symbol, exchange, transaction_type, quantity, trigger_price, product="MIS", tag=None):
        return await self.inner.place_stop_loss_order(await self._out(symbol, exchange), exchange, transaction_type, quantity, trigger_price, product, tag)

    async def exit_position(self, symbol, exchange, quantity, side: OrderSide, *, product="MIS", tag=None):
        return await self.inner.exit_position(await self._out(symbol, exchange), exchange, quantity, side, product=product, tag=tag)

    async def get_ltp_for_symbol(self, symbol, exchange="NSE"):
        return await self.inner.get_ltp_for_symbol(await self._out(symbol, exchange), exchange)

    async def get_quote_for_symbol(self, symbol, exchange="NSE"):
        quote = await self.inner.get_quote_for_symbol(await self._out(symbol, exchange), exchange)
        return quote.model_copy(update={"symbol": symbol}) if quote is not None and quote.symbol != symbol else quote

    async def get_ltp(self, symbols):
        translated, back = await self._out_keys(list(symbols))
        prices = await self.inner.get_ltp(translated)
        return {back.get(k, k): v for k, v in prices.items()}

    async def get_quote(self, symbols):
        translated, back = await self._out_keys(list(symbols))
        quotes = await self.inner.get_quote(translated)
        out: Dict[str, Quote] = {}
        for k, quote in quotes.items():
            original = back.get(k, k)
            out[original] = quote.model_copy(update={"symbol": original}) if quote.symbol != original else quote
        return out

    async def get_historical_data(self, symbol, exchange, interval, from_date, to_date):
        return await self.inner.get_historical_data(await self._out(symbol, exchange), exchange, interval, from_date, to_date)

    async def get_intraday_candles(self, symbol, exchange, interval):
        return await self.inner.get_intraday_candles(await self._out(symbol, exchange), exchange, interval)

    async def subscribe_market_data(self, symbols):
        translated, _ = await self._out_keys(list(symbols))
        return await self.inner.subscribe_market_data(translated)

    # --- inbound ---------------------------------------------------------------------------
    async def _restore(self, rows: Iterable[Any]) -> List[Any]:
        out = []
        for row in rows:
            exchange = getattr(row, "exchange", None)
            symbol = getattr(row, "symbol", None)
            if symbol and (not exchange or exchange.upper() in DERIVATIVE_EXCHANGES):
                restored = await self._back(symbol, exchange)
                if restored != symbol:
                    row = row.model_copy(update={"symbol": restored})
            out.append(row)
        return out

    async def get_positions(self):
        return await self._restore(await self.inner.get_positions())

    async def get_order_book(self):
        return await self._restore(await self.inner.get_order_book())

    async def get_trade_book(self):
        return await self._restore(await self.inner.get_trade_book())

    # --- plain delegation ------------------------------------------------------------------
    async def authenticate(self): return await self.inner.authenticate()
    async def get_profile(self): return await self.inner.get_profile()
    async def get_instruments(self, exchange=None): return await self.inner.get_instruments(exchange)
    async def get_option_chain(self, underlying, expiry=None): return await self.inner.get_option_chain(underlying, expiry)
    async def modify_order(self, order_id, quantity=None, price=None, trigger_price=None, order_type=None):
        return await self.inner.modify_order(order_id, quantity, price, trigger_price, order_type)
    async def cancel_order(self, order_id): return await self.inner.cancel_order(order_id)
    async def get_holdings(self): return await self.inner.get_holdings()
    async def get_margins(self): return await self.inner.get_margins()
    async def get_balance(self): return await self.inner.get_balance()
    async def disconnect(self): return await self.inner.disconnect()


def wrap_contract_symbols(adapter: BrokerInterface) -> BrokerInterface:
    """Upstox speaks the master's language already; everyone else gets the translator."""
    if getattr(adapter, "name", "") == UPSTOX or isinstance(adapter, ContractSymbolBroker):
        return adapter
    return ContractSymbolBroker(adapter)
