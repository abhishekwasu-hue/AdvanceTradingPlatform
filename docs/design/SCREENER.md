# Part S - Screener: design note and plan

Source: `docs/specs/ATP_SCREENER_SPEC.md` (Abhi, v2; it replaces v1). The MASTER SPEC working rules apply:
- don't stop; open questions get a provisional answer and work continues;
- draft PRs only, no merge, no LIVE, a test with every change;
- no hardcoded dates or prices;
- ADR-0004 (exits never blocked) and ADR-0006 (AI acts only on human approval) stand.

The spec numbers its build steps S1..S10. In code, docs and PR titles they keep those names. "Part S" does not clash
with any MASTER part.

## What exists today (main fd217a4) and what each piece becomes
| Today | Where | Becomes |
|---|---|---|
| Market Scanner: indicator conditions (the strategy DSL `Condition`), structure filters (trend, BOS/CHoCH, candle pattern, near S/R), option filters (PCR, bias, near max pain); AND only | `app/scanner/engine.py`, `models.py` (744 lines with the AI part) | The first Filters in the S1 registry. `POST /api/scanner/run` stays: its request is translated into a ScreenQL AST and run by the new executor. A parity test checks old and new results on the same bars. |
| AI scanner: plain language -> `ScanPlan`; explain a result | `app/scanner/ai.py`, `routes.py` | S9: the Copilot tools `build_screen` / `explain_results` produce and explain the same AST. The output filter (H-C1 c) applies. |
| **Scanner runs on candles posted by the browser** | `ScannerSymbolInput.candles` | The same G2 problem the Copilot had (H-C1 a). Screens run on server data only: the lake (B5 `history`) or the tenant's broker session. Posted candles are accepted for one release as a sample run, then removed. |
| Price action: swings, structure, zones, patterns, level strength, breaks, causal swings (trade-repo port) | `app/price_action`, `app/support_resistance` | Factors and Filters for category A (S5). The engine fixtures (BANKNIFTY) are the validation set; the NIFTY holdout stays sealed. |
| Option chain analysis, Greeks, F&O ban (D7), instrument master (F1) | `app/option_chain`, `app/compliance/fo_limits.py`, `app/instruments` | Category C factors; universe classifiers. |
| Fundamentals (Phase AH ingestion, scores) | `app/fundamentals` | Category D factors, read point-in-time by announcement date (S2 adds the announcement-date column where it is missing). |
| Market lake: `md_*` tables with `ingested_at` and `version`, as-of reads, quality events, adjusted view, history API | part B, PRs #96-#102 | The data layer of every screen. DuckDB over the Parquet cold tier (ADR-0013) is also the screener's columnar store. |
| Alerts: channels (Telegram, email, webhook HMAC, web push), delivery outbox, worker drain; in-app notifications | `app/alerts`, `app/notifications`, `alert_channels`, `alert_deliveries`, `notifications` | The Notification Service (ADR-0022) grows out of these tables and the dispatcher. It is not a second system. Risk-engine alerts already use the outbox. |
| Rate limits, Redis | `app/core/rate_limit.py` (now with weighted units, H-C1 b) | Screen and alert quotas per plan. |
| Trial ledger and deflated results | part C5 / H-C3 (planned) | Screen fitness (S6) and screen backtests record their trials in the same ledger. |
| Execution models (slippage, fill) | part C2 (#100) | Rolling screen backtest costs (S6). |

## Engine (ADR-0021)
- **Primitives.**
  - A Factor maps (asset, time) to a number.
  - A Filter maps (asset, time) to a bool.
  - A Classifier maps (asset, time) to a category.
  - Every primitive declares its inputs, `window_length`, timeframe, `as_of` rule and a cost estimate.
  - The field registry (`app/screener/fields/`) is the single list of what a screen can use. The visual builder, the
    autocomplete and the Copilot all read it.
- **ScreenQL (`screenql/1`).**
  - Pipeline: text -> tokens -> typed AST -> validator -> plan -> executor.
  - The validator checks:
    - types and units;
    - timeframe compatibility;
    - offsets;
    - look-ahead: a `[−n]` offset or an `@1D` field read intraday before the close is refused;
    - the cost cap.
  - The AST is versioned and serialisable (JSON). The visual builder, the query text and the Copilot's output are three
    views of one AST. Round-trip parity tests: text -> AST -> text, and builder JSON -> AST -> builder JSON.
  - The parser is hand-written (recursive descent, about 400 lines). There is no grammar-generator dependency, and
    error messages carry positions (open question SC-3).
- **Execution modes.**
  - EOD batch: vectorised over the universe on the columnar store.
  - Bar-close incremental: only finalised bars (the B2 `CandleBuilder` publishes a bar after its close).
  - Live: ticks become 1-minute aggregates in memory; only those aggregates are persisted (B2 tick writer).
  - Historical `as_of`: point-in-time.
  - Rule for every mode: cheap universe filters run first and expensive factors last.
  - Result cache key: hash(AST, universe snapshot id, as_of, timeframe). TTL: 15-30 s for live, until the next bar for
    EOD.
- **Universe.** Index membership, F&O list, lot sizes, price bands, ASM/GSM and sector are all as-of. Delisted names stay
  in historical runs (survivorship).
- **Reproducibility.** A run stores the AST version, the universe snapshot id and the data version (lake `version` max).
  The same three values replay the same result.

## Notification Service (ADR-0022)
- **Pipeline.** `alert_rules` -> `alert_events`, with idempotency key (rule, symbol, condition hash, bar time) kept in
  Redis with a TTL -> `deliveries` per channel. This is the outbox that already exists, extended with priority, group
  id and digest bucket.
- **Delivery.** At-least-once; consumers are idempotent. Status is queued / sent / delivered / failed / suppressed, with
  reason codes.
- **Throttling.** Cooldown per (rule, symbol). Per-user, per-channel hourly cap by plan. Burst grouping: one message
  lists the N symbols that entered in the same bar. Digest mode: hourly or EOD.
- **Delivery windows.** Quiet hours per user, in the user's timezone. Optional suppression while the market is closed.
  Stale data suppresses every alert and raises one system alert (the chaos test).
- **Channels.** In-app (existing), Telegram (existing, plus per-user linking by deep-link code), email (existing seam,
  plus unsubscribe link and bounce handling), webhook (existing HMAC, plus timestamp replay window, schema version, dead
  letter, and an optional Chartink-shaped payload), web push (existing VAPID). Mobile push and SMS/WhatsApp are seams
  only.
- **Content.** Templates per channel, English by default. The output filter (H-C1 c) runs on every rendered text.
- **What an alert can do.** It never places an order. A hand-off to a strategy goes through the monitor's proposal state
  machine (ADR-0006).

## Order of work (each step one draft PR, at most about 800 lines; big steps split)
| Step | Content | Depends on | Split |
|---|---|---|---|
| S0 | This note, ADR-0021, ADR-0022, spec stored, OPEN_QUESTIONS | - | 1 PR |
| S1 | ScreenQL parser/AST/validator; Factor/Filter/Classifier runtime; field registry; scanner filters migrated; `/api/scanner/run` on the new executor (parity) | - | S1a grammar+AST+validator, S1b runtime+registry, S1c scanner migration+API, S1d builder <-> text parity (frontend) |
| S2 | Instrument-master history (addendum U1, below), NSE EOD ingest jobs, quality gates, freshness, DuckDB/Parquet columnar store | B1-B5 (#96-#102) | U1-a..U1-e, then one PR per source family |
| S3 | Notification Service on the existing outbox; management UI; delivery log | - | S3a model+rules+dedupe, S3b channels, S3c UI |
| S4 | Screen and instrument alerts, bar-close engine, live stream, result cache | S1, S3, B2 | 2 PRs |
| S5 | Categories A, C, F, G; F&O dashboard, RRG, breadth, heatmap | S1, S2 | A first (price action), then C, F, G |
| S6 | History as-of, rolling backtest, trial ledger, screen fitness | S1, S2, C2, C5 | 2 PRs |
| S7 | Ranking systems; categories B, D | S1, S2 | 2 PRs |
| S8 | Categories E, H, I | S2, v1.2/v1.3 | |
| S9 | Copilot tools + natural-language box | S1, H-C2 | |
| S10 | ETF/MF, SMS/WhatsApp, mobile push, marketplace | later | |

**Place in the MASTER order** (SC-1, provisional):
- S0 is now.
- S1 and S3 come straight after H-C1. They need no new data: S1 runs on the lake/broker history that exists, and S3
  extends existing tables.
- S2 and later follow part B's merge, because they build on the lake.
- The Copilot order (H-C2 ...) continues in parallel. Only one heavy job (a full suite) runs at a time.

## Addendum U1 - full NSE universe, sectors and indices (the concrete version of spec §1.2, inside S2)
Source: `docs/specs/ATP_NSE_UNIVERSE_ADDENDUM.md`.

**Today.** `app/instruments/master.py` stores only the broker's tradable-instrument dump (`instruments`, replaced
wholesale each day). Part B1 (#96) adds `instrument_master_versions`: contract terms such as lot and tick per contract
over time.

**What U1 adds.** As-of reference data. The broker master stays the "tradability" layer, joined by ISIN or symbol.

| Table | Key | Notes |
|---|---|---|
| `securities` | isin | current symbol, name, series, listing/delisting date, face value, exchange, status, SME / ETF flags |
| `symbol_history` | isin, valid_from | renames and symbol changes; continuity by ISIN |
| `classifications` | isin, scheme, valid_from | NSE 4 levels (macro sector -> sector -> industry -> basic industry); a GICS-style mapping seam |
| `indices` | index_code | name, family, base date, method, broker symbol mapping, derivatives flag |
| `index_membership` | index_code, isin, valid_from | weight where published; rebalances close the old range and open a new one, never overwrite |
| `mcap_buckets` | isin, valid_from | AMFI large / mid / small, half-yearly |
| `fo_membership` | isin, valid_from | underlying-level market lot (`fo_mktlots.csv`). Contract-level terms stay in `instrument_master_versions`; a reconciliation test checks the two agree |
| `fo_ban_history`, `surveillance_flags` (ASM/GSM), `price_band_history` | isin/underlying, date or range | the official lists; D7's computed ban status stays and is checked against `fo_secban.csv` |
| `index_eod` | index_code, date | OHLC plus PE/PB/dividend yield where published |

Every row carries `source`, `fetched_at` and `checksum`. Ranges are half-open `[valid_from, valid_to)`; an open range
has `valid_to = NULL`.

**Sources.** Published NSE / NSE Indices / AMFI download files, behind a `ReferenceSource` seam:
- fetching is polite (headers, caching, retry, checksum);
- a manual upload in the admin UI is the fallback when a site blocks;
- each file is listed in `docs/DATA_SOURCES.md` before its job is switched on;
- BSE's scrip master is a later adapter on the same tables.

**Jobs.**
- They run after the close, from the worker. They are idempotent: a re-run adds no duplicate ranges. They are
  schema-versioned and pass the quality gates.
- Cadence:
  - daily: equity list, symbol changes, constituents (the diff appends ranges and raises a rebalance alert), index
    bhavcopy, lots and ban list;
  - when AMFI publishes: the market-cap buckets;
  - nightly: reconciliation against the broker master, with a mismatch report.
- Backfill uses only the historical files that exist. Gaps are documented and history is never fabricated.

**Universe picker and classifiers.**
- The picker selects by index, sector, industry, market-cap bucket, F&O membership, series and the SME/ETF toggles.
  It supports custom lists, set algebra (NIFTY 500 ∩ F&O − banks), saved universes, and an as-of universe for
  historical runs.
- ScreenQL classifiers: `Sector()`, `Industry()`, `IndexMember("NIFTY 200")`, `McapBucket()`, `IsFnO()`.

**UI.**
- Markets -> "NSE Universe" page: counts, freshness, the last rebalance diff, the reconciliation report and manual
  upload.
- The symbol page shows memberships and classification history.
- The index page shows constituents with weights and a sector heatmap.

**Tests** (fixture CSVs for every source; no network):
- As-of membership: a stock that left an index is out after its exit date and in before it.
- Rename continuity by ISIN.
- A delisted stock is kept in historical universes.
- The reconciliation report.
- Job idempotency.

**Order.**

| Step | Content |
|---|---|
| U1-a | tables + migrations + equity list + symbol history |
| U1-b | index master + constituents + classification + as-of membership |
| U1-c | index bhavcopy + `index_eod` + lots / ban / AMFI |
| U1-d | universe picker + ScreenQL classifiers + UI page |
| U1-e | backfill + reconciliation report |

U1-a to U1-c need no ScreenQL, so they start now. They are stacked on the part B chain (#102), so the Alembic history
stays linear. U1-d waits for S1.

**Open questions (provisional).**
- **U1-Q1. ISIN as the identity.** Instruments without an ISIN (indices) use `index_code`. A security whose ISIN
  changes (a rare corporate event) is linked through `symbol_history` plus a manual mapping, never guessed.
- **U1-Q3. The constituent files' `Industry` column.** It is NSE's sector level; provisional mapping.
- **U1-Q4. Effective date of a rebalance.** A membership change gets the day the job saw the new file. NSE announces
  rebalances with an effective date. A file published the evening before that date therefore lands a day early or
  late. Provisional: accept the observation day and flag it in the rebalance report. U1-e reads the announcement's
  effective date where a published file carries it.
- **U1-Q2. Industry levels the constituent files lack.** Use the NSE quote API `industryInfo` seam only if its terms
  allow; otherwise a manual CSV. Until then the level stays empty, never inferred.

## Performance targets (CI perf job, synthetic universe, no hardcoded prices)
- EOD screen, 50 factors over 2,500 symbols: under 3 s; under 200 ms when cached.
- Bar-close scan over the F&O universe (about 200 symbols): under 1 s after the bar finalises.
- Live alert handoff, tick to delivery queued: under 2 s at p95.

The perf job uses generated bars (seeded) and reports the timings as metrics. The thresholds are config values in the
job, never tuned against results.

## Compliance (spec §6)
- **Wording.** Results are "matches" and "passed filters". UI strings are checked by the existing string lint. Server
  text is checked by the output filter. The word list for screens adds "target" and "buy/sell signal" to the H-C1 c
  list.
- **Disclaimer.** Every screen page and every alert footer carries the disclaimer.
- **Publishing.** Published screens (marketplace, later) carry a research-analyst-boundary flag. Publishing is Abhi's
  business decision.

## S1a (built): ScreenQL grammar, AST and validator
- **Package.** `app/screener/` holds `nodes` (the AST, its JSON wire form for the builder, the canonical text),
  `parser` (hand-written recursive descent), `registry` (6 fields and 24 functions: Factors with units, Filters,
  Classifiers) and `validator`. `compile_screen(text | builder JSON)` returns `(ast, validation)` with one error shape.
- **Grammar `screenql/1`.**
  - OR / AND / NOT; comparisons, BETWEEN … AND …, IN (literals); + - * /; unary minus.
  - `field[n]@tf` and `Fn(args, key=value)[n]@tf`. The offset comes before the timeframe.
  - `$parameters`; `ALL(...)` / `ANY(...)` as the builder's group forms.
  - Keywords are case-insensitive. Registry names are case-sensitive.
  - Text is capped at 4,000 characters and nesting at 60 levels.
- **Validator.** It reports every problem at once, each with its character position.
  - Types and units: `close > RSI(14)` is refused (price vs index); a literal or parameter takes the other side's unit.
  - Names and arity. Windows must be whole numbers from 1 to 500, as a literal or a parameter. Missing and unknown
    parameters are refused.
  - Timeframes: a field finer than the screen's base timeframe is refused.
  - Look-ahead: a negative offset is refused, both in the text and in a builder tree.
  - Offsets are capped at 500.
  - Cost: a per-tenant cap (default 200); cross-sectional functions cost more.
  - The validation also carries the plan inputs: the timeframes used and the bars needed per timeframe
    (offset + window).
  - `closed_bars_only` is always true; the S1b runtime enforces it.
- **Tests.** `tests/test_s1a_screenql.py` (16):
  - text and JSON round trips;
  - builder and text parity;
  - a seeded fuzz: 600 random trees round-trip through text, and 3,000 garbage strings only ever raise
    `ScreenQLSyntaxError`;
  - error positions;
  - offsets and mixed timeframes;
  - look-ahead;
  - units, parameters and cost.
- `app/screener` is added to the mypy gate, and it is clean.
- **Next.** S1b: the runtime over the registry (pandas on server bars, closed bars only), with one fixture test per
  entry.

## S1b (built): the ScreenQL runtime
- **Module.** `app/screener/runtime.py` evaluates a validated AST with one implementation per registry entry. Indicators
  come from `app/indicators`, so the values are identical to the strategies'.
- **Input.** `SymbolData`: closed bars per timeframe, plus sector, industry, market-cap bucket, F&O flag and index
  memberships, all as of the run date.
- **Higher timeframes.**
  - A coarser timeframe is computed on its own bars and shifted by its own offset.
  - It is aligned to the screen's bars by close time, so a 1d value appears only on bars that close after that day
    closes. The test shows day 1's close is invisible while day 1 trades, and day 2 sees only day 1.
  - A missing coarser frame is resampled from the base bars, and a trailing incomplete bucket is dropped.
  - Either `[n]@tf` or `@tf[n]` is accepted; `[n]@tf` is canonical.
- **Cross-sectional.** Rank (1 = highest) and PercentileRank (optionally `by=` a classifier) run over the universe's
  last bars, innermost first.
- **NaN never matches.** Short history, x/0 and missing reference data all produce no match. `run_screen` reports
  "not enough history" per symbol, using the validator's lookback.
- **Tests.** `tests/test_s1b_screen_runtime.py` (10). There is a hand-computed fixture per entry, and a guard test
  fails if a registry entry has no runtime test.
- **Next.** S1c: `/api/scanner/run` translated onto ScreenQL with a parity test, saved screens and runs, and the API.

## S1c (built): the Market Scanner on ScreenQL
- **Registry.** 21 entries are added, so the scanner's filters are the first registry entries.
  - The Strategy Builder indicators: PlusDI, MinusDI, Supertrend, BBUpper/Mid/Lower, DayOpen, PDH/PDL/PDC, ORHigh/ORLow.
    They run on the Strategy Builder's own `Operand` code.
  - The structure filters: Trend, StructureEvent, PatternBullish/Bearish, NearSupport/Resistance.
  - The option filters: PCR, ChainBias, MaxPainDistancePct.
  - VWAP now uses the Strategy Builder's IST-session VWAP. Intraday buckets start at the exchange open.
- **Translator.** `app/scanner/screenql.py`: `to_screen(request)` turns the indicator conditions, structure filters and
  option filters into one AND screen. `run_scanner_screenql` returns the same `ScannerResult`. The match decision is
  the screen's; the labels use the legacy wording.
- **Switch.** `SCANNER_ENGINE=legacy|screenql` selects the engine, with legacy as the default; the response is
  identical.
- **Lenient units for translated scans only.** `strict_units=False` applies only to translated legacy scans: the old
  filters never checked units, and parity comes first. User-written screens are always strict.
- **Tests.** `tests/test_s1c_scanner_screenql.py`:
  - every legacy scanner test is re-run on the new engine;
  - a seeded parity fuzz runs 120 random requests over 6 symbols and 3 sessions, using all 24 indicators, 4
    timeframes, the structure and option filters, and chains on half the symbols, and requires identical matches;
  - the translation text;
  - the API switch.
  - The fuzz found one real difference: the structure event is "CHoCH" in the engine and "CHOCH" in the filter
    enum. It is normalised.

## S1d (built): screener API, saved screens and runs
- **Flag.** `/api/screener/*` sits behind `screener_v2`, which is off by default.
- **Endpoints.**
  - `registry`: the builder palette.
  - `validate`: text or builder tree in; `ok`, problems with positions, canonical text, AST and plan out. Nothing runs.
  - `screens`: CRUD. A screen is validated before it is saved, it is tenant-scoped, and it is archived rather than
    deleted.
  - `run`: a saved screen or ad-hoc source over up to 50 symbols.
  - `runs/{id}`.
- **Data.** A run reads bars through the organisation's broker session (the lake after part B). Without a session the
  answer is 409. Weekly and monthly bars are resampled from server daily bars. One symbol's fetch error is reported
  and the rest of the run continues.
- **Storage.** `screens` and `screen_runs` (migration `c5e7a9b1d3f5`, checked on Postgres: upgrade, check, downgrade,
  upgrade). A run stores the AST hash and version, the universe, the data source and the per-symbol results.
- **Wording and limits.** Results are "matches" and every run carries a disclaimer. `SCREENER_COST_CAP` (default 200)
  is the per-screen cost cap.
- **Tests.** `tests/test_s1d_screener_api.py` (5).
- **Next.**
  - S1e: the builder ⇄ text parity in the frontend (S1d in the spec's numbering).
  - U1-d: the universe picker and the classifiers fed from U1 tables.

## Open questions (provisional answers taken, work continues)
- **SC-8. `screen_runs` retention.** Provisional: runs are kept, with no retention rule yet, because they make a match
  list reproducible. Whether and when to delete old runs is the owner's decision (§14).
- **SC-7. "day" operands inside intraday scans.** The Strategy Builder's `timeframe="day"` filter resamples to
  375-minute buckets from the first session's open. Across overnight gaps these buckets do not line up with sessions.
  ScreenQL's `@1d` is the session.
  - Provisional: keep exact parity. Such a scan stays on the legacy engine (`NotTranslatable`).
  - Owner question: should the Strategy Builder's "day" also mean the session? That would change existing strategies'
    signals, so it waits for an explicit decision.
- **SC-1. Where does part S sit in the MASTER order?** Provisional: S0 now; S1 and S3 after H-C1; S2 onwards after part
  B merges.
- **SC-2. Columnar store vs the lake.** The lake's Postgres/Timescale `md_*` tables stay the source of truth. DuckDB over
  the Parquet cold tier (ADR-0013) plus derived factor tables form a rebuildable compute cache, never a second truth.
  The ClickHouse seam is documented but not built.
- **SC-3. Parser.** Hand-written recursive descent, so there is no new dependency and errors carry positions. Lark
  would be the alternative if the grammar grows past about 30 productions.
- **SC-4. Paid vendors (TrueData, Global Datafeeds, Accelpix, IBKR).** Adapters behind the B2 `HistoryVendor` / stream
  seams, with contract tests against recorded fixtures. No subscription is bought (spend rule). Abhi chooses.
- **SC-5. NSE archives.** Only official archive files, with polite pacing and a user agent. Each source and its terms go
  in `docs/DATA_SOURCES.md` before its job is switched on (the news-feed rule, ADR-0012).
- **SC-6. DuckDB on the 8 GB host.** It runs in the worker process with a memory cap (`SCREENER_DUCKDB_MEMORY_MB`,
  default 512). Batch screens queue behind the worker lock; one runs at a time.
