"""HB0: import the R2 history store into the lake - DAILY into the shared lake, 1-MINUTE into the owner's private store.

Layout written by the Trade-repo collector (docs/design/HB_HISTORY_BACKFILL.md):
    daily/{exchange}/{symbol}/{yyyy}.parquet      m1/{exchange}/{symbol}/{yyyy-mm}.parquet
Parquet (zstd), columns ts (UTC bar END), open, high, low, close, volume, oi (nullable), isin, source, fetched_at;
unadjusted prices. Every folder carries a `manifest.json` listing its files with row counts and sha256 checksums.

Rules:
- Nothing runs on its own: the `history_import` feature flag must be on, and a person runs the CLI.
- 1-minute data is licensed to the owner: it goes ONLY to `md_tenant_candles` for HISTORY_OWNER_TENANT_ID; without that
  setting the importer refuses to load it. Daily bars go to the shared lake (`md_candles`).
- A file is refused (status `refused`, nothing written) when its sha256 or row count disagrees with the manifest, when
  it is not in the layout, has a bar outside its period or the manifest's date range, a row without a price, or two
  different bars with the same END. Gaps are reported (data-quality events), never filled.
- Idempotent and resumable: one row per object in `history_import_files`. A file is written and marked `done` in one
  transaction; a re-run skips files already `done` with the same sha256 and retries everything else (including one
  left `running` by a crash). Re-importing changed bars writes them as new versions (B1), identical bars are skipped.
- After writing, the lake is counted for the file's range and must hold every bar the file had.
- Bar labels are checked, never shifted: a DAILY file must hold at most one bar per trade date, every END at 15:30 IST
  (10:00 UTC), no Saturday / Sunday (so a file of intraday bars can never reach the shared lake as daily bars); a
  1-MINUTE file's ENDs must be whole minutes inside the session (09:16 .. 15:30 IST) - a START-labelled file (a bar
  ending at 09:15) is refused rather than loaded one minute early.
- The owner tenant must be confirmed: HISTORY_OWNER_TENANT_NAME must equal that tenant's name, so a mistyped id cannot
  send licensed data to another customer. `--purge-tenant ID --confirm-name NAME` removes a tenant's imported
  1-minute rows, their quality events and state rows (the remedy if it ever happened).
- One run at a time per database (a Postgres advisory lock); a second run refuses to start.

CLI:  python -m app.market_lake.r2_import --dataset daily|m1 [--symbols A,B] [--limit N] [--local-dir DIR]
      python -m app.market_lake.r2_import --purge-tenant ID --confirm-name "TENANT NAME"
"""
import argparse
import asyncio
import hashlib
import io
import json
import logging
import math
import re
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import delete, distinct, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.db.models import DataQualityEventRecord, HistoryImportFileRecord, MdCandleRecord, MdTenantCandleRecord, Tenant
from app.market_data.calendar import EXCHANGE_SESSIONS, IST, is_trading_day, load_holidays, session_family
from app.market_lake import quality
from app.market_lake.ingest import DAILY_END_IST, LakeBar, trade_date, write_candles_bulk
from app.market_lake.object_store import LocalDirStore, ObjectStore, R2Store

logger = logging.getLogger(__name__)

FLAG = "history_import"
DATASETS = ("daily", "m1")
MANIFEST = "manifest.json"
TIMEFRAME = {"daily": "day", "m1": "1m"}
LAYOUT = {
    "daily": re.compile(r"^daily/(?P<exchange>[A-Za-z0-9_]{1,20})/(?P<symbol>[^/]{1,40})/(?P<period>\d{4})\.parquet$"),
    "m1": re.compile(r"^m1/(?P<exchange>[A-Za-z0-9_]{1,20})/(?P<symbol>[^/]{1,40})/(?P<period>\d{4}-(0[1-9]|1[0-2]))\.parquet$"),
}
REQUIRED_COLUMNS = ("ts", "open", "high", "low", "close", "volume", "oi", "isin", "source", "fetched_at")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
LOCK_KEY = 0x48420001          # pg advisory lock id of the importer ("HB" 0001)


class ImportConfigError(RuntimeError):
    """The run cannot start (flag off, owner tenant missing for 1-minute data, store not configured)."""


class ImportRefused(Exception):
    """This file will not be loaded as it is; recorded as `refused` with the reason."""


@dataclass
class ManifestFile:
    key: str
    manifest_key: str
    dataset: str
    exchange: str
    symbol: str
    period: str
    rows: int
    sha256: str
    date_from: Optional[date] = None
    date_to: Optional[date] = None


