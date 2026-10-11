"""U1-a: parsers for the exchange's equity lists and symbol-change file.

Headers are matched after normalising (upper case, spaces/underscores/dots removed) against a small alias table, so
`NAME OF COMPANY`, `NAME_OF_COMPANY` and ` NAME OF COMPANY` are the same column. A file whose required columns are not
all present raises `LayoutError` - a changed layout stops the job loudly instead of loading wrong data.
"""
import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict, Iterable, List, Optional

ISIN_RE = re.compile(r"^IN[A-Z0-9]{9}[0-9]$")


class LayoutError(ValueError):
    pass


@dataclass(frozen=True)
class ListedSecurity:
    isin: str
    symbol: str
    name: str
    series: str
    listing_date: Optional[date]
    face_value: Optional[float]
    market_lot: Optional[int]
    is_sme: bool = False
    is_etf: bool = False
    underlying: Optional[str] = None


@dataclass(frozen=True)
class SymbolChange:
    old_symbol: str
    new_symbol: str
    effective: date
    company: str = ""


ALIASES = {
    "symbol": ("SYMBOL",),
    "name": ("NAMEOFCOMPANY", "SECURITYNAME", "COMPANYNAME", "NAME"),
    "series": ("SERIES",),
    "listing_date": ("DATEOFLISTING", "LISTINGDATE"),
    "face_value": ("FACEVALUE",),
    "market_lot": ("MARKETLOT",),
    "isin": ("ISINNUMBER", "ISIN", "ISINNO"),
    "underlying": ("UNDERLYING",),
    "company": ("SMNAMEOFCOMPANY", "NAMEOFCOMPANY", "COMPANYNAME"),
    "old_symbol": ("SMKEYSYMBOL", "OLDSYMBOL", "PREVIOUSSYMBOL"),
    "new_symbol": ("SMNEWSYMBOL", "NEWSYMBOL"),
    "effective": ("SMAPPLICABLEFROM", "APPLICABLEFROM", "EFFECTIVEDATE", "DATEOFCHANGE"),
}


def _norm(header: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (header or "").upper())


def _columns(headers: List[str], required: Iterable[str], optional: Iterable[str] = ()) -> Dict[str, int]:
    normed = [_norm(h) for h in headers]
    out: Dict[str, int] = {}
    for field in list(required) + list(optional):
        for alias in ALIASES[field]:
            if alias in normed:
                out[field] = normed.index(alias)
                break
    missing = [f for f in required if f not in out]
    if missing:
        raise LayoutError(f"unexpected file layout: no column for {', '.join(missing)} (headers: {headers})")
    return out


def parse_date(value: str) -> Optional[date]:
    text = (value or "").strip()
    for fmt in ("%d-%b-%Y", "%d-%b-%y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d %b %Y"):
        try:
            return datetime.strptime(text.title() if "%b" in fmt else text, fmt).date()
        except ValueError:
            continue
    return None


def _number(value: str) -> Optional[float]:
    try:
        return float((value or "").replace(",", "").strip())
    except ValueError:
        return None


def _rows(content: bytes) -> List[List[str]]:
    text = content.decode("utf-8-sig", errors="replace")
    return [row for row in csv.reader(io.StringIO(text)) if any(cell.strip() for cell in row)]


def parse_equity_list(content: bytes, *, sme: bool = False, etf: bool = False) -> List[ListedSecurity]:
    """EQUITY_L.csv / SME_EQUITY_L.csv / eq_etfseclist.csv -> securities. Rows without a valid ISIN are skipped."""
    rows = _rows(content)
    if not rows:
        raise LayoutError("empty file")
    cols = _columns(rows[0], ["symbol", "name", "isin"], ["series", "listing_date", "face_value", "market_lot", "underlying"])

    def cell(row: List[str], field: str) -> str:
        i = cols.get(field)
        return row[i].strip() if i is not None and i < len(row) else ""

    out: List[ListedSecurity] = []
    for row in rows[1:]:
        isin = cell(row, "isin").upper()
        if not ISIN_RE.match(isin):
            continue
        lot = _number(cell(row, "market_lot"))
        out.append(ListedSecurity(isin=isin, symbol=cell(row, "symbol").upper(), name=cell(row, "name"),
                                  series=(cell(row, "series") or ("EQ" if etf else "")).upper()[:4] or "EQ",
                                  listing_date=parse_date(cell(row, "listing_date")), face_value=_number(cell(row, "face_value")),
                                  market_lot=int(lot) if lot else None, is_sme=sme, is_etf=etf, underlying=cell(row, "underlying") or None))
    return out


def parse_symbol_changes(content: bytes) -> List[SymbolChange]:
    """symbolchange.csv -> changes, oldest first. The exchange's file may come without a header row; then the
    documented order is assumed (company, old symbol, new symbol, applicable from)."""
    rows = _rows(content)
    if not rows:
        return []
    try:
        cols = _columns(rows[0], ["old_symbol", "new_symbol", "effective"], ["company"])
        body = rows[1:]
    except LayoutError:
        if len(rows[0]) >= 4 and parse_date(rows[0][3]):
            cols, body = {"company": 0, "old_symbol": 1, "new_symbol": 2, "effective": 3}, rows
        else:
            raise
    out: List[SymbolChange] = []
    for row in body:
        when = parse_date(row[cols["effective"]]) if cols["effective"] < len(row) else None
        old, new = row[cols["old_symbol"]].strip().upper(), row[cols["new_symbol"]].strip().upper()
        if when and old and new and old != new:
            out.append(SymbolChange(old, new, when, row[cols["company"]].strip() if "company" in cols else ""))
    return sorted(out, key=lambda c: c.effective)
