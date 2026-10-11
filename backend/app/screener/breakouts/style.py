"""U6 BK1 (spec §2): the timeframe and style selector, and the closed-candle multi-timeframe plumbing.

* Style: Intraday (1m / 3m / 5m / 15m) or Swing (30m, 1h, 2h, 4h, 1d, 1w). The user picks the signal timeframe; the
  context is always the next higher timeframes from the `context_ladder` parameter (15m -> 1h + 1d, 1h -> 1d + 1w).
* Every context value reaches a signal bar by CLOSE time: a 1d bar's trend is visible only on signal bars that close at or
  after that day's bar closed - the same look-ahead guard as the ScreenQL runtime (app/screener/runtime.py), whose
  `resample` builds every ScreenQL timeframe so the engine and a ScreenQL screen see identical bars. 2h / 4h (swing style
  only, not in screenql/1 yet - open question BK1-1) are built here with the same 09:15 anchoring.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from app.screener.breakouts.params import DEFAULT_CONTEXT_LADDER, TF_MINUTES
from app.screener.nodes import TF_MINUTES as SCREENQL_TF_MINUTES

STYLES: Dict[str, Tuple[str, ...]] = {"intraday": ("1m", "3m", "5m", "15m"), "swing": ("30m", "1h", "2h", "4h", "1d", "1w")}
# U1 universe picker defaults per style (spec §2)
DEFAULT_UNIVERSE: Dict[str, str] = {"intraday": "FNO", "swing": "NIFTY500"}
EXTRA_TFS: Dict[str, Tuple[str, timedelta]] = {"2h": ("120min", timedelta(hours=2)), "4h": ("240min", timedelta(hours=4))}


def style_of(signal_tf: str) -> str:
    for style, tfs in STYLES.items():
        if signal_tf in tfs:
            return style
    raise ValueError(f"{signal_tf!r} is not a signal timeframe; intraday: {', '.join(STYLES['intraday'])}; swing: {', '.join(STYLES['swing'])}")


def context_timeframes(signal_tf: str, ladder: Optional[Dict[str, List[str]]] = None) -> Tuple[str, ...]:
    """The higher timeframes read for context (two, a weekly signal has one)."""
    style_of(signal_tf)
    lad = ladder if ladder is not None else DEFAULT_CONTEXT_LADDER
    if signal_tf not in lad:
        raise ValueError(f"the context ladder has no entry for {signal_tf!r}")
    return tuple(lad[signal_tf])


def screenql_expressible(*tfs: str) -> bool:
    """True when every timeframe exists in screenql/1 (2h / 4h do not yet)."""
    return all(tf in SCREENQL_TF_MINUTES for tf in tfs)


def duration(tf: str) -> Any:
    from app.screener.runtime import _DURATION
    if tf in EXTRA_TFS:
        return EXTRA_TFS[tf][1]
    return _DURATION[tf]


def bar_close(index: pd.DatetimeIndex, tf: str) -> pd.DatetimeIndex:
    return index + duration(tf)


def resample(base: pd.DataFrame, base_tf: str, tf: str) -> pd.DataFrame:
    """Closed `tf` bars from closed base bars (bar-start labels). ScreenQL timeframes use the runtime's own resampler (so a
    ScreenQL screen and the engine read the same bars); 2h / 4h are anchored at the exchange open like it, and a trailing
    bucket that had not closed is dropped."""
    from app.screener.runtime import resample as runtime_resample
    from app.screener.runtime import session_origin
    if tf == base_tf:
        return base
    if tf not in EXTRA_TFS:
        return runtime_resample(base, base_tf, tf)
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    if "oi" in base.columns:
        agg["oi"] = "last"
    if base.empty:
        return base
    out = base.resample(EXTRA_TFS[tf][0], label="left", closed="left", origin=session_origin(base.index)).agg(agg)
    out = out.dropna(subset=["close"])
    if len(out) and out.index[-1] + EXTRA_TFS[tf][1] > base.index[-1] + duration(base_tf):
        out = out.iloc[:-1]
    return out


def frames_for(frames: Dict[str, pd.DataFrame], signal_tf: str, context: Tuple[str, ...]) -> Dict[str, pd.DataFrame]:
    """The signal frame plus every context frame: given ones are used as they are (closed bars, as the data layer gives
    them), missing ones are resampled from the finest frame given that is not coarser than the signal timeframe."""
    if signal_tf not in frames or frames[signal_tf] is None or frames[signal_tf].empty:
        raise ValueError(f"no {signal_tf} bars")
    out = {signal_tf: frames[signal_tf]}
    for tf in context:
        given = frames.get(tf)
        out[tf] = given if given is not None and not given.empty else resample(frames[signal_tf], signal_tf, tf)
    return out


def align_closed(values: pd.Series, src_tf: str, dst_index: pd.DatetimeIndex, dst_tf: str) -> pd.Series:
    """A `src_tf` series onto `dst_tf` bars by close time: each destination bar sees the last source bar that had closed
    by the destination bar's own close (never a source bar still forming). The ScreenQL runtime's alignment, verbatim."""
    if src_tf == dst_tf:
        return values
    avail = pd.Series(values.to_numpy(), index=bar_close(values.index, src_tf))
    avail = avail[~avail.index.duplicated(keep="last")].sort_index()
    dst_close = bar_close(dst_index, dst_tf)
    with pd.option_context("future.no_silent_downcasting", True):        # a trend series stays object (no dtype guess)
        out = avail.reindex(avail.index.union(dst_close)).ffill().reindex(dst_close)
    return pd.Series(out.to_numpy(), index=dst_index)


def selector() -> Dict[str, Any]:
    """The style / timeframe selector as data for the builder (spec §2)."""
    return {"styles": {k: list(v) for k, v in STYLES.items()}, "default_universe": dict(DEFAULT_UNIVERSE),
            "context_ladder": {k: list(v) for k, v in DEFAULT_CONTEXT_LADDER.items()}, "directions": ["up", "down", "both"],
            "timeframe_minutes": dict(TF_MINUTES), "closed_bars_only": True}


__all__ = ["STYLES", "DEFAULT_UNIVERSE", "style_of", "context_timeframes", "screenql_expressible", "resample", "frames_for",
           "align_closed", "bar_close", "selector"]
