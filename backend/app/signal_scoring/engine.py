from typing import Dict, List, Optional, Tuple

import pandas as pd

from app.brokers.models import OptionChain
from app.core.enums import SignalDirection, grade_from_score
from app.core.models import Signal
from app.option_chain.analysis import analyze_option_chain
from app.option_chain.models import OptionChainAnalysis
from app.price_action.candlestick_patterns import detect_patterns_at
from app.price_action.market_structure import analyze_market_structure
from app.price_action.models import MarketStructureResult, PatternMatch, TrendState
from app.signal_scoring.models import EnrichedSignal, ScoreComponent
from app.support_resistance.engine import SupportResistanceEngine
from app.support_resistance.models import SRZone

# Weights per the Signal Engine spec: Trend 20%, Structure 15%, S/R 20%, Price Action 20%,
# Volume 10%, Option Chain 10%, Risk/Reward 5% - sums to 100.
WEIGHTS: Dict[str, int] = {
    "trend": 20,
    "structure": 15,
    "support_resistance": 20,
    "price_action": 20,
    "volume": 10,
    "option_chain": 10,
    "risk_reward": 5,
}


def _trend_component(signal: Signal, structure: MarketStructureResult) -> Tuple[float, str]:
    wants_up = signal.direction == SignalDirection.LONG
    if structure.trend == TrendState.RANGE:
        return 50.0, "Higher-timeframe trend is RANGE - only partial alignment"
    trend_is_up = structure.trend == TrendState.UPTREND
    if trend_is_up == wants_up:
        return 100.0, f"Trend is {structure.trend.value}, aligned with {signal.direction.value}"
    return 0.0, f"Trend is {structure.trend.value}, conflicts with {signal.direction.value}"


def _structure_component(signal: Signal, structure: MarketStructureResult) -> Tuple[float, str]:
    if not structure.events:
        return 50.0, "No recent BOS/CHoCH event to confirm or deny structure"
    latest = structure.events[-1]
    wants_bullish = signal.direction == SignalDirection.LONG
    matches = (latest.direction == "BULLISH") == wants_bullish
    if matches and latest.event == "BOS":
        return 100.0, f"Recent BOS confirms {latest.direction.lower()} structure at {latest.level:.2f}"
    if matches and latest.event == "CHoCH":
        return 70.0, f"Recent CHoCH suggests emerging {latest.direction.lower()} structure"
    return 0.0, f"Recent {latest.event} ({latest.direction}) conflicts with the signal direction"


def _support_resistance_component(signal: Signal, zones: List[SRZone]) -> Tuple[float, str]:
    if signal.entry is None:
        return 0.0, "No entry price to check against support/resistance"
    wanted_kind = "SUPPORT" if signal.direction == SignalDirection.LONG else "RESISTANCE"
    candidates = [z for z in zones if z.kind == wanted_kind]
    if not candidates:
        return 30.0, f"No {wanted_kind.lower()} zone identified near this entry"

    nearest = min(candidates, key=lambda z: abs(z.mid - signal.entry))
    distance_pct = abs(nearest.mid - signal.entry) / signal.entry * 100
    if distance_pct > 1.0:
        return 30.0, f"Nearest {wanted_kind.lower()} zone is {distance_pct:.2f}% away - not a tight confluence"
    return float(nearest.strength_score), (
        f"Entry sits inside a {wanted_kind.lower()} zone (strength {nearest.strength_score}/100, "
        f"source={nearest.source})"
    )


def _price_action_component(signal: Signal, patterns: List[PatternMatch]) -> Tuple[float, str]:
    wanted_dir = "BULLISH" if signal.direction == SignalDirection.LONG else "BEARISH"
    matching = [p for p in patterns if p.direction == wanted_dir]
    if not matching:
        return 20.0, "No confirming candlestick pattern on the signal bar"
    best = max(matching, key=lambda p: p.confidence)
    return float(best.confidence), f"{best.pattern} ({best.confidence}% confidence) confirms {wanted_dir.lower()} price action"


