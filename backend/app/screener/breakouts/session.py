"""U6 BK2: the exchange session behind the intraday breakout screens (B16 / B17 / B18) and the two series they need -
session VWAP and relative volume by time of day.

* The session clock comes from the exchange calendar (`app/market_data/calendar.EXCHANGE_SESSIONS`: NSE 09:15-15:30
  IST, no new entries after 15:00), never from constants here. Every bar is placed by its START in exchange time.
* Half days and special sessions: the repository's calendar models the regular session only (holidays come from the
  `market_holidays` table, and a holiday simply has no bars). So:
  - a session is whatever bars a trading date has inside its window - a half day (early close) just has fewer bars, and
    nothing here assumes 375 minutes (the opening range, the first hour and the previous day's range read the bars
    that exist; a time-of-day slot the half day never reached does not enter that slot's average);
  - `special` overrides the window of a date (an exchange circular: a late open after an outage, an early close);
  - a date whose FIRST bar starts at or after the regular close (Muhurat trading in the evening) is a special session
    that opens at that bar. Known at its first bar, so still causal.
  A session that opens at another time than the regular open is special: it is left out of the time-of-day averages
  and is not the "previous day" of the next session (its volume and range are not those of a regular day). A half day
  that opens on time is a regular session that ends early.
* Bars before the open on a session date are pre-open bars (`pre`): outside the session for VWAP, the opening range and
  the averages; B17's `premarket` level reads them where the data has them.

Everything is causal: a value at bar j uses bars 0..j only (the time-of-day average uses PRIOR sessions only - tested).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import List, Mapping, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from app.market_data.calendar import EXCHANGE_SESSIONS, IST, ExchangeSession, session_family

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SessionCalendar:
    """One exchange family's session clock. `cutoff` is the no-new-entries time of a regular session (None: the close)."""
    name: str
    open: time
    close: time
    cutoff: Optional[time] = None
    tz: ZoneInfo = IST
    special: Mapping[date, Tuple[time, time]] = field(default_factory=dict)


def calendar_for(exchange: Optional[str] = "NSE", special: Optional[Mapping[date, Tuple[time, time]]] = None) -> SessionCalendar:
    """The session clock of an exchange (segment aliases as in the calendar: NFO / BSE -> NSE, MCX_FO -> MCX, CDS / BCD ->
    CDS 09:00-17:00 - since BK2-8 the trading worker's clock too). A 24x7 venue has no session open, so the intraday session
    screens do not run on it."""
    spec: ExchangeSession = EXCHANGE_SESSIONS[session_family(exchange)]
    if spec.always_open:
        raise ValueError(f"{spec.name} trades around the clock: the session screens need an exchange with an open and a close")
    return SessionCalendar(spec.name, spec.open, spec.close, spec.no_new_entries_after, IST, dict(special or {}))


DEFAULT_CALENDAR = calendar_for("NSE")


def _minutes(t: time) -> float:
    return t.hour * 60 + t.minute + t.second / 60.0


@dataclass
class SessionMap:
    """Per bar: the session it belongs to (-1: outside every session), minutes from that session's open to the bar's
    start, and whether it is a pre-open bar of a session date. Per session: its date, open / close / window end (minutes
    of the day), and whether it is special."""
    session: np.ndarray
    minute: np.ndarray
    pre: np.ndarray
    pre_session: np.ndarray        # the session a pre-open bar belongs to (-1 otherwise)
    dates: List[date]
    special: np.ndarray
    window_end: np.ndarray         # per session: minutes from the open by which a signal bar must have closed
    tf_minutes: float
    warnings: List[str] = field(default_factory=list)   # data that looks mis-stamped (see map_sessions)

    @property
    def in_session(self) -> np.ndarray:
        return self.session >= 0

    def first_of_session(self) -> np.ndarray:
        """True on the first in-session bar of each session."""
        s = self.session
        prev = np.r_[-2, s[:-1]]
        return (s >= 0) & (s != prev)


