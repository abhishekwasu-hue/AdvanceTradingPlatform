"""U1-c: index daily closes, F&O membership with market lots, and the exchange's own F&O ban list.

* `ind_close_all_<ddmmyyyy>.csv` (all indices, one file per day) -> `index_eod`. An index name not in the catalogue is
  added to `indices` as family OTHER (the file is how new indices are discovered). A row that changes on re-ingest is
  updated in place and counted as a correction (reference data, not trades).
* `fo_mktlots.csv` -> `fo_membership`: the first month column with a lot is the current market lot. A new underlying
  opens a range from the run's day, a lot change closes the range and opens a new one, an underlying missing from the
  file closes its range. Gates: an empty or short file, or a churn above `UNIVERSE_MAX_CHURN_RATIO`, is refused with a
  quality event.
* `fo_secban.csv` -> `fo_ban_history`, one row per (underlying, trade date); the trade date comes from the file's own
  heading when it has one. Idempotent.
"""
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.db.models import DataQualityEventRecord, FoBanRecord, FoMembershipRecord, IndexEodRecord, IndexRecord
from app.instruments.master import INDEX_SYMBOLS
from app.universe import asof
from app.universe.indices import catalogue
from app.universe.parsers import LayoutError, _columns, _rows, parse_date
from app.universe.sources import ReferenceSource

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class IndexClose:
    name: str
    trade_date: date
    open: Optional[float]
    high: Optional[float]
    low: Optional[float]
    close: float
    pe: Optional[float]
    pb: Optional[float]
    div_yield: Optional[float]


@dataclass
class FoReport:
    index_rows: int = 0
    index_corrections: int = 0
    discovered_indices: List[str] = field(default_factory=list)
    fo_entered: List[str] = field(default_factory=list)
    fo_exited: List[str] = field(default_factory=list)
    lot_changes: List[str] = field(default_factory=list)
    banned: List[str] = field(default_factory=list)
    refused: List[str] = field(default_factory=list)


def _num(value: str) -> Optional[float]:
    text = (value or "").replace(",", "").strip()
    if text in ("", "-", "NA", "N.A."):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def code_for(name: str) -> str:
    """The catalogue code for an index name, or a stable code derived from the name."""
    by_name = {i["name"].upper(): i["index_code"] for i in catalogue()}
    upper = re.sub(r"\s+", " ", name.strip().upper())
    return by_name.get(upper) or re.sub(r"[^A-Z0-9]", "", upper)[:40]


def parse_index_closes(content: bytes) -> List[IndexClose]:
    rows = _rows(content)
    if not rows:
        raise LayoutError("empty file")
    cols = _columns(rows[0], ["index_name", "index_date", "close_v"], ["open_v", "high_v", "low_v", "pe", "pb", "div_yield"])
    out: List[IndexClose] = []
    for row in rows[1:]:
        def cell(f: str) -> str:
            i = cols.get(f)
            return row[i].strip() if i is not None and i < len(row) else ""
        when, close = parse_date(cell("index_date")), _num(cell("close_v"))
        if not cell("index_name") or when is None or close is None:
            continue
        out.append(IndexClose(cell("index_name"), when, _num(cell("open_v")), _num(cell("high_v")), _num(cell("low_v")), close,
                              _num(cell("pe")), _num(cell("pb")), _num(cell("div_yield"))))
    return out


def parse_fo_lots(content: bytes) -> List[Tuple[str, int]]:
    """(symbol, current market lot) per underlying. Section rows and rows without a lot are skipped."""
    rows = _rows(content)
    if not rows:
        raise LayoutError("empty file")
    head = [re.sub(r"[^A-Z0-9]", "", h.upper()) for h in rows[0]]
    if "SYMBOL" not in head:
        raise LayoutError(f"unexpected file layout: no SYMBOL column (headers: {rows[0]})")
    sym_i = head.index("SYMBOL")
    month_cols = [i for i in range(len(head)) if i > sym_i]
    out: List[Tuple[str, int]] = []
    for row in rows[1:]:
        symbol = row[sym_i].strip().upper() if sym_i < len(row) else ""
        if not symbol or symbol == "SYMBOL":
            continue
        lot = next((int(v) for v in (_num(row[i]) if i < len(row) else None for i in month_cols) if v), None)
        if lot:
            out.append((symbol, lot))
    return out


def parse_ban_list(content: bytes, default_day: date) -> Tuple[date, List[str]]:
    """(trade date, symbols). The file's heading ("Securities in Ban For Trade Date 10-OCT-2026") gives the date."""
    text = content.decode("utf-8-sig", errors="replace")
    m = re.search(r"(?i)trade\s+date\s+([0-9]{1,2}-[A-Za-z]{3}-[0-9]{4})", text)
    day = parse_date(m.group(1)) if m else None
    symbols: List[str] = []
    for line in text.splitlines()[1 if m else 0:]:
        cells = [c.strip() for c in line.split(",") if c.strip()]
        name = next((c.upper() for c in reversed(cells) if re.fullmatch(r"[A-Za-z0-9&\-]{2,20}", c) and not c.isdigit()), None)
        if name and name not in symbols and "SECURIT" not in name and name not in ("SYMBOL", "SRNO", "SR"):
            symbols.append(name)
    return day or default_day, symbols


def _event(session: AsyncSession, kind: str, key: str, detail: str, source: str) -> None:
    session.add(DataQualityEventRecord(kind=kind[:12], instrument_key=key[:64], source=source[:30], detail=detail[:2000]))


