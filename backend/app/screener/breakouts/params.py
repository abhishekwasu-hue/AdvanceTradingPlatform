"""U6 BK1: every breakout threshold as an editable parameter - a documented default and a one-line research note.

Nothing here is fitted to data. Each default is a published convention or the practitioner range named in
docs/specs/ATP_BREAKOUT_SCREENERS_ADDENDUM.md §1; the note says which, and docs/research/breakouts.md gives the sources.
Distances are multiples of the market's own noise (ATR), never fixed points.

`validate(overrides)` returns a cleaned copy plus the problems (a bad value falls back to its default); `settings()` is
the strict form that raises. The same shape as app/price_action/pa_settings.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class Param:
    name: str
    default: Any
    doc: str                                  # what it does
    note: str                                 # the one-line research note (mirrored in docs/research/breakouts.md)
    low: Optional[float] = None               # inclusive range for numbers
    high: Optional[float] = None
    choices: Tuple[Any, ...] = ()
    group: str = "common"                     # common / B1 / B2 / B4 / B7 / B13 / B14 / quality / style


# The context ladder: signal timeframe -> the two higher timeframes read for context. Daily and weekly signals have one:
# a monthly Dow trend needs dow_min_bars (40) months and the daily fetch holds about 13 (open question BK1-2).
DEFAULT_CONTEXT_LADDER: Dict[str, List[str]] = {
    "1m": ["5m", "15m"], "3m": ["15m", "1h"], "5m": ["15m", "1h"], "15m": ["1h", "1d"],
    "30m": ["2h", "1d"], "1h": ["1d", "1w"], "2h": ["1d", "1w"], "4h": ["1d", "1w"], "1d": ["1w"], "1w": ["1M"],
}

PARAMS: Tuple[Param, ...] = (
    # --- common ---------------------------------------------------------------------------------------------------------
    Param("direction", "both", "long breakouts (up), short breakdowns (down) or both", "User choice (spec §2); no default bias.",
          choices=("up", "down", "both")),
    Param("atr_n", 14, "ATR length (Wilder smoothing); ATR is read on the bar BEFORE the signal so a wide bar cannot inflate its own yardstick",
          "Wilder (1978) original ATR length; the platform-wide default.", 2, 200),
    Param("vol_avg_n", 20, "relative volume = this bar's volume / the mean of the previous n bars",
          "20 bars = about one trading month on Daily, the usual relative-volume window.", 2, 200),
    Param("vol_mult", 1.5, "breakout-bar volume >= vol_mult x average (0 = no volume condition; indices without volume need 0)",
          "Low end of the 1.5-2x practitioner range (O'Neil: 40-50% above average; Minervini / Qullamaggie: 1.5-2x).", 0.0, 20.0),
    Param("min_break_atr", 0.2, "the close must be beyond the level by at least this many ATRs (B1, B2, B4, B7, B14 'through')",
          "ATR-scaled minimum distance, not fixed points (spec §1 false-break filter); 0 = any close beyond.", 0.0, 5.0),
    Param("close_loc", 1.0 / 3.0, "a directional close sits in this fraction of the bar at its breakout end (top third for up)",
          "Top / bottom third of the bar (spec B13 wording; common 'close near the high' rule).", 0.05, 0.5),
    # --- B1 range / consolidation -----------------------------------------------------------------------------------------
    Param("range_mode", "fixed", "fixed: the N-bar range before the bar; auto: the consolidation found by walking back (base rules below)",
          "Both forms named in spec B1.", choices=("fixed", "auto"), group="B1"),
    Param("range_n", 20, "fixed range length in bars (also the reference window of B14 and of the base dry-up check)",
          "20 bars = Donchian / Turtle short channel (Faith, Way of the Turtle).", 2, 500, group="B1"),
    Param("base_min_bars", 5, "auto: a consolidation needs at least this many bars",
          "One trading week on Daily; raise for O'Neil-style bases (flat base >= 5 weeks).", 2, 500, group="B1"),
    Param("base_max_bars", 60, "auto: the walk back stops after this many bars",
          "About three months on Daily (upper end of O'Neil's base lengths); bounds the cost.", 3, 500, group="B1"),
    Param("base_max_height_atr", 3.0, "auto: the base (highest high - lowest low) is at most this many ATRs tall",
          "A box taller than ~3 ATR is a trend leg, not a consolidation (Darvas-box tightness rule of thumb).", 0.5, 50.0, group="B1"),
    Param("base_contraction", True, "auto: the base's second half has a range no larger than its first half",
          "Contracting range into the pivot (Minervini VCP).", group="B1"),
    Param("prior_advance_pct", 0.0, "optional: the close before the base is at least this % above the close prior_advance_n bars earlier (0 = off)",
          "Minervini asks for a prior advance (often 25-30%) before a VCP; off by default for a plain range break.", 0.0, 1000.0, group="B1"),
    Param("prior_advance_n", 60, "bars over which the prior advance is measured", "About three months on Daily.", 2, 500, group="B1"),
    # --- B2 swing structure -----------------------------------------------------------------------------------------------
    Param("swing_degree", 1, "causal swing degree (app/price_action/causal_swings, D0 smallest .. D3 largest) whose last confirmed extreme is broken",
          "D1 = 3 x ATR ZigZag (pa_settings): a structure swing, not bar noise.", 0, 3, group="B2"),
    # --- B4 inside bar / NR4 / NR7 ----------------------------------------------------------------------------------------
    Param("nr_setup", "nr7", "the setup bar: inside (inside the previous bar), nr4 / nr7 (narrowest range of the last 4 / 7 bars)",
          "Crabel (1990) NR4 / NR7; the inside bar is the classic mother-bar setup.", choices=("inside", "nr4", "nr7"), group="B4"),
    Param("nr_trigger_bars", 1, "the break must come within this many bars after the setup bar",
          "Crabel: the trade is the break on the next bar; widen for slower follow-through.", 1, 10, group="B4"),
    # --- B7 N-week high ---------------------------------------------------------------------------------------------------
    Param("high_weeks", 52, "the prior high (low) is the highest high (lowest low) of the previous N calendar weeks",
          "52-week high (George & Hwang 2004 52-week-high momentum; O'Neil's new-high rule).", 1, 520, group="B7"),
    Param("fresh_min_days", 35, "the old high was set at least this many CALENDAR days before the break -> 'fresh' (from a base), else 'extended'",
          "5 weeks, O'Neil's minimum flat-base length (calendar days, so Daily and Weekly read the same).", 1, 3650, group="B7"),
    # --- B13 ATR / range expansion ----------------------------------------------------------------------------------------
    Param("expansion_k", 2.0, "the bar's range is at least k x ATR", "Wide-range bar convention (Crabel range expansion; 2 x ATR).",
          0.5, 20.0, group="B13"),
    # --- B14 volume breakout ----------------------------------------------------------------------------------------------
    Param("shock_mult", 2.0, "volume >= shock_mult x average (the volume shock)", "Upper end of the 1.5-2x practitioner range: a shock, not a confirmation.",
          0.5, 50.0, group="B14"),
    Param("level_tol_atr", 0.25, "'at the level': the bar reached within this many ATRs of the prior range high (low)",
          "A quarter ATR: inside one bar's noise.", 0.0, 5.0, group="B14"),
    # --- quality overlay --------------------------------------------------------------------------------------------------
    Param("htf_degree", 0, "swing degree for the Dow trend on each context timeframe (HH+HL = up, LH+LL = down, else range)",
          "D0 (1.5 x ATR) is already a structure swing on a higher timeframe; Dow theory labels.", 0, 3, group="quality"),
    Param("dow_min_bars", 40, "a D0 Dow trend is read only after this many bars of its frame (missing before: never a guess)",
          "Measured on the session generator (40 seeds): the last-bar trend from N bars agrees with the full history 95.5 % at 30, 98.7 % at 40, 99.9 % at 60.",
          20, 500, group="quality"),
    Param("htf_filter", "not_against", "templates: agree (every context up for a long), not_against (every context known and none down) or off",
          "HTF agreement had the largest effect in the one quantitative study (spec §1); not_against keeps range-bound context, never an unknown one.",
          choices=("agree", "not_against", "off"), group="quality"),
    Param("acceptance_closes", 2, "accepted after this many consecutive closes beyond the level (the breakout bar counts)",
          "N closes beyond = acceptance (spec §1); 2 matches the price_action break rule (break_no_reclaim_bars = 1).", 1, 20, group="quality"),
    Param("retest_tol_atr", 0.25, "a retest reaches within this many ATRs of the level; a close back on the breakout side after it = held",
          "A quarter ATR: inside one bar's noise.", 0.0, 5.0, group="quality"),
    Param("failed_break_bars", 3, "a close back inside the level within this many bars after the signal = failed break (journal tag)",
          "Failed-break rule 'back inside within N bars' (spec §1); 3 bars = practitioner 'it should work at once'.", 1, 50, group="quality"),
    Param("dryup_n", 5, "base dry-up: the mean volume of the last n bars before the break ...",
          "Minervini: volume dries up into the pivot.", 1, 200, group="quality"),
    Param("dryup_ratio", 1.0, "... is at most dryup_ratio x the mean volume of the vol_avg_n bars before the break",
          "1.0 = simply lower than average; no fitted ratio.", 0.05, 5.0, group="quality"),
    Param("atr_pct_lookback", 100, "ATR percentile: ATR / close of the bar before the signal ranked within this many bars",
          "One regime window (about five months on Daily).", 10, 500, group="quality"),
    Param("atr_pct_low", 100.0 / 3.0, "percentile at or below which the regime is 'low' volatility",
          "Lower tercile; breakouts from low-volatility regimes did best in the study (spec §1).", 0.0, 100.0, group="quality"),
    Param("atr_pct_high", 200.0 / 3.0, "percentile at or above which the regime is 'high' volatility", "Upper tercile.", 0.0, 100.0, group="quality"),
    Param("stop_mode", "level_atr", "stop reference: level_atr (breakout level -/+ stop_atr_mult x ATR) or base (base low / high)",
          "Spec §5 stop forms; editable.", choices=("level_atr", "base"), group="quality"),
    Param("stop_atr_mult", 1.0, "ATRs beyond the breakout level for the level_atr stop reference", "One ATR: one bar of noise past the level.",
          0.0, 10.0, group="quality"),
    Param("room_min_r", 2.0, "room to the next opposing level must be at least this many stop distances (R)",
          "2R = conventional minimum reward:risk.", 0.0, 50.0, group="quality"),
    Param("room_swing_degree", 1, "opposing levels: confirmed swings of this degree plus the classical S/R levels (zones port)",
          "Same structure degree as B2.", 0, 3, group="quality"),
    Param("liq_n", 20, "liquidity: median traded value (close x volume) over n bars", "About one trading month on Daily.", 1, 500, group="quality"),
    Param("liq_min_value", 0.0, "liquidity floor on that median traded value, in the instrument's currency (0 = off)",
          "Off by default: the U1 universes (F&O, NIFTY 500) are liquid by construction (open question BK1-3).", 0.0, 1e15, group="quality"),
    Param("grade_a_min", 5, "grade A needs at least this many checklist points (of 6: HTF x2, volume, dry-up, regime, room)",
          "Equal-weight checklist; no fitted weights (HTF counts twice because it is two timeframes).", 0, 6, group="quality"),
    Param("grade_b_min", 3, "grade B needs at least this many points (below: C); a break against both context timeframes is C",
          "Equal-weight checklist; no fitted weights.", 0, 6, group="quality"),
    Param("leg_degree", 0, "one signal per leg: a side re-arms after a swing of this degree against it is confirmed, after a failed break ...",
          "D0 = the smallest confirmed pullback (pa_settings).", 0, 3, group="quality"),
    Param("leg_rearm_bars", 20, "... or after this many bars without a new extreme beyond the signal bar's (a new base formed)",
          "As long as the default B1 range: a base that long is a new leg.", 1, 500, group="quality"),
    # --- style / timeframes -----------------------------------------------------------------------------------------------
    Param("context_ladder", DEFAULT_CONTEXT_LADDER, "signal timeframe -> the higher timeframes read for context (closed bars only)",
          "Elder's triple screen: each screen about 4-6x the one below (spec §2 examples 15m -> 1h + 1d, 1h -> 1d + 1w).", group="style"),
)

BY_NAME: Dict[str, Param] = {p.name: p for p in PARAMS}
DEFAULTS: Dict[str, Any] = {p.name: p.default for p in PARAMS}

# Every timeframe the breakout engine knows, in minutes of session time (ScreenQL's plus 2h / 4h for the swing style).
TF_MINUTES: Dict[str, int] = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "2h": 120, "4h": 240,
                              "1d": 375, "1w": 375 * 5, "1M": 375 * 21}


def _copy(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: list(x) if isinstance(x, list) else x for k, x in v.items()}
    return list(v) if isinstance(v, list) else v


def _ladder_problems(ladder: Any) -> List[str]:
    if not isinstance(ladder, dict):
        return ["context_ladder: a mapping of signal timeframe -> context timeframes"]
    out = []
    for sig, ctx in ladder.items():
        if sig not in TF_MINUTES or not isinstance(ctx, list) or not 1 <= len(ctx) <= 2 or any(t not in TF_MINUTES for t in ctx):
            out.append(f"context_ladder[{sig!r}]: one or two known timeframes")
            continue
        steps = [TF_MINUTES[sig]] + [TF_MINUTES[t] for t in ctx]
        if any(b <= a for a, b in zip(steps, steps[1:])):
            out.append(f"context_ladder[{sig!r}]: each context timeframe must be coarser than the one before")
    return out


def validate(overrides: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], List[str]]:
    """Defaults + overrides, checked. Unknown keys are an error; a bad value falls back to its default."""
    clean = {k: _copy(v) for k, v in DEFAULTS.items()}
    errors: List[str] = []
    for k, v in (overrides or {}).items():
        spec = BY_NAME.get(k)
        if spec is None:
            errors.append(f"unknown breakout parameter {k!r}")
            continue
        d = spec.default
        try:
            if isinstance(d, bool):
                if not isinstance(v, bool):
                    raise ValueError
                clean[k] = v
            elif isinstance(d, int):
                if isinstance(v, bool) or float(v) != int(float(v)):
                    raise ValueError
                clean[k] = int(float(v))
            elif isinstance(d, float):
                if isinstance(v, bool):
                    raise ValueError
                clean[k] = float(v)
            elif isinstance(d, dict):
                problems = _ladder_problems(v)
                if problems:
                    errors.extend(p + " - default used" for p in problems)
                    continue
                clean[k] = {str(a): list(b) for a, b in v.items()}
            else:
                clean[k] = str(v)
        except (TypeError, ValueError):
            errors.append(f"{k}: invalid value {v!r} - default used")
            clean[k] = _copy(d)
            continue
        if spec.choices and clean[k] not in spec.choices:
            errors.append(f"{k}: must be one of {', '.join(map(str, spec.choices))} - default used")
            clean[k] = _copy(d)
        elif spec.low is not None and spec.high is not None and not spec.low <= clean[k] <= spec.high:   # also false for NaN
            errors.append(f"{k}: must be between {spec.low:g} and {spec.high:g} - default used")
            clean[k] = _copy(d)
    if clean["atr_pct_low"] >= clean["atr_pct_high"]:
        errors.append("atr_pct_low must be below atr_pct_high - defaults used")
        clean["atr_pct_low"], clean["atr_pct_high"] = DEFAULTS["atr_pct_low"], DEFAULTS["atr_pct_high"]
    if clean["grade_b_min"] > clean["grade_a_min"]:
        errors.append("grade_b_min must not be above grade_a_min - defaults used")
        clean["grade_a_min"], clean["grade_b_min"] = DEFAULTS["grade_a_min"], DEFAULTS["grade_b_min"]
    if clean["dryup_n"] > clean["vol_avg_n"]:
        errors.append("dryup_n must not be above vol_avg_n (the dry-up window sits inside the average) - defaults used")
        clean["dryup_n"], clean["vol_avg_n"] = DEFAULTS["dryup_n"], DEFAULTS["vol_avg_n"]
    if clean["base_min_bars"] > clean["base_max_bars"]:
        errors.append("base_min_bars must not be above base_max_bars - defaults used")
        clean["base_min_bars"], clean["base_max_bars"] = DEFAULTS["base_min_bars"], DEFAULTS["base_max_bars"]
    return clean, errors


def settings(**overrides: Any) -> Dict[str, Any]:
    """Validated parameters; raises ValueError on any problem (for code paths that must not silently fall back)."""
    clean, errors = validate(overrides)
    if errors:
        raise ValueError("; ".join(errors))
    return clean


def describe() -> List[Dict[str, Any]]:
    """The parameters as data (the builder's 'Breakout' and 'Breakout quality' block fields, the docs page)."""
    return [{"name": p.name, "default": _copy(p.default), "doc": p.doc, "note": p.note, "group": p.group,
             "min": p.low, "max": p.high, "choices": list(p.choices)} for p in PARAMS]


__all__ = ["PARAMS", "DEFAULTS", "BY_NAME", "Param", "TF_MINUTES", "DEFAULT_CONTEXT_LADDER", "validate", "settings", "describe"]