def map_sessions(index: pd.Index, tf_minutes: float, cal: SessionCalendar = DEFAULT_CALENDAR) -> SessionMap:
    if not isinstance(index, pd.DatetimeIndex):
        raise ValueError("the session screens need time-stamped bars")
    idx = index.tz_localize("UTC") if index.tz is None else index
    local = idx.tz_convert(cal.tz)
    days = np.array([d for d in local.date])
    mins = (local.hour * 60 + local.minute + local.second / 60.0).to_numpy(float)
    n = len(index)
    session = np.full(n, -1, dtype=int)
    minute = np.full(n, np.nan)
    pre = np.zeros(n, dtype=bool)
    pre_session = np.full(n, -1, dtype=int)
    dates: List[date] = []
    special: List[bool] = []
    window_end: List[float] = []
    reg_open, reg_close = _minutes(cal.open), _minutes(cal.close)
    cutoff = _minutes(cal.cutoff) if cal.cutoff is not None else reg_close
    i = 0
    while i < n:
        d = days[i]
        k = i
        while k < n and days[k] == d:
            k += 1
        if d in cal.special:
            o, c = (_minutes(t) for t in cal.special[d])
            is_special = o != reg_open                                # a half day (early close) keeps the regular clock
            end = (min(c, cutoff) if not is_special else c) - o
        elif mins[i] >= reg_close:                                   # an evening session on its own (Muhurat)
            o, c, is_special = mins[i], 24 * 60.0, True
            end = c - o
        else:
            o, c, is_special = reg_open, reg_close, False
            end = min(cutoff, c) - o
        inside = [j for j in range(i, k) if o <= mins[j] < c]
        before = [j for j in range(i, k) if mins[j] < o]
        if inside:
            sid = len(dates)
            dates.append(d)
            special.append(is_special)
            window_end.append(end)
            for j in inside:
                session[j], minute[j] = sid, mins[j] - o
            for j in before:
                pre[j], pre_session[j] = True, sid
        i = k
    sm = SessionMap(session, minute, pre, pre_session, dates, np.array(special, dtype=bool), np.array(window_end, dtype=float),
                    float(tf_minutes))
    sm.warnings = _stamp_warnings(sm, local, cal)
    for w in sm.warnings:
        logger.warning("session map: %s", w)
    return sm


def _stamp_warnings(sm: SessionMap, local: pd.DatetimeIndex, cal: SessionCalendar) -> List[str]:
    """Review L2: a naive index is read as UTC. Exchange wall-clock stamps (09:15 written without a zone) then land 5:30
    late, so every session seems to start 330 minutes after the open (or, for an evening venue, after the close)."""
    n = len(sm.dates)
    if n < 3:
        return []
    out: List[str] = []
    if sm.special.all():
        out.append(f"every one of the {n} sessions starts at or after the {cal.close.strftime('%H:%M')} close and reads as an "
                   "evening session - are these exchange wall-clock stamps stored without a time zone (read as UTC)?")
    offset = local[0].utcoffset()
    shift = offset.total_seconds() / 60.0 if offset is not None else 0.0
    firsts = sm.minute[sm.first_of_session()]
    if shift and np.mean(np.isclose(firsts, shift)) >= 0.8:
        out.append(f"most sessions start {shift:g} minutes after the open - the bars look like exchange wall-clock times read as UTC")
    return out