def _volume_component(df: pd.DataFrame) -> Tuple[float, str]:
    if len(df) < 20:
        return 50.0, "Not enough bars to assess volume"
    recent_volume = df["volume"].iloc[-1]
    avg_volume = df["volume"].iloc[-20:].mean()
    if avg_volume <= 0:
        return 50.0, "No usable volume data"
    ratio = recent_volume / avg_volume
    return min(100.0, ratio * 50), f"Signal-bar volume is {ratio:.2f}x the 20-bar average"


def _option_chain_component(signal: Signal, analysis: Optional[OptionChainAnalysis]) -> Tuple[float, str]:
    if analysis is None:
        return 50.0, "No option chain data supplied"
    wanted = "BULLISH" if signal.direction == SignalDirection.LONG else "BEARISH"
    bias = analysis.bias.value
    if bias == wanted:
        return 100.0, f"Option chain bias is {bias}, confirms {wanted.lower()}"
    if bias == "CONFLICTING":
        return 30.0, "Option chain bias is CONFLICTING (PCR and OI-change disagree)"
    if bias == "NEUTRAL":
        return 50.0, "Option chain bias is NEUTRAL"
    return 0.0, f"Option chain bias is {bias}, conflicts with the signal direction"


def _risk_reward_component(signal: Signal) -> Tuple[float, str]:
    if signal.risk_reward is None:
        return 0.0, "No risk/reward computed for this signal"
    rr = signal.risk_reward
    pct = min(100.0, max(0.0, (rr - 1.0) / 2.0 * 100))
    return pct, f"Risk/Reward is 1:{rr:.2f}"


def enrich_signal(
    signal: Signal, ltf_df: pd.DataFrame, option_chain: Optional[OptionChain] = None, swing_window: int = 3
) -> EnrichedSignal:
    """Re-scores an already-generated Signal using the weighted composite formula from the
    Signal Engine spec, by cross-checking it against market structure, support/resistance
    zones, candlestick patterns, volume, and (optionally) option chain bias - all computed
    fresh from `ltf_df` (the strategy's primary/lower timeframe) rather than baked into any
    one strategy, so every inbuilt strategy can be enriched the same way without modification.
    """
    if not signal.is_tradeable:
        return EnrichedSignal(
            signal=signal, composite_score=0, grade=grade_from_score(0),
            breakdown={}, confirmations=["Base signal is NO_TRADE - nothing to score"],
        )

    structure = analyze_market_structure(ltf_df, window=swing_window)
    zones = SupportResistanceEngine(swing_window=swing_window).build_zones(ltf_df, timeframe=signal.timeframe_combo)
    patterns = detect_patterns_at(ltf_df, len(ltf_df) - 1)
    oc_analysis = analyze_option_chain(option_chain) if option_chain is not None else None

    raw_components = {
        "trend": _trend_component(signal, structure),
        "structure": _structure_component(signal, structure),
        "support_resistance": _support_resistance_component(signal, zones),
        "price_action": _price_action_component(signal, patterns),
        "volume": _volume_component(ltf_df),
        "option_chain": _option_chain_component(signal, oc_analysis),
        "risk_reward": _risk_reward_component(signal),
    }

    breakdown: Dict[str, ScoreComponent] = {}
    confirmations: List[str] = []
    weighted_total = 0.0
    for key, (pct, note) in raw_components.items():
        weight = WEIGHTS[key]
        contribution = pct / 100 * weight
        weighted_total += contribution
        breakdown[key] = ScoreComponent(pct=round(pct, 1), weight=weight, contribution=round(contribution, 2), note=note)
        confirmations.append(note)

    composite = int(round(weighted_total))
    return EnrichedSignal(
        signal=signal, composite_score=composite, grade=grade_from_score(composite),
        breakdown=breakdown, confirmations=confirmations,
    )
