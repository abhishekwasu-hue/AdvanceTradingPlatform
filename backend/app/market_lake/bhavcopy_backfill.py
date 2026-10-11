"""HB1: the daily NSE backfill (10 years of bhavcopies) and the nightly EOD job - into the SHARED lake (Tier C: every
user may read daily bars). Design note: docs/design/HB_HISTORY_BACKFILL.md.

What a day is: three files per trade date - `cm` (cash bhavcopy: every NSE equity that traded, delisted and suspended
names included, so history is survivorship-free), `index` (all-indices closes) and `fo` (F&O bhavcopy: futures, and
options only when asked). Parsing is `app.market_lake.bhavcopy` (legacy, full and UDiFF formats); bars are labelled
by their END, 15:30 IST on the trade date, timeframe `day`, sources `nse_bhav:cm|index|fo`, written with the B1 rules
(identical skipped, changed -> next version). Prices are stored UNADJUSTED; the adjusted view is B4 at read time.

Rules:
- Nothing runs unless the `history_backfill` flag is on - the CLI, the nightly job and the admin uploads all refuse.
- Polite fetching (`nse_fetch.PoliteFetcher`): one request at a time, paced, backoff on 429/403/5xx, 404 = no file.
- Idempotent and resumable: one `history_backfill_files` row per (segment, day); a file is written and marked `done`
  in one transaction, so an interrupted run leaves at most one `running` row with nothing written, retried next run.
  `done` days are never fetched again; `blocked` / `refused` / `failed` ones are retried; `no_file` ones are re-checked
  while recent (it may be unpublished yet) or with `--retry-missing`.
- Checksum per file: the SHA-256 of the bytes as received is stored; the raw file can be kept (HISTORY_BACKFILL_ARCHIVE_DIR).
- Quality (B3): identical repeats -> `duplicate`, rows without a price -> `invalid` (left out, never filled), spikes and
  impossible OHLC against the previous day, a short file is refused (a truncated download must not look like a quiet
  day), and a trading day with no file -> `gap` (only where the holiday calendar for that year is known; otherwise
  "gap check skipped" is noted). `gaps` (CLI) reports per-instrument gaps on days whose file we hold.
- A blocked file is uploaded by hand (Admin > History backfill, or `--local-dir`); it goes through the same checks.
- One run at a time per database (Postgres advisory lock).

CLI (inside the backend container; never run by the test suite against NSE):
    python -m app.market_lake.bhavcopy_backfill run --from 2016-01-01 --to 2025-12-31 [--segments cm,index,fo]
        [--with-options] [--include-weekends] [--limit N] [--retry-missing] [--local-dir DIR]
    python -m app.market_lake.bhavcopy_backfill nightly
    python -m app.market_lake.bhavcopy_backfill holidays FILE.csv [--exchange NSE]
    python -m app.market_lake.bhavcopy_backfill corporate-actions FILE.csv
    python -m app.market_lake.bhavcopy_backfill gaps --from 2016-01-01 --to 2025-12-31 [--symbols A,B]
"""
import argparse
import asyncio
import hashlib
import json
import logging
import statistics
import sys
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import distinct, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.db.models import HistoryBackfillFileRecord, MdCandleRecord, MdIsinSymbolRecord
from app.market_data.calendar import IST, load_holidays
from app.market_lake import bhavcopy, quality
from app.market_lake.bhavcopy import BhavcopyRefused, DailyRow, ParsedFile
from app.market_lake.ingest import LakeBar, write_candles_bulk
from app.market_lake.nse_fetch import Fetcher, PoliteFetcher

logger = logging.getLogger(__name__)

FLAG = "history_backfill"
SEGMENTS = bhavcopy.SEGMENTS                      # processing order within a day: cash, indices, F&O
SOURCE = {"cm": "nse_bhav:cm", "index": "nse_bhav:index", "fo": "nse_bhav:fo"}
TIMEFRAME = "day"
CLOSE_IST = time(15, 30)
LOCK_KEY = 0x48420002                             # pg advisory lock id ("HB" 0002); the R2 importer uses 0001
RECHECK_NO_FILE_DAYS = 3                          # a `no_file` this recent is asked again (NSE may publish late)
GATED_SEGMENTS = ("cm", "index")                  # the short-file gate (F&O size depends on --with-options)
F = HistoryBackfillFileRecord


