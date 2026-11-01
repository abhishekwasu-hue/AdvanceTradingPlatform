"""M71 PL-7a: a one-time, re-runnable import of the owner's PRIVATE data checkout into HIS private market-data store.

Generic code, no data: it reads the directory ATP_TRADE_DATA_DIR (the host's read-only checkout of the owner's private
data repository, fetched by `atp.sh import-trade-data` with its own read-only deploy key; docs/deploy/TRADE_DATA_SEED.md).
An absent or empty directory -> status "not_available", nothing is read or written.

What goes where (one rule per file SHAPE - its columns and the gaps between its timestamps, never a path list; the
folders read are TRADE_DATA_FOLDERS from the host, else every top-level folder; anything else is skipped and listed):
- OHLC bar files (csv / csv.gz / parquet with timestamp|date, open, high, low, close[, volume][, oi][, contract]):
  `md_tenant_candles` of the owner's tenant ONLY (HB0's owner rule: HISTORY_OWNER_TENANT_ID + HISTORY_OWNER_TENANT_NAME;
  M71Q-2 - never the shared lake, never another organisation). Keys as the store's own writers name them: an index by
  its lake key (NSE_INDEX:NIFTY50), a future NFO:<SYMBOL>:FUT:<expiry> (BFO for a BSE index, MCX:<SYMBOL>:FUT:<expiry>
  for a commodity), labels 1m / 5m / 15m / 30m / 1h / day / week.
- NSE F&O bhavcopy month files (date, symbol, instrument, expiry, strike, opt_type, open .. close, settle, volume, oi):
  the same private store, Daily bars per contract with HB1's keys (NFO:<SYMBOL>:FUT:<expiry>,
  NFO:<SYMBOL>:<CE|PE>:<expiry>:<strike>) - the recorded premiums the option backtest reads first (app.market_lake.
  seed_store.option_premiums). An untraded contract (no volume, open = high = low = 0: its close is NSE's theoretical
  price, not a trade) is left out and counted.

Conventions kept (the store's): bars labelled by their END, UTC (the files are START-labelled; Daily END = the session
close 15:30 IST, Weekly END = the week's last trading day's close); only bars closed by now; nothing invented - a missing
volume stays NULL (a file without a volume column; F&O volume while HISTORY_BACKFILL_FO_VOLUME withholds it), never 0;
two different bars with one END in a file refuse the file. DH3 flags as the store expects: after the import, the
closing-auction flags of every seeded index 1-minute series (`index_history.flag_days`) and `post_close` on every other
intraday bar that starts after continuous trading.

Bookkeeping:
- per file: one `history_import_files` row (HB0's table, dataset "seed"): source path label, sha256, size, row counts,
  symbol, interval, first / last bar, the manifest's own sha256 / missing sessions and the detected gaps (quality_note).
  Idempotent: a file already `done` with the same sha256 is skipped (nothing rewritten); a changed file is re-read
  (identical bars skipped, a changed bar becomes the next version - B1).
- gaps for the paced broker backfill (PL-1c, `lake_backfill_windows`): per seeded index 1-minute series and fixed
  7-day window: every trading session complete -> `done` (never asked); some sessions missing or short -> `gap` with
  only that range (the gap drain asks it); nothing seeded -> no row (the backfill plans it as before).
- the data-use windows of the private policy (PL-7b, app.market_lake.data_use) for the owner's tenant.

CLI (inside the backend container; atp.sh import-trade-data mounts the checkout read-only):
    python -m app.market_lake.seed_import run      import, then print the totals (JSON)
    python -m app.market_lake.seed_import check    dry run: what each file would give (rows, keys, ranges, gaps); no DB
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.db.models import HistoryImportFileRecord, LakeBackfillWindowRecord, MdTenantCandleRecord
from app.market_data.calendar import EXCHANGE_SESSIONS, IST, is_trading_day
from app.market_lake import data_use
from app.market_lake.ingest import LakeBar, write_candles_bulk

logger = logging.getLogger(__name__)

DATASET = "seed"
OBJECT_PREFIX = "trade-data:"
SOURCE_PREFIX = "seed:"
SUFFIXES = (".csv.gz", ".csv", ".parquet")
MCX_COMMODITIES = frozenset({"ALUMINIUM", "COPPER", "CRUDEOIL", "CRUDEOILM", "GOLD", "GOLDM", "GOLDPETAL", "LEAD", "NATURALGAS",
                             "NATGASMINI", "NICKEL", "SILVER", "SILVERM", "SILVERMIC", "ZINC"})
_MONTHS = {m: i for i, m in enumerate(("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1)}
_CONTRACT = re.compile(r"^([A-Z0-9&-]+)\s+FUT\s+(\d{1,2})\s+([A-Z]{3})\s+(\d{2,4})$")
FO_COLUMNS = {"date", "symbol", "instrument", "expiry", "strike", "opt_type", "open", "high", "low", "close"}
W = LakeBackfillWindowRecord
F = HistoryImportFileRecord
R = MdTenantCandleRecord


class SeedConfigError(RuntimeError):
    """The owner's tenant is not configured or not confirmed - nothing is written."""


