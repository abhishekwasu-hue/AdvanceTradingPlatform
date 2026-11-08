"""M88: import trades done OUTSIDE ATP from a file - a broker tradebook (CSV / Excel) or the trader's own sheet.

Flow: `preview()` reads the file and answers what would be imported (rows read, rows with a problem and why, the
round-trip trades, the ones already in his journal); nothing is stored. `commit()` reads the same file again with the
same column mapping and stores the new trades as ordinary manual (outside-ATP) journal entries
(`journal_manual_trades`, checked by `entries.manual_values` exactly like a trade typed by hand), each carrying the
`journal_imports` row it came from. The whole Journal analysis (the list, Stats, the week, the weekly review, the
export) then reads them like any other manual entry.

* Reading: CSV (comma, semicolon, tab or pipe; UTF-8 or Windows-1252), .xlsx (the first sheet, read with the standard
  library and `defusedxml` - no new dependency) and .xls files that are really an HTML table or text (many broker
  back-offices save those). A true old binary .xls gets a plain sentence: save it as .xlsx or .csv.
* Columns: found by name (`FIELDS`: the names the common Indian broker exports use - Zerodha Console, Upstox, Angel
  One, Dhan, Groww, Fyers - and a plain sheet's). The trader can change any mapping before he imports.
* Two shapes: executions (one buy or sell fill per row, a tradebook) are paired into round trips per symbol,
  first-in first-out (`pair_fifo`): a trade runs from the first fill until the position is back to zero, at the
  quantity-weighted average entry and exit prices. A sheet of whole trades (entry and exit columns) is read as is.
* Every number is the trader's own: nothing is filled in. No stop, target or setup is assumed - a column he did not
  have stays empty, so R is shown only where his own stop is in the file. Times without a zone are India time.
* Duplicates are skipped: the same file again (its SHA-256, while any of its trades is still in the journal), and a
  trade that is already in his journal (same symbol, side, quantity, entry and exit).
* Personal: every read and write is scoped to the logged-in trader (`user_id` and `tenant_id`). Journal data only:
  nothing here places, changes or blocks an order, calls a broker or touches a P&L book or a risk counter.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contract_notes.parser import _number
from app.contract_notes.routes import MAX_UPLOAD_BYTES
from app.db.models import JournalImportRecord, JournalManualTradeRecord, User
from app.journal import entries

IST = timezone(timedelta(hours=5, minutes=30))
IMPORT_MAX_BYTES = MAX_UPLOAD_BYTES        # the platform's broker-file upload limit (contract notes): 5 MB
IMPORT_MAX_ROWS = 5000                     # data rows read from one file
IMPORT_MAX_TRADES = 2000                   # round-trip trades stored from one file
XLSX_MAX_UNZIPPED = 60 * 1024 * 1024       # an .xlsx is a zip: its parts together may not unpack to more than this
HEADER_SCAN_ROWS = 30                      # broker files often start with a few title lines before the header
PREVIEW_ROWS = 500                         # trades and problems listed in the preview (the counts cover the whole file)
EXTENSIONS = (".csv", ".xlsx", ".xls")
PAIRING = ("The fills of one symbol are paired first-in, first-out: a trade runs from its first fill until the "
           "position is back to zero, at the average entry and exit prices of its fills.")


class ImportFileError(ValueError):
    """A file the trader can correct; the message is the plain sentence he sees."""

    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


# --- the columns ------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Field:
    key: str
    label: str
    names: tuple[str, ...]          # header names (normalised: lower case, words only) that mean this column


FIELDS: tuple[Field, ...] = (
    Field("symbol", "Symbol", ("symbol", "tradingsymbol", "trading symbol", "scrip", "scrip name", "script name", "stock name",
                              "stock", "instrument", "security", "security name", "company", "name", "contract", "contract name")),
    Field("side", "Side", ("trade type", "transaction type", "buy sell", "side", "type", "action",
                                                 "buy or sell", "txn type", "b s", "direction", "position", "long short")),
    Field("quantity", "Quantity", ("quantity", "qty", "filled quantity", "traded qty", "traded quantity", "trade qty",
                                   "quantity lot", "qty lot", "net qty", "filled qty")),
    Field("price", "Price (one fill)", ("price", "trade price", "traded price", "avg price", "average price", "fill price",
                                        "execution price", "rate", "avg traded price", "trade rate")),
    Field("value", "Value (one fill)", ("value", "trade value", "amount", "traded value", "turnover")),
    Field("time", "Fill time", ("order execution time", "execution time", "execution date and time", "trade time", "time",
                                "timestamp", "trade date time", "date time", "date and time", "exchange time", "fill time")),
    Field("date", "Fill date", ("trade date", "date", "execution date", "order date")),
    Field("entry_time", "Entry time", ("entry time", "entry date", "entry date time", "buy date", "buy time", "open time",
                                       "open date", "entry")),
    Field("entry_price", "Entry price", ("entry price", "buy price", "avg buy price", "buy rate", "buy avg", "open price")),
    Field("exit_time", "Exit time", ("exit time", "exit date", "exit date time", "sell date", "sell time", "close time",
                                     "close date", "exit")),
    Field("exit_price", "Exit price", ("exit price", "sell price", "avg sell price", "sell rate", "sell avg", "close price")),
    Field("charges", "Charges", ("total charges", "charges", "net charges", "total tax and charges", "taxes and charges",
                                 "brokerage and charges")),
    Field("stop_loss", "Your stop", ("stop loss", "stop", "sl", "invalidation", "stoploss")),
    Field("setup", "Setup", ("setup", "strategy")),
    Field("notes", "Notes", ("notes", "note", "remarks", "comment", "comments")),
    Field("exchange", "Exchange", ("exchange", "exch", "exchange name")),
    Field("fill_id", "Trade id", ("trade id", "trade no", "trade num", "trade number", "tradeid", "exchange trade id")),
    Field("status", "Status", ("status", "order status", "trade status")),
)
FIELD_KEYS = tuple(f.key for f in FIELDS)
_BY_KEY = {f.key: f for f in FIELDS}

# The layouts it recognises by their own header names (the label is shown; the mapping is still per column).
LAYOUTS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("zerodha", "Zerodha Console tradebook", ("trade type", "order execution time", "isin", "auction")),
    ("groww", "Groww order history", ("stock name", "execution date and time", "exchange order id")),
    ("upstox", "Upstox trade report", ("scrip code", "trade num", "instrument type", "side")),
    ("angel", "Angel One tradebook", ("buy sell", "trade price", "order no", "trade no")),
    ("dhan", "Dhan tradebook", ("buy sell", "quantity lot", "trade value", "name")),
    ("fyers", "Fyers tradebook", ("transaction type", "trade value", "order id", "trade id")),
)
LAYOUT_LABELS = {key: label for key, label, _ in LAYOUTS} | {"trades": "Your own sheet of trades", "fills": "A sheet of buys and sells"}


def norm(header: Any) -> str:
    """'Order Execution Time' / 'order_execution_time' / 'Buy/Sell' -> 'order execution time' / 'buy sell'."""
    return " ".join(re.sub(r"[^0-9a-z]+", " ", str(header or "").replace("﻿", "").lower()).split())


def auto_mapping(headers: list[str]) -> dict[str, str | None]:
    """Each field -> the first header whose name means it (each header used once; a longer, more specific name first)."""
    normalised = [norm(h) for h in headers]
    used: set[int] = set()
    out: dict[str, str | None] = dict.fromkeys(FIELD_KEYS)
    for f in FIELDS:
        for name in f.names:
            hit = next((i for i, h in enumerate(normalised) if h == name and i not in used), None)
            if hit is not None:
                out[f.key] = headers[hit]
                used.add(hit)
                break
    return out


def detect_layout(headers: list[str], mapping: dict[str, str | None]) -> str:
    names = {norm(h) for h in headers}
    best, hits = "", 0
    for key, _label, signature in LAYOUTS:
        n = sum(1 for s in signature if s in names)
        if n >= 3 and n > hits:
            best, hits = key, n
    if best:
        return best
    return "trades" if shape_of(mapping) == "trades" else "fills"


def shape_of(mapping: dict[str, str | None]) -> str:
    """'trades' when the sheet has entry and exit columns, else 'fills' (one buy or sell per row)."""
    return "trades" if mapping.get("entry_price") and mapping.get("entry_time") else "fills"


# --- reading the file -------------------------------------------------------------------------------------------------

def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    return " ".join(str(value).split())


def _csv_rows(data: bytes) -> list[list[str]]:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:                                                   # pragma: no cover - cp1252 decodes nearly anything
        raise ImportFileError("The file is not readable text. Save it as CSV (UTF-8) or as an Excel workbook (.xlsx).")
    if "\x00" in text[:2000]:
        raise ImportFileError("The file is not readable text. Save it as CSV (UTF-8) or as an Excel workbook (.xlsx).")
    try:
        dialect: Any = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return [[_cell_text(c) for c in row] for row in csv.reader(io.StringIO(text), dialect)]


_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_DATE_FORMAT_IDS = frozenset({14, 15, 16, 17, 18, 19, 20, 21, 22, 45, 46, 47})


def _col_index(ref: str) -> int:
    n = 0
    for ch in ref:
        if not ch.isalpha():
            break
        n = n * 26 + (ord(ch.upper()) - 64)
    return max(0, n - 1)


def _serial(value: float, date1904: bool) -> datetime:
    base = datetime(1904, 1, 1) if date1904 else datetime(1899, 12, 30)
    return base + timedelta(days=value)


def _xlsx_rows(data: bytes) -> list[list[str]]:
    from defusedxml import ElementTree as ET
    try:
        book = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ImportFileError("This Excel file could not be opened. Save it again as .xlsx or .csv and import that.") from exc
    if sum(i.file_size for i in book.infolist()) > XLSX_MAX_UNZIPPED:
        raise ImportFileError("This Excel file is too large once unpacked. Save the trades on their own sheet as .csv and import that.")
    names = set(book.namelist())

    def xml(name: str) -> Any:
        return ET.fromstring(book.read(name))

    shared: list[str] = []
    if "xl/sharedStrings.xml" in names:
        for si in xml("xl/sharedStrings.xml").iter(f"{_NS}si"):
            shared.append("".join(t.text or "" for t in si.iter(f"{_NS}t")))
    date_styles: set[int] = set()
    if "xl/styles.xml" in names:
        styles = xml("xl/styles.xml")
        custom = {int(n.get("numFmtId") or 0) for n in styles.iter(f"{_NS}numFmt")
                  if re.search(r"[dmyhs]", re.sub(r'"[^"]*"|\[[^\]]*\]', "", (n.get("formatCode") or "").lower()))}
        xfs = styles.find(f"{_NS}cellXfs")
        for i, xf in enumerate(xfs if xfs is not None else []):
            fmt = int(xf.get("numFmtId") or 0)
            if fmt in _DATE_FORMAT_IDS or fmt in custom:
                date_styles.add(i)
    sheet_name = "xl/worksheets/sheet1.xml"
    date1904 = False
    if "xl/workbook.xml" in names:
        workbook = xml("xl/workbook.xml")
        pr = workbook.find(f"{_NS}workbookPr")
        date1904 = pr is not None and (pr.get("date1904") or "").lower() in ("1", "true")
        first = workbook.find(f"{_NS}sheets/{_NS}sheet")
        if first is not None and "xl/_rels/workbook.xml.rels" in names:
            rid = first.get(f"{_REL_NS}id")
            for rel in xml("xl/_rels/workbook.xml.rels"):
                if rel.get("Id") == rid and rel.get("Target"):
                    target = str(rel.get("Target")).lstrip("/")
                    sheet_name = target if target.startswith("xl/") else f"xl/{target}"
    if sheet_name not in names:
        raise ImportFileError("This Excel file has no sheet that could be read. Save it as .csv and import that.")
    rows: list[list[str]] = []
    for row in xml(sheet_name).iter(f"{_NS}row"):
        cells: dict[int, str] = {}
        for c in row.iter(f"{_NS}c"):
            kind, style = c.get("t") or "n", int(c.get("s") or 0)
            v = c.find(f"{_NS}v")
            raw = v.text if v is not None else None
            if kind == "s" and raw is not None:
                text = shared[int(raw)] if int(raw) < len(shared) else ""
            elif kind == "inlineStr":
                text = "".join(t.text or "" for t in c.iter(f"{_NS}t"))
            elif kind == "n" and raw is not None and style in date_styles:
                when = _serial(float(raw), date1904)
                text = when.date().isoformat() if when.time() == datetime.min.time() else when.isoformat(sep=" ", timespec="seconds")
            elif kind == "n" and raw is not None:
                text = _cell_text(float(raw))
            else:
                text = raw or ""
            cells[_col_index(c.get("r") or "A")] = " ".join(text.split())
        if cells:
            width = max(cells) + 1
            rows.append([cells.get(i, "") for i in range(width)])
        if len(rows) > IMPORT_MAX_ROWS + HEADER_SCAN_ROWS:
            break
    return rows


class _TableParser(HTMLParser):
    """The rows of the first <table> of an HTML file saved as '.xls' by a broker back-office."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._tables = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag == "table":
            self._tables += 1
        elif tag == "tr" and self._tables == 1:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._row is not None and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def read_rows(file_name: str, data: bytes) -> list[list[str]]:
    """The file's cells as text rows. Refuses an empty, an oversize or an unreadable file with a plain sentence."""
    name = (file_name or "").lower()
    if not name.endswith(EXTENSIONS):
        raise ImportFileError("Choose a .csv, .xlsx or .xls file.")
    if not data:
        raise ImportFileError("The file is empty.")
    if len(data) > IMPORT_MAX_BYTES:
        raise ImportFileError(f"The file is larger than {IMPORT_MAX_BYTES // (1024 * 1024)} MB. Split it into smaller files.", 413)
    if data[:4] == b"PK\x03\x04":
        return _xlsx_rows(data)
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        raise ImportFileError("This is an old Excel (.xls) workbook. Open it in Excel or Google Sheets, save it as .xlsx or "
                           ".csv, and import that file.")
    head = data[:2048].lstrip().lower()
    if head.startswith((b"<", b"\xef\xbb\xbf<")) and b"<table" in data[:200000].lower():
        parser = _TableParser()
        parser.feed(data.decode("utf-8", errors="replace"))
        return parser.rows
    if name.endswith(".xlsx"):
        raise ImportFileError("This Excel file could not be opened. Save it again as .xlsx or .csv and import that.")
    return _csv_rows(data)


