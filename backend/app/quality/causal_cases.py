"""Z10-a (U3 G2): every shipped bar function registered in the no-look-ahead harness (app/quality/lookahead.py).

What is registered:

* indicator  - every public function of the app.indicators modules;
* operand    - every Strategy Builder indicator (`declarative.Operand`, the code the scanner, the strategies and the
  screener's Supertrend / Bollinger / VWAP / session levels share), on the base timeframe and on a higher one;
* pattern    - every candlestick pattern, as the vectorised masks and as the bar-by-bar detectors;
* swing      - the causal swing engine per degree (zigzag and fractal modes) and the fractal swings;
* structure  - market-structure events (BOS / CHoCH), the S/R engine;
* zone       - order blocks, demand / supply zones, overnight gaps, the reversal markers drawn on charts, the zone table;
* screenql   - every function of the ScreenQL registry through the runtime, on the screen's timeframe and through
  `@` higher timeframes (the close-time alignment), plus the fields.

How to add a case (the coverage test, tests/test_z10a_no_lookahead.py, fails until you do, and its message names the
exact line to add):

* a new ScreenQL registry function `Foo` that gives one value per bar: add `"Foo": ["Foo(<typical args>)"]` to
  SCREENQL_SERIES below (add an `@1h` variant when it is timeframed). Each expression must call `Foo` itself.
* a function answered for the last bar only (`timeframed=False`, not varargs - registry.LAST_BAR_ONLY): add it to
  SCREENQL_LAST_BAR, or to SCREENQL_EXEMPT with the reason when it reads no bars (option chain, reference data).
* a new function in an app.indicators module: `register("indicator.<name>_<params>", lambda f: ..., group="indicator")`
  in `_register_indicators`.
* a new Operand indicator or candlestick pattern: nothing to add by hand - `_register_operands` / `_register_patterns`
  loop over the Literal and over registry.PATTERN_NAMES (give an Operand a period in `periods` if 14 is wrong for it).
* anything else that turns bars into a signal, a level or a zone: `register(name, fn, kind=SERIES | EVENTS | LAST_BAR,
  ...)` in the matching `_register_*` - EVENTS needs `known_at` (the bar a record became known at).

Then run `python -m pytest tests/test_z10a_no_lookahead.py -k <name>`: a case must also produce values (non-vacuity).
"""
from __future__ import annotations

import typing
from functools import partial
from typing import Any, Callable, Dict, List, Optional, cast

import numpy as np
import pandas as pd

from app.quality.lookahead import EVENTS, LAST_BAR, REGISTRY, register

BASE_TF = "5m"

# ScreenQL registry functions that read no bars - nothing to look ahead into. They answer "now" (the option chain
# snapshot, reference data as of the run date, a rank across the universe on its last bars) and the validator keeps
# them out of functions that read a history (`registry.LAST_BAR_ONLY`).
SCREENQL_EXEMPT: Dict[str, str] = {
    "Rank": "cross-sectional: the universe's last bars, run through run_screen only (not a per-symbol series)",
    "PercentileRank": "cross-sectional: the universe's last bars, run through run_screen only (not a per-symbol series)",
    "RankScore": ("cross-sectional: a ranking system over the universe's last bars, run through run_screen only; each factor is a "
                  "daily ScreenQL number with its own causal cases, and tests/test_n3_s7a_rankings.py checks the rank at each date "
                  "equals a run on bars cut there (no look-ahead)"),
    "PCR": "the supplied option-chain snapshot, not bars",
    "ChainBias": "the supplied option-chain snapshot, not bars",
    "MaxPainDistancePct": "the supplied option-chain snapshot, not bars",
    "Sector": "reference data as of the run date, not bars",
    "Industry": "reference data as of the run date, not bars",
    "McapBucket": "reference data as of the run date, not bars",
    "IndexMember": "reference data as of the run date, not bars",
    "IsFnO": "reference data as of the run date, not bars",
}

