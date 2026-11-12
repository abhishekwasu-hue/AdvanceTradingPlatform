"""M97 PR-4 (docs/design/M97_SUPPLY_DEMAND.md, Z3): the "Supply & Demand zones" chart layer.

GET /api/charts/layers/sd-zones?symbol=NSE:NIFTY 50&timeframes=15m,1h,1d&at=<ISO time>&include_violated=0
  * Per chosen timeframe, the zones of the generic detector (`app/price_action/sd_zones.py`, reference settings,
    M97-Q2) on that timeframe's CLOSED bars as of `at` (timezone-aware; default now, a future time reads as now): the
    bars come from the same loader the price-action layer reads (`price_action_layer.load_frame`: the shared lake, or
    the reader's own bars, DATA-H), so nothing after `at` is ever read and a zone exists only once its leg-out closed.
  * 10m and 2h (M97-Q4) are built from the stored 5m / 1h bars, weekly and monthly from the daily ones, by the ScreenQL
    runtime's resampler (`screener.runtime.resample`): a built bar exists only once the last finer bar it holds has
    closed (the @tf close-time guard), so the chart, a scan and the Copilot read the same bars.
  * Each zone: id, side, formation (DBR / RBR / RBD / DBD), state (fresh / tested / violated) and the number of tests,
    the proximal and distal prices, the base start / end and formed times, and the leg-in / base / leg-out candles with
    their numbers (range, body % of the range, multiple of the average range of the candles before - the leg-out's of
    the detector's reference, the average or the ATR).
  * Violated zones are left out unless `include_violated=1`; per timeframe and side the zones nearest the last close
    (MAX_PER_SIDE, a display limit) - `more` counts the rest.
M99 P1: `kind` (base / swing / both) and `swap` (0 / 1) pick the zones drawn (the user's overlay options; reference: base
zones, no swap levels); each zone also carries its kind and its odds-enhancer score with every part and weight
(app/price_action/sd_odds.py - the evaluation the scanners and the templates read). With two or more timeframes drawn,
each timeframe's zones are tagged against the next higher drawn timeframe's zones ("with" / "against"), from that
timeframe's own closed bars; the zones are never merged.
Behind the `sd_pack` flag (off by default -> 503). Zones are price areas from past prices only - reference, never an
order or a recommendation.
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.charts import price_action_layer as L
from app.core.rate_limit import user_rate_limit
from app.db.models import User
from app.db.session import get_session
from app.platform.controls import require_flag
from app.price_action import sd_odds as so
from app.price_action import sd_zones as sd

router = APIRouter(prefix="/api/charts/layers", tags=["charts"])
FLAG = "sd_pack"
SCHEMA = "sd-layer/1"
NOTE = "Supply and demand zones from past prices only - reference, not signals or recommendations."
NO_BARS = "no closed bars for this instrument and timeframe yet"
# the layer's timeframes (ScreenQL's names), finest first; the chart sends its own ids mapped to these
TIMEFRAMES: tuple[str, ...] = ("1m", "3m", "5m", "10m", "15m", "30m", "1h", "2h", "1d", "1w", "1M")
# built from these stored bars (M97-Q4 for 10m / 2h; weekly / monthly from daily bars, as ScreenQL does)
BUILT_FROM: dict[str, str] = {"10m": "5m", "2h": "1h", "1w": "1d", "1M": "1d"}
# history read per timeframe, in calendar days (a few hundred bars; a 1-hour rebuild from 1-minute bars stays inside
# the price-action layer's REBUILD_MAX_DAYS)
DAYS: dict[str, int] = {"1m": 3, "3m": 7, "5m": 15, "10m": 30, "15m": 45, "30m": 90, "1h": 180, "2h": 180, "1d": 1095,
                        "1w": 3650, "1M": 3650}
SHORT: dict[str, str] = {"1m": "1m", "3m": "3m", "5m": "5m", "10m": "10m", "15m": "15m", "30m": "30m", "1h": "1H", "2h": "2H",
                         "1d": "D", "1w": "W", "1M": "M"}
# M98 F1: a candle's span on the chart is [its start, its start + this) - the chart marks the bars of its own timeframe
# whose start falls in that span (one bar on the same timeframe; every lower-timeframe bar inside a higher one)
SPAN: dict[str, pd.DateOffset | pd.Timedelta] = {
    "1m": pd.Timedelta(minutes=1), "3m": pd.Timedelta(minutes=3), "5m": pd.Timedelta(minutes=5), "10m": pd.Timedelta(minutes=10),
    "15m": pd.Timedelta(minutes=15), "30m": pd.Timedelta(minutes=30), "1h": pd.Timedelta(hours=1), "2h": pd.Timedelta(hours=2),
    "1d": pd.Timedelta(days=1), "1w": pd.Timedelta(days=7), "1M": pd.DateOffset(months=1)}
MAX_TIMEFRAMES = 4
MAX_PER_SIDE = 6
EXCHANGE_TZ = "Asia/Kolkata"
rate_limited = user_rate_limit("chart_sd_zones", limit=60, window_seconds=60)


# ---------------------------------------------------------------------------------------------------------------------
# Pure part: closed bars -> zones
# ---------------------------------------------------------------------------------------------------------------------
def _epoch(t: Any) -> int:
    ts = pd.Timestamp(t)
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    return int(ts.timestamp())


def _num(x: float, digits: int = 4) -> float | None:
    return None if x is None or not np.isfinite(x) else round(float(x), digits)


FORMATION_WORDS: dict[str, str] = {"SWING": "Swing", "ENGULF": "Engulf", "SWAP": "Swap"}


def label_text(side: str, formation: str, state: str, tests: int, tf: str) -> str:
    """"Demand · DBR · Fresh · D" - the side, the formation (M99 P1: Swing / Engulf / Swap for the new kinds), the state
    (with the test count) and the timeframe. The odds score is in the zone's tap card (the label stays short, M75 W-1)."""
    word = "Tested" if state == sd.TESTED else state.capitalize()
    shown = f"{word} {tests}" if state == sd.TESTED else word
    return " · ".join(("Demand" if side == sd.DEMAND else "Supply", FORMATION_WORDS.get(formation, formation), shown, SHORT.get(tf, tf)))


