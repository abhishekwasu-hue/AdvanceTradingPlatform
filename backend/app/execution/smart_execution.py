"""Terminal master order Part 7, SMART EXECUTION (owner decisions 23 Oct: TM-3, TM-9, TM-10, TM-11; flag
`smart_execution`, OFF by default, behind `ui_v2`).

One engine for every multi-leg order - the basket today, the bots and the trade terminal when they move onto it - that
works through a `BrokerInterface`: the PaperExecutionAdapter now (TM-1), the user's own broker adapter when Abhi turns
LIVE on (the same code; the `phase0_paper_only` lock and the `live_trading` flag are never read or changed here).

* **leg sequencing** - entry: every BUY (hedge) leg first, then the SELL legs, so the margin benefit applies and the
  short is never sent uncovered; exit: the reverse, the SELL (short) legs are closed first, then the BUY legs, so a hedge
  is never closed while its short is open (`entry_sequence`, `exit_sequence`);
* **no naked leg (TM-11)** - no SELL leg is sent before every hedge is confirmed filled. The first leg that fails stops
  the run: the state is INCOMPLETE with what filled, what was not placed, and the two choices "Complete the rest" /
  "Close the filled legs" - only on the user's tap, nothing is retried or undone by itself (`run`);
* **freeze slicing** - an order above the exchange's freeze quantity goes as several orders of whole lots, each below the
  limit (`slice_units`, `app/instruments/freeze_limits.py`); a symbol whose limit is not known is never sliced;
* **marketable limit (TM-3)** - where the adapter says market orders are not allowed, a limit a protection band past the
  price, the band from the user's own setting (`order_type_for`); exits use it too and are never blocked (ADR-0004);
* **margin (TM-10)** - the broker's basket margin with the hedge benefit through the user's own session where the
  adapter has one; else ATP's estimate, labelled so; plus the free balance after the order (`margin_view`);
* **physical settlement** - stock F&O positions in the money or within the user's band, a set number of days before
  expiry, with the delivery value in rupees (`settlement_flags`; Positions and Telegram).

Every number in a rule comes from the user's settings (`SmartSettings`, prefs namespace `smart_execution`) or from the
exchange's own data; the AI never calls any of this (ADR-0006).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Awaitable, Callable, Dict, Generic, List, Optional, Sequence, Tuple, TypeVar

FLAG = "smart_execution"
NAMESPACE = "smart_execution"
COMPLETE, INCOMPLETE, NOTHING_PLACED = "COMPLETE", "INCOMPLETE", "NOTHING_PLACED"
ACTION_COMPLETE_REST, ACTION_CLOSE_FILLED, ACTION_CLOSE_REST = "complete_rest", "close_filled", "close_rest"
ESTIMATE_LABEL = "ATP's estimate, not your broker's number"
T = TypeVar("T")


@dataclass(frozen=True)
class SmartSettings:
    """The user's own settings the rules read (prefs namespace `smart_execution`; the defaults are the setting's own
    starting values, shown and editable on Settings)."""
    settlement_days_before: int
    settlement_band_pct: float
    settlement_telegram: bool
    protection_band_pct: float

    @classmethod
    def from_prefs(cls, values: Dict[str, Any]) -> "SmartSettings":
        return cls(settlement_days_before=int(values["settlement_days_before"]), settlement_band_pct=float(values["settlement_band_pct"]),
                   settlement_telegram=bool(values["settlement_telegram"]), protection_band_pct=float(values["protection_band_pct"]))


async def settings_for(session: Any, user: Any) -> SmartSettings:
    from app.me import prefs
    values, _ = await prefs.read_one(NAMESPACE, user, session)
    return SmartSettings.from_prefs(values)


# --- sequencing ----------------------------------------------------------------------------------------------------

def entry_sequence(sides: Sequence[str]) -> List[int]:
    """Indexes in placement order: every BUY (hedge) leg first, then every SELL leg, each group in the user's order."""
    return [i for i, s in enumerate(sides) if s.upper() == "BUY"] + [i for i, s in enumerate(sides) if s.upper() != "BUY"]