class BackfillConfigError(RuntimeError):
    """The run cannot start (flag off, another run holds the lock, bad arguments)."""


def bar_end(day: date) -> datetime:
    """A daily bar's END: the 15:30 IST close of its trade date, in UTC (the date stays the trade date in UTC and IST)."""
    return datetime.combine(day, CLOSE_IST, tzinfo=IST).astimezone(timezone.utc)


@dataclass
class Obtained:
    status: str                       # ok | not_found | blocked | error
    content: bytes = b""
    origin: str = "nse_archives"      # nse_archives | local_dir | upload
    url: Optional[str] = None
    file_name: Optional[str] = None
    http_status: Optional[int] = None
    detail: str = ""


@dataclass
class Outcome:
    file_format: str = ""
    rows_read: int = 0
    rows_loaded: int = 0
    inserted: int = 0
    corrected: int = 0
    skipped: int = 0
    quality_events: int = 0
    notes: List[str] = field(default_factory=list)


@dataclass
class BackfillSummary:
    days: int = 0
    files: int = 0
    done: int = 0
    skipped: int = 0
    no_file: int = 0
    blocked: int = 0
    refused: int = 0
    failed: int = 0
    rows: int = 0
    inserted: int = 0
    corrected: int = 0
    quality_events: int = 0
    gaps: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        out["errors"] = self.errors[:200]
        return out


# --- one file into the lake ---------------------------------------------------------------------------------------
async def _short_file_gate(session: AsyncSession, segment: str, day: date, rows: int) -> None:
    ratio = config.HISTORY_BACKFILL_MIN_ROWS_RATIO
    if segment not in GATED_SEGMENTS or ratio <= 0:
        return
    recent = [int(n) for n in await session.scalars(select(F.rows_loaded).where(
        F.segment == segment, F.status == "done", F.trade_date < day, F.trade_date >= day - timedelta(days=30),
        F.rows_loaded.is_not(None)).order_by(F.trade_date.desc()).limit(5))]
    if len(recent) >= 3:
        median = statistics.median(recent)
        if rows < ratio * median:
            raise BhavcopyRefused(f"short file: {rows} rows against a median of {median:g} on the last {len(recent)} days "
                                  f"(minimum ratio {ratio:g}) - a truncated download? Upload it with 'force' if it is right")


async def _previous_bars(session: AsyncSession, keys: Sequence[str], source: str, end: datetime) -> Dict[str, LakeBar]:
    """The latest earlier daily bar per instrument (within 20 days) - what a spike is measured against."""
    out: Dict[str, Tuple[datetime, int, LakeBar]] = {}
    for i in range(0, len(keys), 500):
        rows = await session.execute(select(MdCandleRecord).where(
            MdCandleRecord.instrument_key.in_(list(keys[i:i + 500])), MdCandleRecord.timeframe == TIMEFRAME,
            MdCandleRecord.source == source, MdCandleRecord.ts < end, MdCandleRecord.ts >= end - timedelta(days=20)))
        for (r,) in rows:
            ts = quality._utc(r.ts)
            have = out.get(r.instrument_key)
            if have is None or (ts, r.version) > (have[0], have[1]):
                out[r.instrument_key] = (ts, r.version, LakeBar(r.instrument_key, TIMEFRAME, ts, float(r.open), float(r.high),
                                                                float(r.low), float(r.close), int(r.volume), r.oi))
    return {k: v[2] for k, v in out.items()}


async def _quality(session: AsyncSession, parsed: ParsedFile, bars: List[LakeBar], source: str, end: datetime) -> int:
    events = [quality.QualityEvent("duplicate", key, TIMEFRAME, end, source, "the same row twice in the exchange's file")
              for key in sorted(set(parsed.duplicates))]
    events += [quality.QualityEvent("invalid", key, TIMEFRAME, end, source, "row without a usable price - left out, not filled")
               for key in sorted(set(parsed.invalid))]
    series = {r.instrument_key: r.series for r in parsed.rows}
    previous = await _previous_bars(session, [b.instrument_key for b in bars if series.get(b.instrument_key) not in ("CE", "PE")],
                                    source, end)
    for bar in bars:
        before = previous.get(bar.instrument_key)        # options: no spike check (moves of 10%+ are ordinary there)
        events += [e for e in quality.find_bar_problems([before, bar] if before else [bar], source) if e.ts == end]
    await quality.record(session, events, commit=False)
    return len(events)


