"""TM-1 (owner order ATP_SESSION_TERMINAL_MASTER.md, Part 8; approved 23 Oct): PAPER as a broker adapter.

`PaperExecutionAdapter` implements `BrokerInterface` (app/brokers/base.py) over the existing PAPER fill and cost model:
the fill price is `PaperBroker._slip` of the price the paper exchange holds for the symbol (the same slippage model every
PAPER fill already uses), the charges are `app.execution.india_costs` of the fill's own date. So a caller that holds "a
broker" - the order router, the multi-leg executor, the new order types - talks to ONE interface in PAPER and in LIVE;
only the adapter behind it differs. The `phase0_paper_only` lock and the `live_trading` flag are not read or changed here:
a PAPER adapter never reaches a broker's order API.

What it simulates, and nothing more:

* **prices** - the paper exchange's last price per symbol: `mark(symbol, price)` (the caller's own live read, e.g. the
  price the router already checked), else the optional `price_lookup` (the user's own session). No price, no fill;
* **fills** - only what the price reaches (`app.paper.fno_ticket.paper_fill`): a market order at the price, a
  marketable limit, a triggered SL / SL-M. TM-2: with `keep_resting=True` an order that must wait for its price (a
  limit not reached yet, a stop not triggered yet) RESTS as OPEN and fills on the first price that reaches it
  (`on_price`, the same fill and cost model as an immediate fill); the trigger engine keeps the durable book
  (app/execution/resting_book.py). Without it (every caller before TM-2) such an order is REJECTED with plain words,
  exactly as before;
* **partial fills** - an optional `liquidity(order) -> fillable quantity` hook (default: the whole quantity). The rest of
  a partly filled order stays OPEN until the caller cancels it, as at a broker;
* **idempotency** - a request with the same `client_ref` returns the first order (a second tap never places twice);
* **the books** - order book, trade book and net positions in memory for this adapter's life (the database order trail
  stays the pipeline's own: `app/execution/order_persistence.py`).

Exits are never blocked here (ADR-0004): an exit is an order like any other and fills at the price. The AI never calls
this (ADR-0006).
"""
from __future__ import annotations

import itertools
from datetime import date, datetime, timezone
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

from app.brokers.base import BrokerCapabilities, BrokerInterface, looks_like_option
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
from app.core.enums import OrderSide, SignalDirection
from app.core.models import OHLCVBar
from app.execution import india_costs
from app.execution.paper_broker import IST, PaperBroker

NAME = "paper"
PriceLookup = Callable[[str, str], Awaitable[float]]
Liquidity = Callable[[BrokerOrderRequest], float]
# TM2-4: the paper exchange's durable order book by client id (what a stopped worker already placed), e.g. the trigger
# engine's reading of the order / trade rows. None: this adapter's own in-memory book only.
OrderBookLookup = Callable[[str], Awaitable[Optional[BrokerOrderStatus]]]
NO_PRICE = "No live price for {symbol}: practice mode fills only at a price it can read - nothing was placed"
TERMINAL = ("COMPLETE", "REJECTED", "CANCELLED")
RESTING = "Waiting for your price: it fills when the price reaches it"


def instrument_kind(symbol: str) -> str:
    """The cost segment a symbol trades in: an option, a future, else the cash underlying."""
    text = (symbol or "").upper()
    if looks_like_option(text):
        return "OPTION"
    if text.endswith("FUT"):
        return "FUTURE"
    return "UNDERLYING"


def _capabilities_of(venue: Optional[str]) -> Optional[BrokerCapabilities]:
    """The broker's own capabilities when practising for it; None for no venue or an unknown one."""
    if not venue:
        return None
    from app.brokers.registry import BROKER_ADAPTERS     # local: the registry imports this module
    adapter_cls = BROKER_ADAPTERS.get(venue)
    return getattr(adapter_cls, "capabilities", None) if adapter_cls is not None else None