@dataclass
class ImportSummary:
    manifests: int = 0
    files: int = 0
    done: int = 0
    skipped: int = 0
    refused: int = 0
    failed: int = 0
    rows: int = 0
    inserted: int = 0
    corrected: int = 0
    quality_events: int = 0
    errors: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)


def bar_date(ts: datetime) -> date:
    """The trade date a bar belongs to - `ingest.trade_date`, the same rule the gap check and the adjustment use."""
    return trade_date(_utc(ts))


def _number(value: Any, what: str, *, nullable: bool) -> Optional[float]:
    """A volume / OI cell: None stays None (OI) or 0 (volume); NaN / infinite / negative refuses the file."""
    if value is None:
        return None if nullable else 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ImportRefused(f"unreadable {what} {value!r}") from None
    if math.isnan(number) and nullable:
        return None                    # a float column's missing OI
    if math.isnan(number) or math.isinf(number) or number < 0:
        raise ImportRefused(f"{what} {value!r} is not a count - nothing is filled in")
    return number


def check_labels(dataset: str, bars: Sequence[LakeBar], exchange: str = "NSE") -> None:
    """Refuses bars whose labels do not fit the dataset (see the module docstring); never shifts them."""
    if dataset == "daily":
        per_day: Dict[date, set] = defaultdict(set)      # distinct ENDs: an identical repeat is _dedupe's business
        off_time, weekend = [], []
        for bar in bars:
            ist = _utc(bar.ts).astimezone(IST)
            day = bar_date(bar.ts)
            per_day[day].add(_utc(bar.ts))
            if (ist.hour, ist.minute) != DAILY_END_IST or ist.second or ist.microsecond:
                off_time.append(ist)
            if day.weekday() >= 5:
                weekend.append(day)
        many = sorted(d for d, ends in per_day.items() if len(ends) > 1)
        if many:
            raise ImportRefused(f"not daily bars: {len(many)} trade date(s) with more than one bar (first {many[0]})")
        if off_time:
            raise ImportRefused(f"{len(off_time)} daily bar(s) not labelled by the 15:30 IST close (10:00 UTC) END - "
                                f"first {off_time[0].isoformat()}")
        if weekend:
            raise ImportRefused(f"{len(weekend)} daily bar(s) on a Saturday / Sunday (first {weekend[0]}) - load a special "
                                "session from the exchange's own file (HB1)")
        return
    family = EXCHANGE_SESSIONS[session_family(exchange)]
    bad = []
    for bar in bars:
        ist = _utc(bar.ts).astimezone(IST)
        day = ist.date()
        open_, close = datetime.combine(day, family.open, tzinfo=IST), datetime.combine(day, family.close, tzinfo=IST)
        if ist.second or ist.microsecond or not (open_ < ist <= close):
            bad.append(ist)
    if bad:
        raise ImportRefused(f"{len(bad)} 1-minute bar END(s) outside {family.open:%H:%M}-{family.close:%H:%M} IST or not on a "
                            f"whole minute (first {bad[0].isoformat()}) - a START-labelled file is refused, not shifted")


def lake_source(raw: Optional[str]) -> str:
    """The lake `source` for a row of the R2 store: 'r2:' + the collector's source (e.g. r2:upstox), max 30 chars."""
    clean = re.sub(r"[^a-z0-9_.:-]", "", str(raw or "").strip().lower()) or "unknown"
    return f"r2:{clean}"[:30]


def _date(value: Any) -> Optional[date]:
    if value in (None, ""):
        return None
    return date.fromisoformat(str(value)[:10])