# ScreenQL expressions per registry function (base timeframe 5m). Every function appears at least once; the `@`
# variants exercise the higher-timeframe alignment (a 1h / 1d value is visible only after that bar has closed).
SCREENQL_SERIES: Dict[str, List[str]] = {
    "SMA": ["SMA(close, 10)", "SMA(close, 10)@1h"],
    "EMA": ["EMA(close, 20)", "EMA(close, 5)@1d"],
    "RSI": ["RSI(14)", "RSI(14)@1h"],
    "ADX": ["ADX(14)"],
    "PlusDI": ["PlusDI(14)"],
    "MinusDI": ["MinusDI(14)"],
    "ATR": ["ATR(14)", "ATR(5)@1d"],
    "VWAP": ["VWAP()"],
    "Max": ["Max(high, 20)", "Max(high, 5)@1h"],
    "Min": ["Min(low, 20)"],
    "Greatest": ["Greatest(open, close)"],
    "Least": ["Least(open, close, low)"],
    # U9 TX2: the watch-then-trigger transition (`library.transition_ast`) - the ready condition on the bar before
    "Count": ["Count(10, close > open)", "Count(1, close > EMA(close, 20))[1]"],
    "CountStreak": ["CountStreak(close > open)"],
    "CrossAbove": ["CrossAbove(close, SMA(close, 10))"],
    "CrossBelow": ["CrossBelow(close, EMA(close, 10))"],
    "PctChange": ["PctChange(close, 5)"],
    "ZScore": ["ZScore(close, 20)"],
    "Lag": ["Lag(close, 3)"],
    "Supertrend": ["Supertrend(10, 3)", "Supertrend(10, 3)@1h"],
    "BBUpper": ["BBUpper(20, 2)"],
    "BBMid": ["BBMid(20, 2)"],
    "BBLower": ["BBLower(20, 2)"],
    "DayOpen": ["DayOpen()"],
    "PDH": ["PDH()"],
    "PDL": ["PDL()"],
    "PDC": ["PDC()"],
    "ORHigh": ["ORHigh(30)"],
    "ORLow": ["ORLow(15)"],
    "Pattern": ['Pattern("hammer")', 'Pattern("bullish_engulfing")', 'Pattern("morning_star")', 'Pattern("inside_bar")@1h'],
    "SwingHigh": ["SwingHigh(0)", "SwingHigh(1)", "SwingHigh(2)", "SwingHigh(3)"],
    "SwingLow": ["SwingLow(0)", "SwingLow(1)"],
    "SwingDirection": ["SwingDirection(0)", "SwingDirection(1)@1h"],
    "SwingZoneStrength": ['SwingZoneStrength("low", 0)', 'SwingZoneStrength("high", 1)'],
    "SwingZoneDistance": ['SwingZoneDistance("low", 0)', 'SwingZoneDistance("high", 0)'],
    "ReversalAt": ['ReversalAt(SwingLow(0), "bullish")', 'ReversalAt(SMA(close, 20), "bearish")'],
    "RealBreak": ['RealBreak(SwingHigh(0), "above", 20)', 'RealBreak(PDL(), "below")'],
    "MedianRange": ["MedianRange(20)", "MedianRange(10)@1h"],
    # U6 BK1 breakout screens (one signal per leg) and their factors; volume and distance conditions off so they fire
    "RangeBreakout": ['RangeBreakout("up", 20, 0, 0)', 'RangeBreakout("down", 10, 0.1, 0, "auto")', 'RangeBreakout("up", 10, 0, 0)@1h'],
    "SwingBreakout": ['SwingBreakout("up", 0, 0)', 'SwingBreakout("down", 0, 0)@1h'],
    "SwingBreakType": ['SwingBreakType("up", 0, 0)', 'SwingBreakType("down", 0, 0)'],
    "NRBreakout": ['NRBreakout("up", "nr7", 0, 0)', 'NRBreakout("down", "inside", 0, 0)', 'NRBreakout("up", "nr4", 0, 0)@1h'],
    "NewHighBreakout": ['NewHighBreakout("down", 1, 0, 0)'],          # Daily / Weekly in screens; daily cases below
    "RangeExpansion": ['RangeExpansion("up", 1.5, 0)', 'RangeExpansion("down", 1.5, 0)@1h'],
    "VolumeBreakout": ['VolumeBreakout("up", 1.5)', 'VolumeBreakout("down", 1.5, 10)'],
    "RelVolume": ["RelVolume(20)", "RelVolume(10)@1h"],
    "ATRPercentile": ["ATRPercentile(14, 50)", "ATRPercentile(14, 20)@1h"],
    "DowTrend": ["DowTrend(0)", "DowTrend(0)@15m"],                    # @1h: 50 bars here, too few confirmed swings to read
    # U6 BK2 intraday session functions (the session clock, VWAP from the open, time-of-day volume from prior sessions)
    "ORBreakout": ['ORBreakout("up", 15, 0, FALSE, 15, 0)', 'ORBreakout("down", 30, 0, FALSE, 0, 0)@15m'],
    "DayLevelBreak": ['DayLevelBreak("up", "prev_day", 0, FALSE, 15, 0)', 'DayLevelBreak("down", "first_hour", 0, FALSE, 0, 0)',
                      'DayLevelBreak("up", "first_candle", 0, FALSE, 0, 0)@15m'],
    "DayExtremeBreak": ['DayExtremeBreak("up", 0, FALSE, 15, 0)', 'DayExtremeBreak("down", 0, FALSE, 0, 0)@15m'],
    "RelVolumeTOD": ["RelVolumeTOD(5)", 'RelVolumeTOD(3, "cumulative")@1h'],
    "SessionVWAP": ["SessionVWAP()", "SessionVWAP()@1h"],
    # U6 BK3: VCP (Daily in screens; daily cases below) and the indicator breakouts; volume and distance conditions off
    "VCPBreakout": ['VCPBreakout("up", 2, 1, 0, 0, FALSE)', 'VCPBreakout("down", 2, 1, 0, 0, FALSE, FALSE)'],
    "VCPReady": ['VCPReady("up", 2, 1, 5, FALSE)', 'VCPReady("down", 2, 1, 10, FALSE, FALSE)'],
    "DonchianBreakout": ['DonchianBreakout("up", 20, 0)', 'DonchianBreakout("down", 55, 0.1)', 'DonchianBreakout("up", 10, 0)@1h'],
    "SqueezeBreakout": ['SqueezeBreakout("up", "bollinger", 20, 50, 0)', 'SqueezeBreakout("down", "ttm", 20, 50, 0)',
                        'SqueezeBreakout("up", "ttm", 10, 20, 0)@1h'],
    "MABreakout": ['MABreakout("up", 50, "sma", 0)', 'MABreakout("down", 20, "ema", 0)', 'MABreakout("up", 10, "sma", 0)@1h'],
    "RegimeBreakout": ['RegimeBreakout("up", 15, 10, 30, 0)', 'RegimeBreakout("down", 15, 10, 30, 0)', 'RegimeBreakout("down", 20, 10, 35, 0)@1h'],
    # U6 BK4: trendline / pattern / gap / retest breakouts; volume and distance conditions off so they fire
    "TrendlineBreakout": ['TrendlineBreakout("up", "line", 2, 0)', 'TrendlineBreakout("down", "channel", 2, 0)',
                          'TrendlineBreakout("up", "line", 2, 0)@1h'],
    "PatternBreakout": ['PatternBreakout("up", "triangle", 0, 0)', 'PatternBreakout("down", "flag", 0, 0)', 'PatternBreakout("up", "triangle", 0, 0)@1h'],
    # the synthetic 5m sessions open where the previous one closed or with a gap: only some sessions gap (down here)
    "GapBreakout": ['GapBreakout("down", "prev_day", 15)', 'GapBreakout("down", "prev_day", 5)', 'GapBreakout("down", "prev_day", 60)@15m'],
    "GapType": ['GapType("down", "prev_day", 15)', 'GapType("up", "prev_day", 30)@15m'],
    "RetestEntry": ['RetestEntry("down", "trendline", 20, FALSE)', 'RetestEntry("down", "donchian", 20, FALSE)', 'RetestEntry("up", "trendline", 20, TRUE)'],
    # Z7a-3: market zones from stored snapshots - the case builds a causal snapshot timeline from the bars (_zone_timeline)
    "NearZone": ['NearZone("demand", 0.5)', "NearZone()@1h", 'NearZone("supply", 1.0, "zones")'],
    "ZoneRole": ["ZoneRole(0.5)", "ZoneRole(1.0)@1h"],
    "ZoneState": ['ZoneState("any", 1.0)', 'ZoneState("demand", 0.8, "levels")@1h'],
    "ZoneStrength": ['ZoneStrength("any", 1.0)', 'ZoneStrength("supply", 2.0)@1h'],
    # N3 S8a: ownership and events - the case builds a causal timeline of patterns and events from the bars (_ownership_timeline)
    "Holding": ['Holding("promoter")', 'Holding("pledge")@1h', 'Holding("fii")[2]'],
    "HoldingChange": ['HoldingChange("promoter", 1)', 'HoldingChange("dii", 2)@1h'],
    "DaysToEvent": ['DaysToEvent("results")', 'DaysToEvent("any")@1h', 'DaysToEvent("ex_date") <= 5'],
}
SCREENQL_FIELDS: List[str] = ["close", "volume", "close@1h", "high@1d[1]", "low[2]"]
# A daily screen: weekly and monthly bars built from the daily ones (W-MON / month-start buckets, close-time alignment).
SCREENQL_DAILY: List[str] = ["close@1w", "SMA(close, 5)@1w", "RSI(14)@1w", "close@1M[1]", "Max(high, 3)@1M", "PDH()",
                             'NewHighBreakout("up", 4, 0, 0)', 'NewHighBreakout("down", 26, 0, 0)', "DowTrend(0)@1w",   # U6 BK1
                             'VCPBreakout("up", 2, 1, 0, 0, FALSE, FALSE)', 'VCPBreakout("down", 2, 1, 0, 0, FALSE)',   # U6 BK3
                             'VCPReady("up", 2, 1, 10, FALSE)', 'DonchianBreakout("up", 55, 0)', 'SqueezeBreakout("up", "bollinger", 20, 125, 0)',
                             'SqueezeBreakout("down", "ttm")', 'MABreakout("down", 50, "sma", 0)', 'RegimeBreakout("up", 15, 10, 30, 0)',
                             'TrendlineBreakout("down", "line", 2, 0)', 'PatternBreakout("down", "flag", 0, 0)',      # U6 BK4
                             'RetestEntry("up", "trendline", 20, FALSE)', 'RetestEntry("down", "donchian", 20, FALSE)']   # daily synthetic bars open at the last close: no gaps
