"""Phase W: record option-chain quotes as the worker sees them, so option backtests can later
be priced from real premiums (app/backtest/options.py `SnapshotPricer`).

Sampling, not streaming: one capture per underlying per `CHAIN_SNAPSHOT_INTERVAL_MINUTES`
(default 5, 0 disables), only the `CHAIN_SNAPSHOT_ATM_SPAN` strikes either side of the money,
only while the exchange is open, only for the underlyings of ACTIVE option deployments. That is
roughly 50 rows a capture and a few thousand a day per underlying; retention trims rows older
than `RETENTION_CHAIN_SNAPSHOTS_DAYS` (default 400).

TM94-3 (owner, 23 Oct; the TM-7 rule of the OI collector): every capture is read through ONE organisation's own broker
session (its worker cycle's data adapter) and stored under that organisation (`tenant_id`); each organisation records its
own underlyings on its own interval, and every reader here takes the reader's tenant id and returns only that
organisation's rows. Rows recorded before the fix carry no tenant (they were platform-wide: the first organisation's cycle
to reach an underlying recorded it for everyone) and are read only by the confirmed owner (`legacy_owner`:
HISTORY_OWNER_TENANT_ID confirmed by HISTORY_OWNER_TENANT_NAME, HB0's rule); with no confirmed owner nobody reads them.

Z10-c: an uploaded row (`source = "upload:<tenant id>"`) is that organisation's own history. Only the uploader's
backtests and reads see it, and it never counts as the recorder's capture, so one organisation's upload neither prices
another's backtest nor stops the live recording.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence

from sqlalchemy import and_, false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.backtest.options import OptionChainSnapshotRow
from app.brokers.models import OptionChain
from app.core import config
from app.db.models import OptionChainSnapshotRecord, Tenant
from app.instruments.master import normalise_expiry

logger = logging.getLogger(__name__)

CHAIN_SNAPSHOT_INTERVAL_MINUTES = int(os.environ.get("CHAIN_SNAPSHOT_INTERVAL_MINUTES", "5"))
CHAIN_SNAPSHOT_ATM_SPAN = int(os.environ.get("CHAIN_SNAPSHOT_ATM_SPAN", "12"))
MAX_ROWS_PER_QUERY = 200_000


UPLOAD_SOURCE = "upload"


def recording_enabled() -> bool:
    return CHAIN_SNAPSHOT_INTERVAL_MINUTES > 0


def upload_source(tenant_id: int) -> str:
    return f"{UPLOAD_SOURCE}:{int(tenant_id)}"


def _require_tenant(tenant_id: Optional[int]) -> int:
    if tenant_id is None or int(tenant_id) <= 0:
        raise PermissionError("Recorded option chains are stored per organisation; a tenant id is required")
    return int(tenant_id)


def _recorded():
    """Rows the recorder captured (not uploads)."""
    return ~OptionChainSnapshotRecord.source.like(f"{UPLOAD_SOURCE}%")


def visible_to(tenant_id: Optional[int], *, legacy: bool = False):
    """TM94-3: the organisation's own recorded rows plus its own uploads (Z10-c); with `legacy` (the caller is the
    confirmed owner, see `scope`) also the recorder's rows stored before the fix (no tenant). No organisation: nothing."""
    if tenant_id is None:
        return false()
    tenant = int(tenant_id)
    own = or_(and_(_recorded(), OptionChainSnapshotRecord.tenant_id == tenant), OptionChainSnapshotRecord.source == upload_source(tenant))
    if legacy:
        return or_(own, and_(_recorded(), OptionChainSnapshotRecord.tenant_id.is_(None)))
    return own


async def legacy_owner(session: AsyncSession) -> Optional[int]:
    """The organisation that reads the rows recorded before TM94-3 (no tenant on the row): the owner's, when
    HISTORY_OWNER_TENANT_ID names a tenant whose name equals HISTORY_OWNER_TENANT_NAME; else None (nobody reads them)."""
    tenant_id = config.HISTORY_OWNER_TENANT_ID
    expected = (config.HISTORY_OWNER_TENANT_NAME or "").strip()
    if tenant_id is None or not expected:
        return None
    tenant = await session.get(Tenant, tenant_id)
    return int(tenant_id) if tenant is not None and (tenant.name or "").strip() == expected else None


async def scope(session: AsyncSession, tenant_id: Optional[int]):
    """The WHERE clause of what `tenant_id` reads: `visible_to`, with the pre-fix rows only for the confirmed owner."""
    if tenant_id is None:
        return false()
    return visible_to(tenant_id, legacy=await legacy_owner(session) == int(tenant_id))


def rows_from_chain(chain: OptionChain, underlying: str, now: datetime, *, tenant_id: int, span: int = CHAIN_SNAPSHOT_ATM_SPAN,
                    source: str = "worker") -> List[OptionChainSnapshotRecord]:
    """The strikes around the money with a real LTP, as `tenant_id`'s records. No LTP, no row - the recorder
    never invents a quote."""
    tenant = _require_tenant(tenant_id)
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
            out.append(OptionChainSnapshotRecord(tenant_id=tenant, underlying=underlying, expiry=expiry, captured_at=now, strike=float(row.strike), right=right,
                                                 ltp=float(ltp), iv=float(iv) if iv is not None else None, oi=float(oi) if oi is not None else None,
                                                 underlying_ltp=float(spot) if spot else None, source=source))
    return out


