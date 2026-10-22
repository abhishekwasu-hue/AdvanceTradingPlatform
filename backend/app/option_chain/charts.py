"""OC5 (owner's order, handover 2b + 2c): the series behind the Option chain "Charts" view and the reading facts above it.

Pure functions over the caller's own broker chain read NOW plus the OI collector's stored slots of today's session
(`oi_snapshots` / `strike_oi_snapshots`, app/option_chain/snapshots.py), the day's first-seen OI per strike
(`oi_day_baselines`) and the last stored chain of earlier sessions. Nothing here reads a database, a clock or the
network: the route (charts_routes.py) passes everything in.

The charts (each with a one-line plain caption; a chart that needs a path through the session and does not have two
readings yet says "builds through the session" - never sample numbers):
* by strike, now: OI by strike (calls vs puts, with today's change in each bar's note), change in OI by strike, the IV
  smile;
* through the session: OI per strike (multi-strike), cumulative call vs put OI with spot, PCR, max pain vs spot, the ATM
  straddle premium, ATM IV, and the near-month future's price vs its OI.

Definitions:
* The window: the ATM strike (the listed strike nearest spot; a tie goes to the lower strike) and `each_side` listed
  strikes either side (app/option_chain/analyses.py `near_atm`). The session charts sum over these same strikes at every
  reading, so the totals compare like with like.
* Today's OI change: OI now minus the first OI the collector stored for the strike today (`oi_day_baselines`); when no
  baseline is stored for this expiry, the broker's own OI change (against the previous session) where it sends one.
* The session readings: the stored slots of this expiry with a spot, then the read now; with `interval` 10 or 15 the
  last reading in each 10- / 15-minute bucket (OI is a level, so a bucket keeps its last reading, never a sum -
  `oi_regime.aggregate_history`).
* Max pain at each reading: over the strikes the collector stores around the money (`OI_BANNER_COLLECT_SPAN` either side),
  so a stored reading and the read now are worked out over the same kind of window.
* PCR zone: the band of the window's PCR under the organisation's OI settings (`oi_regime.compute_pcr_zone_label`, the
  port of the Trade repo's zone label). Days to expiry: `oi_regime.compute_dte`. IV change: ATM IV now against the
  average closing ATM IV of the last `IV_SESSIONS` stored sessions; the Trade repo's sideways-session baseline
  (`oi_regime.iv_change_from_average`) is given alongside.

INFORMATION only: no reading here is a gate or a rule, and no text carries buy / sell wording
(tests/test_oc5_option_chain_charts.py checks every caption and sentence against the platform's advice-word lists).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from app.brokers.models import OptionChain, OptionChainRow
from app.option_chain import analyses as A
from app.option_chain import oi_regime
from app.option_chain.models import OptionType
from app.option_chain.oi_regime import OIRegimeSettings

IST = A.IST
DEFAULT_EACH_SIDE = 10          # the owner's "about ATM +/- 10"
MAX_EACH_SIDE = 25
INTERVALS = (5, 10, 15)         # minutes; the collector stores one reading per 5-minute slot
IV_SESSIONS = 5                 # earlier sessions in the plain IV change
MULTI_STRIKE_DEFAULT = 1        # the multi-strike chart opens on ATM and this many strikes either side

KEYS = ("oi_by_strike", "oi_change_by_strike", "multi_strike_oi", "cumulative_oi", "pcr_session", "max_pain_session",
        "straddle_session", "atm_iv_session", "iv_smile", "futures")

BUILDING = ("Builds through the session: a reading of the chain is stored every 5 minutes while the market is open, "
            "so this chart fills in as the day goes on.")
HISTORY_OFF = ("Earlier readings of the session are not used while your own-broker data setting is on, so this chart shows "
               "the latest reading only.")
NO_FUTURE = "The stored readings of this session carry no futures price and OI yet, so this chart fills in once they do."
NO_IV = "No implied volatility is available for these strikes: the broker quoted none and the last prices could not be read as one."
NO_CHANGE = "Today's OI change shows once the first reading of the day is stored for this expiry."


@dataclass(frozen=True)
class Reading:
    """One point of the session: when it was read and the chain (a stored slot, or the read now)."""
    at: datetime
    chain: OptionChain
    future_price: Optional[float] = None
    future_oi: Optional[float] = None
    stored: bool = True


# ---------------------------------------------------------------- small helpers

def _iso(at: datetime) -> str:
    return (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).isoformat()


def _r(value: Optional[float], digits: int = 2) -> Optional[float]:
    return None if value is None or not math.isfinite(value) else round(float(value), digits)


def _oi(value: Optional[float]) -> float:
    return float(value or 0.0)


def _series(key: str, label: str, tone: str, points: List[Dict[str, Any]], *, axis: str = "left", dashed: bool = False,
            strike: Optional[float] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {"key": key, "label": label, "tone": tone, "axis": axis, "dashed": dashed, "points": points}
    if strike is not None:
        out["strike"] = strike
    return out


def _chart(key: str, title: str, caption: str, kind: str, x_kind: str, y_label: str, series: List[Dict[str, Any]], *,
           y2_label: Optional[str] = None, marker: Optional[Dict[str, Any]] = None, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out = {"key": key, "title": title, "caption": caption, "state": "ok", "message": None, "type": kind, "x_kind": x_kind,
           "x_label": "Strike" if x_kind == "strike" else "Time (IST)", "y_label": y_label, "y2_label": y2_label,
           "series": series, "marker": marker}
    out.update(extra or {})
    return out


def _building(key: str, title: str, caption: str, message: str) -> Dict[str, Any]:
    return {"key": key, "title": title, "caption": caption, "state": "building", "message": message, "type": "line",
            "x_kind": "time", "x_label": "Time (IST)", "y_label": "", "y2_label": None, "series": [], "marker": None}


def _pts(values: Sequence[Tuple[Any, Optional[float]]]) -> List[Dict[str, Any]]:
    return [{"x": x, "y": y} for x, y in values]


def window(chain: OptionChain, each_side: int) -> List[OptionChainRow]:
    return A.near_atm(A.sorted_rows(chain), chain.underlying_ltp, each_side)


def atm_strike(chain: OptionChain) -> Optional[float]:
    rows = A.sorted_rows(chain)
    i = A.atm_index(rows, chain.underlying_ltp)
    return rows[i].strike if i is not None else None


def iv_pct(row: OptionChainRow, side: str, spot: Optional[float], years: Optional[float]) -> Optional[float]:
    """The option's IV in percent, read as the IV smile analysis reads it (analyses._iv_pct)."""
    ot = OptionType.CALL if side == "call" else OptionType.PUT
    return A._iv_pct(getattr(row, f"{side}_iv"), getattr(row, f"{side}_ltp"), spot, row.strike, years, ot)


