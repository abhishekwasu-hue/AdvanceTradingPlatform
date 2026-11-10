import math
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

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
    # Kotak Neo: the mobile number registered with the account (with the country code), sent with the TOTP login.
    mobile_number: Optional[str] = None
    # Upstox only: the optional, data-only Analytics Token (one year, read-only, GET-only) and what ATP knows about it
    # (expiry, probe coverage) - both live inside the same encrypted payload; the token is never returned or logged.
    # `analytics_meta` is server-written only (app/brokers/upstox_analytics.py); a client-sent value is dropped.
    analytics_token: Optional[str] = Field(default=None, repr=False)
    analytics_meta: Optional[Dict[str, Any]] = None
    # UI2-5: the OAuth login in progress for this row (Zerodha, Fyers): who started it, the SHA-256 of the one-time state
    # nonce and when it lapses. Server-written only (app/brokers/token_lifecycle.py); a client-sent value is dropped.
    oauth_pending: Optional[Dict[str, Any]] = Field(default=None, repr=False)
    # BRK-1 B: today's automatic TOTP login for this row - the IST day, the attempts made, when the last one ran and, once
    # it gave up, the plain reason shown as "Not working". Server-written only (app/brokers/auto_login.py); a client-sent
    # value is dropped, and any save of new details clears it.
    auto_login: Optional[Dict[str, Any]] = Field(default=None, repr=False)


class BrokerProfile(BaseModel):
    broker: str
    user_id: str
    name: Optional[str] = None
    email: Optional[str] = None


def lot_or_none(value: Any) -> Optional[float]:
    """M68 PR-2 (TS1-088): a broker dump's lot size as a positive number, or None when it gives none (blank, 0, junk)."""
    try:
        lot = float(value) if value not in (None, "") else 0.0
    except (TypeError, ValueError):
        return None
    return lot if math.isfinite(lot) and lot > 0 else None


class Instrument(BaseModel):
    instrument_token: str
    exchange: str
    tradingsymbol: str
    name: Optional[str] = None
    segment: Optional[str] = None
    instrument_type: Optional[str] = None
    # M68 PR-2 (TS1-088): the broker's own lot size; None when its dump gives none - never assumed to be 1 (the instrument
    # master leaves a future / option without one out; a cash share's lot of one is a fact it fills in itself).
    lot_size: Optional[float] = None
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
    # UI2-4a2-pre: the previous session's close, where the broker's quote says it (for a watchlist's change); None = unknown.
    prev_close: Optional[float] = None
    # M94 PR-3: the day's price band (upper / lower circuit limit) the exchange set, as the broker's quote reports it
    # (Upstox and Kite `upper_circuit_limit` / `lower_circuit_limit`); None = the broker did not give it - never computed.
    upper_circuit_limit: float | None = None
    lower_circuit_limit: float | None = None


def circuit_limits(entry: Any) -> dict[str, float | None]:
    """M94 PR-3: the price band fields of one broker quote entry, for `Quote(**circuit_limits(entry))`. Upstox (v2 Full
    Market Quotes, `GET /market-quote/quotes`) and Kite Connect (`GET /quote`) both document `upper_circuit_limit` and
    `lower_circuit_limit`. A field that is absent, not a number, or not above zero (an index has no band) is None: a band
    is never invented."""
    out: dict[str, float | None] = {}
    for key in ("upper_circuit_limit", "lower_circuit_limit"):
        raw = entry.get(key) if isinstance(entry, dict) else None
        try:
            value = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            value = None
        out[key] = value if value is not None and math.isfinite(value) and value > 0 else None
    return out


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
    # Broker-supplied Greeks where the API sends them (Upstox option chain does); otherwise the
    # strike-selection pipeline computes delta from IV / price itself.
    call_delta: Optional[float] = None
    put_delta: Optional[float] = None
    # TM 9.4: the rest of the broker's Greeks where it sends them (Upstox does), kept by the chain history recorder.
    call_gamma: Optional[float] = None
    call_theta: Optional[float] = None
    call_vega: Optional[float] = None
    put_gamma: Optional[float] = None
    put_theta: Optional[float] = None
    put_vega: Optional[float] = None


class OptionChain(BaseModel):
    underlying: str
    expiry: str
    underlying_ltp: Optional[float] = None
    rows: List[OptionChainRow] = []


