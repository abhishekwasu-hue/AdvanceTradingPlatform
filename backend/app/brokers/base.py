from abc import ABC, abstractmethod
from datetime import date, datetime
from typing import Dict, List, Optional

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

    async def place_stop_loss_order(
        self, symbol: str, exchange: str, transaction_type: OrderSide, quantity: float, trigger_price: float,
        product: str = "MIS", tag: Optional[str] = None,
    ) -> BrokerOrderResponse:
        """The protective stop placed right after a LIVE entry fills: a stop-loss *market* order
        ("SL-M" - the order type both Kite and Upstox use for it) on the opposite side, triggered
        at the signal's stop-loss price. A market trigger, not a limit, because a stop that fails
        to fill in a fast move is worse than a stop that fills a tick worse."""
        order = BrokerOrderRequest(
            symbol=symbol, exchange=exchange, transaction_type=transaction_type, quantity=quantity,
            order_type="SL-M", product=product, trigger_price=trigger_price, tag=tag,
        )
        return await self.place_order(order)

    async def get_intraday_candles(self, symbol: str, exchange: str, interval: str) -> List[OHLCVBar]:
        """Today's candles so far. Default: the historical endpoint with a from/to of today, which
        is how Kite-style APIs serve the current day. Brokers that split "today" out into a
        separate endpoint (Upstox) override this."""
        now = datetime.now()
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return await self.get_historical_data(symbol, exchange, interval, start_of_day, now)