# Answered for the last bar only (not series): causal by construction, kept out of history-reading functions.
SCREENQL_LAST_BAR: Dict[str, List[str]] = {
    "Trend": ["Trend(3)"],
    "StructureEvent": ["StructureEvent(3)"],
    "PatternBullish": ["PatternBullish()"],
    "PatternBearish": ["PatternBearish()"],
    "NearSupport": ["NearSupport(0.5, 3)"],
    "NearResistance": ["NearResistance(0.5, 3)"],
}


def _screenql(text: str, base_tf: str = BASE_TF):
    from app.screener import nodes
    from app.screener.parser import parse
    from app.screener.registry import FUNCTIONS
    from app.screener.runtime import SymbolData, evaluate
    ast = parse(text)
    zones = any(isinstance(node, nodes.Call) and "zones" in getattr(FUNCTIONS.get(node.name), "needs", ())
                for node in nodes.walk(ast))
    if zones:
        return lambda f: evaluate(ast, SymbolData("S", {base_tf: f}, zones=_zone_timeline(f, base_tf)), base_tf=base_tf)
    if any(isinstance(node, nodes.Call) and "ownership" in getattr(FUNCTIONS.get(node.name), "needs", ()) for node in nodes.walk(ast)):
        return lambda f: evaluate(ast, SymbolData("S", {base_tf: f}, ownership=_ownership_timeline(f, base_tf)), base_tf=base_tf)
    return lambda f: evaluate(ast, SymbolData("S", {base_tf: f}), base_tf=base_tf)


