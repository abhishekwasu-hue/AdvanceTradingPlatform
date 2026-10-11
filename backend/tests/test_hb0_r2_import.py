"""HB0: the R2 history import - daily bars into the shared lake, 1-minute bars into the owner's private store only.

The bucket is a local directory (LocalDirStore) holding real Parquet (zstd) files and manifests built here; nothing
contacts R2 or a broker. Symbols are made up (HBZ...), dates are in 2001 so no real calendar or price is implied.
Covered: counts verified against the manifest and the lake; idempotent re-run; a changed file re-imported as
corrections; resume after a crash; checksum / row-count / bad-value refusals; quality detectors run and gaps reported
(never filled); the flag and the owner-tenant setting gate the run; another tenant cannot read the owner's 1-minute
store, through the functions or the history API; 3/5/30/60-minute bars are built on read, aligned to 09:15 IST.
"""
import asyncio
import hashlib
import io
import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from sqlalchemy import func, select

from app.db.models import (DataQualityEventRecord, HistoryImportFileRecord, MarketHolidayRecord, MdCandleRecord,
                           MdTenantCandleRecord, User)
from app.market_data.calendar import IST
from app.market_lake import r2_import, tenant_store
from app.market_lake.ingest import LakeBar
from app.market_lake.object_store import LocalDirStore, R2Store, StoredObject
from app.platform import controls
from tests.test_auth_api import _register, _session_factory, client
from tests.utils import enable_mfa

YEAR = 2001
HOLIDAY = date(YEAR, 1, 26)
GAP_DAYS = (date(YEAR, 3, 5), date(YEAR, 3, 6))         # Monday + Tuesday missing from the daily file
M1_DAYS = (date(YEAR, 2, 5), date(YEAR, 2, 6))
M1_GAP = (time(10, 0), time(10, 1), time(10, 2))          # bar STARTS missing on the first day


def _run(coro):
    return asyncio.run(coro)


def _count(model, *where):
    async def go():
        async with _session_factory() as session:
            return int(await session.scalar(select(func.count()).select_from(model).where(*where)) or 0)
    return _run(go())


def _flag(on: bool):
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = current.get("flags") or {}
            flags[r2_import.FLAG] = {"on": on, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())


def _seed_holiday():
    async def go():
        async with _session_factory() as session:
            exists = await session.scalar(select(MarketHolidayRecord.id).where(
                MarketHolidayRecord.exchange == "NSE", MarketHolidayRecord.holiday_date == HOLIDAY))
            if exists is None:
                session.add(MarketHolidayRecord(exchange="NSE", holiday_date=HOLIDAY, description="HB0 test holiday"))
                await session.commit()
    _run(go())


# --- building the bucket --------------------------------------------------------------------------------------------
def _parquet(rows) -> bytes:
    fetched = datetime(YEAR + 20, 1, 1, tzinfo=timezone.utc)
    table = pa.table({
        "ts": pa.array([r["ts"] for r in rows], type=pa.timestamp("us", tz="UTC")),
        "open": pa.array([r["open"] for r in rows], type=pa.float64()),
        "high": pa.array([r["high"] for r in rows], type=pa.float64()),
        "low": pa.array([r["low"] for r in rows], type=pa.float64()),
        "close": pa.array([r["close"] for r in rows], type=pa.float64()),
        "volume": pa.array([r.get("volume", 100) for r in rows], type=pa.int64()),
        "oi": pa.array([r.get("oi") for r in rows], type=pa.int64()),
        "isin": pa.array(["INE000HB0001"] * len(rows), type=pa.string()),
        "source": pa.array([r.get("source", "upstox") for r in rows], type=pa.string()),
        "fetched_at": pa.array([fetched] * len(rows), type=pa.timestamp("us", tz="UTC")),
    })
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="zstd")
    return buf.getvalue()


def _row(ts: datetime, close: float, **kw):
    return {"ts": ts, "open": close, "high": close + 1, "low": close - 1, "close": close, **kw}


def daily_rows(skip=GAP_DAYS):
    rows, day, i = [], date(YEAR, 1, 1), 0
    while day.year == YEAR:
        if day.weekday() < 5 and day != HOLIDAY and day not in skip:
            end = datetime.combine(day, time(15, 30), tzinfo=IST).astimezone(timezone.utc)    # session close = bar END
            rows.append(_row(end, 100 + (i % 5) * 0.5))
            i += 1
        day += timedelta(days=1)
    return rows


