"""All market zones for one symbol, as of a time - the Trade nightly market-zones computation as a pure function.

Ported from Trade@eafc872 market_zones.py (compute_all_zones, compute_intraday_sr_zones, compute_5m_15m_confluence_row /
_table). Which frame feeds which family is a setting (Trade's fixed choice is the default): classical S/R on
`sr_timeframe` (1h), dynamic S/R on each of `dynamic_sr_timeframes` (1m/5m/15m/30m/1h, from the recent frames the caller
passes), order blocks + demand/supply on `zone_timeframe` (1h), unfilled overnight gaps on `gap_timeframe` (15m).

Row: {"zone_type", "timeframe", "zone_low", "zone_high", "strength", "formed_at", "confirmed_at", "state", "first_touch",
"role", "origin_role", "touches", "flips", "last_event", "structure_level", "invalidated_at", "tracker"}. Differences from
Trade, all deliberate:
  * `state` is the ATP lifecycle (lifecycle.track_zone): fresh / tested / flipped / invalidated, tracked from the bar
    AFTER the zone is confirmed (an order block / base on its impulse bar, a gap on its first bar) - never earlier.
  * `first_touch` keeps Trade's FILLED moment exactly (scanned from the second bar after formation, as Trade does - so a
    touch before confirmation can appear here although the lifecycle ignores it): {"event": TOUCH / GAP_THROUGH, "bar",
    "time"} or None. Parity with Trade's ACTIVE / FILLED is checked on this field.
  * S/R and dynamic S/R rows are line levels: they start `fresh` as of the computation, with a tracker whose band is the
    level +/- the memory tolerance; the storage job (snapshot.build_snapshot) remembers them across days and advances
    their tracker over the following bars.
  * a dynamic S/R row's `formed_at` is the last bar of its own frame (Trade stamped every row with the 1H frame's last bar).
  * `role` (RESISTANCE_SUPPLY / SUPPORT_DEMAND) comes from the price for every row (Trade computed it in the callers).
`as_of` (a clock time): every frame is first cut to the bars that had CLOSED by then (stamp + bar length <= as_of, per
frame), so a zone as of T never uses a later bar - nor the unfinished part of a higher-timeframe bar. Stamps and as_of
may be naive (exchange time) or tz-aware (converted with settings.session_timezone).
"""
from typing import Any, Dict, List, Mapping, Optional

import numpy as np
import pandas as pd

from app.price_action.zones.detect import (DEMAND_ZONE, SUPPLY_ZONE, detect_demand_supply_zones, detect_order_blocks, find_overnight_gaps,
                                           first_touch_event, role_by_price)
from app.price_action.zones.levels import compute_dynamic_sr, find_support_resistance_levels
from app.price_action.zones.lifecycle import ZoneTracker, frame_swings, new_tracker, origin_side, track_zone
from app.price_action.zones.memory import zone_tolerance
from app.price_action.zones.settings import TF_MINUTES, ZoneSettings

Frames = Mapping[str, Optional[pd.DataFrame]]


def _s(settings: Optional[ZoneSettings]) -> ZoneSettings:
    return settings if settings is not None else ZoneSettings()


def exchange_time(ts: Any, tz: str) -> Any:
    """Stamps (a Series or one value) as naive exchange time: tz-aware -> converted to `tz` and made naive; naive -> taken
    as exchange time already. Lets tz-aware frames, naive frames and a naive or aware as_of be compared safely."""
    if isinstance(ts, pd.Series):
        t = pd.to_datetime(ts)
        return t.dt.tz_convert(tz).dt.tz_localize(None) if getattr(t.dt, "tz", None) is not None else t
    t1 = pd.Timestamp(ts)
    return t1.tz_convert(tz).tz_localize(None) if t1.tzinfo is not None else t1


