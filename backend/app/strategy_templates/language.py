"""Strategy templates: the signal text (owner order section 6, policy 11l), built from fixed tokens.

A template never writes free text into a signal: it lists token ids, and each token is a phrase in structure /
indicator words. The only things filled in are the timeframe names ("15-minute"), from a fixed table. So the text
can carry no price, no indicator value and no buy / sell / target / stop wording - `guard` checks every phrase, and
the tests run it over every token and every library template's rendered text.

Family G (review finding WB7, owner question WAVE-4): the guard refuses digits, so wave labels are written in words
("wave two", "wave B", "the fifth wave": `WAVE_WORDS`, `TYPE_WORDS`, `CLARITY_WORDS`), and two more placeholders name the
wave-count timeframe and the higher timeframe (`{wave_timeframe}` from the setting `wave_tf`, `{higher_timeframe}` from
`higher_tf`). The invalidation is said in words ("beyond the start of wave A"), never as a price.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence

from app.signals import language as _language

TOKENS: Dict[str, str] = {
    # D1 Supertrend flip
    "supertrend_turned_bullish": "Supertrend turned bullish on the {timeframe} chart",
    "supertrend_turned_bearish": "Supertrend turned bearish on the {timeframe} chart",
    "context_supertrend_agrees": "the {context_timeframe} Supertrend direction agrees",
    # D2 moving-average crossover
    "fast_average_crossed_above": "the fast moving average crossed above the slow one on the {timeframe} chart",
    "fast_average_crossed_below": "the fast moving average crossed below the slow one on the {timeframe} chart",
    "close_above_trend_average": "the close is above the trend average",
    "close_below_trend_average": "the close is below the trend average",
    # B1 range breakout
    "range_broken_upward": "the close broke out above the range on the {timeframe} chart",
    "range_broken_downward": "the close broke down below the range on the {timeframe} chart",
    # B3 channel breakout
    "channel_broken_upward": "the close broke out above the upper edge of a rising channel on the {timeframe} chart",
    "channel_broken_downward": "the close broke down below the lower edge of a falling channel on the {timeframe} chart",
    # C1 range breakout and retest, C3 channel breakout and retest (step 3, R7)
    "range_retest_held_from_above": "the close broke out above the range on the {timeframe} chart and the retest from above held",
    "range_retest_held_from_below": "the close broke down below the range on the {timeframe} chart and the retest from below held",
    "channel_retest_held_from_above": ("the close broke out above the upper edge of a rising channel on the {timeframe} chart and the"
                                       " retest of that edge from above held"),
    "channel_retest_held_from_below": ("the close broke down below the lower edge of a falling channel on the {timeframe} chart and"
                                       " the retest of that edge from below held"),
    # A2 range edges (idea library T2)
    "range_on_chart": "range on the {timeframe} chart",
    "lower_edge_rejection_formed": "the market reached the lower edge and a rejection formed",
    "upper_edge_rejection_formed": "the market reached the upper edge and a rejection formed",
    # Family F option seller packs (F1, F2): the side or the edge in words, for the option structure the user set up
    "pack_bullish_continuation": "read as a bullish continuation for the option structure in your own settings",
    "pack_bearish_continuation": "read as a bearish continuation for the option structure in your own settings",
    "pack_range_lower_edge": "read as the lower edge of the range for the option structure in your own settings",
    "pack_range_upper_edge": "read as the upper edge of the range for the option structure in your own settings",
    # A5 structure flip retest (idea library T5)
    "resistance_flip_retested": "former resistance on the {timeframe} chart was broken and is being retested from above; rejection formed",
    "support_flip_retested": "former support on the {timeframe} chart was broken and is being retested from below; rejection formed",
    # Step 4 (TPL-4): B2 swing-level, B4 trendline, B5 Donchian, B7 squeeze, B6 pattern, B8 quiet bar, B9 volume, B11 contraction breakouts
    "swing_high_broken_with_trend": "the close broke above the latest swing high in the direction of the {timeframe} structure",
    "swing_low_broken_with_trend": "the close broke below the latest swing low in the direction of the {timeframe} structure",
    "trendline_broken_upward": "the close broke out above a falling trendline on the {timeframe} chart",
    "trendline_broken_downward": "the close broke down below a rising trendline on the {timeframe} chart",
    "donchian_broken_upward": "the close broke out above the top of the Donchian channel on the {timeframe} chart",
    "donchian_broken_downward": "the close broke down below the bottom of the Donchian channel on the {timeframe} chart",
    "squeeze_broken_upward": "a volatility squeeze ended with a close beyond the upper side on the {timeframe} chart",
    "squeeze_broken_downward": "a volatility squeeze ended with a close beyond the lower side on the {timeframe} chart",
    "pattern_broken_upward": "the close broke out above a flag, pennant or triangle on the {timeframe} chart",
    "pattern_broken_downward": "the close broke down below a flag, pennant or triangle on the {timeframe} chart",
    "quiet_bar_broken_upward": "the close broke above the high of a quiet bar (an inside or narrow-range bar) on the {timeframe} chart",
    "quiet_bar_broken_downward": "the close broke below the low of a quiet bar (an inside or narrow-range bar) on the {timeframe} chart",
    "volume_surge_at_range_top": "a volume surge drove the close to the top of the recent range on the {timeframe} chart",
    "volume_surge_at_range_bottom": "a volume surge drove the close to the bottom of the recent range on the {timeframe} chart",
    "contraction_base_broken_upward": "the close broke out above the pivot of a tightening base on the {timeframe} chart",
    "contraction_base_broken_downward": "the close broke down below the pivot of a tightening base on the {timeframe} chart",
    # C5 Donchian breakout and retest
    "donchian_retest_held_from_above": ("the close broke out above the top of the Donchian channel on the {timeframe} chart and the retest"
                                        " from above held"),
    "donchian_retest_held_from_below": ("the close broke down below the bottom of the Donchian channel on the {timeframe} chart and the"
                                        " retest from below held"),
    # D4 moving-average breakout, D5 ADX / DI, D6 MACD, D7 parabolic SAR, E4 stochastic
    "average_broken_upward": "the close broke above a flat or rising moving average after a quiet spell around it on the {timeframe} chart",
    "average_broken_downward": "the close broke below a flat or falling moving average after a quiet spell around it on the {timeframe} chart",
    "plus_di_leads_adx_rising": "the plus directional line leads and ADX is above its threshold and rising on the {timeframe} chart",
    "minus_di_leads_adx_rising": "the minus directional line leads and ADX is above its threshold and rising on the {timeframe} chart",
    "macd_crossed_above_signal": "the MACD line crossed above its signal line on the {timeframe} chart",
    "macd_crossed_below_signal": "the MACD line crossed below its signal line on the {timeframe} chart",
    "macd_above_zero": "the MACD line is above zero",
    "macd_below_zero": "the MACD line is below zero",
    "sar_flipped_below_bars": "the parabolic SAR flipped below the bars on the {timeframe} chart",
    "sar_flipped_above_bars": "the parabolic SAR flipped above the bars on the {timeframe} chart",
    "stochastic_up_from_oversold": "the stochastic turned up out of the oversold zone on the {timeframe} chart",
    "stochastic_down_from_overbought": "the stochastic turned down out of the overbought zone on the {timeframe} chart",
    "ctx_close_above_trend_average": "the {context_timeframe} close is above its trend average",
    "ctx_close_below_trend_average": "the {context_timeframe} close is below its trend average",
    # Step 5 (TPL-5): C2 swing breakout and retest, D3 moving-average pullback, E1 RSI pullback, E3 Bollinger mean reversion,
    # E5 session VWAP reclaim / reject
    "swing_retest_held_from_above": "the close broke above the latest swing high on the {timeframe} chart and the retest from above held",
    "swing_retest_held_from_below": "the close broke below the latest swing low on the {timeframe} chart and the retest from below held",
    "pullback_to_fast_ma_reversed_up": ("in a rising trend on the {timeframe} chart the pullback reached the fast moving average and"
                                        " a bullish reversal formed there"),
    "rally_to_fast_ma_reversed_down": ("in a falling trend on the {timeframe} chart the rally reached the fast moving average and"
                                       " a bearish reversal formed there"),
    "rsi_back_up_from_oversold": "the RSI turned back up out of the oversold zone on the {timeframe} chart",
    "rsi_back_down_from_overbought": "the RSI turned back down out of the overbought zone on the {timeframe} chart",
    "ctx_trend_filter_agrees": "the {context_timeframe} trend filter agrees",
    "back_inside_from_lower_band": "the close came back inside the Bollinger bands from below the lower band on the {timeframe} chart",
    "back_inside_from_upper_band": "the close came back inside the Bollinger bands from above the upper band on the {timeframe} chart",
    "range_regime": "the market is in a range regime",
    "vwap_reclaimed_in_session": "the close moved back above the session VWAP after trading below it in the same session on the {timeframe} chart",
    "vwap_lost_in_session": "the close moved back below the session VWAP after trading above it in the same session on the {timeframe} chart",
    # Family G (wave templates): structure words only - wave labels in words, no number, no price
    "wave_two_pullback_ended": "the wave two pullback ended at an area on the {wave_timeframe} chart",
    "wave_four_pullback_ended": "the wave four pullback ended at an area on the {wave_timeframe} chart",
    "wave_b_pullback_ended": "the wave B pullback ended at an area on the {wave_timeframe} chart",
    "wave_x_pullback_ended": "the wave X pullback of a double three ended at an area on the {wave_timeframe} chart",
    "correction_may_be_complete": "the correction on the {wave_timeframe} chart may be complete",
    "fifth_wave_may_be_complete": "the fifth wave on the {wave_timeframe} chart may be complete",
    "minor_trend_resumed": "the minor trend resumed",
    "counter_trend_c_leg": "a counter-trend C leg",
    "structure_turned_up": "the {timeframe} structure turned up",
    "structure_turned_down": "the {timeframe} structure turned down",
    "higher_trend_agrees": "the {higher_timeframe} trend agrees",
    "wave_count_clear": "the wave count is clear",
    "alternate_count_agrees": "the alternate count points the same way",
    "alternate_count_disagrees": "the alternate count points the other way",
    "void_beyond_start_of_wave_one": "the count is void beyond the start of wave one",
    "void_beyond_end_of_wave_one": "the count is void if wave four reaches the end of wave one",
    "void_beyond_start_of_wave_a": "the count is void beyond the start of wave A",
    "void_beyond_end_of_wave_b": "the count is void beyond the end of wave B",
    "void_beyond_corrective_extreme": "the count is void beyond the extreme of the corrective wave",
    # Build steps 3 / 4 (W2-W4, R7): the levels and the anchored wave lines in words (no number, no price)
    "void_beyond_end_of_wave_two": "the count is void beyond the end of wave two",
    "void_beyond_end_of_wave_four": "the count is void beyond the end of wave four",
    "void_beyond_end_of_wave_c": "the count is void beyond the end of wave C",
    "flat_has_no_hard_level": "a flat wave B has no hard limit",
    "fifth_wave_is_limited": "the fifth wave is limited, as wave three is never the shortest",
    "flat_b_beyond_start_of_wave_a": "wave B of a flat went beyond the start of wave A",
    "wave_b_channel_broke": "the channel of wave B broke in the direction of wave C",
    "corrective_channel_broke": "the channel of the corrective wave broke",
    "two_four_line_broke": "the line through the ends of wave two and wave four broke",
    "triangle_side_broke": "the triangle side broke",
    "closed_beyond_end_of_wave_a": "the market closed beyond the end of wave A",
    "broken_wave_line_held_on_retest": "the market came back to the broken wave line and held",
    "end_of_wave_a_held_on_retest": "the market came back to the end of wave A and held",
    # Build step 6 (the templates G1, G3, G8): one sentence per template, whatever trigger style the user picked
    "wave_two_pullback_may_be_over": "the wave two pullback on the {wave_timeframe} chart may be over",
    "upward_wave_three_may_start": "an upward wave three may be starting",
    "downward_wave_three_may_start": "a downward wave three may be starting",
    "wave_b_pullback_may_be_over": "the wave B pullback on the {wave_timeframe} chart may be over",
    "upward_wave_c_may_start": "an upward wave C may be starting",
    "downward_wave_c_may_start": "a downward wave C may be starting",
    "void_beyond_a_start_or_b_end": "the count is void beyond the start of wave A, or beyond the end of wave B if B went past it",
    "larger_trend_may_resume_up": "the larger upward trend may be resuming",
    "larger_trend_may_resume_down": "the larger downward trend may be resuming",
    # Build step 7, second part (the templates G2, G4-G7, G9-G11): one sentence per template, whatever trigger style
    "wave_b_channel_broke_on_chart": "the channel of wave B on the {wave_timeframe} chart broke in the direction of wave C",
    "triangle_may_be_complete": "the triangle on the {wave_timeframe} chart may be complete",
    "upward_thrust_may_start": "an upward thrust out of the triangle may be starting",
    "downward_thrust_may_start": "a downward thrust out of the triangle may be starting",
    "void_beyond_far_end_of_triangle": "the count is void beyond the far end of the triangle",
    "wave_c_passed_end_of_wave_a": "wave C on the {wave_timeframe} chart closed beyond the end of wave A",
    "upward_wave_c_under_way": "an upward wave C is under way",
    "downward_wave_c_under_way": "a downward wave C is under way",
    "flat_b_swept_start_of_wave_a": "wave B of a flat on the {wave_timeframe} chart went beyond the start of wave A and came back",
    "void_beyond_extreme_of_wave_b": "the count is void beyond the extreme of wave B",
    "wave_four_pullback_may_be_over": "the wave four pullback on the {wave_timeframe} chart may be over",
    "upward_fifth_wave_may_start": "an upward fifth wave may be starting",
    "downward_fifth_wave_may_start": "a downward fifth wave may be starting",
    "diagonal_may_be_complete": "the ending diagonal on the {wave_timeframe} chart may be complete",
    "upward_turn_may_start": "the market may be turning up",
    "downward_turn_may_start": "the market may be turning down",
    "void_beyond_fifth_wave_extreme": "the count is void beyond the extreme of the fifth wave",
    "wave_x_pullback_may_be_over": "the wave X pullback of a double three on the {wave_timeframe} chart may be over",
    "upward_wave_y_may_start": "an upward wave Y may be starting",
    "downward_wave_y_may_start": "a downward wave Y may be starting",
    "void_beyond_end_of_wave_x": "the count is void beyond the end of wave X",
}

# Wave labels, types and clarity in words (the counter's values: app/price_action/waves/counter.py)
WAVE_WORDS: Dict[str, str] = {"IMP_1": "wave one", "IMP_2": "wave two", "IMP_3": "wave three", "IMP_4": "wave four", "IMP_5": "the fifth wave",
                              "COR_A": "wave A", "COR_B": "wave B", "COR_C": "wave C", "COR_D": "wave D", "COR_E": "wave E",
                              "UNCLEAR": "an unclear count"}
TYPE_WORDS: Dict[str, str] = {"IMPULSE": "impulse", "DIAGONAL": "diagonal", "ZIGZAG": "zigzag", "FLAT": "flat", "EXPANDED_FLAT": "expanded flat",
                              "TRIANGLE": "triangle", "DOUBLE": "double three", "UNCLEAR": "type not clear yet"}
CLARITY_WORDS: Dict[str, str] = {"CLEAR": "the count is clear", "ALT_AGREES": "the alternate count points the same way",
                                 "ALT_DISAGREES": "the alternate count points the other way", "UNCLEAR": "the count is not clear"}
# The anchored wave lines (app/price_action/waves/levels.py LINES) and wave names in words
_WAVE_NAME_WORDS: Dict[str, str] = {"1": "wave one", "2": "wave two", "3": "wave three", "4": "wave four", "5": "the fifth wave",
                                    "A": "wave A", "B": "wave B", "C": "wave C", "D": "wave D", "E": "wave E",
                                    "current": "the running wave"}
LINE_WORDS: Dict[str, str] = {
    "zero_two": "the line from the start of the count through the end of its second wave",
    "one_three": "the line through the ends of the first and the third wave",
    "two_four": "the line through the ends of the second and the fourth wave",
    **{f"channel_{k}": f"the channel of {v}" for k, v in _WAVE_NAME_WORDS.items()},
}

TIMEFRAME_WORDS: Dict[str, str] = {"1m": "1-minute", "3m": "3-minute", "5m": "5-minute", "15m": "15-minute", "30m": "30-minute",
                                   "1h": "1-hour", "1d": "Daily", "1w": "Weekly", "1M": "Monthly"}
PLACEHOLDERS = ("timeframe", "context_timeframe", "wave_timeframe", "higher_timeframe")
# Family G: the placeholders filled from a template's timeframe settings (the setting a template must declare to use one)
TIMEFRAME_SETTING_PLACEHOLDERS: Dict[str, str] = {"wave_timeframe": "wave_tf", "higher_timeframe": "higher_tf"}

# Words a signal may never carry (policy 11l): trade instructions, levels and outcome words. Matched as whole words,
# case-insensitively, also inside hyphenated words ("stop-loss"). Handover 11l: the list and the check live in the one
# shared signal-language module (app/signals/language.py); the behaviour is identical (tests/test_signal_language.py).
FORBIDDEN_WORDS = _language.STRICT_WORDS


def guard(text: str) -> List[str]:
    """The problems of a phrase or a rendered text (empty = clean): a forbidden word, a digit outside a timeframe name
    (a price or a value), an unknown placeholder, or a character outside plain English text."""
    return _language.strict_guard(text, timeframe_words=TIMEFRAME_WORDS.values(), placeholders=PLACEHOLDERS,
                                  forbidden=FORBIDDEN_WORDS)


def render(tokens: Sequence[str], *, timeframe: str, context_timeframe: Optional[str] = None, wave_timeframe: Optional[str] = None,
           higher_timeframe: Optional[str] = None) -> str:
    """The sentence for a signal: the phrases joined with "; ", capitalised, ending with a full stop."""
    words = {"timeframe": TIMEFRAME_WORDS[timeframe],
             "context_timeframe": TIMEFRAME_WORDS[context_timeframe] if context_timeframe else "higher-timeframe",
             "wave_timeframe": TIMEFRAME_WORDS[wave_timeframe] if wave_timeframe else "wave-count",
             "higher_timeframe": TIMEFRAME_WORDS[higher_timeframe] if higher_timeframe else "higher-timeframe"}
    phrases = [TOKENS[t].format(**words) for t in tokens]
    text = "; ".join(phrases)
    return (text[:1].upper() + text[1:] + ".") if text else ""


def unknown_tokens(tokens: Iterable[str]) -> List[str]:
    return [t for t in tokens if t not in TOKENS]


def wave_words(label: Optional[str]) -> str:
    """A wave label, type, clarity value or wave line in words (empty for a missing value)."""
    if not label:
        return ""
    return WAVE_WORDS.get(label) or TYPE_WORDS.get(label) or CLARITY_WORDS.get(label) or LINE_WORDS.get(label) or ""


__all__ = ["TOKENS", "TIMEFRAME_WORDS", "FORBIDDEN_WORDS", "PLACEHOLDERS", "TIMEFRAME_SETTING_PLACEHOLDERS", "WAVE_WORDS", "TYPE_WORDS",
           "CLARITY_WORDS", "LINE_WORDS", "guard", "render", "unknown_tokens", "wave_words"]
