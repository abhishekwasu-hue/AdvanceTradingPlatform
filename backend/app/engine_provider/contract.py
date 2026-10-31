"""M66 EN-1: the typed interface between ATP and the signal engine of the private package.

Generic by design: the names say what a value IS (a bar, an area, a swing, a signal), never how the engine decides.
The engine's rules, numbers and vocabulary stay in the private package (see loader.py); this file holds none of them.

Direction of the data:

    ATP  --EngineRequest-->  engine.read()  --EngineResult-->  ATP

Rules every value here follows (CLAUDE.md sections 3-5):
- Closed bars only, oldest first; a bar that is still running is never handed in. Times are timezone-aware.
- Missing data stays missing (`None`), it is never substituted (a zero volume is a real zero). The engine reports
  what it lacked in `EngineResult.data_problems`.
- No order field anywhere: no lots, quantity, order type, target or exit. Those come only from the user's own
  deployment settings. Nothing here places, blocks or changes an order (ADR-0004 / ADR-0006).
- No free text from the engine reaches a screen: the card is token ids (`app.signals.language.SignalStructure`) that
  ATP turns into words with `format_signal`; a token without a public phrase is refused there.
- A single price is never shown in signal text. `Signal.invalidation` is an internal reference only.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any, Literal, Optional, Protocol, runtime_checkable

from app.chart_analyze.contract import AREA_SIDES, SWING_LABELS, TREND_SIDES
from app.signals.language import SignalStructure

# The interface number. The package states the number it implements (`TradeEngineProvider.interface`); the loader
# accepts only this one. A change here is a public PR that keeps the previous number working until the package follows.
INTERFACE_VERSION = 1

AreaSide = Literal["buyer", "seller"]
SwingLabel = Literal["HH", "HL", "LH", "LL"]
TrendSide = Literal["up", "down", "sideways", "unclear"]


class ContractViolation(ValueError):
    """A value that breaks the interface (raised while building it; the loader treats it as 'no result')."""


def _aware(value: datetime, what: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ContractViolation(f"{what} must be timezone-aware")


# --- inputs ----------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Bar:
    """One closed bar.

    start / end: the bar's open time and close time (timezone-aware; ATP passes India time).
    open / high / low / close: the bar's prices.
    volume: the bar's own traded volume; None when the source has none (an index has none of its own).
    closing_auction: True when the bar lies in the exchange's closing-auction stretch; None when ATP did not mark it
        (the engine may then mark it itself from the bar times and the calendar).
    extended_session: for a daily bar - True when its range includes the closing-auction stretch; None = not marked.
    """
    start: datetime
    end: datetime
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float] = None
    closing_auction: Optional[bool] = None
    extended_session: Optional[bool] = None

    def __post_init__(self) -> None:
        _aware(self.start, "Bar.start")
        _aware(self.end, "Bar.end")
        if self.end <= self.start:
            raise ContractViolation("Bar.end must be after Bar.start")
        if not (self.low <= min(self.open, self.close) and max(self.open, self.close) <= self.high):
            raise ContractViolation("Bar prices must satisfy low <= open, close <= high")


@dataclass(frozen=True)
class Frame:
    """The closed bars of one timeframe, oldest first.

    timeframe: ATP's timeframe key ("5m", "15m", "1h", "1d", ...).
    bars: the bars, oldest first, each starting after the previous one.
    futures_volume: for an index only - the near-month future's volume on the same bar times, one value per bar
        (None for a bar the source does not have; never filled with a guess). None = no futures volume was handed in.
    """
    timeframe: str
    bars: tuple[Bar, ...]
    futures_volume: Optional[tuple[Optional[float], ...]] = None

    def __post_init__(self) -> None:
        for earlier, later in zip(self.bars, self.bars[1:]):
            if later.start <= earlier.start:
                raise ContractViolation(f"Frame {self.timeframe}: bars must be oldest first, without repeats")
        if self.futures_volume is not None and len(self.futures_volume) != len(self.bars):
            raise ContractViolation(f"Frame {self.timeframe}: futures_volume needs one value per bar")


@dataclass(frozen=True)
class EarlyClose:
    """A trading day that closes before the regular close time."""
    day: date
    close: time


@dataclass(frozen=True)
class MarketCalendar:
    """The exchange calendar the bars come from (operational data, not a reading rule).

    timezone: the IANA name of the exchange's time zone.
    regular_open / regular_close: the regular session's open and close time.
    holidays: the exchange holidays in the window of the bars.
    early_closes: the days in the window that close early, with their close time.
    """
    timezone: str
    regular_open: time
    regular_close: time
    holidays: tuple[date, ...] = ()
    early_closes: tuple[EarlyClose, ...] = ()


@dataclass(frozen=True)
class EngineRequest:
    """One read of one instrument as of the close of its last closed bar.

    symbol: ATP's instrument key; the engine never fetches data itself.
    as_of: the close time of the last closed bar of the signal timeframe; no bar handed in ends after it.
    frames: {timeframe: Frame} for every timeframe the engine asks for (`TradeEngineProvider.timeframes`).
    calendar: the exchange calendar of the window.
    settings: the user's own settings for his engine deployment, name -> value (validated by the package).
    """
    symbol: str
    as_of: datetime
    frames: Mapping[str, Frame]
    calendar: MarketCalendar
    settings: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _aware(self.as_of, "EngineRequest.as_of")
        for key, frame in self.frames.items():
            if key != frame.timeframe:
                raise ContractViolation(f"frames[{key!r}] holds a {frame.timeframe!r} frame")
            if frame.bars and frame.bars[-1].end > self.as_of:
                raise ContractViolation(f"Frame {key}: a bar ends after as_of (only closed bars may be handed in)")


# --- outputs ---------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class PriceRange:
    """A price band: low <= high (an area's edges, an entry zone)."""
    low: float
    high: float

    def __post_init__(self) -> None:
        if self.low > self.high:
            raise ContractViolation("PriceRange.low must not exceed PriceRange.high")


@dataclass(frozen=True)
class Area:
    """A price area the engine tracks.

    area_id: a stable id of the area within one result (signals refer to it).
    side: "buyer" or "seller" (`app.chart_analyze.contract.AREA_SIDES`).
    state: the area's state as a token id of the package (ATP shows only states it has a public phrase for).
    band: the area's edges.
    timeframe: the timeframe the area belongs to.
    formed_at: the close time of the bar the area was formed on.
    """
    area_id: str
    side: AreaSide
    state: str
    band: PriceRange
    timeframe: str
    formed_at: datetime

    def __post_init__(self) -> None:
        if self.side not in AREA_SIDES:
            raise ContractViolation(f"Area.side must be one of {AREA_SIDES}")
        _aware(self.formed_at, "Area.formed_at")


@dataclass(frozen=True)
class Swing:
    """A swing point: its label (`SWING_LABELS`), the bar it is on, its price and timeframe."""
    label: SwingLabel
    at: datetime
    price: float
    timeframe: str

    def __post_init__(self) -> None:
        if self.label not in SWING_LABELS:
            raise ContractViolation(f"Swing.label must be one of {SWING_LABELS}")
        _aware(self.at, "Swing.at")


@dataclass(frozen=True)
class HigherTimeframeSide:
    """The side of the higher timeframe the engine reads (`TREND_SIDES`), and that timeframe."""
    side: TrendSide
    timeframe: str

    def __post_init__(self) -> None:
        if self.side not in TREND_SIDES:
            raise ContractViolation(f"HigherTimeframeSide.side must be one of {TREND_SIDES}")


@dataclass(frozen=True)
class Evidence:
    """One item of a signal's evidence list: a token id and whether it is present. Listed, never weighted."""
    token: str
    present: bool


@dataclass(frozen=True)
class Signal:
    """One signal of the engine on a closed bar.

    model_id: the id of the engine model that produced it (a package id; never shown to a user).
    timeframe: the signal timeframe.
    bar_end: the close time of the bar the signal is on.
    side: the side of the area it refers to ("buyer" / "seller").
    area_id: the `Area.area_id` it refers to, if any.
    entry_zone: the price band the signal refers to.
    invalidation: internal reference only - where the reading no longer holds. Used as a stop reference only when the
        user's own exit settings say so; never shown and never in signal text.
    evidence: the evidence list (present / absent).
    card: the card's fields as token ids; ATP renders the words (`app.signals.language.format_signal`).
    instance_id: a stable id of this signal, so a scan, a backtest and a live read can be compared.
    """
    model_id: str
    timeframe: str
    bar_end: datetime
    side: AreaSide
    entry_zone: PriceRange
    card: SignalStructure
    area_id: Optional[str] = None
    invalidation: Optional[float] = None
    evidence: tuple[Evidence, ...] = ()
    instance_id: Optional[str] = None

    def __post_init__(self) -> None:
        if self.side not in AREA_SIDES:
            raise ContractViolation(f"Signal.side must be one of {AREA_SIDES}")
        _aware(self.bar_end, "Signal.bar_end")


@dataclass(frozen=True)
class EngineResult:
    """What one read returns.

    interface: the interface number this result follows (must equal INTERFACE_VERSION).
    version: the engine version that produced it.
    areas / swings: what the engine tracks as of the request's `as_of`.
    higher_side: the higher timeframe's side, if the engine reads one.
    signals: the signals on the last closed bar (usually none or one).
    data_problems: token ids of what was missing or wrong in the input; any one means no signal is acted on.
    """
    interface: int
    version: str
    areas: tuple[Area, ...] = ()
    swings: tuple[Swing, ...] = ()
    higher_side: Optional[HigherTimeframeSide] = None
    signals: tuple[Signal, ...] = ()
    data_problems: tuple[str, ...] = ()


# --- the provider ----------------------------------------------------------------------------------------------------

@runtime_checkable
class TradeEngineProvider(Protocol):
    """What the private package's `get_provider()` returns (loader.MODULE).

    name: the engine's name; version: its version; interface: the interface number it implements; timeframes: the
    timeframe keys it needs in `EngineRequest.frames`. Optional: `tag`, the private package's release tag (the loader
    also reads `TAG` / `__version__` from the module when the provider has none).
    `read` must be pure and deterministic: the same request gives the same result, and a read uses only the bars
    handed in (nothing after `as_of`).
    """

    name: str
    version: str
    interface: int
    timeframes: tuple[str, ...]

    def read(self, request: EngineRequest) -> EngineResult: ...
