# HB - History backfill: design note

Owner spec: "History backfill" (HB0-HB4). Builds on Part B (docs/design/B_DATA_LAKE.md, ADR-0013): bars are labelled by
their END in UTC, every row carries `ingested_at` and `version`, adjustment is a read-time view (B4), quality problems
are events (B3), never edits.

> **Update (Phase 0 freeze, Z4):** the owner cancelled the Trade-repo R2 collector. The R2 importer below stays in the
> code as an **optional, flag-gated path** (flag `history_import`, off) for any R2 bucket or local folder in this
> layout, but nothing depends on it. **HB1 (NSE bhavcopy backfill + nightly EOD job) is the primary daily path**; the
> owner's 1-minute history (HB2) still lands only in the owner's tenant store (`md_tenant_candles`) whichever way it
> is loaded.

## Status
| # | Item | Status | PR |
|---|---|---|---|
| HB0 | R2 importer (optional path - collector cancelled): manifest + sha256 checks, daily -> shared lake, 1-minute -> owner tenant only, import state, progress API, flag `history_import` (off) | this PR | #164 |
| HB1 | Daily backfill: 10 years, all NSE equities + indices, adjusted via B4 (unadjusted kept), quality gates, gaps reported | planned - **primary daily path**: NSE bhavcopy archives + nightly EOD job (the R2 daily folders are optional) | - |
| HB2 | Owner intraday: 1-minute from the owner's Upstox entitlement since Jan 2022, resampled on read to 3/5/15/30/60 | planned (runs HB0 on the m1 folders) | - |
| HB3 | Extend the universe (new listings, delistings, renames via ISIN) | planned | - |
| HB4 | Native nightly jobs on the VPS + admin progress page (symbols done / pending / last error) | planned - reads `history_import_files` | - |

## Licensing and scope rule
- **Daily** bars are exchange end-of-day data: they go to the shared lake (`md_candles`, timeframe `day`) and every
  tenant may read them (Tier C).
- **1-minute** bars come from the owner's personal Upstox data entitlement. They are **tenant-scoped to the owner**:
  research and backtests of the owner's tenant only. They are never served to another tenant, never copied into the
  shared lake, and never shown on a shared page (marketplace, public pages, shared screens).
- Enforcement in HB0:
  - a separate table `md_tenant_candles` with `tenant_id` in its primary key. No shared read path (the history API's
    shared store, `asof.candles_as_of`, `adjust.candles`, `quality.scan_day`, backtests) queries it, so a forgotten
    filter cannot leak a bar;
  - the only reader, `app/market_lake/tenant_store.py`, takes the reader's tenant id and refuses None;
  - `GET /api/market-data/history?store=tenant` always uses the authenticated user's own tenant (there is no tenant
    parameter); `store=shared` (the default) never sees private rows;
  - data-quality events about the private store carry `tenant_id` and are counted only for that tenant;
  - the importer loads 1-minute data only into `HISTORY_OWNER_TENANT_ID` and refuses to load it when that is unset.

Why a separate table instead of a nullable `tenant_id` on `md_candles`: the column would have to join the primary key
(a hypertable on TimescaleDB, where changing the key is a table rewrite), and every existing reader would need a new
filter - one missed filter is a licensing breach. The separate table changes no existing reader and is reversible by
dropping it. The price: reads of the private store go through `tenant_store` rather than `adjust.candles`, so a later
backtest reader must call it explicitly (HB2).

## The R2 layout (as the cancelled Trade-repo collector would have written it; any producer may use it)
```
daily/{exchange}/{symbol}/{yyyy}.parquet
m1/{exchange}/{symbol}/{yyyy-mm}.parquet
{daily|m1}/{exchange}/manifest.json        (or one per symbol folder)
```
Parquet with zstd compression. Columns: `ts` (timestamp, UTC, bar END), `open`, `high`, `low`, `close` (unadjusted),
`volume`, `oi` (nullable), `isin`, `source`, `fetched_at`. Naive timestamps are read as UTC; a daily bar belongs to the
UTC date of (END - 1 microsecond).

Manifest accepted by the importer (paths relative to the manifest's folder, or full keys):
```json
{"dataset": "daily", "exchange": "NSE", "symbols": ["RELIANCE"],
 "date_range": {"from": "2016-01-01", "to": "2025-12-31"},
 "row_counts": {"RELIANCE": 2480},
 "files": [{"path": "RELIANCE/2024.parquet", "rows": 248, "sha256": "<64 hex>"}]}
```
`files` may also be an object keyed by path. `symbols`, `date_range` and `row_counts` are optional; when present they
are checked.

## What the importer does (`app/market_lake/r2_import.py`)
1. Refuses to start unless the `history_import` flag is on; for 1-minute data also unless `HISTORY_OWNER_TENANT_ID`
   names an existing tenant.
2. Lists every `manifest.json` under the requested dataset, checks each entry (layout, symbol listed, row count and
   sha256 present, per-symbol `row_counts` equal to the sum of the files).
3. Per file, one transaction: download, **sha256 must equal the manifest**, the Parquet row count must equal the
   manifest, every bar must fall in the file's period (and the manifest's date range), no row may lack a timestamp or
   price, two different bars with the same END are refused (an identical repeat is dropped and reported as
   `duplicate`). Bars are written with the B1 rules (identical skipped, changed -> next version), the lake is counted
   for the file's range and must hold every bar, the B3 detectors run (spike / invalid / duplicate; 1-minute gaps per
   session; daily gaps against the holiday calendar), and the file's state row becomes `done`.
