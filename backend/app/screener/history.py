"""N3 S6a (spec §2.6, §2.7): screen history - "who passed on date X" and screen fitness (flag `screener_history`, off).

Point in time throughout:
- **Bars:** the shared lake's end-of-day bars only (NSE bhavcopies; public data, never a broker's or another user's).
  A company's bars come by ISIN across renames and splits, adjusted for corporate actions (`by_isin.isin_candles`).
  A symbol without an ISIN on record uses its own unadjusted bars, and the result says so.
- **Universe:**
  - An index universe is the membership **as of each date** (U1-b `members_on`), so names that later left the index
    or were delisted are included on the dates they were members (survivorship).
  - Dates before the index's first membership file are refused (unknown, not empty).
  - A symbol list is taken as given.
- **The screen at date d sees only bars that closed on or before d.**
  - Every ScreenQL function is causal (Z10-a), so one evaluation of the full series, read at each date, equals running
    the screen on bars cut at that date. `tests/test_n3_s6_history.py` checks exactly that on sample dates (the
    truncation test).
  - A cross-sectional screen (rank / percentile across the universe) and a screen with a function answered for the
    last bar only (Trend, StructureEvent, PatternBullish / PatternBearish, NearSupport / NearResistance - registry
    LAST_BAR_ONLY) are run date by date on cut bars instead (review of S6a, HIGH: read as a series, the last date's answer
    would be copied onto every date). The fast path refuses anything that is not a per-bar series.
  - **Prices as known at d** (review of S6a, MED): the bars are adjusted for every corporate action on record, but a
    split or bonus after d was not known at d. Adjustment after d scales every bar up to d by one factor, so a scale-free
    screen (a ratio, RSI, close > SMA) reads the same either way; an absolute level (close > 1000, volume > 1e6) does
    not. The screen at d therefore reads the bars rescaled to their prices as of d (`scales`: the factor of the actions
    after d, from the adjusted and the raw bar of d). Forward returns use the fully adjusted closes.
  - A symbol with fewer bars than the screen's lookback at d does not match at d.
- **Forward returns** (fitness only) are the closes after d, by design.
  - They are measured on the trading calendar: d + h sessions, at the last close on or before that day. A name with no
    bar at d + h (delisted or suspended in between) keeps its last price, and those observations are counted
    (`no_bar_at_horizon`).
  - With rebalance dates closer than the horizon the forward windows overlap; the t-statistic and the deflation use
    non-overlapping dates only (one every h sessions; `independent_dates`), the means use every date.
  - A date whose horizon has not finished yet is left out for that horizon.
  - The sealed backtest holdout is never read (`data_policy.check_range`).

Screen fitness, the open version of Holly, reports per horizon (1 / 5 / 20 sessions by default):
- the passers' mean forward return against the eligible universe's equal-weight mean on the same dates;
- the hit rate (passers that beat it) and the per-date excess with its t-statistic;
- turnover and the average number of passers;
- a breadth regime split (more or less than half the universe above its 50-session average, known at d);
- sample size, with a "low sample" flag below 30 observations;
- deflation: the run is a trial in the research ledger, and the per-date excess is deflated against every screen
  fitness trial this organisation ran in the last 30 days (Deflated Sharpe).

It is information, never a recommendation; costs and execution are not modelled here (the rolling screen backtest with
the part C cost models is S6b).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import numpy as np
import pandas as pd

from app.market_data.calendar import IST
from app.screener.runtime import SymbolData, evaluate, run_screen

FLAG = "screener_history"
BASE_TF = "1d"
HORIZONS = (1, 5, 20)
MAIN_HORIZON = 5
EVERY = {"daily": 1, "weekly": 5, "4-weekly": 20, "monthly": 21}
MAX_DATES = 260                      # rebalance dates in one fitness run
MAX_CROSS_SECTIONAL_DATES = 60       # a cross-sectional screen runs date by date: fewer dates
MAX_SYMBOLS = 300                    # symbols (index members over the window included) in one run
LOW_SAMPLE = 30
REGIME_SMA = 50
DISCLAIMER = ("Screen fitness describes how a screen's past matches moved afterwards, on past data. It is information, not a "
              "recommendation, and does not predict future results. Costs and execution are not modelled.")


class HistoryError(ValueError):
    """A history request the data cannot answer (shown as is)."""


Scale = Tuple[float, float]          # (price divisor, volume multiplier) that turns adjusted bars into the bars as of a date


def last_bar_only_calls(ast: Any) -> List[str]:
    """The functions of a screen answered for the last bar only (registry LAST_BAR_ONLY): they cannot be read back in
    time from one series, so such a screen runs date by date on cut bars."""
    from app.screener import nodes
    from app.screener.registry import LAST_BAR_ONLY
    out: List[str] = []
    for node in nodes.walk(ast):
        if isinstance(node, nodes.Call) and node.name in LAST_BAR_ONLY and node.name not in out:
            out.append(node.name)
    return out


def scale_of(adj_close: float, raw_close: float, adj_volume: float, raw_volume: float) -> Scale:
    """The as-of scale of one bar from its adjusted and raw values, rounded to 6 significant digits (review of S6a
    re-check: per-bar float noise would otherwise make every date its own scale and its own evaluation)."""
    p = float(adj_close) / float(raw_close) if raw_close else 1.0
    v = float(raw_volume) / float(adj_volume) if adj_volume else 1.0
    p = float(f"{p:.6g}")
    p = 1.0 if abs(p - 1.0) < 1e-6 else p
    # re-check of S6a (LOW): the adjusted volume is stored rounded to whole shares, so its ratio is noisy for a factor
    # like 3:2 (1.50075 for 1.5). A split or bonus moves the volume by the price's own factor: within 1 % of it (or of
    # 1, for a price-only adjustment) the volume scale is taken from the price; anything else is kept, rounded.
    if p != 1.0 and abs(v / p - 1.0) < 0.01:
        v = p
    elif abs(v - 1.0) < 0.01:
        v = 1.0
    else:
        v = float(f"{v:.6g}")
    return (p, v)


def rescaled(frame: pd.DataFrame, scale: Optional[Scale]) -> pd.DataFrame:
    """Adjusted bars -> the bars as known on a date (prices / divisor, volume x multiplier); identity for no scale."""
    if frame is None or frame.empty or scale is None or scale == (1.0, 1.0):
        return frame
    p, v = scale
    out = frame.copy()
    for col in ("open", "high", "low", "close"):
        if col in out.columns:
            out[col] = out[col] / p
    if "volume" in out.columns:
        out["volume"] = out["volume"] * v
    return out


def bar_index(days: Sequence[date]) -> pd.DatetimeIndex:
    """Daily bar timestamps as the screener's daily frames have them: the session date at 00:00 IST, in UTC."""
    return pd.DatetimeIndex([pd.Timestamp(datetime.combine(d, dtime(0, 0), IST)).tz_convert("UTC") for d in days])