# --- what a file is ----------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Spec:
    kind: str                 # ohlc | fo_eod
    symbol: str               # the underlying / index name from the file name
    interval: str             # 1m 5m 15m 30m 1h day week (fo_eod: day)
    futures: bool = False     # the rows are futures (a contract column, or a FUT file name)
    official: bool = False    # an official end-of-day file (its own source, never a version of a broker's bars)


def _stem(name: str) -> str:
    low = name.lower()
    for suffix in SUFFIXES:
        if low.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _minutes_label(n: int) -> str | None:
    return {1: "1m", 3: "3m", 5: "5m", 15: "15m", 30: "30m", 60: "1h"}.get(n)


_NOT_SYMBOL = frozenset({"FUT", "FUTURE", "FUTURES", "INDEX", "SPOT", "ALL", "OPT", "OPTIONS", "EOD", "BARS", "DATA", "CANDLES",
                         "DAILY", "WEEKLY", "D", "W", "H", "M", "MIN", "MINUTE", "CSV", "GZ"})


def _symbol_from_name(stem: str) -> str:
    """The instrument a file name names, by token: a known index / commodity first, else the first upper-case word."""
    from app.market_lake.data_use import _index_names
    tokens = [t for t in re.split(r"[^A-Za-z0-9&]+", stem) if t]
    names = _index_names()
    for t in tokens:
        up = t.upper()
        if up in names or up in MCX_COMMODITIES:
            return names.get(up, up)
    for t in tokens:
        if t.isupper() and t.isalpha() and len(t) >= 2 and t not in _NOT_SYMBOL:
            return t
    return ""


def infer_interval(stamps: pd.Series) -> str | None:
    """The bar interval from the timestamps themselves (never from a file name): the most common gap between two
    bars of one day for intraday files (1m .. 1h), else Daily / Weekly by the median gap between sessions."""
    ts = pd.Series(pd.to_datetime(stamps.dropna().unique())).sort_values()
    if len(ts) < 2:
        return None
    gaps = ts.diff().dropna()
    same_day = gaps[ts.iloc[1:].dt.date.values == ts.iloc[:-1].dt.date.values]
    if len(same_day):
        minutes = int(round(same_day.mode().iloc[0].total_seconds() / 60))
        return _minutes_label(minutes)
    days = gaps.median().total_seconds() / 86400
    return "week" if days >= 5 else "day"


def classify(name: str, df: pd.DataFrame | None = None) -> Spec | None:
    """A file's Spec from its SHAPE - columns and timestamps; the name only for the instrument when no column holds
    it. None when it is not a bar file the store takes. Never a list of known paths."""
    if not name.lower().endswith(SUFFIXES) or df is None:
        return None
    cols = {str(c).lower(): c for c in df.columns}
    if FO_COLUMNS <= set(cols):
        return Spec("fo_eod", "", "day", futures=False, official=True)
    tcol = cols.get("timestamp") or cols.get("datetime") or cols.get("date")
    if tcol is None or not {"open", "high", "low", "close"} <= set(cols) or df.empty:
        return None
    stamps = _ist_naive(df[tcol])
    interval = infer_interval(stamps)
    if interval is None:
        return None
    contract = cols.get("contract")
    futures = bool(contract) and df[contract].astype(str).str.upper().str.contains(" FUT ").any()
    symbol = _symbol_from_name(_stem(name))
    if contract and not symbol:
        symbol = str(df[contract].iloc[0]).split()[0].upper()
    official = "date" in cols and "timestamp" not in cols and interval in ("day", "week")
    return Spec("ohlc", symbol, interval, futures=futures, official=official) if symbol else None


def contract_key(contract: str, fallback_symbol: str) -> tuple[str, str]:
    """('NIFTY FUT 30 MAR 23') -> ('NFO:NIFTY:FUT:2023-03-30', 'NFO'); a bare symbol -> its index / stock key."""
    text = " ".join(str(contract or "").upper().split())
    m = _CONTRACT.match(text)
    if not m:
        return spot_key(text or fallback_symbol)
    sym, day, mon, year = m.group(1), int(m.group(2)), _MONTHS.get(m.group(3)), int(m.group(4))
    if mon is None:
        raise ValueError(f"unreadable contract {contract!r}")
    expiry = date(year + 2000 if year < 100 else year, mon, day)
    venue = venue_of(sym)
    return f"{venue}:{sym}:FUT:{expiry.isoformat()}", venue


def venue_of(symbol: str) -> str:
    from app.instruments import master
    if symbol in MCX_COMMODITIES:
        return "MCX"
    return "BFO" if master.INDEX_EXCHANGE.get(symbol) == "BSE" else "NFO"


def spot_key(symbol: str) -> tuple[str, str]:
    """An index -> its lake key (NSE_INDEX:NIFTY50 / BSE_INDEX:SENSEX); anything else -> NSE:<SYMBOL>."""
    from app.instruments import master
    from app.market_lake import index_history as dh
    name = " ".join(symbol.upper().split())
    if name in master.INDEX_SYMBOLS:
        key = dh.lake_key_for(name)
        return key, "BSE" if key.startswith(dh.BSE_INDEX_PREFIX) else "NSE"
    return f"NSE:{name.replace(' ', '')}", "NSE"


