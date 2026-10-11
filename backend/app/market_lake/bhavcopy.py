"""HB1: NSE's daily files -> rows the lake can take. Pure parsing (no I/O, no database), so every format is tested
with fixture files.

A file is read by WHAT IT IS (its header), never by its date - NSE changed formats over the years and a manual upload
may carry any of them:

  cash (cm)
    cm_legacy  cm<DD><MON><YYYY>bhav.csv(.zip), until 5 Jul 2024:
               SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP[,TOTALTRADES,ISIN]  (older
               files have no TOTALTRADES / ISIN; most end every line with a stray comma)
    cm_full    sec_bhavdata_full_<DDMMYYYY>.csv (the "full bhavcopy" with delivery): SYMBOL, SERIES, DATE1, PREV_CLOSE,
               OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, ... (spaces around
               every cell; no ISIN) - accepted as a manual upload when the bhavcopy itself is blocked
    cm_udiff   BhavCopy_NSE_CM_0_0_0_<YYYYMMDD>_F_0000.csv(.zip), from 8 Jul 2024 (UDiFF): TradDt, ..., ISIN, TckrSymb,
               SctySrs, ..., FinInstrmNm, OpnPric, HghPric, LwPric, ClsPric, ..., TtlTradgVol, ...
  F&O (fo)
    fo_legacy  fo<DD><MON><YYYY>bhav.csv(.zip): INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,OPEN,HIGH,LOW,CLOSE,
               SETTLE_PR,CONTRACTS,VAL_INLAKH,OPEN_INT,CHG_IN_OI,TIMESTAMP
    fo_udiff   BhavCopy_NSE_FO_0_0_0_<YYYYMMDD>_F_0000.csv(.zip): FinInstrmTp (IDF/STF/IDO/STO), TckrSymb, XpryDt, StrkPric,
               OptnTp, OpnPric, ..., OpnIntrst, TtlTradgVol
  indices (index)
    index_close ind_close_all_<DDMMYYYY>.csv - parsed by U1-c's `app.universe.fo_eod.parse_index_closes`.

Rules: nothing is filled in. A row without a usable price is left out and reported (`invalid`), a row that did not
trade (every price 0 / empty and no volume - F&O files list untraded contracts) is left out and counted, a file that
mixes trade dates or has two different rows for one instrument is refused (`BhavcopyRefused`). Lake keys:
`NSE:<SYMBOL>` (cash), `NSE_INDEX:<index code>` (U1 catalogue code), `NFO:<SYMBOL>:FUT:<expiry>` and
`NFO:<SYMBOL>:<CE|PE>:<expiry>:<strike>` (F&O). URLs: `candidate_files` - the format NSE published on that date first.
"""
import csv
import io
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple

from app.instruments import nse_expiries
from app.universe.parsers import ISIN_RE, parse_date

SEGMENTS = ("cm", "index", "fo")
UDIFF_FROM = nse_expiries.UDIFF_FROM                 # 8 Jul 2024: NSE's UDiFF bhavcopies replace the legacy files
ARCHIVE = nse_expiries.ARCHIVE
MAX_KEY = 64


class BhavcopyRefused(ValueError):
    """The file cannot be loaded as it is (wrong kind, mixed dates, conflicting rows); nothing of it is written."""


@dataclass(frozen=True)
class DailyRow:
    instrument_key: str
    symbol: str
    series: str              # cash: EQ / BE / ...; F&O: FUT / CE / PE; indices: INDEX
    isin: Optional[str]
    open: float
    high: float
    low: float
    close: float
    volume: int
    oi: Optional[int] = None
    name: str = ""

    def values(self) -> Tuple:
        return (self.open, self.high, self.low, self.close, self.volume, self.oi)