def session_dates(index: pd.DatetimeIndex) -> List[date]:
    idx = pd.DatetimeIndex(index)
    return list((idx.tz_convert(IST) if idx.tz is not None else idx).date)


def cut(frame: pd.DataFrame, day: date) -> pd.DataFrame:
    """The bars that closed on or before `day` (a daily bar closes on its own session date)."""
    if frame is None or frame.empty:
        return frame
    keep = np.array([d <= day for d in session_dates(frame.index)], dtype=bool)
    return frame[keep]


def rebalance_dates(calendar: Sequence[date], start: date, end: date, every: int) -> List[date]:
    inside = [d for d in calendar if start <= d <= end]
    return inside[::max(1, int(every))]


# --- signals: who passed at each date -------------------------------------------------------------------------------------

def passers_on(ast: Any, validated: Any, frames: Dict[str, pd.DataFrame], day: date, params: Optional[Dict[str, Any]] = None,
               eligible: Optional[Set[str]] = None, scales: Optional[Dict[str, Dict[date, Scale]]] = None,
               excluded: Optional[Set[Tuple[str, date]]] = None) -> Dict[str, Any]:
    """The as-of run: the screen on every eligible symbol's bars cut at `day` (the exact, slow path). Only symbols that
    traded that session are evaluated (a suspended or delisted name has no bar of `day` and is not a match)."""
    universe = []
    for s, f in sorted(frames.items()):
        if (eligible is not None and s not in eligible) or f is None or (excluded is not None and (s, day) in excluded):
            continue
        upto = cut(f, day)
        if upto is not None and not upto.empty and session_dates(upto.index[-1:])[0] == day:
            universe.append(SymbolData(symbol=s, frames={BASE_TF: rescaled(upto, (scales or {}).get(s, {}).get(day))}))
    matches = run_screen(ast, validated, universe, base_tf=BASE_TF, params=params or {})
    return {"matched": sorted(m.symbol for m in matches if m.matched),
            "not_evaluated": {m.symbol: m.reason for m in matches if not m.matched and m.reason},
            "universe": len(universe)}