def exit_sequence(sides: Sequence[str]) -> List[int]:
    """Indexes of open legs in closing order: the SELL (short) legs first, then the BUY legs - a hedge is never closed
    while its short is still open. `sides` are the sides the legs were OPENED with."""
    return [i for i, s in enumerate(sides) if s.upper() != "BUY"] + [i for i, s in enumerate(sides) if s.upper() == "BUY"]


# --- slicing -------------------------------------------------------------------------------------------------------

def slice_units(units: int, lot_size: int, freeze_qty: Optional[int]) -> List[int]:
    """The order's quantity as slices of whole lots, each below the exchange's freeze quantity; one slice when the
    limit is not known or not reached. The slices add up to the order exactly."""
    from app.instruments.freeze_limits import max_slice_units
    units, lot_size = int(units), max(1, int(lot_size))
    if units <= 0:
        return []
    biggest = max_slice_units(freeze_qty, lot_size)
    if biggest is None or units <= biggest:
        return [units]
    full, rest = divmod(units, biggest)
    return [biggest] * full + ([rest] if rest else [])


def slice_note(units: int, lot_size: int, freeze_qty: Optional[int], underlying: str) -> Optional[str]:
    parts = slice_units(units, lot_size, freeze_qty)
    if freeze_qty is None or len(parts) <= 1:
        return None
    return (f"Above the exchange's freeze limit ({freeze_qty:,} qty for {underlying}): sent as {len(parts)} orders of at most "
            f"{max(parts) // lot_size} lots each.")


# --- the order type where market orders are not allowed (TM-3) -----------------------------------------------------

def order_type_for(market_orders_allowed: bool, side: str, price: float, band_pct: float, tick: float = 0.05) -> Tuple[str, Optional[float]]:
    """("MARKET", None) where the broker takes market orders; else ("LIMIT", a price `band_pct` past `price` - above
    for a buy, below for a sell - on the tick), so the order is marketable and still fills in a fast move."""
    if market_orders_allowed:
        return "MARKET", None
    # One price rule for every path (TM-3): the same function the broker adapters and the PAPER adapter use.
    from app.core.enums import OrderSide
    from app.execution.marketable_limit import marketable_limit_price
    return "LIMIT", marketable_limit_price(price, OrderSide.BUY if side.upper() == "BUY" else OrderSide.SELL, band_pct, tick)


# --- the run: one state machine for entries and exits (TM-11) -------------------------------------------------------

@dataclass
class LegOutcome(Generic[T]):
    index: int
    seq: int
    side: str
    status: str                          # FILLED | FAILED | NOT_PLACED
    reasons: List[str] = field(default_factory=list)
    result: Optional[T] = None


@dataclass
class RunOutcome(Generic[T]):
    state: str                           # COMPLETE | INCOMPLETE | NOTHING_PLACED
    legs: List[LegOutcome[T]]
    actions: List[str]
    message: str
    exiting: bool = False

    @property
    def filled(self) -> List[LegOutcome[T]]:
        return [leg for leg in self.legs if leg.status == "FILLED"]

    @property
    def pending(self) -> List[LegOutcome[T]]:
        return [leg for leg in self.legs if leg.status != "FILLED"]


Place = Callable[[int, int], Awaitable[Tuple[bool, List[str], Any]]]