ORDER_INTENT_ENTRY = "ENTRY"


class BrokerOrderRequest(BaseModel):
    symbol: str
    exchange: str = "NSE"
    transaction_type: OrderSide
    quantity: float
    order_type: str = "MARKET"
    product: str = "MIS"
    price: Optional[float] = None
    trigger_price: Optional[float] = None
    validity: str = "DAY"
    tag: Optional[str] = None
    # Z10-c: what the platform built the order for - "ENTRY" for a new position (OrderRouter, the multi-leg executor);
    # None when not stated. Never sent to the broker; the Phase 0 PAPER-only lock refuses only an explicit ENTRY.
    intent: Optional[str] = None
    # TM-1: the platform's own reference for this one order (an idempotency key): the PAPER adapter returns the first
    # order for a repeated reference instead of placing twice. Never sent to a broker by the existing adapters.
    client_ref: Optional[str] = None
    # TM-3: the user's protection band (percent) for a MARKET order that the broker takes only as a marketable LIMIT
    # (BrokerCapabilities.market_orders_allowed False). None = the deployment's / platform's stated default. Never sent
    # to a broker.
    protection_pct: Optional[float] = None
    # An after-market order (Kotak Neo's `am` flag). Adapters that do not take one ignore it.
    after_market: bool = False


class GttRequest(BaseModel):
    """M75 W-10 (owner order M75 CW-6, DECISIONS 1 Nov M75Q-1 / TM-23): one broker GTT ("good till triggered") - the
    broker watches `symbol`'s own last price and sends `order` once it crosses `trigger_price` (a single trigger; the
    side of the cross is the trigger against `last_price`). `client_ref` is the platform's own key (the plan's order),
    never a broker field. Every value comes from the user's own plan; nothing is pre-chosen here."""
    symbol: str
    exchange: str
    trigger_price: float
    last_price: float
    order: BrokerOrderRequest
    client_ref: str


class GttStatus(BaseModel):
    """A broker GTT as the broker states it: ACTIVE (waiting), TRIGGERED (its order was sent: `order_id`), CANCELLED,
    REJECTED, EXPIRED. `message` is the broker's own words when it refused or ended it."""
    gtt_id: str
    status: str
    order_id: Optional[str] = None
    message: Optional[str] = None


class BasketMargin(BaseModel):
    """Part 7 / TM-10: the broker's own margin for a set of orders placed together. `required` is what the broker
    would block for the whole basket WITH the hedge benefit; `without_hedge` the sum of each order alone, where the
    broker says it (None otherwise)."""
    required: float
    without_hedge: Optional[float] = None


class BrokerOrderResponse(BaseModel):
    order_id: str
    status: str
    message: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None


class BrokerOrderStatus(BaseModel):
    order_id: str
    symbol: str
    transaction_type: OrderSide
    quantity: float
    filled_quantity: float = 0
    order_type: str
    status: str
    price: Optional[float] = None
    average_price: Optional[float] = None
    # When the broker took the order: an aware UTC datetime (adapters normalise it with `parse_broker_timestamp`, where a
    # naive broker time is IST); None when the broker does not say.
    placed_at: Optional[datetime] = None
    # Re-exit guard: the order's tag / remarks as the broker echoes it, and its product (platform name: MIS / NRML / CNC)
    # and exchange (platform venue: NSE / NFO / BSE / BFO / MCX) - None when the broker does not say. Used to tell an exit
    # this platform placed from a manual order on the same symbol.
    tag: Optional[str] = None
    product: Optional[str] = None
    exchange: Optional[str] = None


class BrokerTradeEntry(BaseModel):
    trade_id: str
    order_id: str
    symbol: str
    transaction_type: OrderSide
    quantity: float
    price: float
    timestamp: Optional[datetime] = None


class BrokerPosition(BaseModel):
    symbol: str
    exchange: str = "NSE"
    product: str = "MIS"
    quantity: float
    average_price: float
    ltp: float = 0.0
    pnl: float = 0.0


class BrokerHolding(BaseModel):
    symbol: str
    exchange: str = "NSE"
    quantity: float
    average_price: float
    ltp: float = 0.0
    pnl: float = 0.0


class MarginInfo(BaseModel):
    available_cash: float
    used_margin: float = 0.0
    available_margin: float = 0.0
    total_margin: Optional[float] = None