def atm_iv(chain: OptionChain, at: datetime) -> Optional[float]:
    """The mean of the call and put IV (percent) at the ATM strike, or None."""
    rows = A.sorted_rows(chain)
    i = A.atm_index(rows, chain.underlying_ltp)
    if i is None:
        return None
    years = A._years(chain.expiry, at)
    ivs = [v for v in (iv_pct(rows[i], "call", chain.underlying_ltp, years), iv_pct(rows[i], "put", chain.underlying_ltp, years)) if v is not None]
    return sum(ivs) / len(ivs) if ivs else None


def session_readings(chain: OptionChain, slots: Sequence[Reading], now: datetime, interval: int) -> List[Reading]:
    """The stored readings of this chain's expiry (with a spot and strikes) and the read now, oldest first, one per
    `interval`-minute bucket (the bucket's last reading)."""
    kept = [s for s in slots if A._same_expiry(s.chain.expiry, chain.expiry) and A._usable_spot(s.chain.underlying_ltp)
            and s.chain.rows and s.at < now]
    points = sorted(kept, key=lambda s: s.at) + [Reading(now, chain, stored=False)]
    return [p for _, p in reversed(oi_regime.aggregate_history(points, interval, lambda p: p.at.astimezone(IST)))]


def _sum_over(chain: OptionChain, strikes: Sequence[float]) -> Tuple[Optional[float], Optional[float]]:
    """(call OI, put OI) summed over `strikes`; None when the reading holds none of them."""
    wanted = set(strikes)
    rows = [r for r in chain.rows if r.strike in wanted]
    if not rows:
        return None, None
    return sum(_oi(r.call_oi) for r in rows), sum(_oi(r.put_oi) for r in rows)


def _strike_word(n: int) -> str:
    return "strike" if n == 1 else "strikes"


# ---------------------------------------------------------------- the charts by strike (the read now)