async def run(sides: Sequence[str], place: Place, *, exiting: bool = False) -> RunOutcome[Any]:
    """Runs the legs in order (entry: hedges first; exit: shorts first). `place(index, seq)` returns (filled, reasons,
    result). The first leg that does not fill stops the run - entering, no SELL is sent without every hedge filled;
    exiting, no hedge is closed while a short is open - and the outcome says what to do next."""
    order = exit_sequence(sides) if exiting else entry_sequence(sides)
    legs: List[LegOutcome[Any]] = []
    stopped: Optional[LegOutcome[Any]] = None
    for seq, index in enumerate(order, start=1):
        side = sides[index].upper()
        if stopped is not None:
            if exiting:
                why = "Not closed yet: the short leg before it did not close, and closing this one would leave that short uncovered."
            elif side != "BUY" and stopped.side == "BUY":
                why = "Not placed: a hedge leg did not fill, and on its own this sold leg would be uncovered."
            else:
                why = "Not placed yet: an earlier leg did not fill. Nothing is placed by itself."
            legs.append(LegOutcome(index=index, seq=seq, side=side, status="NOT_PLACED", reasons=[why]))
            continue
        filled, reasons, result = await place(index, seq)
        leg = LegOutcome(index=index, seq=seq, side=side, status="FILLED" if filled else "FAILED", reasons=list(reasons), result=result)
        legs.append(leg)
        if not filled:
            stopped = leg
    done = [leg for leg in legs if leg.status == "FILLED"]
    if len(done) == len(legs):
        return RunOutcome(COMPLETE, legs, [], "Every leg closed." if exiting else "Every leg filled.", exiting)
    if not done:
        what = "Nothing was closed" if exiting else "Nothing was placed"
        return RunOutcome(NOTHING_PLACED, legs, [], f"{what}: the first leg did not go through. Fix what it says and try again.", exiting)
    if exiting:
        return RunOutcome(INCOMPLETE, legs, [ACTION_CLOSE_REST],
                          f"{len(done)} of {len(legs)} legs closed. The rest are still open: close the rest, or each with Exit now.", True)
    return RunOutcome(INCOMPLETE, legs, [ACTION_COMPLETE_REST, ACTION_CLOSE_FILLED],
                      f"Incomplete: {len(done)} of {len(legs)} legs filled. Complete the rest, or close the filled legs. Nothing "
                      "happens until you choose.", False)


# --- margin with the hedge benefit (TM-10) --------------------------------------------------------------------------

@dataclass
class MarginView:
    source: str                          # "broker" | "estimate"
    label: str
    required: Optional[float]
    without_hedge: Optional[float]
    benefit: Optional[float]
    free_before: Optional[float]
    free_after: Optional[float]
    free_source: str                     # "broker" | "practice"
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {"source": self.source, "label": self.label, "required": self.required, "without_hedge": self.without_hedge,
                "benefit": self.benefit, "free_before": self.free_before, "free_after": self.free_after, "free_source": self.free_source,
                "notes": list(self.notes)}


def margin_view(broker_margin: Optional[Dict[str, Any]], estimate: Dict[str, Any], free_before: Optional[float], *,
                free_source: str, broker_name: Optional[str] = None) -> MarginView:
    """The broker's basket margin (required with the hedge, and without it when the broker says) where it answered,
    else ATP's labelled estimate (`fno_basket.hedge_margin`); the free balance after = before - required."""
    notes: List[str] = []
    required: Optional[float]
    if broker_margin and broker_margin.get("required") is not None:
        required = float(broker_margin["required"])
        without = broker_margin.get("without_hedge")
        benefit = round(float(without) - required, 2) if without is not None else None
        label = f"Your broker's margin ({broker_name or 'broker'}), with the hedge benefit"
        source = "broker"
    else:
        with_hedge = (estimate or {}).get("with_hedge") or {}
        required = with_hedge.get("value")
        without = ((estimate or {}).get("without_hedge") or {}).get("value")
        benefit = (estimate or {}).get("benefit")
        label = ESTIMATE_LABEL
        source = "estimate"
        if broker_margin is not None and broker_margin.get("error"):
            notes.append("Your broker's margin could not be read just now: this is ATP's estimate.")
        if required is None:
            notes.append("A sold leg without its hedge needs your broker's SPAN margin, which this estimate does not have.")
    free_after = round(free_before - float(required), 2) if free_before is not None and required is not None else None
    if free_source == "practice":
        notes.append("Free balance: your practice balance; the margin of positions you already hold is not counted.")
    return MarginView(source=source, label=label, required=None if required is None else round(float(required), 2),
                      without_hedge=None if without is None else round(float(without), 2), benefit=benefit,
                      free_before=free_before, free_after=free_after, free_source=free_source, notes=notes)