# --- manifest -----------------------------------------------------------------------------------------------------
def parse_manifest(manifest_key: str, raw: bytes) -> Tuple[List[ManifestFile], List[Tuple[str, str]]]:
    """(files to import, refusals as (object key, reason)). A manifest that cannot be read at all raises ImportRefused.

    Accepted shape (per folder, e.g. daily/NSE/manifest.json or daily/NSE/RELIANCE/manifest.json):
      {"dataset": "daily", "exchange": "NSE", "symbols": [...], "date_range": {"from": "2016-01-01", "to": "2025-12-31"},
       "row_counts": {"RELIANCE": 2480} | 2480,
       "files": [{"path": "RELIANCE/2024.parquet", "rows": 248, "sha256": "<64 hex>"}, ...]}
    `files` may also be an object keyed by path. Paths are relative to the manifest's folder (or full keys)."""
    folder = manifest_key.rsplit("/", 1)[0] if "/" in manifest_key else ""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ImportRefused(f"{manifest_key}: not valid JSON ({exc})") from None
    if not isinstance(data, dict) or not isinstance(data.get("files"), (list, dict)):
        raise ImportRefused(f"{manifest_key}: no 'files' list")
    entries: List[Dict[str, Any]] = []
    if isinstance(data["files"], dict):
        for path, meta in data["files"].items():
            entries.append({"path": path, **(meta if isinstance(meta, dict) else {})})
    else:
        entries = [e for e in data["files"] if isinstance(e, dict)]
    raw_range = data.get("date_range")
    rng: Dict[str, Any] = raw_range if isinstance(raw_range, dict) else {}
    try:
        date_from, date_to = _date(rng.get("from") or rng.get("start")), _date(rng.get("to") or rng.get("end"))
    except ValueError:
        raise ImportRefused(f"{manifest_key}: unreadable date_range {rng!r}") from None
    symbols = data.get("symbols")
    listed = {str(s) for s in symbols} if isinstance(symbols, list) else None

    files: List[ManifestFile] = []
    refusals: List[Tuple[str, str]] = []
    for entry in entries:
        path = str(entry.get("key") or entry.get("path") or entry.get("file") or "").strip().lstrip("/")
        if not path:
            refusals.append((manifest_key, "a files entry without a path"))
            continue
        key = path if path.split("/", 1)[0] in DATASETS else (f"{folder}/{path}" if folder else path)
        dataset = key.split("/", 1)[0]
        match = LAYOUT[dataset].match(key) if dataset in LAYOUT else None
        if match is None:
            refusals.append((key, f"not in the store layout ({manifest_key})"))
            continue
        if folder and not key.startswith(folder + "/"):
            refusals.append((key, f"listed by {manifest_key} but outside its folder"))
            continue
        symbol = match.group("symbol")
        if listed is not None and symbol not in listed:
            refusals.append((key, f"symbol {symbol} is not in the manifest's symbols"))
            continue
        sha = str(entry.get("sha256") or "").strip().lower()
        try:
            rows = int(entry.get("rows", entry.get("row_count")))  # type: ignore[arg-type]  # None -> TypeError -> refused
        except (TypeError, ValueError):
            refusals.append((key, "the manifest gives no row count"))
            continue
        if not SHA256.match(sha):
            refusals.append((key, "the manifest gives no valid sha256"))
            continue
        files.append(ManifestFile(key, manifest_key, dataset, match.group("exchange").upper(), symbol, match.group("period"),
                                  rows, sha, date_from, date_to))

    counts = data.get("row_counts")
    per_symbol: Dict[str, int] = defaultdict(int)
    for f in files:
        per_symbol[f.symbol] += f.rows
    bad: Dict[str, str] = {}
    if isinstance(counts, dict):
        for sym, n in counts.items():
            if str(sym) in per_symbol and per_symbol[str(sym)] != int(n):
                bad[str(sym)] = f"the manifest's row_counts for {sym} ({n}) disagree with the sum of its files ({per_symbol[str(sym)]})"
    elif isinstance(counts, int) and not isinstance(counts, bool) and sum(per_symbol.values()) != counts:
        bad = {sym: f"the manifest's total row_counts ({counts}) disagree with the sum of its files ({sum(per_symbol.values())})"
               for sym in per_symbol}
    if bad:
        refusals += [(f.key, bad[f.symbol]) for f in files if f.symbol in bad]
        files = [f for f in files if f.symbol not in bad]
    return files, refusals


# --- one file -----------------------------------------------------------------------------------------------------
@dataclass
class _Outcome:
    rows_read: int = 0
    rows_in_lake: int = 0
    inserted: int = 0
    corrected: int = 0
    skipped: int = 0
    quality_events: int = 0
    notes: List[str] = field(default_factory=list)
    isin: Optional[str] = None
    first_ts: Optional[datetime] = None
    last_ts: Optional[datetime] = None