def family_of(key: str) -> str:
    return "MCX" if key.startswith("MCX:") else "NSE"


# --- reading -----------------------------------------------------------------------------------------------------------
def read_frame(path: Path) -> pd.DataFrame:
    if path.name.lower().endswith(".parquet"):
        return pd.read_parquet(path)
    return pd.read_csv(path, compression="gzip" if path.name.lower().endswith(".gz") else None)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _ist_naive(values: pd.Series) -> pd.Series:
    ts = pd.to_datetime(values, errors="coerce", utc=False, format="mixed")
    if getattr(ts.dt, "tz", None) is not None:
        return ts.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    return ts


def _int_or_none(v: Any) -> int | None:
    if v is None or (isinstance(v, float) and v != v):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def week_end(monday: date, holidays: set[date]) -> date:
    """The last trading day of the week that starts on `monday` (Friday when the calendar knows no holiday then)."""
    start = monday - timedelta(days=monday.weekday())
    for back in range(4, -1, -1):
        day = start + timedelta(days=back)
        if is_trading_day(day, holidays):
            return day
    return start + timedelta(days=4)


SPECIAL_SESSION_END = time(23, 59)     # a session on a non-trading day (Muhurat, a budget Saturday): its close is not known


def session_end_utc(day: date, family: str = "NSE", holidays: set[date] | None = None) -> datetime:
    """A Daily bar's END: the regular close on a regular trading day; on a special session (a weekend or holiday that
    traded) the END of that day, so no reader sees the day's bar before it can have closed."""
    if holidays is not None and not is_trading_day(day, holidays):
        return datetime.combine(day, SPECIAL_SESSION_END, tzinfo=IST).astimezone(UTC)
    return datetime.combine(day, EXCHANGE_SESSIONS[family].close, tzinfo=IST).astimezone(UTC)


@dataclass
class Parsed:
    bars: list[LakeBar] = field(default_factory=list)
    rows_read: int = 0
    invalid: int = 0
    untraded: int = 0
    forming: int = 0
    duplicates: int = 0
    volume: str = "in file"   # in file | missing | withheld
    refused: str | None = None


def parse_ohlc(df: pd.DataFrame, spec: Spec, *, now: datetime, holidays: set[date]) -> Parsed:
    out = Parsed(rows_read=len(df))
    cols = {c.lower(): c for c in df.columns}
    tcol = cols.get("timestamp") or cols.get("date") or cols.get("datetime")
    if tcol is None or not {"open", "high", "low", "close"} <= set(cols):
        out.refused = "no timestamp / open / high / low / close columns"
        return out
    starts = _ist_naive(df[tcol])
    has_volume = "volume" in cols
    out.volume = "in file" if has_volume else "missing"
    contract = cols.get("contract")
    if spec.futures and not contract:
        out.refused = "a futures file without a contract column (the expiry is not known)"
        return out
    keys: dict[str, tuple[str, str]] = {}
    seen: dict[tuple[str, datetime], LakeBar] = {}
    minutes = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60}.get(spec.interval)
    for start, row in zip(starts, df.to_dict("records")):
        o, h, lo, c = (row[cols[k]] for k in ("open", "high", "low", "close"))
        if pd.isna(start) or any(pd.isna(x) for x in (o, h, lo, c)):
            out.invalid += 1
            continue
        raw_contract = str(row[contract]) if contract else spec.symbol
        if raw_contract not in keys:
            keys[raw_contract] = contract_key(raw_contract, spec.symbol) if contract else spot_key(spec.symbol)
        key, _venue = keys[raw_contract]
        fam = family_of(key)
        if minutes is not None:
            end = (pd.Timestamp(start).to_pydatetime().replace(tzinfo=IST) + timedelta(minutes=minutes)).astimezone(UTC)
        elif spec.interval == "day":
            end = session_end_utc(pd.Timestamp(start).date(), fam, holidays if fam == "NSE" else set())
        else:
            end = session_end_utc(week_end(pd.Timestamp(start).date(), holidays), fam)
        if end > now:
            out.forming += 1
            continue
        volume = _int_or_none(row[cols["volume"]]) if has_volume else None
        oi = _int_or_none(row[cols["oi"]]) if "oi" in cols else None
        bar = LakeBar(key, spec.interval, end, float(o), float(h), float(lo), float(c), volume, oi)
        old = seen.get((key, end))
        if old is not None:
            if old.values() != bar.values():
                out.refused = f"two different bars end at {end.isoformat()} for one instrument"
                return out
            out.duplicates += 1
            continue
        seen[(key, end)] = bar
    out.bars = [seen[k] for k in sorted(seen, key=lambda k: (k[0], k[1]))]
    return out


