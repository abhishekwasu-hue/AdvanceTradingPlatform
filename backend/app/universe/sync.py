"""U1-a: the equity-list and symbol-change job. Idempotent: the same files twice change nothing.

Rules:
* Securities are keyed by ISIN. A row is updated only when its content checksum changes; `last_seen_on` moves to the
  run's day. A security missing from today's file is NOT marked delisted (only the exchange's delisted list does that).
* Quality gate: an equity list with fewer than `UNIVERSE_MIN_ROWS_RATIO` of the active main-board securities we hold is
  refused (a truncated download must not shrink the universe); a `data_quality_events` row says why.
* Symbol history is derived: for each ISIN, the chain of changes in the exchange's cumulative symbol-change file that
  ends at its current symbol gives the ranges [listing, d1) old ... [dn, -) current. An ISIN whose ranges differ is
  rebuilt; a rebuild that would drop a symbol we already hold is refused (event) - history is never lost.
* A symbol that changed in the equity list with no change record is closed/opened at the run's day, marked
  `observed_rename`, with a quality event: the exact date is unknown and is not invented.
"""
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.db.models import DataQualityEventRecord, SecurityRecord, SymbolHistoryRecord
from app.universe.parsers import LayoutError, ListedSecurity, SymbolChange, parse_equity_list, parse_symbol_changes
from app.universe.sources import ReferenceSource

log = logging.getLogger(__name__)

FILES = (("equity_list", False, False), ("sme_equity_list", True, False), ("etf_list", False, True))


@dataclass
class SyncReport:
    files: Dict[str, str] = field(default_factory=dict)          # name -> checksum (or the reason it was not used)
    inserted: int = 0
    updated: int = 0
    seen: int = 0
    history_rebuilt: int = 0
    observed_renames: int = 0
    refused: List[str] = field(default_factory=list)


def _row_checksum(s: ListedSecurity) -> str:
    raw = "|".join(str(v) for v in (s.isin, s.symbol, s.name, s.series, s.listing_date, s.face_value, s.market_lot, s.is_sme, s.is_etf, s.underlying))
    return hashlib.sha256(raw.encode()).hexdigest()


def _event(session: AsyncSession, kind: str, key: str, detail: str, source: str) -> None:
    session.add(DataQualityEventRecord(kind=kind[:12], instrument_key=key[:64], source=source[:30], detail=detail[:2000]))


async def _active_main_board(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count()).select_from(SecurityRecord).where(
        SecurityRecord.status == "ACTIVE", SecurityRecord.is_sme.is_(False), SecurityRecord.is_etf.is_(False))) or 0)


async def _upsert(session: AsyncSession, listed: Sequence[ListedSecurity], today: date, source: str, report: SyncReport) -> None:
    now = datetime.now(timezone.utc)
    existing = {r.isin: r for r in await session.scalars(select(SecurityRecord).where(SecurityRecord.isin.in_([s.isin for s in listed])))}
    for s in listed:
        checksum, row = _row_checksum(s), existing.get(s.isin)
        report.seen += 1
        if row is None:
            session.add(SecurityRecord(isin=s.isin, symbol=s.symbol, name=s.name, series=s.series, listing_date=s.listing_date,
                                       face_value=s.face_value, market_lot=s.market_lot, is_sme=s.is_sme, is_etf=s.is_etf,
                                       underlying=s.underlying, last_seen_on=today, source=source, fetched_at=now, checksum=checksum))
            session.add(SymbolHistoryRecord(isin=s.isin, symbol=s.symbol, valid_from=s.listing_date, valid_to=None,
                                            source=source, fetched_at=now, checksum=checksum))
            report.inserted += 1
            continue
        row.last_seen_on = today
        if row.checksum == checksum:
            continue
        if row.symbol != s.symbol:
            await _observed_rename(session, row, s.symbol, today, source, now, checksum)
            report.observed_renames += 1
        for name in ("symbol", "name", "series", "listing_date", "face_value", "market_lot", "is_sme", "is_etf", "underlying"):
            setattr(row, name, getattr(s, name))
        row.source, row.fetched_at, row.checksum = source, now, checksum
        report.updated += 1


async def _observed_rename(session, row: SecurityRecord, new_symbol: str, today: date, source: str, now, checksum: str) -> None:
    open_ranges = list(await session.scalars(select(SymbolHistoryRecord).where(SymbolHistoryRecord.isin == row.isin, SymbolHistoryRecord.valid_to.is_(None))))
    for r in open_ranges:
        r.valid_to = today
    session.add(SymbolHistoryRecord(isin=row.isin, symbol=new_symbol, valid_from=today, valid_to=None, source="observed_rename",
                                    fetched_at=now, checksum=checksum))
    _event(session, "RENAME_NOREC", row.isin, f"{row.symbol} -> {new_symbol} seen in the equity list on {today}; no symbol-change record (date unknown)", source)