def find_header(rows: list[list[str]]) -> int:
    """The index of the header row: the first of the opening rows naming at least three known columns (else row 0)."""
    known = {n for f in FIELDS for n in f.names}
    for i, row in enumerate(rows[:HEADER_SCAN_ROWS]):
        if sum(1 for c in row if norm(c) in known) >= 3:
            return i
    return next((i for i, row in enumerate(rows) if any(c for c in row)), 0)


# --- the values -------------------------------------------------------------------------------------------------------

_DATETIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",
                     "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M",
                     "%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d %b %Y %H:%M:%S", "%d %b %Y %H:%M",
                     "%d %b %Y, %I:%M %p", "%d %b %Y %I:%M %p", "%d-%m-%Y %I:%M:%S %p", "%d-%m-%Y %I:%M %p",
                     "%d/%m/%Y %I:%M:%S %p", "%d/%m/%Y %I:%M %p", "%Y-%m-%d %I:%M:%S %p", "%Y-%m-%d %I:%M %p")
_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y", "%d-%b-%y", "%d-%m-%y", "%d/%m/%y", "%Y/%m/%d",
                 "%d.%m.%Y")
_TIME_FORMATS = ("%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p")


def _aware(when: datetime) -> datetime:
    """A time without a zone is India time (the Indian brokers' exports); one with a zone keeps it. As UTC."""
    return (when.replace(tzinfo=IST) if when.tzinfo is None else when).astimezone(UTC)


