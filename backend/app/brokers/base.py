import math
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from typing import Awaitable, Callable, Dict, FrozenSet, List, Optional, Sequence, Tuple

from app.core.enums import OrderSide
from app.core.models import OHLCVBar
from app.brokers.models import (
    BasketMargin,
    BrokerHolding,
    BrokerOrderRequest,
    BrokerOrderResponse,
    BrokerOrderStatus,
    BrokerPosition,
    BrokerProfile,
    BrokerTradeEntry,
    Instrument,
    MarginInfo,
    OptionChain,
    Quote,
)


@dataclass(frozen=True)
class BrokerCapabilities:
    """P0.5 / T2: what an adapter's broker actually accepts, so the platform never asks for an order type the
    exchange gateway refuses. Defaults describe the common Indian broker; adapters override what differs."""
    stop_market: bool = True                 # SL-M accepted at all
    stop_market_on_options: bool = True      # SL-M accepted on option contracts (Kite refuses it)
    stop_limit: bool = True                  # SL (stop-loss limit) accepted
    modify_orders: bool = True               # modify_order implemented (else cancel + re-place)
    cancel_orders: bool = True
    # TM-3: the broker takes a MARKET order from an API / algo client. False (Kotak Neo: "market orders are not allowed
    # for retail algo trading") = a MARKET order goes as a marketable LIMIT a protection band past the reference price
    # (app/execution/marketable_limit.py), the same transform in PAPER and LIVE; exits are never blocked (ADR-0004).
    market_orders_allowed: bool = True


# Platform tick for the limit leg of a stop-limit protective order (NSE equity / F&O tick).
_STOP_LIMIT_TICK = 0.05
# A strike (digits) right before CE/PE: `NIFTY26OCT26000CE`, `NIFTY-OCT2026-26000-CE`, canonical `NIFTY 26000 CE 30 OCT 26`.
# "RELIANCE" ends in CE and is not an option, hence the digit requirement.
_OPTION_RE = re.compile(r"\d[ -]?(CE|PE)(?: |$)")
# Shoonya spelling: `NIFTY30OCT26C26000` (expiry digits, C/P, strike).
_SHOONYA_OPTION_RE = re.compile(r"\d[CP]\d+(?:\.\d+)?$")


def looks_like_option(symbol: str) -> bool:
    """True for an option contract in any broker spelling the platform handles; False for equities,
    indices and futures."""
    text = (symbol or "").strip().upper()
    if not text:
        return False
    return bool(_OPTION_RE.search(text)) or bool(_SHOONYA_OPTION_RE.search(text))


_TICK_005_EXCHANGES = {"NSE", "BSE", "NFO", "BFO"}   # equity and equity F&O: 0.05


def round_stop_trigger(trigger_price: float, transaction_type: OrderSide, *, tick: Optional[float] = None,
                       symbol: Optional[str] = None, exchange: Optional[str] = None) -> float:
    """A stop trigger on the exchange's tick grid, moved to the tick farther from the market: down for a sell stop (it
    protects a long, the market is above), up for a buy stop. An off-tick trigger is refused by the exchange, and
    rounding toward the market could fire the stop early. The tick: `tick` if given, else the instrument's own tick
    from the registry (MCX / crypto), else 0.05 on NSE / BSE / NFO / BFO. With no known tick (another venue, a symbol
    the registry does not hold) the trigger is returned unchanged - guessing a tick could put a stop on the wrong
    side of a low-priced instrument."""
    if tick is None and symbol:
        from app.instruments.registry import get_contract_spec   # local: the registry is a leaf, keep base light
        spec = get_contract_spec(symbol)
        if spec is not None and spec.tick_size:
            tick = float(spec.tick_size)
    if tick is None and (exchange is None or exchange.upper() in _TICK_005_EXCHANGES):
        tick = _STOP_LIMIT_TICK
    if not tick:
        return trigger_price
    steps = trigger_price / tick
    steps = math.floor(round(steps, 6)) if transaction_type == OrderSide.SELL else math.ceil(round(steps, 6))
    decimals = max(2, len(f"{tick:.10f}".rstrip("0").split(".")[1]))
    return round(max(tick, steps * tick), decimals)


