"""Adapt ATP's existing closed-bar readers to signal-only AMW breakout research.

Pass a calendar-validated frame per timeframe. The index is the bar's start and
``bar_end`` is its explicit, timezone-aware close. The adapter does no data
fetching and does not infer exchange-session boundaries from a timestamp.
"""
from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import numpy as np
import pandas as pd

from app.indicators.momentum import rsi
from app.price_action import sd_zones, structure_series
from app.screener.breakouts import params, retest, screens, session
from app.screener.runtime import _divergence_swings

from .amw_breakout import Candidate, Candle, Divergence, Settings, Snapshot, Zone
from .amw_sources import crosses, horizontal_candidates, session_candidates

_OHLC = ("open", "high", "low", "close")
_SIDES: dict[str, Literal[1, -1]] = {"up": 1, "down": -1}
_STRUCTURE = {"UP": 1, "DOWN": -1}
_ZONE_SIDES: dict[str, Literal[1, -1]] = {sd_zones.DEMAND: 1, sd_zones.SUPPLY: -1}
_SCREEN_COLUMNS = ("signal", "level", "base_len")
_RETEST_SOURCES = {"B1": "range", "B2": "swing"}


@dataclass(frozen=True)
class _Frame:
    bars: pd.DataFrame
    closes: pd.DatetimeIndex
    available_through: pd.Timestamp | None
    known: pd.DatetimeIndex | None = None

    def last_at(self, at: pd.Timestamp) -> int:
        if self.available_through is not None and at > self.available_through:
            return -1
        j = int(self.closes.searchsorted(at, side="right")) - 1
        return -1 if j >= 0 and self.known is not None and self.known[j] > at else j


def _normalize(frame: pd.DataFrame, tf: str, as_of: pd.Timestamp | None) -> _Frame:
    available = frame.attrs.get("available_through")
    horizon = pd.Timestamp(available) if available is not None else None
    if horizon is not None and (pd.isna(horizon) or horizon.tzinfo is None):
        raise ValueError(f"{tf}: available_through must be a timezone-aware timestamp")
    if not set((*_OHLC, "bar_end")).issubset(frame.columns):
        raise ValueError(f"{tf}: expected OHLC columns and an explicit bar_end column")
    if frame.empty:
        return _Frame(frame, pd.DatetimeIndex([], tz="UTC"), horizon)
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        raise ValueError(f"{tf}: bar starts must be a timezone-aware DatetimeIndex")
    ends = pd.DatetimeIndex(frame["bar_end"])
    if ends.tz is None or ends.hasnans or not ends.is_monotonic_increasing or not ends.is_unique:
        raise ValueError(f"{tf}: bar ends must be timezone-aware, ascending and unique")
    ends = ends.tz_convert("UTC")
    cutoffs = [cutoff for cutoff in (as_of, horizon) if cutoff is not None]
    stop = int(ends.searchsorted(min(cutoffs), side="right")) if cutoffs else len(ends)
    bars = frame.iloc[:stop].copy()
    starts = bars.index.tz_convert("UTC")
    ends = ends[:stop]
    if starts.hasnans or (starts >= ends).any() or (starts[1:] < ends[:-1]).any():
        raise ValueError(f"{tf}: each bar must open before its own close and after the preceding close")
    o, h, low, c = bars.loc[:, list(_OHLC)].to_numpy(dtype=float).T
    if not np.isfinite([o, h, low, c]).all() or (low > np.minimum(o, c)).any() or (h < np.maximum(o, c)).any():
        raise ValueError(f"{tf}: invalid OHLC values")
    bars.index = starts
    bars["bar_end"] = ends
    known = None
    if "known_at" in bars and len(bars):
        values = pd.DatetimeIndex(bars["known_at"])
        if values.tz is None or values.hasnans or (values < ends).any():
            raise ValueError(f"{tf}: known_at must be timezone-aware and at or after bar_end")
        known = pd.to_datetime(np.maximum.accumulate(values.to_numpy(dtype="datetime64[ns]").astype("int64")), utc=True)
    return _Frame(bars, ends, horizon, known)


