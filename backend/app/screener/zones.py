"""Z7a-3: the ScreenQL market-zone functions - NearZone, ZoneRole, ZoneState, ZoneStrength - and their flag gate.

* Data: the stored zone snapshots (Z7a-2, `app/price_action/zones`), never the bars of the screen. A bar sees the
  snapshot `zones_as_of` returns at that bar's CLOSE (the tenant's scope; `read.timeline`), so a value at bar t never
  uses a zone computed from a later bar; appending or changing later bars or snapshots leaves it unchanged (tested).
  Role (demand / supply) comes from the bar's close; state, touches and strength are the snapshot's.
* Each function reads the live zone nearest to the close within `tolerance_pct` % of it (`chart.nearest_zone` - the
  same code the chart layer uses): NearZone = there is one; ZoneRole / ZoneState = its role / state (missing when
  there is none, so it never matches); ZoneStrength = its 0-100 strength (NaN when none).
* Flag `market_zones` (off by default), the U6 BK1 pattern: the functions stay in the one registry (so the validator,
  the runtime and the parity tests see them), but while the flag is off for a tenant the registry endpoint hides them
  and a screen, a saved screen or an alert rule using one is refused with the reason; an active alert rule using one
  pauses (stays active, `last_problem` says why) and resumes when the flag is back. Zone rules fire on bar close only.
  Research only - matches, never orders.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from app.price_action.zones.chart import FAMILIES
from app.screener import nodes
from app.screener.registry import ZONES_FLAG as FLAG
ROLE_CHOICES = ("any", "demand", "supply")
WHAT = ("near", "role", "state", "strength")
MAX_TOLERANCE_PCT = 20.0


# ------------------------------------------------------------------------------------------------------------- the gate
def flagged_calls(ast: Any) -> List[str]:
    """Zone functions used in a screen (each once, in order)."""
    from app.screener.registry import FUNCTIONS
    out: List[str] = []
    if ast is None:
        return out
    for node in nodes.walk(ast):
        spec = FUNCTIONS.get(node.name) if isinstance(node, nodes.Call) else None
        if spec is not None and spec.flag == FLAG and spec.name not in out:
            out.append(spec.name)
    return out


def refusal(names: List[str]) -> str:
    return f"{', '.join(n + '()' for n in names)}: market zones are not enabled for this organisation (flag {FLAG})"


def intrabar_refusal(names: List[str]) -> str:
    return f"{', '.join(n + '()' for n in names)}: market-zone alerts fire on bar close only, not intrabar"


async def enabled(session: AsyncSession, tenant_id: Any) -> bool:
    from app.platform.controls import flag_enabled
    return await flag_enabled(session, FLAG, tenant_id)


async def blocked_calls(session: AsyncSession, tenant_id: Any, ast: Any) -> List[str]:
    """The zone functions a screen uses while the flag is off for the tenant (empty: nothing to refuse)."""
    names = flagged_calls(ast)
    if not names or await enabled(session, tenant_id):
        return []
    return names


# ---------------------------------------------------------------------------------------------------------- the data
def instrument_key(symbol: str, exchange: str = "NSE") -> Optional[str]:
    """The lake key of a screen symbol ("NSE:RELIANCE"); None when it cannot be one (nothing stored for it)."""
    from app.price_action.zones.store import normalise_key
    sym = str(symbol or "").strip().upper()
    try:
        return normalise_key(sym if ":" in sym else f"{(exchange or 'NSE').strip().upper()}:{sym}")
    except ValueError:
        return None


def zone_lookback(ast: Any, base_tf: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, int]:
    """The bars the ZONE calls of a screen are read over, per timeframe - the window of snapshots to load. Each top-level
    ALL / ANY / NOT branch that holds a zone call is measured on its own (so `Count(5, NearZone()) AND SMA(close, 200) >
    close` loads 5 bars of zones, not 200); a branch that mixes is measured whole (a safe over-estimate)."""
    from app.screener.validator import validate
    out: Dict[str, int] = {}

    def visit(node: Any) -> None:
        if not flagged_calls(node):
            return
        if isinstance(node, nodes.Logic):
            for item in node.items:
                visit(item)
            return
        if isinstance(node, nodes.Not):
            visit(node.item)
            return
        for tf, bars in validate(node, base_tf=base_tf, params=params).lookback.items():
            out[tf] = max(out.get(tf, 0), int(bars))
    visit(ast)
    return out or {base_tf: 1}


async def attach_zones(session: AsyncSession, tenant_id: Optional[int], exchange: str, universe: List[Any], end: Any,
                       ast: Any, base_tf: str, params: Optional[Dict[str, Any]] = None) -> None:
    """Loads every symbol's zone timeline in one batched read (read.timelines: the tenant's scope, the snapshots its bars
    can see, as of `end`, over the zone calls' own window) onto its SymbolData. A symbol that cannot be an instrument
    key gets an empty timeline (its zone functions then explain)."""
    from app.price_action.zones import read
    start = read.window_start(zone_lookback(ast, base_tf, params), end)
    keys = {data.symbol: instrument_key(data.symbol, exchange) for data in universe}
    found = await read.timelines(session, sorted({k for k in keys.values() if k}), tenant_id, start, end)
    for data in universe:
        key = keys[data.symbol]
        data.zones = found.get(key) if key else None
        if data.zones is None:
            data.zones = read.ZoneTimeline(key or data.symbol)


# ------------------------------------------------------------------------------------------------------- the runtime
def _closes_ns(index: pd.Index, tf: str) -> np.ndarray:
    from app.screener.runtime import bar_close
    closes = pd.DatetimeIndex(bar_close(pd.DatetimeIndex(index), tf))
    closes = closes.tz_localize("UTC") if closes.tz is None else closes.tz_convert("UTC")
    return np.asarray(closes.asi8, dtype="int64")


def zone_series(data: Any, f: pd.DataFrame, tf: str, what: str, role: str = "any", tolerance_pct: float = 0.5,
                family: str = "all") -> pd.Series:
    """One zone function on every bar of `f`: the snapshot as of the bar's close, the nearest live zone to the close."""
    from app.screener.runtime import ScreenRuntimeError
    if what not in WHAT:
        raise ScreenRuntimeError(f"unknown zone measure {what!r}")
    if role not in ROLE_CHOICES:
        raise ScreenRuntimeError(f"unknown zone role {role!r} (one of {', '.join(ROLE_CHOICES)})")
    if family not in FAMILIES:
        raise ScreenRuntimeError(f"unknown zone family {family!r} (one of {', '.join(FAMILIES)})")
    tol = float(tolerance_pct)
    if not (math.isfinite(tol) and 0 <= tol <= MAX_TOLERANCE_PCT):
        raise ScreenRuntimeError(f"zone tolerance_pct must be between 0 and {MAX_TOLERANCE_PCT:g}, got {tolerance_pct!r}")
    timeline = getattr(data, "zones", None)
    if timeline is None:
        raise ScreenRuntimeError(f"{data.symbol}: market zones were not loaded for this run")
    if timeline.empty:
        raise ScreenRuntimeError(f"{data.symbol}: no stored market zones (the zone job does not follow this instrument yet)")
    if timeline.truncated:
        raise ScreenRuntimeError(f"{data.symbol}: the zone calls read more stored snapshots than a run loads "
                                 f"({len(timeline.snapshots)} at most) - shorten their window")
    idx = timeline.index_at(_closes_ns(f.index, tf)) if len(f) else np.array([], dtype=int)
    closes = f["close"].astype(float).to_numpy()
    if what == "near":
        out: np.ndarray = np.zeros(len(f), dtype=bool)
    elif what == "strength":
        out = np.full(len(f), np.nan)
    else:
        out = np.full(len(f), None, dtype=object)
    # vectorised per snapshot segment: the bars that see one snapshot against all its zones at once (chart.nearest_zone's
    # rule - distance, then strength, then zone_key - with the zones pre-ordered so argmin's first minimum is that pick)
    for i in np.unique(idx[idx >= 0]):
        snap = timeline.snapshots[int(i)]
        if snap is None:
            continue
        a = snap.arrays(family)
        if not len(a["low"]):
            continue
        bars = np.flatnonzero((idx == i) & np.isfinite(closes) & (closes > 0))
        if not len(bars):
            continue
        c = closes[bars][:, None]
        low, high = a["low"][None, :], a["high"][None, :]
        gap = np.where(c < low, low - c, np.where(c > high, c - high, 0.0))
        dist = gap / c * 100.0
        supply = (low + high) / 2 > c
        ok = dist <= tol
        if role == "demand":
            ok &= ~supply
        elif role == "supply":
            ok &= supply
        hit = ok.any(axis=1)
        if not hit.any():
            continue
        best = np.argmin(np.where(ok, dist, np.inf), axis=1)
        rows_hit, cols = bars[hit], best[hit]
        if what == "near":
            out[rows_hit] = True
        elif what == "role":
            out[rows_hit] = np.where(supply[hit, cols], "SUPPLY", "DEMAND")
        elif what == "state":
            out[rows_hit] = a["state"][cols]
        else:
            out[rows_hit] = a["strength"][cols]
    if what == "near":
        return pd.Series(out, index=f.index, dtype=bool)
    if what == "strength":
        return pd.Series(out, index=f.index, dtype=float)
    return pd.Series(out, index=f.index, dtype=object)


__all__ = ["FLAG", "flagged_calls", "refusal", "intrabar_refusal", "enabled", "blocked_calls", "attach_zones", "zone_series",
           "zone_lookback", "instrument_key"]