def parse_fo_eod(df: pd.DataFrame, *, now: datetime, holidays: set[date] | None = None) -> Parsed:
    from app.market_lake.bhavcopy import _strike
    out = Parsed(rows_read=len(df))
    withhold = config.HISTORY_BACKFILL_FO_VOLUME != "as_printed"
    out.volume = "withheld" if withhold else "in file"
    seen: dict[tuple[str, datetime], LakeBar] = {}
    for r in df.itertuples(index=False):
        try:
            day = pd.Timestamp(r.date).date()
            expiry = pd.Timestamp(r.expiry).date()
        except (TypeError, ValueError):
            out.invalid += 1
            continue
        o, h, lo, c = r.open, r.high, r.low, r.close
        vol = _int_or_none(r.volume)
        if any(pd.isna(x) for x in (o, h, lo, c)):
            out.invalid += 1
            continue
        if not vol and float(o) == 0 and float(h) == 0 and float(lo) == 0:
            out.untraded += 1                         # an untraded contract: its close is not a traded price
            continue
        sym, inst = str(r.symbol).upper(), str(r.instrument).upper()
        if inst.startswith("FUT"):
            key = f"NFO:{sym}:FUT:{expiry.isoformat()}"
        elif inst.startswith("OPT") and str(r.opt_type).upper() in ("CE", "PE") and not pd.isna(r.strike):
            key = f"NFO:{sym}:{str(r.opt_type).upper()}:{expiry.isoformat()}:{_strike(float(r.strike))}"
        else:
            out.invalid += 1
            continue
        end = session_end_utc(day, "NSE", holidays if holidays is not None else set())
        if end > now:
            out.forming += 1
            continue
        bar = LakeBar(key, "day", end, float(o), float(h), float(lo), float(c), None if withhold else vol, _int_or_none(r.oi))
        old = seen.get((key, end))
        if old is not None:
            if old.values() != bar.values():
                out.refused = f"two different rows for {key} on {day.isoformat()}"
                return out
            out.duplicates += 1
            continue
        seen[(key, end)] = bar
    out.bars = [seen[k] for k in sorted(seen)]
    return out


# --- gaps ----------------------------------------------------------------------------------------------------------------
def expected_bars(minutes: int, family: str = "NSE") -> int:
    s = EXCHANGE_SESSIONS[family]
    span = (datetime.combine(date(2001, 1, 1), s.close) - datetime.combine(date(2001, 1, 1), s.open)).seconds // 60
    return -(-span // minutes)


def session_counts(ends: Iterable[datetime], minutes: int, family: str = "NSE") -> dict[date, int]:
    """Bars inside the continuous session per IST trade date (by START)."""
    s = EXCHANGE_SESSIONS[family]
    out: Counter[date] = Counter()
    for end in ends:
        start = end.astimezone(IST) - timedelta(minutes=minutes)
        if s.open <= start.time() < s.close:
            out[start.date()] += 1
    return dict(out)


def gaps(counts: dict[date, int], minutes: int, holidays: set[date], family: str = "NSE") -> dict[str, list[str]]:
    """Between the first and the last seeded session: trading days without a bar (missing) and with fewer bars than a
    full session (short, detected missing bars)."""
    if not counts:
        return {"missing": [], "short": []}
    full = expected_bars(minutes, family)
    first, last = min(counts), max(counts)
    missing, short = [], []
    day = first
    while day <= last:
        if is_trading_day(day, holidays):
            n = counts.get(day, 0)
            if n == 0:
                missing.append(day.isoformat())
            elif n < full:
                short.append(day.isoformat())
        day += timedelta(days=1)
    return {"missing": missing, "short": short}


def manifest_info(path: Path) -> dict[str, Any]:
    """What the folder's manifest says about this file: its sha256 / rows and any list of missing sessions."""
    info: dict[str, Any] = {}
    mf = path.parent / "manifest.json"
    if mf.is_file():
        try:
            data = json.loads(mf.read_text(encoding="utf-8"))
        except ValueError:
            return {"manifest": "unreadable"}
        entry: Any = None
        files = data.get("files") if isinstance(data, dict) else None
        if isinstance(files, dict):
            entry = files.get(path.name) or next((v for v in files.values() if isinstance(v, dict) and v.get("file") == path.name), None)
        if isinstance(entry, dict):
            info["manifest"] = mf.name
            for k in ("sha256", "rows"):
                if k in entry:
                    info[f"manifest_{k}"] = entry[k]
            for k, v in entry.items():
                if ("missing" in k or "without_bars" in k) and isinstance(v, list):
                    info[f"manifest_{k}"] = [str(x) for x in v]
    csv_mf = path.parent / "manifest.csv"
    if csv_mf.is_file() and re.fullmatch(r"\d{4}-\d{2}", _stem(path.name)):
        try:
            m = pd.read_csv(csv_mf)
            month = m[m["date"].astype(str).str.startswith(_stem(path.name))]
            bad = month[(month["status"] != "ok") & (pd.to_datetime(month["date"]).dt.weekday < 5)]
            info["manifest"] = csv_mf.name
            info["manifest_weekday_days_not_ok"] = [str(d) for d in bad["date"]]
        except (KeyError, ValueError):
            info["manifest"] = "unreadable"
    return info


# --- one run -------------------------------------------------------------------------------------------------------------
@dataclass
class FileResult:
    path: str
    status: str               # done | skipped | refused | failed | not_market_data
    dataset: str = ""
    symbol: str = ""
    interval: str = ""
    source: str = ""
    sha256: str = ""
    size: int = 0
    rows_read: int = 0
    bars: int = 0
    keys: int = 0
    inserted: int = 0
    corrected: int = 0
    skipped: int = 0
    first: str | None = None
    last: str | None = None
    volume: str = ""
    notes: list[str] = field(default_factory=list)
    gaps: dict[str, Any] = field(default_factory=dict)


@dataclass
class Report:
    status: str = "ok"
    data_dir: str = ""
    files: list[FileResult] = field(default_factory=list)
    windows: dict[str, Any] = field(default_factory=dict)
    policy: dict[str, Any] = field(default_factory=dict)
    flags: int = 0
    notes: list[str] = field(default_factory=list)

    def totals(self) -> dict[str, Any]:
        by = Counter(f.status for f in self.files)
        return {"status": self.status, "files": dict(by), "bars_inserted": sum(f.inserted for f in self.files),
                "bars_in_files": sum(f.bars for f in self.files), "windows": self.windows, "policy": self.policy, "flags": self.flags,
                "notes": self.notes}

    def as_dict(self) -> dict[str, Any]:
        return {**self.totals(), "file_results": [asdict(f) for f in self.files]}


def data_root(path: str | None = None) -> Path | None:
    raw = (path if path is not None else config.TRADE_DATA_DIR) or ""
    if not raw.strip():
        return None
    root = Path(raw).expanduser()
    if not root.is_dir() or not any(root.iterdir()):
        return None
    return root


def data_folders(root: Path) -> list[str]:
    """The top-level folders to read: TRADE_DATA_FOLDERS (the host's .env - the private data's own folder names never
    appear in this public code), else every top-level folder of the checkout (a non-bar file is skipped anyway)."""
    chosen = [f for f in config.TRADE_DATA_FOLDERS if f and "/" not in f and not f.startswith(".")]
    if chosen:
        return chosen
    return sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))