def signals(ast: Any, validated: Any, frames: Dict[str, pd.DataFrame], dates: Sequence[date], params: Optional[Dict[str, Any]] = None,
            eligible: Optional[Dict[date, Set[str]]] = None, scales: Optional[Dict[str, Dict[date, Scale]]] = None,
            excluded: Optional[Set[Tuple[str, date]]] = None) -> pd.DataFrame:
    """[dates x symbols] -> True where the symbol passed the screen at that date (on bars closed by then, at the prices
    known then: `scales`). Ineligible (not an index member at that date) and `excluded` (symbol, date) pairs are False."""
    params = dict(params or {})
    dates = sorted(dates)
    symbols = sorted(frames)
    table = pd.DataFrame(False, index=pd.Index(dates, name="date"), columns=symbols, dtype=bool)
    if not dates or not symbols:
        return table
    last_bar = last_bar_only_calls(ast)
    if validated.cross_sectional or last_bar:
        if len(dates) > MAX_CROSS_SECTIONAL_DATES:
            what = "a cross-sectional screen (a rank across the universe)" if validated.cross_sectional else \
                f"a screen with {', '.join(n + '()' for n in last_bar)} (answered for the last bar only)"
            raise HistoryError(f"{what} runs date by date: at most {MAX_CROSS_SECTIONAL_DATES} dates - use a longer rebalance step "
                               f"or a shorter window")
        for d in dates:
            matched = passers_on(ast, validated, frames, d, params, eligible.get(d) if eligible is not None else None, scales, excluded)["matched"]
            for s in matched:
                table.at[d, s] = True
        return table
    values = series_at_dates(ast, validated, frames, dates, params, scales)
    for s in values.columns:
        col = values[s]
        for d in dates:
            v = col.at[d]
            if v is not None and not (isinstance(v, float) and math.isnan(v)) and bool(v) and (eligible is None or s in eligible.get(d, set())) \
                    and (excluded is None or (s, d) not in excluded):
                table.at[d, s] = True
    return table