async def _observe_isins(session: AsyncSession, rows: Sequence[DailyRow], day: date) -> None:
    pairs: Dict[Tuple[str, str], str] = {(r.isin, r.symbol[:40]): r.series for r in rows if r.isin}
    isins = sorted({isin for isin, _ in pairs})
    known: Dict[Tuple[str, str], MdIsinSymbolRecord] = {}
    for i in range(0, len(isins), 500):
        for rec in await session.scalars(select(MdIsinSymbolRecord).where(MdIsinSymbolRecord.exchange == "NSE",
                                                                         MdIsinSymbolRecord.isin.in_(isins[i:i + 500]))):
            known[(rec.isin, rec.symbol)] = rec
    for (isin, symbol), series in sorted(pairs.items()):
        rec = known.get((isin, symbol))
        if rec is None:
            session.add(MdIsinSymbolRecord(exchange="NSE", isin=isin, symbol=symbol, series=series[:4], first_date=day, last_date=day))
            continue
        if day < rec.first_date:
            rec.first_date = day
        if day >= rec.last_date:
            rec.last_date, rec.series = day, series[:4]


async def load_file(session: AsyncSession, segment: str, day: date, content: bytes, *, sha256: str, now: datetime,
                    with_options: Optional[bool] = None, force: bool = False) -> Outcome:
    """Checks, writes and verifies one day's file. Does not commit (the caller commits it with the file's state row)."""
    parsed, closes = bhavcopy.parse(segment, content, series=config.HISTORY_BACKFILL_SERIES,
                                    with_options=config.HISTORY_BACKFILL_FO_OPTIONS if with_options is None else with_options)
    if parsed.trade_date != day:
        raise BhavcopyRefused(f"the file is for {parsed.trade_date.isoformat()}, not {day.isoformat()}")
    if not parsed.rows:
        raise BhavcopyRefused(f"no rows to load (left out: {parsed.skipped or 'none'})")
    if not force:
        await _short_file_gate(session, segment, day, len(parsed.rows))
    end, source = bar_end(day), SOURCE[segment]
    bars = [LakeBar(r.instrument_key, TIMEFRAME, end, r.open, r.high, r.low, r.close, r.volume, r.oi) for r in parsed.rows]
    written = await write_candles_bulk(session, bars, source, ingested_at=now)
    await session.flush()
    in_lake = int(await session.scalar(select(func.count(distinct(MdCandleRecord.instrument_key))).where(
        MdCandleRecord.timeframe == TIMEFRAME, MdCandleRecord.source == source, MdCandleRecord.ts == end)) or 0)
    if in_lake < len(bars):
        raise RuntimeError(f"verification failed: the lake holds {in_lake} of the file's {len(bars)} instruments for {day}")
    out = Outcome(parsed.file_format, parsed.rows_read, len(bars), written.inserted, written.corrected, written.skipped)
    out.notes += parsed.notes[:20]
    if parsed.skipped:
        out.notes.append("left out: " + ", ".join(f"{k} {v}" for k, v in sorted(parsed.skipped.items())))
    if in_lake > len(bars):
        out.notes.append(f"{in_lake - len(bars)} instrument(s) in the lake for this day are not in this file (an earlier version?)")
    if segment == "cm":
        await _observe_isins(session, parsed.rows, day)
    if segment == "index" and closes:
        from app.universe.fo_eod import FoReport, apply_index_closes
        await apply_index_closes(session, closes, source, sha256, FoReport())      # U1-c index_eod, with P/E, P/B, yield
        await session.flush()
    out.quality_events = await _quality(session, parsed, bars, source, end)
    return out


