"""U6 BK1 / BK2 (spec §4): pre-built breakout templates, the "Breakout" / "Breakout quality" builder block families, and the
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
}
# Columns a result row shows (spec §4): base metrics, breakout distance in ATR, relative volume, HTF agreement, grade.
RESULT_COLUMNS: Tuple[str, ...] = ("symbol", "side", "grade", "why", "base_len", "base_height_pct", "dist_atr", "rvol", "htf", "event",
                                   "status", "retest", "level", "stop_ref", "time")
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
    Template("daily_swing_break", "Daily swing-high / low break (BOS / CHoCH)", "B2", "1d", note="D1 swing on Daily, context Weekly + Monthly"),
    Template("intraday_15m_nr7", "Intraday 15m NR7 breakout", "B4", "15m", note="NR7 setup bar, next-bar break, context 1H + Daily"),
    Template("daily_52w_high", "Daily 52-week high breakout", "B7", "1d", direction="up", note="close above the prior 52-week high on volume"),
    Template("weekly_52w_high", "Weekly 52-week high breakout", "B7", "1w", direction="up", note="weekly close above the prior 52-week high"),
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
)
BY_ID: Dict[str, Template] = {t.id: t for t in TEMPLATES}


def _arg(value: Any) -> n.Node:
    if isinstance(value, bool):
        return n.Bool(value)
    if isinstance(value, str):
        return n.Str(value)
    return n.Num(float(value))


def screen_call(screen: str, side: str, p: Dict[str, Any]) -> n.Call:
    name, keys = CALLS[screen]
    return n.Call(name, [n.Str(side)] + [_arg(p[k]) for k in keys])


def _htf_items(side: str, context: Tuple[str, ...], p: Dict[str, Any]) -> List[n.Node]:
    if p["htf_filter"] == "off":
        return []
    with_, against = ("UP", "DOWN") if side == "up" else ("DOWN", "UP")
    items: List[n.Node] = []
    for tf in context:
        trend = n.Call("DowTrend", [n.Num(float(p["htf_degree"]))], tf=tf)
        # not_against is NOT (trend == against): a missing (unknown) trend passes, as in quality.htf_pass
        items.append(n.Compare("==", trend, n.Str(with_)) if p["htf_filter"] == "agree" else n.Not(n.Compare("==", trend, n.Str(against))))
    return items


def screenql_ast(screen: str, signal_tf: str, params: Optional[Dict[str, Any]] = None) -> Tuple[Optional[n.Node], Optional[str]]:
    """(AST, None) or (None, why not). The AST is the template's whole condition, both sides joined by OR."""
    p = bp.settings(**(params or {}))
    if screen not in SCREENS:
        raise ValueError(f"unknown breakout screen {screen!r}")
    context = style.context_timeframes(signal_tf, p["context_ladder"])
    if not style.screenql_expressible(signal_tf, *context):
        return None, f"screenql/1 has no {', '.join('@' + t for t in (signal_tf, *context) if not style.screenql_expressible(t))} yet - engine only"
    _, args = CALLS[screen]
    hidden = [k for k in SIGNAL_KEYS[screen] if k not in args and p[k] != bp.DEFAULTS[k]]
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


def blocks() -> Dict[str, Any]:
    """The builder's block families (spec §4): one "Breakout" block per screen with its fields, and the "Breakout quality"
    overlay block - field lists come from the parameter table, so the builder, the docs and the engine never drift."""
    described = {d["name"]: d for d in bp.describe()}
    family = []
    for code, name in SCREENS.items():
        fn, args = CALLS[code]
        own = [k for k in SIGNAL_KEYS[code] if k not in _COMMON]
        family.append({"screen": code, "name": name, "function": fn, "args": ["side", *args],
                       "fields": [described[k] for k in dict.fromkeys(own + ["direction"])],
                       "timeframes": list(SCREEN_TIMEFRAMES.get(code, style.STYLES["intraday"] + style.STYLES["swing"]))})
    quality = [d for d in described.values() if d["group"] == "quality"]
    common = [described[k] for k in ("atr_n", "vol_avg_n", "vol_mult", "min_break_atr", "close_loc", "leg_degree", "leg_rearm_bars")]
    intraday = [d for d in described.values() if d["group"] == "intraday"]
    return {"breakout": family, "quality": {"name": "Breakout quality", "fields": quality, "grades": ["A", "B", "C"]},
            "common": common, "intraday": intraday, "result_columns": list(RESULT_COLUMNS), "intraday_columns": list(INTRADAY_COLUMNS)}


def catalog() -> Dict[str, Any]:
    """Everything the builder needs for the breakout family: selector, blocks, templates with their ScreenQL text."""
    rows = []
    for t in TEMPLATES:
        text, why = screenql(t.screen, t.signal_tf, template_params(t))
        rows.append({"id": t.id, "name": t.name, "screen": t.screen, "signal_tf": t.signal_tf, "style": style.style_of(t.signal_tf),
                     "direction": t.direction, "params": dict(t.params), "note": t.note,
                     "context_tfs": list(style.context_timeframes(t.signal_tf)), "screenql": text, "engine_only_reason": why})
    return {"selector": style.selector(), "blocks": blocks(), "templates": rows, "params": bp.describe()}


def _register_taxonomy() -> None:
    """U9 TX1: every template is filed in the screen taxonomy (app/screener/data/taxonomy.json, OPEN_QUESTIONS TX1-6); the
    CI guard (taxonomy.coverage_problems) fails on an unfiled one. Hidden while `screener_breakouts` is off."""
    from app.screener import taxonomy
    taxonomy.register_source("breakouts", lambda: [f"breakouts.{t.id}" for t in TEMPLATES], flag="screener_breakouts")


_register_taxonomy()


__all__ = ["TEMPLATES", "BY_ID", "Template", "screenql", "screenql_ast", "screen_call", "blocks", "catalog", "template_params",
           "SIGNAL_KEYS", "CALLS", "RESULT_COLUMNS", "INTRADAY_COLUMNS"]
