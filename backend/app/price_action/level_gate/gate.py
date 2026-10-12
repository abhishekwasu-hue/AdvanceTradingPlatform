"""N1 L1: the level gate's state machine, ported from Trade `bots_common/level_reaction.py` (pinned revision in
`settings.TRADE_REVISION`). Logic unchanged; messages in English.

Per (symbol, area), replayed every cycle from the session's CLOSED bars (restart-safe and causal: the decision at bar
t uses bars <= t only):
  IDLE -> ARMED      a closed candle overlaps the area, the previous close was on the trade side (above a demand area,
                     below a supply area), and the Daily trend allows that side
       -> CONFIRMED  a commitment candle (`method.commitment` with the study window), no impulsive approach, a close
                     beyond the area's inner edge, R:R >= `min_rr` - `entry` is True only on that candle itself
       -> DISARMED   a real break of the far edge (`breaks.break_from`: no breakout or reverse trade that day in that
                     role), timeout (`max_armed_bars`), impulsive approach, R:R below the minimum, no target area,
                     against the trend
Fail-closed (`safe_evaluate`): any error means no entry, with the reason. Places no order; reads no broker.
"""
from typing import Any, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple

import numpy as np
import pandas as pd

from app.price_action import breaks as BR
from app.price_action.level_gate import method as M
from app.price_action.level_gate.settings import BREAK_DEFAULTS, METHOD_DEFAULTS, gate_settings

BULL, BEAR = "BULLISH", "BEARISH"
IDLE, ARMED, CONFIRMED, DISARMED = "IDLE", "ARMED", "CONFIRMED", "DISARMED"

# Reasons (signal log, daily summary) - fixed words, the same as Trade's.
R_AGAINST = "ignored: against trend"
R_NO_TREND = "no entry: trend unclear"
R_IMPULSIVE = "impulsive approach"
R_NO_COMMIT = "no commitment"
R_BROKEN = "broken"
R_TIMEOUT = "timeout"
R_RR = "R:R < min"
R_NO_TARGET = "no target area"
R_ERROR = "gate error (fail-closed)"
R_ABSORPTION = "absorption (counter side strengthening inside the area)"
R_LOW_VOL = "commitment on low volume"
R_NO_BARS = "no closed candles today"

_FORMS = {"engulf": ("bullish engulfing", "bearish engulfing"), "pin": ("hammer / pin", "shooting star / pin"),
          "star": ("morning star", "evening star"), "strong_close": ("strong bullish close", "strong bearish close"),
          "strong_close_2": ("strong bullish close (2 candles)", "strong bearish close (2 candles)"),
          "inside_break": ("inside-bar break up", "inside-bar break down"), "doji_confirm": ("doji + bullish confirm", "doji + bearish confirm"),
          "tweezer": ("tweezer bottom", "tweezer top")}

Area = Tuple[float, float]


class Pivot(Protocol):
    price: float


class DailyState(Protocol):
    """The Daily trend state the gate reads (Trade `decision3.daily.DState`; the fold itself is N1 L2)."""
    trend: str
    band: Optional[Tuple[float, float]]
    phase: Optional[str]
    counter: Optional[str]
    protected: Optional[Pivot]
    origin_risk: bool
    mature: bool


def form_name(form: Optional[str], side: Optional[str]) -> str:
    if form is None:
        return ""
    pair = _FORMS.get(form)
    return pair[0 if side == BULL else 1] if pair else str(form)


# --- (1) trend -> allowed sides --------------------------------------------------------------------------------------

def context_from_state(st: DailyState, s: Optional[Mapping[str, Any]] = None, asof: Optional[str] = None) -> Dict[str, Any]:
    """A Daily state -> which sides may enter. UP: bullish only; DOWN: bearish only; RANGE: both edges; NEUTRAL /
    UNKNOWN, the protected origin closed through, the origin at risk (Q47), or a parent correction (unless allowed):
    no new entries."""
    gs = gate_settings(s)
    ctx: Dict[str, Any] = {"ok": False, "trend": st.trend, "sides": set(), "band": st.band, "phase": st.phase, "counter": st.counter,
                           "protected": None if st.protected is None else round(float(st.protected.price), 2), "asof": asof,
                           "origin_risk": bool(st.origin_risk), "mature": bool(st.mature)}
    if st.trend in ("UP", "DOWN"):
        if st.phase == "origin_broken":
            ctx["reason"] = f"{R_NO_TREND} (Daily {st.trend}: the protected origin was broken by a close)"
        elif st.origin_risk:
            ctx["reason"] = f"{R_NO_TREND} (Daily {st.trend}: origin at risk - Q47)"
        elif st.counter == "correction" and not gs["allow_parent_correction"]:
            ctx["reason"] = f"{R_NO_TREND} (Daily {st.trend}: impulse origin broken, a parent correction is running)"
        else:
            ctx.update(ok=True, sides={BULL} if st.trend == "UP" else {BEAR}, reason=f"Daily {st.trend}")
    elif st.trend == "RANGE" and st.band:
        ctx.update(ok=True, sides={BULL, BEAR}, reason=f"Daily RANGE {st.band[0]:,.0f}–{st.band[1]:,.0f} (both edges)")
    else:
        ctx["reason"] = f"{R_NO_TREND} (Daily {st.trend})"
    return ctx