# --- getting the bytes ----------------------------------------------------------------------------------------------
ObtainFn = Callable[[str, date], Awaitable[Obtained]]


def archive_obtainer(fetcher: Fetcher) -> ObtainFn:
    async def obtain(segment: str, day: date) -> Obtained:
        seen: List[Obtained] = []
        for url, name in bhavcopy.candidate_files(segment, day):
            got = await fetcher.get(url)
            if got.status == "ok":
                return Obtained("ok", got.content, "nse_archives", url, name, 200)
            seen.append(Obtained(got.status, b"", "nse_archives", url, name, got.http_status, got.detail))
        for status in ("blocked", "error"):
            hit = next((o for o in seen if o.status == status), None)
            if hit is not None:
                return hit
        return Obtained("not_found", b"", "nse_archives", seen[0].url if seen else None, None, 404,
                        "no file under any of the archive's names")
    return obtain


def local_obtainer(root: str) -> ObtainFn:
    base = Path(root).resolve()

    async def obtain(segment: str, day: date) -> Obtained:
        for name in bhavcopy.local_names(segment, day):
            for folder in (base, base / segment, base / segment / str(day.year)):
                path = (folder / name).resolve()
                if base in path.parents and path.is_file():
                    return Obtained("ok", path.read_bytes(), "local_dir", None, name)
        return Obtained("not_found", origin="local_dir", detail=f"no file for {day} in {base}")
    return obtain


def _keep_raw(segment: str, day: date, name: Optional[str], content: bytes) -> None:
    folder = config.HISTORY_BACKFILL_ARCHIVE_DIR
    if not folder or not name:
        return
    path = Path(folder) / segment / str(day.year) / Path(name).name
    try:
        if not path.exists() or path.read_bytes() != content:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    except OSError:
        logger.warning("Could not keep %s in %s", name, folder, exc_info=True)


# --- state and one attempt ------------------------------------------------------------------------------------------
async def _missing_day(session: AsyncSession, segment: str, day: date, today: date) -> Tuple[str, bool]:
    """(note, is a gap) for a day the archive has no file for. Never decides "holiday" without the calendar."""
    if day.weekday() >= 5:
        return "weekend - no regular session", False
    holidays = await load_holidays(session, "NSE", day.year)
    if not holidays:
        return f"no NSE holiday calendar for {day.year}: a holiday cannot be told from a gap (gap check skipped)", False
    if day in holidays:
        return "exchange holiday", False
    if day >= today:
        return "not published yet - asked again on the next run", False
    return "no file for a trading day (the holiday calendar says the market was open) - reported as a gap", True


async def _set(session_factory, state_id: int, **values: Any) -> None:
    async with session_factory() as session:
        await session.execute(update(F).where(F.id == state_id).values(updated_at=datetime.now(timezone.utc), **values))
        await session.commit()


async def _start(session_factory, segment: str, day: date, *, skip_done: bool, retry_no_file: bool,
                 today: date) -> Tuple[Optional[int], Optional[str]]:
    """Claims (segment, day) as `running` -> (state id, the status it had); (None, ...) when it should be skipped
    (already done, or a settled `no_file`)."""
    async with session_factory() as session:
        state = await session.scalar(select(F).where(F.segment == segment, F.trade_date == day))
        previous = state.status if state is not None else None
        if state is not None and skip_done:
            if state.status == "done":
                return None, previous
            if state.status == "no_file" and not retry_no_file and day < today - timedelta(days=RECHECK_NO_FILE_DAYS):
                return None, previous
        if state is None:
            state = F(segment=segment, trade_date=day, status="pending", attempts=0, inserted=0, corrected=0, skipped=0,
                      quality_events=0)
            session.add(state)
        state.status, state.error = "running", None
        state.started_at = datetime.now(timezone.utc)
        state.attempts = (state.attempts or 0) + 1
        await session.commit()
        return state.id, previous


