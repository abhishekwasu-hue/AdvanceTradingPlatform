"""WL1 (handover item 4): NSE's industry classification for every listed company, from one file - when the operator has one.

The index constituent files (U1-b, `indices.py`) carry only NSE's *sector* level, and only for index members (NIFTY
TOTAL MARKET + MICROCAP 250 hold about a thousand companies). A full classification file gives all four levels
(macro-economic sector -> sector -> industry -> basic industry) for every listed company. Its URL is config
(`UNIVERSE_CLASSIFICATION_URL`, empty by default: NSE publishes no documented bulk file we could confirm - see
docs/DATA_SOURCES.md and OPEN_QUESTIONS WL1-1); the fetch goes through the same polite `ReferenceSource` seam, so a
manual upload works the same way. Nothing from the file is committed to git.

Layout: a CSV with a `Symbol` and/or `ISIN` column and any of `Macro-Economic Sector`, `Sector`, `Industry`,
`Basic Industry` (headers are matched loosely, as in `parsers.py`). A row is applied by ISIN (the ISIN column, else the
symbol's ISIN in `securities`); a changed value closes the open classification range and opens a new one, exactly as
the constituent files do. A row whose company we do not hold is counted as unknown, never invented.

Precedence: once a company carries the finer levels from this file, a constituent file's sector-only value does not
overwrite it (`indices._classify` skips rows that have an industry).
"""
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.db.models import ClassificationRecord, DataQualityEventRecord, SecurityRecord
from app.universe.parsers import ISIN_RE, LayoutError, _rows
from app.universe.sources import ReferenceSource

log = logging.getLogger(__name__)
FILE = "classification"

_HEADERS = {
    "symbol": ("SYMBOL", "NSESYMBOL", "TICKER"),
    "isin": ("ISIN", "ISINCODE", "ISINNUMBER", "ISINNO"),
    "macro_sector": ("MACROECONOMICSECTOR", "MACROSECTOR", "MACROECONOMICINDICATOR", "MES"),
    "sector": ("SECTOR",),
    "industry": ("INDUSTRY",),
    "basic_industry": ("BASICINDUSTRY", "BASICIND"),
}
LEVELS = ("macro_sector", "sector", "industry", "basic_industry")


@dataclass(frozen=True)
class Classified:
    symbol: str
    isin: Optional[str]
    macro_sector: Optional[str]
    sector: Optional[str]
    industry: Optional[str]
    basic_industry: Optional[str]

    def levels(self) -> Tuple[Optional[str], ...]:
        return (self.macro_sector, self.sector, self.industry, self.basic_industry)


@dataclass
class ClassificationReport:
    rows: int = 0
    changed: int = 0
    unknown: List[str] = field(default_factory=list)
    refused: Optional[str] = None


def _norm(header: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (header or "").upper())


def parse_classification(content: bytes) -> List[Classified]:
    rows = _rows(content)
    if not rows:
        raise LayoutError("empty file")
    normed = [_norm(h) for h in rows[0]]
    cols: Dict[str, int] = {}
    for name, aliases in _HEADERS.items():
        for alias in aliases:
            if alias in normed:
                cols[name] = normed.index(alias)
                break
    if "symbol" not in cols and "isin" not in cols:
        raise LayoutError(f"unexpected file layout: no Symbol or ISIN column (headers: {rows[0]})")
    if not any(level in cols for level in LEVELS):
        raise LayoutError(f"unexpected file layout: no classification column (headers: {rows[0]})")
    out: List[Classified] = []
    for row in rows[1:]:
        def cell(name: str) -> str:
            i = cols.get(name)
            return " ".join(row[i].split()) if i is not None and i < len(row) else ""
        isin = cell("isin").upper()
        symbol = cell("symbol").upper()
        if not symbol and not ISIN_RE.match(isin):
            continue
        values = {level: (cell(level) or None) for level in LEVELS}
        if not any(values.values()):
            continue
        out.append(Classified(symbol=symbol, isin=isin if ISIN_RE.match(isin) else None, **values))
    return out