def oi_by_strike(rows: Sequence[OptionChainRow], change: Mapping[float, Tuple[Optional[float], Optional[float]]], spot: Optional[float],
                 basis: Optional[str]) -> Dict[str, Any]:
    title = "OI by strike"
    caption = "Open interest at each strike now: calls against puts. Each bar's note gives today's change."
    calls = [{"x": r.strike, "y": _oi(r.call_oi), "d": change.get(r.strike, (None, None))[0]} for r in rows]
    puts = [{"x": r.strike, "y": _oi(r.put_oi), "d": change.get(r.strike, (None, None))[1]} for r in rows]
    return _chart("oi_by_strike", title, caption, "bar", "strike", "Open interest",
                  [_series("call_oi", "Call OI", "down", calls), _series("put_oi", "Put OI", "up", puts)],
                  marker={"x": spot, "label": "Spot"} if spot is not None else None, extra={"change_basis": basis})


def oi_change(chain: OptionChain, rows: Sequence[OptionChainRow], baselines: Mapping[float, Tuple[Optional[float], Optional[float]]]
              ) -> Tuple[Dict[float, Tuple[Optional[float], Optional[float]]], Optional[str]]:
    """Today's OI change per strike in `rows` and its basis: "first_reading" (against `oi_day_baselines`) or
    "previous_session" (the broker's own change); ({}, None) when neither is there."""
    if any(r.strike in baselines for r in rows):
        out: Dict[float, Tuple[Optional[float], Optional[float]]] = {}
        for r in rows:
            base = baselines.get(r.strike)
            if base is None:
                out[r.strike] = (None, None)
                continue
            call = _oi(r.call_oi) - base[0] if base[0] is not None and r.call_oi is not None else None
            put = _oi(r.put_oi) - base[1] if base[1] is not None and r.put_oi is not None else None
            out[r.strike] = (call, put)
        return out, "first_reading"
    if any(r.call_change_oi is not None or r.put_change_oi is not None for r in rows):
        return {r.strike: (r.call_change_oi, r.put_change_oi) for r in rows}, "previous_session"
    return {}, None


def oi_change_by_strike(rows: Sequence[OptionChainRow], change: Mapping[float, Tuple[Optional[float], Optional[float]]],
                        basis: Optional[str], first_at: Optional[datetime], spot: Optional[float]) -> Dict[str, Any]:
    title = "Change in OI by strike"
    if basis is None:
        return _building("oi_change_by_strike", title, "How much open interest each strike added or shed today.", NO_CHANGE) | {
            "type": "bar", "x_kind": "strike", "x_label": "Strike"}
    since = (f"since the first reading at {A.fmt_time(first_at)} IST" if basis == "first_reading" and first_at else
             "since the first reading of the day" if basis == "first_reading" else "against the previous session, as your broker sends it")
    caption = f"How much open interest each strike added (above the line) or shed (below it) {since}."
    calls = [{"x": r.strike, "y": _r(change.get(r.strike, (None, None))[0], 0)} for r in rows]
    puts = [{"x": r.strike, "y": _r(change.get(r.strike, (None, None))[1], 0)} for r in rows]
    return _chart("oi_change_by_strike", title, caption, "bar", "strike", "OI change",
                  [_series("call_change", "Call OI change", "down", calls), _series("put_change", "Put OI change", "up", puts)],
                  marker={"x": spot, "label": "Spot"} if spot is not None else None, extra={"change_basis": basis})


def iv_smile(chain: OptionChain, rows: Sequence[OptionChainRow], now: datetime) -> Dict[str, Any]:
    title = "IV smile now"
    caption = "Implied volatility of the calls and puts at each strike now; the dashed line is spot."
    spot = chain.underlying_ltp
    years = A._years(chain.expiry, now)
    calls = [(r.strike, _r(iv_pct(r, "call", spot, years))) for r in rows]
    puts = [(r.strike, _r(iv_pct(r, "put", spot, years))) for r in rows]
    if all(y is None for _, y in calls + puts):
        return _building("iv_smile", title, caption, NO_IV) | {"x_kind": "strike", "x_label": "Strike"}
    return _chart("iv_smile", title, caption, "line", "strike", "IV %",
                  [_series("call_iv", "Call IV", "down", _pts(calls)), _series("put_iv", "Put IV", "up", _pts(puts))],
                  marker={"x": spot, "label": "Spot"} if spot is not None else None)


# ---------------------------------------------------------------- the charts through the session

def _need_path(key: str, title: str, caption: str, readings: Sequence[Reading], history_off: bool) -> Optional[Dict[str, Any]]:
    if len(readings) >= 2:
        return None
    return _building(key, title, caption, HISTORY_OFF if history_off else BUILDING)


