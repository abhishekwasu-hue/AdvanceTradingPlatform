"""M99 P2: trade-management options for the intraday templates (family H) - the user's own switches, all OFF by default.

A template only generates signals (section 5: exits are the user's settings). These options are more of the user's exit
settings on a template bot, stored with the other template exits (`TemplateExits.management`, a JSON text; NULL = none,
exactly the behaviour before this module). Every option is off until the user turns it on and every number is his:

* trail (`trail`)  - SWING: the latest confirmed swing low (high for a downward idea) of `trail_degree`;
  ATR: the best price since the entry less `trail_k` average true ranges of `trail_n` bars (the percent trail's shape);
  MA: the `trail_ma` average (EMA / SMA) of `trail_n` closes. Read on each bar close; the stop moves only when the level
  is tighter than the stop and still on the losing side of that close.
* no new extreme (`stall_bars`) - exit when this many bars in a row closed after the trade's last new high (low for a
  downward idea) without a new one.
* bars in the trade (`max_bars`) - exit after this many closed bars in the trade (a count of bars, no clock).

The existing settings stay as they are and work beside these: the break-even at an R multiple, the percent trail, the
time exits and the scale-out (a partial exit at an R multiple, M99 P7) - `app/trading/exit_rules.py`, ExitRules - the
user's own Square-off time, the template's stop and target rule. A partial exit is NOT built here a second time: a template
bot books a part through that one scale-out (M99 P2 Decisions).

A bot that reads the underlying and trades its future or a single option of it (M99 P2 follow-up): the trail level and
the bar counts are read on the bars it READS (the underlying's structure); the result is acted on the traded instrument
through the existing paths - a future's stop is the underlying stop transplanted by the entry's offset
(app/execution/contract_execution.py), an option exits when the underlying crosses its underlying stop
(app/trading/exit_logic.py check_contract_exit) - no premium stop is made up. A multi-leg structure is refused (its exits
are its own credit levels).

Rules every option keeps (ADR-0004, CLAUDE.md section 3): a stop only ever tightens, never widens; nothing here blocks,
delays or removes an exit - it only adds a tighter stop or an earlier exit; no lots, stop, target or R:R is chosen here.
Everything is read on CLOSED bars after the entry (no look-ahead) and acted on at the next price - the next bar's open in
a backtest, the price right after the bar close in the worker (the template exit's own rule), through `step`, the one
function both call (backtest = live).
"""
from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

TRAILS = ("SWING", "ATR", "MA")
AVERAGES = ("EMA", "SMA")
# The bounds of the user's numbers (form validation, not trading rules)
DEGREE_BOUNDS = (0, 3)
N_BOUNDS = (1, 500)
K_BOUNDS = (0.1, 20.0)
BARS_BOUNDS = (1, 1000)
# Reference values shown in the form ("reference - edit before use"); none is ever applied unless the user turns the
# option on and keeps it. Sources: the PR's Decisions table (M99 P2).
REFERENCE: dict[str, Any] = {"trail_degree": 0, "trail_n_atr": 22, "trail_k": 3.0, "trail_n_ma": 20, "trail_ma": "EMA", "stall_bars": 2}
REF_LABEL = "Reference - edit before use"


