"""Phase Q / master prompt section 17: the pre-placement checks every *new entry* passes after
the kill switches and before the broker is asked for a fill.

Section 17 lists what an order must prove before placement: data fresh (Phase G1), broker
healthy (Phase G2 circuit breaker), risk approved (risk engine + Phase I hierarchy), **margin
available** and **instrument/expiry valid**. This module closes the last two:

* `validate_instrument` - the thing being ordered exists and can be traded today. An index
  spot symbol (NIFTY 50) is never a broker order: a deployment must derive an option or future
  from it. A resolved contract whose expiry has passed is refused. A plain symbol unknown to
  the broker's instrument master (when that master has been synced) is refused, because the
  broker would reject it anyway and the platform must say why *before* touching the broker.
* `live_margin_cap` - how many units the account's free margin allows, from the broker's own
  margin calculator for one lot/share (Zerodha basket margin, Upstox `/charges/margin`) against
  `MARGIN_SAFETY` of available margin. Generalises Phase F3's `written_lot_cap` (which stays
  strict: writing without a margin number is refused) to bought options, futures and equity.
  A bought option without a calculator answer falls back to premium x lot size, which *is* the
  exact cash the buy consumes. Equity or a future without a calculator answer is not guessed
  (intraday leverage differs per broker and per stock): the check is noted as skipped and the
  broker enforces margin at placement, exactly as before this phase.

Refusals are `PreCheckRefusal` (a business decision -> the order trail records REJECTED, never
FAILED). Notes are appended to the order's reasons so the trail shows what was verified.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.base import BrokerInterface
from app.brokers.models import BrokerOrderRequest
from app.core.enums import InstrumentKind, OptionPosition, OrderSide
from app.db.models import InstrumentRecord
from app.execution.contract_execution import MARGIN_SAFETY
from app.instruments.contracts import ResolvedContract
from app.instruments.master import INDEX_SYMBOLS, find_by_symbol, underlying_of
from app.instruments.registry import get_contract_spec
from app.market_data.calendar import IST

logger = logging.getLogger(__name__)


class PreCheckRefusal(ValueError):
    """The entry must not be placed; the text is the order trail's rejection reason."""


def is_index_symbol(symbol: str) -> bool:
    """`NIFTY 50`, `NIFTY BANK`, `SENSEX`... - quoted, but not an instrument a broker will take an
    order on. Their F&O contracts are (Phase F2 resolves them)."""
    return underlying_of(symbol) in INDEX_SYMBOLS


async def _master_rows(session: AsyncSession, broker: str, exchange: str) -> int:
    return int(await session.scalar(
        select(func.count()).select_from(InstrumentRecord)
        .where(InstrumentRecord.broker == broker, InstrumentRecord.exchange == exchange.upper())
    ) or 0)


async def validate_instrument(
    session: AsyncSession, *, symbol: str, exchange: str, mode: str, broker_name: Optional[str],
    contract: Optional[ResolvedContract] = None, today: Optional[date] = None,
) -> Tuple[List[str], List[str]]:
    """Returns `(refusals, notes)`. Empty refusals means the instrument may be ordered.

    Applied in both modes for what needs no broker (an expired contract is as wrong on paper as
    live); the index and master checks are LIVE-only so paper deployments on an index spot,
    which the strategy engine and its tests have always allowed, keep working.
    """
    today = today or datetime.now(IST).date()
    refusals: List[str] = []
    notes: List[str] = []

    if contract is not None:
        if contract.expiry < today:
            refusals.append(f"{contract.tradingsymbol} expired on {contract.expiry.isoformat()} - contract not tradable")
        elif contract.expiry == today:
            notes.append(f"{contract.tradingsymbol} expires today")
        return refusals, notes

    if mode != "LIVE":
        return refusals, notes

    if get_contract_spec(symbol) is not None:
        # MCX/crypto registry instruments carry their own exchange and lot size (Phase F1 note:
        # they are a static registry, not master rows), so the master lookup does not apply.
        return refusals, notes

    if is_index_symbol(symbol):
        refusals.append(
            f"{symbol} is an index, not a tradable instrument - set option or future contract rules on the deployment"
        )
        return refusals, notes

    if not broker_name:
        return refusals, notes
    broker_key = broker_name.lower()
    rows = await _master_rows(session, broker_key, exchange)
    if rows == 0:
        notes.append(f"Instrument master not synced for {broker_key}/{exchange.upper()}: symbol not verified before placement")
        return refusals, notes
    record = await find_by_symbol(session, symbol, broker=broker_key, exchange=exchange)
    if record is None:
        refusals.append(f"{symbol} is not in {broker_key}'s {exchange.upper()} instrument master - order refused")
    elif record.instrument_type == "INDEX":
        refusals.append(f"{symbol} is an index on {exchange.upper()} - not directly tradable")
    elif record.expiry is not None and record.expiry < today:
        refusals.append(f"{symbol} expired on {record.expiry.isoformat()} - contract not tradable")
    else:
        notes.append(f"{symbol} verified against {broker_key}'s instrument master (lot {record.lot_size})")
    return refusals, notes