def series_at_dates(ast: Any, validated: Any, frames: Dict[str, pd.DataFrame], dates: Sequence[date],
                    params: Optional[Dict[str, Any]] = None, scales: Optional[Dict[str, Dict[date, Scale]]] = None) -> pd.DataFrame:
    """[dates x symbols] an expression's value at each date, on bars closed by then: one causal series per symbol (or
    per group of symbols sharing a bar index - the Z10-d panel) read at the date, at the prices known then (`scales`;
    a symbol with a split / bonus between the dates is evaluated once per price scale). NaN where the symbol has no bar
    that date or fewer bars than the expression's lookback. Used for the screen's matches and for S6b's rank values.
    Review of S6a (HIGH): an expression with a last-bar-only function is refused - read as a series, the last date's
    answer would be copied onto every date (the callers run such screens date by date)."""
    params = dict(params or {})
    dates = sorted(dates)
    symbols = sorted(frames)
    out = pd.DataFrame(np.nan, index=pd.Index(dates), columns=symbols, dtype=object)
    if not dates or not symbols:
        return out
    if last_bar_only_calls(ast) or validated.cross_sectional:
        raise HistoryError("this expression is answered for the last bar only (or across the universe): it cannot be read back in time")
    lookback = int((validated.lookback or {}).get(BASE_TF, 1) or 1)
    last = dates[-1]
    cut_frames = {s: cut(frames[s], last) for s in symbols}
    cut_frames = {s: f for s, f in cut_frames.items() if f is not None and not f.empty}

    def mark(s: str, values: Any, frame: pd.DataFrame, only: Optional[Set[date]] = None) -> None:
        if not isinstance(values, pd.Series):
            raise HistoryError("the expression did not give a per-bar series; it cannot be read back in time")
        days = session_dates(frame.index)
        pos = {d: i for i, d in enumerate(days)}
        arr = values.to_numpy()
        for d in dates:
            i = pos.get(d)
            if i is not None and i + 1 >= lookback and (only is None or d in only):
                out.at[d, s] = arr[i]
    scaled = {s for s in cut_frames if any(v != (1.0, 1.0) for v in (scales or {}).get(s, {}).values())}
    rest = list(cut_frames)
    from app.screener import panel as P
    if P.supported(ast, validated, BASE_TF):
        # Z10-d: symbols sharing one bar index are evaluated at once (bit for bit the per-symbol series).
        universe = [SymbolData(symbol=s, frames={BASE_TF: f}) for s, f in cut_frames.items() if s not in scaled]
        rest = sorted(scaled)
        for members in P._groups(universe, BASE_TF):
            names = [universe[i].symbol for i in members]
            if len(members) < P.MIN_GROUP:
                rest.extend(names)
                continue
            frame = universe[members[0]].frames[BASE_TF]
            try:
                value = P._PanelEval(P.Panel([universe[i].frames[BASE_TF] for i in members], [f"#{k}" for k in range(len(members))]),
                                     BASE_TF, params).ev(ast, BASE_TF)
            except Exception:  # noqa: BLE001 - the per-symbol runtime below, as the live run does
                rest.extend(names)
                continue
            for k, name in enumerate(names):
                mark(name, value.iloc[:, k] if isinstance(value, pd.DataFrame) else value, frame)
    for s in rest:
        frame = cut_frames[s]
        groups: Dict[Optional[Scale], Set[date]] = {}
        for d in dates:
            groups.setdefault((scales or {}).get(s, {}).get(d), set()).add(d)
        for scale, only in groups.items():
            try:
                values = evaluate(ast, SymbolData(symbol=s, frames={BASE_TF: rescaled(frame, scale)}), base_tf=BASE_TF, params=params)
            except Exception:  # noqa: BLE001 - one symbol's bad data never fails the run (it simply has no value)
                continue
            mark(s, values, frame, only if len(groups) > 1 else None)
    return out


# --- fitness --------------------------------------------------------------------------------------------------------------

@dataclass
class HorizonStats:
    horizon: int
    dates: int = 0
    observations: int = 0
    passers_mean: Optional[float] = None
    universe_mean: Optional[float] = None
    excess_mean: Optional[float] = None
    hit_rate: Optional[float] = None
    t_stat: Optional[float] = None
    low_sample: bool = True
    excess_by_date: List[float] = field(default_factory=list)
    independent_excess: List[float] = field(default_factory=list)   # one date every `horizon` sessions (no overlap)
    no_bar_at_horizon: int = 0

    def as_dict(self) -> Dict[str, Any]:
        r = lambda v, k=6: None if v is None else round(float(v), k)  # noqa: E731
        return {"horizon": self.horizon, "dates": self.dates, "observations": self.observations, "passers_mean": r(self.passers_mean),
                "universe_mean": r(self.universe_mean), "excess_mean": r(self.excess_mean), "hit_rate": r(self.hit_rate, 4),
                "t_stat": r(self.t_stat, 3), "low_sample": self.low_sample, "independent_dates": len(self.independent_excess),
                "no_bar_at_horizon": self.no_bar_at_horizon}