@dataclass
class ParsedFile:
    segment: str
    file_format: str
    trade_date: date
    rows: List[DailyRow]
    rows_read: int = 0
    skipped: Dict[str, int] = field(default_factory=dict)        # reason -> rows left out (other series, no trade, ...)
    invalid: List[str] = field(default_factory=list)              # instrument keys of rows without a usable price
    duplicates: List[str] = field(default_factory=list)           # identical repeats dropped (reported as `duplicate`)
    notes: List[str] = field(default_factory=list)

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


# --- reading bytes ------------------------------------------------------------------------------------------------
def decode(content: bytes) -> str:
    """The CSV text of a file as received: a zip (the archive's .csv.zip) or plain CSV."""
    if content[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                names = [n for n in z.namelist() if n.lower().endswith(".csv")]
                if len(names) != 1:
                    raise BhavcopyRefused(f"the zip holds {len(names)} CSV files, expected one")
                return z.read(names[0]).decode("utf-8-sig", errors="replace")
        except zipfile.BadZipFile as exc:
            raise BhavcopyRefused(f"not a readable zip ({exc})") from None
    return content.decode("utf-8-sig", errors="replace")


def _norm(header: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (header or "").upper())


def _table(text: str) -> Tuple[List[str], List[Dict[str, str]]]:
    """(normalised headers, rows as {normalised header: stripped cell}); blank lines dropped."""
    reader = csv.reader(io.StringIO(text))
    header: Optional[List[str]] = None
    rows: List[Dict[str, str]] = []
    for raw in reader:
        if not any(cell.strip() for cell in raw):
            continue
        if header is None:
            header = [_norm(h) for h in raw]
            continue
        rows.append({h: (raw[i].strip() if i < len(raw) else "") for i, h in enumerate(header) if h})
    return [h for h in (header or []) if h], rows


def _num(value: str) -> Optional[float]:
    text = (value or "").replace(",", "").strip()
    if text in ("", "-", "NA", "N.A.", "nan"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _int(value: str) -> Optional[int]:
    number = _num(value)
    return int(round(number)) if number is not None else None


def _isin(value: str) -> Optional[str]:
    text = (value or "").strip().upper()
    return text if ISIN_RE.match(text) else None


def _one_date(values: Sequence[Optional[date]], what: str) -> date:
    days = {d for d in values if d is not None}
    if not days:
        raise BhavcopyRefused(f"no readable trade date in the {what} column")
    if len(days) > 1:
        raise BhavcopyRefused(f"the file mixes trade dates ({', '.join(sorted(d.isoformat() for d in days))})")
    return days.pop()


def _iso_or_text(value: str) -> Optional[date]:
    text = (value or "").strip()
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return parse_date(text)


def _strike(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _add(parsed: ParsedFile, row: DailyRow, seen: Dict[str, DailyRow], series_rank: Optional[Dict[str, int]] = None) -> None:
    """One instrument per file: an identical repeat is dropped (reported), a different second row for the same series
    refuses the file, and a symbol in two loaded series keeps the series listed first (the other is counted)."""
    if len(row.instrument_key) > MAX_KEY:
        parsed.skip("key_too_long")
        return
    old = seen.get(row.instrument_key)
    if old is None:
        seen[row.instrument_key] = row
        return
    if old.series == row.series:
        if old.values() == row.values():
            parsed.duplicates.append(row.instrument_key)
            return
        raise BhavcopyRefused(f"two different rows for {row.instrument_key} ({row.series}) - which one is true is not guessed")
    rank = series_rank or {}
    if rank.get(row.series, 99) < rank.get(old.series, 99):
        seen[row.instrument_key] = row
    parsed.skip("second_series")
    parsed.notes.append(f"{row.symbol} in series {old.series} and {row.series}: kept {seen[row.instrument_key].series}")


def _prices(parsed: ParsedFile, key: str, o: Optional[float], h: Optional[float], lo: Optional[float], c: Optional[float],
            volume: Optional[int]) -> Optional[Tuple[float, float, float, float]]:
    """The four prices, or None (row left out): untraded rows are counted, rows with a missing price are reported."""
    prices = (o, h, lo, c)
    if all(p in (None, 0.0) for p in prices) and not volume:
        parsed.skip("no_trade")
        return None
    if any(p is None for p in prices):
        parsed.invalid.append(key)
        return None
    return o, h, lo, c  # type: ignore[return-value]   # all four checked above


# --- cash ---------------------------------------------------------------------------------------------------------
def detect(headers: Sequence[str]) -> str:
    have = set(headers)
    if {"TCKRSYMB", "TRADDT"} <= have:
        return "udiff"                        # one layout for every segment; the Sgmt column says which (CM / FO)
    if {"INSTRUMENT", "EXPIRYDT"} <= have:
        return "fo_legacy"
    if {"SYMBOL", "SERIES", "TOTTRDQTY"} <= have:
        return "cm_legacy"
    if {"SYMBOL", "SERIES", "DATE1", "CLOSEPRICE"} <= have:
        return "cm_full"
    if {"INDEXNAME", "INDEXDATE"} <= have:
        return "index_close"
    return "unknown"


def _udiff_rows(fmt: str, rows: List[Dict[str, str]], segment: str) -> Tuple[str, List[Dict[str, str]]]:
    """UDiFF files share one layout: keep the rows of `segment` (Sgmt CM / FO); a file of another segment is refused."""
    if fmt != "udiff":
        return fmt, rows
    segments = {r.get("SGMT", "").upper() for r in rows} - {""}
    if segments and segment.upper() not in segments:
        raise BhavcopyRefused(f"a UDiFF file of segment {', '.join(sorted(segments))}, not {segment.upper()}")
    return f"{segment}_udiff", [r for r in rows if r.get("SGMT", "").upper() in ("", segment.upper())]


def parse_cm(text: str, series: Sequence[str] = ("EQ", "BE", "BZ", "SM", "ST", "SZ")) -> ParsedFile:
    headers, rows = _table(text)
    fmt, rows = _udiff_rows(detect(headers), rows, "cm")
    if fmt not in ("cm_legacy", "cm_full", "cm_udiff"):
        raise BhavcopyRefused(f"not a cash-market bhavcopy (format {fmt}; headers {headers[:6]}...)")
    wanted = [s.upper() for s in series]
    rank = {s: i for i, s in enumerate(wanted)}
    if fmt == "cm_legacy":
        cols = dict(symbol="SYMBOL", series="SERIES", o="OPEN", h="HIGH", lo="LOW", c="CLOSE", vol="TOTTRDQTY", isin="ISIN",
                    day="TIMESTAMP", name="")
    elif fmt == "cm_full":
        cols = dict(symbol="SYMBOL", series="SERIES", o="OPENPRICE", h="HIGHPRICE", lo="LOWPRICE", c="CLOSEPRICE",
                    vol="TTLTRDQNTY", isin="", day="DATE1", name="")
    else:
        cols = dict(symbol="TCKRSYMB", series="SCTYSRS", o="OPNPRIC", h="HGHPRIC", lo="LWPRIC", c="CLSPRIC", vol="TTLTRADGVOL",
                    isin="ISIN", day="TRADDT", name="FININSTRMNM")
    trade_date = _one_date([_iso_or_text(r.get(cols["day"], "")) for r in rows], cols["day"])
    parsed = ParsedFile("cm", fmt, trade_date, [], rows_read=len(rows))
    if fmt == "cm_full":
        parsed.notes.append("full bhavcopy (sec_bhavdata_full): no ISIN column")
    elif "ISIN" not in headers:
        parsed.notes.append("an older layout without ISIN")
    seen: Dict[str, DailyRow] = {}
    for r in rows:
        symbol, ser = r.get(cols["symbol"], "").upper(), r.get(cols["series"], "").upper()
        if not symbol:
            parsed.skip("no_symbol")
            continue
        if ser not in rank:
            parsed.skip("other_series")
            continue
        key = f"NSE:{symbol}"
        volume = _int(r.get(cols["vol"], ""))
        prices = _prices(parsed, key, _num(r.get(cols["o"], "")), _num(r.get(cols["h"], "")), _num(r.get(cols["lo"], "")),
                         _num(r.get(cols["c"], "")), volume)
        if prices is None:
            continue
        _add(parsed, DailyRow(key, symbol, ser, _isin(r.get(cols["isin"], "")) if cols["isin"] else None, *prices,
                              volume=volume or 0, name=r.get(cols["name"], "")[:200] if cols["name"] else ""), seen, rank)
    parsed.rows = sorted(seen.values(), key=lambda x: x.instrument_key)
    return parsed


# --- F&O ----------------------------------------------------------------------------------------------------------
LEGACY_FO = {"FUTIDX": "FUT", "FUTSTK": "FUT", "FUTIVX": "FUT", "OPTIDX": "OPT", "OPTSTK": "OPT"}
UDIFF_FO = {"IDF": "FUT", "STF": "FUT", "IDO": "OPT", "STO": "OPT"}


def parse_fo(text: str, *, with_options: bool = False) -> ParsedFile:
    """Futures always; options only `with_options` (tens of thousands of contracts a day). The volume unit is the
    file's own: CONTRACTS in the legacy file, TtlTradgVol in UDiFF (see the design note's open questions)."""
    headers, rows = _table(text)
    fmt, rows = _udiff_rows(detect(headers), rows, "fo")
    if fmt not in ("fo_legacy", "fo_udiff"):
        raise BhavcopyRefused(f"not an F&O bhavcopy (format {fmt}; headers {headers[:6]}...)")
    legacy = fmt == "fo_legacy"
    day_col = "TIMESTAMP" if legacy else "TRADDT"
    trade_date = _one_date([_iso_or_text(r.get(day_col, "")) for r in rows], day_col)
    parsed = ParsedFile("fo", fmt, trade_date, [], rows_read=len(rows))
    seen: Dict[str, DailyRow] = {}
    for r in rows:
        kind = (LEGACY_FO if legacy else UDIFF_FO).get(r.get("INSTRUMENT" if legacy else "FININSTRMTP", "").upper())
        if kind is None:
            parsed.skip("other_instrument")
            continue
        if kind == "OPT" and not with_options:
            parsed.skip("options_not_loaded")
            continue
        symbol = r.get("SYMBOL" if legacy else "TCKRSYMB", "").upper()
        expiry = _iso_or_text(r.get("EXPIRYDT" if legacy else "XPRYDT", ""))
        if not symbol or expiry is None:
            parsed.skip("no_contract")
            continue
        if kind == "FUT":
            key, series = f"NFO:{symbol}:FUT:{expiry.isoformat()}", "FUT"
        else:
            right = r.get("OPTIONTYP" if legacy else "OPTNTP", "").upper()
            strike = _num(r.get("STRIKEPR" if legacy else "STRKPRIC", ""))
            if right not in ("CE", "PE") or strike is None:
                parsed.skip("no_contract")
                continue
            key, series = f"NFO:{symbol}:{right}:{expiry.isoformat()}:{_strike(strike)}", right
        volume = _int(r.get("CONTRACTS" if legacy else "TTLTRADGVOL", ""))
        prices = _prices(parsed, key, _num(r.get("OPEN" if legacy else "OPNPRIC", "")), _num(r.get("HIGH" if legacy else "HGHPRIC", "")),
                         _num(r.get("LOW" if legacy else "LWPRIC", "")), _num(r.get("CLOSE" if legacy else "CLSPRIC", "")), volume)
        if prices is None:
            continue
        _add(parsed, DailyRow(key, symbol, series, None, *prices, volume=volume or 0,
                              oi=_int(r.get("OPENINT" if legacy else "OPNINTRST", ""))), seen)
    parsed.rows = sorted(seen.values(), key=lambda x: x.instrument_key)
    return parsed


# --- indices ------------------------------------------------------------------------------------------------------
def parse_index(text: str):
    """(ParsedFile of lake rows, the U1-c IndexClose list). Every close goes to `index_eod` (U1-c); only rows with all
    four prices become lake bars - an index published with a close only is not given an invented open/high/low."""
    from app.universe.fo_eod import code_for, parse_index_closes
    from app.universe.parsers import LayoutError

    headers, raw_rows = _table(text)
    if detect(headers) != "index_close":
        raise BhavcopyRefused(f"not an all-indices close file (headers {headers[:6]}...)")
    try:
        closes = parse_index_closes(text.encode("utf-8"))
    except LayoutError as exc:
        raise BhavcopyRefused(f"not an all-indices close file ({exc})") from None
    trade_date = _one_date([c.trade_date for c in closes], "Index Date")
    parsed = ParsedFile("index", "index_close", trade_date, [], rows_read=len(raw_rows))
    seen: Dict[str, DailyRow] = {}
    for c in closes:
        key = f"NSE_INDEX:{code_for(c.name)}"
        if c.open is None or c.high is None or c.low is None:
            parsed.skip("close_only")
            continue
        _add(parsed, DailyRow(key, code_for(c.name), "INDEX", None, c.open, c.high, c.low, c.close, 0, name=c.name[:200]), seen)
    parsed.rows = sorted(seen.values(), key=lambda x: x.instrument_key)
    return parsed, closes


def parse(segment: str, content: bytes, *, series: Sequence[str] = ("EQ", "BE", "BZ", "SM", "ST", "SZ"),
          with_options: bool = False):
    """(ParsedFile, extra) for any segment; `extra` is the IndexClose list for `index`, else None."""
    text = decode(content)
    if segment == "index":
        return parse_index(text)
    if segment == "cm":
        return parse_cm(text, series), None
    if segment == "fo":
        return parse_fo(text, with_options=with_options), None
    raise BhavcopyRefused(f"unknown segment {segment!r}")


# --- where the files are -----------------------------------------------------------------------------------------
def cm_legacy_url(day: date) -> str:
    mon = day.strftime("%b").upper()
    return f"{ARCHIVE}/content/historical/EQUITIES/{day.year}/{mon}/cm{day:%d}{mon}{day.year}bhav.csv.zip"


def cm_udiff_url(day: date) -> str:
    return f"{ARCHIVE}/content/cm/BhavCopy_NSE_CM_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"


def index_url(day: date) -> str:
    from app.core import config

    template = config.UNIVERSE_FILE_URLS.get("index_close_all") or f"{ARCHIVE}/content/indices/ind_close_all_{{ddmmyyyy}}.csv"
    return template.replace("{ddmmyyyy}", day.strftime("%d%m%Y"))


def candidate_files(segment: str, day: date) -> List[Tuple[str, str]]:
    """[(url, file name)] to try in order: the format NSE published on that date first, then the other one (the
    archive has served both names around the switch)."""
    if segment == "cm":
        urls = [cm_udiff_url(day), cm_legacy_url(day)]
    elif segment == "fo":
        urls = [nse_expiries.udiff_url(day), nse_expiries.legacy_url(day)]
    elif segment == "index":
        return [(index_url(day), index_url(day).rsplit("/", 1)[-1])]
    else:
        raise ValueError(f"unknown segment {segment!r}")
    if day < UDIFF_FROM:
        urls.reverse()
    return [(u, u.rsplit("/", 1)[-1]) for u in urls]


def local_names(segment: str, day: date) -> List[str]:
    """File names a local directory (or a bulk download) may hold for that day - with or without .zip, plus the full
    bhavcopy for cash."""
    names: List[str] = []
    for _, name in candidate_files(segment, day):
        names += [name, name[:-4]] if name.endswith(".zip") else [name]
    if segment == "cm":
        names.append(f"sec_bhavdata_full_{day:%d%m%Y}.csv")
    return names
