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
| HB1 | Daily backfill: 10 years, all NSE equities + indices (+ F&O futures), adjusted via B4 (unadjusted kept), quality gates, gaps reported; nightly EOD job; flag `history_backfill` (off) | this PR (draft, stacked on HB0) - **primary daily path** (NSE bhavcopy archives; the R2 daily folders are optional) | - |
| HB2 | Owner intraday: 1-minute from the owner's Upstox entitlement since Jan 2022, resampled on read to 3/5/15/30/60 | planned (runs HB0 on the m1 folders) | - |
| HB3 | Extend the universe (new listings, delistings, renames via ISIN) | planned | - |
| HB4 | Native nightly jobs on the VPS + admin progress page (symbols done / pending / last error) | planned - reads `history_import_files` | - |

## HB1 - daily NSE bhavcopy backfill and the nightly EOD job

Code: `app/market_lake/bhavcopy.py` (parsers, file names), `nse_fetch.py` (polite fetcher), `bhavcopy_backfill.py` (the
job, the nightly run, uploads, progress, gap report, CLI), `corporate_actions.py` (CSV loader for B4), `by_isin.py`
(ISIN-stitched reads, universe on a day), `app/market_data/holiday_import.py` (past holiday lists). Migration
`c5e7a9b1d3f4` (revises HB0's `a8c0e2f4b6d8`): `history_backfill_files`, `md_isin_symbols`. Flag `history_backfill`
(off; in `FEATURE_FLAGS` and `DEFAULT_OFF_FLAGS`): while it is off the CLI, the nightly job and the admin uploads all
refuse and nothing contacts NSE.

### What is loaded
Three free public files per trade date from the NSE archive (`nsearchives.nseindia.com`):

| Segment | File (until 5 Jul 2024 / from 8 Jul 2024) | Lake key, source | Notes |
|---|---|---|---|
| `cm` | `cm<DD><MON><YYYY>bhav.csv.zip` / UDiFF `BhavCopy_NSE_CM_0_0_0_<YYYYMMDD>_F_0000.csv.zip` | `NSE:<SYMBOL>`, `nse_bhav:cm` | every equity that traded: series `HISTORY_BACKFILL_SERIES` (default EQ, BE, BZ, SM, ST, SZ - main board, trade-for-trade, SME; ETFs trade as EQ); a symbol in two series keeps the first listed |
| `index` | `ind_close_all_<DDMMYYYY>.csv` | `NSE_INDEX:<U1 index code>`, `nse_bhav:index` | also written to U1-c `index_eod` (with P/E, P/B, yield); an index published with a close only goes to `index_eod` but gets no lake bar (no invented open/high/low) |
| `fo` | `fo<DD><MON><YYYY>bhav.csv.zip` / UDiFF `BhavCopy_NSE_FO_0_0_0_<YYYYMMDD>_F_0000.csv.zip` | `NFO:<SYMBOL>:FUT:<expiry>` (options: `NFO:<SYMBOL>:<CE/PE>:<expiry>:<strike>`), `nse_bhav:fo` | futures with OI; options only with `--with-options` / `HISTORY_BACKFILL_FO_OPTIONS` (tens of thousands of rows a day); untraded contracts are left out |

Formats are recognised by the header, not the date, so a manual upload of any vintage works: the legacy cash file
(with or without the later `TOTALTRADES` / `ISIN` columns, trailing commas), the full bhavcopy
`sec_bhavdata_full_<DDMMYYYY>.csv` (manual upload; no ISIN), the July-2024 UDiFF layout (one layout for every segment,
told apart by `Sgmt`), legacy and UDiFF F&O, the all-indices file. The fetcher asks for the format NSE published on
that date first and the other name second. Bars: timeframe `day`, END = 15:30 IST of the trade date (10:00 UTC - the
same date in UTC and IST, as HB0's open question 2 recommended), OHLC and volume as printed (**unadjusted**), OI for
F&O. Written with B1's rules (`write_candles_bulk`, now one read per chunk of instruments): an identical bar is skipped,
a changed one becomes the next version, `ingested_at` = the load time.

Usage: the shared lake, Tier C - EOD screeners, fundamentals joins (by symbol, or by ISIN through `md_isin_symbols`),
backtests and daily charts read it through `GET /api/market-data/history` (`timeframe=day`, `sources=nse_bhav:cm`
when an R2 daily copy of the same symbol also exists).

### Universe as of a day (U1) and symbol changes
The cash file of a day lists every security that traded that day, so the lake is survivorship-free by construction:
names delisted or suspended later keep every bar they had (`by_isin.universe_on(day)` = the instruments in that
day's file). Bars are stored under the symbol printed that day. `md_isin_symbols` records, per ISIN, each symbol it
traded under with the first and last trade date seen (kept apart from U1's `symbol_history`, which is derived from the
exchange's symbol-change file, so neither rewrites the other). `by_isin.isin_candles(isin, ...)` stitches one
company's history across renames - each symbol's bars only inside that ISIN's own range, so a symbol later reused by
another company does not leak in - and adjusts it with the actions of every symbol it used. Older cash files without an
ISIN column load bars but add no ISIN ranges (noted on the file).

### Adjusted series (B4) and corporate actions
Stored prices stay unadjusted; B4 adjusts at read time with `md_corporate_actions` as known at `as_of`: splits and
bonuses (prices x old/new, volume / factor), **rights** (new in HB1: theoretical ex-rights factor
(old x P + new x issue price) / ((old + new) x P) with P the last close before the ex-date; prices only; no adjustment
when the issue price is not below P) and dividends when `LAKE_ADJUST_DIVIDENDS` is on.
**There is no automatic corporate-action source yet** (open question 1). The hook is a CSV the owner loads (Admin >
History backfill > corporate actions, or the CLI), in either ATP's layout (`symbol` or `isin`, `ex_date`, `action`,
`ratio_new`, `ratio_old`, `amount`) or the exchange's corporate-actions export (`SYMBOL`, `PURPOSE`, `FACE VALUE`,
`EX-DATE`): "Bonus a:b", "Face Value Split ... From Rs X ... To Rs Y" (and consolidations), "Rights a:b @ Premium Rs p"
(issue price = face value + premium) and "Dividend - Rs x" / "x%" (several on one ex-date add up) are read; a purpose
that names an adjusting action but cannot be read exactly (demerger, scheme of arrangement, capital reduction, rights
without a price, ...) is listed in the response, never guessed. Loads follow B1 (same action again = unchanged, changed =
next version).

### Holidays, gaps and quality gates (B3)
- **Past holiday lists:** only the current year is seeded. `holidays` (CLI) or Admin > History backfill > holidays loads
  the exchange's own lists for 2016-2025 from a CSV (`date` + `description`; the circular's "Sr. No, Date, Day,
  Description" layout works). Dates are never invented: a year without a list keeps "gap check skipped" notes.
- **Missing file:** a 404 on a weekend is "no regular session"; on a weekday of a year without a calendar the note says
  the gap check was skipped; on a listed holiday it is "exchange holiday"; on a trading day before today it is a `gap`
  event (`NSE_BHAV:CM|INDEX|FO`). Today's 404 is "not published yet" and is asked again by the next run. Holidays are
  still asked for (Muhurat sessions on Diwali publish files).
- **Per instrument:** `gaps` (CLI) reports, for each cash instrument, trading days whose file we hold but where it has no
  bar (no trade or suspended) as `gap` events. Nothing is ever filled.
- **Per file:** identical repeated rows -> `duplicate`; a row without a usable price -> `invalid` (left out); spikes and
  impossible OHLC against the instrument's previous day (not for options); two different rows for one instrument, mixed
  trade dates, a file of another segment or for another date -> the file is **refused** (nothing written); a `cm` or
  `index` file with fewer rows than `HISTORY_BACKFILL_MIN_ROWS_RATIO` (0.5) of the median of the last done days is
  refused as a likely truncated download (an upload with `force` loads it after the owner has checked it). After the
  write, the lake is counted for the day and must hold every instrument of the file.

### Politeness, checksums, resume
`PoliteFetcher`: browser-like headers with the exchange's site as referer (`HISTORY_BACKFILL_USER_AGENT` overrides the
agent), one request at a time at least `HISTORY_BACKFILL_PAUSE_SECONDS` (1.5) apart, exponential backoff 2, 4, 8 ... s
(capped at `HISTORY_BACKFILL_MAX_BACKOFF_SECONDS`, `Retry-After` honoured) on 429 / 403 / 408 / 5xx / timeouts,
`HISTORY_BACKFILL_RETRIES` (4) attempts per URL, 64 MB cap; a 404 is an answer and is not retried. A 403 that survives
the retries marks the day `blocked` - the job moves on and the owner uploads that file by hand. The SHA-256 of every
file as received is stored on its `history_backfill_files` row; `HISTORY_BACKFILL_ARCHIVE_DIR` keeps the raw files
(`{segment}/{yyyy}/{name}`), and `--local-dir` replays such a folder (or any bulk download) without the network.
One row per (segment, day): written and marked `done` in one transaction, so an interrupted run leaves at most one
`running` row with nothing written, retried next time; `done` days are never fetched again; `blocked` / `refused` /
`failed` are retried; `no_file` is asked again while recent (3 days) or with `--retry-missing`. One run at a time per
database (advisory lock `0x48420002`). Days are processed oldest first (spike checks look back).

### Nightly EOD job
The trading worker starts it once per IST day from `HISTORY_BACKFILL_HOUR_IST` (19:00), only while the flag is on, in a
background task (a slow archive never delays a trading cycle): today's three files plus a catch-up of the last
`HISTORY_BACKFILL_CATCHUP_DAYS` (7) days, weekends included (special sessions), with the same rules - so a late or
blocked file is picked up the next night. A second replica or a manual run in progress is kept out by the lock.

### Admin (SUPER_ADMIN with an MFA session, audit-logged)
`GET /api/admin/history-backfill/progress` (per segment: files by status, first / last day done, rows; the files that
need attention), `POST .../upload` (segment, trade_date, file, optional `force`: the manual fallback when a file is
blocked, or a correction), `POST .../corporate-actions`, `POST .../holidays`. Uploads need the flag on. (The admin page
itself is HB4's.)

### Operator steps (on the VPS; the session never ran these against NSE)
1. Deploy, `alembic upgrade head` (migration `c5e7a9b1d3f4`). Confirm the archive's terms (docs/DATA_SOURCES.md).
2. Admin > Controls: turn `history_backfill` on.
3. Past holidays (copy the CSV into the container first: `docker compose cp nse_holidays_2016_2025.csv backend:/tmp/`):
   `docker compose exec backend python -m app.market_lake.bhavcopy_backfill holidays /tmp/nse_holidays_2016_2025.csv`
4. Start small, then the full range (resumable; re-run the same command to continue):
   `docker compose exec backend python -m app.market_lake.bhavcopy_backfill run --from 2025-12-01 --to 2025-12-31`
   `docker compose exec backend python -m app.market_lake.bhavcopy_backfill run --from 2016-01-01 --to 2025-12-31`
   (about 2,500 trading days x 3 files; at 1.5 s per request expect roughly 3-4 hours plus holidays and retries.)
5. Check `GET /api/admin/history-backfill/progress`; upload any `blocked` day's file there (or put the files in a folder
   and run `run --from ... --to ... --local-dir /path`).
6. Corporate actions: `... bhavcopy_backfill corporate-actions /path/CF-CA-equities.csv`; read the `unreadable` list.
7. Gaps: `... bhavcopy_backfill gaps --from 2016-01-01 --to 2025-12-31`.
8. Leave the flag on for the nightly job (19:00 IST), or run `... bhavcopy_backfill nightly` by hand.
Exit codes: 0 all done, 1 some files blocked / refused / failed, 2 refused to start (flag off, another run).

### Tests (`backend/tests/test_hb1_bhavcopy_backfill.py`, no network)
Fixture files in every format (legacy cash with and without ISIN, the full bhavcopy, the July-2024 UDiFF cash file,
legacy and UDiFF F&O, all-indices; zipped and plain) parse to the same rows; refusals (mixed dates, conflicting rows,
wrong segment); idempotent re-run (no requests, nothing new) and a corrected upload (one new version); resume after the
process dies mid-run; the July-2024 switch keeps one series per symbol and ISIN; split + bonus from the exchange's
corporate-action export and a rights issue adjust correctly while the unadjusted series is kept; symbol change stitched
by ISIN, a delisted name kept in its days; holiday CSV loader, gap events on a missing trading day, "gap check skipped"
without a calendar, per-instrument gaps; short-file gate and `force`; rate-limit backoff with `Retry-After`, pacing, a
blocked file uploaded by hand; the flag gates everything; the nightly job's catch-up and the worker's gate; admin
endpoints (SUPER_ADMIN only, flag-gated) and the daily bars readable by any user through the history API.

### Open questions for the owner (HB1)
1. **Corporate actions source**: none is automated. Options: the exchange's corporate-actions export uploaded
   periodically (supported now), or a vendor feed. Demergers and schemes need a factor entered by hand (ATP layout).
2. **Holiday lists 2016-2025**: please provide the exchange's lists (CSV) - until then those years say "gap check skipped".
3. **Archive terms**: confirm the NSE archive's terms allow this use before the flag is turned on (same caveat as U1).
4. **F&O options**: load them (about 50-90k rows a day in recent years, roughly 100M rows over ten years) or futures only?
   Default: futures only. Also: the UDiFF F&O `TtlTradgVol` and the legacy `CONTRACTS` may not be the same unit
   (contracts vs quantity) - to be confirmed on the first real files; volume is stored as printed.
5. **Series**: EQ, BE, BZ, SM, ST, SZ by default. Add or drop any (e.g. IL, RR, GB / GS government securities are out)?
6. **Nightly hour**: 19:00 IST with a 7-day catch-up. Earlier if NSE reliably publishes by 18:00?

## Licensing and scope rule
- **Daily** bars (HB1's bhavcopies and the optional R2 daily folders) are exchange end-of-day data: they go to the shared lake (`md_candles`, timeframe `day`) and every
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
  - the importer loads 1-minute data only into `HISTORY_OWNER_TENANT_ID` and refuses to load it when that is unset, or
    when `HISTORY_OWNER_TENANT_NAME` does not equal that tenant's name (a confirmation: a mistyped id cannot send
    licensed data to another customer). The skip-if-done test compares the tenant too, so a corrected run re-imports into
    the right tenant, and `--purge-tenant ID --confirm-name NAME` removes a wrong tenant's imported rows, events and
    state rows;
  - a private-store read counts the 1-minute rows first and refuses more than `LAKE_TENANT_MAX_BASE_ROWS` (60,000), so
    a 60-minute request cannot pull 60 x 5,000 rows into memory;
  - downgrading the migration deletes the tenant-scoped quality events before it drops their `tenant_id` column (they
    would otherwise read as shared-lake events).

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
`volume`, `oi` (nullable), `isin`, `source`, `fetched_at`. Naive timestamps are read as UTC. A bar belongs to the
trade date `ingest.trade_date` = the IST date of (END - 1 microsecond) - one rule shared by the importer, the gap
check (B3) and the adjustment (B4).

Labels are checked, never shifted (review fix): a **daily** file must hold at most one bar per trade date, every END at
**15:30 IST = 10:00 UTC** (the same END HB1 writes) and no Saturday / Sunday - so intraday bars put in a daily folder
are refused and can never reach the shared lake as `day` bars (a special weekend session comes from HB1's exchange
files instead). A **1-minute** file's ENDs must be whole minutes inside the session (09:16 .. 15:30 IST): a
START-labelled file (first bar "ending" 09:15) is refused, since it would shift every resampled bar. A NaN / negative
volume refuses the file; a NaN OI is read as missing.

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
   A file already `done` stays `done` if its manifest later refuses it (the reason goes to its error field).
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
- `HISTORY_OWNER_TENANT_ID` - the owner's tenant id, and `HISTORY_OWNER_TENANT_NAME` - that tenant's exact name (only
  for the 1-minute import).

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
4. Corporate actions: the adjusted view needs `md_corporate_actions` filled for the 10 years. HB1 adds the CSV loader;
   the source is still to confirm (see HB1 open question 1).
5. Holiday calendar: daily gap checks need `market_holidays` for 2016-2025 (only 2026 is seeded). HB1 adds the CSV
   loader; until the lists are loaded those years say "gap check skipped".
6. Should users of the owner's tenant other than the owner (team members) see the 1-minute store? HB0 scopes it to
   the tenant, as the spec says; a narrower per-user scope would need a further check.