def cut_as_of(df: Optional[pd.DataFrame], as_of: Any, timeframe: str, tz: str = "Asia/Kolkata") -> Optional[pd.DataFrame]:
    """The bars of a `timeframe` frame that had CLOSED by `as_of` (None -> unchanged). Bars are stamped at their open, so a
    bar counts once stamp + bar length <= as_of: as of 14:00 a 1h bar stamped 14:00 (15m data to 14:59) is not used,
    nor a 15m bar stamped 13:50. Conservative at a session's short last bar (a 1h bar stamped 15:00 counts from 16:00)."""
    if df is None or as_of is None:
        return df
    if timeframe not in TF_MINUTES:
        raise ValueError(f"unknown timeframe {timeframe!r} - its bar length is needed to cut as of a time")
    closes = exchange_time(df["timestamp"], tz) + pd.Timedelta(minutes=TF_MINUTES[timeframe])
    return df[(closes <= exchange_time(as_of, tz)).to_numpy()]


def _cut_all(frames: Optional[Frames], as_of: Any, tz: str) -> Dict[str, Optional[pd.DataFrame]]:
    return {tf: cut_as_of(df, as_of, tf, tz) for tf, df in (frames or {}).items()}


def tracker_fields(tracker: ZoneTracker) -> Dict[str, Any]:
    """The lifecycle columns of a zone row from its tracker."""
    return {"state": tracker.state, "origin_role": tracker.origin_side, "touches": tracker.touches, "flips": tracker.flips,
            "last_event": tracker.last_event, "structure_level": tracker.structure, "invalidated_at": tracker.invalidated_at,
            "tracker": tracker.to_dict()}


def _state_row(zone: Dict[str, Any], timeframe: str, df: pd.DataFrame, trade_after: pd.DataFrame, settings: ZoneSettings,
               swings: Any) -> Dict[str, Any]:
    """`df` is the timeframe's frame (reset index); `trade_after` the slice Trade scans for its first touch."""
    ev = first_touch_event(zone["zone_low"], zone["zone_high"], trade_after)
    if ev is not None:
        ev = {**ev, "bar": int(trade_after.index[ev["bar"]])}
    tracker = new_tracker(zone["zone_low"], zone["zone_high"], origin_side(zone["zone_type"], zone["zone_low"], zone["zone_high"], None))
    track_zone(tracker, df, settings, start_after=zone.get("confirmed_at", zone["formed_at"]), swings=swings)
    return {"zone_type": zone["zone_type"], "timeframe": timeframe, "zone_low": zone["zone_low"], "zone_high": zone["zone_high"],
            "strength": None, "formed_at": zone["formed_at"], "confirmed_at": zone.get("confirmed_at"), "first_touch": ev,
            **tracker_fields(tracker)}


def line_tracker(level: float, df: pd.DataFrame, formed_at: Any, settings: ZoneSettings) -> ZoneTracker:
    """A fresh tracker for a line level known on `formed_at`: band = level +/- the memory tolerance of its frame, side by
    the frame's last close."""
    price = float(df["close"].iloc[-1])
    return new_tracker(level, level, origin_side("LEVEL", level, level, price), tol=zone_tolerance(df, price, settings), start_at=formed_at)


def _level_rows(levels: Dict[str, Any], prefix: str, timeframe: str, df: pd.DataFrame, settings: ZoneSettings) -> List[Dict[str, Any]]:
    rows = []
    formed_at = df["timestamp"].iloc[-1]
    for side, name in (("support", "SUPPORT"), ("resistance", "RESISTANCE")):
        for lv in levels.get(side, []):
            tracker = line_tracker(lv["level"], df, formed_at, settings)
            rows.append({"zone_type": f"{prefix}{name}", "timeframe": timeframe, "zone_low": lv["level"], "zone_high": lv["level"],
                         "strength": lv["touches"], "formed_at": formed_at, "confirmed_at": formed_at, "first_touch": None,
                         **tracker_fields(tracker)})
    return rows


def _latest_close(frames: List[Optional[pd.DataFrame]], tz: str) -> Optional[float]:
    live = [f for f in frames if f is not None and len(f)]
    if not live:
        return None
    last = max(live, key=lambda f: exchange_time(f["timestamp"].iloc[-1], tz))
    return float(last["close"].iloc[-1])