async def live_margin_cap(
    broker: BrokerInterface, *, symbol: str, exchange: str, side: OrderSide, unit: float,
    product: str = "MIS", unit_price: Optional[float] = None, exact_cash: bool = False,
) -> Tuple[Optional[float], str]:
    """`(max_quantity, note)` for a LIVE entry. `unit` is one lot (or one share). `max_quantity`
    is None when margin could not be verified without guessing (no calculator and not a bought
    option); the note says so and the broker enforces it at placement.

    Raises `PreCheckRefusal` when margin *is* known and does not cover one unit, or when the
    broker cannot report its available margin at all (safety rule 7: uncertain fails safe).
    """
    probe = BrokerOrderRequest(
        symbol=symbol, exchange=exchange, transaction_type=side, quantity=unit, order_type="MARKET", product=product,
    )
    per_unit: Optional[float] = None
    try:
        reported = await broker.get_order_margin(probe)
        if reported is not None and float(reported) > 0:
            per_unit = float(reported)
    except Exception as exc:  # noqa: BLE001 - a calculator hiccup is "unknown", handled below
        logger.warning("%s margin calculator failed for %s: %s", broker.name, symbol, exc)
    source = "broker margin calculator"
    if per_unit is None:
        if exact_cash and unit_price is not None and unit_price > 0:
            per_unit = float(unit_price) * float(unit)
            source = "premium x lot"
        else:
            return None, f"Margin not verifiable with {broker.name} for {symbol}: the broker enforces it at placement"

    try:
        margins = await broker.get_margins()
    except Exception as exc:  # noqa: BLE001
        raise PreCheckRefusal(f"Could not read available margin from {broker.name}: {exc} - entry refused") from exc
    available = float(margins.available_margin or margins.available_cash or 0.0)
    units = int((available * MARGIN_SAFETY) // per_unit)
    if units < 1:
        raise PreCheckRefusal(
            f"Insufficient margin for {symbol}: {per_unit:,.0f} per {'lot' if unit != 1 else 'share'} needed "
            f"({source}), {available:,.0f} available"
        )
    cap = units * unit
    return cap, f"Margin {per_unit:,.0f}/{'lot' if unit != 1 else 'share'} ({source}), {available:,.0f} available -> at most {cap:g}"


def margin_probe_for(contract: Optional[ResolvedContract], *, symbol: str, exchange: str, side: OrderSide,
                     unit: float, unit_price: Optional[float]) -> dict:
    """The `live_margin_cap` arguments for a plain symbol or a resolved contract."""
    if contract is None:
        return {"symbol": symbol, "exchange": exchange, "side": side, "unit": unit, "unit_price": unit_price, "exact_cash": False}
    bought_option = contract.kind == InstrumentKind.OPTION and contract.position != OptionPosition.WRITE
    return {
        "symbol": contract.instrument_key if "|" in (contract.instrument_key or "") else contract.tradingsymbol,
        "exchange": contract.exchange, "side": contract.entry_side, "unit": float(contract.lot_size),
        "unit_price": unit_price, "exact_cash": bought_option,
    }