class PaperExecutionAdapter(BrokerInterface):
    """The PAPER execution venue behind `BrokerInterface` (TM-1). One instance per order flow (a ticket, a basket, a
    router call); it never holds another user's orders."""

    name = NAME
    max_tag_length = 64
    capabilities = BrokerCapabilities()
    # A practice venue has no session to renew and no broker-side margin calculator.
    paper = True

    def __init__(self, paper_broker: Optional[PaperBroker] = None, *, price_lookup: Optional[PriceLookup] = None,
                 liquidity: Optional[Liquidity] = None, funds: Optional[float] = None,
                 clock: Optional[Callable[[], datetime]] = None, venue: Optional[str] = None,
                 venue_plan: Optional[str] = None, keep_resting: bool = False, book: Optional[OrderBookLookup] = None) -> None:
        self.paper_broker = paper_broker or PaperBroker()
        self.keep_resting = keep_resting
        self.book = book
        self._resting: List[str] = []
        # Part 1B (owner principle: the same code path for PAPER and LIVE): practising for a broker account (`venue`, a
        # registry name such as "kotak_neo") applies that broker's own order transform - a MARKET order where the broker
        # takes none goes as the same marketable LIMIT (app/execution/marketable_limit.py) - and the brokerage that
        # broker states for `venue_plan` (app/brokers/broker_charges.py) when it states one. None: as before.
        self.venue = venue
        self.venue_plan = venue_plan
        self.venue_capabilities = _capabilities_of(venue)
        if self.venue_capabilities is not None:
            self.capabilities = self.venue_capabilities      # stops too: SL with a band where the venue takes no SL-M
        self.price_lookup = price_lookup
        self.liquidity = liquidity
        self.funds = funds
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._marks: Dict[Tuple[str, str], float] = {}
        self._orders: Dict[str, BrokerOrderStatus] = {}
        self._requests: Dict[str, BrokerOrderRequest] = {}
        self._charges: Dict[str, float] = {}
        self._by_ref: Dict[str, str] = {}
        self._trades: List[BrokerTradeEntry] = []
        self._order_ids = itertools.count(1)
        self._trade_ids = itertools.count(1)
        self._access_token = None

    # --- the paper exchange's prices ---------------------------------------------------------------------------------

    def mark(self, symbol: str, price: float, exchange: str = "NSE") -> None:
        """The price the paper exchange holds for `symbol` now (the caller's own live read)."""
        self._marks[(exchange.upper(), symbol.upper())] = float(price)
        self._marks[("", symbol.upper())] = float(price)

    async def _price(self, symbol: str, exchange: str) -> Optional[float]:
        key = (exchange.upper(), symbol.upper())
        if key in self._marks:
            return self._marks[key]
        if ("", symbol.upper()) in self._marks:
            return self._marks[("", symbol.upper())]
        if self.price_lookup is None:
            return None
        try:
            value = float(await self.price_lookup(symbol, exchange))
        except Exception:  # noqa: BLE001 - no quote, no fill (never a made-up price)
            return None
        return value if value > 0 else None

    # --- orders -----------------------------------------------------------------------------------------------------

    def _fill_price(self, order: BrokerOrderRequest, ltp: float) -> Tuple[Optional[float], str]:
        """Where the order fills now (PaperBroker's slippage on the price), or (None, why it would rest)."""
        from app.paper.fno_ticket import paper_fill        # local: the ticket module imports the database layer
        side = "BUY" if order.transaction_type == OrderSide.BUY else "SELL"
        order_type = (order.order_type or "MARKET").upper()
        if order_type in ("LIMIT", "SL") and order.price is None:
            return None, "A limit order needs a limit price"
        if order_type in ("SL", "SL-M") and order.trigger_price is None:
            return None, "A stop order needs a trigger price"
        at, note = paper_fill(order_type if order_type in ("MARKET", "LIMIT", "SL", "SL-M") else "MARKET", side, ltp,
                              order.price, order.trigger_price)
        if at is None:
            return None, note
        direction = SignalDirection.LONG if side == "BUY" else SignalDirection.SHORT
        fill = self.paper_broker._slip(at, direction)
        if order_type in ("LIMIT", "SL") and order.price is not None:
            # A limit is a limit: the slippage never fills past it.
            fill = min(fill, float(order.price)) if side == "BUY" else max(fill, float(order.price))
        return fill, note

    def _status(self, order_id: str, request: BrokerOrderRequest, *, status: str, filled: float = 0.0,
                average: Optional[float] = None) -> BrokerOrderStatus:
        return BrokerOrderStatus(order_id=order_id, symbol=request.symbol, transaction_type=request.transaction_type,
                                 quantity=float(request.quantity), filled_quantity=float(filled), order_type=request.order_type,
                                 status=status, price=request.price, average_price=average, placed_at=self.clock(),
                                 tag=request.tag, product=request.product, exchange=request.exchange)

    async def find_order_by_client_id(self, client_id: str) -> Optional[BrokerOrderStatus]:
        """TM2-4: the order placed with this client id (`client_ref`) - this adapter's own book, then the durable `book`
        - or None when the paper exchange never took one. The same lookup the live adapters answer from their order
        book by tag (BrokerInterface.find_order_by_client_id)."""
        if client_id in self._by_ref:
            return self._orders.get(self._by_ref[client_id])
        if self.book is None:
            return None
        found = await self.book(client_id)
        if found is not None:
            self._orders[found.order_id] = found
            self._by_ref[client_id] = found.order_id
        return found

    async def place_order(self, order: BrokerOrderRequest) -> BrokerOrderResponse:
        if order.client_ref and order.client_ref not in self._by_ref and self.book is not None:
            await self.find_order_by_client_id(order.client_ref)     # TM2-4: already placed before a restart -> that one
        if order.client_ref and order.client_ref in self._by_ref:
            first = self._orders[self._by_ref[order.client_ref]]
            return BrokerOrderResponse(order_id=first.order_id, status=first.status, message="Already placed (same reference)",
                                       raw={"duplicate": True})
        order_id = f"PAPER-{next(self._order_ids)}"
        if order.client_ref:
            self._by_ref[order.client_ref] = order_id
        self._requests[order_id] = order
        if order.quantity <= 0:
            self._orders[order_id] = self._status(order_id, order, status="REJECTED")
            return BrokerOrderResponse(order_id=order_id, status="REJECTED", message="The quantity must be above zero")
        ltp = await self._price(order.symbol, order.exchange)
        if ltp is None:
            self._orders[order_id] = self._status(order_id, order, status="REJECTED")
            return BrokerOrderResponse(order_id=order_id, status="REJECTED", message=NO_PRICE.format(symbol=order.symbol))
        if self.venue_capabilities is not None:
            from app.execution.marketable_limit import to_venue_order
            order, _venue_note = to_venue_order(order, self.venue_capabilities, ltp)
            self._requests[order_id] = order
        fill, note = self._fill_price(order, ltp)
        if fill is None:
            if self.keep_resting and self._can_rest(order):
                # TM-2: it waits for its price (OPEN, nothing filled) instead of being refused.
                self._orders[order_id] = self._status(order_id, order, status="OPEN")
                self._resting.append(order_id)
                return BrokerOrderResponse(order_id=order_id, status="OPEN", message=RESTING, raw={"resting": True})
            self._orders[order_id] = self._status(order_id, order, status="REJECTED")
            return BrokerOrderResponse(order_id=order_id, status="REJECTED", message=note)
        return self._fill_now(order_id, order, fill, note)

    def _fill_now(self, order_id: str, order: BrokerOrderRequest, fill: float, note: str) -> BrokerOrderResponse:
        fillable = float(order.quantity) if self.liquidity is None else max(0.0, min(float(order.quantity), float(self.liquidity(order))))
        if fillable <= 0:
            self._orders[order_id] = self._status(order_id, order, status="OPEN")
            return BrokerOrderResponse(order_id=order_id, status="OPEN", message="Waiting for a fill")
        self._record_fill(order_id, order, fill, fillable)
        status = "COMPLETE" if fillable >= float(order.quantity) else "OPEN"
        self._orders[order_id] = self._status(order_id, order, status=status, filled=fillable, average=fill)
        return BrokerOrderResponse(order_id=order_id, status=status, message=note,
                                   raw={"average_price": fill, "filled_quantity": fillable, "charges": self._charges[order_id]})

    @staticmethod
    def _can_rest(order: BrokerOrderRequest) -> bool:
        kind = (order.order_type or "MARKET").upper()
        if kind == "LIMIT":
            return order.price is not None
        if kind == "SL":
            return order.price is not None and order.trigger_price is not None
        return kind == "SL-M" and order.trigger_price is not None

    def resting(self) -> List[str]:
        """The ids of the orders waiting for their price (TM-2)."""
        return list(self._resting)

    async def on_price(self, symbol: str, price: float, exchange: str = "NSE") -> List[BrokerOrderResponse]:
        """TM-2: a new price for `symbol` on the paper exchange - every resting order it reaches fills now, through the
        same fill and cost model as an immediate order. A triggered SL whose limit the price is already past keeps
        waiting at its limit, as at an exchange."""
        self.mark(symbol, price, exchange)
        filled: List[BrokerOrderResponse] = []
        for order_id in list(self._resting):
            request = self._requests[order_id]
            if request.symbol.upper() != symbol.upper():
                continue
            fill, note = self._fill_price(request, float(price))
            if fill is None:
                continue
            self._resting.remove(order_id)
            filled.append(self._fill_now(order_id, request, fill, note))
        return filled

    def _record_fill(self, order_id: str, order: BrokerOrderRequest, price: float, quantity: float) -> None:
        when = self.clock()
        self._trades.append(BrokerTradeEntry(trade_id=f"PAPER-T{next(self._trade_ids)}", order_id=order_id, symbol=order.symbol,
                                             transaction_type=order.transaction_type, quantity=quantity, price=price, timestamp=when))
        side = "buy" if order.transaction_type == OrderSide.BUY else "sell"
        day: date = (when.astimezone(IST) if when.tzinfo else when).date()
        brokerage = self.paper_broker.brokerage_per_order
        segment = india_costs.segment_for(instrument_kind(order.symbol))
        if self.venue:
            # Part 1B: the broker's stated brokerage for this order (flat, or Groww's percent with a cap and a minimum).
            from app.brokers.broker_charges import brokerage_for_order
            stated = brokerage_for_order(self.venue, self.venue_plan, day, segment, float(price) * float(quantity))
            if stated is not None:
                brokerage = stated
        cost = india_costs.leg_cost(day, side, price, quantity, segment, brokerage_per_order=brokerage)
        self._charges[order_id] = round(self._charges.get(order_id, 0.0) + float(cost["total"]), 2)

    async def modify_order(self, order_id: str, quantity: Optional[float] = None, price: Optional[float] = None,
                           trigger_price: Optional[float] = None, order_type: Optional[str] = None) -> BrokerOrderResponse:
        current = self._orders.get(order_id)
        if current is None:
            return BrokerOrderResponse(order_id=order_id, status="REJECTED", message="No such practice order")
        if current.status in TERMINAL:
            return BrokerOrderResponse(order_id=order_id, status=current.status, message=f"The order is already {current.status.lower()}")
        request = self._requests[order_id].model_copy(update={k: v for k, v in (
            ("quantity", quantity), ("price", price), ("trigger_price", trigger_price), ("order_type", order_type)) if v is not None})
        self._requests[order_id] = request
        self._orders[order_id] = current.model_copy(update={"quantity": float(request.quantity), "price": request.price,
                                                             "order_type": request.order_type})
        return BrokerOrderResponse(order_id=order_id, status=current.status, message="Modified")

    async def cancel_order(self, order_id: str) -> BrokerOrderResponse:
        current = self._orders.get(order_id)
        if current is None:
            return BrokerOrderResponse(order_id=order_id, status="REJECTED", message="No such practice order")
        if current.status in TERMINAL:
            return BrokerOrderResponse(order_id=order_id, status=current.status, message=f"The order is already {current.status.lower()}")
        self._orders[order_id] = current.model_copy(update={"status": "CANCELLED"})
        if order_id in self._resting:
            self._resting.remove(order_id)
        return BrokerOrderResponse(order_id=order_id, status="CANCELLED", message="Cancelled")

    def order(self, order_id: str) -> Optional[BrokerOrderStatus]:
        return self._orders.get(order_id)

    def charges(self, order_id: str) -> float:
        """The estimated charges of this order's fills (india_costs of the fill's date)."""
        return self._charges.get(order_id, 0.0)

    async def get_order_book(self) -> List[BrokerOrderStatus]:
        return list(self._orders.values())

    async def get_trade_book(self) -> List[BrokerTradeEntry]:
        return list(self._trades)

    async def get_positions(self) -> List[BrokerPosition]:
        net: Dict[Tuple[str, str, str], List[float]] = {}
        for trade in self._trades:
            request = self._requests[trade.order_id]
            key = (trade.symbol, request.exchange, request.product)
            qty, cost = net.setdefault(key, [0.0, 0.0])
            signed = trade.quantity if trade.transaction_type == OrderSide.BUY else -trade.quantity
            net[key] = [qty + signed, cost + signed * trade.price]
        out: List[BrokerPosition] = []
        for (symbol, exchange, product), (qty, cost) in net.items():
            if abs(qty) < 1e-9:
                continue
            average = cost / qty
            ltp = self._marks.get((exchange.upper(), symbol.upper()), average)
            out.append(BrokerPosition(symbol=symbol, exchange=exchange, product=product, quantity=qty, average_price=round(average, 2),
                                      ltp=ltp, pnl=round((ltp - average) * qty, 2)))
        return out

    async def get_holdings(self) -> List[BrokerHolding]:
        return []

    async def get_margins(self) -> MarginInfo:
        cash = float(self.funds or 0.0)
        return MarginInfo(available_cash=cash, available_margin=cash, used_margin=0.0, total_margin=cash)

    # --- reads a practice venue does not serve (prices come from the user's own session) ----------------------------

    async def authenticate(self) -> BrokerProfile:
        return BrokerProfile(broker=NAME, user_id=NAME, name="Practice (PAPER)")

    async def get_profile(self) -> BrokerProfile:
        return await self.authenticate()

    async def get_instruments(self, exchange: Optional[str] = None) -> List[Instrument]:
        return []

    async def get_ltp(self, symbols: List[str]) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for key in symbols:
            exchange, _, symbol = key.rpartition(":")
            price = await self._price(symbol, exchange or "NSE")
            if price is not None:
                out[key] = price
        return out

    async def get_quote(self, symbols: List[str]) -> Dict[str, Quote]:
        return {key: Quote(symbol=key, ltp=price) for key, price in (await self.get_ltp(symbols)).items()}

    async def get_historical_data(self, symbol: str, exchange: str, interval: str, from_date: datetime,
                                  to_date: datetime) -> List[OHLCVBar]:
        return []

    async def get_option_chain(self, underlying: str, expiry: Optional[date] = None) -> OptionChain:
        raise NotImplementedError("Practice mode reads the option chain through your own broker session")