async def apply_index_closes(session: AsyncSession, closes: List[IndexClose], source: str, checksum: str, report: FoReport) -> None:
    now = datetime.now(timezone.utc)
    for c in closes:
        code = code_for(c.name)
        if await session.get(IndexRecord, code) is None:
            known = next((i for i in catalogue() if i["index_code"] == code), None)
            if known is not None:                      # a catalogue index not loaded yet: load it, it is not a discovery
                from app.universe.indices import load_catalogue
                await load_catalogue(session)
            else:
                session.add(IndexRecord(index_code=code, name=c.name.strip(), family="OTHER", broker_symbols="{}", has_derivatives=False,
                                        source=source, fetched_at=now, checksum=checksum))
                report.discovered_indices.append(code)
            await session.flush()
        row = await session.get(IndexEodRecord, (code, c.trade_date))
        values = {"open": c.open, "high": c.high, "low": c.low, "close": c.close, "pe": c.pe, "pb": c.pb, "div_yield": c.div_yield}
        if row is None:
            session.add(IndexEodRecord(index_code=code, trade_date=c.trade_date, source=source, fetched_at=now, checksum=checksum, **values))
            report.index_rows += 1
        elif any(getattr(row, k) != v for k, v in values.items()):
            for k, v in values.items():
                setattr(row, k, v)
            row.source, row.fetched_at, row.checksum = source, now, checksum
            report.index_corrections += 1


async def apply_fo_lots(session: AsyncSession, lots: List[Tuple[str, int]], today: date, source: str, checksum: str, report: FoReport) -> None:
    now = datetime.now(timezone.utc)
    open_rows = {r.underlying: r for r in await session.scalars(select(FoMembershipRecord).where(FoMembershipRecord.valid_to.is_(None)))}
    wanted: Dict[str, int] = dict(lots)
    why = None
    if not wanted:
        why = "empty F&O lot list"
    elif open_rows and len(wanted) < config.UNIVERSE_MIN_ROWS_RATIO * len(open_rows):
        why = f"{len(wanted)} underlyings against {len(open_rows)} held"
    elif open_rows and len(set(wanted) ^ set(open_rows)) > config.UNIVERSE_MAX_CHURN_RATIO * len(open_rows):
        why = f"{len(set(wanted) ^ set(open_rows))} F&O membership changes against {len(open_rows)} held"
    if why:
        report.refused.append(f"fo_lots: {why}")
        _event(session, "FO_LOTS", "fo_lots", why, source)
        return
    first_load = not open_rows and not await session.scalar(select(FoMembershipRecord.id).limit(1))
    for symbol, lot in wanted.items():
        row = open_rows.get(symbol)
        if row is not None and row.lot_size == lot:
            continue
        if row is not None:
            row.valid_to = today
            report.lot_changes.append(f"{symbol} {row.lot_size}->{lot}")
        elif not first_load:
            report.fo_entered.append(symbol)
        is_index = symbol in INDEX_SYMBOLS
        isin = None if is_index else await asof.isin_for(session, symbol, today)
        session.add(FoMembershipRecord(underlying=symbol, isin=isin, is_index=is_index, lot_size=lot, valid_from=today, valid_to=None,
                                       start_observed=first_load, source=source, fetched_at=now, checksum=checksum))
    for symbol, row in open_rows.items():
        if symbol not in wanted:
            row.valid_to = today
            report.fo_exited.append(symbol)


async def apply_ban_list(session: AsyncSession, day: date, symbols: List[str], source: str, checksum: str, report: FoReport) -> None:
    now = datetime.now(timezone.utc)
    for symbol in symbols:
        if await session.get(FoBanRecord, (symbol, day)) is None:
            session.add(FoBanRecord(underlying=symbol, trade_date=day, source=source, fetched_at=now, checksum=checksum))
            report.banned.append(symbol)


async def sync_fo_and_index_eod(session: AsyncSession, source: ReferenceSource, today: date, *, commit: bool = True) -> FoReport:
    report = FoReport()
    for name, apply in (("index_close_all", "closes"), ("fo_lots", "lots"), ("fo_ban", "ban")):
        fetched = await source.fetch(name)
        if fetched is None:
            continue
        try:
            if apply == "closes":
                await apply_index_closes(session, parse_index_closes(fetched.content), fetched.source, fetched.checksum, report)
            elif apply == "lots":
                await apply_fo_lots(session, parse_fo_lots(fetched.content), today, fetched.source, fetched.checksum, report)
            else:
                day, symbols = parse_ban_list(fetched.content, today)
                await apply_ban_list(session, day, symbols, fetched.source, fetched.checksum, report)
        except LayoutError as exc:
            report.refused.append(f"{name}: {exc}")
            _event(session, "LAYOUT", name, str(exc), fetched.source)
        await session.flush()
    if commit:
        await session.commit()
    log.info("F&O / index EOD sync %s: %s", today, json.dumps(report.__dict__, default=str)[:500])
    return report


def dated_urls(day: date) -> Dict[str, str]:
    """The configured URLs with the trade date filled in (the all-indices file is one file per day)."""
    urls = dict(config.UNIVERSE_FILE_URLS)
    urls["index_close_all"] = urls.get("index_close_all", "").replace("{ddmmyyyy}", day.strftime("%d%m%Y"))
    return {k: v for k, v in urls.items() if k in ("index_close_all", "fo_lots", "fo_ban") and v}


__all__ = ["parse_index_closes", "parse_fo_lots", "parse_ban_list", "apply_index_closes", "apply_fo_lots", "apply_ban_list",
           "sync_fo_and_index_eod", "dated_urls", "code_for", "FoReport"]