def _source_rows(frame: _Frame, tf: str, settings: Settings, bars: screens.Bars, p: dict[str, Any]) -> dict[str, dict[str, pd.DataFrame]]:
    return {
        source: screens.evaluate_screen(frame.bars, source, p, bars=bars, tf=tf, columns=_SCREEN_COLUMNS)
        for source in settings.sources if source in _RETEST_SOURCES
    }


def _candidate_key(frame: _Frame, source: str, side: int, j: int, row: pd.Series) -> str:
    origin = j - int(row["base_len"])
    if not 0 <= origin < j:
        raise ValueError(f"{source}: breakout source supplied a level without a prior origin")
    return f"{source}:{side}:{frame.closes[origin].isoformat()}:{float(row['level']).hex()}"


def _candidates(frame: _Frame, j: int, sources: Mapping[str, Mapping[str, pd.DataFrame]]) -> tuple[Candidate, ...]:
    out = []
    for source, by_side in sources.items():
        for direction, rows in by_side.items():
            row = rows.iloc[j]
            if not row["signal"]:
                continue
            side = _SIDES[direction]
            level = float(row["level"])
            key = _candidate_key(frame, source, side, j, row)
            out.append(Candidate(key=key, side=side, level=level, sources=(source,)))
    return tuple(out)


def _retests(frame: _Frame, settings: Settings, bars: screens.Bars, p: dict[str, Any],
             sources: Mapping[str, Mapping[str, pd.DataFrame]]) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for source, by_side in sources.items():
        rules = {**p, "retest_of": _RETEST_SOURCES[source], "retest_max_bars": settings.max_age,
                 "retest_tol_atr": settings.retest_tolerance, "retest_reversal": False}
        for direction, rows in by_side.items():
            side = _SIDES[direction]
            for event in retest.retest_events(bars, bars.side(side), rules):
                row = rows.iloc[event.breakout]
                if row["signal"]:
                    key = _candidate_key(frame, source, side, event.breakout, row)
                    out.setdefault(event.bar, []).append(key)
    return out


def _zones_at(frame: _Frame, zones: list[sd_zones.SDZone], tf: str, j: int, wanted: set[int]) -> Iterator[Zone]:
    for z in zones:
        side = _ZONE_SIDES[z.side]
        if side not in wanted or z.state_at(j) not in (sd_zones.FRESH, sd_zones.TESTED):
            continue
        low, high = sorted((float(z.proximal), float(z.distal)))
        key = f"{tf}:{z.kind}:{z.formation}:{side}:{frame.closes[z.base_start].isoformat()}:{frame.closes[z.formed].isoformat()}:{low.hex()}:{high.hex()}"
        yield Zone(key=key, side=side, low=low, high=high, tf=tf)


def _zone_breaks(frame: _Frame, zones: list[sd_zones.SDZone], tf: str, i: int, j: int,
                  at: pd.Timestamp, before: pd.Timestamp, wanted: set[int], bars: screens.Bars,
                  settings: Settings) -> Iterator[Candidate]:
    for z in zones:
        zone_side = _ZONE_SIDES[z.side]
        v = z.violated
        if zone_side not in wanted or v is None or v > i:
            continue
        known = frame.closes if frame.known is None else frame.known
        if not known[z.formed] <= before < known[v] <= at:
            continue
        side: Literal[1, -1] = -1 if zone_side == 1 else 1
        level = float(z.distal)
        if crosses(bars, j, side, level, settings.buffer):
            key = f"ZONE:{tf}:{side}:{frame.closes[z.formed].isoformat()}:{level.hex()}"
            yield Candidate(key=key, side=side, level=level, sources=("ZONE",))


@dataclass(frozen=True)
class _DivergenceSeries:
    known: np.ndarray
    swings: list[Any]

    def last_at(self, j: int) -> Any:
        i = int(np.searchsorted(self.known, j, side="right")) - 1
        return self.swings[i] if i >= 0 else None