4. A refused file is recorded with its reason and nothing of it is written; a failed one (unexpected error) likewise.
   Both are retried on the next run. A file already `done` with the same sha256 is skipped; one the collector has
   rewritten (new sha256) is re-imported, and only changed bars become new versions.
5. A crash leaves the current file `running` with nothing written; the next run retries it.
6. One import at a time: a second run on the same Postgres database refuses to start (advisory lock).

Gaps are reported, never filled. Daily gap detection needs the exchange holiday calendar for that year
(`market_holidays`); for a year without one the check is skipped and the file's `quality_note` says so (otherwise every
holiday would be a "gap").

Lake rows: instrument key `{EXCHANGE}:{SYMBOL}`, timeframe `day` or `1m`, source `r2:{collector source}` (e.g.
`r2:upstox`), `ingested_at` = the import time.

## State and progress (for HB4)
`history_import_files`: one row per object key - dataset, exchange, symbol, period, tenant (1-minute only), status
(`pending | running | done | refused | failed`), manifest sha256 and object etag, rows expected / read / in the lake,
inserted / corrected / skipped, quality-event count and note, ISIN, first and last bar, attempts, last error, timestamps.
`GET /api/admin/history-import/progress` (SUPER_ADMIN with an MFA session, read-only) returns the files by status per
dataset and, per symbol, done / pending / refused / failed, rows in the lake, quality events and the last error -
symbols and counts only, never bars. HB4's admin page reads this.

## Operator steps
Environment names only - values are set on the server, never committed:
- `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` - a **read-only, bucket-scoped** R2 API token;
- `R2_BUCKET`, `R2_ENDPOINT` (`https://<account id>.r2.cloudflarestorage.com`);
- `HISTORY_OWNER_TENANT_ID` - the owner's tenant id (only for the 1-minute import).

1. Deploy this release and run `alembic upgrade head` (migration `a8c0e2f4b6d8`).
2. Admin > Controls: turn the `history_import` flag on (it is off by default).
3. Daily (optional - HB1's bhavcopy backfill is the primary path): `python -m app.market_lake.r2_import --dataset daily`
   (inside the backend container). Optional:
   `--symbols A,B` to start small, `--limit N` to import at most N files per run (re-run to continue).
4. 1-minute (HB2), with `HISTORY_OWNER_TENANT_ID` set: `python -m app.market_lake.r2_import --dataset m1`.
5. Check `GET /api/admin/history-import/progress`; refused / failed files show their reason. Fix the source (or the
   manifest) in the bucket and run the same command again - only what is not `done` is retried.
6. Turn the flag off again when the backfill is complete. HB4 replaces this manual run with nightly jobs.
`--local-dir PATH` reads the same layout from a directory (a bucket synced to disk) instead of R2.
Exit codes: 0 all done, 1 some files refused or failed, 2 refused to start.

## Tests (`backend/tests/test_hb0_r2_import.py`)
Real Parquet fixtures in a local-directory store; no network. Idempotent re-run; changed file -> corrections; resume
after a simulated crash; `--limit` partial runs; sha256 and row-count mismatches refused (and loaded once fixed);
manifest `row_counts` mismatch; missing price / conflicting duplicate / bar outside its period refused; identical
duplicate reported; counts verified against the manifest and the lake; daily and 1-minute gaps reported as events;
holiday-less years skip the daily gap check; the flag and the owner setting gate the run; another tenant cannot read
the owner's 1-minute store (functions and history API, including the quality-event count); the shared store never
returns private bars; resampling aligned to 09:15 IST with the last bucket ending at 15:30; progress endpoint is
SUPER_ADMIN only; the R2 client is driven through a fake S3 client.

## Open questions for the owner
1. Manifest schema: the importer accepts the shape above. The Trade-repo collector is cancelled; if another producer
   ever fills an R2 bucket, it must write this shape (or the parser is the one place to adapt).
2. Daily bar END: the importer takes the collector's `ts` as is. Recommended: 15:30 IST (10:00 UTC) on the trading day.
3. `ingested_at` for backfilled rows is the import time, so an `as_of` replay earlier than the import does not see
   them. Should the backfill instead use `fetched_at` (still recent) or a fixed "known since" date for research?
4. Corporate actions: the adjusted view needs `md_corporate_actions` filled for the 10 years (HB1). Source to confirm
   (NSE corporate-action files vs a vendor).
5. Holiday calendar: daily gap checks need `market_holidays` for 2016-2025 (only 2026 is seeded). Load the past NSE
   lists before HB1, or accept "gap check skipped" notes for those years.
6. Should users of the owner's tenant other than the owner (team members) see the 1-minute store? HB0 scopes it to
   the tenant, as the spec says; a narrower per-user scope would need a further check.