def discover(root: Path) -> list[Path]:
    out: list[Path] = []
    for folder in data_folders(root):
        base = root / folder
        if base.is_dir():
            out += sorted(p for p in base.rglob("*") if p.is_file() and not any(part.startswith(".") for part in p.relative_to(root).parts))
    return out


def source_for(rel: Path, spec: Spec) -> str:
    """One source per top-level folder (and a separate one for official end-of-day files), so two different suppliers
    never become versions of each other; inside one source a later file's changed bar is a correction (B1)."""
    folder = rel.parts[0]
    return (SOURCE_PREFIX + folder + (":eod" if spec.official and spec.kind == "ohlc" else ""))[:30]


def _symbol_of(spec: Spec, bars: Sequence[LakeBar]) -> str:
    if spec.symbol:
        return spec.symbol
    names = Counter(data_use.underlying_of(b.instrument_key) or "?" for b in bars)
    return names.most_common(1)[0][0] if names else ""


def prepare(root: Path, path: Path, *, now: datetime, holidays: set[date]) -> tuple[FileResult, Spec | None, Parsed | None]:
    rel = path.relative_to(root)
    res = FileResult(path=rel.as_posix(), status="not_market_data", dataset=rel.parts[0])
    try:
        df = read_frame(path) if path.name.lower().endswith(SUFFIXES) else None
        spec = classify(path.name, df)
        if spec is None:
            res.notes.append("not a bar file the store takes")
            return res, None, None
        res.sha256, res.interval, res.size = sha256_of(path), spec.interval, path.stat().st_size
        res.source = source_for(rel, spec)
        info = manifest_info(path)
        if info.get("manifest_sha256") and info["manifest_sha256"] != res.sha256:
            res.status = "refused"
            res.notes.append("sha256 differs from the folder's manifest")
            return res, spec, None
        parsed = parse_fo_eod(df, now=now, holidays=holidays) if spec.kind == "fo_eod" else parse_ohlc(df, spec, now=now, holidays=holidays)
        res.rows_read, res.volume = parsed.rows_read, parsed.volume
        if parsed.refused:
            res.status = "refused"
            res.notes.append(parsed.refused)
            return res, spec, None
        res.bars, res.keys = len(parsed.bars), len({b.instrument_key for b in parsed.bars})
        res.symbol = _symbol_of(spec, parsed.bars)
        if parsed.bars:
            res.first = min(b.ts for b in parsed.bars).isoformat()
            res.last = max(b.ts for b in parsed.bars).isoformat()
        for name in ("invalid", "untraded", "forming", "duplicates"):
            if getattr(parsed, name):
                res.notes.append(f"{name}: {getattr(parsed, name)}")
        res.gaps = {k: v for k, v in info.items() if k.startswith("manifest")}
        minutes = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60}.get(spec.interval)
        if minutes and parsed.bars:
            by_key: dict[str, list[datetime]] = defaultdict(list)
            for b in parsed.bars:
                by_key[b.instrument_key].append(b.ts)
            detected = {}
            for key, ends in by_key.items():
                fam = family_of(key)
                g = gaps(session_counts(ends, minutes, fam), minutes, holidays if fam == "NSE" else set(), fam)
                detected[key] = {"sessions": len(session_counts(ends, minutes, fam)), **g}
            res.gaps["detected"] = detected
        res.status = "ready"
        return res, spec, parsed
    except Exception as exc:                           # one bad file never stops the run; it is listed
        res.status = "failed"
        res.notes.append(f"{type(exc).__name__}: {str(exc)[:200]}")
        return res, None, None


