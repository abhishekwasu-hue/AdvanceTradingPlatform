"""U6 BK1 / BK2 / BK3 / BK4 (spec §4): pre-built breakout templates, the "Breakout" / "Breakout quality" builder block families, and the
ScreenQL text of a template.

A template is (screen, signal timeframe, parameters, direction). Its ScreenQL text is built as an AST (app/screener/nodes)
and printed canonically, so the builder, the query text and the template are one tree: the screen call (RangeBreakout,
SwingBreakout, ...) AND the higher-timeframe filter on each context timeframe (DowTrend(degree)@tf). Parity with the
engine (quality.run_breakout: signal AND htf_pass) is tested bar by bar.

Not every template has a ScreenQL text: screenql/1 has no @2h / @4h yet (open question BK1-1), and a ScreenQL call
exposes only a screen's main arguments - a template that changes another signal parameter (atr_n, the auto-base rules,
...) runs through the engine only. `screenql()` then returns None with the reason.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.screener import nodes as n
from app.screener.breakouts import params as bp
from app.screener.breakouts import style
from app.screener.breakouts.screens import SCREEN_TIMEFRAMES, SCREENS

# The parameters that change a screen's per-leg signal, and the ones its ScreenQL call takes as arguments (in order).
_COMMON = ("atr_n", "vol_avg_n", "leg_degree", "leg_rearm_bars")
SIGNAL_KEYS: Dict[str, Tuple[str, ...]] = {
    "B1": _COMMON + ("range_mode", "range_n", "min_break_atr", "vol_mult", "base_min_bars", "base_max_bars", "base_max_height_atr",
                     "base_contraction", "prior_advance_pct", "prior_advance_n"),
    "B2": _COMMON + ("swing_degree", "min_break_atr"),
    "B4": _COMMON + ("nr_setup", "nr_trigger_bars", "min_break_atr", "vol_mult"),
    "B7": _COMMON + ("high_weeks", "min_break_atr", "vol_mult"),
    "B13": _COMMON + ("expansion_k", "close_loc", "vol_mult"),
    "B14": _COMMON + ("shock_mult", "range_n", "level_tol_atr", "close_loc", "min_break_atr"),
}
# U6 BK2: the intraday screens read the session clock and time-of-day volume instead of the n-bar volume average
_INTRADAY = ("atr_n", "min_break_atr", "skip_first_min", "tod_window_end", "tod_vol_mult", "tod_sessions", "tod_min_sessions", "tod_mode",
             "vwap_agree")
SIGNAL_KEYS.update({
    "B16": _INTRADAY + ("or_minutes", "intraday_max_per_session"),
    "B17": _INTRADAY + ("day_level", "first_hour_min", "intraday_max_per_session"),
    "B18": _INTRADAY + ("leg_degree", "leg_rearm_bars"),
})
# U6 BK3: VCP and the indicator breakouts
_TT = ("tt_filter", "tt_ma_fast", "tt_ma_mid", "tt_ma_slow", "tt_slow_rising_n", "tt_year_bars", "tt_near_high_pct", "tt_above_low_pct")
SIGNAL_KEYS.update({
    "B3": _COMMON + ("vcp_mode", "vcp_min_contractions", "vcp_ratio", "vcp_pivot_atr", "vcp_max_depth_pct", "vcp_min_bars", "vcp_max_bars",
                     "vcp_vol_dryup", "vcp_ready_pct", "vol_mult", "min_break_atr") + _TT,
    "B10": _COMMON + ("donchian_n", "min_break_atr"),
    "B11": _COMMON + ("squeeze_mode", "bb_n", "bb_k", "squeeze_lookback", "squeeze_within", "kc_n", "kc_mult", "vol_mult"),
    "B12": _COMMON + ("ma_n", "ma_type", "ma_slope_n", "ma_flat_pct", "ma_consol_n", "ma_band_atr", "ma_stack", "ma_stack_fast", "ma_stack_slow",
                      "min_break_atr"),
    "B15": _COMMON + ("rsi_n", "rsi_band", "regime_n", "adx_n", "adx_low", "min_break_atr"),
})
# U6 BK4: trendline / pattern / gap / retest
_TL = ("tl_order", "tl_min_touches", "tl_min_gap", "tl_max_slope", "tl_touch_tol", "tl_max_bars")
SIGNAL_KEYS.update({
    "B5": _COMMON + ("tl_mode",) + _TL + ("min_break_atr",),
    "B6": _COMMON + ("pattern", "pole_min_atr", "pole_max_bars", "flag_min_bars", "flag_max_bars", "flag_max_retrace") + _TL
    + ("vol_mult", "min_break_atr"),
    "B8": ("atr_n", "gap_level", "gap_base_n", "gap_min_atr", "gap_hold_min"),
})
SIGNAL_KEYS["B9"] = tuple(dict.fromkeys(("retest_of", "retest_max_bars", "retest_reversal", "retest_tol_atr")
                                        + SIGNAL_KEYS["B1"] + SIGNAL_KEYS["B2"] + SIGNAL_KEYS["B10"] + SIGNAL_KEYS["B5"]))
CONTEXT_KEYS: Tuple[str, ...] = ("dow_min_bars",)   # changes the context filter, not a ScreenQL argument
CALLS: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    "B1": ("RangeBreakout", ("range_n", "min_break_atr", "vol_mult", "range_mode")),
    "B2": ("SwingBreakout", ("swing_degree", "min_break_atr")),
    "B4": ("NRBreakout", ("nr_setup", "vol_mult", "min_break_atr")),
    "B7": ("NewHighBreakout", ("high_weeks", "vol_mult", "min_break_atr")),
    "B13": ("RangeExpansion", ("expansion_k", "vol_mult")),
    "B14": ("VolumeBreakout", ("shock_mult", "range_n")),
    "B16": ("ORBreakout", ("or_minutes", "tod_vol_mult", "vwap_agree", "skip_first_min", "min_break_atr")),
    "B17": ("DayLevelBreak", ("day_level", "tod_vol_mult", "vwap_agree", "skip_first_min", "min_break_atr")),
    "B18": ("DayExtremeBreak", ("tod_vol_mult", "vwap_agree", "skip_first_min", "min_break_atr")),
    "B3": ("VCPBreakout", ("vcp_min_contractions", "vcp_ratio", "vol_mult", "min_break_atr", "tt_filter", "vcp_vol_dryup")),
    "B10": ("DonchianBreakout", ("donchian_n", "min_break_atr")),
    "B11": ("SqueezeBreakout", ("squeeze_mode", "bb_n", "squeeze_lookback", "vol_mult")),
    "B12": ("MABreakout", ("ma_n", "ma_type", "min_break_atr")),
    "B15": ("RegimeBreakout", ("rsi_band", "regime_n", "adx_low", "min_break_atr")),
    "B5": ("TrendlineBreakout", ("tl_mode", "tl_min_touches", "min_break_atr")),
    "B6": ("PatternBreakout", ("pattern", "vol_mult", "min_break_atr")),
    "B8": ("GapBreakout", ("gap_level", "gap_hold_min")),
    "B9": ("RetestEntry", ("retest_of", "retest_max_bars", "retest_reversal")),
}
# U6 BK3: the B3 Watchlist is its own ScreenQL function; volume and the break distance do not apply to a base not yet broken
WATCHLIST_CALL: Tuple[str, Tuple[str, ...]] = ("VCPReady", ("vcp_min_contractions", "vcp_ratio", "vcp_ready_pct", "tt_filter", "vcp_vol_dryup"))
_WATCHLIST_UNUSED = ("vol_mult", "min_break_atr", "leg_degree", "leg_rearm_bars")


def call_for(screen: str, p: Dict[str, Any]) -> Tuple[str, Tuple[str, ...], Tuple[str, ...]]:
    """(ScreenQL function, its argument parameters, parameters it expresses or ignores beyond them) for a screen."""
    if screen == "B3" and p.get("vcp_mode") == "watchlist":
        return WATCHLIST_CALL[0], WATCHLIST_CALL[1], ("vcp_mode",) + _WATCHLIST_UNUSED
    if screen == "B3":
        return CALLS[screen][0], CALLS[screen][1], ("vcp_mode", "vcp_ready_pct")
    return CALLS[screen][0], CALLS[screen][1], ()
# Columns a result row shows (spec §4): base metrics, breakout distance in ATR, relative volume, HTF agreement, grade.
RESULT_COLUMNS: Tuple[str, ...] = ("symbol", "side", "grade", "why", "base_len", "base_height_pct", "dist_atr", "rvol", "htf", "event",
                                   "status", "retest", "level", "stop_ref", "measured_move", "time")
# U6 BK2: the intraday screens add the session VWAP at the signal bar and its exchange-clock time
INTRADAY_COLUMNS: Tuple[str, ...] = ("vwap", "clock")


@dataclass(frozen=True)
class Template:
    id: str
    name: str
    screen: str
    signal_tf: str
    direction: str = "both"
    params: Dict[str, Any] = field(default_factory=dict)
    note: str = ""


TEMPLATES: Tuple[Template, ...] = (
    Template("swing_1h_range", "Swing 1H range breakout", "B1", "1h", note="20-bar range on 1H, context Daily + Weekly"),
    Template("daily_swing_break", "Daily swing-high / low break (BOS / CHoCH)", "B2", "1d", note="D1 swing on Daily, context Weekly"),
    Template("intraday_15m_nr7", "Intraday 15m NR7 breakout", "B4", "15m", note="NR7 setup bar, next-bar break, context 1H + Daily"),
    Template("daily_52w_high", "Daily 52-week high breakout", "B7", "1d", direction="up", note="close above the prior 52-week high on volume"),
    Template("weekly_52w_high", "Weekly 52-week high breakout", "B7", "1w", direction="up", params={"htf_filter": "off"},
             note="weekly close above the prior 52-week high; no context filter - a monthly trend needs 40 months of history (BK1-2)"),
    Template("intraday_15m_expansion", "Intraday 15m ATR expansion", "B13", "15m", note="wide-range bar >= 2 ATR with a directional close"),
    Template("intraday_5m_volume", "Intraday 5m volume breakout", "B14", "5m", note="volume shock at the 20-bar high / low"),
    # U6 BK2 (spec §4 "Intraday ORB"): stop reference = the opening-range midpoint for the "Then" template (spec B16)
    Template("intraday_5m_orb15", "Intraday ORB: 15-minute opening range on 5m", "B16", "5m", params={"or_minutes": 15, "stop_mode": "midpoint"},
             note="first close beyond the 09:15-09:30 range, VWAP side, time-of-day volume; stop = range midpoint; context 15m + 1H"),
    Template("intraday_15m_orb30", "Intraday ORB: 30-minute opening range on 15m", "B16", "15m", params={"or_minutes": 30, "stop_mode": "midpoint"},
             note="first close beyond the 09:15-09:45 range; stop = range midpoint; context 1H + Daily"),
    Template("intraday_15m_first_hour", "Intraday first-hour high / low break on 15m", "B17", "15m", params={"day_level": "first_hour", "stop_mode": "base"},
             note="first close beyond the 09:15-10:15 range; stop = the other side of the first hour"),
    Template("intraday_5m_prev_day", "Intraday previous-day high / low break on 5m", "B17", "5m", params={"day_level": "prev_day"},
             note="first close beyond yesterday's high / low after the first 15 minutes"),
    Template("intraday_5m_day_extreme", "Intraday new high / low of day on 5m", "B18", "5m",
             note="close beyond the session's high / low so far with time-of-day volume and VWAP agreement, once per leg"),
    # U6 BK3 (flag screener_breakouts_bk3 on top of screener_breakouts)
    Template("daily_vcp", "Daily volatility-contraction (VCP-style) breakout", "B3", "1d", direction="up",
             note="two or more contractions each shallower than the last, volume drying up, trend template; close above the pivot on volume"),
    Template("daily_vcp_watchlist", "Daily volatility-contraction (VCP-style) watchlist: ready, not yet broken", "B3", "1d", direction="up",
             params={"vcp_mode": "watchlist"},
             note="the same base rules, close at most 5 % under the pivot and not yet above it; listed every day it stays ready"),
    Template("daily_donchian_20", "Daily 20-day Donchian breakout", "B10", "1d", note="close beyond the previous 20 days' high / low by an ATR buffer"),
    Template("daily_donchian_55", "Daily 55-day Donchian breakout", "B10", "1d", params={"donchian_n": 55},
             note="Turtle System 2 channel: close beyond the previous 55 days' high / low by an ATR buffer"),
    Template("daily_bb_squeeze", "Daily Bollinger squeeze breakout", "B11", "1d",
             note="band width at its six-month low, then a close outside the band on volume within two weeks"),
    Template("intraday_15m_ttm_squeeze", "Intraday 15m TTM squeeze fire", "B11", "15m", params={"squeeze_mode": "ttm"},
             note="Bollinger bands leave the Keltner channel with momentum on the breakout side; context 1H + Daily"),
    Template("daily_50ma_breakout", "Daily 50-day moving-average breakout", "B12", "1d",
             note="first close beyond a flat or rising 50-day SMA after two weeks of closes around it"),
    Template("daily_rsi_adx_regime", "Daily RSI / ADX regime breakout", "B15", "1d",
             note="RSI leaves the 40-60 band it held for a month, ADX rises from below 20, price clears the month's range"),
    # U6 BK4 (flag screener_breakouts_bk4 on top of screener_breakouts)
    Template("daily_trendline_break", "Daily trendline breakout", "B5", "1d",
             note="first close beyond a falling resistance / rising support line with two or more touches, by an ATR buffer"),
    Template("swing_1h_channel_break", "Swing 1H channel breakout", "B5", "1h", params={"tl_mode": "channel"},
             note="close beyond the far edge of a rising / falling channel on 1H; measured move = the channel's width beyond the edge"),
    Template("daily_flag", "Daily flag breakout", "B6", "1d", direction="up",
             note="a fast pole of 3+ ATR, a shallow consolidation of one to three weeks, then a close above it on volume; measured move"),
    Template("daily_triangle", "Daily triangle breakout", "B6", "1d", params={"pattern": "triangle"},
             note="converging resistance and support lines, then a close beyond on volume; measured move = the triangle's height"),
    Template("intraday_5m_gap_and_go", "Intraday gap beyond the previous day's high / low on 5m", "B8", "5m",
             note="the session opens beyond yesterday's high / low and every close of the first 30 minutes holds it"),
    Template("daily_gap_base", "Daily gap beyond a 20-day base", "B8", "1d", direction="up", params={"gap_level": "base"},
             note="the day opens above the last 20 sessions' high and closes above it"),
    Template("daily_range_retest", "Daily range-breakout retest", "B9", "1d",
             note="after a 20-day range breakout, price leaves the level, comes back within two weeks and reverses there"),
    Template("swing_1h_range_retest", "Swing 1H range-breakout retest", "B9", "1h",
             note="the 1H version: a reversal at the broken 20-bar range within 10 bars; context Daily + Weekly"),
)
BK3_TEMPLATE_SCREENS: Tuple[str, ...] = ("B3", "B10", "B11", "B12", "B15")
BK4_TEMPLATE_SCREENS: Tuple[str, ...] = ("B5", "B6", "B8", "B9")
BY_ID: Dict[str, Template] = {t.id: t for t in TEMPLATES}


def _arg(value: Any) -> n.Node:
    if isinstance(value, bool):
        return n.Bool(value)
    if isinstance(value, str):
        return n.Str(value)
    return n.Num(float(value))


def screen_call(screen: str, side: str, p: Dict[str, Any]) -> n.Call:
    name, keys, _ = call_for(screen, p)
    return n.Call(name, [n.Str(side)] + [_arg(p[k]) for k in keys])


def _htf_items(side: str, context: Tuple[str, ...], p: Dict[str, Any]) -> List[n.Node]:
    if p["htf_filter"] == "off":
        return []
    with_, against = ("UP", "DOWN") if side == "up" else ("DOWN", "UP")
    items: List[n.Node] = []
    for tf in context:
        trend = n.Call("DowTrend", [n.Num(float(p["htf_degree"]))], tf=tf)
        # not_against is trend != against: a missing (unknown) trend never passes (a comparison with a missing value is
        # false), as in quality.htf_pass - so an alert never fires on a context it could not read
        items.append(n.Compare("==", trend, n.Str(with_)) if p["htf_filter"] == "agree" else n.Compare("!=", trend, n.Str(against)))
    return items


def screenql_ast(screen: str, signal_tf: str, params: Optional[Dict[str, Any]] = None) -> Tuple[Optional[n.Node], Optional[str]]:
    """(AST, None) or (None, why not). The AST is the template's whole condition, both sides joined by OR."""
    p = bp.settings(**(params or {}))
    if screen not in SCREENS:
        raise ValueError(f"unknown breakout screen {screen!r}")
    context = style.context_timeframes(signal_tf, p["context_ladder"])
    if not style.screenql_expressible(signal_tf, *context):
        return None, f"screenql/1 has no {', '.join('@' + t for t in (signal_tf, *context) if not style.screenql_expressible(t))} yet - engine only"
    _, args, expressed = call_for(screen, p)
    hidden = [k for k in SIGNAL_KEYS[screen] + CONTEXT_KEYS if k not in args and k not in expressed and p[k] != bp.DEFAULTS[k]]
    if hidden:
        return None, f"{', '.join(hidden)} changed from the default and is not a ScreenQL argument - engine only"
    sides = ("up", "down") if p["direction"] == "both" else (p["direction"],)
    branches: List[n.Node] = []
    for side in sides:
        items = [screen_call(screen, side, p), *_htf_items(side, context, p)]
        branches.append(n.Logic("ALL", items) if len(items) > 1 else items[0])
    return (n.Logic("ANY", branches) if len(branches) > 1 else branches[0]), None