def side_allowed(ctx: Optional[Mapping[str, Any]], side: str, area: Area) -> bool:
    """Does the trend allow this side at this area? RANGE: bullish in the lower half, bearish in the upper half (Q38)."""
    if not ctx or not ctx.get("ok") or side not in ctx.get("sides", ()):
        return False
    if ctx.get("trend") == "RANGE" and ctx.get("band"):
        mid = (ctx["band"][0] + ctx["band"][1]) / 2.0
        am = (area[0] + area[1]) / 2.0
        return am <= mid if side == BULL else am >= mid
    return True


# --- bars -------------------------------------------------------------------------------------------------------------

def closed_bars(df: Optional[pd.DataFrame], minutes: int, now: Any) -> Optional[pd.DataFrame]:
    """Only completed candles (timestamp = candle start; start + minutes <= now), in IST wall time."""
    if df is None or len(df) == 0:
        return df
    ts = pd.to_datetime(df["timestamp"])
    if getattr(ts.dt, "tz", None) is not None:
        ts = ts.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    now = pd.Timestamp(now)
    if now.tzinfo is not None:
        now = now.tz_convert("Asia/Kolkata").tz_localize(None)
    out = df.assign(timestamp=ts)
    return out[ts + pd.Timedelta(minutes=int(minutes)) <= now].sort_values("timestamp").reset_index(drop=True)


def _arrays(bars: pd.DataFrame) -> Dict[str, Any]:
    A: Dict[str, Any] = {k: bars[k].to_numpy(float) for k in ("open", "high", "low", "close")}
    A["volume"] = bars["volume"].to_numpy(float) if "volume" in bars.columns else None
    return A


