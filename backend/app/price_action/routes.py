"""The price-action endpoints on the candles the request sends: market structure, candlestick patterns, the logical
reversal at a level and the reversal chart markers. Education / chart annotation only.

M55 Q1: moved from app/main.py (which now only includes routers) as it was - only the annotations are in built-in
generics; included at the end of the router list in the same order as before, so every path, method, dependency
and the match order are unchanged (tests/test_m55_q1_route_table.py).
"""
import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth.dependencies import get_current_user
from app.core.models import OHLCVBar, bars_to_dataframe
from app.db.models import User
from app.price_action.candlestick_patterns import detect_patterns
from app.price_action.market_structure import analyze_market_structure
from app.price_action.models import MarketStructureResult, PatternMatch

router = APIRouter()


class CandlesRequest(BaseModel):
    symbol: str
    candles: list[OHLCVBar]
    timeframe: str = "1min"
    swing_window: int = 3


@router.post("/api/price-action/structure", response_model=MarketStructureResult)
def price_action_structure(request: CandlesRequest, _: User = Depends(get_current_user)) -> MarketStructureResult:
    df = bars_to_dataframe(request.candles)
    return analyze_market_structure(df, window=request.swing_window)


@router.post("/api/price-action/patterns", response_model=list[PatternMatch])
def price_action_patterns(request: CandlesRequest, _: User = Depends(get_current_user)) -> list[PatternMatch]:
    df = bars_to_dataframe(request.candles)
    return detect_patterns(df)


class ReversalRequest(BaseModel):
    """Trade port: a logical-reversal check at a level on CLOSED candles (oldest first). `settings` = price_action
    pa_settings overrides (reversal_mode "composite" (default) or "score100", thresholds in median-range multiples)."""
    candles: list[OHLCVBar] = Field(min_length=3, max_length=5000)
    level: float | list[float]
    direction: Literal["BULLISH", "BEARISH"]
    settings: dict[str, Any] = Field(default_factory=dict)


class ReversalMarkersRequest(BaseModel):
    candles: list[OHLCVBar] = Field(min_length=3, max_length=3000)
    levels: list[float] = Field(min_length=1, max_length=20)
    settings: dict[str, Any] = Field(default_factory=dict)


def _pa_settings_or_400(overrides: dict[str, Any]) -> dict[str, Any]:
    from app.price_action import pa_settings
    clean, errors = pa_settings.validate(overrides)
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))
    return clean


def _plain(value: Any) -> Any:
    """numpy scalars -> Python, for the JSON response."""
    return json.loads(json.dumps(value, default=lambda o: o.item() if hasattr(o, "item") else str(o)))


@router.post("/api/price-action/reversal")
def price_action_reversal(request: ReversalRequest, _: User = Depends(get_current_user)) -> dict[str, Any]:
    """Did price logically reverse at the level on the last closed candle? Education / chart annotation only."""
    from app.price_action.reversal import evaluate_reversal
    s = _pa_settings_or_400(request.settings)
    df = bars_to_dataframe(request.candles).reset_index()
    return _plain(evaluate_reversal(df, request.level, request.direction, s))


@router.post("/api/price-action/reversal-markers")
def price_action_reversal_markers(request: ReversalMarkersRequest, _: User = Depends(get_current_user)) -> list[dict[str, Any]]:
    """Chart annotations: each closed candle's passing reversal at a nearby level (each marker uses only bars up to its own)."""
    from app.price_action.reversal import scan_markers
    s = _pa_settings_or_400(request.settings)
    df = bars_to_dataframe(request.candles).reset_index()
    return _plain(scan_markers(df, request.levels, s))