def parse_when(raw: str) -> tuple[datetime | None, bool]:
    """(the instant, whether it had a time of day). Day first, as Indian exports write dates."""
    text = " ".join((raw or "").replace(" ", " ").split())
    if not text:
        return None, False
    try:
        when = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return _aware(when), len(text) > 10
    except ValueError:
        pass
    for fmt in _DATETIME_FORMATS:
        try:
            return _aware(datetime.strptime(text, fmt)), True
        except ValueError:
            continue
    for fmt in _DATE_FORMATS:
        try:
            return _aware(datetime.strptime(text, fmt)), False
        except ValueError:
            continue
    return None, False


def parse_time_of_day(raw: str) -> tuple[int, int, int] | None:
    text = " ".join((raw or "").split())
    for fmt in _TIME_FORMATS:
        try:
            t = datetime.strptime(text, fmt)
            return t.hour, t.minute, t.second
        except ValueError:
            continue
    return None


def parse_side(raw: str) -> str | None:
    """BUY / SELL from what the file says (buy, b, long, purchase / sell, s, short, sale)."""
    text = norm(raw)
    if text in ("b", "buy", "bought", "purchase", "long", "buy long", "bot"):
        return "BUY"
    if text in ("s", "sell", "sold", "sale", "short", "sell short", "sld"):
        return "SELL"
    return None


