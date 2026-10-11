"""Z6: behaviour-tag detectors - pure functions over a list of journal trades (no database, no clock, no AI).

Each detector says which trades show one behaviour and why (the evidence the trader sees before confirming or
dismissing the tag). `detect()` runs them all over `trades` and returns the detections for the trades in `target_ids`;
the others are context (the losing exit before a revenge entry, the open position an add averaged into, the days that
set the trader's own daily norm). Same input, same output: sorted, no randomness, no wall clock (`now` is passed in).

Tags:
* revenge_trade      - entry within `revenge_window_minutes` of a losing exit, with a larger size than that loser;
* fomo_entry         - filled beyond the plan's entry in the chasing direction by more than `fomo_chase_r` of the
                       planned risk;
* averaging_loser    - a new entry in the same symbol and direction while an earlier position is open and under water
                       (the new fill is worse than the earlier one's);
* moved_stop_against - a stop change that moved the stop farther from the entry (more risk);
* no_stop            - no protective stop at entry (missing, zero, or on the wrong side of the entry);
* overtrading        - trades beyond the daily norm (fixed, or the trader's own median times a multiple);
* early_exit         - closed between the stop and the first target by the trader's own decision (an exit by hand,
                       an approved AI-action exit) rather than by one of the platform's exit rules, with no
                       invalidation;
* held_through_event - open across the start of a market event (events calendar) that applies to the underlying.

Legs of one multi-leg structure (same `group_id`) are one decision: they count once for the daily norm and are never
compared with each other.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from statistics import median
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from app.journal.settings import DisciplineSettings
from app.market_data.calendar import IST

# Exit reasons the platform's own rules write (stop, target, time rule, square-off, structure exits, kill switch, stop
# guard...). An exit whose reason contains none of these was taken by hand.
SYSTEMATIC_EXIT_MARKERS: Tuple[str, ...] = (
    "stop", "target", "time", "square", "end of day", "eod", "structure", "spread", "breakeven", "breached", "trail",
    "premium floor", "premium ceiling", "emergency", "kill", "expir", "lockout", "reconcil", "invalidat",
    "protective",
)
# Exit reasons that are the TRADER's decision even when they mention a stop or a target: an approved AI-action exit
# ("AI action #12 (stop_too_wide) approved" - the trader pressed Approve; the AI only proposed) and a hand exit ("Exit
# now", "by hand", "manual"). Checked before the systematic markers.
TRADER_EXIT_MARKERS: Tuple[str, ...] = ("ai action", "exit now", "by hand", "manual")


@dataclass(frozen=True)
class StopChange:
    at: datetime
    old: float
    new: float
    source: str = "rule"


@dataclass(frozen=True)
class JournalTrade:
    """What the detectors read about one trade. Times are timezone-aware."""

    id: int
    symbol: str
    underlying: str
    direction: str                       # LONG / SHORT (on the contract held)
    entry_time: datetime
    entry_price: float
    quantity: float
    plan_entry: Optional[float] = None   # the price the plan/signal expected
    initial_stop: Optional[float] = None
    final_stop: Optional[float] = None
    target1: Optional[float] = None
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    pnl: Optional[float] = None
    stop_changes: Tuple[StopChange, ...] = ()
    group_id: Optional[str] = None
    has_group_stop: bool = False         # a multi-leg structure judged by its group stop

    @property
    def unit(self) -> str:
        return f"g:{self.group_id}" if self.group_id else f"t:{self.id}"

    @property
    def sign(self) -> int:
        return 1 if self.direction == "LONG" else -1

    @property
    def risk_per_unit(self) -> Optional[float]:
        if self.initial_stop is None or self.initial_stop <= 0:
            return None
        risk = self.sign * (self.entry_price - self.initial_stop)
        return risk if risk > 0 else None

    @property
    def size(self) -> Tuple[str, float]:
        """('risk', amount) when the trade has a valid stop, else ('notional', amount)."""
        per_unit = self.risk_per_unit
        if per_unit is not None:
            return "risk", per_unit * abs(self.quantity)
        return "notional", abs(self.entry_price * self.quantity)

    def open_at(self, when: datetime) -> bool:
        return self.entry_time <= when and (self.exit_time is None or self.exit_time > when)


@dataclass(frozen=True)
class MarketEvent:
    id: int
    kind: str
    scope: Optional[str]                 # None / "*" = every symbol; "INDEX" = the index bucket; else one underlying
    at: datetime                         # the event's start (IST date + start time, or the session open)
    description: str = ""


@dataclass(frozen=True)
class Detection:
    trade_id: int
    tag: str
    evidence: Dict = field(default_factory=dict)


ScopeMatch = Callable[[Optional[str], str], bool]


def _r(value: float, places: int = 2) -> float:
    return round(float(value), places)


def _ist_day(when: datetime) -> date:
    return when.astimezone(IST).date()


# --- the detectors ----------------------------------------------------------------------------------------------------

def revenge_trades(trades: Sequence[JournalTrade], s: DisciplineSettings, targets: Set[int]) -> List[Detection]:
    window = timedelta(minutes=s.revenge_window_minutes)
    losers = sorted((t for t in trades if t.exit_time is not None and t.pnl is not None and t.pnl < 0), key=lambda t: (t.exit_time, t.id))
    out: List[Detection] = []
    for t in trades:
        if t.id not in targets:
            continue
        prior = [l for l in losers if l.exit_time is not None and l.exit_time <= t.entry_time and t.entry_time - l.exit_time <= window
                 and l.id != t.id and (t.group_id is None or l.group_id != t.group_id)]
        if not prior:
            continue
        loser = prior[-1]
        if loser.exit_time is None:
            continue
        kind_t, size_t = t.size
        kind_l, size_l = loser.size
        if kind_t != kind_l:                       # compare like with like: notional when either has no stop
            kind_t, size_t = "notional", abs(t.entry_price * t.quantity)
            kind_l, size_l = "notional", abs(loser.entry_price * loser.quantity)
        if size_l <= 0 or size_t <= size_l * s.revenge_size_multiple:
            continue
        out.append(Detection(t.id, "revenge_trade", {
            "after_trade_id": loser.id, "after_loss": _r(loser.pnl or 0.0),
            "minutes_after_loss": _r((t.entry_time - loser.exit_time).total_seconds() / 60.0, 1),
            "size_basis": kind_t, "size": _r(size_t), "losing_trade_size": _r(size_l), "size_ratio": _r(size_t / size_l),
            "window_minutes": s.revenge_window_minutes, "size_multiple": s.revenge_size_multiple,
        }))
    return out


def fomo_entries(trades: Sequence[JournalTrade], s: DisciplineSettings, targets: Set[int]) -> List[Detection]:
    out: List[Detection] = []
    for t in trades:
        if t.id not in targets or t.plan_entry is None or t.initial_stop is None or t.initial_stop <= 0:
            continue
        planned_risk = abs(t.plan_entry - t.initial_stop)
        if planned_risk <= 0:
            continue
        chase = t.sign * (t.entry_price - t.plan_entry)          # positive = paid more (long) / sold lower (short)
        chase_r = chase / planned_risk
        if chase_r > s.fomo_chase_r:
            out.append(Detection(t.id, "fomo_entry", {
                "plan_entry": _r(t.plan_entry, 4), "fill": _r(t.entry_price, 4), "chase": _r(chase, 4),
                "chase_r": _r(chase_r, 3), "threshold_r": s.fomo_chase_r,
            }))
    return out


def averaging_losers(trades: Sequence[JournalTrade], s: DisciplineSettings, targets: Set[int]) -> List[Detection]:
    out: List[Detection] = []
    for t in trades:
        if t.id not in targets:
            continue
        under_water = [p for p in trades if p.id != t.id and p.symbol == t.symbol and p.direction == t.direction
                       and (t.group_id is None or p.group_id != t.group_id)
                       and (p.entry_time, p.id) < (t.entry_time, t.id) and p.open_at(t.entry_time)
                       and t.sign * (t.entry_price - p.entry_price) < 0]
        if under_water:
            first = min(under_water, key=lambda p: (p.entry_time, p.id))
            out.append(Detection(t.id, "averaging_loser", {
                "added_to_trade_id": first.id, "open_entry": _r(first.entry_price, 4), "add_fill": _r(t.entry_price, 4),
                "open_positions": sorted(p.id for p in under_water),
            }))
    return out


def moved_stops(trades: Sequence[JournalTrade], s: DisciplineSettings, targets: Set[int]) -> List[Detection]:
    out: List[Detection] = []
    for t in trades:
        if t.id not in targets:
            continue
        moves = [c for c in sorted(t.stop_changes, key=lambda c: c.at) if t.sign * (c.new - c.old) < 0]
        if moves:
            out.append(Detection(t.id, "moved_stop_against", {
                "moves": [{"at": c.at.isoformat(), "from": _r(c.old, 4), "to": _r(c.new, 4), "source": c.source} for c in moves],
            }))
        elif (t.initial_stop is not None and t.final_stop is not None and t.initial_stop > 0 and t.final_stop > 0
              and t.sign * (t.final_stop - t.initial_stop) < 0):
            # No history recorded (the change happened before capture was on): the trade row still shows it.
            out.append(Detection(t.id, "moved_stop_against", {
                "moves": [{"at": None, "from": _r(t.initial_stop, 4), "to": _r(t.final_stop, 4), "source": "trade row"}],
            }))
    return out


def no_stops(trades: Sequence[JournalTrade], s: DisciplineSettings, targets: Set[int]) -> List[Detection]:
    out: List[Detection] = []
    for t in trades:
        if t.id not in targets or t.has_group_stop:
            continue
        if t.initial_stop is None or t.initial_stop <= 0:
            out.append(Detection(t.id, "no_stop", {"stop": None, "why": "no stop at entry"}))
        elif t.risk_per_unit is None:
            out.append(Detection(t.id, "no_stop", {"stop": _r(t.initial_stop, 4), "entry": _r(t.entry_price, 4),
                                                   "why": "stop not on the protective side of the entry"}))
    return out


def _daily_units(trades: Iterable[JournalTrade]) -> Dict[date, List[Tuple[datetime, str]]]:
    """Per IST day, each decision (a trade or a whole structure) once, with its first entry time, in order."""
    first: Dict[str, Tuple[datetime, str]] = {}
    for t in trades:
        key = t.unit
        if key not in first or t.entry_time < first[key][0]:
            first[key] = (t.entry_time, key)
    days: Dict[date, List[Tuple[datetime, str]]] = defaultdict(list)
    for when, key in first.values():
        days[_ist_day(when)].append((when, key))
    for items in days.values():
        items.sort()
    return days


def daily_norm(days: Dict[date, List[Tuple[datetime, str]]], day: date, s: DisciplineSettings) -> Tuple[int, str]:
    """The trades-per-day norm for `day` and how it was set."""
    if s.overtrading_daily_norm > 0:
        return int(s.overtrading_daily_norm), "fixed"
    start = day - timedelta(days=s.overtrading_lookback_days)
    counts = [len(v) for d, v in days.items() if start <= d < day and v]
    if not counts:
        return int(s.overtrading_min_norm), "minimum (no earlier days)"
    own = math.ceil(median(counts) * s.overtrading_multiple)
    return max(int(s.overtrading_min_norm), own), f"own median {median(counts):g} x {s.overtrading_multiple:g} over {len(counts)} days"


def overtrading(trades: Sequence[JournalTrade], s: DisciplineSettings, targets: Set[int]) -> List[Detection]:
    days = _daily_units(trades)
    by_unit: Dict[str, List[JournalTrade]] = defaultdict(list)
    for t in trades:
        by_unit[t.unit].append(t)
    out: List[Detection] = []
    for day in sorted(days):
        units = days[day]
        norm, basis = daily_norm(days, day, s)
        for index, (_when, key) in enumerate(units):
            if index < norm:
                continue
            for t in sorted(by_unit[key], key=lambda x: x.id):
                if t.id in targets:
                    out.append(Detection(t.id, "overtrading", {"day": day.isoformat(), "trade_number": index + 1, "trades_that_day": len(units),
                                                               "daily_norm": norm, "norm_basis": basis}))
    return out


def is_trader_exit(reason: Optional[str]) -> bool:
    text = (reason or "").lower()
    return any(marker in text for marker in TRADER_EXIT_MARKERS)


def is_systematic_exit(reason: Optional[str]) -> bool:
    """An exit one of the platform's rules took on its own. A trader's decision (`is_trader_exit`) never is, whatever
    words its reason carries."""
    if is_trader_exit(reason):
        return False
    text = (reason or "").lower()
    return any(marker in text for marker in SYSTEMATIC_EXIT_MARKERS)


def early_exits(trades: Sequence[JournalTrade], s: DisciplineSettings, targets: Set[int]) -> List[Detection]:
    out: List[Detection] = []
    for t in trades:
        if t.id not in targets or t.exit_price is None or t.exit_time is None or t.target1 is None:
            continue
        stop = t.final_stop if t.final_stop is not None and t.final_stop > 0 else t.initial_stop
        if stop is None or stop <= 0 or is_systematic_exit(t.exit_reason):
            continue
        low, high = sorted((stop, t.target1))
        if low < t.exit_price < high:
            progress = None
            if t.target1 != t.entry_price:
                progress = _r((t.exit_price - t.entry_price) / (t.target1 - t.entry_price), 3)
            out.append(Detection(t.id, "early_exit", {"exit": _r(t.exit_price, 4), "target1": _r(t.target1, 4), "stop": _r(stop, 4),
                                                      "exit_reason": (t.exit_reason or "")[:100], "share_of_target_move": progress}))
    return out


def held_through_events(trades: Sequence[JournalTrade], s: DisciplineSettings, targets: Set[int], events: Sequence[MarketEvent],
                        now: datetime, scope_match: ScopeMatch) -> List[Detection]:
    kinds = {k.upper() for k in s.event_kinds}
    relevant = sorted((e for e in events if e.kind.upper() in kinds), key=lambda e: (e.at, e.id))
    out: List[Detection] = []
    for t in trades:
        if t.id not in targets:
            continue
        end = t.exit_time or now
        hits = [e for e in relevant if t.entry_time < e.at < end and scope_match(e.scope, t.underlying)]
        if hits:
            out.append(Detection(t.id, "held_through_event", {"events": [{"id": e.id, "kind": e.kind, "at": e.at.isoformat(),
                                                                          "description": e.description[:120]} for e in hits]}))
    return out


def detect(trades: Sequence[JournalTrade], settings: DisciplineSettings, *, events: Sequence[MarketEvent] = (),
           target_ids: Optional[Iterable[int]] = None, now: datetime, scope_match: Optional[ScopeMatch] = None) -> List[Detection]:
    """Every detection for the trades in `target_ids` (default: all), sorted by (trade id, tag)."""
    ordered = sorted(trades, key=lambda t: (t.entry_time, t.id))
    targets = set(target_ids) if target_ids is not None else {t.id for t in ordered}
    match = scope_match or (lambda scope, underlying: not scope or scope == "*" or scope.upper() == underlying.upper())
    found: List[Detection] = []
    found += revenge_trades(ordered, settings, targets)
    found += fomo_entries(ordered, settings, targets)
    found += averaging_losers(ordered, settings, targets)
    found += moved_stops(ordered, settings, targets)
    found += no_stops(ordered, settings, targets)
    found += overtrading(ordered, settings, targets)
    found += early_exits(ordered, settings, targets)
    found += held_through_events(ordered, settings, targets, events, now, match)
    return sorted(found, key=lambda d: (d.trade_id, d.tag))
