from abc import ABC, abstractmethod
from datetime import date, datetime
from typing import Dict, List, Optional

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
        self, order_id: str, quantity: Optional[int] = None, price: Optional[float] = None,
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