_EXCHANGE_PREFIX = re.compile(r"^(NSE|BSE|NFO|BFO|MCX|CDS)[:\s-]+", re.IGNORECASE)
_SERIES_SUFFIX = re.compile(r"-(EQ|BE|BZ|SM|ST)$", re.IGNORECASE)
_OPTION = re.compile(r"\d(CE|PE)$")


def clean_symbol(raw: str) -> tuple[str, str | None, str]:
    """('SBIN', 'NSE', 'UNDERLYING') from 'NSE:SBIN-EQ'; an option / future symbol keeps its name and gets its kind."""
    text = " ".join((raw or "").upper().split())
    exchange = None
    m = _EXCHANGE_PREFIX.match(text)
    if m:
        exchange, text = m.group(1).upper(), text[m.end():]
    text = _SERIES_SUFFIX.sub("", text).strip()
    compact = text.replace(" ", "")
    kind = "OPTION" if _OPTION.search(compact) else "FUTURE" if compact.endswith("FUT") else "UNDERLYING"
    return text, exchange, kind


_NOT_DONE = re.compile(r"reject|cancel|fail|expired|pending|open order", re.IGNORECASE)


# --- rows -> fills -> trades ------------------------------------------------------------------------------------------

@dataclass
class Fill:
    row: int
    symbol: str
    exchange: str | None
    kind: str
    side: str                 # BUY / SELL
    quantity: float
    price: float
    at: datetime
    charges: float
    timed: bool               # the file gave a time of day (False: a date only)