@dataclass(frozen=True)
class Management:
    """One bot's management options. Every field None = off (the behaviour before M99 P2)."""
    trail: str | None = None
    trail_degree: int | None = None
    trail_n: int | None = None
    trail_k: float | None = None
    trail_ma: str | None = None
    stall_bars: int | None = None
    max_bars: int | None = None

    @property
    def active(self) -> bool:
        return any(v not in (None, False) for v in asdict(self).values())

    def to_json(self) -> str | None:
        data = {k: v for k, v in asdict(self).items() if v is not None and v is not False}
        return json.dumps(data, sort_keys=True) if data else None

    @classmethod
    def from_json(cls, text: str | None) -> Management:
        """The stored text -> options; anything unreadable is "no options" (never an error on a running bot)."""
        if not text:
            return cls()
        try:
            data = json.loads(text)
        except (TypeError, ValueError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        return cls(**{k: data[k] for k in cls.__dataclass_fields__ if k in data})

    def problems(self) -> list[str]:
        """Plain messages for options that cannot be used as typed (none: they fit)."""
        out: list[str] = []

        def bad(v: Any, bounds: tuple, integer: bool = False) -> bool:
            if v is None or isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
                return True
            if integer and float(v) != int(v):
                return True
            return not bounds[0] <= float(v) <= bounds[1]

        if self.trail is not None:
            if self.trail not in TRAILS:
                out.append("Pick how the stop trails from the list")
            elif self.trail == "SWING":
                if bad(self.trail_degree, DEGREE_BOUNDS, integer=True):
                    out.append(f"Pick the swing size the stop trails ({DEGREE_BOUNDS[0]} to {DEGREE_BOUNDS[1]})")
            else:
                if bad(self.trail_n, N_BOUNDS, integer=True):
                    out.append(f"Type the bars of the trail's average ({N_BOUNDS[0]} to {N_BOUNDS[1]})")
                if self.trail == "ATR" and bad(self.trail_k, K_BOUNDS):
                    out.append(f"Type how many average true ranges the stop trails by ({K_BOUNDS[0]:g} to {K_BOUNDS[1]:g})")
                if self.trail == "MA" and self.trail_ma not in AVERAGES:
                    out.append("Pick the kind of average the stop trails")
        elif any(v is not None for v in (self.trail_degree, self.trail_n, self.trail_k, self.trail_ma)):
            out.append("The trail's numbers are for a trailing stop only")
        if self.stall_bars is not None and bad(self.stall_bars, BARS_BOUNDS, integer=True):
            out.append(f"Type the bars without a new high or low ({BARS_BOUNDS[0]} to {BARS_BOUNDS[1]})")
        if self.max_bars is not None and bad(self.max_bars, BARS_BOUNDS, integer=True):
            out.append(f"Type the bars to stay in the trade ({BARS_BOUNDS[0]} to {BARS_BOUNDS[1]})")
        return out

    def trail_texts(self) -> tuple[str, str] | None:
        """The ScreenQL level the trail reads on each bar close, as (upward idea, downward idea); None for no level series
        (no trail). ATR reads the average true range only (the best price since the entry is the trade's own)."""
        if self.trail == "SWING":
            d = int(self.trail_degree or 0)
            return f"SwingLow({d})", f"SwingHigh({d})"
        if self.trail == "ATR":
            n = int(self.trail_n or 1)
            return f"ATR({n})", f"ATR({n})"
        if self.trail == "MA":
            n, kind = int(self.trail_n or 1), str(self.trail_ma or "EMA")
            return f"{kind}(close, {n})", f"{kind}(close, {n})"
        return None

    def describe(self) -> str:
        """Plain words for the bot's summary line (empty when off)."""
        parts: list[str] = []
        if self.trail == "SWING":
            parts.append(f"trail the stop by the swings (size {int(self.trail_degree or 0)})")
        elif self.trail == "ATR":
            parts.append(f"trail the stop {self.trail_k:g} average true ranges ({int(self.trail_n or 0)} bars) from the best price")
        elif self.trail == "MA":
            parts.append(f"trail the stop at the {int(self.trail_n or 0)}-bar {self.trail_ma}")
        if self.stall_bars is not None:
            parts.append(f"out after {int(self.stall_bars)} bars without a new high or low")
        if self.max_bars is not None:
            parts.append(f"out after {int(self.max_bars)} bars in the trade")
        return ", ".join(parts)


NONE = Management()


@dataclass(frozen=True)
class Step:
    """What the options say on one bar close for one open trade. Nothing here is an order: the caller acts at the next
    price. `stop` None = unchanged."""
    stop: float | None = None
    stop_reason: str | None = None
    exit_reason: str | None = None


def _finite(v: Any) -> bool:
    try:
        return v is not None and math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def step(m: Management, *, direction: int, entry: float | None, initial_stop: float | None, current_stop: float | None,
         highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], level: float | None = None) -> Step:
    """The options on the latest closed bar of an open trade. `highs` / `lows` / `closes`: the closed bars AFTER the
    entry, oldest first (the last one is the bar just closed); `level`: the trail's series on that bar (the swing, the
    average, or the ATR for the ATR trail). `direction` +1 for an upward idea, -1 for a downward one. Pure function: the
    backtest engine, the options backtest engine and the worker call it with the same bars (backtest = live).

    Every price here is on the bars the bot READS: for a future or an option bot that is the underlying, so `current_stop`
    is the trade's stop as an underlying level and the new stop comes back as one - the caller maps it onto the traded
    instrument the one existing way (a future: the entry's transplant; a single option: its underlying stop). No stop on
    the underlying (`current_stop` None: a single option with the premium stop only) = no trail; the bar exits still run.
    `entry` / `initial_stop` are not read by the options (kept for the callers' records)."""
    n = len(closes)
    if not m.active or n == 0 or direction == 0:
        return Step()
    up = direction > 0
    last = float(closes[-1])
    candidates: list[tuple[float, str]] = []
    if m.trail is not None and _finite(level) and _finite(current_stop):
        lv = float(level)                                         # type: ignore[arg-type]
        if m.trail == "ATR":
            best = max(highs) if up else min(lows)
            lv = float(best) - direction * float(m.trail_k or 0.0) * lv
        candidates.append((lv, "trail"))
    stop, reason = (float(current_stop) if _finite(current_stop) else math.nan), None    # type: ignore[arg-type]
    for cand, why in candidates:
        # a stop only tightens, and only to a level still on the losing side of the close (never into the price)
        if (up and stop < cand < last) or (not up and last < cand < stop):
            stop, reason = cand, why
    exit_reason: str | None = None
    if m.max_bars is not None and n >= int(m.max_bars):
        exit_reason = f"Bars in the trade ({int(m.max_bars)})"
    if exit_reason is None and m.stall_bars is not None:
        ext = highs if up else lows
        last_new, best_so_far = 0, float(ext[0])
        for k in range(1, n):
            v = float(ext[k])
            if (up and v > best_so_far) or (not up and v < best_so_far):
                best_so_far, last_new = v, k
        if n - 1 - last_new >= int(m.stall_bars):
            exit_reason = f"No new {'high' if up else 'low'} in {int(m.stall_bars)} bars"
    return Step(stop=round(stop, 2) if reason else None, stop_reason=reason, exit_reason=exit_reason)


__all__ = ["Management", "Step", "step", "TRAILS", "AVERAGES", "REFERENCE", "REF_LABEL", "NONE", "DEGREE_BOUNDS", "N_BOUNDS",
           "K_BOUNDS", "BARS_BOUNDS"]
