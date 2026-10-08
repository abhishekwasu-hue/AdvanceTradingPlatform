"""Gap context - INTERFACE PLACEHOLDER ONLY.

ATP deliberately has no gap logic of its own. The gap code in the Trade repository today (opportunity_engine detectors,
the MTF pullback overnight gaps, mtf_gap_fill, sr_levels_v3 unfilled gaps, gap_fill_reversal) uses four different
definitions, fixed % thresholds and has lookahead / re-fire bugs, so it is NOT ported.

The unified gap context (gap_atr = (open - previous close) / ATR14; classes G0-G5 / GX plus an event flag;
acceptance / rejection history; fill %; an unfilled-gap registry) will be merged in Trade as `price_action/gap_context.py`
(vision v2.1). TODO: once that is merged, port it here in its own PR, keeping this signature, with its tests and
no-lookahead truncation tests.
"""
from typing import Any, Dict, Optional

import pandas as pd


class GapContextNotPorted(NotImplementedError):
    """Raised until the Trade gap context is ported - callers must not fall back to ad-hoc gap rules."""


def gap_context(daily: pd.DataFrame, intraday: Optional[pd.DataFrame] = None, *, as_of=None,
                settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Planned: the gap of the session `as_of` (class, gap_atr, fill %, acceptance / rejection, open gaps). Not ported yet."""
    raise GapContextNotPorted("gap context is not ported yet - see the module docstring (Trade price_action/gap_context.py)")