async def broker_basket_margin(broker: Any, orders: Sequence[Any]) -> Optional[Dict[str, Any]]:
    """The broker's basket margin through `broker` (the user's own session): {required, without_hedge} or None when
    the adapter has no basket margin call; {error} when the call failed (the caller falls back to the estimate)."""
    if broker is None:
        return None
    try:
        out = await broker.get_basket_margin(list(orders))
    except Exception as exc:  # noqa: BLE001 - a margin read never stands in the way: the estimate is shown instead
        return {"error": type(exc).__name__}
    if out is None:
        return None
    return {"required": out.required, "without_hedge": out.without_hedge}


# --- physical settlement (stock F&O) --------------------------------------------------------------------------------

@dataclass
class Holding:
    trade_id: int
    underlying: str
    right: str                           # CE | PE | FUT
    strike: Optional[float]
    expiry: date
    quantity: float
    side: str                            # BUY | SELL (as opened)
    label: str


def is_stock_underlying(underlying: str) -> bool:
    """Stock F&O settles by delivery; index F&O settles in cash."""
    from app.instruments.master import INDEX_SYMBOLS, underlying_of
    return underlying_of(underlying) not in INDEX_SYMBOLS


def moneyness(right: str, strike: float, spot: float, band_pct: float) -> Optional[str]:
    """"ITM", "NEAR_ITM" (within the user's band of the strike, still out of the money) or None."""
    if right == "CE":
        if spot > strike:
            return "ITM"
        return "NEAR_ITM" if spot >= strike * (1 - band_pct / 100.0) else None
    if right == "PE":
        if spot < strike:
            return "ITM"
        return "NEAR_ITM" if spot <= strike * (1 + band_pct / 100.0) else None
    return None


def settlement_flags(holdings: Sequence[Holding], spots: Dict[str, float], today: date, settings: SmartSettings) -> List[Dict[str, Any]]:
    """Every stock F&O position that may settle by delivery: within `settlement_days_before` days of its expiry, a
    future (always delivered if held), or an option in the money or within the user's band. The delivery value is the
    contract's quantity at the strike (an option) or at the price (a future). Information only: nothing is closed."""
    out: List[Dict[str, Any]] = []
    for h in holdings:
        if not is_stock_underlying(h.underlying):
            continue
        days = (h.expiry - today).days
        if days < 0 or days > settings.settlement_days_before:
            continue
        spot = spots.get(h.underlying)
        state: Optional[str]
        if h.right == "FUT":
            state = "FUTURE"
            value = round(spot * h.quantity, 2) if spot else None
        else:
            if spot is None or h.strike is None:
                continue
            state = moneyness(h.right, float(h.strike), float(spot), settings.settlement_band_pct)
            if state is None:
                continue
            value = round(float(h.strike) * h.quantity, 2)
        gives = _delivery_side(h)
        when = "today" if days == 0 else ("tomorrow" if days == 1 else f"in {days} days")
        what = {"ITM": "is in the money", "NEAR_ITM": f"is within {settings.settlement_band_pct:g}% of the money",
                "FUTURE": "is a stock future"}[state]
        text = (f"{h.label} {what} and expires {when}. Held to expiry it settles by delivery: you would {gives} "
                f"{h.quantity:g} shares of {h.underlying}" + (f" (about ₹{value:,.0f})." if value is not None else "."))
        out.append({"trade_id": h.trade_id, "label": h.label, "underlying": h.underlying, "expiry": h.expiry.isoformat(),
                    "days_to_expiry": days, "state": state, "delivery_value": value, "quantity": h.quantity,
                    "obligation": gives, "message": text})
    return out


def _delivery_side(h: Holding) -> str:
    long_ = h.side.upper() == "BUY"
    if h.right == "FUT":
        return "take delivery of" if long_ else "deliver"
    if h.right == "CE":
        return "take delivery of" if long_ else "deliver"
    return "deliver" if long_ else "take delivery of"