def median_ranges(A: Mapping[str, Any], n: int) -> np.ndarray:
    r = pd.Series(A["high"] - A["low"])
    return r.shift(1).rolling(int(n), min_periods=max(3, int(n) // 2)).median().to_numpy(float)


def _impulsive_approach(A: Mapping[str, Any], t: int, side: str, mr: np.ndarray, s: Mapping[str, Any]) -> bool:
    """(a) consecutive wide candles (>= approach_wide_mr x median range) closing at the approach-side end."""
    run, best = 0, 0
    for i in range(max(0, t - int(s["approach_bars"]) + 1), t + 1):
        m = mr[i]
        rng = A["high"][i] - A["low"][i]
        if not np.isfinite(m) or m <= 0 or rng <= 0:
            run = 0
            continue
        # the approach runs against the trade: bullish (support) -> coming down -> close near the low
        loc = (A["close"][i] - A["low"][i]) / rng if side == BULL else (A["high"][i] - A["close"][i]) / rng
        down = (A["close"][i] - A["open"][i]) * (1 if side == BEAR else -1) > 0
        if rng >= float(s["approach_wide_mr"]) * m and loc <= float(s["approach_close_frac"]) and down:
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best >= int(s["approach_min_wide"])


def _target(side: str, entry: float, area: Area, others: Optional[Iterable[Sequence[float]]]) -> Optional[float]:
    """The next opposite area: bullish -> the nearest area's low above the entry; bearish -> the nearest area's high below."""
    best: Optional[float] = None
    for lo, hi in others or ():
        if hi >= area[0] and lo <= area[1]:
            continue                                                       # the same / an overlapping area
        x = lo if side == BULL else hi
        if (x - entry) * (1 if side == BULL else -1) <= 0:
            continue
        if best is None or abs(x - entry) < abs(best - entry):
            best = x
    return best


# --- the state machine ------------------------------------------------------------------------------------------------

def evaluate(bars: pd.DataFrame, area: Sequence[float], ctx: Optional[Mapping[str, Any]], others: Iterable[Sequence[float]] = (),
             session_start: int = 0, s: Optional[Mapping[str, Any]] = None, s3: Optional[Mapping[str, Any]] = None,
             es: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Replays today's closed bars for one area. `bars` = DataFrame (timestamp, open, high, low, close[, volume]); earlier
    days' bars only feed the median range; `session_start` = today's first bar index. `ctx` = `context_from_state`.
    `others` = the other areas [(lo, hi)] (targets). Returns state, entry (CONFIRMED on the last bar?), side, reason,
    events [(i, state, reason)] and, once a commitment is found, entry_price / sl / target / rr / form / grade / evidence."""
    gs = gate_settings(s)
    s3 = s3 or METHOD_DEFAULTS
    es = es or BREAK_DEFAULTS
    lo, hi = float(min(area)), float(max(area))
    n = len(bars)
    out: Dict[str, Any] = {"state": IDLE, "entry": False, "side": None, "reason": "", "events": [], "area": (lo, hi), "bars_armed": 0,
                           "max_armed_bars": int(gs["max_armed_bars"])}
    if n == 0 or session_start >= n:
        out["reason"] = R_NO_BARS
        return out
    A = _arrays(bars)
    mr = median_ranges(A, gs["mr_n"])
    frame = bars.reset_index(drop=True)
    state: str = IDLE
    side: Optional[str] = None
    arm: Optional[int] = None
    broken, reason = False, ""
    broken_side: Optional[str] = None                                      # the role the area truly broke in today
    commit_at: Optional[int] = None
    info: Dict[str, Any] = {}
    swept = False
    lvl = {"lo": lo, "hi": hi}
    events: List[Tuple[int, str, str]] = out["events"]

    def ev(i: int, st_: str, why: str) -> None:
        events.append((int(i), st_, why))

    for t in range(int(session_start), n):
        o, h, low, c = A["open"][t], A["high"][t], A["low"][t], A["close"][t]
        overlap = low <= hi and h >= lo
        if state != IDLE and side is not None and side != broken_side:     # every visit: a real break of the far edge?
            d = M.UP if side == BULL else M.DOWN
            far_side = "below" if side == BULL else "above"
            far = lo if side == BULL else hi
            if (c - far) * d < 0:                                          # closed beyond the far edge
                r = BR.break_from(frame, t, far, far_side, dict(es), mr=None)
                swept = True                                               # beyond the far edge (a sweep if not a break)
                if r is not None and r != -1:
                    state, reason, broken, broken_side = DISARMED, R_BROKEN, True, side
                    ev(r, DISARMED, R_BROKEN)
                    out["broken_at"] = int(r)
                    continue
                if r == -1:
                    out["break_pending"] = True                            # undecided (the next bars are not there yet)
                continue                                                   # a false break (reclaimed): commitment later
        if state in (CONFIRMED, DISARMED):
            if not overlap:
                state, side, arm, swept, commit_at = IDLE, None, None, False, None   # left the area: the next touch is new
            continue
        if state == IDLE:
            if not overlap:
                continue
            pc = A["close"][t - 1] if t > 0 else o
            if pc > hi:
                sd = BULL                                                  # came from above: support / demand
            elif pc < lo:
                sd = BEAR                                                  # came from below: resistance / supply
            else:
                continue                                                   # already inside: not an arrival
            if sd == broken_side:
                continue                                                   # broke today in this role: not again (a flip is fine)
            if not side_allowed(ctx, sd, (lo, hi)):
                reason = R_AGAINST if (ctx or {}).get("ok") else (ctx or {}).get("reason", R_NO_TREND)
                ev(t, DISARMED, reason)
                state, side, arm = DISARMED, sd, t
            else:
                state, side, arm, swept = ARMED, sd, t, False
                for key in ("absorbing", "low_vol_logged", "impulsive"):
                    info.pop(key, None)                                    # a new visit: no flags carried over
                ev(t, ARMED, "price arrived")
                if _impulsive_approach(A, t, sd, mr, gs):
                    info["impulsive"] = True
            d = M.UP if side == BULL else M.DOWN
            far = lo if side == BULL else hi
            if (c - far) * d < 0 or (o - far) * d < 0:                     # the arrival candle itself beyond the far edge
                r = BR.break_from(frame, t, far, "below" if side == BULL else "above", dict(es), mr=None)
                swept = True
                if r is not None and r != -1:
                    state, reason, broken, broken_side = DISARMED, R_BROKEN, True, side
                    ev(r, DISARMED, R_BROKEN)
                    out["broken_at"] = int(r)
                    continue
                if r == -1:
                    out["break_pending"] = True
                continue
        if state != ARMED or arm is None or side is None:      # (armed always has both; the check is for the type checker)
            continue
        d = M.UP if side == BULL else M.DOWN
        far = lo if side == BULL else hi
        if (low < far and side == BULL) or (h > far and side == BEAR):
            swept = True
        if info.get("impulsive"):
            state, reason = DISARMED, R_IMPULSIVE
            ev(t, DISARMED, R_IMPULSIVE)
            info.pop("impulsive", None)
            continue
        if commit_at is not None:                                          # wait_next_candle: the candle after the commitment
            if (c - (hi if side == BULL else lo)) * d > 0:
                state = CONFIRMED
                ev(t, CONFIRMED, "commitment + next candle held")
                out.update(info.get("plan", {}))
                if t == n - 1:
                    out["entry"] = True
            else:
                state, reason, commit_at = DISARMED, R_NO_COMMIT + " (next candle back inside)", None
                ev(t, DISARMED, reason)
            continue
        if t - 1 >= arm:                                                   # a2: counter side strengthening (absorption) - wait
            zp = M.zone_study(A, arm, t - 1, d, s3, mr=mr[t])
            if zp.get("absorption"):
                if not info.get("absorbing"):
                    ev(t, ARMED, R_ABSORPTION)
                info["absorbing"] = True
                if t - arm + 1 >= int(gs["max_armed_bars"]):
                    state, reason = DISARMED, R_TIMEOUT
                    ev(t, DISARMED, R_TIMEOUT)
                continue
            info["absorbing"] = False
        ok, _why, cm = M.commitment(A, t, d, lvl, s3, None, win=arm)
        if not ok and cm.get("wait") == "low_volume" and not info.get("low_vol_logged"):
            ev(t, ARMED, R_LOW_VOL)                                        # c0: dominant but low volume - wait until timeout
            info["low_vol_logged"] = True
        inner_ok = (c - (hi if side == BULL else lo)) * d > 0              # closed beyond the area's inner edge
        if ok and inner_ok:
            m = mr[t] if np.isfinite(mr[t]) else 0.0
            seg_l, seg_h = A["low"][arm:t + 1].min(), A["high"][arm:t + 1].max()
            sl = (min(lo, seg_l) - float(gs["sl_buffer_mr"]) * m) if side == BULL else (max(hi, seg_h) + float(gs["sl_buffer_mr"]) * m)
            tgt = _target(side, c, (lo, hi), others)
            rk = (c - sl) * d
            rr = None if (tgt is None or rk <= 0) else (tgt - c) * d / rk
            zs = M.zone_study(A, arm, t, d, s3, mr=mr[t])                  # a2: the window from arming to the commitment
            evid = {"sweep": bool(swept), "corrective_approach": bool(M.power_shift(A, t, d, None, s3, lvl)[0]),
                    "handover": zs.get("handover"), "pattern": zs.get("pattern"), "rsi_divergence": None,
                    "commit_grade_A": cm.get("grade") == "A", "volume_na": bool(cm.get("vol_na"))}
            n_ev = sum(1 for key in ("sweep", "corrective_approach", "pattern", "rsi_divergence") if evid[key])
            grade = "A" if (evid["handover"] or n_ev >= 2) else "B"       # a handover (both sides) is strong evidence
            if cm.get("vol_na"):
                grade = "B"                                                # c0: volume NA -> grade B at most
            kind = "demand" if side == BULL else "supply"
            vtxt = f"volume {cm['vol_mult']:.1f}× window avg" if cm.get("vol_mult") is not None else "volume: NA"
            btxt = f"body {cm['body_mult']:.1f}× window avg" if cm.get("body_mult") is not None else ""
            plan = {"entry_price": round(float(c), 2), "sl": round(float(sl), 2), "target": None if tgt is None else round(float(tgt), 2),
                    "rr": None if rr is None else round(float(rr), 2), "form": cm.get("form"), "commit_grade": cm.get("grade"),
                    "grade": grade, "evidence": evid, "confirm_bar": int(t),
                    "confirm_ts": str(pd.Timestamp(bars["timestamp"].iloc[t])),
                    "study": f"inside {kind}: " + (zs.get("line") or "no clear handover read"),
                    "commit_line": "commitment: " + " · ".join(x for x in (form_name(cm.get("form"), side), btxt, vtxt) if x)}
            if tgt is None:
                state, reason = DISARMED, R_NO_TARGET
                ev(t, DISARMED, R_NO_TARGET)
                out.update(plan)
                continue
            if rr is None or rr < float(gs["min_rr"]):
                state, reason = DISARMED, f"{R_RR} ({rr:.2f} < {float(gs['min_rr']):g})" if rr is not None else R_RR
                ev(t, DISARMED, reason)
                out.update(plan)
                continue
            if gs["wait_next_candle"]:
                commit_at, info["plan"] = t, plan
                ev(t, ARMED, f"commitment {cm.get('form')} - waiting for the next candle")
                continue
            state = CONFIRMED
            ev(t, CONFIRMED, f"commitment {cm.get('form')} · grade {plan['grade']}")
            out.update(plan)
            if t == n - 1:
                out["entry"] = True
            continue
        if t - arm + 1 >= int(gs["max_armed_bars"]):
            state, reason = DISARMED, R_TIMEOUT
            ev(t, DISARMED, R_TIMEOUT)
            continue
    out["state"], out["side"] = state, side
    out["bars_armed"] = 0 if arm is None or state != ARMED else int(n - arm)
    if state == ARMED:
        out["reason"] = R_NO_COMMIT + (" (break pending)" if out.get("break_pending") else "")
    elif state == DISARMED:
        out["reason"] = reason
    elif state == CONFIRMED:
        out["reason"] = "confirmed" if out["entry"] else "confirmed earlier (area not left yet)"
    else:
        out["reason"] = R_BROKEN if broken else "price not at area"      # broke today (no flip retest yet): "broken"
    out["broken"] = bool(broken)
    out["absorbing"] = state == ARMED and bool(info.get("absorbing"))
    out["low_volume"] = state == ARMED and bool(info.get("low_vol_logged"))
    return out


def safe_evaluate(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """Fail-closed: an exception means no entry, with the reason."""
    try:
        return evaluate(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - fail closed
        return {"state": "ERROR", "entry": False, "side": None, "reason": f"{R_ERROR}: {type(exc).__name__}: {str(exc)[:80]}",
                "events": []}


def session_start_index(bars: Optional[pd.DataFrame], day: Any = None) -> int:
    """Index of the first bar of `day` (default: the last bar's day)."""
    if bars is None or len(bars) == 0:
        return 0
    ts = pd.to_datetime(bars["timestamp"])
    day = pd.Timestamp(day).normalize() if day is not None else ts.iloc[-1].normalize()
    idx = np.nonzero((ts.dt.normalize() == day).to_numpy())[0]
    return int(idx[0]) if len(idx) else len(bars)


def describe(r: Mapping[str, Any], area: Optional[Sequence[float]] = None) -> str:
    """One line: "Watching supply 22,570–22,640 · price arrived · waiting for a bearish commitment · 3/8 bars"."""
    lo, hi = r.get("area") or area or (None, None)
    kind = "demand" if r.get("side") == BULL else ("supply" if r.get("side") == BEAR else "area")
    rng = f"{lo:,.0f}–{hi:,.0f}" if lo is not None else ""
    st = r.get("state")
    if st == ARMED:
        word = "bullish" if r.get("side") == BULL else "bearish"
        why = " · absorption" if r.get("absorbing") else (" · commitment on low volume" if r.get("low_volume") else "")
        return (f"Watching {kind} {rng} · price arrived · waiting for a {word} commitment · "
                f"{r.get('bars_armed', 0)}/{r.get('max_armed_bars')} bars{why}")
    if st == CONFIRMED:
        return (f"CONFIRMED {kind} {rng} · {r.get('form')} · grade {r.get('grade')} · entry {r.get('entry_price')} · SL {r.get('sl')} · "
                f"target {r.get('target')} · R:R {r.get('rr')}")
    if st == DISARMED:
        return f"{kind} {rng} · disarmed: {r.get('reason')}"
    if st == "ERROR":
        return f"{rng} · {r.get('reason')}"
    return f"{kind} {rng} · {r.get('reason')}"


def lot_multiplier(r: Mapping[str, Any], s: Optional[Mapping[str, Any]] = None) -> float:
    gs = gate_settings(s)
    return 1.0 if r.get("grade") == "A" else float(gs["grade_b_lot_mult"])