async def last_capture(session: AsyncSession, underlying: str, *, tenant_id: int, since: Optional[datetime] = None,
                       until: Optional[datetime] = None) -> Optional[datetime]:
    """The organisation's newest capture for the underlying, optionally within (since, until]. The recorder asks
    for the current interval only, so a backfilled or future-dated upload never blocks live
    recording, and another organisation's capture never stands in for this one's (TM94-3)."""
    tenant = _require_tenant(tenant_id)
    q = select(func.max(OptionChainSnapshotRecord.captured_at)).where(OptionChainSnapshotRecord.underlying == underlying, _recorded(),
                                                                      OptionChainSnapshotRecord.tenant_id == tenant)
    if since is not None:
        q = q.where(OptionChainSnapshotRecord.captured_at > since)
    if until is not None:
        q = q.where(OptionChainSnapshotRecord.captured_at <= until)
    return await session.scalar(q)


async def record_chain(session: AsyncSession, underlying: str, chain: OptionChain, now: Optional[datetime] = None, *, tenant_id: int,
                       interval_minutes: int = CHAIN_SNAPSHOT_INTERVAL_MINUTES, span: int = CHAIN_SNAPSHOT_ATM_SPAN, source: str = "worker") -> int:
    """Stores the sampled rows of `chain` - read through `tenant_id`'s own broker session - as that organisation's,
    unless it already holds a capture for this underlying within the interval. Returns the number of rows written.
    Commits."""
    tenant = _require_tenant(tenant_id)
    now = now or datetime.now(timezone.utc)
    interval = timedelta(minutes=max(1, interval_minutes))
    if await last_capture(session, underlying, tenant_id=tenant, since=now - interval, until=now) is not None:
        return 0
    rows = rows_from_chain(chain, underlying, now, tenant_id=tenant, span=span, source=source)
    if not rows:
        return 0
    session.add_all(rows)
    await session.commit()
    return len(rows)


async def store_uploaded(session: AsyncSession, underlying: str, rows: Sequence[OptionChainSnapshotRow], *, tenant_id: int) -> int:
    """Persist user-supplied history (a CSV export of a chain recorder, say) - Phase W lets a
    tenant bring its own quotes. Exact duplicates (same contract and timestamp) of a row this tenant
    can already see are skipped. Z10-c: the rows are the tenant's own (`upload:<tenant id>`)."""
    if not rows:
        return 0
    tenant = _require_tenant(tenant_id)
    source = upload_source(tenant)
    stamps = {r.timestamp.astimezone(timezone.utc) if r.timestamp.tzinfo else r.timestamp.replace(tzinfo=timezone.utc) for r in rows}
    existing = set()
    if stamps:
        q = select(OptionChainSnapshotRecord.expiry, OptionChainSnapshotRecord.strike, OptionChainSnapshotRecord.right, OptionChainSnapshotRecord.captured_at).where(
            and_(OptionChainSnapshotRecord.underlying == underlying, OptionChainSnapshotRecord.captured_at >= min(stamps),
                 OptionChainSnapshotRecord.captured_at <= max(stamps), await scope(session, tenant)))
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
        session.add(OptionChainSnapshotRecord(tenant_id=tenant, underlying=underlying, expiry=r.expiry, captured_at=ts, strike=float(r.strike), right=r.right.upper(),
                                              ltp=float(r.ltp), iv=r.iv, oi=r.oi, underlying_ltp=r.underlying_ltp, source=source))
        written += 1
    await session.commit()
    return written


async def load_rows(session: AsyncSession, underlying: str, start: datetime, end: datetime, *, tenant_id: Optional[int],
                    limit: int = MAX_ROWS_PER_QUERY) -> List[OptionChainSnapshotRow]:
    """Recorded quotes for the underlying between two instants (the candle span of a backtest): `tenant_id`'s own
    recorded rows and uploads (and the pre-fix rows for the confirmed owner), never another organisation's. No
    organisation: no rows."""
    if tenant_id is None:
        return []
    q = (select(OptionChainSnapshotRecord)
         .where(and_(OptionChainSnapshotRecord.underlying == underlying, OptionChainSnapshotRecord.captured_at >= start,
                     OptionChainSnapshotRecord.captured_at <= end, await scope(session, tenant_id)))
         .order_by(OptionChainSnapshotRecord.captured_at, OptionChainSnapshotRecord.id).limit(limit))
    return [OptionChainSnapshotRow(timestamp=r.captured_at if r.captured_at.tzinfo else r.captured_at.replace(tzinfo=timezone.utc), expiry=r.expiry,
                                   strike=r.strike, right=r.right, ltp=r.ltp, iv=r.iv, oi=r.oi, underlying_ltp=r.underlying_ltp)
            for r in await session.scalars(q)]


async def coverage(session: AsyncSession, underlying: Optional[str] = None, *, tenant_id: Optional[int] = None) -> List[Dict]:
    """What has been recorded: per underlying, the span, row count and distinct capture days (`tenant_id`'s own recorded
    rows and uploads, and the pre-fix rows for the confirmed owner). No organisation: nothing."""
    if tenant_id is None:
        return []
    q = select(OptionChainSnapshotRecord.underlying, func.min(OptionChainSnapshotRecord.captured_at), func.max(OptionChainSnapshotRecord.captured_at),
               func.count(OptionChainSnapshotRecord.id), func.count(func.distinct(OptionChainSnapshotRecord.expiry))).where(
        await scope(session, tenant_id)).group_by(OptionChainSnapshotRecord.underlying)
    if underlying:
        q = q.where(OptionChainSnapshotRecord.underlying == underlying)
    out = []
    for name, first, last, rows, expiries in (await session.execute(q)).all():
        out.append({"underlying": name, "from": first.isoformat() if first else None, "to": last.isoformat() if last else None,
                    "rows": int(rows), "expiries": int(expiries)})
    return sorted(out, key=lambda r: r["underlying"])