def _candle(frame: pd.DataFrame, nums: dict[str, np.ndarray], times: pd.DatetimeIndex, i: int, of: str = "avg",
            tf: str | None = None) -> dict[str, Any]:
    """One candle the chart highlights on a tap: its time (the bar's START, as the chart's candles are stamped), the end
    of its span (`end`, M98 F1: start + the timeframe's length), high / low, and the numbers it was judged by."""
    ref = nums[of][i]
    with np.errstate(invalid="ignore", divide="ignore"):
        mult = nums["range"][i] / ref if ref and np.isfinite(ref) and ref > 0 else float("nan")
    start = pd.Timestamp(times[i])
    local = start.tz_convert(EXCHANGE_TZ) if start.tzinfo is not None else start      # a month is a calendar month in India
    end = _epoch(local + SPAN[tf]) if tf in SPAN else None
    return {"time": _epoch(start), "end": end, "high": _num(float(frame["high"].iloc[i])), "low": _num(float(frame["low"].iloc[i])),
            "range": _num(nums["range"][i]), "body_pct": _num(nums["body_pct"][i], 1), "mult": _num(mult, 2)}


def zone_id(tf: str, z: sd.SDZone, times: pd.DatetimeIndex) -> str:
    raw = f"{tf}|{z.side}|{_epoch(times[z.base_start])}|{_epoch(times[z.formed])}" + ("" if z.kind == "base" else f"|{z.kind}")
    return f"sd-{tf}-{hashlib.sha256(raw.encode()).hexdigest()[:12]}"