def stop_order_params(capabilities: BrokerCapabilities, symbol: str, transaction_type: OrderSide, trigger_price: float, *,
                      is_option: Optional[bool] = None, limit_band_pct: Optional[float] = None) -> Tuple[str, Optional[float]]:
    """(order_type, limit price) for a protective stop. SL-M wherever the broker takes it; otherwise SL with the
    limit a band past the trigger (sell stops below, buy stops above) so a fast move still fills - a stop that
    never fills is the failure mode this guards against.

    `limit_band_pct` None = the 1% band rounded to the nearest tick (unchanged since P0.5). A set band (G-LIVE,
    STOP_LIMIT_BAND_PCT) rounds outward - down for a sell, up for a buy - and keeps the limit at least one tick
    past the trigger, so rounding can never shrink the room the stop has to fill."""
    option = looks_like_option(symbol) if is_option is None else is_option
    if capabilities.stop_market and (capabilities.stop_market_on_options or not option):
        return "SL-M", None
    if limit_band_pct is not None:
        tick = _STOP_LIMIT_TICK
        trigger = round(round(trigger_price / tick) * tick, 2)
        band_set = max(0.0, float(limit_band_pct)) / 100.0
        if transaction_type == OrderSide.SELL:
            steps = math.floor(round(trigger * (1 - band_set) / tick, 6))
            return "SL", round(max(tick, min(steps * tick, trigger - tick)), 2)
        steps = math.ceil(round(trigger * (1 + band_set) / tick, 6))
        return "SL", round(max(steps * tick, trigger + tick), 2)
    band = 0.01
    raw = trigger_price * (1 - band) if transaction_type == OrderSide.SELL else trigger_price * (1 + band)
    limit = round(round(raw / _STOP_LIMIT_TICK) * _STOP_LIMIT_TICK, 2)
    if transaction_type == OrderSide.SELL:
        limit = max(_STOP_LIMIT_TICK, min(limit, round(trigger_price, 2)))
    else:
        limit = max(limit, round(trigger_price, 2))
    return "SL", limit