def dynamic_sr_rows(dynamic_frames: Frames, timeframes: List[str], settings: Optional[ZoneSettings] = None) -> List[Dict[str, Any]]:
    """DYNAMIC_SR_SUPPORT / DYNAMIC_SR_RESISTANCE rows for each timeframe with at least dyn_min_bars bars."""
    s = _s(settings)
    rows: List[Dict[str, Any]] = []
    for tf in timeframes:
        df = dynamic_frames.get(tf)
        if df is None or len(df) < s.dyn_min_bars:
            continue
        rows += _level_rows(compute_dynamic_sr(df, s), "DYNAMIC_SR_", tf, df.reset_index(drop=True), s)
    return rows


def compute_all_zones(frames: Frames, dynamic_frames: Optional[Frames] = None, settings: Optional[ZoneSettings] = None,
                      as_of: Any = None, current_price: Optional[float] = None) -> List[Dict[str, Any]]:
    """frames: {timeframe: bars} holding at least settings.sr_timeframe / zone_timeframe / gap_timeframe (long history).
    dynamic_frames: {timeframe: recent bars} for dynamic S/R (Trade passed ~20 days per timeframe; its 15m fell back to
    the long 15m history when no recent frame was given - a caller choice here). Missing frames skip their family."""
    s = _s(settings)
    frames = _cut_all(frames, as_of, s.session_timezone)
    dyn = _cut_all(dynamic_frames, as_of, s.session_timezone)
    rows: List[Dict[str, Any]] = []

    df_sr = frames.get(s.sr_timeframe)
    if df_sr is not None and len(df_sr) >= s.sr_min_bars:
        rows += _level_rows(find_support_resistance_levels(df_sr, settings=s), "", s.sr_timeframe, df_sr.reset_index(drop=True), s)

    rows += dynamic_sr_rows(dyn, s.dynamic_sr_timeframes, s)

    df_z = frames.get(s.zone_timeframe)
    if df_z is not None and len(df_z):
        df_z = df_z.reset_index(drop=True)
        ts = pd.to_datetime(df_z["timestamp"])
        sw = frame_swings(df_z, s)
        for zone in detect_order_blocks(df_z, s) + detect_demand_supply_zones(df_z, s):
            after = df_z[ts > pd.Timestamp(zone["formed_at"])].iloc[1:]           # Trade skips the first bar after formation
            rows.append(_state_row(zone, s.zone_timeframe, df_z, after, s, sw))

    df_g = frames.get(s.gap_timeframe)
    if df_g is not None and len(df_g) >= 2:
        df_g = df_g.reset_index(drop=True)
        ts = pd.to_datetime(df_g["timestamp"])
        sw = frame_swings(df_g, s)
        for gap in find_overnight_gaps(df_g, s):
            rows.append(_state_row(gap, s.gap_timeframe, df_g, df_g[ts > pd.Timestamp(gap["formed_at"])], s, sw))

    price = current_price if current_price is not None else _latest_close([df_z, df_sr, df_g, *dyn.values()], s.session_timezone)
    for row in rows:
        row["role"] = role_by_price(row["zone_low"], row["zone_high"], price) if price is not None else None
    return rows


def compute_intraday_sr_zones(dynamic_frames: Frames, settings: Optional[ZoneSettings] = None, as_of: Any = None) -> List[Dict[str, Any]]:
    """Only the intraday dynamic S/R rows (settings.intraday_sr_timeframes, default 15m / 30m / 1h) - Trade's separate
    intraday refresh that never touches the other zone types."""
    s = _s(settings)
    dyn = _cut_all(dynamic_frames, as_of, s.session_timezone)
    rows = dynamic_sr_rows(dyn, s.intraday_sr_timeframes, s)
    for row in rows:
        df = dyn[row["timeframe"]]
        row["role"] = role_by_price(row["zone_low"], row["zone_high"], float(df["close"].iloc[-1])) if df is not None else None
    return rows


def _nearest_n(levels: List[float], price: float, want_below: bool, n: int) -> List[float]:
    side = [lv for lv in levels if (lv <= price if want_below else lv >= price)]
    side.sort(key=lambda lv: abs(lv - price))
    return side[:n]


