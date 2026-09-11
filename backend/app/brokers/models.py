from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict

from app.core.enums import OrderSide


class BrokerCredentials(BaseModel):
    """Broker-specific auth fields. Each adapter documents which of these it needs;
    unused fields are ignored. Never persisted in plaintext - encrypt at rest once
    the secrets-storage layer exists.
    """

    model_config = ConfigDict(extra="allow")

    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    access_token: Optional[str] = None
    request_token: Optional[str] = None
    client_id: Optional[str] = None
    pin: Optional[str] = None
    totp_secret: Optional[str] = None
    redirect_uri: Optional[str] = None


class BrokerProfile(BaseModel):
    broker: str
    user_id: str
    name: Optional[str] = None
    email: Optional[str] = None


class Instrument(BaseModel):
    instrument_token: str
    exchange: str
    tradingsymbol: str
    name: Optional[str] = None
    segment: Optional[str] = None
    instrument_type: Optional[str] = None
    lot_size: int = 1
    tick_size: float = 0.05
    expiry: Optional[str] = None
    strike: Optional[float] = None


class Quote(BaseModel):
    symbol: str
    ltp: float
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: float = 0.0
    oi: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    bid_qty: Optional[float] = None
    ask_qty: Optional[float] = None
    timestamp: Optional[datetime] = None


class OptionChainRow(BaseModel):
    strike: float
    call_oi: Optional[float] = None
    call_change_oi: Optional[float] = None
    call_volume: Optional[float] = None
    call_ltp: Optional[float] = None
    call_iv: Optional[float] = None
    call_bid: Optional[float] = None
    call_ask: Optional[float] = None
    put_oi: Optional[float] = None
    put_change_oi: Optional[float] = None
    put_volume: Optional[float] = None
    put_ltp: Optional[float] = None
    put_iv: Optional[float] = None
    put_bid: Optional[float] = None
    put_ask: Optional[float] = None


class OptionChain(BaseModel):
    underlying: str
    expiry: str
    underlying_ltp: Optional[float] = None
    rows: List[OptionChainRow] = []


class BrokerOrderRequest(BaseModel):
    symbol: str
    exchange: str = "NSE"
    transaction_type: OrderSide
    quantity: int
    order_type: str = "MARKET"
    product: str = "MIS"
    price: Optional[float] = None
    trigger_price: Optional[float] = None
    validity: str = "DAY"
    tag: Optional[str] = None


class BrokerOrderResponse(BaseModel):
    order_id: str
    status: str
    message: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None


class BrokerOrderStatus(BaseModel):
    order_id: str
    symbol: str
    transaction_type: OrderSide
    quantity: int
    filled_quantity: int = 0
    order_type: str
    status: str
    price: Optional[float] = None
    average_price: Optional[float] = None
    placed_at: Optional[datetime] = None


class BrokerTradeEntry(BaseModel):
    trade_id: str
    order_id: str
    symbol: str
    transaction_type: OrderSide
    quantity: int
    price: float
    timestamp: Optional[datetime] = None


class BrokerPosition(BaseModel):
    symbol: str
    exchange: str = "NSE"
    product: str = "MIS"
    quantity: int
    average_price: float
    ltp: float = 0.0
    pnl: float = 0.0


class BrokerHolding(BaseModel):
    symbol: str
    exchange: str = "NSE"
    quantity: int
    average_price: float
    ltp: float = 0.0
    pnl: float = 0.0


class MarginInfo(BaseModel):
    available_cash: float
    used_margin: float = 0.0
    available_margin: float = 0.0
    total_margin: Optional[float] = None