async def attempt(session_factory, segment: str, day: date, obtain: ObtainFn, summary: BackfillSummary, *,
                  today: date, now: Optional[datetime] = None, skip_done: bool = True, retry_no_file: bool = False,
                  with_options: Optional[bool] = None, force: bool = False) -> str:
    """One (segment, day): obtain the file, load it, record the outcome. Returns the final status, or 'skipped'."""
    state_id, previous = await _start(session_factory, segment, day, skip_done=skip_done, retry_no_file=retry_no_file, today=today)
    if state_id is None:
        summary.skipped += 1
        return "skipped"
    summary.files += 1
    try:
        got = await obtain(segment, day)
    except Exception as exc:  # noqa: BLE001 - a broken source fails this file, not the run
        logger.exception("HB1 fetch of %s %s failed", segment, day)
        got = Obtained("error", detail=f"{type(exc).__name__}: {exc}")
    common: Dict[str, Any] = dict(origin=got.origin, url=got.url[:400] if got.url else None,
                                  file_name=got.file_name[:200] if got.file_name else None, http_status=got.http_status)
    if got.status == "not_found":
        async with session_factory() as session:
            note, is_gap = await _missing_day(session, segment, day, today)
            if is_gap:
                await quality.record(session, [quality.QualityEvent(
                    "gap", f"NSE_BHAV:{segment.upper()}", TIMEFRAME, bar_end(day), SOURCE[segment],
                    f"no {segment} file for trading day {day.isoformat()}")], commit=True)
                summary.gaps.append(f"{segment} {day.isoformat()}")
        await _set(session_factory, state_id, status="no_file", note=note[:2000], error=None, **common)
        summary.no_file += 1
        return "no_file"
    if got.status in ("blocked", "error"):
        why = (f"the archive refused ({got.detail}) - upload the file in Admin > History backfill or use --local-dir"
               if got.status == "blocked" else got.detail or "fetch failed")
        await _set(session_factory, state_id, status="blocked" if got.status == "blocked" else "failed", error=why[:2000], **common)
        summary.blocked += got.status == "blocked"
        summary.failed += got.status == "error"
        summary.errors.append(f"{segment} {day}: {got.status} - {why}"[:500])
        return "blocked" if got.status == "blocked" else "failed"
    sha = hashlib.sha256(got.content).hexdigest()
    if got.origin == "nse_archives":
        _keep_raw(segment, day, got.file_name, got.content)
    async with session_factory() as session:
        try:
            out = await load_file(session, segment, day, got.content, sha256=sha, now=now or datetime.now(timezone.utc),
                                  with_options=with_options, force=force)
            await session.execute(update(F).where(F.id == state_id).values(
                status="done", error=None, sha256=sha, size_bytes=len(got.content), file_format=out.file_format,
                rows_read=out.rows_read, rows_loaded=out.rows_loaded, inserted=out.inserted, corrected=out.corrected,
                skipped=out.skipped, quality_events=out.quality_events, note="; ".join(out.notes)[:2000] or None,
                done_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc), **common))
            await session.commit()
        except BhavcopyRefused as exc:
            await session.rollback()
            if previous == "done":       # a refused re-upload leaves the day's loaded file as it was
                await _set(session_factory, state_id, status="done", error=f"a later file was refused: {exc}"[:2000])
            else:
                await _set(session_factory, state_id, status="refused", error=str(exc)[:2000], sha256=sha,
                           size_bytes=len(got.content), **common)
            summary.refused += 1
            summary.errors.append(f"{segment} {day}: refused - {exc}"[:500])
            return "refused"
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the run; it stays retryable
            await session.rollback()
            logger.exception("HB1 load of %s %s failed", segment, day)
            why = f"{type(exc).__name__}: {exc}"
            if previous == "done":
                await _set(session_factory, state_id, status="done", error=f"a later file failed: {why}"[:2000])
            else:
                await _set(session_factory, state_id, status="failed", error=why[:2000], sha256=sha, **common)
            summary.failed += 1
            summary.errors.append(f"{segment} {day}: failed - {exc}"[:500])
            return "failed"
    summary.done += 1
    summary.rows += out.rows_loaded
    summary.inserted += out.inserted
    summary.corrected += out.corrected
    summary.quality_events += out.quality_events
    return "done"