def _read_parquet(data: bytes):
    import pyarrow.parquet as pq  # the API process never imports it

    try:
        return pq.read_table(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001 - any parse failure is a refusal of this file, with the reason
        raise ImportRefused(f"not a readable Parquet file ({type(exc).__name__}: {exc})") from None


def _price(value: Any) -> Optional[float]:
    if value is None:
        return None
    number = float(value)
    return None if math.isnan(number) or math.isinf(number) else number


def _bars_from_table(mf: ManifestFile, table) -> Tuple[Dict[str, List[LakeBar]], List[str]]:
    """Validated bars per lake source, plus the file's ISINs. Raises ImportRefused on anything it would have to guess."""
    missing = [c for c in REQUIRED_COLUMNS if c not in table.column_names]
    if missing:
        raise ImportRefused(f"missing column(s) {', '.join(missing)}")
    if table.num_rows != mf.rows:
        raise ImportRefused(f"row count: manifest {mf.rows}, file {table.num_rows}")
    import pyarrow as pa

    ts_column = table.column("ts")
    if not pa.types.is_timestamp(ts_column.type):
        raise ImportRefused(f"column ts is {ts_column.type}, not a timestamp (the bar END in UTC)")
    try:        # naive timestamps are taken as UTC (the layout says UTC); sub-microsecond values are refused, not rounded
        ts_values = ts_column.cast(pa.timestamp("us", tz="UTC")).to_pylist()
    except (pa.ArrowInvalid, pa.ArrowNotImplementedError) as exc:
        raise ImportRefused(f"column ts cannot be read as UTC microseconds ({exc})") from None
    cols = {c: table.column(c).to_pylist() for c in ("open", "high", "low", "close", "volume", "oi", "isin", "source")}
    cols["ts"] = ts_values
    key, tf = f"{mf.exchange}:{mf.symbol.upper()}", TIMEFRAME[mf.dataset]
    if len(key) > 64:
        raise ImportRefused(f"instrument key {key!r} is longer than 64 characters")
    by_source: Dict[str, List[LakeBar]] = defaultdict(list)
    no_price = outside = outside_range = 0
    for i in range(table.num_rows):
        ts = cols["ts"][i]
        if not isinstance(ts, datetime):
            no_price += 1
            continue
        ts = _utc(ts)
        day = bar_date(ts)
        if (day.strftime("%Y") if mf.dataset == "daily" else day.strftime("%Y-%m")) != mf.period:
            outside += 1
            continue
        if (mf.date_from and day < mf.date_from) or (mf.date_to and day > mf.date_to):
            outside_range += 1
            continue
        o, h, lo, c = (_price(cols[name][i]) for name in ("open", "high", "low", "close"))
        if o is None or h is None or lo is None or c is None:
            no_price += 1
            continue
        volume = _number(cols["volume"][i], "volume", nullable=False)
        oi = _number(cols["oi"][i], "oi", nullable=True)
        by_source[lake_source(cols["source"][i])].append(
            LakeBar(key, tf, ts, o, h, lo, c, int(volume or 0), int(oi) if oi is not None else None))
    if no_price:
        raise ImportRefused(f"{no_price} row(s) without a timestamp or a price - nothing is filled in")
    if outside:
        raise ImportRefused(f"{outside} bar(s) outside the file's period {mf.period}")
    if outside_range:
        raise ImportRefused(f"{outside_range} bar(s) outside the manifest's date range")
    for bars in by_source.values():
        check_labels(mf.dataset, bars, mf.exchange)
    isins = sorted({str(v).strip() for v in cols["isin"] if v not in (None, "")})
    return by_source, isins


def _dedupe(bars: List[LakeBar], source: str) -> Tuple[List[LakeBar], List[quality.QualityEvent]]:
    """Identical repeats of a bar are dropped (and reported); two different bars with one END are refused."""
    seen: Dict[datetime, LakeBar] = {}
    for bar in bars:
        ts = _utc(bar.ts)
        if ts in seen and seen[ts].values() != bar.values():
            raise ImportRefused(f"two different bars end at {ts.isoformat()} ({source}) - which one is true is not guessed")
        seen.setdefault(ts, bar)
    return [seen[ts] for ts in sorted(seen)], quality.find_duplicates(bars, source)


async def _trading_days(session: AsyncSession, exchange: str, years: Sequence[int]) -> Tuple[List[date], List[int]]:
    """Weekdays minus holidays for the years that HAVE a holiday calendar; the years without one are returned apart."""
    calendar = EXCHANGE_SESSIONS[session_family(exchange)].holiday_calendar
    days: List[date] = []
    unknown: List[int] = []
    for year in sorted(set(years)):
        holidays = await load_holidays(session, calendar, year)
        if not holidays:
            unknown.append(year)
            continue
        day = date(year, 1, 1)
        while day.year == year:
            if is_trading_day(day, holidays):
                days.append(day)
            day += timedelta(days=1)
    return days, unknown


async def _quality(session: AsyncSession, mf: ManifestFile, source: str, bars: List[LakeBar],
                   dupes: List[quality.QualityEvent], tenant_id: Optional[int], notes: List[str]) -> int:
    events = list(dupes) + quality.find_bar_problems(bars, source)      # no `late` check: a backfill is late by nature
    if mf.dataset == "m1":
        per_day: Dict[date, List[LakeBar]] = defaultdict(list)
        for bar in bars:
            per_day[_utc(bar.ts).astimezone(IST).date()].append(bar)
        for day, day_bars in sorted(per_day.items()):
            events += quality.find_gaps(day_bars, day, source)
    else:
        days, unknown = await _trading_days(session, mf.exchange, [bar_date(b.ts).year for b in bars])
        if unknown:
            notes.append(f"no {mf.exchange} holiday calendar for {', '.join(map(str, unknown))}: daily gap check skipped")
        known = [b for b in bars if bar_date(b.ts).year not in unknown]
        events += quality.find_daily_gaps(known, days, source)
    await quality.record(session, events, tenant_id=tenant_id, commit=False)
    return len(events)


async def _count_in_lake(session: AsyncSession, mf: ManifestFile, source: str, first: datetime, last: datetime,
                         tenant_id: Optional[int]) -> int:
    model = MdTenantCandleRecord if tenant_id is not None else MdCandleRecord
    query = select(func.count(distinct(model.ts))).where(
        model.instrument_key == f"{mf.exchange}:{mf.symbol.upper()}", model.timeframe == TIMEFRAME[mf.dataset],
        model.source == source, model.ts >= first, model.ts <= last)
    if tenant_id is not None:
        query = query.where(MdTenantCandleRecord.tenant_id == tenant_id)
    return int(await session.scalar(query) or 0)


async def load_file(session: AsyncSession, mf: ManifestFile, data: bytes, *, tenant_id: Optional[int],
                    now: datetime) -> _Outcome:
    """Checks, writes and verifies one object. Does not commit (the caller commits it with the file's state row)."""
    digest = hashlib.sha256(data).hexdigest()
    if digest != mf.sha256:
        raise ImportRefused(f"sha256 mismatch: manifest {mf.sha256}, object {digest}")
    if mf.dataset == "m1" and tenant_id is None:
        raise ImportConfigError("1-minute data needs the owner tenant (HISTORY_OWNER_TENANT_ID)")
    by_source, isins = _bars_from_table(mf, _read_parquet(data))
    out = _Outcome(rows_read=mf.rows)
    if len(isins) > 1:
        out.notes.append(f"several ISINs in one file: {', '.join(isins)}")
    out.isin = isins[-1][:12] if isins else None
    for source, raw in sorted(by_source.items()):
        bars, dupes = _dedupe(raw, source)
        written = await write_candles_bulk(session, bars, source, tenant_id=tenant_id, ingested_at=now)
        out.inserted += written.inserted
        out.corrected += written.corrected
        out.skipped += written.skipped
        await session.flush()
        first, last = _utc(bars[0].ts), _utc(bars[-1].ts)
        in_lake = await _count_in_lake(session, mf, source, first, last, tenant_id)
        if in_lake < len(bars):
            raise RuntimeError(f"verification failed: the lake holds {in_lake} of the file's {len(bars)} bars ({source})")
        if in_lake > len(bars):
            out.notes.append(f"{in_lake - len(bars)} bar(s) of {source} in the lake are not in this file (an earlier upload?)")
        out.rows_in_lake += in_lake
        out.first_ts = first if out.first_ts is None else min(out.first_ts, first)
        out.last_ts = last if out.last_ts is None else max(out.last_ts, last)
        out.quality_events += await _quality(session, mf, source, bars, dupes, tenant_id, out.notes)
    return out


# --- the run ------------------------------------------------------------------------------------------------------
async def _set_state(session: AsyncSession, state_id: int, **values: Any) -> None:
    await session.execute(update(HistoryImportFileRecord).where(HistoryImportFileRecord.id == state_id)
                          .values(updated_at=datetime.now(timezone.utc), **values))
    await session.commit()


async def _state_for(session: AsyncSession, mf: ManifestFile, tenant_id: Optional[int],
                     state: Optional[HistoryImportFileRecord]) -> HistoryImportFileRecord:
    if state is None:
        state = HistoryImportFileRecord(object_key=mf.key, status="pending", attempts=0, inserted=0, corrected=0, skipped=0,
                                        quality_events=0)
        session.add(state)
    state.manifest_key, state.dataset, state.exchange, state.symbol, state.period = (
        mf.manifest_key, mf.dataset, mf.exchange, mf.symbol[:40], mf.period)
    state.tenant_id = tenant_id if mf.dataset == "m1" else None
    state.sha256, state.rows_expected = mf.sha256, mf.rows
    return state


async def _refuse_listed(session_factory: Callable[[], Any], manifest_key: str, key: str, reason: str) -> None:
    """A manifest entry refused before download: recorded so the progress page shows it."""
    match = next((m for m in (LAYOUT[d].match(key) for d in DATASETS) if m), None)
    async with session_factory() as session:
        state = await session.scalar(select(HistoryImportFileRecord).where(HistoryImportFileRecord.object_key == key[:512]))
        if state is None:
            state = HistoryImportFileRecord(object_key=key[:512], sha256="", rows_expected=0, attempts=0, inserted=0,
                                            corrected=0, skipped=0, quality_events=0)
            session.add(state)
        if state.status == "done":           # its bars are in the lake: keep it done, record why the manifest refuses it now
            state.error = f"the manifest now refuses this entry: {reason}"[:2000]
            await session.commit()
            return
        state.manifest_key = manifest_key
        state.dataset = key.split("/", 1)[0][:8]
        state.exchange = (match.group("exchange").upper() if match else "?")[:20]
        state.symbol = (match.group("symbol") if match else "?")[:40]
        state.period = (match.group("period") if match else "?")[:7]
        state.status, state.error = "refused", reason[:2000]
        await session.commit()


async def import_one(session_factory: Callable[[], Any], store: ObjectStore, mf: ManifestFile, *, tenant_id: Optional[int],
                     summary: ImportSummary, now: Optional[datetime] = None) -> str:
    """Imports one manifest entry; returns its final status ('skipped' when it was already done)."""
    async with session_factory() as session:
        existing = await session.scalar(select(HistoryImportFileRecord).where(HistoryImportFileRecord.object_key == mf.key))
        target = tenant_id if mf.dataset == "m1" else None
        if existing is not None and existing.status == "done" and existing.sha256 == mf.sha256 and existing.tenant_id == target:
            summary.skipped += 1          # already loaded into the same store, and not rewritten since
            return "skipped"
        state = await _state_for(session, mf, tenant_id, existing)
        state.status, state.error = "running", None
        state.started_at = datetime.now(timezone.utc)
        state.attempts = (state.attempts or 0) + 1
        await session.commit()
        state_id = state.id
    summary.files += 1
    async with session_factory() as session:
        try:
            obj = await asyncio.to_thread(store.get, mf.key)
            out = await load_file(session, mf, obj.data, tenant_id=tenant_id if mf.dataset == "m1" else None,
                                  now=now or datetime.now(timezone.utc))
            await session.execute(update(HistoryImportFileRecord).where(HistoryImportFileRecord.id == state_id).values(
                status="done", error=None, etag=obj.etag, size_bytes=len(obj.data), rows_read=out.rows_read,
                rows_in_lake=out.rows_in_lake, inserted=out.inserted, corrected=out.corrected, skipped=out.skipped,
                quality_events=out.quality_events, quality_note="; ".join(out.notes)[:2000] or None, isin=out.isin,
                first_ts=out.first_ts, last_ts=out.last_ts, imported_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc)))
            await session.commit()
        except ImportRefused as exc:
            await session.rollback()
            await _set_state(session, state_id, status="refused", error=str(exc)[:2000])
            summary.refused += 1
            summary.errors.append(f"{mf.key}: refused - {exc}")
            return "refused"
        except ImportConfigError:
            await session.rollback()
            await _set_state(session, state_id, status="pending", error="owner tenant not configured")
            raise
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the run; it stays retryable
            await session.rollback()
            logger.exception("HB0 import of %s failed", mf.key)
            await _set_state(session, state_id, status="failed", error=f"{type(exc).__name__}: {exc}"[:2000])
            summary.failed += 1
            summary.errors.append(f"{mf.key}: failed - {exc}")
            return "failed"
    summary.done += 1
    summary.rows += out.rows_read
    summary.inserted += out.inserted
    summary.corrected += out.corrected
    summary.quality_events += out.quality_events
    return "done"