def session_vwap(f: pd.DataFrame, sm: SessionMap) -> np.ndarray:
    """VWAP from the session open: cumulative typical price x volume / cumulative volume over the session's bars up to
    and including this (closed) bar. NaN outside the session and while the session has no volume (indices)."""
    tp = (f["high"].to_numpy(float) + f["low"].to_numpy(float) + f["close"].to_numpy(float)) / 3.0
    v = f["volume"].to_numpy(float) if "volume" in f.columns else np.full(len(f), np.nan)
    ins = sm.in_session
    pv = pd.Series(np.where(ins, tp * v, np.nan))
    vv = pd.Series(np.where(ins, v, np.nan))
    key = pd.Series(sm.session)
    cpv = pv.groupby(key).cumsum().to_numpy(float)
    cv = vv.groupby(key).cumsum().to_numpy(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(ins & (cv > 0), cpv / np.where(cv > 0, cv, np.nan), np.nan)
    return np.asarray(out, dtype=float)


def time_of_day_rvol(f: pd.DataFrame, sm: SessionMap, sessions: int, min_sessions: int, mode: str = "bar") -> np.ndarray:
    """Relative volume against the same time of day. For a bar at `minute` from its session's open: its volume (mode
    `cumulative`: the session's volume up to and including it) divided by the mean of the same slot in the previous
    `sessions` REGULAR sessions that had that slot (at least `min_sessions` of them, else NaN). The current session never
    enters its own average, and a later session never enters an earlier one (no look-ahead)."""
    v = f["volume"].to_numpy(float) if "volume" in f.columns else np.full(len(f), np.nan)
    if mode == "cumulative":
        vv = pd.Series(np.where(sm.in_session, v, np.nan))
        v = vv.groupby(pd.Series(sm.session)).cumsum().to_numpy(float)
    out = np.full(len(f), np.nan)
    ins = np.flatnonzero(sm.in_session)
    if not len(ins):
        return out
    # Vectorised (review M3): per slot, the rolling mean over the regular sessions' values (each row includes itself);
    # a bar then takes the row of the LAST earlier contribution to its slot (merge_asof, strictly before) - the
    # previous `sessions` regular sessions that had the slot, never its own.
    bars = pd.DataFrame({"pos": ins, "slot": sm.minute[ins], "v": v[ins]})
    regular = ~sm.special[sm.session[ins]] & np.isfinite(bars["v"].to_numpy())
    contrib = bars[regular]
    if contrib.empty:
        return out
    avg = contrib.groupby("slot", sort=False)["v"].rolling(int(sessions), min_periods=int(min_sessions)).mean()
    contrib = contrib.assign(avg=avg.reset_index(level=0, drop=True))[["pos", "slot", "avg"]]
    merged = pd.merge_asof(bars, contrib, on="pos", by="slot", allow_exact_matches=False)
    a = merged["avg"].to_numpy(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(np.isfinite(a) & (a > 0) & np.isfinite(merged["v"].to_numpy(float)), merged["v"].to_numpy(float) / a, np.nan)
    out[merged["pos"].to_numpy()] = ratio
    return out


def session_extremes_before(h: np.ndarray, low: np.ndarray, sm: SessionMap) -> Tuple[np.ndarray, np.ndarray]:
    """(highest high, lowest low) of the session's bars BEFORE each bar (NaN on the session's first bar and outside)."""
    key = pd.Series(sm.session)
    hs = pd.Series(np.where(sm.in_session, h, np.nan))
    ls = pd.Series(np.where(sm.in_session, low, np.nan))
    hi = hs.groupby(key).cummax().groupby(key).shift(1).to_numpy(float)
    lo = ls.groupby(key).cummin().groupby(key).shift(1).to_numpy(float)
    hi[~sm.in_session] = np.nan
    lo[~sm.in_session] = np.nan
    return hi, lo


def window_levels(h: np.ndarray, low: np.ndarray, sm: SessionMap, minutes: float) -> Tuple[np.ndarray, np.ndarray]:
    """Per bar: the high / low of its session's first `minutes` (the opening range), known only on bars that start at or
    after the window's end; NaN when the session's data does not start at the open (a partial first day)."""
    hi, lo = np.full(len(h), np.nan), np.full(len(h), np.nan)
    ins = np.flatnonzero(sm.in_session)
    if not len(ins):
        return hi, lo
    df = pd.DataFrame({"sid": sm.session[ins], "minute": sm.minute[ins], "h": h[ins], "l": low[ins]})
    opens_at_zero = df.groupby("sid")["minute"].min() == 0
    win = df[df["minute"] < minutes].groupby("sid").agg(top=("h", "max"), bot=("l", "min"))
    win = win[opens_at_zero.reindex(win.index, fill_value=False)]
    after = (df["minute"] >= minutes).to_numpy() & df["sid"].isin(win.index).to_numpy()
    sids = df["sid"].to_numpy()[after]
    hi[ins[after]] = win["top"].reindex(sids).to_numpy(float)
    lo[ins[after]] = win["bot"].reindex(sids).to_numpy(float)
    return hi, lo


def previous_session_levels(h: np.ndarray, low: np.ndarray, sm: SessionMap) -> Tuple[np.ndarray, np.ndarray]:
    """Per bar: the high / low of the previous REGULAR session in the data (NaN for the first session)."""
    n_sessions = len(sm.dates)
    top, bot = np.full(n_sessions, np.nan), np.full(n_sessions, np.nan)
    for j in np.flatnonzero(sm.in_session):
        s = int(sm.session[j])
        top[s] = h[j] if not np.isfinite(top[s]) else max(top[s], h[j])
        bot[s] = low[j] if not np.isfinite(bot[s]) else min(bot[s], low[j])
    prev_hi, prev_lo = np.full(n_sessions, np.nan), np.full(n_sessions, np.nan)
    last: Optional[int] = None
    for s in range(n_sessions):
        if last is not None:
            prev_hi[s], prev_lo[s] = top[last], bot[last]
        if not sm.special[s]:
            last = s
    hi, lo = np.full(len(h), np.nan), np.full(len(h), np.nan)
    ins = sm.in_session
    hi[ins], lo[ins] = prev_hi[sm.session[ins]], prev_lo[sm.session[ins]]
    return hi, lo


def premarket_levels(h: np.ndarray, low: np.ndarray, sm: SessionMap) -> Tuple[np.ndarray, np.ndarray]:
    """Per in-session bar: the high / low of its date's pre-open bars (NaN when the data has none)."""
    n_sessions = len(sm.dates)
    top, bot = np.full(n_sessions, np.nan), np.full(n_sessions, np.nan)
    for j in np.flatnonzero(sm.pre):
        s = int(sm.pre_session[j])
        top[s] = h[j] if not np.isfinite(top[s]) else max(top[s], h[j])
        bot[s] = low[j] if not np.isfinite(bot[s]) else min(bot[s], low[j])
    hi, lo = np.full(len(h), np.nan), np.full(len(h), np.nan)
    ins = sm.in_session
    hi[ins], lo[ins] = top[sm.session[ins]], bot[sm.session[ins]]
    return hi, lo


def eligible(sm: SessionMap, skip_min: float, window: str) -> np.ndarray:
    """Bars a signal may come from: in the session, starting at or after `skip_min` minutes from the open and, with
    window `calendar`, closing by the exchange's no-new-entries time (a special session: by its close)."""
    ok = sm.in_session & (np.nan_to_num(sm.minute, nan=-1.0) >= skip_min)
    if window == "calendar":
        end = np.full(len(sm.session), -np.inf)
        ins = sm.in_session
        end[ins] = sm.window_end[sm.session[ins]]
        ok &= np.nan_to_num(sm.minute, nan=np.inf) + sm.tf_minutes <= end
    return np.asarray(ok, dtype=bool)


def clock(dt: datetime, cal: SessionCalendar = DEFAULT_CALENDAR) -> str:
    """HH:MM in exchange time (for the reasons a result shows)."""
    ts = pd.Timestamp(dt)
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts
    return ts.tz_convert(cal.tz).strftime("%H:%M")


__all__ = ["SessionCalendar", "SessionMap", "calendar_for", "DEFAULT_CALENDAR", "map_sessions", "session_vwap", "time_of_day_rvol",
           "session_extremes_before", "window_levels", "previous_session_levels", "premarket_levels", "eligible", "clock"]
