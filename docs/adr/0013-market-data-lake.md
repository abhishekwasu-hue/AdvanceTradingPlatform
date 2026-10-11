# ADR-0013: Market data lake - TimescaleDB hot tier + Parquet cold tier behind one query interface

**Date:** 2026-10-10 · **Status:** provisional (spec 0.6: the recommended option, until the owner says otherwise) · **Part:** B0

## Context
Every consumer of history today gets it differently: backtests take candles posted by the client, the worker asks the
broker for candles each cycle (cached in Redis for a minute), option quotes are recorded into `option_chain_snapshots`,
the instrument master is a single current snapshot. Nothing is point-in-time: a backtest cannot ask "what did we know
at 10:15 on that day", corporate actions are not modelled, and a broker outage means no history. Part B wants one store
for ticks, candles, option chains, instrument-master versions, corporate actions and data-quality events, with an
`as_of` on every read, survivable on the 8 GB production host (ADR-0011 successor: Hostinger KVM 2), with PITR backups.

Volume (estimate, India first): one underlying's 1-min bars = 375/day ~ 94k/year; 200 F&O names x 1-min = ~19M rows/year;
L1 ticks for 50 instruments ~ 50-100M rows/year; option chains every minute for 3 indices x ~100 strikes ~ 30M/year.

## Options
| | TimescaleDB (Postgres extension) | Parquet files + DuckDB | ClickHouse / QuestDB |
|---|---|---|---|
| Fit with what runs | same Postgres, same backups/PITR, same SQLAlchemy, same row-level tenancy for tenant data | new storage path; DuckDB in-process | a new server to run, back up, secure |
| Writes (ticks) | good (hypertables, batch inserts) | poor for streaming (needs batching to files) | excellent |
| Analytics over years | good with compression + continuous aggregates | excellent, columnar, cheap on object storage | excellent |
| Point-in-time (`as_of`) | `ingested_at` / `version` columns + query filter | same, by partition + filter | same |
| Cost on 8 GB host | memory shared with Postgres (tune `shared_buffers`) | near zero RAM; object storage cents/GB | 2-4 GB RAM for the server |
| Ops | `CREATE EXTENSION`; image `timescale/timescaledb-ha` | files + a compaction job | new service |

## Decision (provisional)
1. **Hot tier: TimescaleDB** in the existing Postgres (image switched to a Timescale build of the same major version):
   hypertables for `ticks`, `candles`, `option_chain_snapshots`; native compression after 7 days; retention 90 days for
   ticks, 2 years for 1-min candles (config `LAKE_TIERS`).
2. **Cold tier: Parquet** (partitioned by venue / instrument / year-month) on the same S3-compatible store as backups
   (Cloudflare R2, H-2), written by a nightly export of chunks past the hot window; read with **DuckDB** in-process.
3. **One query interface** `app/lake/query.py::history(instrument, tf, from, to, as_of, adjusted)` decides hot vs cold by
   date range and merges; every caller (backtest, research, scanner, worker warm-up) goes through it (B5).
4. **As-of semantics**: every row carries `ingested_at` and `version`; a read with `as_of=T` sees only rows ingested at or
   before T, latest version wins - corrections never rewrite history (B7 test: data arriving later is invisible to an
   earlier `as_of`).
5. **Migration path**: Redis stays the 60-second candle cache in front of the lake; the broker becomes a *source* that
   fills gaps (backfill job) rather than the per-cycle store. Client-posted candles in backtests go behind a deprecation
   flag, then are removed (B5).

## Consequences
- One database to back up (PITR covers the hot tier); the cold tier is files on R2 with their own checksums.
- Postgres memory budget grows: Timescale wants `shared_buffers` ~25 % of its limit; the 8 GB budget (OPERATIONS §1.2b)
  keeps Postgres at 2 GB until measured otherwise - ticks for 50 instruments fit; more needs the next host size.
- DuckDB adds a dependency (pure wheel, no server). Parquet export is a batch job (part E's queue later).
- The schema is venue-neutral from day one (ADR-0017): instrument ids are platform ids mapped to MIC + symbol, times are
  UTC with the venue's timezone kept on the instrument.
- Reversible: the extension and hypertables are added by an Alembic migration with a downgrade that converts back to
  plain tables; the cold tier is additive.