def _list(store: ObjectStore, prefix: str) -> List[str]:
    return list(store.list_keys(prefix))


async def run_import(session_factory: Callable[[], Any], store: ObjectStore, *, datasets: Sequence[str],
                     owner_tenant_id: Optional[int] = None, symbols: Optional[Sequence[str]] = None,
                     limit: Optional[int] = None, now: Optional[datetime] = None,
                     owner_tenant_name: Optional[str] = None) -> ImportSummary:
    """Reads every manifest of the requested datasets and imports what is not done yet. See the module docstring."""
    from app.platform.controls import flag_enabled

    unknown = [d for d in datasets if d not in DATASETS]
    if unknown or not datasets:
        raise ImportConfigError(f"Unknown dataset(s) {unknown or '(none)'}; choose from {', '.join(DATASETS)}")
    async with session_factory() as session:
        if not await flag_enabled(session, FLAG):
            raise ImportConfigError(f"The '{FLAG}' feature flag is off - turn it on in Admin > Controls to run the import")
        if "m1" in datasets:
            if owner_tenant_id is None:
                raise ImportConfigError("HISTORY_OWNER_TENANT_ID is not set: refusing to load 1-minute data (owner-licensed)")
            owner = await session.get(Tenant, owner_tenant_id)
            if owner is None:
                raise ImportConfigError(f"HISTORY_OWNER_TENANT_ID={owner_tenant_id} is not a tenant of this database")
            expected = (owner_tenant_name if owner_tenant_name is not None else config.HISTORY_OWNER_TENANT_NAME or "").strip()
            if not expected or expected != (owner.name or "").strip():
                raise ImportConfigError(f"HISTORY_OWNER_TENANT_NAME must equal the name of tenant {owner_tenant_id} - a "
                                        "confirmation that the id is the owner's (refusing to load licensed 1-minute data)")
    wanted = {s.upper() for s in symbols} if symbols else None
    summary = ImportSummary()
    async with session_factory() as lock_session:
        if not await _lock(lock_session, True):
            raise ImportConfigError("Another history import is running on this database - one at a time")
        try:
            await _run_manifests(session_factory, store, datasets, owner_tenant_id, wanted, limit, now, summary)
        finally:
            await _lock(lock_session, False)
    return summary


