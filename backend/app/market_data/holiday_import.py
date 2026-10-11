"""HB1: load past exchange holiday lists (e.g. NSE 2016-2025) into `market_holidays` from a CSV.

The backfill's daily gap check needs each year's holiday list - without it every holiday would look like a gap - and
the platform only seeds the current year. Nothing here invents a date: the owner supplies the exchange's own lists
(from its annual circulars) as a CSV, and a year that is still missing keeps "gap check skipped" in the backfill.

Accepted: a header row with a date column (`date`, `holiday date`, `trading date`) and optionally `description` /
`holiday` / `holidays` / `purpose` and `exchange`; dates as 26-Jan-2016, 26-01-2016, 2016-01-26 or 26/01/2016 (the
circular's "Sr. No, Date, Day, Description" layout works as is). Weekend dates are kept (the circular lists them) but
counted. Idempotent: a date already recorded is left alone.
"""
import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MarketHolidayRecord
from app.universe.parsers import parse_date


@dataclass
class HolidayReport:
    rows: int = 0
    added: int = 0
    existing: int = 0
    weekends: int = 0
    years: List[int] = field(default_factory=list)
    unreadable: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict:
        return {"rows": self.rows, "added": self.added, "existing": self.existing, "weekends": self.weekends,
                "years": self.years, "unreadable": self.unreadable[:100]}


def _norm(h: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (h or "").upper())


def parse_holidays_csv(content: bytes, default_exchange: str = "NSE") -> Tuple[List[Tuple[str, date, str]], List[str]]:
    """([(exchange, date, description)], unreadable lines)."""
    text = content.decode("utf-8-sig", errors="replace")
    rows = [r for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
    if not rows:
        return [], ["empty file"]
    head = [_norm(h) for h in rows[0]]

    def col(*names: str) -> Optional[int]:
        return next((head.index(n) for n in names if n in head), None)

    i_date = col("DATE", "HOLIDAYDATE", "TRADINGDATE", "HOLIDAYDATES")
    i_desc = col("DESCRIPTION", "HOLIDAY", "HOLIDAYS", "PURPOSE", "OCCASION")
    i_ex = col("EXCHANGE")
    if i_date is None:
        return [], [f"no date column (headers {rows[0]})"]
    out: List[Tuple[str, date, str]] = []
    bad: List[str] = []
    for n, row in enumerate(rows[1:], start=2):
        raw = row[i_date].strip() if i_date < len(row) else ""
        day = parse_date(raw)
        if day is None:
            bad.append(f"line {n}: unreadable date {raw!r}")
            continue
        desc = row[i_desc].strip() if i_desc is not None and i_desc < len(row) else ""
        exchange = (row[i_ex].strip() if i_ex is not None and i_ex < len(row) else "") or default_exchange
        out.append((exchange.upper()[:20], day, desc[:200]))
    return out, bad


async def import_holidays(session: AsyncSession, content: bytes, *, exchange: str = "NSE", commit: bool = True) -> HolidayReport:
    entries, bad = parse_holidays_csv(content, exchange.upper())
    report = HolidayReport(rows=len(entries) + len(bad), unreadable=bad)
    seen = set()
    for ex, day, desc in entries:
        if (ex, day) in seen:
            continue
        seen.add((ex, day))
        if day.weekday() >= 5:
            report.weekends += 1
        exists = await session.scalar(select(MarketHolidayRecord.id).where(MarketHolidayRecord.exchange == ex,
                                                                          MarketHolidayRecord.holiday_date == day))
        if exists is not None:
            report.existing += 1
            continue
        session.add(MarketHolidayRecord(exchange=ex, holiday_date=day, description=desc))
        report.added += 1
    report.years = sorted({day.year for _, day, _ in entries})
    if commit:
        await session.commit()
    else:
        await session.flush()
    return report