def zone_rows(frame: pd.DataFrame, tf: str, *, settings: sd.SDSettings | None = None, include_violated: bool = False,
              per_side: int = MAX_PER_SIDE, odds: so.OddsSettings | None = None, htf: so.HtfContext | None = None) -> dict[str, Any]:
    """The zones of one timeframe's closed bars (index = bar start, oldest first), as of the last bar: each zone's state
    at that bar's close. Violated ones only with `include_violated`; per side the `per_side` zones whose proximal line is
    nearest the last close (`more`: how many were left out, `hidden_violated`: how many violated ones were hidden)."""
    s = settings or sd.SDSettings()
    out: dict[str, Any] = {"timeframe": tf, "bars": int(len(frame)), "as_of_bar": None, "zones": [], "more": 0, "hidden_violated": 0}
    if len(frame) < 3:
        return out
    times = pd.DatetimeIndex(frame.index)
    view = so.view(frame, s, odds, htf, series=sd.sd_series(frame, s))
    zones = view.zones
    nums = sd.candle_numbers(frame, s)
    last = len(frame) - 1
    close = float(frame["close"].iloc[-1])
    out["as_of_bar"] = _epoch(times[last])
    out_ref = "ref"
    rows: list[tuple[float, dict[str, Any]]] = []
    for z in zones:
        state = z.state_at(last)
        if state is None:
            continue
        if state == sd.VIOLATED and not include_violated:
            out["hidden_violated"] += 1
            continue
        tests = z.visits_by(last)
        base = range(z.base_start, z.base_end + 1)
        odd = view.breakdown(z, last)
        depart = z.depart if z.depart >= 0 else z.formed
        b_hi = float(frame["high"].iloc[z.base_start:z.base_end + 1].max())
        b_lo = float(frame["low"].iloc[z.base_start:z.base_end + 1].min())
        with np.errstate(invalid="ignore", divide="ignore"):
            mults = [nums["range"][i] / nums["avg"][i] for i in base if np.isfinite(nums["avg"][i]) and nums["avg"][i] > 0]
        row = {
            "id": zone_id(tf, z, times), "timeframe": tf, "side": z.side, "formation": z.formation, "state": state, "tests": int(tests),
            "proximal": _num(z.proximal), "distal": _num(z.distal),
            "base_start": _epoch(times[z.base_start]), "base_end": _epoch(times[z.base_end]), "formed": _epoch(times[z.formed]),
            "violated_at": _epoch(times[z.violated]) if z.violated is not None and z.violated <= last else None,
            "leg_in": _candle(frame, nums, times, z.leg_in, tf=tf),
            "base": {"candles": len(base), "times": [_epoch(times[i]) for i in base], "high": _num(b_hi), "low": _num(b_lo),
                     "bars": [_candle(frame, nums, times, i, tf=tf) for i in base],
                     "max_body_pct": _num(float(max(nums["body_pct"][i] for i in base)), 1),
                     "max_mult": _num(max(mults), 2) if mults else None},
            "leg_out": {**_candle(frame, nums, times, depart, of=out_ref if z.kind == "base" else "avg", tf=tf),
                        "of": "ATR" if s.out_ref == "atr" and z.kind == "base" else "average range"},
            "kind": z.kind, "odds": odd,
            "label": label_text(z.side, z.formation, state, int(tests), tf),
        }
        rows.append((abs(close - z.proximal), row))
    for side in sd.SIDES:
        mine = sorted((r for r in rows if r[1]["side"] == side), key=lambda r: r[0])
        out["zones"].extend(r for _, r in mine[:per_side])
        out["more"] += max(0, len(mine) - per_side)
    return out


def indexed(frame: pd.DataFrame) -> pd.DataFrame:
    """A loader frame (columns timestamp = bar start, bar_end, open .. volume) as an OHLCV frame indexed by bar start (UTC)."""
    if not len(frame):
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"], index=pd.DatetimeIndex([], tz="UTC"), dtype=float)
    idx = pd.DatetimeIndex(pd.to_datetime(frame["timestamp"], utc=True))
    out = pd.DataFrame({c: pd.to_numeric(frame[c], errors="coerce").to_numpy(float) for c in ("open", "high", "low", "close", "volume")},
                       index=idx)
    return out[~out.index.duplicated(keep="last")].sort_index()


def built(base: pd.DataFrame, base_tf: str, tf: str, exchange: str | None) -> pd.DataFrame:
    """10m / 2h / 1w / 1M from closed finer bars by the ScreenQL runtime's resampler: a trailing bucket whose close is
    after the last finer bar's close is dropped (closed bars only, the @tf guard)."""
    from app.screener.runtime import resample
    if not len(base):
        return base
    return resample(base, base_tf, tf, exchange=exchange)


async def load_tf(session: AsyncSession, key: str, tf: str, when: datetime, private: Any) -> tuple[pd.DataFrame, str | None]:
    """One timeframe's closed bars ending by `when` (indexed by bar start) and their source label."""
    src_tf = BUILT_FROM.get(tf, tf)
    start = when - timedelta(days=DAYS[tf])
    frame, source = await L.load_frame(session, key, src_tf, start, when, private=private, provisional=True)
    df = indexed(frame)
    if tf in BUILT_FROM:
        df = built(df, src_tf, tf, key.split(":", 1)[0] if ":" in key else None)
    return df, source