def _divergence_reads(frame: _Frame, settings: Settings) -> tuple[dict[tuple[int, str], _DivergenceSeries], np.ndarray]:
    by_kind = {}
    rsi_values = rsi(frame.bars["close"].astype(float), settings.rsi_period).to_numpy(float)
    ready = np.isfinite(rsi_values)
    for side, word in ((1, "bullish"), (-1, "bearish")):
        for kind in ("regular", "hidden"):
            swings = _divergence_swings(frame.bars, word, settings.swing_degree, settings.rsi_period, kind)
            known = np.asarray([s.confirmed for s in swings], dtype=int)
            by_kind[(side, kind)] = _DivergenceSeries(known, swings)
            if kind == "regular":  # regular and hidden use the same causal pivot pairs
                pairs = [b.confirmed for a, b in zip(swings, swings[1:]) if np.isfinite(rsi_values[[a.bar, b.bar]]).all()]
                if pairs:
                    ready[:pairs[0]] = False
                else:
                    ready[:] = False
    return by_kind, ready


def _divergences_at(frame: _Frame, reads: Mapping[tuple[int, str], _DivergenceSeries], tf: str,
                    j: int, max_age: int) -> Iterator[Divergence]:
    for (side, kind), series in reads.items():
        start = int(np.searchsorted(series.known, j - max_age, side="left"))
        end = int(np.searchsorted(series.known, j, side="right"))
        for swing in series.swings[start:end]:
            if not swing.diverges:
                continue
            later = frame.bars["close"].iloc[swing.confirmed + 1:j + 1]
            if ((later - swing.price) * side < 0).any():
                continue
            key = f"{tf}:{side}:{kind}:{frame.closes[swing.bar].isoformat()}:{frame.closes[swing.confirmed].isoformat()}"
            yield Divergence(side=side, kind=kind, active=True, key=key)



def _regime_at(frame: _Frame | None, regime: np.ndarray | None, at: pd.Timestamp) -> int:
    if frame is None or regime is None:
        return 0
    i = frame.last_at(at)
    return _STRUCTURE.get(regime[i], 0) if i >= 0 else 0