async def _owner(session: AsyncSession) -> tuple[int, int]:
    from app.market_lake import intraday_history as hb2
    try:
        tenant_id = await hb2.owner_tenant(session)
        user = await hb2.owner_user(session, tenant_id)
    except hb2.IntradayConfigError as exc:
        raise SeedConfigError(str(exc)) from None
    if user is None:
        raise SeedConfigError("the owner's organisation has no active owner user")
    return tenant_id, int(user.id)


async def _holidays(session: AsyncSession) -> set[date]:
    from app.market_data.calendar import load_holidays
    return set(await load_holidays(session, "NSE") or [])


async def _write_file(session: AsyncSession, tenant_id: int, res: FileResult, spec: Spec, parsed: Parsed, now: datetime) -> None:
    key = (OBJECT_PREFIX + res.path)[:512]
    state = await session.scalar(select(F).where(F.object_key == key))
    if state is not None and state.status == "done" and state.sha256 == res.sha256 and state.tenant_id == tenant_id:
        res.status = "skipped"
        res.notes.append("already imported with the same sha256")
        return
    fresh, same = await _drop_known(session, tenant_id, res.source, parsed.bars)
    written = await write_candles_bulk(session, fresh, res.source, tenant_id=tenant_id, ingested_at=now)
    res.inserted, res.corrected, res.skipped = written.inserted, written.corrected, written.skipped + same
    await session.flush()
    in_store = 0
    if parsed.bars:
        keys = sorted({b.instrument_key for b in parsed.bars})
        cond = [MdTenantCandleRecord.tenant_id == tenant_id, MdTenantCandleRecord.source == res.source,
                MdTenantCandleRecord.timeframe == spec.interval, MdTenantCandleRecord.version == 1,
                MdTenantCandleRecord.ts >= min(b.ts for b in parsed.bars), MdTenantCandleRecord.ts <= max(b.ts for b in parsed.bars)]
        if len(keys) <= 500:
            cond.append(MdTenantCandleRecord.instrument_key.in_(keys))
        in_store = int(await session.scalar(select(func.count()).select_from(MdTenantCandleRecord).where(*cond)) or 0)
    note = json.dumps({"source": res.source, "keys": res.keys, "volume": res.volume, "notes": res.notes, "gaps": res.gaps}, default=str)
    values = dict(manifest_key=str(res.gaps.get("manifest", ""))[:512], dataset=DATASET, exchange=family_of(parsed.bars[0].instrument_key) if parsed.bars else "NSE",
                  symbol=res.symbol[:40] or "?", period=spec.interval[:7], tenant_id=tenant_id, status="done", sha256=res.sha256,
                  size_bytes=res.size, rows_expected=int(res.gaps.get("manifest_rows") or res.rows_read), rows_read=res.rows_read,
                  rows_in_lake=in_store, inserted=res.inserted, corrected=res.corrected, skipped=res.skipped, quality_events=0,
                  quality_note=note, first_ts=min(b.ts for b in parsed.bars) if parsed.bars else None,
                  last_ts=max(b.ts for b in parsed.bars) if parsed.bars else None, error=None, imported_at=now, started_at=now)
    if state is None:
        session.add(F(object_key=key, attempts=1, **values))
    else:
        for k, v in values.items():
            setattr(state, k, v)
        state.attempts = int(state.attempts or 0) + 1
    res.status = "done"


async def _drop_known(session: AsyncSession, tenant_id: int, source: str, bars: Sequence[LakeBar]) -> tuple[list[LakeBar], int]:
    """Bars another file of the same source already stored with the SAME prices are not written again when this file
    adds nothing (its volume / OI missing or equal): two exports of one series differ only in how they print a missing
    volume, which is not a correction. A bar whose prices changed, or that brings a volume / OI the store lacks, is
    written (the next version, B1)."""
    if not bars:
        return [], 0
    known: dict[tuple[str, str, datetime], tuple[int, tuple[float, ...], int | None, int | None]] = {}
    keys = {b.instrument_key for b in bars}
    for tf in sorted({b.timeframe for b in bars}):          # one read per file and label (a month file holds ~2000 keys)
        group = [b for b in bars if b.timeframe == tf]
        query = select(R.instrument_key, R.ts, R.version, R.open, R.high, R.low, R.close, R.volume, R.oi).where(
            R.tenant_id == tenant_id, R.source == source, R.timeframe == tf,
            R.ts >= min(b.ts for b in group), R.ts <= max(b.ts for b in group))
        if len(keys) <= 500:
            query = query.where(R.instrument_key.in_(sorted(keys)))
        for key, ts, version, o, h, lo, c, vol, oi in await session.execute(query):
            if key not in keys:
                continue
            at = (key, tf, ts if ts.tzinfo else ts.replace(tzinfo=UTC))
            if at not in known or version > known[at][0]:
                known[at] = (version, tuple(round(float(x), 4) for x in (o, h, lo, c)), vol, oi)
    fresh: list[LakeBar] = []
    same = 0
    for b in bars:
        old = known.get((b.instrument_key, b.timeframe, b.ts))
        if (old is not None and old[1] == tuple(round(x, 4) for x in (b.open, b.high, b.low, b.close))
                and (b.volume is None or b.volume == old[2]) and (b.oi is None or b.oi == old[3] or (old[3] is None and not b.oi))):
            same += 1
            continue
        fresh.append(b)
    return fresh, same