def closes_on_calendar(frames: Dict[str, pd.DataFrame], calendar: Sequence[date]) -> pd.DataFrame:
    """[calendar x symbols] last close on or before each session (NaN before a symbol's first bar)."""
    out = {}
    for s, f in frames.items():
        if f is None or f.empty:
            continue
        series = pd.Series(f["close"].to_numpy(dtype=float), index=pd.Index(session_dates(f.index)))
        series = series[~series.index.duplicated(keep="last")]
        out[s] = series.reindex(sorted(set(series.index) | set(calendar))).ffill().reindex(list(calendar))
    return pd.DataFrame(out, index=pd.Index(list(calendar)))


def traded_on(frames: Dict[str, pd.DataFrame], calendar: Sequence[date]) -> pd.DataFrame:
    """[calendar x symbols] True where the symbol has its own bar that session."""
    out = {s: pd.Series(True, index=pd.Index(session_dates(f.index))).groupby(level=0).first().reindex(list(calendar), fill_value=False)
           for s, f in frames.items() if f is not None and not f.empty}
    return pd.DataFrame(out, index=pd.Index(list(calendar))).fillna(False).astype(bool)


def _breadth(closes: np.ndarray, traded_row: np.ndarray, i: int, mask: np.ndarray) -> Optional[bool]:
    """More than half of the eligible symbols that traded at row `i` close above their 50-session average close, using
    rows up to `i` only (causal)."""
    if i + 1 < REGIME_SMA:
        return None
    cols = mask & traded_row
    if not cols.any():
        return None
    window = closes[i + 1 - REGIME_SMA:i + 1][:, cols]
    with np.errstate(invalid="ignore"):
        above = window[-1] > np.nanmean(window, axis=0)
    return bool(np.mean(above) > 0.5)


