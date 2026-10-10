"""U1-b: the index catalogue, index membership as-of and the NSE industry classification.

* The catalogue (`data/indices.json`) is loaded into `indices` (upsert by code, checksum per row).
* A constituent file (`Company Name, Industry, Symbol, Series, ISIN Code`) is diffed against the open membership
  ranges: new ISINs open a range from the run's day, missing ones close theirs at the run's day (not a member on that
  day). The first file ever seen for an index opens every range with `start_observed=True` - the true inclusion date
  is unknown, so as-of reads before it return nothing for that index rather than a guess.
* Gates: an empty file, a file with fewer than `UNIVERSE_MIN_ROWS_RATIO` of the members held, or one that would change
  more than `UNIVERSE_MAX_CHURN_RATIO` of them in a day is refused with a quality event (a provider glitch must not
  rewrite history). Every membership change is in the report (the rebalance diff).
* The file's `Industry` column is NSE's sector level (provisional, OPEN_QUESTIONS U1-Q3); the other levels stay NULL
  until a source gives them. A changed value closes the classification range and opens a new one.
"""
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.db.models import ClassificationRecord, DataQualityEventRecord, IndexMembershipRecord, IndexRecord, SecurityRecord
from app.universe.parsers import ISIN_RE, LayoutError, _columns, _rows
from app.universe.sources import ReferenceSource

log = logging.getLogger(__name__)
CATALOGUE = Path(__file__).resolve().parent / "data" / "indices.json"


@dataclass(frozen=True)
class Constituent:
    isin: str
    symbol: str
    name: str
    industry: Optional[str]
    weight: Optional[float] = None


@dataclass
class IndexReport:
    index_code: str
    entered: List[str] = field(default_factory=list)
    exited: List[str] = field(default_factory=list)
    classified: int = 0
    unknown_isins: List[str] = field(default_factory=list)
    refused: Optional[str] = None


def catalogue() -> List[dict]:
    return json.loads(CATALOGUE.read_text(encoding="utf-8"))["indices"]


def source_urls() -> Dict[str, str]:
    return {f"constituents:{i['index_code']}": i["constituents_url"] for i in catalogue() if i.get("constituents_url")}


def parse_constituents(content: bytes) -> List[Constituent]:
    rows = _rows(content)
    if not rows:
        raise LayoutError("empty file")
    cols = _columns(rows[0], ["isin", "symbol"], ["name", "industry", "weight"])
    out: List[Constituent] = []
    for row in rows[1:]:
        def cell(f: str) -> str:
            i = cols.get(f)
            return row[i].strip() if i is not None and i < len(row) else ""
        isin = cell("isin").upper()
        if not ISIN_RE.match(isin):
            continue
        try:
            weight = float(cell("weight").rstrip("%")) if cell("weight") else None
        except ValueError:
            weight = None
        out.append(Constituent(isin=isin, symbol=cell("symbol").upper(), name=cell("name"), industry=cell("industry") or None, weight=weight))
    return out


def _sum(*parts) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()


async def load_catalogue(session: AsyncSession) -> int:
    """Upsert the catalogue rows; returns how many changed."""
    now, changed = datetime.now(timezone.utc), 0
    for item in catalogue():
        checksum = _sum(item["index_code"], item["name"], item["family"], json.dumps(item.get("broker_symbols") or {}, sort_keys=True), item.get("has_derivatives"))
        row = await session.get(IndexRecord, item["index_code"])
        if row is not None and row.checksum == checksum:
            continue
        if row is None:
            row = IndexRecord(index_code=item["index_code"])
            session.add(row)
        row.name, row.family = item["name"], item["family"]
        row.broker_symbols = json.dumps(item.get("broker_symbols") or {}, sort_keys=True)
        row.has_derivatives = bool(item.get("has_derivatives"))
        row.source, row.fetched_at, row.checksum = "catalogue", now, checksum
        changed += 1
    return changed


def _event(session: AsyncSession, kind: str, key: str, detail: str, source: str) -> None:
    session.add(DataQualityEventRecord(kind=kind[:12], instrument_key=key[:64], source=source[:30], detail=detail[:2000]))