async def _lock(session: AsyncSession, take: bool) -> bool:
    """One import at a time (Postgres session advisory lock, held by a connection kept for the whole run)."""
    if session.bind.dialect.name != "postgresql":
        return True
    fn = "pg_try_advisory_lock" if take else "pg_advisory_unlock"
    return bool(await session.scalar(text(f"SELECT {fn}(:key)"), {"key": LOCK_KEY}))


async def _run_manifests(session_factory: Callable[[], Any], store: ObjectStore, datasets: Sequence[str],
                         owner_tenant_id: Optional[int], wanted: Optional[set], limit: Optional[int],
                         now: Optional[datetime], summary: ImportSummary) -> None:
    attempted = 0
    for dataset in datasets:
        listed: List[str] = await asyncio.to_thread(_list, store, f"{dataset}/")
        manifest_keys = sorted(k for k in listed if k.rsplit("/", 1)[-1] == MANIFEST)
        for manifest_key in manifest_keys:
            summary.manifests += 1
            try:
                files, refusals = parse_manifest(manifest_key, (await asyncio.to_thread(store.get, manifest_key)).data)
            except ImportRefused as exc:
                summary.errors.append(f"{manifest_key}: {exc}")
                continue
            for key, reason in refusals:
                if key == manifest_key:
                    summary.errors.append(f"{key}: {reason}")
                elif wanted is None or any(f"/{s}/" in key.upper() for s in wanted):
                    await _refuse_listed(session_factory, manifest_key, key, reason)
                    summary.refused += 1
                    summary.errors.append(f"{key}: refused - {reason}")
            for mf in sorted(files, key=lambda f: f.key):
                if mf.dataset != dataset or (wanted is not None and mf.symbol.upper() not in wanted):
                    continue
                if limit is not None and attempted >= limit:
                    return
                status = await import_one(session_factory, store, mf, tenant_id=owner_tenant_id, summary=summary, now=now)
                if status != "skipped":
                    attempted += 1


