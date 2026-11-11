"""M99 B1-c: a thin facade over the two trend-line modules - one call for a trend line or a channel through CONFIRMED
pivots, as of every bar, projected to that bar from the bars before it.

It changes neither module:
  * `app.screener.breakouts.lines` (`Lines`, the per-bar form B5 / ChannelLine use: at bar t the line is chosen from the
    bars before t - swings known `tl_order` bars after their own bar, no close through the line from its first anchor to
    the bar before t - and its value at t is the projection);
  * `app.price_action.trendlines` (`find_trendline` / `channel_of`, the chart layer's Trade port) for one cut of bars.
`Lines.select` is the finder's choice on each prefix (tests/test_z7_bk4_breakouts.py); tests/test_m99_b1_structure.py
checks this facade against `find_trendline` on cut bars too.

  rising   the rising support over swing lows (a close below breaks it); channel: its parallel through the highest high
           above it between the two anchors (the upper edge)
  falling  the falling resistance over swing highs (a close above breaks it); channel: the lower edge (the mirror image)

Every number is a parameter (LINE_PARAMS, the breakout parameters' defaults - `params_doc()`), labelled "reference - edit before use".
Reference only - nothing here places an order.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

REFERENCE = "reference - edit before use"
DIRECTIONS = ("rising", "falling")
LINE_PARAMS = ("tl_order", "tl_min_gap", "tl_max_slope", "tl_touch_tol", "tl_min_touches", "tl_max_bars")


def params_doc() -> Dict[str, Dict[str, Any]]:
    """The line parameters with their defaults (app/screener/breakouts/params.py), each labelled as a reference."""
    from app.screener.breakouts.params import BY_NAME
    return {k: {"default": BY_NAME[k].default, "doc": BY_NAME[k].doc, "label": REFERENCE} for k in LINE_PARAMS}



def _params(overrides: Dict[str, Any]) -> Dict[str, Any]:
    from app.screener.breakouts import params as bp
    bad = sorted(set(overrides) - set(LINE_PARAMS))
    if bad:
        raise ValueError(f"unknown line setting(s): {', '.join(bad)}")
    return bp.settings(**{k: v for k, v in overrides.items() if v is not None})


@dataclass
class LineSeries:
    """Per bar t: the line (and channel edge) chosen from the bars before t, projected to t; NaN / -1 where there is none."""
    direction: str
    value: np.ndarray            # the line's value at t (real prices)
    channel: np.ndarray          # the channel's far edge at t; NaN once a close went beyond it before t
    anchor_a: np.ndarray         # the first anchor's bar
    anchor_b: np.ndarray         # the second anchor's bar (a confirmed pivot: <= t - 1 - tl_order)
    slope: np.ndarray            # per bar, real prices

    def frame(self, index: Any = None) -> pd.DataFrame:
        return pd.DataFrame({"value": self.value, "channel": self.channel, "anchor_a": self.anchor_a, "anchor_b": self.anchor_b,
                             "slope": self.slope}, index=index)


def line_series(frame: pd.DataFrame, direction: str, **overrides: Any) -> LineSeries:
    """The trend line of `direction` and its channel edge on every bar of `frame` (closed bars, oldest first)."""
    from app.screener.breakouts.lines import _lines
    from app.screener.breakouts.screens import Bars
    if direction not in DIRECTIONS:
        raise ValueError(f"unknown line direction {direction!r} (rising / falling)")
    p = _params(overrides)
    b = Bars(frame, p)
    s = b.side(1 if direction == "rising" else -1)      # falling = the rising rule on mirrored prices (high <-> -low)
    lines = _lines(b, s, p, "support", edges=True)
    n = b.n
    out = LineSeries(direction, np.full(n, np.nan), np.full(n, np.nan), np.full(n, -1, dtype=int), np.full(n, -1, dtype=int),
                     np.full(n, np.nan))
    if not len(lines.A):
        return out
    for t in range(int(lines.B.min()) + lines.order + 1, n):
        line = lines.select(t)
        if line is None:
            continue
        out.value[t] = s.back(line.at(t))
        out.anchor_a[t], out.anchor_b[t] = line.a, line.b
        out.slope[t] = s.back(line.m)
        if not line.edge_broken < t:
            out.channel[t] = s.back(line.at(t) + line.edge)
    return out


def trendline_at(frame: pd.DataFrame, t: int, direction: str, **overrides: Any) -> Optional[Dict[str, Any]]:
    """The chart layer's finder (`trendlines.find_trendline` + `channel_of`) on the bars before t, projected to t:
    {value, channel, anchor_a, anchor_b, slope, touches} in real prices, or None. No age filter (the finder has none)."""
    from app.price_action import trendlines as tl
    if direction not in DIRECTIONS:
        raise ValueError(f"unknown line direction {direction!r} (rising / falling)")
    p = _params(overrides)
    cut = frame.iloc[:int(t)].reset_index(drop=True)
    rr = tl.ref_range(cut)
    side, sign = ("support", 1) if direction == "rising" else ("resistance", -1)
    line = tl.find_trendline(cut, rr, side, sign, order=int(p["tl_order"]), min_gap=int(p["tl_min_gap"]), max_slope_k=float(p["tl_max_slope"]),
                             tol=float(p["tl_touch_tol"]) * rr if np.isfinite(rr) else None, min_touches=int(p["tl_min_touches"]))
    if line is None:
        return None
    ch = tl.channel_of(line, cut)
    return {"value": line.at(t), "channel": line.at(t) + ch["offset"], "anchor_a": line.a, "anchor_b": line.b_idx, "slope": line.m,
            "touches": line.touches}


__all__ = ["REFERENCE", "DIRECTIONS", "LINE_PARAMS", "params_doc", "LineSeries", "line_series", "trendline_at"]
