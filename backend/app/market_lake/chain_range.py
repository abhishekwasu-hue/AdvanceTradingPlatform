"""M96 N1: OI over a from-to range, the replay of OI per strike, and the multi-expiry summary - read from the
tenant-private 1-minute chain store (`md_tenant_chain_snapshots`, app/market_lake/chain_store.py).

Scope: the caller's own data only. Every read goes through `chain_store._visible` - the reader's organisation and,
within it, only the rows the reader may see (a row read through another member's own broker session never shows).
Values are the broker's as stored; a missing one stays None (never guessed, never sample data).

No look-ahead: the OI "at" a time is the newest chain READ (`captured_at`) at or before that time, on the same IST day; a
replay frame carries only its own chain; a summary's OI change compares with the day's first stored chain, never a
later one.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.models import OptionChainRow
from app.market_data.calendar import IST
from app.market_lake import chain_store
from app.market_lake.chain_store import R, StoredChain


def _f(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    out = float(value)
    return out if math.isfinite(out) else None


def _diff(a: float | None, b: float | None) -> float | None:
    return b - a if a is not None and b is not None else None


def _utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)


def meta(chain: StoredChain) -> dict[str, Any]:
    return {"slot": chain.slot_start.astimezone(IST).isoformat(), "captured_at": chain.captured_at.astimezone(IST).isoformat(),
            "spot": chain.spot, "expiry": chain.expiry.isoformat()}


def atm_of(strikes: list[float], spot: float | None) -> float | None:
    if spot is None or not strikes:
        return None
    return min(strikes, key=lambda k: (abs(k - spot), k))


# ---------------------------------------------------------------- 1. OI change between two times (pure)

def oi_diff(start: StoredChain, end: StoredChain) -> dict[str, Any]:
    """Per strike (ascending): call / put OI at `start` and at `end` and the change (end - start). A strike missing in
    one of the two chains, or a missing OI, gives a None change. Totals add only the strikes with both numbers."""
    a = {float(r.strike): r for r in start.rows}
    b = {float(r.strike): r for r in end.rows}
    rows: list[dict[str, Any]] = []
    ce_total = pe_total = 0.0
    for k in sorted(set(a) | set(b)):
        ra, rb = a.get(k), b.get(k)
        ce_from, ce_to = (_f(ra.call_oi) if ra else None), (_f(rb.call_oi) if rb else None)
        pe_from, pe_to = (_f(ra.put_oi) if ra else None), (_f(rb.put_oi) if rb else None)
        ce_change, pe_change = _diff(ce_from, ce_to), _diff(pe_from, pe_to)
        ce_total += ce_change or 0.0
        pe_total += pe_change or 0.0
        rows.append({"strike": k, "ce_from": ce_from, "ce_to": ce_to, "ce_change": ce_change,
                     "pe_from": pe_from, "pe_to": pe_to, "pe_change": pe_change,
                     "diff": pe_change - ce_change if ce_change is not None and pe_change is not None else None})
    return {"from": meta(start), "to": meta(end), "atm": atm_of(sorted(b), end.spot), "rows": rows,
            "totals": {"ce_change": ce_total, "pe_change": pe_total, "diff": pe_total - ce_total}}


# ---------------------------------------------------------------- 2. the replay frames (pure)

def _window(rows: list[OptionChainRow], spot: float | None, around: int) -> list[float]:
    strikes = sorted(float(r.strike) for r in rows)
    if around <= 0 or spot is None or len(strikes) <= 2 * around + 1:
        return strikes
    mid = strikes.index(atm_of(strikes, spot))  # type: ignore[arg-type]
    return strikes[max(0, mid - around): mid + around + 1]


def replay_frames(chains: list[StoredChain], around: int = 0) -> dict[str, Any]:
    """One frame per stored chain (oldest first): its time, spot and the call / put OI per strike. The strikes are those
    within `around` of each frame's own ATM (0 = every strike), joined over the frames - a frame's numbers come only from
    its own chain."""
    chains = sorted(chains, key=lambda c: c.captured_at)
    wanted: set[float] = set()
    for c in chains:
        wanted.update(_window(c.rows, c.spot, around))
    strikes = sorted(wanted)
    frames: list[dict[str, Any]] = []
    for c in chains:
        by = {float(r.strike): r for r in c.rows}
        frames.append({**meta(c), "ce": [_f(by[k].call_oi) if k in by else None for k in strikes],
                       "pe": [_f(by[k].put_oi) if k in by else None for k in strikes]})
    return {"strikes": strikes, "frames": frames}


# ---------------------------------------------------------------- 3. one expiry's summary (pure)

def expiry_summary(chain: StoredChain, first: StoredChain | None, *, holidays: Any = (), rate: float | None = None) -> dict[str, Any]:
    """Total call / put OI, their change since `first` (the day's first stored chain of this expiry), PCR (OI), max pain
    and ATM IV - the live page's own functions (chain_view.build_view) on the stored chain, so the numbers match it."""
    from app.option_chain import chain_view
    kwargs: dict[str, Any] = {} if rate is None else {"rate": rate}
    view = chain_view.build_view(chain.chain(), spot=chain.spot, future=None, expiry=chain.expiry, now=chain.captured_at,
                                 oi=chain_view.OiChange("none", None), holidays=holidays, **kwargs)
    s = view["summary"]
    ce_change = pe_change = None
    if first is not None:
        d = oi_diff(first, chain)
        known = [r for r in d["rows"] if r["ce_change"] is not None or r["pe_change"] is not None]
        if known:
            ce_change, pe_change = d["totals"]["ce_change"], d["totals"]["pe_change"]
    return {**meta(chain), "total_call_oi": s["total_call_oi"], "total_put_oi": s["total_put_oi"], "call_oi_change": ce_change,
            "put_oi_change": pe_change, "pcr_oi": s["pcr_oi"], "max_pain": s["max_pain"], "atm_strike": s["atm_strike"],
            "atm_iv": s["atm_iv"], "days_to_expiry": s["days_to_expiry"],
            "since": meta(first)["captured_at"] if first is not None else None}


# ---------------------------------------------------------------- the store reads (the reader's own rows only)

async def chain_at(session: AsyncSession, tenant_id: int | None, reader_user_id: int | None, underlying: str, expiry: date,
                   at: datetime) -> StoredChain | None:
    """The newest stored chain of `expiry` read at or before `at`, on `at`'s IST day; None when there is none."""
    at = _utc(at)
    query = (select(R).where(chain_store._visible(tenant_id, reader_user_id), R.underlying == underlying, R.expiry == expiry,
                             R.trade_date == at.astimezone(IST).date(), R.captured_at <= at)
             .order_by(R.captured_at.desc()).limit(1))
    row = await session.scalar(query)
    if row is None:
        return None
    return StoredChain(row.underlying, row.expiry, _utc(row.slot_start), _utc(row.captured_at), row.spot, chain_store.decode(row.payload, row.codec))


async def first_of_day(session: AsyncSession, tenant_id: int | None, reader_user_id: int | None, underlying: str, expiry: date,
                       day: date, *, before: datetime) -> StoredChain | None:
    """The day's first stored chain of `expiry` read at or before `before`."""
    query = (select(R).where(chain_store._visible(tenant_id, reader_user_id), R.underlying == underlying, R.expiry == expiry,
                             R.trade_date == day, R.captured_at <= _utc(before)).order_by(R.captured_at).limit(1))
    row = await session.scalar(query)
    if row is None:
        return None
    return StoredChain(row.underlying, row.expiry, _utc(row.slot_start), _utc(row.captured_at), row.spot, chain_store.decode(row.payload, row.codec))


async def day_expiries(session: AsyncSession, tenant_id: int | None, reader_user_id: int | None, underlying: str, day: date) -> list[date]:
    """The expiries with a stored chain on `day` (nearest first)."""
    query = (select(R.expiry).where(chain_store._visible(tenant_id, reader_user_id), R.underlying == underlying, R.trade_date == day)
             .group_by(R.expiry).order_by(R.expiry))
    return [e for e in await session.scalars(query)]


__all__ = ["oi_diff", "replay_frames", "expiry_summary", "chain_at", "first_of_day", "day_expiries", "atm_of", "meta"]