# --- the runs -------------------------------------------------------------------------------------------------------
async def _require_flag(session_factory) -> None:
    from app.platform.controls import flag_enabled

    async with session_factory() as session:
        if not await flag_enabled(session, FLAG):
            raise BackfillConfigError(f"The '{FLAG}' feature flag is off - turn it on in Admin > Controls first")


async def _lock(session: AsyncSession, take: bool) -> bool:
    if session.bind.dialect.name != "postgresql":
        return True
    fn = "pg_try_advisory_lock" if take else "pg_advisory_unlock"
    return bool(await session.scalar(text(f"SELECT {fn}(:key)"), {"key": LOCK_KEY}))


def _segments(raw: Sequence[str]) -> List[str]:
    unknown = [s for s in raw if s not in SEGMENTS]
    if unknown or not raw:
        raise BackfillConfigError(f"Unknown segment(s) {unknown or '(none)'}; choose from {', '.join(SEGMENTS)}")
    return [s for s in SEGMENTS if s in raw]


async def run_backfill(session_factory, *, start: date, end: date, segments: Sequence[str] = SEGMENTS,
                       fetcher: Optional[Fetcher] = None, local_dir: Optional[str] = None, include_weekends: bool = False,
                       limit: Optional[int] = None, retry_no_file: bool = False, with_options: Optional[bool] = None,
                       today: Optional[date] = None, now: Optional[datetime] = None) -> BackfillSummary:
    """Every (segment, day) from `start` to `end` that is not done yet, oldest first. See the module docstring."""
    wanted = _segments(segments)
    if end < start:
        raise BackfillConfigError("--to is before --from")
    if (fetcher is None) == (local_dir is None):
        raise BackfillConfigError("give exactly one source: the archive fetcher or a local directory")
    await _require_flag(session_factory)
    obtain = local_obtainer(local_dir) if local_dir else archive_obtainer(fetcher)  # type: ignore[arg-type]  # one is set
    today = today or datetime.now(IST).date()
    summary = BackfillSummary()
    async with session_factory() as lock_session:
        if not await _lock(lock_session, True):
            raise BackfillConfigError("Another history backfill is running on this database - one at a time")
        try:
            attempted, day = 0, start
            while day <= end:
                if include_weekends or day.weekday() < 5:
                    summary.days += 1
                    for segment in wanted:
                        if limit is not None and attempted >= limit:
                            return summary
                        status = await attempt(session_factory, segment, day, obtain, summary, today=today, now=now,
                                               retry_no_file=retry_no_file, with_options=with_options)
                        attempted += status != "skipped"
                day += timedelta(days=1)
        finally:
            await _lock(lock_session, False)
    return summary


def nightly_due(now: datetime, last_day: Optional[date]) -> Optional[date]:
    """The IST date to run the nightly EOD job for, or None (before HISTORY_BACKFILL_HOUR_IST, or already run today)."""
    ist = now.astimezone(IST)
    if ist.hour < config.HISTORY_BACKFILL_HOUR_IST or last_day == ist.date():
        return None
    return ist.date()


async def run_nightly(session_factory, fetcher: Fetcher, *, now: Optional[datetime] = None) -> BackfillSummary:
    """After the close: today's files plus a catch-up of the last HISTORY_BACKFILL_CATCHUP_DAYS (a late or blocked file is
    picked up the next night). Weekends are asked too - a special session (budget day, Muhurat) has files."""
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(IST).date()
    return await run_backfill(session_factory, start=today - timedelta(days=max(0, config.HISTORY_BACKFILL_CATCHUP_DAYS)),
                              end=today, fetcher=fetcher, include_weekends=True, today=today, now=now)


async def ingest_upload(session_factory, segment: str, day: date, content: bytes, file_name: str, *, force: bool = False,
                        now: Optional[datetime] = None) -> Dict[str, Any]:
    """The admin's manual upload for one (segment, day) - a file the archive blocked, or a correction. Same checks as
    the job; a day already done is loaded again (identical bars skipped, changed ones become new versions)."""
    _segments([segment])
    await _require_flag(session_factory)
    summary = BackfillSummary()

    async def obtain(_segment: str, _day: date) -> Obtained:
        return Obtained("ok", content, "upload", None, file_name[:200] or None)

    status = await attempt(session_factory, segment, day, obtain, summary, today=datetime.now(IST).date(), now=now,
                           skip_done=False, force=force)
    async with session_factory() as session:
        state = await session.scalar(select(F).where(F.segment == segment, F.trade_date == day))
    return {"segment": segment, "trade_date": day.isoformat(), **_state_dict(state), "status": status,
            "day_status": state.status if state is not None else None}