def _untouched(df: pd.DataFrame, zones: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The zones with no first-touch event yet (Trade's "not mitigated"), checked from the second bar after formation."""
    ts = pd.to_datetime(df["timestamp"])
    out = []
    for z in zones:
        after = df[ts > pd.Timestamp(z["formed_at"])].iloc[1:]
        if first_touch_event(z["zone_low"], z["zone_high"], after) is None:
            out.append(z)
    return out


def compute_confluence_row(timeframe: str, df: Optional[pd.DataFrame], current_price: float,
                           settings: Optional[ZoneSettings] = None) -> Dict[str, Any]:
    """One row of the 5m / 15m confluence table: the nearest confluence_levels_per_side classical supports / resistances
    (fresh swing clusters of this timeframe), the nearest fresh (untouched) demand zone, supply zone and order block.
    Raw numbers (levels rounded to 2 decimals, distances in % of the price to 3)."""
    s = _s(settings)
    price = float(current_price)
    row: Dict[str, Any] = {"timeframe": timeframe, "current_price": round(price, 2),
                           "demand_zone_low": None, "demand_zone_high": None, "demand_zone_distance_pct": None,
                           "supply_zone_low": None, "supply_zone_high": None, "supply_zone_distance_pct": None,
                           "order_block_type": None, "order_block_low": None, "order_block_high": None, "order_block_distance_pct": None}
    for n in range(1, s.confluence_levels_per_side + 1):
        suffix = "" if n == 1 else f"_{n}"
        for key in ("support_level", "support_distance_pct", "resistance_level", "resistance_distance_pct"):
            row[f"{key}{suffix}"] = None

    def pct(x: float) -> float:
        return round((x - price) / price * 100, 3)

    if df is not None and len(df) >= s.confluence_swing_order * 2 + 1:
        sr = find_support_resistance_levels(df, order=s.confluence_swing_order, top_n=s.confluence_sr_top_n, settings=s)
        for name, want_below in (("support", True), ("resistance", False)):
            near = _nearest_n([c["level"] for c in sr[name]], price, want_below, s.confluence_levels_per_side)
            for i, level in enumerate(near):
                suffix = "" if i == 0 else f"_{i + 1}"
                # Trade rounds these numpy floats with numpy rounding (the zone figures below are Python floats)
                row[f"{name}_level{suffix}"] = float(np.round(level, 2))
                row[f"{name}_distance_pct{suffix}"] = float(np.round((level - price) / price * 100, 3))

    if df is not None and len(df) >= s.avg_window + 1:
        df = df.reset_index(drop=True)
        fresh_ds = _untouched(df, detect_demand_supply_zones(df, s))
        for name, kind in (("demand_zone", DEMAND_ZONE), ("supply_zone", SUPPLY_ZONE)):
            pool = [((z["zone_low"] + z["zone_high"]) / 2, z) for z in fresh_ds if z["zone_type"] == kind]
            if pool:
                mid, z = min(pool, key=lambda x: abs(x[0] - price))
                row[f"{name}_low"], row[f"{name}_high"] = round(z["zone_low"], 2), round(z["zone_high"], 2)
                row[f"{name}_distance_pct"] = pct(mid)
        obs = [((z["zone_low"] + z["zone_high"]) / 2, z) for z in _untouched(df, detect_order_blocks(df, s))]
        if obs:
            mid, ob = min(obs, key=lambda x: abs(x[0] - price))
            row["order_block_type"] = ob["zone_type"]
            row["order_block_low"], row["order_block_high"] = round(ob["zone_low"], 2), round(ob["zone_high"], 2)
            row["order_block_distance_pct"] = pct(mid)
    return row


def compute_confluence_table(current_price: float, frames: Frames, settings: Optional[ZoneSettings] = None,
                             as_of: Any = None) -> List[Dict[str, Any]]:
    """One confluence row per settings.confluence_timeframes entry (frames[tf] may be missing -> an empty row)."""
    s = _s(settings)
    return [compute_confluence_row(tf, cut_as_of(frames.get(tf), as_of, tf, s.session_timezone), current_price, s) for tf in s.confluence_timeframes]