def multi_strike_oi(readings: Sequence[Reading], strikes: Sequence[float], atm: Optional[float], history_off: bool) -> Dict[str, Any]:
    title = "OI per strike through the session"
    caption = "Call and put open interest of the strikes you pick, reading by reading (calls solid, puts dashed)."
    waiting = _need_path("multi_strike_oi", title, caption, readings, history_off)
    if waiting:
        return waiting
    series: List[Dict[str, Any]] = []
    for k in strikes:
        call_pts, put_pts = [], []
        for p in readings:
            row = next((r for r in p.chain.rows if r.strike == k), None)
            call_pts.append({"x": _iso(p.at), "y": _oi(row.call_oi) if row is not None and row.call_oi is not None else None})
            put_pts.append({"x": _iso(p.at), "y": _oi(row.put_oi) if row is not None and row.put_oi is not None else None})
        label = A.fmt_strike(k)
        series.append(_series(f"call_{label}", f"{label} call OI", "down", call_pts, strike=k))
        series.append(_series(f"put_{label}", f"{label} put OI", "up", put_pts, dashed=True, strike=k))
    default: List[float] = []
    if atm is not None and atm in strikes:
        i = list(strikes).index(atm)
        default = list(strikes)[max(0, i - MULTI_STRIKE_DEFAULT): i + MULTI_STRIKE_DEFAULT + 1]
    return _chart("multi_strike_oi", title, caption, "line", "time", "Open interest", series,
                  extra={"strikes": list(strikes), "default_strikes": default})


def cumulative_oi(readings: Sequence[Reading], strikes: Sequence[float], history_off: bool) -> Dict[str, Any]:
    title = "Total call vs put OI, with spot"
    caption = (f"Open interest summed over the {len(strikes)} {_strike_word(len(strikes))} on screen at each reading, "
               "calls against puts; spot is the line on the right scale.")
    waiting = _need_path("cumulative_oi", title, caption, readings, history_off)
    if waiting:
        return waiting
    calls, puts, spot = [], [], []
    for p in readings:
        c, q = _sum_over(p.chain, strikes)
        calls.append((_iso(p.at), c))
        puts.append((_iso(p.at), q))
        spot.append((_iso(p.at), _r(p.chain.underlying_ltp)))
    return _chart("cumulative_oi", title, caption, "line", "time", "Open interest",
                  [_series("call_oi", "Total call OI", "down", _pts(calls)), _series("put_oi", "Total put OI", "up", _pts(puts)),
                   _series("spot", "Spot", "brand", _pts(spot), axis="right")], y2_label="Spot")


def pcr_session(readings: Sequence[Reading], strikes: Sequence[float], zone: str, history_off: bool) -> Dict[str, Any]:
    title = "PCR through the session"
    caption = (f"Put OI divided by call OI over the {len(strikes)} {_strike_word(len(strikes))} on screen, reading by reading. "
               f"Zone now: {zone}.")
    waiting = _need_path("pcr_session", title, caption, readings, history_off)
    if waiting:
        return waiting
    points = []
    for p in readings:
        c, q = _sum_over(p.chain, strikes)
        points.append((_iso(p.at), _r(q / c) if c and q is not None else None))
    return _chart("pcr_session", title, caption, "line", "time", "PCR", [_series("pcr", "PCR", "brand", _pts(points))])


def _collector_window(chain: OptionChain, span: int) -> List[OptionChainRow]:
    return A.near_atm(A.sorted_rows(chain), chain.underlying_ltp, span)


def max_pain_session(readings: Sequence[Reading], span: int, history_off: bool) -> Dict[str, Any]:
    title = "Max pain vs spot"
    caption = "The max pain strike at each reading against spot, over the strikes stored around the money."
    waiting = _need_path("max_pain_session", title, caption, readings, history_off)
    if waiting:
        return waiting
    pain, spot = [], []
    for p in readings:
        rows = p.chain.rows if p.stored else _collector_window(p.chain, span)
        value = oi_regime.max_pain(rows) if any(_oi(r.call_oi) or _oi(r.put_oi) for r in rows) else None
        pain.append((_iso(p.at), value))
        spot.append((_iso(p.at), _r(p.chain.underlying_ltp)))
    return _chart("max_pain_session", title, caption, "line", "time", "Price",
                  [_series("max_pain", "Max pain", "warn", _pts(pain)), _series("spot", "Spot", "brand", _pts(spot))])


