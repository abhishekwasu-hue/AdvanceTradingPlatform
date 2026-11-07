"""FN2-e: the option-chain functions on the screener run and bar-close alert paths - PCR, ChainBias,
MaxPainDistancePct and FuturesOI - read from the STORED chain snapshots, never from a live chain call per symbol.

* Source (the readers the option-chain pages use, `app/option_chain/routes.py::_day_view`): the organisation's OI
  collector slots (`snapshots.latest_chains_many`, TM-7 tenant scope); with the own-broker data tier on
  (`byob_data_tier`), only the reader's OWN stored chain reads (`chain_store.holder_latest_many`, B1) - never another
  member's, never the collector's.
* Underlying: the symbol's F&O underlying (`master.underlying_of`: `NIFTY 50` -> NIFTY, `RELIANCE` -> RELIANCE).
* No look-ahead: each symbol's cut is its last loaded bar's close (and never later than the run's clock); only a
  snapshot whose chain was read at or before the cut is loaded, and in the series a bar sees it only when the bar
  closed at or after the snapshot was read AND it was read on the bar's own IST session date (an earlier session's
  chain never answers: "option chain last read on <date>", not evaluated). These functions are known for the last bar only (`LAST_BAR_ONLY`: the
  validator refuses reading them back in time), so one snapshot per symbol answers the bar the screen decides on;
  a bar that closed before it reads nothing.
* Expiry: the nearest expiry present in that snapshot (the first on or after the day it was read). The collector
  stores one expiry per slot; an own read may hold several.
* FuturesOI: the near-month future's open interest the collector stored with the same slot (OC5). Own reads carry no
  future, and the store's bars carry no open interest - such a symbol is not evaluated.
* Never guessed: a symbol with no snapshot, or a snapshot without the future's OI, is "not evaluated" with the reason
  (ScreenRuntimeError -> `reasons.NOT_CHECKED`), never a pass or a 0.
Research only - values on a chart's terms, never an order, never a buy / sell word.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import numpy as np
import pandas as pd

from app.screener import nodes

LOADED = frozenset({"PCR", "ChainBias", "MaxPainDistancePct", "FuturesOI"})
WHAT = ("pcr", "bias", "max_pain_distance_pct", "future_oi")
SOURCE_ORG = "your organisation's stored option-chain snapshots"
SOURCE_OWN = "your own stored option-chain reads"


@dataclass(frozen=True)
class ChainPoint:
    captured_at: datetime                    # when the chain was read (data time)
    chain: Any                               # app.brokers.models.OptionChain, one expiry
    future_oi: float | None = None           # the near-month future's open interest in the same slot (collector only)


@dataclass
class ChainTimeline:
    underlying: str
    source: str
    points: list[ChainPoint] = field(default_factory=list)   # oldest first
    reason: str | None = None                                  # why there is no point (shown as "not evaluated")


def calls(ast: Any) -> list[str]:
    """The chain functions a screen uses, in order of appearance."""
    out: list[str] = []
    if ast is None:
        return out
    for node in nodes.walk(ast):
        if isinstance(node, nodes.Call) and node.name in LOADED and node.name not in out:
            out.append(node.name)
    return out


def underlying_for(symbol: str) -> str | None:
    """The chain underlying a screened symbol reads (None: not a valid underlying name)."""
    from app.instruments import master
    from app.option_chain.snapshots import normalise_underlying
    try:
        return normalise_underlying(master.underlying_of(symbol))
    except ValueError:
        return None


def cut_of(data: Any, base_tf: str, now: datetime) -> datetime:
    """The newest instant a symbol's chain may be read at: its last loaded bar's close, never later than `now`."""
    from app.screener.runtime import bar_close
    now = now if now.tzinfo else now.replace(tzinfo=UTC)
    f = data.frames.get(base_tf)
    if f is None or f.empty:
        return now
    idx = pd.DatetimeIndex(f.index[-1:])
    idx = idx.tz_localize("UTC") if idx.tz is None else idx
    close = pd.DatetimeIndex(bar_close(idx, base_tf, getattr(data, "exchange", None)))[0].to_pydatetime()
    return min(close, now)


def nearest_expiry(stored: list[Any]) -> Any | None:
    """Of one slot's own stored chains (every expiry), the nearest expiry present: the first on or after the IST day it
    was read (else the earliest stored)."""
    if not stored:
        return None
    from app.option_chain.snapshots import IST
    day: date = stored[0].captured_at.astimezone(IST).date()
    ahead = [s for s in stored if s.expiry >= day]
    return min(ahead or stored, key=lambda s: s.expiry)


async def attach(session: Any, universe: list[Any], *, tenant_id: int, user_id: int | None, own: bool, base_tf: str,
                 now: datetime | None = None) -> None:
    """Sets `chains` (and `option_chain`, the chain the evidence pack shows) on each SymbolData from the stored snapshots:
    the organisation's collector slots, or with `own` (the own-broker tier) only `user_id`'s own reads. Batched: one
    read for every symbol, never a broker call."""
    if not universe:
        return
    now = now or datetime.now(UTC)
    source = SOURCE_OWN if own else SOURCE_ORG
    cuts: dict[str, datetime] = {}
    under: dict[str, str | None] = {}
    for d in universe:
        u = underlying_for(d.symbol)
        under[d.symbol] = u
        if u is not None:
            cut = cut_of(d, base_tf, now)
            cuts[u] = min(cuts[u], cut) if u in cuts else cut      # two symbols, one underlying: the earlier cut (never later)
    points: dict[str, ChainPoint] = {}
    if cuts and own and user_id is not None:
        from app.market_lake import chain_store
        for u, stored in (await chain_store.holder_latest_many(session, tenant_id, user_id, cuts)).items():
            pick = nearest_expiry(stored)
            if pick is not None:
                points[u] = ChainPoint(pick.captured_at, pick.chain())
    elif cuts and not own:
        from app.option_chain import snapshots
        for u, slot in (await snapshots.latest_chains_many(session, cuts, tenant_id=tenant_id)).items():
            if slot is not None and slot.chain.rows:
                points[u] = ChainPoint(slot.captured_at, slot.chain, slot.future_oi)
    for d in universe:
        u = under[d.symbol]
        point = points.get(u) if u is not None else None
        d.option_chain = point.chain if point is not None else None     # this load's chain only (a cycle may reuse the object)
        if u is None:
            d.chains = ChainTimeline(d.symbol, source, reason=f"{d.symbol} is not an option-chain underlying")
        elif point is None:
            d.chains = ChainTimeline(u, source, reason=f"no stored option chain of {u} at or before this bar's close (read from {source})")
        else:
            d.chains = ChainTimeline(u, source, [point])


def series(data: Any, f: pd.DataFrame, tf: str, what: str) -> pd.Series:
    """One chain measure on every bar of `f`: the snapshot read at or before each bar's close (NaN / None before it).
    The bar the screen decides on (the last) with no snapshot, or FuturesOI with no future in it, is not evaluated."""
    from app.option_chain.snapshots import IST
    from app.screener.runtime import ScreenRuntimeError, _chain
    from app.screener.zones import _closes_ns
    if what not in WHAT:
        raise ScreenRuntimeError(f"unknown option-chain measure {what!r}")
    timeline: ChainTimeline = data.chains
    bias = what == "bias"
    out: np.ndarray = np.full(len(f), None, dtype=object) if bias else np.full(len(f), np.nan)
    if not len(f):
        return pd.Series(out, index=f.index, dtype=object if bias else float)
    if not timeline.points:
        raise ScreenRuntimeError(f"{data.symbol}: {timeline.reason or 'no stored option chain'}")
    caps = np.array([pd.Timestamp(p.captured_at).value for p in timeline.points], dtype="int64")
    closes_ns = _closes_ns(f.index, tf)
    seg = np.searchsorted(caps, closes_ns, side="right") - 1      # the newest snapshot read by each close
    last = int(seg[-1])
    if last < 0:
        raise ScreenRuntimeError(f"{data.symbol}: no stored option chain of {timeline.underlying} at or before this bar's close "
                                 f"(read from {timeline.source})")
    # same session only: a snapshot read on an earlier IST session date never answers a bar (an old chain is not today's)
    # a bar's session date: its own start's IST date (a daily bar's close stamp is the end of its day, which can be the
    # next date); a week or a month: the IST date it closes on (its last session)
    if nodes.TF_MINUTES.get(tf, 0) <= nodes.TF_MINUTES["1d"]:
        starts = pd.DatetimeIndex(f.index)
        bar_days = (starts.tz_localize("UTC") if starts.tz is None else starts).tz_convert(IST).date
    else:
        bar_days = pd.to_datetime(closes_ns, utc=True).tz_convert(IST).date
    read_days = [p.captured_at.astimezone(IST).date() for p in timeline.points]
    same = np.array([s >= 0 and read_days[int(s)] == day for s, day in zip(seg, bar_days)], dtype=bool)
    if not same[-1]:
        raise ScreenRuntimeError(f"{data.symbol}: option chain last read on {read_days[last].isoformat()}")
    seg = np.where(same, seg, -1)
    if what == "future_oi" and timeline.points[last].future_oi is None:
        raise ScreenRuntimeError(f"{data.symbol}: the stored option chain of {timeline.underlying} has no near-month future "
                                 f"open interest (read from {timeline.source})")
    for s in np.unique(seg[seg >= 0]):
        p = timeline.points[int(s)]
        value = (np.nan if p.future_oi is None else float(p.future_oi)) if what == "future_oi" else _chain(p.chain, what)
        out[seg == s] = value
    return pd.Series(out, index=f.index, dtype=object if bias else float)


__all__ = ["LOADED", "WHAT", "ChainPoint", "ChainTimeline", "calls", "underlying_for", "cut_of", "nearest_expiry", "attach", "series"]
