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
  The broker's intraday feed ends with the bar still forming; `closed_only` drops it, so a run decides on closed
  bars only.
- **Storage.** `screens` and `screen_runs` (migration `c5e7a9b1d3f5`, checked on Postgres: upgrade, check, downgrade,
  upgrade). A run stores the AST hash and version, the universe, the data source and the per-symbol results.
- **Wording and limits.** Results are "matches" and every run carries a disclaimer. `SCREENER_COST_CAP` (default 200)
  is the per-screen cost cap.
- **Tests.** `tests/test_s1d_screener_api.py` (5).
- **Next.**
  - S1e: the builder ⇄ text parity in the frontend (S1d in the spec's numbering).
  - U1-d: the universe picker and the classifiers fed from U1 tables.

## S3a (built): Notification Service - rules, events, dedupe, throttle, grouping, digest
- **Model** (ADR-0022 option 2: the existing outbox grows).
  - `alert_rules`: a screen rule (a saved screen) or an instrument rule (a ScreenQL condition on one symbol, validated;
    cross-sectional conditions refused). Each rule has a priority (critical/normal/low), a cooldown, instant or digest
    mode (hourly or EOD), an expiry, and pause/resume.
  - `alert_events`: unique on `idem_key` (rule, symbol, condition hash, bar time). Every event carries a status
    (pending / sent / suppressed / held) and a reason code.
  - `notification_policies`: per organisation - timezone, quiet hours, hourly cap, grouping window, EOD digest time.
  - `alert_deliveries` gains priority, group_id, digest_bucket and reason_code.
  - Migration `e9a1c3d5f7b9`, checked on Postgres.
- **Engine** (`app/alerts/engine.py`).
  - `record_event`: dedupe on the key, then cooldown (stored as suppressed / "cooldown").
  - `flush` runs in the worker before the outbox drain, isolated from it.
    - Burst grouping: one notification per rule and bar, listing every symbol.
    - Quiet hours in the organisation's timezone, including windows that cross midnight: normal and low events are held
      ("quiet_hours") and critical ones go through.
    - The hourly cap holds the overflow ("rate_cap"); critical events are exempt.
    - Digest rules send once per hourly or EOD bucket.
  - Messages say "matches ... not recommendations". Nothing places an order.
- **API.** `/api/alerts/rules` (CRUD, pause/resume, events log) and `/api/alerts/policy`, behind the `screener_v2`
  flag.
- **Tests.** `tests/test_s3a_notification_service.py` (6). Mutation checks confirmed it: removing quiet hours or
  cooldown fails the tests.
- **Next.**
  - S3b: channel work - Telegram per-user linking, email unsubscribe, a versioned webhook body with a replay window and
    dead letter.
  - S3c: the management UI.
  - S4: rule evaluation at bar close, feeding `record_event`.

## S3b-1 (built): webhook channel - versioned body, Chartink shape, replay window, dead letters

- Body `atp.notification/1` (header `X-ATP-Schema`):
  - the existing fields plus `schema`;
  - a screen alert adds an `alert` block (`atp.alert/1`) with rule id/name, screen id, symbols, the trigger values,
    the bar time, the data timestamp per symbol and the group id. The values are read from `alert_events`, so the
    receiver sees exactly what fired.
- `payload_format: "chartink"` on the channel config sends the Chartink-style body instead (`stocks`,
  `trigger_prices`, `triggered_at`, `scan_name`, `alert_name`, `scan_url`). This lets an existing Chartink receiver
  switch without changes. Header `X-ATP-Schema: chartink/1`.
- Signing is unchanged (`sha256=` HMAC over `<timestamp>.<body>`). `verify_webhook` is the receiver's check:
  signature AND timestamp within `REPLAY_WINDOW_SECONDS` (300 s); a stale, altered or unparseable request is refused.
- Dead letters:
  - after `MAX_ATTEMPTS` a delivery is FAILED with `reason_code = "dead_letter"`; a disabled or removed channel
    gives `channel_disabled`;
  - `GET /api/alerts/dead-letters` lists them (tenant-scoped, with the channel);
  - `POST /api/alerts/deliveries/{id}/retry` requeues a FAILED row (409 otherwise; 404 for another tenant's row).
- Tests: `tests/test_s3b_webhook_channels.py` (5), plus mutation checks on the replay window and the dead-letter
  reason.
- Next (S3b-2): Telegram per-user linking, email unsubscribe link.

## S3b-2 (built): per-user Telegram linking and email unsubscribe

- **Telegram linking.**
  - `POST /api/alerts/telegram/link-code` gives a one-time code; the user sends `/start <code>` to the organisation's
    bot from a private chat. The code is stored only as a hash, expires after 15 minutes, works once, and a new code
    voids the previous one.
  - Refused: group chats (a group would see one user's alerts), unknown, used or expired codes, and another
    organisation's codes. The attempt is rate limited and audited (`telegram_linked` / `telegram_link_refused`).
  - The linked chat gets the screen alerts of rules that user created, as its own outbox row
    (`alert_deliveries.address`), so retries are per chat. The row exists only when the organisation's Telegram
    channel took the notification (same severity floor). A linked chat gains **no** command rights.
  - `GET` / `DELETE /api/alerts/telegram/link` shows the link and unlinks.
- **Email unsubscribe.**
  - Screen-alert mails go to one recipient at a time. When `PUBLIC_BASE_URL` is set, each mail carries its own signed
    link plus `List-Unsubscribe` and `List-Unsubscribe-Post` (RFC 8058 one-click).
  - The link opens a page with a button; only the POST unsubscribes, so a mail scanner that follows links does
    nothing. The token is an HMAC over tenant and address: a forged or tampered token is refused.
  - An opt-out stops screen-alert mails to that address for that organisation only. Risk and system mails still go to
    every address.
  - `GET` / `DELETE /api/alerts/email-opt-outs` lets a trader see opt-outs and re-subscribe an address. Changes are
    audited.
- **Storage.** `notification_links`, `email_opt_outs` and `alert_deliveries.address` (migration `f1b3d5e7a9c1`,
  Postgres round-trip OK).
- **Tests.** `tests/test_s3b2_links_unsubscribe.py` (4), plus 4 mutation checks: private-chat check, linked delivery,
  opt-out filter, single use.
- **Not yet.** Bounce handling needs an inbound mail source and is deferred until one is chosen (SC-9).

## S3c (built): alert-management UI

- **Where.** Notifications page, new tab "Rules & delivery" (the feed stays the first tab). Strings are English, as the
  rest of the dashboard.
- **Rules.**
  - The list shows each rule's status, priority and a one-line description (target, timeframe, delivery, cooldown).
  - Pause / resume.
  - A delivery log per rule shows every firing with a plain-language reason (cooldown, quiet hours, cap, digest).
- **New instrument rule.**
  - Fields: name, symbol, ScreenQL condition, timeframe, priority, cooldown, instant or digest.
  - Client checks mirror the server; the server's validator problems are shown with their column.
- **Delivery policy.** Timezone, quiet hours, messages per hour, burst grouping window, end-of-day digest time.
- **Failed deliveries.** Dead letters with their reason and last error, plus Retry. Viewers simply see none.
- **Your channels.**
  - Telegram link code (the `/start` command to send privately, its expiry), link status, unlink.
  - Email addresses that unsubscribed, with Re-subscribe.
- **Flag off.** With `screener_v2` off the rules part says so; failed deliveries and personal channels still work.
- **Wording.** "Alerts report that a rule's conditions matched. They are not recommendations and never place orders."
- **Tests.** `src/alerts/rules.test.ts` (5) for the helpers: rule description, validator problems, policy and rule
  checks, reason labels. tsc, vitest (57) and build all pass, and the bundle budget holds: the panel is in the lazily
  loaded Notifications chunk.

## S4a (built): the bar-close engine for alert rules

- **When.** Every worker cycle, for each active, unexpired rule of an organisation with `screener_v2` on.
- **Which bar.** `expected_bar` gives the latest fully closed bar on the NSE clock:
  - intraday buckets start at 09:15 and the day's last bucket may be short (1h: 15:15-15:30);
  - a daily bar closes at 15:30 on a trading day;
  - weekends and holidays (the holiday table) are skipped.
- **Closed bars only.** A still-forming bar is trimmed off and never decides anything.
  - A symbol whose broker data has not reached the expected bar is skipped this round, never evaluated on stale
    bars.
  - When no symbol is current the rule waits, retries after `RETRY_SECONDS` (60) and shows why (`last_problem`).
- **Once per bar.** `last_bar_at` marks the evaluated bar, and S3a's idempotency key backs it up.
- **Never holds up trading.** Each cycle gets a 10-second budget (`TIME_BUDGET_SECONDS`), taking the
  least-recently-checked rules first; the rest are still due next cycle. Evaluation and delivery run in separate
  guards, so a rule failure never blocks sending what already fired.
- **What fires.** Each match goes to `record_event` with its trigger values (close, volume, oi when present) and the
  bar time as `as_of`. Grouping, quiet hours, caps and delivery are S3's job.
- **Screen rules.** A screen rule now carries its own symbols (1-50, `universe_json`) and an exchange. A screen that
  is gone or archived is reported on the rule.
- **Daily rules.** A broker's daily history starts at yesterday, so after 15:30 today's bar is built from today's
  15-minute bars.
- **Not yet.** Weekly and monthly rules are listed but not evaluated (SC-10). Live (tick) alerts and the result
  cache are S4b.
- **Storage.** `alert_rules` gains `universe_json`, `exchange`, `last_bar_at`, `last_checked_at` and `last_problem`
  (migration `a3c5e7b9d1f3`, Postgres round-trip OK).
- **Tests.** `tests/test_s4a_barclose.py` (7), plus 3 mutation checks: forming bar, stale data, once per bar.

## S4b-1 (built): the cycle cache - shared fetches and evaluations

- Within one worker cycle (`CycleCache`, on `Outcome.cache`):
  - rules needing the same organisation, exchange, timeframe, closed bar and symbol fetch it once; a rule needing a
    longer lookback than the cached fetch fetches again;
  - the same screen (canonical AST) over the same current symbols, bar and params is evaluated once, and every rule
    still records its own events.
- The cache lives for one cycle only, so nothing from one bar is reused for the next. Counters: `fetched_symbols`,
  `reused_symbols`, `reused_results`.
- **Tests.** `tests/test_s4b_cycle_cache.py` (2), plus 3 mutation checks (no fetch cache, ignored lookback, no result
  cache).

## S4b-2 (built): intrabar alerts, opt-in, default off

- **Opt-in per rule.** `fire_on`: `bar_close` (the default, unchanged) or `intrabar`. Intrabar needs the
  `screener_intrabar` flag, which is **off by default**.
  - The API refuses an intrabar rule while the flag is off (409).
  - If the flag is turned off later, the engine skips the rule and says why (`last_problem`).
- **Which bar.** `forming_bar` gives the start of the bar forming now, inside the session of a trading day only.
  Outside the session nothing is evaluated or fetched.
- **Data.** Server bars with the forming bar kept (`fetch_frames(include_forming=True)`). A base timeframe built by
  resampling keeps its forming bucket (`resample(keep_forming=True)`).
  - Higher timeframes referenced inside a screen still use closed buckets only.
  - Bar-close rules and manual screen runs never see a forming bar.
- **How often.** At most every `RETRY_SECONDS` (60) per rule, by the same pause as bar-close retries.
- **Once per bar and symbol.** This uses S3a's idempotency key, which is per bar. `last_bar_at` is not set, so another
  symbol, or the same one later in the bar, can still fire within that bar. Cooldown applies as before.
- **Events are marked `intrabar: true`.** The bar had not closed, so the condition may no longer hold at the close
  (SC-11).
- **Storage.** `alert_rules.fire_on` (migration `c5e7b9d1f3a5`; Postgres upgrade, check, downgrade and upgrade all OK).
- **Tests.** `tests/test_s4b2_intrabar.py` (5), plus 6 mutation checks: `last_bar_at` set, session end inclusive, no
  flag gate, no intrabar mark, forming bucket dropped, no API gate.

### S4b-2 review follow-up
- **No cached copy for intrabar.** The candle cache keeps a copy for up to 60 s, so an intrabar fetch could miss the
  bar forming now. `get_candles(fresh=True)` skips the cache read for intrabar fetches only. The result is still
  written to the cache; bar-close fetches are unchanged.
- **The message says intrabar.** When a batch holds an intrabar event, the notification says "the bar still forming
  at … (intrabar: it may not hold at the close)" instead of "the bar closing …".
- **Out of session clears the old problem.** Outside the session an intrabar rule is skipped with `last_problem`
  cleared, so a "waiting" note from the day does not stay up overnight.
- **Tests (+3).**
  - The real `fetch_frames` path, with and without the forming bar, at 1m and resampled 3m.
  - `fresh` skips the cache read.
  - The message text, and the cleared problem.
- **Not changed (noted).**
  - Intrabar rules are due every 60 s in session, and broker fetches are per symbol. The 10 s cycle budget is
    checked between rules, so bar-close rules can move to the next cycle; ordering by oldest `last_checked_at` limits
    this.
  - `condition_hash` does not include `fire_on`. No route edits `fire_on` today.

## S5-A1 (built): price action as ScreenQL series

Category A starts with the parts of `app/price_action` that can be computed bar by bar from closed bars only. Because
each one is a series (not a last-bar flag like `Trend` or `NearSupport`), it works with offsets, `@timeframe`, `Count`,
`CountStreak` and alerts.

| Entry | Kind | What it is |
|---|---|---|
| `Pattern(name)` | filter | The named candlestick pattern on this bar. It reads this bar and at most the two before it. There are 14 names, from the detectors in `candlestick_patterns.py`, with the direction split where a detector has two: `doji`, `hammer`, `shooting_star`, `bullish_engulfing`, `bearish_engulfing`, `morning_star`, `evening_star`, `bullish_pin_bar`, `bearish_pin_bar`, `inside_bar`, `bullish_outside_bar`, `bearish_outside_bar`, `bullish_rejection`, `bearish_rejection`. |
| `SwingHigh(degree)` / `SwingLow(degree)` | factor, price | The last **confirmed** swing high / low from the causal swing engine (`causal_swings.py`: ZigZag on ATR by default; degree 0 to 3 = the smallest to the largest threshold). |
| `SwingDirection(degree)` | classifier | `UP` after a confirmed swing low, `DOWN` after a confirmed swing high, empty before the first. |
| `MedianRange(n)` | factor, price | The median (high − low) of the n bars **before** this one: the market's own noise, as used by the break and reversal logic. |

- **No look-ahead.**
  - A swing counts from the bar that confirmed it, when price had come back from the extreme by the threshold. It never
    counts from the extreme bar.
  - A pattern never reads past the frame's start (bars 0 and 1 are false).
  - The tests check truncation invariance: the value at bar j is the same with or without later bars.
- **Validation.**
  - `name` and `degree` take only the listed values, as a literal or a parameter; anything else is refused before a run.
  - `describe()` lists the choices for the builder palette.
  - Lookback includes the swing warm-up (`min_bars` 100) and the bar before a median range (`extra_bars` 1).
    CrossAbove/Below now use the same `extra_bars` field, so their lookback is unchanged.
- **Settings.** The swings use the default `pa_settings`, the same for every tenant (SC-13).
- **Tests.**
  - `tests/test_s5a_price_action.py` (12):
    - pattern parity with the detectors on every bar, and hand-built hammer and engulfing bars;
    - no wrap-around;
    - the confirmation bar;
    - truncation invariance at degrees 0 and 1;
    - degree ordering;
    - the median range excludes the current bar;
    - validation;
    - an end-to-end screen.
  - Plus an S1b registry-coverage test.
  - 7 mutation checks, all killed:
    - a swing counted at its extreme bar;
    - a pattern that wraps around;
    - a median range that includes the current bar;
    - no choices check;
    - `min_bars` ignored;
    - direction swapped;
    - pattern direction ignored.
- **Review follow-up (fresh-eyes pass).**
  - **`Pattern` is vectorised.**
    - `candlestick_patterns.pattern_masks` applies the same rules as the detectors to whole columns.
    - Bar by bar it cost about 0.2 s per name per symbol, enough to block a request for minutes. Now all 14 names on
      3000 bars take well under a second (there is a test).
    - It is parity-tested against the detectors on every bar. The mapping from name to detector is written out in the
      test, not read from the code under test.
    - Hand-built pin bars, rejection candles, outside bars and a shooting star are included.
  - **A missing `SwingDirection` (before the first pivot) is missing, not `""`.**
    - `!=` and `IN` no longer match there.
    - `StructureEvent` keeps `""` for "no event", as before.
  - **Classifier values are checked.** Trend, StructureEvent, ChainBias and SwingDirection list their values, and a
    literal outside them is refused ("… never matches"). `describe()` lists them.
  - **History on higher timeframes.**
    - A higher timeframe built from the base bars is now checked against its lookback. For example, `SwingLow(0)@1d`
      on a 1m screen with two weeks of bars says "not enough history on 1d". Before, it was silently false.
    - `fetch_days` counts each timeframe's bars in its own minutes, turns sessions into calendar days (weekends), adds
      4 days for holidays, and is capped at 60.
  - **The end-to-end screen test checks `matched`** against the series, and its negation.
  - **Mutation checks: 7 of 7 killed.**
    - pin directions swapped;
    - rejection direction;
    - shooting star read as hammer;
    - missing direction as empty;
    - resampled history unchecked;
    - no values check;
    - fetch days without weekends.
  - **Noted, not changed.**
    - Degree 3 swings depend on where the fetched history starts (ZigZag is path-dependent), so they can move when an
      unrelated part of the screen changes the fetch window. (SC-14; the S5-A4 review measured D1 and D2 disagreeing too
      at 100 bars, so the history is now set per degree: see S5-A4.)
    - Older items, not new here:
      - lookback is not summed through nested calls (`Lag(SwingHigh(0), 50)`);
      - parametrised screens cannot become alert rules;
      - `NOT` of a comparison on a missing value is true;
      - the manual run route evaluates on the event loop.
- **Next (S5-A2).**
  - Logical reversal at a level (`reversal.evaluate_reversal`).
  - Real break against false break of a level (`breaks.first_real_break`).
  - Zone strength and distance (`level_strength`).
  - These cost more per bar, so the design first needs a bounded tail or an incremental cache.

## S5-A2 (built): `ReversalAt(level, direction)`
- **What it is.** The trade-port logical reversal rule (`app/price_action/reversal.py`, composite mode, default
  settings), on every bar: did price reverse at **that bar's** `level`?
  - The rule: touch, reclaim, strength and close location. Hammer, engulfing and star candles are one rule.
  - `bullish` reads support; `bearish` reads resistance.
  - The level is any price expression: `SwingLow()`, `PDL()`, `ORLow(15)`, a parameter or a number.
- **Causal.** The window always ends on the bar, and the level is the level known at the bar.
  - The series is checked to equal `evaluate_reversal` on the candles up to each bar, which also proves the prefilter
    below changes nothing.
  - Truncation invariance is tested.
- **Cost.** `Bars` are built once per frame. Only bars whose last few candles reached the level are evaluated: a vector
  prefilter, widened by the touch tolerance on the correct side. A universe stays well under 0.5 s per symbol (the
  test allows 1.5 s for slow CI runners). Cost weight 4.
- **Validation.**
  - The level must be a price, so `ReversalAt(RSI(14), "bullish")` is refused (`Spec.price_args`).
  - The direction is `bullish` or `bearish`.
  - The lookback includes the median-range warm-up (`min_bars` 25).
- **Tests.**
  - `tests/test_s5a2_reversal.py` (7):
    - parity with the rule for bullish/SwingLow and bearish/SwingHigh;
    - a hand-built hammer at support passes, and the wrong side never does;
    - truncation;
    - validation;
    - a timing budget;
    - parity with a touch tolerance > 0;
    - parity under the addendum follow-through (the prefilter reaches back `followthrough_max_bars` more bars), and
      `reversal_mode` other than composite is refused rather than silently evaluated as composite (review follow-up).
  - 5 mutation checks, all killed:
    - the prefilter too narrow;
    - the bearish touch sign (killed only by the tolerance test, because the default tolerance is 0);
    - the next bar's level (look-ahead);
    - direction ignored;
    - no price check.
- **Next (S5-A3).** Real break versus false break of a level (`breaks`).

## S5-A3 (built): `RealBreak(level, side, n=20)`
- **What it is.** The trade-port break rule (`app/price_action/breaks.py`, `first_real_break`, default settings), on
  every bar: was a **real** break of this bar's `level` confirmed within the last `n` bars?
  - A real break is a close beyond the level by the buffer, then displacement, acceptance (no reclaim within the
    acceptance bars), or a failed retest from the broken side. A wick through, or a close that is reclaimed before it
    is confirmed, is a false break and is not counted.
  - `above` breaks resistance upwards; `below` breaks support downwards. The level is any price expression.
- **An event in the window.** Like the other windowed filters, a break confirmed and later reclaimed still reads true
  until it is older than `n` bars. For "still beyond", AND it with the close (`RealBreak(PDL(), "below") AND close <
  PDL()`). There is a test for both.
- **Crossing required.** The scan starts on the bar after the **first** close on the near side of the level in bars
  j-n..j (`break_scan_start`), so every break in the window that crossed from the near side is found. Price that sat
  beyond the level for the whole window is not a break of it: nothing crossed.
- **Causal.** The rule and its retest check stop at the bar (`end=j`). The level is the level known at the bar.
  Truncation invariance is tested, and so is invariance to history beyond the validator's lookback.
- **History.** The lookback is `n` + 21 bars (`extra_bars`): the bar before the window, and the 20-bar median range
  before the earliest candidate. Without it, early candidates had no median range and the answer depended on how much
  history was fetched (review finding).
- **Cost.** A vector prefilter (the rolling min / max of close beyond the level by the buffer, over the window) skips
  bars where no close in the window got beyond the level. It is loosened by a relative epsilon, because the rule
  tests `close < level - buffer`, which can round differently on tick prices (review finding, with a test). Cost
  weight 4.
- **Validation.** The level must be a price (`Spec.price_args`); the side is `above` or `below`.
- **Tests.**
  - `tests/test_s5a3_breaks.py` (10):
    - a wick is not a break and a strong close is;
    - acceptance versus a reclaim;
    - the window forgets an old break;
    - parity with the rule and truncation invariance;
    - validation and the lookback;
    - the crossing;
    - the failed-retest confirmation (a session-shaped sample found by search);
    - a close exactly at level minus buffer on tick prices;
    - an event in the window after a reclaim;
    - no dependence on history beyond the lookback.
  - Mutation checks, 6/6 killed:
    - no crossing;
    - reading later bars (`end=None`), killed once the redundant `c <= j` guard was removed;
    - the `above` prefilter;
    - the `below` prefilter without the epsilon;
    - the near side flipped;
    - no failed-retest confirmation.
- **Next (S5-A4).** Zone strength (`level_strength`).

## S5-A4 (built): `SwingZoneStrength(side, degree=0)` and `SwingZoneDistance(side, degree=0)`
- **What they are.** The zone at the last confirmed swing low (`low`, support) or swing high (`high`, resistance),
  measured on every bar by the trade-port module `app/price_action/level_strength.py` (default settings).
  - `SwingZoneStrength` is `strength_score`, 0-100, from departure, a short base, recency and role reversal.
  - `SwingZoneDistance` is (close - zone mid) / median range: positive above the zone.
  - Both are missing before the first confirmed pivot of that side.
- **The zone.** The pivot candle from its extreme to its body: support is [low, min(open, close)], resistance is
  [max(open, close), high]. The pivot bar is the zone's origin, so departure and base are measured from it.
- **Causal.** A pivot is used only from the bar that confirmed it. Every bar the measures read is at or before the bar
  (the module clips the departure window to the bar). Tested by parity with the module on the pivot confirmed by each
  bar (found independently), and by truncation invariance for degrees 0 and 1.
- **Stamps.** The module finds the origin by timestamp, so it is given bar-numbered stamps: the lookup is exact whatever
  the index holds. An IST, naive-IST or duplicated-stamp index gives the same series as UTC (tested). Nothing else
  reads the stamps, because time at price is not used.
- **Flat bars.** A suspended or circuit-locked stock has no price range to measure in (a zero median range). Those bars
  are missing, not an error (review finding: it was a ZeroDivisionError).
- **One bad symbol does not stop a run.** `run_screen` now reports an unexpected error on one symbol as "could not be
  evaluated (ErrorName)", logs it, and goes on to the next symbol. Before, any error other than a screen error ended
  the whole universe (review finding).
- **What the score leaves out.**
  - Leg labels: ScreenQL has no leg classifier yet, so the origin-strength part scores 0.
  - Time at price: that needs 1-minute data, so it scores as unknown (half).
  - So the score runs from 5 to 70 today, not 0 to 100. Thresholds should be read on that scale (SC-15).
- **Cost.** One `strength_features` call per bar after the first pivot, with the bar arrays prepared once per frame.
  Strength and distance come from one pass, kept for the frame, so a screen using both pays once. A 500-bar frame
  takes about 0.05 s; the test allows 0.6 s. Cost weight 4.
  - A 1-minute screen over about 1,900 bars costs roughly 0.2 s per symbol. That is about 100 s for 500 symbols, too
    slow for a 10-second intrabar alert. Computing only the tail is open (SC-15).
- **History by swing degree (all swing functions).** ZigZag pivots depend on where the history starts. Measured on
  session-shaped samples (the last bar on exactly N bars against a long history):
  - D0 agrees from 100 bars;
  - D1 from 250;
  - D2 from 600 (37 of 60 differed at 100 bars).

  `SwingHigh`, `SwingLow`, `SwingDirection` and both zone functions now ask for 100 / 250 / 600 / 1200 bars by degree
  (`Spec.degree_min_bars`; a parameter degree is read from its value). A D2 screen on 1h bars therefore needs more
  history than the 60-day fetch cap. It reports "not enough history" instead of a pivot that a longer history would
  not give.
- **Tests.**
  - `tests/test_s5a4_zone_strength.py` (14):
    - parity for both sides;
    - the distance sign;
    - truncation for degrees 0 and 1;
    - no dependence on history beyond the lookback (degrees 0 and 1);
    - IST, naive-IST and duplicated stamps;
    - flat bars;
    - one pass for both functions, and a changed frame recomputed;
    - one bad symbol does not stop the run;
    - validation, units and the history per degree;
    - cost;
    - the 5-70 scale.
  - Mutation checks, 9/9 killed:
    - origin at the confirmation bar;
    - a pivot used before it was confirmed;
    - the support zone to the body top;
    - either pivot kind;
    - index stamps;
    - the distance sign;
    - no flat-bar guard;
    - a cache that ignores the side;
    - a cache that ignores a changed frame.
- **Next (S5-C).** Category C.

## Open questions (provisional answers taken, work continues)
- **SC-12. Validation set for category A.** The plan names the BANKNIFTY engine fixtures as the validation set, but no
  BANKNIFTY bar fixtures are in this repository. Only the expiry calendars are. Provisional: the S5-A tests use the
  session-shaped generator (`tests/sample_market.py`), the same one the trade-port tests use. The NIFTY holdout stays
  sealed.
  - Owner question: should a BANKNIFTY bar sample (from your own data, outside the holdout) be added as a fixture?
- **SC-15. The swing zone and its strength scale.** Provisional:
  - The zone is the pivot candle from its extreme to its body.
  - The score runs 5-70 until a leg classifier and 1-minute time at price are available.
  - Owner questions:
    - Should the zone be wider (for example the base candles before the pivot)?
    - Should the score be rescaled to 0-100 over the parts that are known?
    - For intrabar alerts on 1-minute bars, should only the tail (the bars Lag and Cross need) be computed?
- **SC-14. Degree-3 swings and the fetch window.** Provisional: documented, not fixed. A D3 pivot can differ when
  the screen's total lookback (and so the fetch start) changes.
  - Owner question: should D3 be computed from a fixed warm-up anchor (for example, always 400 bars before now)?
    That costs one longer fetch per symbol.
- **SC-13. Swing settings per tenant.** Provisional: `SwingHigh`/`SwingLow`/`SwingDirection` use the default
  `pa_settings` (ATR mode, multipliers 1.5 / 3 / 6 / 12). Per-tenant price-action settings are not applied in screens.
  - Owner question: should a screen use the organisation's saved price-action settings? That would make the same
    screen give different results in different organisations.
- **SC-11. Intrabar alerts that stop holding by the close.** An intrabar event can fire on a condition that is false
  when the bar closes. Provisional: the event says `intrabar: true` and nothing more is sent.
  - Owner question: should a short follow-up go out ("no longer true at the 09:30 close")? Or should intrabar rules
    be limited to critical priority?
- **SC-10. Weekly / monthly alert rules.** Provisional: they can be saved but are not evaluated yet. A weekly bar
  would close at the week's last trading session, and the same holds for monthly bars.
  - Owner question: should these fire at that close (like daily), or only on the next session's pre-open?
- **SC-9. Unsubscribe scope and bounces.** Provisional:
  - an unsubscribe stops screen-alert emails only; risk and system emails cannot be unsubscribed;
  - a retry after one recipient's failure re-sends to the earlier recipients of that alert (at most 10 addresses).
  - Owner questions: should system emails also offer an opt-out? Which bounce source should be used (SMTP DSN
    mailbox, or the provider's webhook)?
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