async def _record_refused(session: AsyncSession, tenant_id: int, res: FileResult, now: datetime) -> None:
    key = (OBJECT_PREFIX + res.path)[:512]
    state = await session.scalar(select(F).where(F.object_key == key))
    values = dict(manifest_key="", dataset=DATASET, exchange="NSE", symbol=(res.symbol or "?")[:40], period=(res.interval or "?")[:7],
                  tenant_id=tenant_id, status=res.status, sha256=res.sha256 or "", rows_expected=0, rows_read=res.rows_read,
                  inserted=0, corrected=0, skipped=0, quality_events=0, error="; ".join(res.notes)[:500], started_at=now)
    if state is None:
        session.add(F(object_key=key, attempts=1, **values))
    elif state.status != "done":
        for k, v in values.items():
            setattr(state, k, v)


async def post_close_flags(session: AsyncSession, tenant_id: int, bars_by_series: dict[tuple[str, str], list[datetime]]) -> int:
    """DH3 `post_close` on every seeded intraday bar (other than the index 1-minute series, flagged by flag_days) that
    starts at or after the continuous session's close."""
    from app.market_lake import index_history as dh
    rows: list[Any] = []
    for (key, tf), ends in bars_by_series.items():
        minutes = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60}.get(tf)
        if not minutes:
            continue
        close = EXCHANGE_SESSIONS[family_of(key)].close
        for end in ends:
            start = end.astimezone(IST) - timedelta(minutes=minutes)
            if start.time() >= close:
                rows.append(dh.FlagRow(key, tf, end, "post_close", None, "bar after continuous trading"))
    return await dh.write_flags(session, tenant_id, rows)


async def seed_windows(session: AsyncSession, tenant_id: int, user_id: int, keys: Sequence[str], now: datetime,
                       holidays: set[date]) -> dict[str, Any]:
    """PL-1c bookkeeping from what the private store now holds (every source the owner reads): per fixed 7-day window of
    each seeded index 1-minute series, `done` when every trading session is complete, `gap` (only the missing range)
    when some are missing or short. A window the backfill already settled is left as it is."""
    from app.market_lake import backfill as bf
    from app.market_lake import index_history as dh
    from app.market_lake import intraday_history as hb2
    from app.market_lake.tenant_store import holder_visible
    last_closed = hb2.last_closed_day(now, holidays)
    full = expected_bars(1)
    out: dict[str, Any] = {"done": 0, "gap": 0, "kept": 0, "series": {}}
    for key in keys:
        sym = bf._symbol_for_key(key)
        if sym is None:
            continue
        ends = [r[0] for r in await session.execute(select(MdTenantCandleRecord.ts).where(
            MdTenantCandleRecord.tenant_id == tenant_id, MdTenantCandleRecord.instrument_key == key, MdTenantCandleRecord.timeframe == "1m",
            holder_visible(MdTenantCandleRecord.source, user_id)).distinct())]
        counts = session_counts([e if e.tzinfo else e.replace(tzinfo=UTC) for e in ends], 1)
        if not counts:
            continue
        first, last = min(counts), min(max(counts), last_closed)
        existing = {r.window_start: r for r in await session.scalars(select(W).where(
            W.tenant_id == tenant_id, W.user_id == user_id, W.instrument_key == key, W.timeframe == "1m"))}
        series = {"done": 0, "gap": 0}
        for ws, we in dh.windows("1m", first, last):
            days = [ws + timedelta(days=i) for i in range((we - ws).days + 1)]
            trading = [d for d in days if d <= last_closed and is_trading_day(d, holidays)]
            lacking = [d for d in trading if counts.get(d, 0) < full]
            if len(lacking) == len(trading):
                continue                                     # nothing seeded here: the backfill plans it as before
            row = existing.get(ws)
            if row is not None and row.status in ("done", "no_data"):
                out["kept"] += 1
                continue
            status = "gap" if lacking or we > last_closed else "done"
            note = bf.gap_note(min(lacking), max(lacking), sym) if lacking else None
            if status == "gap" and note is None:
                status = "open"                              # the window still grows: the backfill asks it as before
            bars = sum(counts.get(d, 0) for d in days)
            if row is None:
                session.add(W(tenant_id=tenant_id, user_id=user_id, instrument_key=key, timeframe="1m", window_start=ws, window_end=we,
                              status=status, bars=bars, attempts=0, error=note, updated_at=now))
            else:
                row.status, row.bars, row.error, row.updated_at = status, bars, note, now
            if status in series:
                series[status] += 1
                out[status] += 1
        out["series"][key] = series
    return out