def _event(session: AsyncSession, kind: str, key: str, detail: str, source: str) -> None:
    session.add(DataQualityEventRecord(kind=kind[:12], instrument_key=key[:64], source=source[:30], detail=detail[:2000]))


async def apply_classification(session: AsyncSession, rows: List[Classified], today: date, source: str, checksum: str) -> ClassificationReport:
    report = ClassificationReport(rows=len(rows))
    now = datetime.now(timezone.utc)
    held_full = int(await session.scalar(select(func.count()).select_from(ClassificationRecord).where(
        ClassificationRecord.scheme == "NSE", ClassificationRecord.valid_to.is_(None), ClassificationRecord.industry.is_not(None))) or 0)
    if not rows:
        report.refused = "empty classification file"
    elif held_full and len(rows) < config.UNIVERSE_MIN_ROWS_RATIO * held_full:
        report.refused = f"{len(rows)} rows against {held_full} companies classified from the last file"
    if report.refused:
        _event(session, "CLASSIFY", FILE, report.refused, source)
        return report
    by_symbol = {s: i for s, i in (await session.execute(select(SecurityRecord.symbol, SecurityRecord.isin))).all()}
    wanted: Dict[str, Classified] = {}
    for r in rows:
        isin = r.isin or by_symbol.get(r.symbol)
        if not isin:
            report.unknown.append(r.symbol or "?")
            continue
        wanted[isin] = r
    open_rows = {r.isin: r for r in await session.scalars(select(ClassificationRecord).where(
        ClassificationRecord.isin.in_(list(wanted)), ClassificationRecord.scheme == "NSE", ClassificationRecord.valid_to.is_(None)))}
    seen = set(await session.scalars(select(ClassificationRecord.isin).where(ClassificationRecord.isin.in_(list(wanted))).distinct()))
    for isin, r in wanted.items():
        current = open_rows.get(isin)
        if current is not None and (current.macro_sector, current.sector, current.industry, current.basic_industry) == r.levels():
            continue
        if current is not None and current.valid_from == today:            # two files the same day: the later one wins
            current.macro_sector, current.sector, current.industry, current.basic_industry = r.levels()
            current.source, current.checksum, current.fetched_at = source[:40], checksum, now
            report.changed += 1
            continue
        if current is not None:
            current.valid_to = today
        session.add(ClassificationRecord(isin=isin, scheme="NSE", macro_sector=r.macro_sector, sector=r.sector, industry=r.industry,
                                         basic_industry=r.basic_industry, valid_from=today, valid_to=None,
                                         start_observed=current is None and isin not in seen, source=source[:40], fetched_at=now,
                                         checksum=checksum))
        report.changed += 1
    if report.unknown:
        _event(session, "UNKNOWN_SYM", FILE, f"classified companies not in securities: {', '.join(report.unknown[:20])}", source)
    return report


async def sync_classification(session: AsyncSession, source: ReferenceSource, today: date, *, commit: bool = True) -> Optional[ClassificationReport]:
    """The full classification file, when the source has one (None: not configured / not fetched)."""
    fetched = await source.fetch(FILE)
    if fetched is None:
        return None
    try:
        rows = parse_classification(fetched.content)
    except LayoutError as exc:
        _event(session, "LAYOUT", FILE, str(exc), fetched.source)
        if commit:
            await session.commit()
        return ClassificationReport(refused=str(exc))
    report = await apply_classification(session, rows, today, fetched.source, fetched.checksum)
    if commit:
        await session.commit()
    log.info("Classification sync %s: %s rows, %s changed, %s unknown%s", today, report.rows, report.changed, len(report.unknown),
             f", refused: {report.refused}" if report.refused else "")
    return report


__all__ = ["parse_classification", "apply_classification", "sync_classification", "Classified", "ClassificationReport", "FILE"]