@dataclass
class Trade:
    symbol: str
    exchange: str | None
    kind: str
    direction: str
    quantity: float
    entry_time: datetime
    entry_price: float
    exit_time: datetime | None = None
    exit_price: float | None = None
    charges: float = 0.0
    stop_loss: float | None = None
    setup: str | None = None
    notes: str | None = None
    rows: list[int] = field(default_factory=list)
    timed: bool = True
    duplicate: bool = False

    def body(self) -> dict[str, Any]:
        """The manual-trade body `entries.manual_values` checks - only what the file gave."""
        return {"symbol": self.symbol, "exchange": self.exchange, "instrument_kind": self.kind, "direction": self.direction,
                "quantity": round(self.quantity, 6), "entry_time": self.entry_time, "entry_price": round(self.entry_price, 4),
                "exit_time": self.exit_time, "exit_price": round(self.exit_price, 4) if self.exit_price is not None else None,
                "charges": round(self.charges, 2), "stop_loss": self.stop_loss, "setup": self.setup, "notes": self.notes}

    def key(self) -> tuple[Any, ...]:
        return _key(self.symbol, self.direction, self.quantity, self.entry_time, self.entry_price, self.exit_time, self.exit_price)


def _key(symbol: str, direction: str, quantity: float, entry_time: datetime | None, entry_price: float,
         exit_time: datetime | None, exit_price: float | None) -> tuple[Any, ...]:
    def second(t: datetime | None) -> int | None:
        if t is None:
            return None
        return int((t if t.tzinfo else t.replace(tzinfo=UTC)).timestamp())
    return (symbol.upper(), direction, round(float(quantity), 4), second(entry_time), round(float(entry_price), 2),
            second(exit_time), round(float(exit_price), 2) if exit_price is not None else None)


@dataclass
class Problem:
    row: int
    reason: str


def _get(row: list[str], headers: list[str], mapping: dict[str, str | None], key: str) -> str:
    header = mapping.get(key)
    if not header or header not in headers:
        return ""
    i = headers.index(header)
    return row[i].strip() if i < len(row) else ""


def _money(raw: str) -> float | None:
    return _number(raw.replace("Rs", "").replace("rs", "")) if raw else None


def _fill_time(row: list[str], headers: list[str], mapping: dict[str, str | None]) -> tuple[datetime | None, bool]:
    """The fill's instant from its time column, its date column, or both (a date column plus a time-of-day column)."""
    t_raw, d_raw = _get(row, headers, mapping, "time"), _get(row, headers, mapping, "date")
    when, timed = parse_when(t_raw) if t_raw else (None, False)
    if when is not None and (timed or not d_raw):
        return when, timed
    day, _ = parse_when(d_raw) if d_raw else (None, False)
    if day is None:
        return when, timed
    clock = parse_time_of_day(t_raw) if t_raw else None
    local = day.astimezone(IST)
    if clock is not None:
        return local.replace(hour=clock[0], minute=clock[1], second=clock[2]).astimezone(UTC), True
    return day, False


def _fills(rows: list[list[str]], headers: list[str], mapping: dict[str, str | None], first_row: int) -> tuple[list[Fill], list[Problem]]:
    fills: list[Fill] = []
    problems: list[Problem] = []
    seen_ids: dict[str, int] = {}
    for n, row in enumerate(rows, start=first_row):
        if not any(c.strip() for c in row):
            continue
        status = _get(row, headers, mapping, "status")
        if status and _NOT_DONE.search(status):
            problems.append(Problem(n, f"Not a filled trade (status: {status})."))
            continue
        symbol_raw = _get(row, headers, mapping, "symbol")
        if not symbol_raw:
            problems.append(Problem(n, "No symbol."))
            continue
        symbol, prefix_exchange, kind = clean_symbol(symbol_raw)
        side = parse_side(_get(row, headers, mapping, "side"))
        if side is None:
            problems.append(Problem(n, f"Side not readable ('{_get(row, headers, mapping, 'side') or 'empty'}')."))
            continue
        quantity = _number(_get(row, headers, mapping, "quantity"))
        if not quantity or quantity == 0:
            problems.append(Problem(n, "No quantity."))
            continue
        quantity = abs(quantity)
        price = _money(_get(row, headers, mapping, "price"))
        if price is None:
            value = _money(_get(row, headers, mapping, "value"))
            price = abs(value) / quantity if value else None
        if price is None or price <= 0:
            problems.append(Problem(n, "No price."))
            continue
        at, timed = _fill_time(row, headers, mapping)
        if at is None:
            problems.append(Problem(n, "No readable date or time."))
            continue
        fill_id = _get(row, headers, mapping, "fill_id")
        if fill_id:
            if fill_id in seen_ids:
                problems.append(Problem(n, f"The same fill as row {seen_ids[fill_id]} (trade id {fill_id}); counted once."))
                continue
            seen_ids[fill_id] = n
        exchange = (_get(row, headers, mapping, "exchange").upper() or prefix_exchange or None)
        fills.append(Fill(n, symbol, exchange[:20] if exchange else None, kind, side, quantity, price, at,
                          abs(_money(_get(row, headers, mapping, "charges")) or 0.0), timed))
    return fills, problems