class BrokerInterface(ABC):
    """Every broker adapter (Zerodha, Upstox, Angel One, Fyers, Dhan, ...) implements this.

    The trading engine, risk engine and order router only ever talk to this interface -
    never to a broker-specific client directly - so a new broker can be added by writing
    one adapter class here without touching anything upstream. All I/O methods are async
    since every one of them is a real network call.
    """

    name: str
    # Longest order `tag` this broker accepts (Phase D1 algo tagging shortens to fit). Zerodha
    # documents 20 alphanumeric characters; adapters that allow more override this.
    max_tag_length: int = 20
    # P0.5 / T2: order types this broker accepts (see BrokerCapabilities).
    capabilities: BrokerCapabilities = BrokerCapabilities()
    # LD-10: True when get_historical_data reads only the IST dates of from/to (the same bars for any time of the same
    # day), so a shared candle copy can be keyed by the dates (app/market_data/shared_candles.window_anchor).
    history_by_date: bool = False
    # UI2-4a2: the longest range of daily bars one history request may ask for; longer ranges (weekly and monthly charts)
    # go out as whole-date chunks of at most this many days. Conservative where the broker publishes no limit.
    daily_days_per_request: int = 365
    # SCR-FIX A1: the ATP intervals this broker's history / intraday APIs accept as they are. Any other interval is fetched
    # at the largest native interval that divides it and resampled (app/market_data/intervals.py), never sent raw.
    native_intervals: FrozenSet[str] = frozenset({"1min", "3min", "5min", "10min", "15min", "30min", "60min", "day"})
    # The session token this adapter uses (read back by `access_token`); adapters set it in __init__ / authenticate().
    _access_token: Optional[str] = None

    @abstractmethod
    async def authenticate(self) -> BrokerProfile: ...

    @abstractmethod
    async def get_profile(self) -> BrokerProfile: ...

    @abstractmethod
    async def get_instruments(self, exchange: Optional[str] = None) -> List[Instrument]: ...

    @abstractmethod
    async def get_ltp(self, symbols: List[str]) -> Dict[str, float]: ...

    @abstractmethod
    async def get_quote(self, symbols: List[str]) -> Dict[str, Quote]: ...

    @abstractmethod
    async def get_historical_data(
        self, symbol: str, exchange: str, interval: str, from_date: datetime, to_date: datetime
    ) -> List[OHLCVBar]: ...

    @abstractmethod
    async def get_option_chain(self, underlying: str, expiry: Optional[date] = None) -> OptionChain: ...

    @abstractmethod
    async def place_order(self, order: BrokerOrderRequest) -> BrokerOrderResponse: ...

    @abstractmethod
    async def modify_order(
        self, order_id: str, quantity: Optional[float] = None, price: Optional[float] = None,
        trigger_price: Optional[float] = None, order_type: Optional[str] = None,
    ) -> BrokerOrderResponse: ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> BrokerOrderResponse: ...

    @abstractmethod
    async def get_order_book(self) -> List[BrokerOrderStatus]: ...

    async def find_order_by_client_id(self, client_id: str) -> Optional[BrokerOrderStatus]:
        """TM2-4: the order this platform placed with `client_id` as its tag, from the broker's own order book (the tag
        the broker echoes back: Zerodha / Upstox `tag`, Fyers `orderTag`, Angel `ordertag`, Dhan `correlationId`, Kotak
        `GuiOrdId`, Groww `order_reference_id`, Shoonya `remarks`, CoinDCX `client_order_id`), cut to this broker's tag
        length. None: no such order in the book. An unreadable book raises - "unknown" is never "not there"."""
        wanted = client_id[: self.max_tag_length] if self.max_tag_length else client_id
        matches = [o for o in await self.get_order_book() if o.tag and o.tag == wanted]
        return matches[-1] if matches else None

    @abstractmethod
    async def get_trade_book(self) -> List[BrokerTradeEntry]: ...

    @abstractmethod
    async def get_positions(self) -> List[BrokerPosition]: ...

    @abstractmethod
    async def get_holdings(self) -> List[BrokerHolding]: ...

    @abstractmethod
    async def get_margins(self) -> MarginInfo: ...

    @property
    def access_token(self) -> Optional[str]:
        """The session token this adapter is currently using, if any - read back after a
        successful `authenticate()` so the platform can persist a freshly-exchanged token
        (app/brokers/token_lifecycle.py). Adapters keep it in `_access_token` by convention."""
        return getattr(self, "_access_token", None)

    # --- Non-abstract conveniences the autonomous worker relies on --------------------------
    # Each has a sensible default in terms of the abstract methods above, so existing adapters
    # keep working unchanged; an adapter overrides one only where its API needs something
    # different (Upstox: instrument-key symbols and a separate intraday candle endpoint).

    async def get_order_margin(self, order: BrokerOrderRequest) -> Optional[float]:
        """Margin the broker would block for `order` (Phase F3: sizing written options). None
        when the broker does not expose a margin calculator - the caller then refuses to write
        rather than guess."""
        return None

    async def get_basket_margin(self, orders: List[BrokerOrderRequest]) -> Optional[BasketMargin]:
        """Part 7 / TM-10: the broker's margin for these orders placed together, with the hedge benefit. None when the
        broker has no basket margin call - the caller then shows ATP's labelled estimate (never a made-up number)."""
        return None

    async def get_quote_for_symbol(self, symbol: str, exchange: str = "NSE") -> Optional[Quote]:
        """Full quote for one plain trading symbol *with the exchange's own timestamp* when the
        broker provides one (Phase G1 staleness gate: an exit decision is refused on a quote
        older than QUOTE_MAX_STALE_SECONDS). Default None: the broker's LTP endpoint carries no
        timestamp, the price is accepted as real-time and only its availability is checked."""
        return None

    # UI2-4a2-pre: True when `get_quotes_batch` is the broker's multi-quote call (one call for a whole watchlist).
    multi_quote: bool = False
    # UI2-4a2-pre review: set by a caller that budgets broker calls - awaited before a lookup call the adapter makes on
    # its own (Shoonya's /SearchScrip); False = no token, the adapter raises BrokerBudgetUsed. None = not budgeted.
    budget_hook: Optional[Callable[[], Awaitable[bool]]] = None

    async def get_quotes_batch(self, items: Sequence[Tuple[str, str]]) -> Optional[Dict[Tuple[str, str], Quote]]:
        """UI2-4a2-pre: quotes for many (exchange, plain trading symbol) pairs in ONE broker call (the watchlist), keyed
        by the pair asked for. A pair the broker returns nothing for is left out. Default None: this adapter has no
        multi-quote call, so the caller uses the live feed's ticks and per-symbol quotes within its budget instead."""
        return None

    async def _quotes_batch_by_keys(self, items: Sequence[Tuple[str, str]]) -> Dict[Tuple[str, str], Quote]:
        """`get_quotes_batch` for an adapter whose `get_quote` takes "EXCHANGE:SYMBOL" keys and serves them in one call."""
        keys = {f"{exchange}:{symbol}": (exchange, symbol) for exchange, symbol in items}
        quotes = await self.get_quote(list(keys))
        return {keys[key]: quote for key, quote in quotes.items() if key in keys}

    async def get_balance(self) -> MarginInfo:
        """Free funds and margin (V3.14 rule 2: every adapter answers "how much can I trade").
        Alias of `get_margins` so callers have one obvious name; adapters override when their
        funds endpoint differs from their margin endpoint."""
        return await self.get_margins()

    async def exit_position(self, symbol: str, exchange: str, quantity: float, side: OrderSide, *, product: str = "MIS",
                            tag: Optional[str] = None) -> BrokerOrderResponse:
        """Phase M / master prompt section 8 and V3.14: square off `quantity` of `symbol` at market.
        `side` is the position's side (LONG -> BUY); the exit is the opposite. Adapters may
        override with a broker-native square-off call; the default is one market order."""
        exit_side = OrderSide.SELL if side == OrderSide.BUY else OrderSide.BUY
        return await self.place_order(BrokerOrderRequest(symbol=symbol, exchange=exchange, transaction_type=exit_side,
                                                         quantity=quantity, order_type="MARKET", product=product, tag=tag))

    async def subscribe_market_data(self, symbols: List[str]) -> None:
        """Streaming quotes (section 8). Phase S implements them outside the adapter, in
        `app.market_data.stream` (`stream_for(adapter)` returns the Upstox V3 or Kite ticker
        stream; the worker owns the connection and the tick cache feeds `get_ltp`). An adapter
        with no stream raises here and the platform polls REST with the staleness gate (G1)."""
        from app.market_data.stream import stream_for
        if stream_for(self) is None:
            raise NotImplementedError(f"{self.name} has no streaming market data; poll get_ltp/get_quote")
        raise NotImplementedError(
            f"{self.name} streams through app.market_data.stream.stream_for(adapter); the worker owns the subscription"
        )

    async def disconnect(self) -> None:
        """Invalidate this session token at the broker (Upstox `DELETE /logout`, Kite
        `DELETE /session/token`) and forget it locally. Default: forget only - the adapter has no
        server-side logout - so a caller can always call this and then drop the adapter."""
        self._access_token = None

    async def get_ltp_for_symbol(self, symbol: str, exchange: str = "NSE") -> float:
        """Last traded price for one plain trading symbol (RELIANCE, NIFTY 50, ...). `get_ltp`
        takes each broker's own quote-identifier format, which differs per broker; this is the
        broker-neutral form the position monitor uses. Default: the Kite-style "EXCHANGE:SYMBOL"
        key most Indian broker quote APIs accept."""
        key = f"{exchange}:{symbol}"
        prices = await self.get_ltp([key])
        if key in prices:
            return float(prices[key])
        if symbol in prices:
            return float(prices[symbol])
        if len(prices) == 1:
            return float(next(iter(prices.values())))
        raise KeyError(f"No LTP returned for {key}")

    def stop_order_params(self, symbol: str, transaction_type: OrderSide, trigger_price: float, *,
                          is_option: Optional[bool] = None) -> Tuple[str, Optional[float]]:
        """(order_type, limit price) this broker needs for a protective stop on `symbol` (P0.5 / T2)."""
        from app.core import config
        return stop_order_params(self.capabilities, symbol, transaction_type, trigger_price, is_option=is_option,
                                 limit_band_pct=config.STOP_LIMIT_BAND_PCT)

    async def place_stop_loss_order(
        self, symbol: str, exchange: str, transaction_type: OrderSide, quantity: float, trigger_price: float,
        product: str = "MIS", tag: Optional[str] = None, is_option: Optional[bool] = None,
    ) -> BrokerOrderResponse:
        """The protective stop placed right after a LIVE entry fills: a stop-loss *market* order
        ("SL-M") on the opposite side, triggered at the signal's stop-loss price - a market trigger,
        not a limit, because a stop that fails to fill in a fast move is worse than one that fills a
        tick worse. P0.5 / T2: where the broker refuses SL-M (Kite on options) the stop goes as SL
        with the limit one band past the trigger instead of being refused at the gateway. The trigger is put on
        the tick grid, away from the market (`round_stop_trigger`)."""
        trigger_price = round_stop_trigger(trigger_price, transaction_type, symbol=symbol, exchange=exchange)
        order_type, limit = self.stop_order_params(symbol, transaction_type, trigger_price, is_option=is_option)
        order = BrokerOrderRequest(
            symbol=symbol, exchange=exchange, transaction_type=transaction_type, quantity=quantity,
            order_type=order_type, product=product, trigger_price=trigger_price, price=limit, tag=tag,
        )
        return await self.place_order(order)

    async def get_intraday_candles(self, symbol: str, exchange: str, interval: str) -> List[OHLCVBar]:
        """Today's candles so far. Default: the historical endpoint with a from/to of today, which
        is how Kite-style APIs serve the current day. Brokers that split "today" out into a
        separate endpoint (Upstox) override this."""
        now = datetime.now()
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return await self.get_historical_data(symbol, exchange, interval, start_of_day, now)