# --- reporting ------------------------------------------------------------------------------------------------------
def _state_dict(s: Optional[HistoryBackfillFileRecord]) -> Dict[str, Any]:
    if s is None:
        return {}
    return {"status": s.status, "origin": s.origin, "file_name": s.file_name, "file_format": s.file_format, "sha256": s.sha256,
            "rows_read": s.rows_read, "rows_loaded": s.rows_loaded, "inserted": s.inserted, "corrected": s.corrected,
            "skipped": s.skipped, "quality_events": s.quality_events, "note": s.note, "error": s.error, "attempts": s.attempts}


async def progress(session: AsyncSession, *, limit: int = 200) -> Dict[str, Any]:
    """Per segment: files by status, first / last done day, rows loaded; then the files that need attention (blocked,
    refused, failed, or a missing file on a trading day) - newest first. Counts and names only, never bars."""
    segments: Dict[str, Dict[str, Any]] = {s: {"by_status": {}, "first_done": None, "last_done": None, "rows_loaded": 0} for s in SEGMENTS}
    for seg, status, n, first, last, rows in await session.execute(
            select(F.segment, F.status, func.count(), func.min(F.trade_date), func.max(F.trade_date), func.sum(F.rows_loaded))
            .group_by(F.segment, F.status)):
        entry = segments.setdefault(seg, {"by_status": {}, "first_done": None, "last_done": None, "rows_loaded": 0})
        entry["by_status"][status] = int(n)
        if status == "done":
            entry["first_done"], entry["last_done"] = first.isoformat() if first else None, last.isoformat() if last else None
            entry["rows_loaded"] = int(rows or 0)
    attention = await session.scalars(select(F).where(
        (F.status.in_(("blocked", "refused", "failed", "running"))) | ((F.status == "no_file") & F.note.like("%trading day%")))
        .order_by(F.trade_date.desc()).limit(max(1, limit)))
    return {"segments": segments,
            "attention": [{"segment": s.segment, "trade_date": s.trade_date.isoformat(), **_state_dict(s)} for s in attention]}


async def gap_report(session: AsyncSession, start: date, end: date, *, symbols: Optional[Sequence[str]] = None,
                     record: bool = True) -> Dict[str, Any]:
    """Per instrument (cash): trading days whose file we hold but that have no bar for it - it did not trade or was
    suspended that day. Reported as `gap` events, never filled. Years without a holiday calendar are skipped."""
    from app.market_lake.r2_import import _trading_days

    held = set(await session.scalars(select(F.trade_date).where(F.segment == "cm", F.status == "done",
                                                                F.trade_date >= start, F.trade_date <= end)))
    days, unknown = await _trading_days(session, "NSE", range(start.year, end.year + 1))
    trading = [d for d in days if start <= d <= end]
    missing_files = [d.isoformat() for d in trading if d not in held]
    usable = [d for d in trading if d in held]
    source = SOURCE["cm"]
    keys_q = select(MdCandleRecord.instrument_key).distinct().where(
        MdCandleRecord.timeframe == TIMEFRAME, MdCandleRecord.source == source,
        MdCandleRecord.ts >= bar_end(start), MdCandleRecord.ts <= bar_end(end))
    if symbols:
        keys_q = keys_q.where(MdCandleRecord.instrument_key.in_([f"NSE:{s.strip().upper()}" for s in symbols]))
    keys = sorted(await session.scalars(keys_q)) if usable else []      # no calendar-checked day held: nothing to check
    events: List[quality.QualityEvent] = []
    for key in keys:
        stamps = sorted({quality._utc(ts) for ts in await session.scalars(select(MdCandleRecord.ts).where(
            MdCandleRecord.instrument_key == key, MdCandleRecord.timeframe == TIMEFRAME, MdCandleRecord.source == source,
            MdCandleRecord.ts >= bar_end(start), MdCandleRecord.ts <= bar_end(end)))})
        bars = [LakeBar(key, TIMEFRAME, ts, 1, 1, 1, 1) for ts in stamps]
        events += [replace(e, detail=e.detail + " - not in the exchange's file (no trade or suspended)")
                   for e in quality.find_daily_gaps(bars, usable, source)]
    written = await quality.record(session, events, commit=True) if record and events else 0
    return {"instruments": len(keys), "gap_events": len(events), "new_events": written, "trading_days": len(trading),
            "days_with_file": len(usable), "missing_files": missing_files[:500], "years_without_calendar": unknown}