def pair_fifo(fills: list[Fill]) -> list[Trade]:
    """Round trips per symbol, first-in first-out, in time order (the file's order breaks a tie). A fill that crosses
    zero closes the open trade and opens the other side with the rest (its charges split by quantity). A position still
    open at the end of the file is an open trade (no exit)."""
    trades: list[Trade] = []
    by_symbol: dict[tuple[str, str | None], list[Fill]] = defaultdict(list)
    for f in fills:
        by_symbol[(f.symbol, f.exchange)].append(f)
    for (symbol, exchange), group in by_symbol.items():
        group.sort(key=lambda f: (f.at, f.row))
        current: Trade | None = None
        position = 0.0                 # signed: + long, - short
        opened_value = closed_value = closed_qty = 0.0
        for f in group:
            signed = f.quantity if f.side == "BUY" else -f.quantity
            rest = f.quantity
            while rest > 1e-9:
                if current is None:
                    direction = "LONG" if signed > 0 else "SHORT"
                    current = Trade(symbol, exchange, f.kind, direction, 0.0, f.at, f.price, timed=f.timed)
                    opened_value = closed_value = closed_qty = 0.0
                adds = (current.direction == "LONG") == (signed > 0)
                share = f.charges * (rest / f.quantity) if f.quantity else 0.0
                if adds:
                    opened_value += rest * f.price
                    current.quantity += rest
                    current.entry_price = opened_value / current.quantity
                    position += rest if signed > 0 else -rest
                    current.charges += share
                    current.timed = current.timed and f.timed
                    if f.row not in current.rows:
                        current.rows.append(f.row)
                    rest = 0.0
                    continue
                take = min(rest, abs(position))
                closed_value += take * f.price
                closed_qty += take
                position += take if signed > 0 else -take
                current.charges += f.charges * (take / f.quantity) if f.quantity else 0.0
                current.timed = current.timed and f.timed
                if f.row not in current.rows:
                    current.rows.append(f.row)
                rest -= take
                if abs(position) <= 1e-9:
                    position = 0.0
                    current.exit_time, current.exit_price = f.at, closed_value / closed_qty
                    trades.append(current)
                    current = None
        if current is not None:
            trades.append(current)
    trades.sort(key=lambda t: (t.entry_time, t.symbol))
    return trades


def _whole_trades(rows: list[list[str]], headers: list[str], mapping: dict[str, str | None], first_row: int) -> tuple[list[Trade], list[Problem]]:
    trades: list[Trade] = []
    problems: list[Problem] = []
    for n, row in enumerate(rows, start=first_row):
        if not any(c.strip() for c in row):
            continue
        symbol_raw = _get(row, headers, mapping, "symbol")
        if not symbol_raw:
            problems.append(Problem(n, "No symbol."))
            continue
        symbol, exchange, kind = clean_symbol(symbol_raw)
        side = parse_side(_get(row, headers, mapping, "side"))
        if side is None:
            problems.append(Problem(n, f"Side not readable ('{_get(row, headers, mapping, 'side') or 'empty'}')."))
            continue
        quantity = _number(_get(row, headers, mapping, "quantity"))
        if not quantity:
            problems.append(Problem(n, "No quantity."))
            continue
        entry_time, timed = parse_when(_get(row, headers, mapping, "entry_time"))
        entry_price = _money(_get(row, headers, mapping, "entry_price"))
        if entry_time is None or not entry_price or entry_price <= 0:
            problems.append(Problem(n, "No readable entry time and entry price."))
            continue
        exit_raw, exit_price_raw = _get(row, headers, mapping, "exit_time"), _get(row, headers, mapping, "exit_price")
        exit_time, exit_timed = parse_when(exit_raw) if exit_raw else (None, True)
        exit_price = _money(exit_price_raw) if exit_price_raw else None
        if (exit_time is None) != (exit_price is None):
            problems.append(Problem(n, "The exit needs both its time and its price (leave both empty for an open trade)."))
            continue
        stop = _money(_get(row, headers, mapping, "stop_loss"))
        exchange = _get(row, headers, mapping, "exchange").upper() or exchange
        trades.append(Trade(symbol, exchange[:20] if exchange else None, kind, "LONG" if side == "BUY" else "SHORT", abs(quantity),
                            entry_time, entry_price, exit_time, exit_price,
                            abs(_money(_get(row, headers, mapping, "charges")) or 0.0),
                            stop if stop and stop > 0 else None, _get(row, headers, mapping, "setup")[:100] or None,
                            _get(row, headers, mapping, "notes")[:entries.NOTES_MAX] or None, [n], timed and exit_timed))
    return trades, problems


# --- preview and import -----------------------------------------------------------------------------------------------

@dataclass
class Reading:
    file_name: str
    file_hash: str
    layout: str
    shape: str
    headers: list[str]
    mapping: dict[str, str | None]
    data_rows: int
    trades: list[Trade]
    problems: list[Problem]