async def progress(session: AsyncSession, *, dataset: Optional[str] = None, limit: int = 500) -> Dict[str, Any]:
    """Per dataset: files by status; per symbol: done / pending / refused / failed and the last error - for the admin.
    Aggregated in SQL (the 1-minute store alone is ~60 files per symbol)."""
    f = HistoryImportFileRecord
    scope = [f.dataset == dataset] if dataset else []
    totals: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    per_symbol: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    grouped = await session.execute(
        select(f.dataset, f.exchange, f.symbol, f.status, func.count(), func.sum(f.rows_in_lake), func.sum(f.quality_events),
               func.min(f.first_ts), func.max(f.last_ts), func.max(f.imported_at))
        .where(*scope).group_by(f.dataset, f.exchange, f.symbol, f.status))
    for ds, exchange, symbol, raw_status, n, rows, events, first, last, imported in grouped:
        status = "pending" if raw_status in ("pending", "running") else raw_status
        totals[ds][status] += int(n)
        entry = per_symbol.setdefault((ds, exchange, symbol), {
            "dataset": ds, "exchange": exchange, "symbol": symbol, "done": 0, "pending": 0, "refused": 0, "failed": 0,
            "rows_in_lake": 0, "quality_events": 0, "first_ts": None, "last_ts": None, "last_error": None, "last_imported_at": None})
        entry[status] = entry.get(status, 0) + int(n)
        entry["rows_in_lake"] += int(rows or 0)
        entry["quality_events"] += int(events or 0)
        for name, value, pick in (("first_ts", first, min), ("last_ts", last, max), ("last_imported_at", imported, max)):
            if value is not None:
                iso = _utc(value).isoformat()
                entry[name] = iso if entry[name] is None else pick(entry[name], iso)
    errors = await session.execute(select(f.dataset, f.exchange, f.symbol, f.period, f.error).where(
        *scope, f.status.in_(("refused", "failed")), f.error.is_not(None)).order_by(f.updated_at))
    for ds, exchange, symbol, period, error in errors:
        if (ds, exchange, symbol) in per_symbol:
            per_symbol[(ds, exchange, symbol)]["last_error"] = f"{period}: {error}"[:500]
    symbols = sorted(per_symbol.values(), key=lambda e: (-(e["refused"] + e["failed"]), -e["pending"], e["dataset"], e["symbol"]))
    return {"datasets": {k: dict(v) for k, v in totals.items()}, "symbols": symbols[:max(1, limit)],
            "symbol_count": len(symbols)}


