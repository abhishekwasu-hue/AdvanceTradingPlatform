import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

from app.core.enums import OrderSide
from app.core.models import OHLCVBar
from app.brokers.models import (
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


def stop_order_params(capabilities: BrokerCapabilities, symbol: str, transaction_type: OrderSide, trigger_price: float, *,
                      is_option: Optional[bool] = None, limit_band_pct: float = 1.0) -> Tuple[str, Optional[float]]:
    """(order_type, limit price) for a protective stop. SL-M wherever the broker takes it; otherwise SL with the
    limit a band past the trigger (sell stops below, buy stops above) so a fast move still fills - a stop that
    never fills is the failure mode this guards against."""
    option = looks_like_option(symbol) if is_option is None else is_option
    if capabilities.stop_market and (capabilities.stop_market_on_options or not option):
        return "SL-M", None
    band = max(0.0, float(limit_band_pct)) / 100.0
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

    async def get_quote_for_symbol(self, symbol: str, exchange: str = "NSE") -> Optional[Quote]:
        """Full quote for one plain trading symbol *with the exchange's own timestamp* when the
        broker provides one (Phase G1 staleness gate: an exit decision is refused on a quote
        older than QUOTE_MAX_STALE_SECONDS). Default None: the broker's LTP endpoint carries no
        timestamp, the price is accepted as real-time and only its availability is checked."""
        return None

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
        self._access_token = None  # type: ignore[attr-defined]

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
        with the limit one band past the trigger instead of being refused at the gateway."""
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