def _clean_mapping(given: dict[str, Any] | None, headers: list[str], auto: dict[str, str | None]) -> dict[str, str | None]:
    if given is None:
        return auto
    out: dict[str, str | None] = dict.fromkeys(FIELD_KEYS)
    for key, value in given.items():
        if key not in _BY_KEY:
            raise ImportFileError(f"'{key}' is not a column this import knows.")
        if value in (None, ""):
            continue
        if not isinstance(value, str) or value not in headers:
            raise ImportFileError(f"The column '{value}' for {_BY_KEY[key].label} is not in this file.")
        out[key] = value
    return out


def read_file(file_name: str, data: bytes, mapping: dict[str, Any] | None = None, *, now: datetime | None = None) -> Reading:
    """Pure: the file's trades and problems under `mapping` (the auto-detected one when None)."""
    rows = read_rows(file_name, data)
    if not any(any(c for c in r) for r in rows):
        raise ImportFileError("The file has no rows.")
    h = find_header(rows)
    raw_headers = rows[h]
    headers: list[str] = []
    for i, cell in enumerate(raw_headers):                       # every header is unique and named, so it can be chosen
        name = cell or f"Column {i + 1}"
        while name in headers:
            name = f"{name} ({i + 1})"
        headers.append(name)
    body = rows[h + 1:]
    if len(body) > IMPORT_MAX_ROWS:
        raise ImportFileError(f"The file has more than {IMPORT_MAX_ROWS:,} rows. Split it into smaller files.", 413)
    auto = auto_mapping(headers)
    used = _clean_mapping(mapping, headers, auto)
    if not used.get("symbol"):
        raise ImportFileError("No symbol column was found. Choose which column holds the symbol.")
    shape = shape_of(used)
    first = h + 2                                                # the row number as a spreadsheet shows it
    if shape == "trades":
        trades, problems = _whole_trades(body, headers, used, first)
    else:
        missing = [_BY_KEY[k].label for k in ("side", "quantity") if not used.get(k)]
        if not used.get("price") and not used.get("value"):
            missing.append(_BY_KEY["price"].label)
        if not used.get("time") and not used.get("date"):
            missing.append(_BY_KEY["time"].label)
        if missing:
            raise ImportFileError("Choose the columns for: " + ", ".join(missing) + ". A sheet of whole trades needs Entry time "
                               "and Entry price instead.")
        fills, problems = _fills(body, headers, used, first)
        trades = pair_fifo(fills)
    kept: list[Trade] = []
    when = now or datetime.now(UTC)
    for t in trades:                                             # the same checks as a trade typed by hand
        try:
            entries.manual_values(t.body(), now=when)
        except entries.EntryError as exc:
            problems.extend(Problem(r, f"{_plain(str(exc))}.") for r in t.rows[:1])
            continue
        kept.append(t)
    problems.sort(key=lambda p: p.row)
    data_rows = sum(1 for r in body if any(c.strip() for c in r))
    return Reading(file_name, hashlib.sha256(data).hexdigest(), detect_layout(headers, used), shape, headers, used, data_rows, kept, problems)


def _plain(message: str) -> str:
    words = {"entry_time": "the entry time", "exit_time": "the exit time", "exit_price": "the exit price", "entry_price": "the entry price"}
    for k, v in words.items():
        message = message.replace(k, v)
    return message[:1].upper() + message[1:]


async def _existing_keys(session: AsyncSession, user: User, trades: list[Trade]) -> set[tuple[Any, ...]]:
    if not trades:
        return set()
    start = min(t.entry_time for t in trades) - timedelta(seconds=1)
    end = max(t.entry_time for t in trades) + timedelta(seconds=1)
    rows = await session.scalars(select(JournalManualTradeRecord).where(
        JournalManualTradeRecord.user_id == user.id, JournalManualTradeRecord.tenant_id == user.tenant_id,
        JournalManualTradeRecord.symbol.in_(sorted({t.symbol.upper() for t in trades})),
        JournalManualTradeRecord.entry_time >= start, JournalManualTradeRecord.entry_time <= end))
    return {_key(r.symbol, r.direction, r.quantity, r.entry_time, r.entry_price, r.exit_time, r.exit_price) for r in rows}


async def _earlier_import(session: AsyncSession, user: User, file_hash: str) -> tuple[JournalImportRecord | None, int]:
    """The same file imported before, and how many of its trades are still in the journal."""
    row = await session.scalar(select(JournalImportRecord).where(JournalImportRecord.user_id == user.id,
                                                                 JournalImportRecord.tenant_id == user.tenant_id,
                                                                 JournalImportRecord.file_hash == file_hash))
    if row is None:
        return None, 0
    left = await session.scalar(select(func.count()).select_from(JournalManualTradeRecord).where(
        JournalManualTradeRecord.user_id == user.id, JournalManualTradeRecord.import_id == row.id)) or 0
    return row, int(left)


async def _mark_duplicates(session: AsyncSession, user: User, reading: Reading) -> None:
    existing = await _existing_keys(session, user, reading.trades)
    seen: set[tuple[Any, ...]] = set()
    for t in reading.trades:
        key = t.key()
        t.duplicate = key in existing or key in seen
        seen.add(key)


