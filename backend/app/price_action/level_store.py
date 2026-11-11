"""M99 B1-c: a generic in-run store of labelled levels and zones, read as of a bar - the interface the M99 packs (swap
levels, Fibonacci levels, clusters) write their levels to and read them back from.

In memory, for one run over one frame of one timeframe: no database, no migration. It follows the as-of rule of
`app.price_action.zones.read` (the snapshot a bar sees is the latest one at or before that bar's close): every write is
stamped with the bar at whose close it became known, and a read at bar t sees exactly the records stamped <= t - the
level as it was known then (its band and state), never a later update.

Rules:
  * the clock only moves forward: a write stamped before the latest write is refused (no back-dating);
  * a level is never deleted or edited in place - a change (state, band) is a new record at its bar; `history` keeps
    them all;
  * levels from different sources or timeframes are never merged: each `add` is its own level with its own id.
Reference only - nothing here places an order.
"""
from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable
from dataclasses import dataclass, replace

ACTIVE, TESTED, BROKEN, FLIPPED, EXPIRED = "ACTIVE", "TESTED", "BROKEN", "FLIPPED", "EXPIRED"
STATES = (ACTIVE, TESTED, BROKEN, FLIPPED, EXPIRED)


@dataclass(frozen=True)
class Level:
    """One level (low == high) or zone (low < high) as known at `as_of_bar`."""
    id: int
    source: str                 # who made it, e.g. "swap", "fib", "cluster", "swing"
    label: str                  # what it is, e.g. "0.618", "swap_high"
    low: float
    high: float
    created_bar: int            # the bar at whose close it first became known
    state: str
    state_bar: int              # the bar of the record this view comes from
    tf: str = ""
    meta: tuple[tuple[str, object], ...] = ()

    @property
    def price(self) -> float:
        """The level's price (a zone's middle)."""
        return (self.low + self.high) / 2.0

    @property
    def is_zone(self) -> bool:
        return self.high > self.low

    def contains(self, price: float) -> bool:
        return self.low <= price <= self.high


class LevelStore:
    """Labelled levels / zones of one run, each with its history of records; as-of reads only."""

    def __init__(self, tf: str = "") -> None:
        self.tf = tf
        self._history: dict[int, list[Level]] = {}
        self._bars: dict[int, list[int]] = {}              # per level: its records' bars (for bisect)
        self._clock = -1
        self._next = 1

    # ------------------------------------------------------------------ writes
    def _tick(self, bar: int) -> int:
        bar = int(bar)
        if bar < 0:
            raise ValueError("a level's bar must be >= 0")
        if bar < self._clock:
            raise ValueError(f"bar {bar} is before the store's latest write (bar {self._clock}): no back-dating")
        self._clock = bar
        return bar

    def add(self, bar: int, source: str, label: str, low: float, high: float | None = None, state: str = ACTIVE,
            meta: dict[str, object] | None = None) -> int:
        """A new level known at `bar`'s close (`high` None: a single price). Returns its id."""
        if state not in STATES:
            raise ValueError(f"unknown level state {state!r}")
        hi = float(low) if high is None else float(high)
        lo = float(low)
        if hi < lo:
            lo, hi = hi, lo
        bar = self._tick(bar)
        lid = self._next
        self._next += 1
        rec = Level(lid, str(source), str(label), lo, hi, bar, state, bar, self.tf, tuple(sorted((meta or {}).items())))
        self._history[lid] = [rec]
        self._bars[lid] = [bar]
        return lid

    def update(self, level_id: int, bar: int, state: str | None = None, low: float | None = None,
               high: float | None = None) -> Level:
        """A change to a level known at `bar`'s close: a new record (the older ones stay)."""
        if level_id not in self._history:
            raise KeyError(f"no level {level_id}")
        if state is not None and state not in STATES:
            raise ValueError(f"unknown level state {state!r}")
        bar = self._tick(bar)
        last = self._history[level_id][-1]
        lo = last.low if low is None else float(low)
        hi = last.high if high is None else float(high)
        if hi < lo:
            lo, hi = hi, lo
        rec = replace(last, low=lo, high=hi, state=last.state if state is None else state, state_bar=bar)
        if self._bars[level_id][-1] == bar:                 # two changes at one close: the later one is what is known
            self._history[level_id][-1] = rec
        else:
            self._history[level_id].append(rec)
            self._bars[level_id].append(bar)
        return rec

    # ------------------------------------------------------------------ reads (as of a bar)
    def get(self, level_id: int, bar: int) -> Level | None:
        """The level as known at `bar`'s close; None when it did not exist yet."""
        bars = self._bars.get(level_id)
        if not bars:
            return None
        i = bisect_right(bars, int(bar))
        return self._history[level_id][i - 1] if i else None

    def as_of(self, bar: int, source: str | None = None, states: Iterable[str] | None = None,
              label: str | None = None) -> list[Level]:
        """Every level known at `bar`'s close (in the order they were added), optionally only one source / label / some
        states (the state as known at that bar)."""
        want = None if states is None else set(states)
        out: list[Level] = []
        for lid in self._history:
            lv = self.get(lid, bar)
            if lv is None:
                continue
            if (source is None or lv.source == source) and (label is None or lv.label == label) and (want is None or lv.state in want):
                out.append(lv)
        return out

    def nearest(self, bar: int, price: float, side: str | None = None, **filters: object) -> Level | None:
        """The level known at `bar` nearest to `price`; side "above" / "below": only levels whose band is above / below it."""
        if side not in (None, "above", "below"):
            raise ValueError(f"unknown side {side!r}")
        best: Level | None = None
        best_d = float("inf")
        for lv in self.as_of(bar, **filters):  # type: ignore[arg-type]
            if side == "above" and lv.low < price:
                continue
            if side == "below" and lv.high > price:
                continue
            d = 0.0 if lv.contains(price) else min(abs(lv.low - price), abs(lv.high - price))
            if d < best_d:
                best, best_d = lv, d
        return best

    def history(self, level_id: int) -> list[Level]:
        """Every record of a level, oldest first (for a chart's timeline)."""
        return list(self._history.get(level_id, []))

    def __len__(self) -> int:
        return len(self._history)


__all__ = ["Level", "LevelStore", "STATES", "ACTIVE", "TESTED", "BROKEN", "FLIPPED", "EXPIRED"]
