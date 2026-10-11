"""One stored snapshot of a symbol's zones - detection + lifecycle + level memory across days, as a pure function.

`build_snapshot(frames, dynamic_frames, settings, previous)` is what the storage job (`job.py`) runs per symbol per night:
  1. compute_all_zones on the frames (bars that closed by the snapshot time only - the caller cuts them): order blocks,
     demand / supply, gaps with their lifecycle tracked from confirmation; S/R and dynamic S/R line levels;
  2. level memory (memory.apply_memory - Trade level_memory) per line family and timeframe, against the PREVIOUS
     snapshot's live levels: a level near a remembered one keeps the old price and formed time, an old level not found
     today stays while price visited it within the retire window;
  3. the lifecycle of every line level: a remembered level continues its stored tracker over the bars after the last one
     it saw (touch / flip / structural break), a new level starts fresh on the snapshot's last bar;
  4. role by the price and a stable `zone_key` per row (the identity across snapshots).
Nothing here reads the database or the clock: the previous snapshot comes in as rows, "now" is each frame's last bar.
"""
from typing import Any, Dict, List, Mapping, Optional, Tuple

import pandas as pd

from app.price_action.zones.compute import Frames, compute_all_zones, line_tracker, tracker_fields
from app.price_action.zones.detect import role_by_price
from app.price_action.zones.lifecycle import ZoneTracker, frame_swings, track_zone
from app.price_action.zones.memory import CLASSIC_PREFIX, DYNAMIC_PREFIX, apply_memory
from app.price_action.zones.settings import ZoneSettings

LINE_TYPES = {f"{p}{side}" for p in (CLASSIC_PREFIX, DYNAMIC_PREFIX) for side in ("SUPPORT", "RESISTANCE")}


def is_line(zone_type: str) -> bool:
    return zone_type in LINE_TYPES


def family_of(zone_type: str) -> str:
    """"DYNAMIC_SR" or "SR" for line levels, the zone type itself otherwise."""
    if not is_line(zone_type):
        return zone_type
    return "DYNAMIC_SR" if zone_type.startswith(DYNAMIC_PREFIX) else "SR"


def _iso(t: Any) -> str:
    return "" if t is None else pd.Timestamp(t).isoformat()


def zone_key(row: Mapping[str, Any]) -> str:
    """Identity across snapshots. A line level is its family + timeframe + price + first formation (memory keeps a
    remembered level's price and formed time exactly, and its support / resistance name may change with the price; a
    level found again after it was invalidated is a new one); a zone is its type + timeframe + formation bar + bounds."""
    zt, tf = str(row["zone_type"]), str(row["timeframe"])
    if is_line(zt):
        return f"{family_of(zt)}|{tf}|{float(row['zone_low']):.6f}|{_iso(row['formed_at'])}"
    return f"{zt}|{tf}|{_iso(row['formed_at'])}|{float(row['zone_low']):.6f}|{float(row['zone_high']):.6f}"


def _latest_close(frames: List[Optional[pd.DataFrame]]) -> Optional[float]:
    live = [f for f in frames if f is not None and len(f)]
    if not live:
        return None
    last = max(live, key=lambda f: pd.Timestamp(f["timestamp"].iloc[-1]))
    return float(last["close"].iloc[-1])


def _line_families(frames: Frames, dynamic_frames: Frames, s: ZoneSettings) -> List[Tuple[str, str, Optional[pd.DataFrame]]]:
    fams: List[Tuple[str, str, Optional[pd.DataFrame]]] = [(CLASSIC_PREFIX, s.sr_timeframe, frames.get(s.sr_timeframe))]
    fams += [(DYNAMIC_PREFIX, tf, dynamic_frames.get(tf)) for tf in s.dynamic_sr_timeframes]
    return fams


def build_snapshot(frames: Frames, dynamic_frames: Optional[Frames] = None, settings: Optional[ZoneSettings] = None,
                   previous: Optional[List[Mapping[str, Any]]] = None, current_price: Optional[float] = None) -> List[Dict[str, Any]]:
    """All zone rows of one snapshot (see the module doc). `frames` / `dynamic_frames` as compute_all_zones takes them,
    already holding only bars that closed by the snapshot time; `previous`: the previous snapshot's rows (with their
    "tracker"), or None for a first run. Each row gains "zone_key" and "remembered"."""
    s = settings if settings is not None else ZoneSettings()
    dyn = dict(dynamic_frames or {})
    rows = compute_all_zones(frames, dyn, s, current_price=current_price)
    prev = [dict(r) for r in (previous or [])]
    prev_line = {zone_key(r): r for r in prev if is_line(str(r["zone_type"])) and r.get("state") != "invalidated"}

    out = [r for r in rows if not is_line(str(r["zone_type"]))]
    for r in out:
        r["remembered"] = False
    for prefix, tf, df in _line_families(frames, dyn, s):
        types = (f"{prefix}SUPPORT", f"{prefix}RESISTANCE")
        mine = [r for r in rows if r["timeframe"] == tf and r["zone_type"] in types]
        if df is None or not len(df):
            out += [{**r, "remembered": False} for r in mine]
            continue
        df = df.reset_index(drop=True)
        now = df["timestamp"].iloc[-1]
        merged = apply_memory(mine, prev, {tf: df}, now, s, prefix=prefix) if prev and s.memory_enabled else \
            [{**r, "remembered": False} for r in mine]
        swings = frame_swings(df, s)
        for r in merged:
            r.setdefault("remembered", False)
            old = prev_line.get(zone_key(r))
            if old is not None and old.get("tracker"):
                tracker = ZoneTracker.from_dict(old["tracker"])
                r["confirmed_at"] = old.get("confirmed_at") or r.get("confirmed_at")
            else:
                tracker = line_tracker(float(r["zone_low"]), df, now, s)
            track_zone(tracker, df, s, swings=swings)
            r.update(tracker_fields(tracker))
            out.append(r)

    price = current_price if current_price is not None else _latest_close([*frames.values(), *dyn.values()])
    for r in out:
        r["role"] = role_by_price(float(r["zone_low"]), float(r["zone_high"]), price) if price is not None else None
        r["zone_key"] = zone_key(r)
    return out


def live_rows(rows: List[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    """The rows still worth drawing / trading against (any state but invalidated)."""
    return [r for r in rows if r.get("state") != "invalidated"]


def summary(rows: List[Mapping[str, Any]]) -> Dict[str, int]:
    """Count of rows per state (for the run record / logs)."""
    out: Dict[str, int] = {}
    for r in rows:
        out[str(r.get("state"))] = out.get(str(r.get("state")), 0) + 1
    return out
