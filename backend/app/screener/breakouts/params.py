"""U6 BK1 / BK2 / BK3 / BK4: every breakout threshold as an editable parameter - a documented default and a one-line research note.

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
    group: str = "common"                     # common / B1 .. B17 (the screen's own) /
    #                                           intraday / quality / style


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
    Param("stop_mode", "level_atr", "stop reference: level_atr (breakout level -/+ stop_atr_mult x ATR), base (base low / high; B16: the "
          "opposite side of the opening range) or midpoint (the middle of the base; B16: the opening-range midpoint)",
          "Spec §5 stop forms; editable.", choices=("level_atr", "base", "midpoint"), group="quality"),
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
    # --- intraday (U6 BK2: B16 / B17 / B18) -------------------------------------------------------------------------------
    Param("skip_first_min", 15, "intraday: no signal from a bar that starts in the first N minutes of the session (B16 also waits for its range)",
          "Spec §1 'avoid the first minutes'; 15 minutes = the NSE opening-noise window (09:15-09:30) the platform's ORB strategy also waits out.",
          0, 240, group="intraday"),
    Param("tod_window_end", "calendar", "intraday: calendar = the signal bar must close by the exchange's no-new-entries time (NSE 15:00, from "
          "app/market_data/calendar.py; a special session: its close); close = any bar up to the session close",
          "The platform's own intraday cut-off (no new entries after 15:00 on NSE) is the time-of-day window of the spec's quality overlay.",
          choices=("calendar", "close"), group="intraday"),
    Param("tod_sessions", 20, "relative volume by time of day: the average is over the same slot (minutes from the open) in the previous N sessions",
          "20 sessions = about one trading month, as vol_avg_n; the same-time average removes the U-shaped intraday volume curve (Jain & Joh 1988).",
          2, 120, group="intraday"),
    Param("tod_min_sessions", 5, "the time-of-day average needs at least this many prior sessions with the slot (before that: no value, no match)",
          "One trading week of history before the average is trusted.", 1, 120, group="intraday"),
    Param("tod_mode", "bar", "bar: this bar's volume / the slot average; cumulative: the session's volume so far / the average volume by the same time",
          "Both forms are in use: bar confirms the break bar (like vol_mult); cumulative is the scanners' 'relative volume at time of day'.",
          choices=("bar", "cumulative"), group="intraday"),
    Param("tod_vol_mult", 1.5, "intraday screens: time-of-day relative volume >= this on the signal bar (0 = no volume condition; indices need 0)",
          "Low end of the 1.5-2x practitioner range, as vol_mult, measured against the same time of day.", 0.0, 20.0, group="intraday"),
    Param("vwap_agree", True, "intraday screens: the close is on the breakout side of the session VWAP (above for up, below for down); "
          "VWAP needs volume, so indices need False",
          "Spec B16 / B18: VWAP on the breakout side; VWAP is the intraday execution benchmark (Berkowitz, Logue & Noser 1988).", group="intraday"),
    Param("intraday_max_per_session", 1, "B16 / B17: at most this many signals per side per session; a new signal needs a close back inside "
          "the level and a new close beyond (a new leg)",
          "The platform's ORB strategy takes only the first close beyond the range; a re-cross is often the whipsaw the failed-break rule warns of.",
          1, 20, group="intraday"),
    Param("or_minutes", 15, "B16: the opening range is the high / low of the session's first N minutes (a whole number of signal bars)",
          "Crabel (1990) opening range; 15 / 30 / 60 minutes are the windows named in spec B16 (15 = the platform's ORB strategy).",
          1, 240, group="B16"),
    Param("day_level", "first_hour", "B17: first_hour (high / low of the first first_hour_min minutes), prev_day (the previous regular "
          "session's high / low), first_candle (the session's first signal bar), premarket (pre-open bars, where the data has them)",
          "The B17 levels named in the spec.", choices=("first_hour", "prev_day", "first_candle", "premarket"), group="B17"),
    Param("first_hour_min", 60, "B17 first_hour: the minutes from the open that make the 'first hour' (a whole number of signal bars)",
          "The first trading hour (09:15-10:15 on NSE) by name.", 1, 240, group="B17"),
    # --- B3 VCP (U6 BK3, flag screener_breakouts_bk3) ----------------------------------------------------------------------
    Param("vcp_mode", "breakout", "breakout: the close above the pivot (the last contraction's high); watchlist: bases that are ready "
          "(every rule holds, the close within vcp_ready_pct below the pivot) but not yet broken - listed on every bar they are ready",
          "Spec B3 names both: the pivot breakout and a 'Watchlist' of bases ready but not yet broken.",
          choices=("breakout", "watchlist"), group="B3"),
    Param("vcp_min_contractions", 2, "the base needs at least this many successive contractions (swing high to the next swing low)",
          "Spec B3: at least 2 successive contractions; Minervini's examples run from 2 to 6 ('T' count).", 2, 8, group="B3"),
    Param("vcp_ratio", 1.0, "each contraction is shallower than vcp_ratio x the one before it (depth = high-to-low drop in %)",
          "Spec B3 wording 'each smaller than the previous' (1.0 = simply smaller); Minervini's examples roughly halve (0.5).",
          0.1, 1.0, group="B3"),
    Param("vcp_pivot_atr", 1.0, "a swing in the base needs a reversal of at least this many ATRs (the base's own causal zigzag; "
          "ATR of the bars before the reversal bar); contraction depths are then measured in %",
          "One ATR: a swing must clear one bar's noise; a VCP's final contraction is often only a few percent, about one daily ATR.",
          0.25, 10.0, group="B3"),
    Param("vcp_max_depth_pct", 35.0, "the first (deepest) contraction is at most this % deep",
          "Minervini: a correction much deeper than about 35 % (in a normal market) is too deep for a sound base.", 5.0, 80.0, group="B3"),
    Param("vcp_min_bars", 15, "the base, from the first contraction's high to the bar, is at least this many bars long",
          "Three weeks on Daily, the shortest VCP base in Minervini's range (3 to 65 weeks).", 3, 500, group="B3"),
    Param("vcp_max_bars", 125, "the first contraction's high is at most this many bars before the bar",
          "About six months on Daily; bounds the walk (raise it for the longer bases in Minervini's 3-65 week range).", 10, 500, group="B3"),
    Param("vcp_vol_dryup", True, "volume declines through the base: the last contraction's mean volume is below the first's "
          "(indices without volume need False)",
          "Minervini: volume contracts with the price contractions and dries up in the last one.", group="B3"),
    Param("vcp_ready_pct", 5.0, "watchlist: a ready base closes at most this % below the pivot",
          "The pivot level sits at the top of the last contraction; within about 5 % of it the base is 'ready'.", 0.1, 50.0, group="B3"),
    Param("tt_filter", True, "B3 trend template (prerequisite): close above the fast, mid and slow SMAs in that order (fast > mid > slow), "
          "the slow SMA rising, the close near the year's high and well above the year's low",
          "Minervini's trend template (2013), without the RS rating (open question BK3-1).", group="B3"),
    Param("tt_ma_fast", 50, "trend template: the fast SMA length", "Minervini's trend template: the 50-day line.", 2, 500, group="B3"),
    Param("tt_ma_mid", 150, "trend template: the middle SMA length", "Minervini's trend template: the 150-day line.", 2, 500, group="B3"),
    Param("tt_ma_slow", 200, "trend template: the slow SMA length", "Minervini's trend template: the 200-day line.", 2, 500, group="B3"),
    Param("tt_slow_rising_n", 21, "trend template: the slow SMA is above its value this many bars ago (rising)",
          "Minervini: the 200-day line rising for at least one month (about 21 sessions).", 1, 250, group="B3"),
    Param("tt_year_bars", 250, "trend template: the year's high / low = the highest high / lowest low of the last n bars (this bar included)",
          "About 52 weeks of NSE sessions.", 20, 500, group="B3"),
    Param("tt_near_high_pct", 25.0, "trend template: the close is within this % below the year's high",
          "Minervini's trend template: within 25 % of the 52-week high.", 0.0, 100.0, group="B3"),
    Param("tt_above_low_pct", 30.0, "trend template: the close is at least this % above the year's low",
          "Minervini's trend template: at least 30 % above the 52-week low.", 0.0, 1000.0, group="B3"),
    # --- B10 Donchian channel (U6 BK3) ------------------------------------------------------------------------------------
    Param("donchian_n", 20, "the channel: the highest high / lowest low of the previous n bars (this bar excluded); the ATR filter is "
          "min_break_atr", "Donchian / Turtle System 1 = 20 bars, System 2 = 55 (Faith, Way of the Turtle).", 2, 500, group="B10"),
    # --- B11 Bollinger squeeze -> expansion (U6 BK3) ----------------------------------------------------------------------
    Param("squeeze_mode", "bollinger", "bollinger: band width at its lowest of squeeze_lookback bars, then a close outside the band on "
          "volume (vol_mult); ttm: the squeeze is the Bollinger bands inside the Keltner channel, the signal its release with the momentum "
          "on the breakout side", "Both forms named in spec B11: Bollinger's band-width squeeze and the TTM squeeze (Carter, Mastering the Trade, 2005).",
          choices=("bollinger", "ttm"), group="B11"),
    Param("bb_n", 20, "Bollinger bands: SMA length (also the TTM momentum length)", "Bollinger's default: 20-period bands (Bollinger on Bollinger Bands, 2001).",
          2, 500, group="B11"),
    Param("bb_k", 2.0, "Bollinger bands: standard deviations from the SMA", "Bollinger's default: 2 standard deviations.", 0.5, 5.0, group="B11"),
    Param("squeeze_lookback", 125, "bollinger: a squeeze bar has the lowest band width ((upper - lower) / middle) of the last n bars",
          "Bollinger: the squeeze is band width at a six-month low (about 125 sessions).", 10, 500, group="B11"),
    Param("squeeze_within", 10, "bollinger: the close outside the band comes within this many bars after the last squeeze bar",
          "Two trading weeks on Daily: the expansion follows the squeeze, not a later unrelated move; no fitted value.", 1, 100, group="B11"),
    Param("kc_n", 20, "ttm: Keltner channel EMA and ATR length", "TTM squeeze default: a 20-period Keltner channel (Carter 2005).", 2, 500, group="B11"),
    Param("kc_mult", 1.5, "ttm: Keltner channel width in ATRs", "TTM squeeze default: 1.5 ATR (Carter 2005).", 0.5, 5.0, group="B11"),
    # --- B12 moving-average breakout (U6 BK3) -----------------------------------------------------------------------------
    Param("ma_n", 50, "the key moving average", "The 50-day line, the key intermediate average in O'Neil's and Minervini's work.", 2, 500, group="B12"),
    Param("ma_type", "sma", "the key moving average: simple or exponential", "Both are in use; the simple average is the classic key line.",
          choices=("sma", "ema"), group="B12"),
    Param("ma_slope_n", 10, "flat or rising: the key MA now against its value this many bars ago", "Two trading weeks on Daily.", 1, 250, group="B12"),
    Param("ma_flat_pct", 0.5, "flat: the key MA fell by at most this % over ma_slope_n bars (a rising MA always passes)",
          "Half a percent over two weeks reads as flat for a 50-bar average on Daily; no fitted value.", 0.0, 10.0, group="B12"),
    Param("ma_consol_n", 10, "consolidation: the closes of the n bars before the break stayed within ma_band_atr ATRs of the key MA; "
          "the break closes above all of them",
          "Two trading weeks of closes hugging the line = a consolidation around it.", 2, 200, group="B12"),
    Param("ma_band_atr", 1.0, "consolidation: the band around the key MA, in ATRs (ATR of the bars before the break)",
          "One ATR: inside one bar's noise of the line.", 0.1, 10.0, group="B12"),
    Param("ma_stack", False, "MA stack alignment: also require the fast MA above the key MA above the slow MA (up; the mirror for down)",
          "Spec B12 'MA stack alignment'; off by default because a break from a base around the line often comes before the stack aligns.",
          group="B12"),
    Param("ma_stack_fast", 20, "MA stack: the fast average's length (same type as the key MA)", "20 / 50 / 200: the common short / intermediate / long stack.",
          2, 500, group="B12"),
    Param("ma_stack_slow", 200, "MA stack: the slow average's length (same type as the key MA)", "The 200-day line, the common long-term average.",
          2, 500, group="B12"),
    # --- B15 RSI / ADX regime breakout (U6 BK3) ---------------------------------------------------------------------------
    Param("rsi_n", 14, "RSI length (Wilder smoothing)", "Wilder (1978) RSI length.", 2, 200, group="B15"),
    Param("rsi_band", 10.0, "compression: RSI stayed within 50 +/- band on the regime_n bars before the break; leaving = RSI above 50 + band "
          "(up) / below 50 - band (down)", "The 40-60 RSI band reads as no trend (Cardwell's RSI range rules).", 1.0, 40.0, group="B15"),
    Param("regime_n", 20, "the compression (and the price range it breaks) is the regime_n bars before the bar",
          "About one trading month on Daily: a 'long' compression.", 5, 500, group="B15"),
    Param("adx_n", 14, "ADX length (Wilder smoothing)", "Wilder (1978) ADX length.", 2, 200, group="B15"),
    Param("adx_low", 20.0, "ADX was at or below this inside the compression (a low base) and rises on the break bar, with the DI on the "
          "breakout side leading", "Common reading of Wilder's ADX: below 20 = no trend; rising from there = a trend being born.",
          5.0, 60.0, group="B15"),
    # --- B5 trendline / channel (U6 BK4, flag screener_breakouts_bk4) -----------------------------------------------------
    Param("tl_mode", "line", "line: close beyond a falling resistance line (up) / rising support line (down); channel: close beyond the far "
          "edge of a rising (up) / falling (down) channel - the parallel through the farthest extreme between the line's anchors",
          "Both forms named in spec B5; the lines and channels are the platform's trendline port (CH3b, Trade trendlines).",
          choices=("line", "channel"), group="B5"),
    Param("tl_order", 3, "trendline swings: a swing high / low is the extreme of the 2 x order + 1 bars around it (known order bars later)",
          "Trade trendlines default: the extreme of 3 bars either side.", 2, 10, group="B5"),
    Param("tl_min_touches", 2, "the line has at least this many swings on it (its two anchors included)", "Spec B5: at least 2 touches.",
          2, 10, group="B5"),
    Param("tl_min_gap", 5, "the line's two anchors are at least this many bars apart", "Trade trendlines default: anchors at least 5 bars apart.",
          2, 100, group="B5"),
    Param("tl_max_slope", 0.5, "angle filter: the line moves at most this many median bar ranges per bar",
          "Trade trendlines default (0.5 x the median range per bar): a steeper line is a leg, not a trendline.", 0.05, 5.0, group="B5"),
    Param("tl_touch_tol", 0.25, "a swing touches the line within this many median bar ranges",
          "Trade trendlines default: within a quarter of the median range.", 0.0, 2.0, group="B5"),
    Param("tl_max_bars", 120, "age filter: the line's first anchor is at most this many bars before the bar",
          "About six months on Daily; spec B5's age filter, which also bounds the cost.", 20, 500, group="B5"),
    # --- B6 flag / pennant / triangle (U6 BK4) ----------------------------------------------------------------------------
    Param("pattern", "flag", "flag: a fast pole, then a shallow consolidation, then a close above it; pennant: the same with a contracting "
          "consolidation; triangle: a falling resistance and a rising support line converging, then a close above the resistance",
          "The three patterns named in spec B6 (Edwards & Magee, Technical Analysis of Stock Trends; Bulkowski, Encyclopedia of Chart Patterns).",
          choices=("flag", "pennant", "triangle"), group="B6"),
    Param("pole_min_atr", 3.0, "flag / pennant: the pole rises at least this many ATRs (ATR at the pole's top)",
          "A pole is a fast, outsized move; 3 ATR is a common flagpole threshold, not fitted.", 0.5, 20.0, group="B6"),
    Param("pole_max_bars", 10, "flag / pennant: the pole takes at most this many bars", "Bulkowski: flag poles are steep and short (two weeks on Daily).",
          2, 50, group="B6"),
    Param("flag_min_bars", 5, "flag / pennant: the consolidation lasts at least this many bars", "One trading week on Daily.", 2, 100, group="B6"),
    Param("flag_max_bars", 15, "flag / pennant: the consolidation lasts at most this many bars",
          "Bulkowski: flags and pennants last up to about three weeks; longer is a base (B1).", 3, 200, group="B6"),
    Param("flag_max_retrace", 0.5, "flag / pennant: the consolidation gives back at most this fraction of the pole",
          "Classic rule: a flag retraces less than half of the pole (Edwards & Magee).", 0.05, 1.0, group="B6"),
    # --- B8 gap breakout (U6 BK4) -----------------------------------------------------------------------------------------
    Param("gap_level", "prev_day", "the level the gap clears: prev_day (the previous session's high / low) or base (the highest high / "
          "lowest low of the previous gap_base_n sessions)", "Spec B8 'gap beyond a level/base on the open'.",
          choices=("prev_day", "base"), group="B8"),
    Param("gap_base_n", 20, "base: the sessions whose high / low make the level", "About one trading month, as vol_avg_n.", 2, 250, group="B8"),
    Param("gap_min_atr", 0.0, "the session opens beyond the level by at least this many daily ATRs (ATR of the sessions before; 0 = any gap)",
          "ATR-scaled, not points; 0 keeps every open beyond the level - raise it for larger gaps only.", 0.0, 10.0, group="B8"),
    Param("gap_hold_min", 30, "intraday: every close of the session's first N minutes stays beyond the level; the signal is the bar that ends "
          "them (a whole number of bars); Daily: the day's close", "Spec B8 'holding above the level by the end of the first N minutes'; 30 "
          "minutes = the second opening-range window of spec B16.", 1, 240, group="B8"),
    # --- B9 retest entry (U6 BK4) -----------------------------------------------------------------------------------------
    Param("retest_of", "range", "the breakout a retest follows: range (B1), swing (B2), donchian (B10), trendline or channel (B5), with their own "
          "parameters; trendline_projected: the trendline breakout (B5 line mode) with the broken line read at the retest bar (trendline: "
          "its value at the breakout bar)", "Spec B9 'breakout already happened'; the fixed-level breakouts of this family.",
          choices=("range", "swing", "donchian", "trendline", "channel", "trendline_projected"), group="B9"),
    Param("retest_max_bars", 10, "the retest comes within this many bars after the breakout (and before any close back inside the level)",
          "Two trading weeks on Daily: a later return is a new base, not a retest.", 1, 100, group="B9"),
    Param("retest_reversal", True, "the retest bar is a logical reversal at the level (app/price_action/reversal.py, as ReversalAt); False: "
          "a touch within retest_tol_atr ATR with the close held is enough", "Spec B9: the same commitment forms as the pullback screens (ReversalAt).",
          group="B9"),
    # --- screen fitness (U6 BK5, flag screener_breakouts_fitness) ---------------------------------------------------------
    Param("fitness_min_samples", 30, "screen fitness: a bucket (a grade, a regime, a timeframe, a horizon) with fewer signals than this is "
          "marked low_sample and shown greyed", "Spec §6 'shown greyed when samples are small'; 30 is the usual rule of thumb before a mean is read at all.",
          5, 1000, group="fitness"),
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
    if clean["tod_min_sessions"] > clean["tod_sessions"]:
        errors.append("tod_min_sessions must not be above tod_sessions - defaults used")
        clean["tod_sessions"], clean["tod_min_sessions"] = DEFAULTS["tod_sessions"], DEFAULTS["tod_min_sessions"]
    if clean["flag_min_bars"] > clean["flag_max_bars"]:
        errors.append("flag_min_bars must not be above flag_max_bars - defaults used")
        clean["flag_min_bars"], clean["flag_max_bars"] = DEFAULTS["flag_min_bars"], DEFAULTS["flag_max_bars"]
    if clean["vcp_min_bars"] > clean["vcp_max_bars"]:
        errors.append("vcp_min_bars must not be above vcp_max_bars - defaults used")
        clean["vcp_min_bars"], clean["vcp_max_bars"] = DEFAULTS["vcp_min_bars"], DEFAULTS["vcp_max_bars"]
    if not clean["tt_ma_fast"] < clean["tt_ma_mid"] < clean["tt_ma_slow"]:
        errors.append("tt_ma_fast < tt_ma_mid < tt_ma_slow is required - defaults used")
        for k in ("tt_ma_fast", "tt_ma_mid", "tt_ma_slow"):
            clean[k] = DEFAULTS[k]
    if not clean["ma_stack_fast"] < clean["ma_n"] < clean["ma_stack_slow"] and clean["ma_stack"]:
        errors.append("the MA stack needs ma_stack_fast < ma_n < ma_stack_slow - defaults used")
        for k in ("ma_stack_fast", "ma_n", "ma_stack_slow"):
            clean[k] = DEFAULTS[k]
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
