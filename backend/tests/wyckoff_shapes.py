"""M99 P4: synthetic bar shapes for the Wyckoff reader tests - generic range shapes with our own numbers (an accumulation
with a shake under the range, its mirror, a range broken without a shake, and negatives). No source text or names."""
from __future__ import annotations

import numpy as np
import pandas as pd

WICK = 0.3


class Path:
    """Bars built leg by leg: each leg moves the close in equal steps; open = the previous close; a small wick."""

    def __init__(self, start: float = 200.0) -> None:
        self.rows: list[tuple[float, float, float, float, float]] = []
        self.last = start

    def leg(self, to: float, bars: int, vol: float | list[float], wick: float = WICK) -> Path:
        vols = vol if isinstance(vol, list) else [vol] * bars
        for k in range(bars):
            c = self.last + (to - self.last) / (bars - k)
            self.bar(self.last, c, vols[k], wick)
        return self

    def bar(self, o: float, c: float, vol: float, wick: float = WICK, high: float | None = None, low: float | None = None) -> Path:
        hi = max(o, c) + wick if high is None else high
        lo = min(o, c) - wick if low is None else low
        self.rows.append((o, hi, lo, c, vol))
        self.last = c
        return self

    def zigzag(self, lo: float, hi: float, legs: int, bars: int, vol: float) -> Path:
        for k in range(legs):
            self.leg(hi if k % 2 == 0 else lo, bars, vol)
        return self

    def frame(self, freq: str = "15min") -> pd.DataFrame:
        a = np.array(self.rows, dtype=float)
        idx = pd.date_range("2026-01-05 03:45", periods=len(a), freq=freq, tz="UTC")
        return pd.DataFrame({"open": a[:, 0], "high": a[:, 1], "low": a[:, 2], "close": a[:, 3], "volume": a[:, 4]}, index=idx)


def mirror(frame: pd.DataFrame, around: float = 400.0) -> pd.DataFrame:
    """The same shape upside down (a range after a rise from a range after a fall): prices reflected, volume kept."""
    return pd.DataFrame({"open": around - frame["open"], "high": around - frame["low"], "low": around - frame["high"],
                         "close": around - frame["close"], "volume": frame["volume"]}, index=frame.index)


def accumulation(spring: bool = True, break_up: bool = True, quiet_test: bool = True, then: str = "back_up",
                 st_volume: float = 110.0, spring_low: float = 138.5, poke: float | None = None) -> tuple[pd.DataFrame, dict[str, int]]:
    """A fall, a climax, the reaction up, a secondary test, a long middle, a shake under the bottom (or none), its quiet
    test, a move to the middle and a lighter pullback, closes over the top, then (`then`) a quiet back-up to the top and
    a rise ("back_up"), a rise with no pullback ("away"), a heavy pullback ("heavy") or a fall back inside ("fail").
    Returns the frame and the bar index of each built point."""
    p = Path(200.0)
    at: dict[str, int] = {}
    p.zigzag(196.0, 204.0, 4, 6, 100.0)                    # a quiet start (warm-up for the averages)
    p.leg(180.0, 10, 100.0).leg(184.0, 4, 90.0).leg(165.0, 10, 100.0).leg(168.0, 3, 90.0).leg(150.0, 8, 110.0)
    at["climax"] = len(p.rows)
    p.bar(150.0, 145.0, 420.0, high=150.5, low=140.0)       # the climax: a very wide down bar, very heavy volume, close off the low
    p.leg(160.0, 8, [300.0, 260.0, 220.0, 190.0, 170.0, 150.0, 130.0, 120.0])
    at["ar"] = len(p.rows) - 1
    p.leg(144.0, 8, st_volume)                              # the secondary test, lighter than the climax
    at["st"] = len(p.rows) - 1
    p.zigzag(146.0, 156.0, 6, 6, 95.0)                      # the middle of the range (Phase B), 36 bars
    p.leg(143.0, 5, 90.0)
    if spring:
        at["shake"] = len(p.rows)
        p.bar(143.0, 141.5, 80.0, high=143.3, low=spring_low)   # under the bottom and back inside on the same bar
        if poke is not None:
            at["poke"] = len(p.rows)
            p.bar(141.5, 141.8, 85.0, high=142.4, low=poke)     # a second poke under the bottom, back inside
    else:
        p.leg(145.0, 2, 90.0)
    p.leg(144.5, 2, [120.0, 110.0])
    if quiet_test:
        at["test"] = len(p.rows)
        p.bar(144.5, 144.2, 60.0, high=144.7, low=143.9)    # a quiet down bar holding over the shake's low, closing off its low
    else:
        p.bar(144.5, 144.2, 160.0, high=144.7, low=143.9)
    p.leg(156.0, 7, 150.0)                                  # the move to the middle and beyond
    at["move_top"] = len(p.rows) - 1
    p.leg(149.0, 5, 80.0)                                   # a lighter pullback that holds
    at["last_point"] = len(p.rows) - 1
    if break_up:
        p.leg(159.0, 5, 140.0)
        at["break_start"] = len(p.rows)
        p.leg(166.0, 4, [200.0, 190.0, 180.0, 170.0])       # closes over the top
        if then == "back_up":
            p.leg(163.0, 2, [150.0, 120.0])
            at["back_up"] = len(p.rows)
            p.bar(163.0, 162.0, 90.0, high=163.2, low=161.4)    # a quiet bar back toward the top, closing over it
            p.leg(176.0, 8, 130.0)
        elif then == "heavy":
            at["back_up"] = len(p.rows)
            p.bar(166.0, 162.0, 400.0, high=166.2, low=161.0)   # a wide pullback to the top on heavy volume
            p.leg(170.0, 6, 130.0)
        elif then == "fail":
            p.leg(163.0, 2, [200.0, 210.0])
            at["fail"] = len(p.rows)
            p.bar(163.0, 157.0, 160.0)                          # a close back inside the range
            p.leg(150.0, 6, 120.0)
        else:
            p.leg(180.0, 25, 130.0)
    else:
        p.zigzag(146.0, 156.0, 4, 6, 95.0)
    return p.frame(), at


def broken_without_shake() -> tuple[pd.DataFrame, dict[str, int]]:
    """A range whose lower test never breaks the bottom (no shake), then closes over the top."""
    return accumulation(spring=False, quiet_test=False, break_up=True)


def deep_break_down() -> tuple[pd.DataFrame, dict[str, int]]:
    """A range whose bottom is left: closes under it bar after bar (a sign of weakness, no return)."""
    p = Path(200.0)
    f, at = accumulation(spring=False, quiet_test=False, break_up=False)
    p.rows = [tuple(r) for r in f[["open", "high", "low", "close", "volume"]].to_numpy()]
    p.last = float(f["close"].iloc[-1])
    p.leg(144.0, 4, 100.0)
    at["break_start"] = len(p.rows)
    p.leg(130.0, 6, [180.0, 170.0, 160.0, 150.0, 140.0, 130.0])
    return p.frame(), at


def falling_market() -> pd.DataFrame:
    """A steady fall with no climax and no range (a negative)."""
    p = Path(200.0)
    p.zigzag(196.0, 204.0, 4, 6, 100.0)
    for k in range(12):
        p.leg(195.0 - 8 * k, 6, 100.0).leg(198.0 - 8 * k, 3, 90.0)
    return p.frame()