# --- CLI ------------------------------------------------------------------------------------------------------------
def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m app.market_lake.bhavcopy_backfill", description="HB1: daily NSE bhavcopy backfill")
    sub = p.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="backfill a date range (resumable; only what is not done yet)")
    run.add_argument("--from", dest="start", required=True, type=date.fromisoformat)
    run.add_argument("--to", dest="end", required=True, type=date.fromisoformat)
    run.add_argument("--segments", default="cm,index,fo")
    run.add_argument("--with-options", action="store_true", help="also load F&O options (large)")
    run.add_argument("--include-weekends", action="store_true", help="also ask for Saturdays and Sundays (special sessions)")
    run.add_argument("--limit", type=int, default=None, help="process at most N files this run (resume later)")
    run.add_argument("--retry-missing", action="store_true", help="ask again for days recorded as no_file")
    run.add_argument("--local-dir", default=None, help="read the files from a directory instead of the NSE archive")
    sub.add_parser("nightly", help="today's files plus the catch-up window, now")
    hol = sub.add_parser("holidays", help="load an exchange holiday list CSV into market_holidays")
    hol.add_argument("file")
    hol.add_argument("--exchange", default="NSE")
    ca = sub.add_parser("corporate-actions", help="load a corporate-actions CSV into md_corporate_actions")
    ca.add_argument("file")
    gaps = sub.add_parser("gaps", help="report per-instrument gaps on days whose cash file is held")
    gaps.add_argument("--from", dest="start", required=True, type=date.fromisoformat)
    gaps.add_argument("--to", dest="end", required=True, type=date.fromisoformat)
    gaps.add_argument("--symbols", default="")
    return p


async def _main(argv: Optional[Sequence[str]] = None) -> int:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    args = _parser().parse_args(argv)
    engine = create_async_engine(config.DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    result: Dict[str, Any]
    try:
        await _require_flag(factory)
        if args.command in ("run", "nightly"):
            fetcher = PoliteFetcher() if args.command == "nightly" or not args.local_dir else None
            try:
                if args.command == "nightly":
                    summary = await run_nightly(factory, fetcher)  # type: ignore[arg-type]  # set above
                else:
                    summary = await run_backfill(factory, start=args.start, end=args.end, fetcher=fetcher, local_dir=args.local_dir,
                                                 segments=[s.strip() for s in args.segments.split(",") if s.strip()],
                                                 include_weekends=args.include_weekends, limit=args.limit,
                                                 retry_no_file=args.retry_missing, with_options=args.with_options or None)
            finally:
                if fetcher is not None:
                    await fetcher.aclose()
            print(json.dumps(summary.as_dict(), indent=2))
            return 1 if summary.refused or summary.failed or summary.blocked else 0
        async with factory() as session:
            if args.command == "holidays":
                from app.market_data.holiday_import import import_holidays
                result = (await import_holidays(session, Path(args.file).read_bytes(), exchange=args.exchange)).as_dict()
            elif args.command == "corporate-actions":
                from app.market_lake.corporate_actions import load_csv
                result = (await load_csv(session, Path(args.file).read_bytes(), source="ca_csv:cli")).as_dict()
            else:
                result = await gap_report(session, args.start, args.end,
                                          symbols=[s for s in args.symbols.split(",") if s.strip()] or None)
        print(json.dumps(result, indent=2, default=str))
        return 0
    except BackfillConfigError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    finally:
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(asyncio.run(_main()))