async def purge_tenant_m1(session: AsyncSession, tenant_id: int, *, confirm_name: str) -> Dict[str, int]:
    """Removes what the importer put in ONE tenant's private store: its 1-minute rows from the R2 store (`r2:*`), the
    quality events about them and the m1 state rows (so a corrected run imports them into the right tenant). The name
    must match the tenant's - the same confirmation the import asks for."""
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None or not confirm_name.strip() or confirm_name.strip() != (tenant.name or "").strip():
        raise ImportConfigError(f"--confirm-name must equal the name of tenant {tenant_id}")
    bars = await session.execute(delete(MdTenantCandleRecord).where(
        MdTenantCandleRecord.tenant_id == tenant_id, MdTenantCandleRecord.timeframe == TIMEFRAME["m1"],
        MdTenantCandleRecord.source.like("r2:%")))
    events = await session.execute(delete(DataQualityEventRecord).where(
        DataQualityEventRecord.tenant_id == tenant_id, DataQualityEventRecord.source.like("r2:%")))
    states = await session.execute(delete(HistoryImportFileRecord).where(
        HistoryImportFileRecord.tenant_id == tenant_id, HistoryImportFileRecord.dataset == "m1"))
    await session.commit()
    return {"bars": int(bars.rowcount or 0), "quality_events": int(events.rowcount or 0), "state_rows": int(states.rowcount or 0)}


# --- CLI ----------------------------------------------------------------------------------------------------------
def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m app.market_lake.r2_import", description="HB0: import the R2 history store")
    p.add_argument("--dataset", choices=("daily", "m1", "all"))
    p.add_argument("--purge-tenant", type=int, default=None, help="remove this tenant's imported 1-minute rows and state")
    p.add_argument("--confirm-name", default="", help="the tenant's name, for --purge-tenant")
    p.add_argument("--symbols", default="", help="comma-separated symbols (default: every symbol in the manifests)")
    p.add_argument("--limit", type=int, default=None, help="import at most this many files this run (resume later)")
    p.add_argument("--local-dir", default=None, help="read the layout from a directory instead of R2")
    return p


async def _main(argv: Optional[Sequence[str]] = None) -> int:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    args = _parser().parse_args(argv)
    if args.purge_tenant is not None:
        engine = create_async_engine(config.DATABASE_URL)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                print(json.dumps(await purge_tenant_m1(session, args.purge_tenant, confirm_name=args.confirm_name), indent=2))
            return 0
        except ImportConfigError as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 2
        finally:
            await engine.dispose()
    if args.dataset is None:
        print("REFUSED: --dataset is required (or --purge-tenant)", file=sys.stderr)
        return 2
    datasets = list(DATASETS) if args.dataset == "all" else [args.dataset]
    try:
        store: ObjectStore = LocalDirStore(args.local_dir) if args.local_dir else R2Store.from_env()
    except RuntimeError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    engine = create_async_engine(config.DATABASE_URL)
    try:
        summary = await run_import(async_sessionmaker(engine, expire_on_commit=False), store, datasets=datasets,
                                   owner_tenant_id=config.HISTORY_OWNER_TENANT_ID,
                                   symbols=[s.strip() for s in args.symbols.split(",") if s.strip()] or None, limit=args.limit)
    except ImportConfigError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    finally:
        await engine.dispose()
    print(json.dumps(summary.as_dict(), indent=2))
    return 1 if summary.refused or summary.failed else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(asyncio.run(_main()))