def iter_snapshots(
    frames: Mapping[str, pd.DataFrame],
    settings: Settings,
    *,
    as_of: datetime | None = None,
    instrument_is_index: bool = False,
    breakout_overrides: Mapping[str, Any] | None = None,
    zone_settings: Mapping[str, Any] | None = None,
) -> Iterator[Snapshot]:
    """Yield snapshots from B1/B2, raw SD lifecycle, RSI and structure, in close order.

    The caller owns market-calendar completeness and can attest source freshness
    through the timezone-aware ``frame.attrs['available_through']`` horizon. An
    index uses only an explicit, already aligned ``futures_volume`` column.
    Another instrument uses its own ``volume``. Missing volume stays missing.
    """
    when = pd.Timestamp(as_of) if as_of is not None else None
    if when is not None and (pd.isna(when) or when.tzinfo is None):
        raise ValueError("as_of must be a timezone-aware timestamp")
    source_tf = settings.breakout_tf
    zone_sides: dict[str, set[int]] = {}
    for side, names in ((1, settings.demand_tfs), (-1, settings.supply_tfs)):
        for tf in names:
            zone_sides.setdefault(tf, set()).add(side)
    structure_tfs = tuple(dict.fromkeys((settings.structure_tf, *settings.htf_tfs)))
    rsi_tfs = settings.rsi_tfs if settings.divergence_mode != "OFF" else ()
    wanted = tuple(dict.fromkeys((source_tf, *rsi_tfs, *zone_sides, *structure_tfs)))
    data = {tf: _normalize(frames[tf], tf, when) for tf in wanted if tf in frames}
    if source_tf not in data or data[source_tf].bars.empty:
        return

    frame = data[source_tf]
    p, errors = params.validate({**dict(breakout_overrides or {}), "min_break_atr": settings.buffer,
                                 "vol_mult": 0.0, "swing_degree": settings.swing_degree,
                                 "range_mode": settings.range_mode, "range_n": settings.range_n})
    if errors:
        raise ValueError("; ".join(errors))
    source_col = "futures_volume" if instrument_is_index else "volume"
    market = frame.bars.copy()
    raw_volume = market.get(source_col, pd.Series(np.nan, index=market.index))
    volume = pd.to_numeric(raw_volume, errors="coerce").astype(float)
    valid_volume = np.isfinite(volume) & (volume > 0)
    invalid_volume = (raw_volume.notna() & ~valid_volume).to_numpy(bool)
    market["volume"] = volume.where(valid_volume)
    bars = screens.Bars(market, p, tf=source_tf)
    source_rows = _source_rows(frame, source_tf, settings, bars, p)
    retest_by_bar = _retests(frame, settings, bars, p, source_rows) if settings.acceptance == "RETEST_BREAKOUT" else None
    calendar = frame.bars.attrs.get("session_calendar")
    previous = frame.bars.attrs.get("previous_sessions")
    session_by_bar = (session_candidates(bars, frame.bars.index, frame.closes, calendar, previous, settings)
                      if "SESSION" in settings.sources and isinstance(calendar, session.SessionCalendar)
                      and isinstance(previous, dict) and source_tf != "1d" else {})
    relative_volume = bars.rvol
    sd_settings = sd_zones.settings_from(dict(zone_settings or {}))
    min_zone_bars = sd_settings.avg_n + 3
    if sd_settings.kind in ("swing", "both"):
        min_zone_bars = max(min_zone_bars, sd_settings.swing_left + 2)
    zones = {tf: sd_zones.cached_sd_series(data[tf].bars, sd_settings).zones for tf in zone_sides
             if tf in data and not data[tf].bars.empty}
    divergence = {tf: _divergence_reads(data[tf], settings) for tf in rsi_tfs
                  if tf in data and not data[tf].bars.empty}
    regimes = {tf: structure_series.structure_series(data[tf].bars, tf, time_col="bar_end").regime for tf in structure_tfs
               if tf in data and not data[tf].bars.empty}
    for j, at in enumerate(frame.closes):
        if frame.last_at(at) != j:
            continue
        atr_value = float(bars.atr_prev[j])
        atr = atr_value if np.isfinite(atr_value) and atr_value > 0 else None
        zone_facts = []
        candidates = list(_candidates(frame, j, source_rows))
        if "HORIZONTAL" in settings.sources:
            candidates.extend(horizontal_candidates(bars, frame.closes, j, settings))
        candidates.extend(session_by_bar.get(j, ()))
        zones_ready = bool(zone_sides)
        for tf, sides in zone_sides.items():
            i = data[tf].last_at(at) if tf in zones else -1
            if i + 1 < min_zone_bars:
                zones_ready = False
                continue
            zone_facts.extend(_zones_at(data[tf], zones[tf], tf, i, sides))
            if "ZONE" in settings.sources and j:
                candidates.extend(_zone_breaks(data[tf], zones[tf], tf, i, j, at, frame.closes[j - 1], sides, bars, settings))
        divergence_facts = []
        rsi_ready = bool(rsi_tfs)
        for tf in rsi_tfs:
            i = data[tf].last_at(at) if tf in divergence else -1
            if i < 0 or not divergence[tf][1][i]:
                rsi_ready = False
                continue
            divergence_facts.extend(_divergences_at(data[tf], divergence[tf][0], tf, i, settings.divergence_age))
        context = [_regime_at(data.get(tf), regimes.get(tf), at) for tf in settings.htf_tfs]
        htf = context[0] if context and all(x == context[0] for x in context) else 0
        value = float(relative_volume[j]) if np.isfinite(relative_volume[j]) else None
        label = ("proxy" if instrument_is_index else "genuine") if value is not None else (
            "invalid" if invalid_volume[j] else "unavailable"
        )
        yield Snapshot(
            at=at.to_pydatetime(),
            candle=Candle(float(bars.o[j]), float(bars.h[j]), float(bars.l[j]), float(bars.c[j]), atr, value, label),
            candidates=tuple(candidates),
            zones=tuple(zone_facts),
            divergences=tuple(divergence_facts),
            zones_ready=zones_ready,
            rsi_ready=rsi_ready,
            structure=_regime_at(data.get(settings.structure_tf), regimes.get(settings.structure_tf), at),
            htf=htf,
            retested_keys=None if retest_by_bar is None else tuple(retest_by_bar.get(j, ())),
        )