def screenql(screen: str, signal_tf: str, params: Optional[Dict[str, Any]] = None) -> Tuple[Optional[str], Optional[str]]:
    ast, why = screenql_ast(screen, signal_tf, params)
    return (n.to_text(ast) if ast is not None else None), why


def template_params(t: Template, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {**t.params, "direction": t.direction, **(overrides or {})}


def _set_params(group: Tuple[str, ...]) -> set:
    """Parameters only a set's screens read (left out of the catalog while its flag is off)."""
    own = {k for code in group for k in SIGNAL_KEYS[code]}
    rest = {k for code, keys in SIGNAL_KEYS.items() if code not in group for k in keys}
    shared = {p.name for p in bp.PARAMS if p.group in ("common", "quality", "style")}      # the overlay's and the family's own
    return own - rest - shared


def _bk3_params() -> set:
    return _set_params(BK3_TEMPLATE_SCREENS)


def _hidden_screens(bk3: bool, bk4: bool) -> Tuple[str, ...]:
    return (() if bk3 else BK3_TEMPLATE_SCREENS) + (() if bk4 else BK4_TEMPLATE_SCREENS)


def blocks(bk3: bool = True, bk4: bool = True) -> Dict[str, Any]:
    """The builder's block families (spec §4): one "Breakout" block per screen with its fields, and the "Breakout quality"
    overlay block - field lists come from the parameter table, so the builder, the docs and the engine never drift.
    `bk3`: whether the U6 BK3 screens are shown (flag `screener_breakouts_bk3`)."""
    described = {d["name"]: d for d in bp.describe()}
    family = []
    hide = _hidden_screens(bk3, bk4)
    for code, name in SCREENS.items():
        if code in hide:
            continue
        fn, args = CALLS[code]
        own = [k for k in SIGNAL_KEYS[code] if k not in _COMMON]
        extra = {"watchlist_function": WATCHLIST_CALL[0], "watchlist_args": ["side", *WATCHLIST_CALL[1]]} if code == "B3" else {}
        family.append({"screen": code, "name": name, "function": fn, "args": ["side", *args], **extra,
                       "fields": [described[k] for k in dict.fromkeys(own + ["direction"])],
                       "timeframes": list(SCREEN_TIMEFRAMES.get(code, style.STYLES["intraday"] + style.STYLES["swing"]))})
    quality = [d for d in described.values() if d["group"] == "quality"]
    common = [described[k] for k in ("atr_n", "vol_avg_n", "vol_mult", "min_break_atr", "close_loc", "leg_degree", "leg_rearm_bars")]
    intraday = [d for d in described.values() if d["group"] == "intraday"]
    return {"breakout": family, "quality": {"name": "Breakout quality", "fields": quality, "grades": ["A", "B", "C"]},
            "common": common, "intraday": intraday, "result_columns": list(RESULT_COLUMNS), "intraday_columns": list(INTRADAY_COLUMNS)}


def catalog(bk3: bool = True, bk4: bool = True) -> Dict[str, Any]:
    """Everything the builder needs for the breakout family: selector, blocks, templates with their ScreenQL text. `bk3` /
    `bk4`: whether the U6 BK3 / BK4 screens, templates and parameters are included (flags `screener_breakouts_bk3` / `_bk4`)."""
    rows = []
    hide = _hidden_screens(bk3, bk4)
    for t in TEMPLATES:
        if t.screen in hide:
            continue
        text, why = screenql(t.screen, t.signal_tf, template_params(t))
        rows.append({"id": t.id, "name": t.name, "screen": t.screen, "signal_tf": t.signal_tf, "style": style.style_of(t.signal_tf),
                     "direction": t.direction, "params": dict(t.params), "note": t.note,
                     "context_tfs": list(style.context_timeframes(t.signal_tf)), "screenql": text, "engine_only_reason": why})
    hidden = (set() if bk3 else _bk3_params()) | (set() if bk4 else _set_params(BK4_TEMPLATE_SCREENS))
    return {"selector": style.selector(), "blocks": blocks(bk3, bk4), "templates": rows,
            "params": [d for d in bp.describe() if d["name"] not in hidden]}


def _register_taxonomy() -> None:
    """U9 TX1: every template is filed in the screen taxonomy (app/screener/data/taxonomy.json, OPEN_QUESTIONS TX1-6); the
    CI guard (taxonomy.coverage_problems) fails on an unfiled one. Hidden while `screener_breakouts` is off."""
    from app.screener import taxonomy
    taxonomy.register_source("breakouts", lambda: [f"breakouts.{t.id}" for t in TEMPLATES
                                                   if t.screen not in BK3_TEMPLATE_SCREENS + BK4_TEMPLATE_SCREENS],
                             flag="screener_breakouts")
    # U6 BK3: its templates are a second source, hidden while either flag is off
    taxonomy.register_source("breakouts_bk3", lambda: [f"breakouts_bk3.{t.id}" for t in TEMPLATES if t.screen in BK3_TEMPLATE_SCREENS],
                             flag="screener_breakouts", also=("screener_breakouts_bk3",))
    # U6 BK4: the third source, hidden while screener_breakouts or screener_breakouts_bk4 is off
    taxonomy.register_source("breakouts_bk4", lambda: [f"breakouts_bk4.{t.id}" for t in TEMPLATES if t.screen in BK4_TEMPLATE_SCREENS],
                             flag="screener_breakouts", also=("screener_breakouts_bk4",))


_register_taxonomy()


__all__ = ["TEMPLATES", "BY_ID", "Template", "screenql", "screenql_ast", "screen_call", "blocks", "catalog", "template_params",
           "SIGNAL_KEYS", "CALLS", "WATCHLIST_CALL", "call_for", "BK3_TEMPLATE_SCREENS", "BK4_TEMPLATE_SCREENS", "RESULT_COLUMNS", "INTRADAY_COLUMNS"]