def _iso(t: datetime | None) -> str | None:
    return t.astimezone(UTC).isoformat() if t is not None else None


def _trade_view(t: Trade) -> dict[str, Any]:
    pnl = None
    if t.exit_price is not None:
        sign = 1 if t.direction == "LONG" else -1
        pnl = round(sign * (t.exit_price - t.entry_price) * t.quantity - t.charges, 2)
    return {"symbol": t.symbol, "exchange": t.exchange, "instrument_kind": t.kind, "direction": t.direction,
            "quantity": round(t.quantity, 6), "entry_time": _iso(t.entry_time), "entry_price": round(t.entry_price, 4),
            "exit_time": _iso(t.exit_time), "exit_price": round(t.exit_price, 4) if t.exit_price is not None else None,
            "charges": round(t.charges, 2), "pnl": pnl, "stop_loss": t.stop_loss, "rows": t.rows, "date_only": not t.timed,
            "duplicate": t.duplicate}


async def preview(session: AsyncSession, user: User, file_name: str, data: bytes, mapping: dict[str, Any] | None = None) -> dict[str, Any]:
    """What an import of this file would store - nothing is written."""
    reading = read_file(file_name, data, mapping)
    await _mark_duplicates(session, user, reading)
    earlier, left = await _earlier_import(session, user, reading.file_hash)
    new = [t for t in reading.trades if not t.duplicate]
    return {
        "file": {"name": file_name, "size": len(data), "hash": reading.file_hash},
        "layout": {"id": reading.layout, "label": LAYOUT_LABELS.get(reading.layout, reading.layout)},
        "shape": reading.shape, "pairing": PAIRING if reading.shape == "fills" else None,
        "headers": reading.headers, "mapping": reading.mapping,
        "fields": [{"key": f.key, "label": f.label} for f in FIELDS],
        "counts": {"rows": reading.data_rows, "problems": len(reading.problems), "trades": len(reading.trades),
                   "duplicates": len(reading.trades) - len(new), "to_import": 0 if left else min(len(new), IMPORT_MAX_TRADES),
                   "open": sum(1 for t in reading.trades if t.exit_time is None),
                   "date_only": sum(1 for t in reading.trades if not t.timed)},
        "problems": [{"row": p.row, "reason": p.reason} for p in reading.problems[:PREVIEW_ROWS]],
        "trades": [_trade_view(t) for t in reading.trades[:PREVIEW_ROWS]],
        "already_imported": {"at": _iso(earlier.created_at if earlier.created_at.tzinfo else earlier.created_at.replace(tzinfo=UTC)),
                             "file_name": earlier.file_name, "trades_left": left} if earlier is not None and left else None,
        "limits": {"max_bytes": IMPORT_MAX_BYTES, "max_rows": IMPORT_MAX_ROWS, "max_trades": IMPORT_MAX_TRADES},
    }


async def commit(session: AsyncSession, user: User, file_name: str, data: bytes, mapping: dict[str, Any] | None = None) -> dict[str, Any]:
    """Stores the file's new trades as manual journal entries (one transaction) and records the file."""
    reading = read_file(file_name, data, mapping)
    earlier, left = await _earlier_import(session, user, reading.file_hash)
    if earlier is not None and left:
        return {"imported": 0, "duplicates": len(reading.trades), "problems": len(reading.problems), "import_id": earlier.id,
                "already_imported": True, "entry_ids": []}
    if earlier is not None:                                      # every trade of it was removed since: it may come in again
        await session.delete(earlier)
        await session.flush()
    await _mark_duplicates(session, user, reading)
    new = [t for t in reading.trades if not t.duplicate]
    if len(new) > IMPORT_MAX_TRADES:
        raise ImportFileError(f"The file holds more than {IMPORT_MAX_TRADES:,} trades. Split it into smaller files.", 413)
    record = JournalImportRecord(tenant_id=user.tenant_id, user_id=user.id, file_hash=reading.file_hash, file_name=(file_name or "file")[:200],
                                 layout=reading.layout, rows=reading.data_rows, imported=len(new), skipped=len(reading.trades) - len(new))
    session.add(record)
    await session.flush()
    rows = [JournalManualTradeRecord(tenant_id=user.tenant_id, user_id=user.id, import_id=record.id, **entries.manual_values(t.body()))
            for t in new]
    session.add_all(rows)
    await session.commit()
    return {"imported": len(rows), "duplicates": len(reading.trades) - len(new), "problems": len(reading.problems),
            "import_id": record.id, "already_imported": False, "entry_ids": [r.id for r in rows]}


__all__ = ["FIELDS", "LAYOUTS", "PAIRING", "IMPORT_MAX_BYTES", "IMPORT_MAX_ROWS", "IMPORT_MAX_TRADES", "ImportFileError", "read_rows",
           "read_file", "pair_fifo", "preview", "commit", "parse_when", "parse_side", "clean_symbol", "auto_mapping"]
