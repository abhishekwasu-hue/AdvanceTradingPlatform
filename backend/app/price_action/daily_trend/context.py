"""N1 L2: the gate's Daily trend context - Trade `bots_common/level_reaction.trend_context` (pinned revision), the typed
bridge between this package's fold and the level gate (`level_gate.gate.context_from_state`, which reads a `DState`
through its read-only `DailyState` protocol; mypy checks this call).

Today's Daily bar is removed first (the trend is what was known at yesterday's close); fewer than 30 Daily candles ->
"trend unclear" (no entries). No I/O: the caller reads the bars (X1 reads the shared lake, once per symbol and day)."""
from typing import Any, Dict, Mapping, Optional

import pandas as pd

from app.price_action.daily_trend import fold as F
from app.price_action.level_gate import gate as G

MIN_DAILY_BARS = 30


def trend_context(daily_df: pd.DataFrame, today: Any = None, s: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Which sides may enter today: `context_from_state` of the last Daily state known before `today`."""
    d = F.prepare_daily(daily_df, today)
    if len(d) < MIN_DAILY_BARS:
        return {"ok": False, "trend": "UNKNOWN", "sides": set(), "reason": f"{G.R_NO_TREND} (Daily data too short: {len(d)} bars)"}
    state: F.DState = F.fold(d)[-1]
    return G.context_from_state(state, s, asof=str(d["timestamp"].iloc[-1].date()))