def straddle_session(readings: Sequence[Reading], history_off: bool) -> Dict[str, Any]:
    title = "ATM straddle premium"
    caption = "The ATM call plus the ATM put price at each reading (the ATM strike follows spot)."
    waiting = _need_path("straddle_session", title, caption, readings, history_off)
    if waiting:
        return waiting
    points = []
    for p in readings:
        found = A.straddle_of(p.chain)
        points.append((_iso(p.at), _r(found[1] + found[2]) if found else None))
    if all(y is None for _, y in points):
        return _building("straddle_session", title, caption, "The ATM call and put prices are not both available in these readings.")
    return _chart("straddle_session", title, caption, "line", "time", "Premium", [_series("straddle", "ATM straddle", "brand", _pts(points))])


def atm_iv_session(readings: Sequence[Reading], history_off: bool) -> Dict[str, Any]:
    title = "ATM IV through the session"
    caption = "The average of the ATM call and put implied volatility at each reading."
    waiting = _need_path("atm_iv_session", title, caption, readings, history_off)
    if waiting:
        return waiting
    points = [(_iso(p.at), _r(atm_iv(p.chain, p.at))) for p in readings]
    if all(y is None for _, y in points):
        return _building("atm_iv_session", title, caption, NO_IV)
    return _chart("atm_iv_session", title, caption, "line", "time", "IV %", [_series("atm_iv", "ATM IV", "brand", _pts(points))])


def futures(slots: Sequence[Reading], interval: int, history_off: bool) -> Dict[str, Any]:
    title = "Futures price vs futures OI"
    caption = "The near-month future's last price (left scale) and its open interest (right scale) at each stored reading."
    if history_off:
        return _building("futures", title, caption, HISTORY_OFF)
    with_future = [s for s in slots if s.future_price is not None or s.future_oi is not None]
    bucketed = [p for _, p in reversed(oi_regime.aggregate_history(with_future, interval, lambda p: p.at.astimezone(IST)))]
    if len(bucketed) < 2:
        return _building("futures", title, caption, NO_FUTURE if len(slots) >= 2 else BUILDING)
    return _chart("futures", title, caption, "line", "time", "Futures price",
                  [_series("future_price", "Futures price", "brand", _pts([(_iso(p.at), _r(p.future_price)) for p in bucketed])),
                   _series("future_oi", "Futures OI", "warn", _pts([(_iso(p.at), p.future_oi) for p in bucketed]), axis="right")],
                  y2_label="Futures OI")


# ---------------------------------------------------------------- the reading facts (2b)

@dataclass(frozen=True)
class EarlierSession:
    """An earlier trading day's closing chain and its spot readings (they stand in for the day's candle)."""
    day: date
    at: datetime
    chain: OptionChain
    prices: Tuple[float, ...]


def iv_change(today_iv: Optional[float], earlier: Sequence[EarlierSession], today: date, settings: OIRegimeSettings) -> Optional[Dict[str, Any]]:
    """ATM IV now against the closing ATM IV of the last `IV_SESSIONS` stored sessions, plus the Trade repo's
    sideways-session baseline. None without IV now or without an earlier session."""
    if today_iv is None:
        return None
    closes = [(s.day, atm_iv(s.chain, s.at), s.prices) for s in sorted(earlier, key=lambda s: s.day) if s.day < today]
    recent = [(d, iv) for d, iv, _ in closes if iv is not None][-IV_SESSIONS:]
    if not recent:
        return None
    average = sum(iv for _, iv in recent) / len(recent)
    days = [oi_regime.DayIv(d, iv, p[0], max(p), min(p), p[-1]) for d, iv, p in closes if p]
    sideways = oi_regime.iv_change_from_average(today_iv, days, today, settings.iv_lookback_days, settings.marubozu_threshold)
    return {"atm_iv": _r(today_iv), "average": _r(average), "change_pct": _r((today_iv - average) / average * 100) if average > 0 else None,
            "sessions": [{"day": d.isoformat(), "atm_iv": _r(iv)} for d, iv in recent],
            "sideways_baseline": None if sideways is None else {"average": _r(sideways.baseline_avg_iv), "change_pct": _r(sideways.change_pct),
                                                                "sessions": sideways.days_in_baseline}}


