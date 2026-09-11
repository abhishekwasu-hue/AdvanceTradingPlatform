from datetime import date, datetime
from typing import Dict, List, Optional

import httpx

from app.brokers.base import BrokerInterface
from app.brokers.models import (
    BrokerCredentials,
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
from app.core.models import OHLCVBar


class _StubBrokerAdapter(BrokerInterface):
    """Structural placeholder for a broker the architecture supports but that isn't wired to
    live endpoints yet (Zerodha and Upstox above are the fully implemented reference adapters).

    The class is instantiable and satisfies BrokerInterface today so it can be registered and
    swapped in later, but every I/O method raises NotImplementedError until someone fills in
    the real REST calls against that broker's current API docs. This keeps the adapter isolated
    and pluggable per the platform's broker-abstraction requirement without the risk of shipping
    unverified endpoint guesses as if they were tested.
    """

    docs_url: str = ""

    def __init__(self, credentials: BrokerCredentials, client: Optional[httpx.AsyncClient] = None) -> None:
        self.credentials = credentials
        self._client = client or httpx.AsyncClient(timeout=15.0)

    def _not_implemented(self, method: str) -> NotImplementedError:
        return NotImplementedError(
            f"{self.name}.{method}() is not wired to a live endpoint yet. "
            f"Implement it against {self.docs_url or 'the broker API docs'} before enabling this broker."
        )

    async def authenticate(self) -> BrokerProfile:
        raise self._not_implemented("authenticate")

    async def get_profile(self) -> BrokerProfile:
        raise self._not_implemented("get_profile")

    async def get_instruments(self, exchange: Optional[str] = None) -> List[Instrument]:
        raise self._not_implemented("get_instruments")

    async def get_ltp(self, symbols: List[str]) -> Dict[str, float]:
        raise self._not_implemented("get_ltp")

    async def get_quote(self, symbols: List[str]) -> Dict[str, Quote]:
        raise self._not_implemented("get_quote")

    async def get_historical_data(
        self, symbol: str, exchange: str, interval: str, from_date: datetime, to_date: datetime
    ) -> List[OHLCVBar]:
        raise self._not_implemented("get_historical_data")

    async def get_option_chain(self, underlying: str, expiry: Optional[date] = None) -> OptionChain:
        raise self._not_implemented("get_option_chain")

    async def place_order(self, order: BrokerOrderRequest) -> BrokerOrderResponse:
        raise self._not_implemented("place_order")

    async def modify_order(
        self, order_id: str, quantity: Optional[int] = None, price: Optional[float] = None,
        trigger_price: Optional[float] = None, order_type: Optional[str] = None,
    ) -> BrokerOrderResponse:
        raise self._not_implemented("modify_order")

    async def cancel_order(self, order_id: str) -> BrokerOrderResponse:
        raise self._not_implemented("cancel_order")

    async def get_order_book(self) -> List[BrokerOrderStatus]:
        raise self._not_implemented("get_order_book")

    async def get_trade_book(self) -> List[BrokerTradeEntry]:
        raise self._not_implemented("get_trade_book")

    async def get_positions(self) -> List[BrokerPosition]:
        raise self._not_implemented("get_positions")

    async def get_holdings(self) -> List[BrokerHolding]:
        raise self._not_implemented("get_holdings")

    async def get_margins(self) -> MarginInfo:
        raise self._not_implemented("get_margins")


class AngelOneBroker(_StubBrokerAdapter):
    name = "angel_one"
    docs_url = "https://smartapi.angelbroking.com/docs"


class FyersBroker(_StubBrokerAdapter):
    name = "fyers"
    docs_url = "https://myapi.fyers.in/docsv3"


class DhanBroker(_StubBrokerAdapter):
    name = "dhan"
    docs_url = "https://dhanhq.co/docs/v2/"