def desired_ranges(current: str, listing: Optional[date], changes: Sequence[SymbolChange]) -> List[Tuple[str, Optional[date], Optional[date]]]:
    """[(symbol, valid_from, valid_to)] for an ISIN whose current symbol is `current`; [] when no change leads to it."""
    by_new: Dict[str, List[SymbolChange]] = {}
    for c in changes:
        by_new.setdefault(c.new_symbol, []).append(c)
    chain: List[SymbolChange] = []
    symbol, before, seen = current, None, {current}
    while True:
        options = [c for c in by_new.get(symbol, []) if before is None or c.effective < before]
        if not options:
            break
        change = max(options, key=lambda c: c.effective)
        chain.append(change)
        symbol, before = change.old_symbol, change.effective
        if symbol in seen:              # a loop in the file (A -> B -> A): stop rather than walk forever
            break
        seen.add(symbol)
    if not chain:
        return []
    chain.reverse()                     # oldest first
    out, start = [], listing
    for change in chain:
        if start is not None and change.effective <= start:
            continue                    # a change dated before the listing we hold belongs to an earlier life of the symbol
        out.append((change.old_symbol, start, change.effective))
        start = change.effective
    out.append((current, start, None))
    return out if len(out) > 1 else []


async def _rebuild_history(session: AsyncSession, changes: Sequence[SymbolChange], source: str, checksum: str, report: SyncReport) -> None:
    if not changes:
        return
    now = datetime.now(timezone.utc)
    targets = {c.new_symbol for c in changes}
    for sec in await session.scalars(select(SecurityRecord).where(SecurityRecord.symbol.in_(targets))):
        want = desired_ranges(sec.symbol, sec.listing_date, changes)
        if not want:
            continue
        have = list(await session.scalars(select(SymbolHistoryRecord).where(SymbolHistoryRecord.isin == sec.isin)))
        if {(r.symbol, r.valid_from, r.valid_to) for r in have} == set(want):
            continue
        if not {r.symbol for r in have} <= {w[0] for w in want}:
            _event(session, "HIST_SHRINK", sec.isin, f"symbol-change file would drop {sorted({r.symbol for r in have} - {w[0] for w in want})}; kept what we hold", source)
            continue
        for r in have:
            await session.delete(r)
        await session.flush()
        for symbol, start, end in want:
            session.add(SymbolHistoryRecord(isin=sec.isin, symbol=symbol, valid_from=start, valid_to=end, source=source, fetched_at=now, checksum=checksum))
        report.history_rebuilt += 1


async def sync_equity_lists(session: AsyncSession, source: ReferenceSource, today: date, *, commit: bool = True) -> SyncReport:
    report = SyncReport()
    for name, sme, etf in FILES:
        fetched = await source.fetch(name)
        if fetched is None:
            report.files[name] = "not available"
            continue
        try:
            listed = parse_equity_list(fetched.content, sme=sme, etf=etf)
        except LayoutError as exc:
            report.refused.append(f"{name}: {exc}")
            _event(session, "LAYOUT", name, str(exc), fetched.source)
            continue
        if name == "equity_list":
            held = await _active_main_board(session)
            if held and len(listed) < config.UNIVERSE_MIN_ROWS_RATIO * held:
                why = f"{len(listed)} rows against {held} active securities held (minimum ratio {config.UNIVERSE_MIN_ROWS_RATIO})"
                report.refused.append(f"{name}: {why}")
                _event(session, "SHORT_FILE", name, why, fetched.source)
                continue
        report.files[name] = fetched.checksum
        await _upsert(session, listed, today, fetched.source, report)
    await session.flush()
    fetched = await source.fetch("symbol_changes")
    if fetched is not None:
        try:
            changes = parse_symbol_changes(fetched.content)
            report.files["symbol_changes"] = fetched.checksum
            await _rebuild_history(session, changes, fetched.source, fetched.checksum, report)
        except LayoutError as exc:
            report.refused.append(f"symbol_changes: {exc}")
            _event(session, "LAYOUT", "symbol_changes", str(exc), fetched.source)
    if commit:
        await session.commit()
    log.info("Universe sync %s: %s", today, report)
    return report