def facts(chain: OptionChain, rows: Sequence[OptionChainRow], now: datetime, settings: OIRegimeSettings,
          earlier: Sequence[EarlierSession]) -> Dict[str, Any]:
    """The reading facts above the charts: days to expiry, the PCR and its zone, the IV change over recent sessions,
    each with one plain sentence."""
    today = now.astimezone(IST).date()
    expiry, dte = oi_regime.compute_dte(chain.expiry or None, today)
    calls, puts = sum(_oi(r.call_oi) for r in rows), sum(_oi(r.put_oi) for r in rows)
    pcr = round(puts / calls, 2) if calls else None
    band = oi_regime.pcr_band(pcr, settings.pcr_bands)
    zone = oi_regime.compute_pcr_zone_label(pcr, settings.pcr_bands) if pcr is not None else "Not available"
    iv_now = atm_iv(chain, now)
    change = iv_change(iv_now, earlier, today, settings)
    lines: List[str] = []
    if dte is not None and dte >= 0:
        lines.append("Expiry is today." if dte == 0 else f"{dte} {'day' if dte == 1 else 'days'} to expiry.")
    if pcr is not None:
        lines.append(f"PCR {pcr:.2f} over the {len(rows)} {_strike_word(len(rows))} on screen: {zone}.")
    if change is not None and change["change_pct"] is not None:
        n = len(change["sessions"])
        lines.append(f"ATM IV {change['atm_iv']:.2f}% is {abs(change['change_pct']):.1f}% {'above' if change['change_pct'] >= 0 else 'below'} "
                     f"its average close of {change['average']:.2f}% over the last {n} {'session' if n == 1 else 'sessions'}.")
    elif iv_now is not None:
        lines.append(f"ATM IV is {iv_now:.2f}%; the change against earlier sessions shows once a session's closing reading is stored.")
    return {"expiry": expiry.isoformat() if expiry else None, "dte": dte, "pcr": pcr, "pcr_band": band.value if band else None,
            "pcr_zone": zone, "atm_iv": _r(iv_now), "iv_change": change, "sentences": lines}


# ---------------------------------------------------------------- all of it

def compute(chain: OptionChain, slots: Sequence[Reading], now: datetime, *, each_side: int = DEFAULT_EACH_SIDE, interval: int = 5,
            baselines: Optional[Mapping[float, Tuple[Optional[float], Optional[float]]]] = None,
            earlier: Sequence[EarlierSession] = (), settings: Optional[OIRegimeSettings] = None, collect_span: int = 15,
            history_off: bool = False) -> Dict[str, Any]:
    """The facts and the ten charts in view order. `slots`: today's stored readings (any expiry; only this chain's is
    kept). `baselines`: today's first-seen OI per strike for THIS expiry (the route leaves it empty otherwise)."""
    if interval not in INTERVALS:
        raise ValueError(f"interval must be one of {INTERVALS}")
    settings = settings or OIRegimeSettings()
    chain = A.clean_spot(chain)
    slots = [Reading(s.at, A.clean_spot(s.chain), s.future_price, s.future_oi) for s in slots]
    rows = window(chain, each_side)
    strikes = [r.strike for r in rows]
    spot = chain.underlying_ltp
    readings = session_readings(chain, slots, now, interval)
    stored = [p for p in readings if p.stored]
    change, basis = oi_change(chain, rows, baselines or {})
    same_expiry = [s for s in slots if A._same_expiry(s.chain.expiry, chain.expiry) and s.at < now]
    first_at = min((s.at for s in same_expiry), default=None)
    info = facts(chain, rows, now, settings, earlier)
    charts = [
        oi_by_strike(rows, change, spot, basis),
        oi_change_by_strike(rows, change, basis, first_at, spot),
        multi_strike_oi(readings, strikes, atm_strike(chain) if rows else None, history_off),
        cumulative_oi(readings, strikes, history_off),
        pcr_session(readings, strikes, info["pcr_zone"], history_off),
        max_pain_session(readings, collect_span, history_off),
        straddle_session(readings, history_off),
        atm_iv_session(readings, history_off),
        iv_smile(chain, rows, now),
        futures(sorted(same_expiry, key=lambda s: s.at), interval, history_off),
    ]
    return {"window": {"each_side": each_side, "strikes": strikes, "atm": atm_strike(chain) if rows else None},
            "interval": interval, "intervals": list(INTERVALS),
            "session": {"readings": len(stored), "since": _iso(stored[0].at) if stored else None,
                        "note": HISTORY_OFF if history_off else (BUILDING if not stored else None)},
            "facts": info, "charts": charts}


__all__ = ["compute", "Reading", "EarlierSession", "facts", "iv_change", "session_readings", "oi_change", "window", "atm_iv", "KEYS",
           "INTERVALS", "DEFAULT_EACH_SIDE", "MAX_EACH_SIDE", "BUILDING", "HISTORY_OFF", "NO_FUTURE", "NO_CHANGE"]