async def apply_constituents(session: AsyncSession, index_code: str, members: List[Constituent], today: date, source: str,
                             file_checksum: str) -> IndexReport:
    report = IndexReport(index_code)
    now = datetime.now(timezone.utc)
    open_rows = {r.isin: r for r in await session.scalars(select(IndexMembershipRecord).where(
        IndexMembershipRecord.index_code == index_code, IndexMembershipRecord.valid_to.is_(None)))}
    wanted: Dict[str, Constituent] = {m.isin: m for m in members}
    if not wanted:
        report.refused = "empty constituent list"
    elif open_rows and len(wanted) < config.UNIVERSE_MIN_ROWS_RATIO * len(open_rows):
        report.refused = f"{len(wanted)} constituents against {len(open_rows)} members held"
    else:
        churn = len(set(wanted) ^ set(open_rows))
        if open_rows and churn > config.UNIVERSE_MAX_CHURN_RATIO * len(open_rows):
            report.refused = f"{churn} membership changes against {len(open_rows)} members held (max ratio {config.UNIVERSE_MAX_CHURN_RATIO})"
    if report.refused:
        _event(session, "CONSTITUENTS", index_code, report.refused, source)
        return report
    first_load = not open_rows and not await session.scalar(select(IndexMembershipRecord.id).where(IndexMembershipRecord.index_code == index_code).limit(1))
    known = set(await session.scalars(select(SecurityRecord.isin).where(SecurityRecord.isin.in_(list(wanted)))))
    for isin, m in wanted.items():
        if isin not in known:
            report.unknown_isins.append(isin)
        row = open_rows.get(isin)
        if row is None:
            session.add(IndexMembershipRecord(index_code=index_code, isin=isin, weight=m.weight, valid_from=today, valid_to=None,
                                              start_observed=first_load, source=source, fetched_at=now, checksum=file_checksum))
            if not first_load:
                report.entered.append(isin)
        elif m.weight is not None and row.weight != m.weight:
            row.weight = m.weight
    for isin, row in open_rows.items():
        if isin not in wanted:
            row.valid_to = today
            report.exited.append(isin)
    if report.unknown_isins:
        _event(session, "UNKNOWN_ISIN", index_code, f"constituents not in securities: {', '.join(report.unknown_isins[:20])}", source)
    report.classified = await _classify(session, members, today, source, file_checksum, now)
    return report


async def _classify(session: AsyncSession, members: List[Constituent], today: date, source: str, checksum: str, now) -> int:
    changed = 0
    for m in members:
        if not m.industry:
            continue
        current = await session.scalar(select(ClassificationRecord).where(ClassificationRecord.isin == m.isin, ClassificationRecord.scheme == "NSE",
                                                                         ClassificationRecord.valid_to.is_(None)))
        if current is not None and current.sector == m.industry:
            continue
        first = current is None and not await session.scalar(select(ClassificationRecord.id).where(ClassificationRecord.isin == m.isin).limit(1))
        if current is not None:
            if current.valid_from == today:                     # two files the same day disagree: the later one wins
                current.sector, current.checksum, current.fetched_at = m.industry, checksum, now
                changed += 1
                continue
            current.valid_to = today
        session.add(ClassificationRecord(isin=m.isin, scheme="NSE", sector=m.industry, valid_from=today, valid_to=None,
                                         start_observed=first, source=source, fetched_at=now, checksum=checksum))
        changed += 1
    return changed


async def sync_indices(session: AsyncSession, source: ReferenceSource, today: date, *, codes: Optional[List[str]] = None,
                       commit: bool = True) -> List[IndexReport]:
    await load_catalogue(session)
    await session.flush()
    reports: List[IndexReport] = []
    for item in catalogue():
        code = item["index_code"]
        if codes and code not in codes:
            continue
        fetched = await source.fetch(f"constituents:{code}")
        if fetched is None:
            continue
        try:
            members = parse_constituents(fetched.content)
        except LayoutError as exc:
            _event(session, "LAYOUT", code, str(exc), fetched.source)
            reports.append(IndexReport(code, refused=str(exc)))
            continue
        report = await apply_constituents(session, code, members, today, fetched.source, fetched.checksum)
        await session.flush()
        if report.entered or report.exited:
            log.info("Index %s rebalanced on %s: +%s -%s", code, today, report.entered, report.exited)
        reports.append(report)
    if commit:
        await session.commit()
    return reports


# --- as-of reads ------------------------------------------------------------------------------------------------------
def _alive(model, day: date):
    return and_(model.valid_from <= day, or_(model.valid_to.is_(None), model.valid_to > day))


async def members_on(session: AsyncSession, index_code: str, day: date) -> List[str]:
    """ISINs in the index on `day`. Before the first file we hold for the index: [] (unknown, not empty)."""
    return sorted(await session.scalars(select(IndexMembershipRecord.isin).where(IndexMembershipRecord.index_code == index_code,
                                                                                 _alive(IndexMembershipRecord, day))))


async def indices_of(session: AsyncSession, isin: str, day: date) -> List[str]:
    return sorted(await session.scalars(select(IndexMembershipRecord.index_code).where(IndexMembershipRecord.isin == isin,
                                                                                       _alive(IndexMembershipRecord, day))))


async def classification_on(session: AsyncSession, isin: str, day: date, scheme: str = "NSE") -> Optional[dict]:
    row = await session.scalar(select(ClassificationRecord).where(ClassificationRecord.isin == isin, ClassificationRecord.scheme == scheme,
                                                                  _alive(ClassificationRecord, day)))
    if row is None:
        return None
    return {"macro_sector": row.macro_sector, "sector": row.sector, "industry": row.industry, "basic_industry": row.basic_industry}


async def coverage_from(session: AsyncSession, index_code: str) -> Optional[date]:
    """The first day we hold membership for the index (as-of reads before it are unknown)."""
    rows = list(await session.scalars(select(IndexMembershipRecord.valid_from).where(IndexMembershipRecord.index_code == index_code)
                                      .order_by(IndexMembershipRecord.valid_from).limit(1)))
    return rows[0] if rows else None


__all__ = ["catalogue", "parse_constituents", "load_catalogue", "apply_constituents", "sync_indices", "members_on", "indices_of",
           "classification_on", "coverage_from", "source_urls", "Constituent", "IndexReport"]