async def run(session_factory: Any, *, data_dir: str | None = None, now: datetime | None = None) -> Report:
    """The whole import (see the module docstring). Idempotent."""
    now = now or datetime.now(UTC)
    report = Report()
    root = data_root(data_dir)
    if root is None:
        report.status = "not_available"
        report.notes.append("the private data directory is not available (ATP_TRADE_DATA_DIR unset, missing or empty) - nothing done")
        return report
    report.data_dir = "set"
    problem = data_use.policy_problem(root)
    if problem:                                           # fail closed: no policy read -> nothing imported
        report.status = "policy_unreadable"
        report.notes.append(problem)
        return report
    async with session_factory() as session:
        try:
            tenant_id, user_id = await _owner(session)
        except SeedConfigError as exc:
            report.status = "not_configured"
            report.notes.append(str(exc))
            return report
        holidays = await _holidays(session)
    series: dict[tuple[str, str], list[datetime]] = defaultdict(list)
    index_minute_keys: set[str] = set()
    for path in discover(root):
        res, spec, parsed = prepare(root, path, now=now, holidays=holidays)
        report.files.append(res)
        if res.status in ("refused", "failed"):
            async with session_factory() as session:
                await _record_refused(session, tenant_id, res, now)
                await session.commit()
            continue
        if res.status != "ready" or spec is None or parsed is None:
            continue
        async with session_factory() as session:
            try:
                await _write_file(session, tenant_id, res, spec, parsed, now)
                await session.commit()
            except Exception as exc:
                await session.rollback()
                res.status = "failed"
                res.notes.append(f"{type(exc).__name__}: {str(exc)[:200]}")
                await _record_refused(session, tenant_id, res, now)
                await session.commit()
                continue
        if res.status == "done":
            for b in parsed.bars:
                if spec.interval == "1m" and b.instrument_key.split(":", 1)[0] in ("NSE_INDEX", "BSE_INDEX"):
                    index_minute_keys.add(b.instrument_key)
                elif spec.interval not in ("day", "week"):
                    series[(b.instrument_key, spec.interval)].append(b.ts)
        parsed.bars.clear()
    from app.market_lake import index_history as dh
    async with session_factory() as session:
        report.flags = await post_close_flags(session, tenant_id, series)
        for key in sorted(index_minute_keys):
            report.flags += await dh.flag_days(session, tenant_id, user_id, key, date(2000, 1, 1), now.astimezone(IST).date())
        all_minute_keys = sorted({r[0] for r in await session.execute(select(MdTenantCandleRecord.instrument_key).where(
            MdTenantCandleRecord.tenant_id == tenant_id, MdTenantCandleRecord.timeframe == "1m",
            MdTenantCandleRecord.source.like(f"{SOURCE_PREFIX}%")).distinct())})
        report.windows = await seed_windows(session, tenant_id, user_id, all_minute_keys, now, holidays)
        policy = data_use.load_policy(root)
        if policy is None:
            report.policy = {"windows": 0, "note": "the private data states no data-use policy"}
        else:
            report.policy = {"source": policy.source, "windows": len(policy.windows), **await data_use.store(session, tenant_id, policy),
                             "kinds": dict(Counter(w.kind for w in policy.windows))}
        await session.commit()
    if any(f.status in ("refused", "failed") for f in report.files):
        report.status = "partial"
    return report


def check(data_dir: str | None = None, *, now: datetime | None = None, holidays: set[date] | None = None) -> Report:
    """Dry run without a database: what each file would give. Holidays: none unless given (a gap list may then hold a
    holiday; the real run uses ATP's holiday calendar)."""
    now = now or datetime.now(UTC)
    report = Report()
    root = data_root(data_dir)
    if root is None:
        report.status = "not_available"
        report.notes.append("the private data directory is not available (ATP_TRADE_DATA_DIR unset, missing or empty) - nothing done")
        return report
    report.data_dir = "set"
    problem = data_use.policy_problem(root)
    if problem:
        report.status = "policy_unreadable"
        report.notes.append(problem)
    for path in discover(root):
        res, _spec, parsed = prepare(root, path, now=now, holidays=holidays or set())
        if res.status == "ready":
            res.status = "would_import"
        if parsed is not None:
            parsed.bars.clear()
        report.files.append(res)
    policy = data_use.load_policy(root)
    report.policy = {"windows": len(policy.windows), "kinds": dict(Counter(w.kind for w in policy.windows))} if policy else {"windows": 0}
    return report


# --- CLI -----------------------------------------------------------------------------------------------------------------
def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m app.market_lake.seed_import", description=__doc__.split("\n")[0])
    p.add_argument("command", choices=("run", "check"))
    p.add_argument("--data-dir", default=None, help="default: ATP_TRADE_DATA_DIR")
    p.add_argument("--details", action="store_true", help="print one line per file (paths inside the private checkout)")
    return p


async def _main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "check":
        report = check(args.data_dir)
    else:
        from app.db.session import _session_factory
        report = await run(_session_factory, data_dir=args.data_dir)
    print(json.dumps(report.totals(), default=str))
    if args.details:
        for f in report.files:
            print(json.dumps({k: v for k, v in asdict(f).items() if k != "gaps"}, default=str))
    return {"not_available": 0, "not_configured": 2, "policy_unreadable": 3}.get(report.status, 0 if report.status in ("ok", "partial") else 1)


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    return asyncio.run(_main(argv))


if __name__ == "__main__":
    sys.exit(main())