def fitness(sig: pd.DataFrame, frames: Dict[str, pd.DataFrame], calendar: Sequence[date], horizons: Sequence[int] = HORIZONS,
            eligible: Optional[Dict[date, Set[str]]] = None, excluded: Optional[Set[Tuple[str, date]]] = None) -> Dict[str, Any]:
    """The fitness statistics of a signal table (pure; numpy over the calendar x symbols grid)."""
    calendar = sorted(calendar)
    pos = {d: i for i, d in enumerate(calendar)}
    closes_df = closes_on_calendar(frames, calendar)
    cols = list(closes_df.columns)
    closes = closes_df.to_numpy(dtype=float)
    traded = traded_on(frames, calendar).reindex(columns=cols, fill_value=False).to_numpy(dtype=bool)
    sig = sig.reindex(columns=cols, fill_value=False)
    sig_arr = sig.to_numpy(dtype=bool)
    col_of = {c: j for j, c in enumerate(cols)}
    all_cols = np.ones(len(cols), dtype=bool)

    def mask_for(d: date) -> np.ndarray:
        if eligible is None:
            return all_cols
        m = np.zeros(len(cols), dtype=bool)
        for s in eligible.get(d, set()):
            j = col_of.get(s)
            if j is not None:
                m[j] = True
        return m
    out: Dict[str, Any] = {"horizons": [], "regimes": {}, "dates": len(sig.index)}
    turnovers: List[float] = []
    counts = sig_arr.sum(axis=1)
    for k in range(1, len(sig_arr)):
        a, b = sig_arr[k - 1], sig_arr[k]
        if a.any() or b.any():
            turnovers.append(1.0 - float((a & b).sum()) / max(int(a.sum()), int(b.sum()), 1))
    out["turnover"] = round(float(np.mean(turnovers)), 4) if turnovers else None
    out["avg_passers"] = round(float(np.mean(counts)), 2) if len(counts) else 0.0
    regime_rows: Dict[str, List[Tuple[float, float]]] = {"breadth_up": [], "breadth_down": []}
    masks = {d: mask_for(d) for d in sig.index}
    for h in horizons:
        st = HorizonStats(horizon=int(h))
        passer_rets: List[float] = []
        universe_means: List[float] = []
        hits = 0
        last_independent: Optional[int] = None
        for k, d in enumerate(sig.index):
            i = pos.get(d)
            if i is None or i + h >= len(calendar):
                continue                                               # the horizon has not finished
            m = masks[d] & traded[i]
            if not m.any():
                continue
            with np.errstate(invalid="ignore", divide="ignore"):
                fwd = closes[i + h] / closes[i] - 1.0
            ok = m & np.isfinite(fwd)
            if excluded:
                # a split / bonus inside the window of unadjusted bars: that observation is not a return
                for j, c in enumerate(cols):
                    if ok[j] and (c, d) in excluded:
                        ok[j] = False
            if not ok.any():
                continue
            u = float(fwd[ok].mean())
            chosen = ok & sig_arr[k]
            if not chosen.any():
                continue
            p = fwd[chosen]
            st.dates += 1
            passer_rets.extend(float(x) for x in p)
            universe_means.append(u)
            hits += int((p > u + 1e-12).sum())                     # beat the universe (float noise is not a beat)
            st.excess_by_date.append(float(p.mean()) - u)
            st.no_bar_at_horizon += int((chosen & ~traded[i + h]).sum())
            if last_independent is None or i - last_independent >= h:
                st.independent_excess.append(float(p.mean()) - u)        # forward windows that do not overlap
                last_independent = i
            if h == MAIN_HORIZON:
                regime = _breadth(closes, traded[i], i, masks[d])
                if regime is not None:
                    regime_rows["breadth_up" if regime else "breadth_down"].append((float(p.mean()), u))
        st.observations = len(passer_rets)
        st.low_sample = st.observations < LOW_SAMPLE
        if passer_rets:
            st.passers_mean = float(np.mean(passer_rets))
            st.universe_mean = float(np.mean(universe_means))
            st.hit_rate = hits / len(passer_rets)
            st.excess_mean = float(np.mean(st.excess_by_date))
            ind = np.array(st.independent_excess, dtype=float)       # review of S6a (MED): overlapping windows inflate t
            if len(ind) >= 2 and ind.std(ddof=1) > 0:
                st.t_stat = float(ind.mean() / (ind.std(ddof=1) / math.sqrt(len(ind))))
        out["horizons"].append(st)
    for name, rows in regime_rows.items():
        out["regimes"][name] = ({"dates": len(rows), "passers_mean": round(float(np.mean([r[0] for r in rows])), 6),
                                 "universe_mean": round(float(np.mean([r[1] for r in rows])), 6),
                                 "low_sample": len(rows) < LOW_SAMPLE} if rows else {"dates": 0, "low_sample": True})
    return out


def confidence_note(stats: HorizonStats) -> str:
    """Plain words on how much the sample can carry (never a recommendation)."""
    if stats.observations == 0:
        return "No matches with a finished horizon in this window."
    if stats.low_sample:
        return f"Low sample ({stats.observations} observations over {stats.dates} dates): treat the figures as anecdotal."
    if stats.t_stat is None or abs(stats.t_stat) < 2:
        return f"{stats.observations} observations; the difference from the universe is within what chance alone often produces."
    return (f"{stats.observations} observations; the difference from the universe is larger than chance alone usually gives "
            f"(t {stats.t_stat:.1f}) - before deflation for the screens tried.")


def warmup_bars(lookback_bars: int) -> int:
    """Sessions read before the first date: the screen's lookback plus a warm-up, so smoothed indicators (RSI, EMA, ATR)
    have settled the way they have on a long live history."""
    return max(3 * int(lookback_bars), int(lookback_bars) + 100)


def window_days(start: date, end: date, lookback_bars: int, horizon: int) -> Tuple[date, date]:
    """Calendar days to read: the screen's lookback and warm-up before `start` (sessions -> calendar days, with room for
    holidays) and the longest horizon after `end`."""
    before = int(math.ceil(warmup_bars(lookback_bars) * 1.6)) + 10
    after = int(math.ceil(horizon * 1.6)) + 10
    return start - timedelta(days=before), end + timedelta(days=after)


def utc_close(day: date) -> datetime:
    return datetime.combine(day, dtime(15, 30), IST).astimezone(timezone.utc)