def close_ns(df: pd.DataFrame, tf: str, exchange: str | None) -> np.ndarray:
    """When each bar of `df` closed (int64 ns, UTC) - the ScreenQL runtime's bar_close, so a higher timeframe's zone is
    read from the close of the candle that made it, as every @tf read is."""
    from app.screener.runtime import bar_close
    if not len(df):
        return np.empty(0, dtype=np.int64)
    return pd.DatetimeIndex(bar_close(pd.DatetimeIndex(df.index), tf, exchange)).asi8


def _compute(frames: list[tuple[str, pd.DataFrame, str | None]], include_violated: bool, settings: sd.SDSettings | None = None,
             exchange: str | None = None) -> list[dict[str, Any]]:
    """Each timeframe's rows; M99 P1: tagged against the next higher drawn timeframe (its own closed bars)."""
    s = settings or sd.SDSettings()
    out = []
    for i, (tf, df, source) in enumerate(frames):
        ctx = None
        if i + 1 < len(frames) and len(df) and len(frames[i + 1][1]) >= 3:
            htf, hdf, _ = frames[i + 1]
            ctx = so.htf_context(htf, sd.sd_series(hdf, s).zones, close_ns(hdf, htf, exchange), close_ns(df, tf, exchange))
        rows = zone_rows(df, tf, include_violated=include_violated, settings=s, htf=ctx)
        out.append({**rows, "source": source, "reason": None if len(df) else NO_BARS})
    return out


def parse_timeframes(text: str) -> list[str]:
    tfs = [t.strip() for t in (text or "").split(",") if t.strip()]
    bad = [t for t in tfs if t not in TIMEFRAMES]
    if bad:
        raise HTTPException(status_code=422, detail=f"unknown timeframe(s) {', '.join(bad)}; use {', '.join(TIMEFRAMES)}")
    tfs = list(dict.fromkeys(tfs))
    if not tfs:
        raise HTTPException(status_code=422, detail="choose at least one timeframe")
    if len(tfs) > MAX_TIMEFRAMES:
        raise HTTPException(status_code=422, detail=f"at most {MAX_TIMEFRAMES} timeframes at once")
    return sorted(tfs, key=TIMEFRAMES.index)


# ---------------------------------------------------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------------------------------------------------
@router.get("/sd-zones", dependencies=[Depends(rate_limited)])
async def sd_zones_layer(symbol: str = Query(..., min_length=1, max_length=100), timeframes: str = Query("1d", max_length=60),
                         at: datetime | None = None, include_violated: bool = False,
                         kind: str = Query("base", pattern="^(base|swing|both)$"), swap: bool = False,
                         user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    await require_flag(session, FLAG, user.tenant_id)
    owner = user.id
    tfs = parse_timeframes(timeframes)
    at_utc = L._aware(at, "at")
    now = datetime.now(UTC)
    when = now.replace(second=0, microsecond=0) if at_utc is None or at_utc > now else at_utc
    s = sd.settings_from({"kind": kind, "swap": swap})
    head = {"schema": SCHEMA, "symbol": symbol, "at": when.isoformat(), "note": NOTE, "include_violated": include_violated,
            "settings": {name: getattr(s, name) for name, *_ in sd.SETTINGS_TABLE}, "settings_note": sd.REFERENCE_LABEL,
            "odds_settings": so.settings_dict(so.OddsSettings())}
    from app.price_action.zones.store import normalise_key
    try:
        key: str | None = normalise_key(symbol if ":" in symbol else f"NSE:{symbol}")
    except ValueError:
        key = None
    if key is None or key == "*":
        return {**head, "timeframes": [{**zone_rows(pd.DataFrame(), tf), "source": None, "reason": NO_BARS} for tf in tfs]}
    from app.market_lake import own_bars
    private = await own_bars.scope_for(session, user, key)
    frames: list[tuple[str, pd.DataFrame, str | None]] = []
    try:
        for tf in tfs:
            df, source = await load_tf(session, key, tf, when, private)
            frames.append((tf, df, source))
    except L.WindowTooLarge as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    rows = await L._in_slot(session, owner, _compute, frames, include_violated, s, key.split(":", 1)[0] if ":" in key else None)
    return {**head, "symbol": key, "timeframes": rows}


__all__ = ["router", "FLAG", "TIMEFRAMES", "BUILT_FROM", "zone_rows", "label_text", "load_tf", "built", "indexed", "parse_timeframes",
           "close_ns", "FORMATION_WORDS"]