OWNERSHIP_EVERY = 30             # bars between the synthetic shareholding patterns
EVENT_EVERY = 12                 # bars between the synthetic event announcements


def _ownership_timeline(f: pd.DataFrame, tf: str) -> Any:
    """N3 S8a: a stand-in for the shareholding patterns and the event calendar - a pattern is published at the close of
    every 30th bar for the "quarter" that ended 21 days before, its stakes built ONLY from the bars up to it; every 3rd
    one also corrects the pattern before (a second row for that quarter, known from its own close). An event of a
    rotating kind is announced at the close of every 12th bar for a day 2-11 days ahead (from that bar's close price).
    Like the store, each row is known from its `known_from` on; the case proves no bar sees a row from after its close."""
    from datetime import timedelta

    from app.screener.ownership import EVENT_KINDS, Event, Ownership, Pattern
    from app.screener.runtime import bar_close
    if not len(f):
        return Ownership([], [])
    closes = pd.DatetimeIndex(bar_close(f.index, tf))
    c = f["close"].astype(float).to_numpy()
    v = f["volume"].astype(float).to_numpy()
    patterns: List[Any] = []
    events: List[Any] = []
    kinds = [k for k in EVENT_KINDS if k != "any"]
    for i in range(len(f)):
        at = pd.Timestamp(closes[i]).to_pydatetime()
        day = pd.Timestamp(closes[i]).tz_convert("Asia/Kolkata").date()
        if (i + 1) % OWNERSHIP_EVERY == 0:
            w = slice(max(0, i - OWNERSHIP_EVERY + 1), i + 1)
            promoter = 40.0 + float(c[w].mean()) % 20.0
            stakes: Dict[str, Optional[float]] = {"promoter": promoter, "pledge": float(v[w].std() % 10.0), "fii": 15.0 + float(c[i]) % 10.0,
                      "dii": 10.0 + float(v[i]) % 7.0, "public": max(0.0, 100.0 - promoter - 30.0)}
            q_end = day - timedelta(days=21)
            patterns.append(Pattern(q_end, at, stakes))
            if len(patterns) % 3 == 0 and len(patterns) >= 2:
                prev = patterns[-2]
                patterns.append(Pattern(prev.quarter_end, at, {**prev.stakes, "promoter": (prev.stakes["promoter"] or 0.0) + 0.5}))
        if (i + 1) % EVENT_EVERY == 0:
            events.append(Event(kinds[(i // EVENT_EVERY) % len(kinds)], day + timedelta(days=2 + int(c[i]) % 10), at))
    return Ownership(patterns, events)


ZONE_SNAPSHOT_EVERY = 25          # bars between the synthetic zone snapshots (plus each session's last bar)


def _zone_timeline(f: pd.DataFrame, tf: str) -> Any:
    """Z7a-3: stored zone snapshots stand-in for the zone functions - a snapshot at the close of every 25th bar and of each
    session's last bar, built ONLY from the bars up to that one (a demand band at the recent low, a supply band at the
    recent high, a support line at the close, their touches in the window). Like the stored snapshots, each is known
    from its as_of on; the case then proves the runtime never shows a bar a snapshot from after its close (the zone
    job's own causality is tested in tests/test_zones_store_job.py)."""
    from app.price_action.zones.read import Snapshot, ZoneTimeline
    from app.screener.runtime import bar_close
    if not len(f):
        return ZoneTimeline("NSE:S")
    closes = bar_close(f.index, tf)
    ist = pd.DatetimeIndex(closes).tz_convert("Asia/Kolkata")
    last_of_day = np.asarray((ist.hour * 60 + ist.minute) >= 15 * 60 + 30)   # by the clock, never by looking at the next bar
    # an empty snapshot from before the first bar: the instrument is followed, nothing is known yet (no bars read)
    times: List[int] = [pd.Timestamp(f.index[0]).value]
    snaps: List[Optional[Snapshot]] = [Snapshot(-1, pd.Timestamp(f.index[0]).to_pydatetime(), "platform", None, [])]
    lo_, hi_, c_ = (f[k].astype(float).to_numpy() for k in ("low", "high", "close"))
    for i in range(len(f)):
        if not (last_of_day[i] or (i + 1) % ZONE_SNAPSHOT_EVERY == 0):
            continue
        w = slice(max(0, i - ZONE_SNAPSHOT_EVERY + 1), i + 1)
        low, high, close = float(lo_[w].min()), float(hi_[w].max()), float(c_[i])
        demand = (low, low * 1.0015)
        supply = (high * 0.9985, high)
        touches_d = int((lo_[w] <= demand[1]).sum()) - 1
        touches_s = int((hi_[w] >= supply[0]).sum()) - 1
        rows: List[Dict[str, Any]] = [{"zone_key": f"d{i}", "zone_type": "DEMAND_ZONE", "timeframe": tf, "zone_low": demand[0], "zone_high": demand[1],
                 "state": "tested" if touches_d > 0 else "fresh", "touches": max(0, touches_d), "strength": None, "remembered": False},
                {"zone_key": f"s{i}", "zone_type": "SUPPLY_ZONE", "timeframe": tf, "zone_low": supply[0], "zone_high": supply[1],
                 "state": "flipped" if i % 2 else "fresh", "touches": max(0, touches_s), "strength": None, "remembered": False},
                {"zone_key": f"l{i}", "zone_type": "SUPPORT", "timeframe": tf, "zone_low": close, "zone_high": close,
                 "state": "fresh", "touches": 0, "strength": 3, "remembered": i % 3 == 0}]
        times.append(pd.Timestamp(closes[i]).value)
        snaps.append(Snapshot(i, pd.Timestamp(closes[i]).to_pydatetime(), "platform", close, rows))
    return ZoneTimeline("NSE:S", np.array(times, dtype="int64"), snaps)


def _zone_frame(f: pd.DataFrame) -> pd.DataFrame:
    """The zone detectors' input shape: a `timestamp` column (exchange time) and a plain row index."""
    out = f.reset_index(names="timestamp")
    out["timestamp"] = out["timestamp"].dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    return out


def _swing_frame(f: pd.DataFrame) -> pd.DataFrame:
    frame = pd.DataFrame({k: f[k].astype(float).to_numpy() for k in ("open", "high", "low", "close")})
    frame["timestamp"] = frame["bar_end"] = f.index
    return frame


def _operand_series(op: Any, f: pd.DataFrame) -> pd.Series:
    return op.series(f, BASE_TF)


def _pattern_mask(name: str, f: pd.DataFrame) -> np.ndarray:
    from app.price_action.candlestick_patterns import pattern_masks
    return pattern_masks(f)[name]


def _degree_pivots(degree: int, s: Dict[str, Any], f: pd.DataFrame) -> List[Any]:
    from app.price_action.causal_swings import degree_pivots
    return degree_pivots(_swing_frame(f), degree, s)


def _on_zone_frame(fn: Callable[[pd.DataFrame], List[Dict[str, Any]]], f: pd.DataFrame) -> List[Dict[str, Any]]:
    return fn(_zone_frame(f))


def _register_indicators() -> None:
    from app.indicators import directional, momentum, trend, volatility, vwap
    close = lambda f: f["close"]                                                         # noqa: E731
    for name, fn in {
        "sma_10": lambda f: trend.sma(close(f), 10),
        "ema_20": lambda f: trend.ema(close(f), 20),
        "macd_12_26_9": lambda f: trend.macd(close(f)),
        "rsi_14": lambda f: momentum.rsi(close(f), 14),
        "true_range": lambda f: volatility.true_range(f),
        "atr_14": lambda f: volatility.atr(f, 14),
        "supertrend_10_3": lambda f: volatility.supertrend(f, 10, 3.0),
        "bollinger_20_2": lambda f: volatility.bollinger(close(f), 20, 2.0),
        "adx_14": lambda f: directional.adx(f, 14),
        "session_vwap": lambda f: vwap.session_vwap(f),
    }.items():
        register(f"indicator.{name}", fn, group="indicator")
    for name, fn in {
        "rsi_14": lambda f: momentum.rsi(close(f), 14),
        "supertrend_10_3": lambda f: volatility.supertrend(f, 10, 3.0),
        "adx_14": lambda f: directional.adx(f, 14),
    }.items():
        register(f"indicator.daily.{name}", fn, group="indicator", bars="1d")


def operand_indicators() -> List[str]:
    from app.strategy_engine.declarative import IndicatorName
    return list(typing.get_args(IndicatorName))


def _register_operands() -> None:
    from app.strategy_engine.declarative import Operand
    periods = {"OR_HIGH": 30, "OR_LOW": 30, "SUPERTREND": 10, "BB_UPPER": 20, "BB_MID": 20, "BB_LOWER": 20}
    for name in operand_indicators():
        for tf in (None, "60m"):
            op = Operand(type="indicator", indicator=cast(Any, name), period=periods.get(name, 14),
                         multiplier=2.0 if name.startswith("BB_") else 3.0, timeframe=tf)
            register(f"operand.{name}{'@' + tf if tf else ''}", partial(_operand_series, op), group="operand")


def _register_patterns() -> None:
    from app.price_action import candlestick_patterns as cp
    from app.screener.registry import PATTERN_NAMES
    for name in PATTERN_NAMES:
        register(f"pattern.{name}", partial(_pattern_mask, name), group="pattern")

    def detected(f: pd.DataFrame) -> List[Any]:
        pos = {ts: i for i, ts in enumerate(f.index)}
        return [(pos[m.timestamp], m.pattern, m.direction, m.confidence) for m in cp.detect_patterns(f)]
    register("pattern.detect_patterns", detected, kind=EVENTS, group="pattern", known_at=lambda r: r[0], samples=12, length=200)


def _register_swings() -> None:
    from app.price_action import pa_settings
    from app.price_action.swings import find_swings
    for mode, degrees in (("atr", (0, 1, 2, 3)), ("fractal", (0, 1))):
        s = pa_settings.settings(swing_mode=mode) if mode != "atr" else pa_settings.settings()
        for d in degrees:
            register(f"swing.{mode}.degree_{d}", partial(_degree_pivots, d, s), kind=EVENTS, group="swing",
                     known_at=lambda p: int(p.confirmed_idx), key=lambda p: (p.kind, p.bar_idx, p.confirmed_idx, p.price), samples=40)
    for window in (3, 5):
        def fractal(f: pd.DataFrame, window: int = window) -> List[Any]:
            pos = {ts: i for i, ts in enumerate(f.index)}
            return [(pos[s.timestamp] + window, pos[s.timestamp], s.kind, s.price) for s in find_swings(f, window)]
        register(f"swing.fractal_window_{window}", fractal, kind=EVENTS, group="swing", known_at=lambda r: r[0], samples=30, length=300)


def _register_structure() -> None:
    from app.price_action.market_structure import analyze_market_structure
    from app.support_resistance.engine import SupportResistanceEngine
    for window in (3, 5):
        def events(f: pd.DataFrame, window: int = window) -> List[Any]:
            pos = {ts: i for i, ts in enumerate(f.index)}
            return [(pos[e.timestamp], e.event, e.direction, e.level) for e in analyze_market_structure(f, window).events]
        register(f"structure.events_window_{window}", events, kind=EVENTS, group="structure", known_at=lambda r: r[0], samples=30, length=300)
    register("structure.trend", lambda f: analyze_market_structure(f, 3).trend.value, kind=LAST_BAR, group="structure", samples=6)
    register("structure.sr_engine_zones", lambda f: [z.model_dump() for z in SupportResistanceEngine().build_zones(f, "5m")],
             kind=LAST_BAR, group="structure", samples=3)


def _register_zones() -> None:
    from app.price_action import reversal as rv
    from app.price_action.zones import compute, detect
    key = lambda z: (z["zone_type"], z["zone_low"], z["zone_high"], z["formed_bar"], z["confirmed_bar"])   # noqa: E731
    for name, fn in {"order_blocks": detect.detect_order_blocks, "demand_supply": detect.detect_demand_supply_zones,
                     "overnight_gaps": detect.find_overnight_gaps}.items():
        register(f"zone.{name}", partial(_on_zone_frame, fn), kind=EVENTS, group="zone", bars="15m",
                 known_at=lambda z: int(z["confirmed_bar"]), key=key, samples=40)

    def markers(f: pd.DataFrame) -> List[Any]:
        head = f.iloc[:60]                                     # levels known from the first 60 bars (in every prefix checked)
        levels = [float(head["high"].max()), float(head["low"].min()), float(head["close"].median())]
        return rv.scan_markers(f, levels)
    register("zone.reversal_markers", markers, kind=EVENTS, group="zone", min_t=70, samples=10, length=240,
             known_at=lambda m: int(m["index"]), key=lambda m: (m["index"], m["level"], m["bullish"], m["score_pct"], m["label"]))
    register("zone.compute_all_zones", lambda f: compute.compute_all_zones({"15m": _zone_frame(f)}, {"15m": _zone_frame(f)}),
             kind=LAST_BAR, group="zone", bars="15m", samples=4)


def _register_screenql() -> None:
    for exprs in SCREENQL_SERIES.values():
        for text in exprs:
            heavy = any(k in text for k in ("SwingZone", "ReversalAt", "RealBreak", "Breakout", "BreakType", "VCPReady", "Gap", "Retest"))
            register(f"screenql.{text}", _screenql(text), group="screenql", samples=4 if heavy else 8)
    for text in SCREENQL_FIELDS:
        register(f"screenql.{text}", _screenql(text), group="screenql")
    for text in SCREENQL_DAILY:
        register(f"screenql.daily.{text}", _screenql(text, "1d"), group="screenql", bars="1d")
    for exprs in SCREENQL_LAST_BAR.values():
        for text in exprs:
            register(f"screenql.{text}", _screenql(text), kind=LAST_BAR, group="screenql", samples=3)


def register_all() -> None:
    """Registers every case once (idempotent: the test session and a CLI may both call it)."""
    if any(name.startswith("indicator.") for name in REGISTRY):
        return
    _register_indicators()
    _register_operands()
    _register_patterns()
    _register_swings()
    _register_structure()
    _register_zones()
    _register_screenql()


def indicator_functions() -> List[str]:
    """Public functions defined in the app.indicators modules (not only `__all__`: macd, bollinger and true_range are
    shipped through their modules)."""
    import inspect

    from app.indicators import directional, momentum, trend, volatility, vwap
    return sorted(f"{mod.__name__.rsplit('.', 1)[-1]}.{name}" for mod in (trend, momentum, volatility, directional, vwap)
                  for name, fn in inspect.getmembers(mod, inspect.isfunction) if fn.__module__ == mod.__name__ and not name.startswith("_"))


def screenql_case_mismatches() -> List[str]:
    """SCREENQL_SERIES / SCREENQL_LAST_BAR expressions that do not call the function they are listed under."""
    from app.screener import nodes
    from app.screener.parser import parse
    out: List[str] = []
    for table, cases in (("SCREENQL_SERIES", SCREENQL_SERIES), ("SCREENQL_LAST_BAR", SCREENQL_LAST_BAR)):
        for name, exprs in cases.items():
            for text in exprs:
                if not any(isinstance(node, nodes.Call) and node.name == name for node in nodes.walk(parse(text))):
                    out.append(f"{table}[{name!r}] has {text!r}, which does not call {name}")
    return out


def coverage_gaps() -> List[str]:
    """Shipped functions with no case and no exemption, each with the exact thing to add (the coverage test wants this
    empty)."""
    from app.screener.registry import FUNCTIONS, LAST_BAR_ONLY, PATTERN_NAMES
    register_all()
    gaps: List[str] = []
    covered_sql = set(SCREENQL_SERIES) | set(SCREENQL_LAST_BAR) | set(SCREENQL_EXEMPT)
    for name in FUNCTIONS:
        if name in covered_sql:
            continue
        if name in LAST_BAR_ONLY:
            gaps.append(f"ScreenQL {name} (last bar only): add SCREENQL_LAST_BAR[{name!r}] = [\"{name}(...)\"] in "
                        f"app/quality/causal_cases.py, or SCREENQL_EXEMPT[{name!r}] = \"<why it reads no bars>\"")
        else:
            gaps.append(f"ScreenQL {name}: add SCREENQL_SERIES[{name!r}] = [\"{name}(...)\"] in app/quality/causal_cases.py "
                        f"(an expression that calls {name}; add an @1h variant if it is timeframed)")
    gaps += screenql_case_mismatches()
    gaps += [f"Operand {name}: no case operand.{name} (_register_operands loops over IndicatorName - is it imported?)"
             for name in operand_indicators() if f"operand.{name}" not in REGISTRY]
    gaps += [f"pattern {name}: no case pattern.{name} (_register_patterns loops over registry.PATTERN_NAMES)"
             for name in PATTERN_NAMES if f"pattern.{name}" not in REGISTRY]
    for qual in indicator_functions():
        name = qual.split(".", 1)[1]
        if not any(n.startswith(f"indicator.{name}") for n in REGISTRY):
            gaps.append(f"indicator app.indicators.{qual}: add register(\"indicator.{name}_<params>\", lambda f: ..., "
                        f"group=\"indicator\") in _register_indicators (app/quality/causal_cases.py)")
    return gaps


def last_bar_answers(case_name: str, prefixes: int = 12) -> List[Any]:
    """A last-bar case's answers on `prefixes` growing prefixes of its data set (non-vacuity: they must not all be
    missing, and a filter must say yes somewhere)."""
    from app.quality.lookahead import _DEFAULT_LENGTH, synthetic_bars
    case = REGISTRY[case_name]
    bars = synthetic_bars(case.length or _DEFAULT_LENGTH.get(case.bars, 600), tf=case.bars, seed=7)
    return [case.fn(bars.iloc[:t + 1]) for t in np.linspace(case.min_t + 40, len(bars) - 1, num=prefixes).astype(int)]


def sample_values(case_name: str) -> int:
    """How many non-missing values a series case produced on its data set (a test that never sees a value proves
    nothing: the coverage test asserts each series case is not empty)."""
    from app.quality.lookahead import _DEFAULT_LENGTH, _parts, _values, synthetic_bars
    case = REGISTRY[case_name]
    out = case.fn(synthetic_bars(case.length or _DEFAULT_LENGTH.get(case.bars, 600), tf=case.bars, seed=7))
    total = 0
    for _, part in _parts(out):
        v = _values(part)
        if v.ndim == 0:
            continue
        if v.dtype == bool:
            total += int(v.sum())
        else:
            total += int(sum(1 for x in v if x is not None and not (isinstance(x, (float, np.floating)) and np.isnan(x))))
    return total


__all__ = ["register_all", "coverage_gaps", "sample_values", "last_bar_answers", "indicator_functions", "screenql_case_mismatches", "SCREENQL_EXEMPT", "SCREENQL_SERIES", "SCREENQL_LAST_BAR", "operand_indicators"]
