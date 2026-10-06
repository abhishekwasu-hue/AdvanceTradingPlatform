"""Phase W: record option-chain quotes as the worker sees them, so option backtests can later
be priced from real premiums (app/backtest/options.py `SnapshotPricer`).

Sampling, not streaming: one capture per underlying per `CHAIN_SNAPSHOT_INTERVAL_MINUTES`
(default 5, 0 disables), only the `CHAIN_SNAPSHOT_ATM_SPAN` strikes either side of the money,
only while the exchange is open, only for the underlyings of ACTIVE option deployments. That is
roughly 50 rows a capture and a few thousand a day per underlying; retention trims rows older
than `RETENTION_CHAIN_SNAPSHOTS_DAYS` (default 400). The table is platform-wide: the first
tenant's worker cycle to reach an underlying in an interval records it for everyone, and a
capture within the interval by another tenant is skipped.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.backtest.options import OptionChainSnapshotRow
from app.brokers.models import OptionChain
from app.db.models import OptionChainSnapshotRecord
from app.instruments.master import normalise_expiry

logger = logging.getLogger(__name__)

CHAIN_SNAPSHOT_INTERVAL_MINUTES = int(os.environ.get("CHAIN_SNAPSHOT_INTERVAL_MINUTES", "5"))
CHAIN_SNAPSHOT_ATM_SPAN = int(os.environ.get("CHAIN_SNAPSHOT_ATM_SPAN", "12"))
MAX_ROWS_PER_QUERY = 200_000


def recording_enabled() -> bool:
    return CHAIN_SNAPSHOT_INTERVAL_MINUTES > 0


def rows_from_chain(chain: OptionChain, underlying: str, now: datetime, *, span: int = CHAIN_SNAPSHOT_ATM_SPAN,
                    source: str = "worker") -> List[OptionChainSnapshotRecord]:
    """The strikes around the money with a real LTP, as records. No LTP, no row - the recorder
    never invents a quote."""
    expiry = normalise_expiry(chain.expiry)
    if expiry is None or not chain.rows:
        return []
    spot = chain.underlying_ltp
    rows = sorted(chain.rows, key=lambda r: r.strike)
    if spot and spot > 0:
        atm = min(range(len(rows)), key=lambda i: abs(rows[i].strike - spot))
        rows = rows[max(0, atm - span): atm + span + 1]
    out: List[OptionChainSnapshotRecord] = []
    for row in rows:
        for right, ltp, iv, oi in (("CE", row.call_ltp, row.call_iv, row.call_oi), ("PE", row.put_ltp, row.put_iv, row.put_oi)):
            if ltp is None or ltp <= 0:
                continue
            out.append(OptionChainSnapshotRecord(underlying=underlying, expiry=expiry, captured_at=now, strike=float(row.strike), right=right,
                                                 ltp=float(ltp), iv=float(iv) if iv is not None else None, oi=float(oi) if oi is not None else None,
                                                 underlying_ltp=float(spot) if spot else None, source=source))
    return out


async def last_capture(session: AsyncSession, underlying: str, *, since: Optional[datetime] = None,
                       until: Optional[datetime] = None) -> Optional[datetime]:
    """The newest capture for the underlying, optionally within (since, until]. The recorder asks
    for the current interval only, so a backfilled or future-dated upload never blocks live
    recording."""
    q = select(func.max(OptionChainSnapshotRecord.captured_at)).where(OptionChainSnapshotRecord.underlying == underlying)
    if since is not None:
        q = q.where(OptionChainSnapshotRecord.captured_at > since)
    if until is not None:
        q = q.where(OptionChainSnapshotRecord.captured_at <= until)
    return await session.scalar(q)


async def record_chain(session: AsyncSession, underlying: str, chain: OptionChain, now: Optional[datetime] = None, *,
                       interval_minutes: int = CHAIN_SNAPSHOT_INTERVAL_MINUTES, span: int = CHAIN_SNAPSHOT_ATM_SPAN, source: str = "worker") -> int:
    """Stores the sampled rows unless a capture for this underlying exists within the interval.
    Returns the number of rows written. Commits."""
    now = now or datetime.now(timezone.utc)
    interval = timedelta(minutes=max(1, interval_minutes))
    if await last_capture(session, underlying, since=now - interval, until=now) is not None:
        return 0
    rows = rows_from_chain(chain, underlying, now, span=span, source=source)
    if not rows:
        return 0
    session.add_all(rows)
    await session.commit()
    return len(rows)


async def store_uploaded(session: AsyncSession, underlying: str, rows: Sequence[OptionChainSnapshotRow], *, source: str = "upload") -> int:
    """Persist user-supplied history (a CSV export of a chain recorder, say) - Phase W lets a
    tenant bring its own quotes. Exact duplicates (same contract and timestamp) are skipped."""
    if not rows:
        return 0
    stamps = {r.timestamp.astimezone(timezone.utc) if r.timestamp.tzinfo else r.timestamp.replace(tzinfo=timezone.utc) for r in rows}
    existing = set()
    if stamps:
        q = select(OptionChainSnapshotRecord.expiry, OptionChainSnapshotRecord.strike, OptionChainSnapshotRecord.right, OptionChainSnapshotRecord.captured_at).where(
            and_(OptionChainSnapshotRecord.underlying == underlying, OptionChainSnapshotRecord.captured_at >= min(stamps),
                 OptionChainSnapshotRecord.captured_at <= max(stamps)))
        for expiry, strike, right, captured in (await session.execute(q)).all():
            if captured is not None and captured.tzinfo is None:
                captured = captured.replace(tzinfo=timezone.utc)
            existing.add((expiry, float(strike), right, captured))
    written = 0
    for r in rows:
        ts = r.timestamp.astimezone(timezone.utc) if r.timestamp.tzinfo else r.timestamp.replace(tzinfo=timezone.utc)
        key = (r.expiry, float(r.strike), r.right.upper(), ts)
        if key in existing:
            continue
        existing.add(key)
        session.add(OptionChainSnapshotRecord(underlying=underlying, expiry=r.expiry, captured_at=ts, strike=float(r.strike), right=r.right.upper(),
                                              ltp=float(r.ltp), iv=r.iv, oi=r.oi, underlying_ltp=r.underlying_ltp, source=source))
        written += 1
    await session.commit()
    return written


async def load_rows(session: AsyncSession, underlying: str, start: datetime, end: datetime, *, limit: int = MAX_ROWS_PER_QUERY) -> List[OptionChainSnapshotRow]:
    """Recorded quotes for the underlying between two instants (the candle span of a backtest)."""
    q = (select(OptionChainSnapshotRecord)
         .where(and_(OptionChainSnapshotRecord.underlying == underlying, OptionChainSnapshotRecord.captured_at >= start,
                     OptionChainSnapshotRecord.captured_at <= end))
         .order_by(OptionChainSnapshotRecord.captured_at, OptionChainSnapshotRecord.id).limit(limit))
    return [OptionChainSnapshotRow(timestamp=r.captured_at if r.captured_at.tzinfo else r.captured_at.replace(tzinfo=timezone.utc), expiry=r.expiry,
                                   strike=r.strike, right=r.right, ltp=r.ltp, iv=r.iv, oi=r.oi, underlying_ltp=r.underlying_ltp)
            for r in await session.scalars(q)]


async def coverage(session: AsyncSession, underlying: Optional[str] = None) -> List[Dict]:
    """What has been recorded: per underlying, the span, row count and distinct capture days."""
    q = select(OptionChainSnapshotRecord.underlying, func.min(OptionChainSnapshotRecord.captured_at), func.max(OptionChainSnapshotRecord.captured_at),
               func.count(OptionChainSnapshotRecord.id), func.count(func.distinct(OptionChainSnapshotRecord.expiry))).group_by(OptionChainSnapshotRecord.underlying)
    if underlying:
        q = q.where(OptionChainSnapshotRecord.underlying == underlying)
    out = []
    for name, first, last, rows, expiries in (await session.execute(q)).all():
        out.append({"underlying": name, "from": first.isoformat() if first else None, "to": last.isoformat() if last else None,
                    "rows": int(rows), "expiries": int(expiries)})
    return sorted(out, key=lambda r: r["underlying"])