def m1_rows():
    rows = []
    for d in M1_DAYS:
        start = datetime.combine(d, time(9, 15), tzinfo=IST)
        for k in range(375):
            bar_start = start + timedelta(minutes=k)
            if d == M1_DAYS[0] and bar_start.timetz().replace(tzinfo=None) in M1_GAP:
                continue
            rows.append(_row((bar_start + timedelta(minutes=1)).astimezone(timezone.utc), 200 + (k % 7) * 0.25, oi=1000 + k))
    return rows


class Bucket:
    """A local-directory R2 layout: write files, then (re)write each folder's manifest from what is there."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.files = {}                                       # key -> (rows, sha256) as the manifest will claim

    def put(self, key: str, rows, *, sha: str = "", claimed_rows=None) -> bytes:
        data = _parquet(rows)
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self.files[key] = (len(rows) if claimed_rows is None else claimed_rows, sha or hashlib.sha256(data).hexdigest())
        return data

    def manifests(self, extra=None) -> None:
        folders = {}
        for key, (rows, sha) in self.files.items():
            dataset, exchange = key.split("/")[:2]
            folders.setdefault(f"{dataset}/{exchange}", []).append((key, rows, sha))
        for folder, entries in folders.items():
            symbols = sorted({k.split("/")[2] for k, _, _ in entries})
            manifest = {"dataset": folder.split("/")[0], "exchange": folder.split("/")[1], "symbols": symbols,
                        "date_range": {"from": f"{YEAR}-01-01", "to": f"{YEAR}-12-31"},
                        "row_counts": {s: sum(r for k, r, _ in entries if k.split("/")[2] == s) for s in symbols},
                        "files": [{"path": k[len(folder) + 1:], "rows": r, "sha256": sha} for k, r, sha in sorted(entries)]}
            manifest.update((extra or {}).get(folder, {}))
            (self.root / folder / "manifest.json").write_text(json.dumps(manifest))

    def store(self):
        return LocalDirStore(self.root)


def _import(store, datasets=("daily",), owner=None, **kw):
    return _run(r2_import.run_import(_session_factory, store, datasets=list(datasets), owner_tenant_id=owner, **kw))


def _state(key):
    async def go():
        async with _session_factory() as session:
            return await session.scalar(select(HistoryImportFileRecord).where(HistoryImportFileRecord.object_key == key))
    return _run(go())


# --- daily -> shared lake -------------------------------------------------------------------------------------------
def test_daily_import_verifies_counts_reports_gaps_and_reruns_idempotently(tmp_path):
    _flag(True)
    _seed_holiday()
    bucket = Bucket(tmp_path)
    key = f"daily/NSE/HBZD1/{YEAR}.parquet"
    rows = daily_rows()
    bucket.put(key, rows)
    bucket.manifests()

    first = _import(bucket.store())
    assert (first.done, first.refused, first.failed, first.rows) == (1, 0, 0, len(rows))
    lake_key = "NSE:HBZD1"
    assert _count(MdCandleRecord, MdCandleRecord.instrument_key == lake_key, MdCandleRecord.timeframe == "day") == len(rows)
    assert _count(MdTenantCandleRecord, MdTenantCandleRecord.instrument_key == lake_key) == 0
    state = _state(key)
    assert state.status == "done" and state.rows_expected == state.rows_read == state.rows_in_lake == len(rows)
    assert state.isin == "INE000HB0001" and state.tenant_id is None and state.attempts == 1
    gaps = _count(DataQualityEventRecord, DataQualityEventRecord.instrument_key == lake_key, DataQualityEventRecord.kind == "gap",
                  DataQualityEventRecord.tenant_id.is_(None))
    assert gaps == 1                                    # the two missing days are one run; the holiday is not a gap

    again = _import(bucket.store())
    assert (again.done, again.skipped, again.files) == (0, 1, 0)
    assert _count(MdCandleRecord, MdCandleRecord.instrument_key == lake_key) == len(rows)
    assert _state(key).attempts == 1

    # The collector rewrites the file with one corrected close: re-imported, one new version, the rest skipped.
    fixed = list(rows)
    fixed[10] = _row(rows[10]["ts"], rows[10]["close"] + 0.25)
    bucket.put(key, fixed)
    bucket.manifests()
    third = _import(bucket.store())
    assert third.done == 1 and third.corrected == 1 and third.inserted == 0
    assert _count(MdCandleRecord, MdCandleRecord.instrument_key == lake_key) == len(rows) + 1
    assert _count(MdCandleRecord, MdCandleRecord.instrument_key == lake_key, MdCandleRecord.version == 2) == 1
    assert _count(DataQualityEventRecord, DataQualityEventRecord.instrument_key == lake_key, DataQualityEventRecord.kind == "gap") == 1


def test_daily_without_a_holiday_calendar_skips_the_gap_check_and_says_so(tmp_path):
    _flag(True)
    bucket = Bucket(tmp_path)
    key = "daily/NSE/HBZD2/1998.parquet"
    end = datetime.combine(date(1998, 6, 1), time(15, 30), tzinfo=IST).astimezone(timezone.utc)
    bucket.put(key, [_row(end, 50), _row(end + timedelta(days=3), 50.5)])     # Mon, Thu: two missing weekdays
    bucket.manifests(extra={"daily/NSE": {"date_range": {}}})
    assert _import(bucket.store()).done == 1
    assert "daily gap check skipped" in (_state(key).quality_note or "")
    assert _count(DataQualityEventRecord, DataQualityEventRecord.instrument_key == "NSE:HBZD2", DataQualityEventRecord.kind == "gap") == 0


def test_checksum_and_count_mismatches_are_refused_and_nothing_is_written(tmp_path):
    _flag(True)
    bucket = Bucket(tmp_path)
    bad_sha, bad_rows = f"daily/NSE/HBZC1/{YEAR}.parquet", f"daily/NSE/HBZC2/{YEAR}.parquet"
    bucket.put(bad_sha, daily_rows(), sha="0" * 64)
    bucket.put(bad_rows, daily_rows(), claimed_rows=7)
    bucket.manifests(extra={"daily/NSE": {"row_counts": {}}})
    summary = _import(bucket.store())
    assert (summary.done, summary.refused) == (0, 2)
    assert _state(bad_sha).status == "refused" and "sha256 mismatch" in _state(bad_sha).error
    assert _state(bad_rows).status == "refused" and "row count: manifest 7" in _state(bad_rows).error
    for sym in ("HBZC1", "HBZC2"):
        assert _count(MdCandleRecord, MdCandleRecord.instrument_key == f"NSE:{sym}") == 0

    # Fixed upstream: the next run retries the refused files and loads them.
    bucket.put(bad_sha, daily_rows())
    bucket.put(bad_rows, daily_rows())
    bucket.manifests()
    fixed = _import(bucket.store())
    assert (fixed.done, fixed.refused) == (2, 0)
    assert _state(bad_sha).status == "done" and _state(bad_sha).error is None and _state(bad_sha).attempts == 2


def test_manifest_row_counts_disagreeing_with_files_refuse_that_symbol(tmp_path):
    _flag(True)
    bucket = Bucket(tmp_path)
    key = f"daily/NSE/HBZC3/{YEAR}.parquet"
    bucket.put(key, daily_rows())
    bucket.manifests(extra={"daily/NSE": {"row_counts": {"HBZC3": 1}}})
    assert _import(bucket.store()).refused == 1
    assert _state(key).status == "refused" and "row_counts" in _state(key).error


def test_values_the_importer_would_have_to_guess_are_refused(tmp_path):
    _flag(True)
    bucket = Bucket(tmp_path)
    rows = daily_rows()
    no_price = [dict(r) for r in rows]
    no_price[3]["close"] = None
    conflicting = rows + [_row(rows[5]["ts"], rows[5]["close"] + 9)]
    identical = rows + [dict(rows[6])]
    wrong_year = rows + [_row(rows[0]["ts"] - timedelta(days=400), 100)]
    keys = {}
    for sym, data in (("HBZV1", no_price), ("HBZV2", conflicting), ("HBZV3", identical), ("HBZV4", wrong_year)):
        keys[sym] = f"daily/NSE/{sym}/{YEAR}.parquet"
        bucket.put(keys[sym], data)
    bucket.manifests(extra={"daily/NSE": {"date_range": {}}})
    summary = _import(bucket.store())
    assert (summary.done, summary.refused) == (1, 3)
    assert "without a timestamp or a price" in _state(keys["HBZV1"]).error
    assert "two different bars" in _state(keys["HBZV2"]).error
    assert "outside the file's period" in _state(keys["HBZV4"]).error
    ok = _state(keys["HBZV3"])                       # an identical repeat is dropped and reported, not refused
    assert ok.status == "done" and ok.rows_read == len(rows) + 1 and ok.rows_in_lake == len(rows)
    assert _count(DataQualityEventRecord, DataQualityEventRecord.instrument_key == "NSE:HBZV3", DataQualityEventRecord.kind == "duplicate") == 1


class _Crash(BaseException):
    """The process dying mid-run (not an error the importer could catch and record)."""


class _CrashingStore(LocalDirStore):
    def __init__(self, root, crash_on: str) -> None:
        super().__init__(root)
        self.crash_on = crash_on

    def get(self, key: str) -> StoredObject:
        if key == self.crash_on:
            raise _Crash(key)
        return super().get(key)


def test_resume_after_an_interruption(tmp_path):
    _flag(True)
    bucket = Bucket(tmp_path)
    keys = [f"daily/NSE/HBZR{i}/{YEAR}.parquet" for i in (1, 2, 3)]
    for key in keys:
        bucket.put(key, daily_rows())
    bucket.manifests()
    with pytest.raises(_Crash):
        _import(_CrashingStore(tmp_path, keys[1]))
    assert [_state(k).status if _state(k) else None for k in keys] == ["done", "running", None]
    assert _count(MdCandleRecord, MdCandleRecord.instrument_key == "NSE:HBZR2") == 0

    resumed = _import(bucket.store())
    assert (resumed.skipped, resumed.done, resumed.refused, resumed.failed) == (1, 2, 0, 0)
    for i, key in enumerate(keys, 1):
        state = _state(key)
        assert state.status == "done" and state.rows_in_lake == len(daily_rows())
        assert _count(MdCandleRecord, MdCandleRecord.instrument_key == f"NSE:HBZR{i}") == len(daily_rows())
    assert _state(keys[1]).attempts == 2 and _state(keys[0]).attempts == 1

    limited = Bucket(tmp_path / "limited")
    for i in (4, 5, 6):
        limited.put(f"daily/NSE/HBZR{i}/{YEAR}.parquet", daily_rows())
    limited.manifests()
    assert _import(limited.store(), limit=2).done == 2               # --limit: a partial run, finished by the next one
    rest = _import(limited.store())
    assert (rest.skipped, rest.done) == (2, 1)


def test_the_run_is_gated_by_the_flag_and_the_owner_setting(tmp_path):
    bucket = Bucket(tmp_path)
    bucket.put(f"m1/NSE/HBZG1/{YEAR}-02.parquet", m1_rows())
    bucket.manifests()
    _flag(False)
    with pytest.raises(r2_import.ImportConfigError, match="flag is off"):
        _import(bucket.store(), datasets=("daily",))
    assert r2_import.FLAG in controls.DEFAULT_OFF_FLAGS and r2_import.FLAG in controls.FEATURE_FLAGS
    _flag(True)
    with pytest.raises(r2_import.ImportConfigError, match="HISTORY_OWNER_TENANT_ID"):
        _import(bucket.store(), datasets=("m1",), owner=None)
    with pytest.raises(r2_import.ImportConfigError, match="not a tenant"):
        _import(bucket.store(), datasets=("m1",), owner=987654)
    assert _count(MdTenantCandleRecord, MdTenantCandleRecord.instrument_key == "NSE:HBZG1") == 0
    assert _state(f"m1/NSE/HBZG1/{YEAR}-02.parquet") is None


# --- 1-minute -> the owner's tenant only ----------------------------------------------------------------------------
def _user(email):
    headers = {"Authorization": f"Bearer {_register(email)}"}
    return headers, client.get("/api/auth/me", headers=headers).json()


def _history(headers, **params):
    base = {"instrument_key": "NSE:HBZM1", "timeframe": "1m", "start": datetime(YEAR, 2, 5, tzinfo=timezone.utc).isoformat(),
            "end": datetime(YEAR, 2, 7, tzinfo=timezone.utc).isoformat()}
    return client.get("/api/market-data/history", params={**base, **params}, headers=headers)


def test_one_minute_goes_to_the_owner_tenant_and_no_one_else_can_read_it(tmp_path):
    _flag(True)
    owner_headers, owner = _user("hb0-owner@example.com")
    other_headers, other = _user("hb0-other@example.com")
    assert owner["tenant_id"] != other["tenant_id"]
    bucket = Bucket(tmp_path)
    key = f"m1/NSE/HBZM1/{YEAR}-02.parquet"
    rows = m1_rows()
    bucket.put(key, rows)
    bucket.manifests()

    summary = _import(bucket.store(), datasets=("m1",), owner=owner["tenant_id"])
    assert len(rows) == 2 * 375 - 3 and (summary.done, summary.rows) == (1, len(rows))
    state = _state(key)
    assert state.status == "done" and state.tenant_id == owner["tenant_id"] and state.rows_in_lake == len(rows)
    lake_key = "NSE:HBZM1"
    assert _count(MdCandleRecord, MdCandleRecord.instrument_key == lake_key) == 0                  # never in the shared lake
    assert _count(MdTenantCandleRecord, MdTenantCandleRecord.instrument_key == lake_key,
                  MdTenantCandleRecord.tenant_id == owner["tenant_id"]) == len(rows)
    assert _count(MdTenantCandleRecord, MdTenantCandleRecord.instrument_key == lake_key,
                  MdTenantCandleRecord.tenant_id != owner["tenant_id"]) == 0
    gap_events = _count(DataQualityEventRecord, DataQualityEventRecord.instrument_key == lake_key, DataQualityEventRecord.kind == "gap")
    assert gap_events == 1 and _count(DataQualityEventRecord, DataQualityEventRecord.instrument_key == lake_key,
                                      DataQualityEventRecord.tenant_id == owner["tenant_id"]) == gap_events

    # The owner reads their own store; the 3 missing minutes stay missing.
    mine = _history(owner_headers, store="tenant", adjusted="false")
    assert mine.status_code == 200, mine.text
    body = mine.json()
    assert body["store"] == "tenant" and len(body["candles"]) == len(rows) and body["quality_events"] == 1
    # Another tenant: the same request returns nothing - not the bars, not even the quality-event count.
    theirs = _history(other_headers, store="tenant", adjusted="false").json()
    assert theirs["candles"] == [] and theirs["quality_events"] == 0
    # The shared store never carries the private bars or their events, for anyone (the owner included).
    for headers in (owner_headers, other_headers):
        shared = _history(headers).json()
        assert shared["store"] == "shared" and shared["candles"] == [] and shared["quality_events"] == 0
    # There is no way to name another tenant: the parameter does not exist, and the function refuses no tenant.
    assert _history(other_headers, store="tenant", tenant_id=str(owner["tenant_id"])).json()["candles"] == []
    assert _history(other_headers, store="everyone").status_code == 422

    async def no_tenant():
        async with _session_factory() as session:
            await tenant_store.candles(session, None, lake_key, "1m", datetime(YEAR, 2, 5, tzinfo=timezone.utc),
                                       datetime(YEAR, 2, 7, tzinfo=timezone.utc), symbol="HBZM1")
    with pytest.raises(PermissionError):
        _run(no_tenant())

    # Built on read from 1-minute: 5m and 60m for the two sessions (the last 60m bar ends at the 15:30 close).
    five = _history(owner_headers, store="tenant", timeframe="5m", adjusted="false").json()["candles"]
    assert len(five) == 2 * 75
    sixty = _history(owner_headers, store="tenant", timeframe="60m", adjusted="false").json()["candles"]
    ends = [datetime.fromisoformat(c["ts"]).astimezone(IST).strftime("%H:%M") for c in sixty[:7]]
    assert ends == ["10:15", "11:15", "12:15", "13:15", "14:15", "15:15", "15:30"]
    assert _history(owner_headers, store="tenant", timeframe="7m").status_code == 422

    # Re-run: nothing new, nothing duplicated.
    assert _import(bucket.store(), datasets=("m1",), owner=owner["tenant_id"]).skipped == 1
    assert _count(MdTenantCandleRecord, MdTenantCandleRecord.instrument_key == lake_key) == len(rows)


def test_resample_aligns_to_the_session_open_and_never_invents_minutes():
    day = date(YEAR, 2, 5)
    open_ = datetime.combine(day, time(9, 15), tzinfo=IST)
    bars = [LakeBar("K", "1m", (open_ + timedelta(minutes=k + 1)).astimezone(timezone.utc), 10 + k, 11 + k, 9 + k, 10.5 + k, 2, k)
            for k in range(60) if k not in (31, 32)]
    thirty = tenant_store.resample(bars, 30)
    assert [b.ts.astimezone(IST).strftime("%H:%M") for b in thirty] == ["09:45", "10:15"]
    first, second = thirty
    assert (first.open, first.high, first.low, first.close, first.volume, first.oi) == (10, 40, 9, 39.5, 60, 29)
    assert second.volume == 2 * 28 and second.open == 40 and second.close == 69.5        # two minutes missing, not filled
    assert tenant_store.resample(bars, 1) == sorted(bars, key=lambda b: b.ts)
    with pytest.raises(ValueError):
        tenant_store.resample_minutes("7m")


# --- the admin progress view, the manifest parser and the R2 client ------------------------------------------------
def test_progress_is_super_admin_only_and_shows_symbols(tmp_path):
    _flag(True)
    bucket = Bucket(tmp_path)
    bucket.put(f"daily/NSE/HBZP1/{YEAR}.parquet", daily_rows())
    bucket.put(f"daily/NSE/HBZP2/{YEAR}.parquet", daily_rows(), sha="1" * 64)
    bucket.manifests()
    _import(bucket.store())
    owner_headers, _ = _user("hb0-progress-owner@example.com")
    assert client.get("/api/admin/history-import/progress", headers=owner_headers).status_code == 403
    admin_headers, admin = _user("hb0-progress-admin@example.com")

    async def promote():
        async with _session_factory() as session:
            user = await session.get(User, admin["id"])
            user.role = "SUPER_ADMIN"
            await session.commit()
    _run(promote())
    enable_mfa(admin_headers)
    body = client.get("/api/admin/history-import/progress", params={"dataset": "daily", "limit": 5000}, headers=admin_headers).json()
    by_symbol = {s["symbol"]: s for s in body["symbols"]}
    assert body["flag_on"] is True and body["datasets"]["daily"]["done"] >= 1 and body["datasets"]["daily"]["refused"] >= 1
    assert by_symbol["HBZP1"]["done"] == 1 and by_symbol["HBZP1"]["rows_in_lake"] == len(daily_rows())
    assert by_symbol["HBZP2"]["refused"] == 1 and "sha256 mismatch" in by_symbol["HBZP2"]["last_error"]
    assert "candles" not in json.dumps(body)


def test_manifest_parser_accepts_both_shapes_and_refuses_bad_entries():
    sha = "a" * 64
    files, refusals = r2_import.parse_manifest("m1/NSE/manifest.json", json.dumps({
        "symbols": ["AAA", "BBB"],
        "files": {"AAA/2022-01.parquet": {"rows": 10, "sha256": sha}, "BBB/2022-13.parquet": {"rows": 1, "sha256": sha},
                  "CCC/2022-01.parquet": {"rows": 1, "sha256": sha}, "BBB/2022-02.parquet": {"rows": 1, "sha256": "xyz"},
                  "daily/NSE/AAA/2022.parquet": {"rows": 1, "sha256": sha}}}).encode())
    assert [(f.key, f.dataset, f.exchange, f.symbol, f.period, f.rows) for f in files] == [
        ("m1/NSE/AAA/2022-01.parquet", "m1", "NSE", "AAA", "2022-01", 10)]
    reasons = dict(refusals)
    assert "layout" in reasons["m1/NSE/BBB/2022-13.parquet"] and "not in the manifest" in reasons["m1/NSE/CCC/2022-01.parquet"]
    assert "sha256" in reasons["m1/NSE/BBB/2022-02.parquet"] and "outside its folder" in reasons["daily/NSE/AAA/2022.parquet"]
    with pytest.raises(r2_import.ImportRefused):
        r2_import.parse_manifest("daily/NSE/manifest.json", b"{not json")
    assert r2_import.lake_source("Upstox V3") == "r2:upstoxv3" and r2_import.lake_source(None) == "r2:unknown"


def test_r2_store_reads_through_the_s3_api_with_env_credentials_only(monkeypatch):
    for name in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET", "R2_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match="R2_ACCESS_KEY_ID"):
        R2Store.from_env()

    class _Body:
        def read(self):
            return b"payload"

    class _Paginator:
        def paginate(self, Bucket, Prefix):
            assert Bucket == "hb0-test-bucket"
            yield {"Contents": [{"Key": f"{Prefix}x/manifest.json"}]}
            yield {}

    class _FakeS3:
        def get_paginator(self, name):
            assert name == "list_objects_v2"
            return _Paginator()

        def get_object(self, Bucket, Key):
            return {"Body": _Body(), "ETag": '"etag-1"'}

    store = R2Store(bucket="hb0-test-bucket", endpoint="http://r2.invalid", access_key_id="FAKEFAKE", secret_access_key="FAKEFAKE",
                    client=_FakeS3())
    assert list(store.list_keys("daily/")) == ["daily/x/manifest.json"]
    obj = store.get("daily/x/manifest.json")
    assert obj.data == b"payload" and obj.etag == "etag-1"
