# M71 independent review against origin/main (d420c2a5)

Rules re-read: CLAUDE.md, roadmap. Read-only review; nothing committed. Sources: `git show origin/main:<path>`, order text
`origin/claude/inbox-m71:docs/owner-orders/ATP_ORDER_M71.md`. Line numbers are origin/main. Call counts are DERIVED from
code constants (not measured on production); production settings / flags are not known from the repo (OWNER_RULES A.2).

## 0. Top findings (ranked)

1. **Most REST history reads use NO budget at all.** `fetch_frames` builds a raw adapter (`screener/routes.py:526`
   `build_adapter(record)`; BYOB: `live_access.py:77-78` also raw) and wraps it in `api_budget.BudgetedAdapter` only when
   `budget_out` is passed (`screener/routes.py:527-529`, wrap at :528), which happens only for a batch run
   (`screener/routes.py:878` `budget = {} if body.batch else None`). Not budgeted: a normal Screener run, the Copilot
   screener tool (`ai/tools/screener.py:359`), every bar-close alert (`alerts/barclose.py:483,486`, in the worker but
   outside its RateBudget), breakouts / library / ranking screens (`screener/breakouts/routes.py:199-213`,
   `library_routes.py:146`, `ranking_routes.py:200`), Copilot candles (`ai/routes.py:1061-1069`, `ai/tools/market.py`
   `_frame` -> `build_adapter`), `ai/evidence.py:57`, `ai/worker_bridge.py:44,74`. PL-2 is useless unless every data
   path goes through one budgeted adapter factory.
2. **The order's facts about rate_budget are confirmed.** `brokers/rate_budget.py:40` Upstox `per_second=8, burst=10,
   per_minute=200`; no 30-minute window (`:37-38` says so; HB2 has its own DB-counted 600 / 30 min,
   `config.py:341`, `intraday_history.py:75,496`). Worker alone can do 200/min x 30 = 6,000 per 30 min vs Upstox 2,000.
   The docstring `:3` still says "Upstox roughly 25 req/s and 250 req/min" and `api_budget.py:59` has Upstox 25/s; R33
   says 50/s, 500/min, 2,000/30 min. Three different Upstox figures in the code - PL-2 must replace them with ONE table
   that cites the doc page and date.
3. **No shared, per-credential worker budget; one pool for orders and data.** Worker budgets are in-process, keyed
   `(tenant_id, "<broker>@<account>")` (`trading_worker.py:2371-2380`), not by credential id, not in Redis. Every call
   (orders, quotes, history, chain) draws the same two buckets (`rate_budget.py:302-319`). Only the API side is in Redis
   per credential (`api_budget.py:5-9,113-129`, keys `ab:{<record.id>}:...`), at a fixed 50 % of the worker's
   conservative numbers (`api_budget.py:54,91-102`; Upstox API: 4/s, 100/min, 500/30 min). Worker + API never see each
   other's counts.
4. **`RateBudget.acquire()` sleeps while holding its lock** (`rate_budget.py:239-249`). Any data call that waits
   (engine runner, strategy history, OI/VIX reads) queues every later exit behind it. The engine runner gets the
   exit's own budget with full priority: `_spawn_engine_runner.broker_for` -> `_first_usable_broker` ->
   `_usable_adapter` -> `_rate_limited` (`trading_worker.py:1867-1868, 971-984, 2382-2390`), i.e. `acquire()`, not
   `acquire_spare()`. Only HB2 and the chain recorder use `acquire_spare` (`intraday_history.py:545`,
   `chain_recorder.py:172`). PL-2's exit reserve must fix this (never wait under a lock; exits from their own pool).
5. **No 429 back-off anywhere in the data path.** Upstox `_request` raises `BrokerAPIError(msg, status)` on any >=400
   (`upstox.py:182-199`); no retry, no Retry-After. Groww / Kotak map 429 (`groww.py:309`, `kotak_neo.py:323`) and raise.
   A 429 is counted as a HEALTH FAILURE by the circuit breaker (`circuit_breaker.py:49-51`), one breaker per broker
   NAME per process (`:17-18`), which refuses NEW LIVE entries platform-wide when open (`:5-8`; exits still pass).
   So data 429s in the worker can open the order breaker for every tenant: the data pool's 429s must not feed
   `observe_call` of the order breaker (HB2 already keeps its errors out, `intraday_history.py:21-22`).
6. **Streaming and a tick->1m bar writer already exist; PL-1b must extend, not add a fourth stream owner.**
   Streams: Phase S `StreamManager` (`trading_worker.py:349`, flag `STREAMING_QUOTES_ENABLED`, `config.py:271`), the
   data hub `DataHub` (`hub.py`, flag `data_hub`), BYOB `UserSessionFanIn` (`fanin.py`, flag `byob_data_tier`). Bar
   writer: `market_lake/recorder.py` `LakeRecorder` + `ingest.CandleBuilder`, flag `LAKE_TICK_WRITER_ENABLED`
   (`config.py:273`), wired `trading_worker.py:358-363,568`, writes the SHARED `md_candles` as `stream:<broker>`. Upstox
   stream subscribes mode `"ltpc"` (`stream.py:601,620-622`): price only, no volume, no OHLC; the recorder builds bars
   from `ltp` only (`recorder.py:37-41`) -> every tick-built bar has volume 0 and high/low from sampled ticks.
7. **Data terms block the "shared store" as written.** HB2 / DATA-H broker bars are owner-PRIVATE
   (`md_tenant_candles`, `models.py:2976-2981`; `own_bars.py:1-19`); the only sharing is M55 T4 index 1-minute rows
   while `byob_data_tier` is off (`own_bars.py:14-19,61-69`), itself an open lawyer item (OPEN_QUESTIONS M55Q-1,
   COMPLIANCE_IN.md:38-49). `backtest/bar_source.py:9-11` forbids reading intraday bars in the shared lake "recorded from
   other organisations' broker feeds". BROKER_DATA_TERMS.md:19 (Upstox: distribution to Upstox users because each call
   needs that user's own token) does not cover serving the owner's credential's bars to all users; Zerodha is
   execution-only (:21, :65-73). PL-1's "owner credential feeds the shared store for indices / universe" = M71Q-1 /
   M55Q-1 territory: build it behind a flag defaulting to the existing M55 T4 rule.
8. **M70 is not in the repository** (section 4). The SC-1..SC-18 numbers in the repo are Screener open questions
   (docs/design/SCREENER.md:1873-1884), not M70 - slices must not mark those superseded.

## 1. PL-1..PL-6 - what exists, what is missing

### PL-1 own store as single source
- (a) Daily/Weekly from bhavcopy: exists. HB1 `market_lake/bhavcopy_backfill.py`, `md_candles` timeframe `day`, sources
  `nse_bhav:cm|index|fo` (DATA_SOURCES.md:28), flag `history_backfill` OFF there; read for backtests only behind
  `backtest_stored_history` (`backtest/bar_source.py:6-12,226-235`, Weekly built from Daily, equity adjusted on read
  via `market_lake.adjust`, `:27`) and the Daily screener fallback before the broker login (`screener/routes.py:781-787`,
  `screener/stored_history.py`). Charts' Daily path does NOT read it (`candles_routes.py` only reads stored index
  1-minute history, `:89-93,380-415,485-504,583-587`). "Already ingested" on production is not known from the repo; the
  licence position BSH-1 is open (DATA_SOURCES.md:28). Bhavcopy is unadjusted: a store-first Daily read must apply the
  same `adjust` the backtest uses, or charts / screens change vs today's broker-adjusted bars.
- (b) intraday WebSocket 1m bars: partly exists (finding 6). Missing: universe subscription (today: deployments +
  positions for StreamManager; positions > charts > watchlist > screener for hub/BYOB, cap
  `BYOB_SESSION_SYMBOL_CAP=50`, `config.py:395`, `fanin.py:83-85`); volume (needs a mode with cumulative volume, e.g.
  Upstox `full`, whose per-connection instrument cap is lower than `ltpc` - take both from Upstox's docs); OHLC fidelity;
  resample 5m/15m/1H with DH3 flags. No Upstox per-connection cap constant exists in code (Groww has
  `MAX_SUBSCRIPTIONS=1000`, unverified, `groww_feed.py:30-31`).
- (c) paced backfill: three mechanisms exist - HB2 `market_lake/intraday_history.py` (owner 1m, resumable per
  `history_intraday_days`, off-hours only `:23-24`, 600 / 30 min, flag `history_intraday`), DATA-H
  `market_lake/index_history.py` (indices, resume rows `lake_backfill_windows`, `models.py:3257-3279`, through
  `BudgetedAdapter` `:353-355`), `ingest.py:213` vendor/adapter backfill. `lake_fill.py` only fills the LAST closed
  session from the reader's own lake when the broker lags (`lake_fill.py:1-14`, `MAX_BARS=3000`); no `request_gap_fill`
  exists yet. Last-bar bookkeeping per (symbol, interval): only the two resume tables above; `md_candles` has none.
- `app/market_store/` does not exist on main.

### PL-2 budgets
- Exists: worker token buckets per (tenant, broker@account) (`rate_budget.py:224-249`), per-day caps by call type for
  Groww only with an exit reserve for orders and price reads (`:55-71,112-179`), `exit_reads()` context (`:76-90`),
  `acquire_spare` reserve for background readers (`:255-275`), SEBI OPS throttle for orders (`execution/ops_throttle.py`,
  `rate_budget.py:329-369`). API: Redis Lua bucket + sliding windows per credential incl. 30 min for Upstox
  (`api_budget.py:61,83-88,113-129`), Redis-down fallback = per-process share by `API_WORKERS` (`:10-14,143-183`).
- Missing vs order: published limits for every broker / every window (Kite has no day cap in `DAILY_CAPS`, `:61-63`;
  Kite quote 1/s is not modelled, `api_budget.py:31-32`); one Redis pool per credential shared by worker AND API;
  separate data / order pools (Upstox orders 10/s, 500/min, 2,000/30 min are a separate family per R33);
  `safety margin` setting (today: hard-coded "a third", `rate_budget.py:35-36`, and `API_SHARE=0.5`); 429 back-off +
  jitter; exits' reserved share in the per-second / per-minute pools (today only the Groww day cap has one).
- Orders: `RateLimitedBroker` deliberately budgets orders too (`rate_budget.py:279-281`). With separate pools an exit
  must still never be refused - only waited for briefly - and must never wait behind a data call.

### PL-3 read order
- Today: broker first everywhere, per-request Redis copies: candle cache 60 s (`service.py:43`), shared history copy
  300 s, minute window 90 s, today 60 s capped at the forming bar (`shared_candles.py:38-40`), history pages 1 h
  (`service.py:44`). A second identical run within 5 min re-fetches only "today"; after 5 min it re-fetches all
  history. No store-first read exists for intraday; Daily store-first only as a fallback.
- "Second run = zero history calls" needs the store to hold everything up to the newest closed bar; today's forming
  minutes must come from the feed, otherwise each run still makes 1 "today" call per symbol (`service.py:321,401`).

### PL-4 feed health
- Exists: reconnect with backoff `BACKOFF_SECONDS=(1,2,5,10,30)` and resubscribe (`stream.py:51,520-544`), stream
  status (`stream.py:382-388`: reconnects, last_tick_at, last_error), hub status per namespace in Redis
  (`hub.py:58-107,135-140`: live / connecting / reconnecting / renew_needed / polling, subscribed count).
- Missing: gap detection after reconnect (no code compares last bar vs reconnect time), "gaps filled today" counter,
  an admin page (no admin route or card reads hub/stream status; existing admin routes `admin/console.py:145-428`,
  cards in `frontend/src/admin/`, e.g. `ChainHistoryCard.tsx` as the pattern).

### PL-5 data terms
- See finding 7. Per BROKER_DATA_TERMS.md: Upstox and Dhan "launch list" for Tier A (own user, own token); Zerodha
  execution-only; all others "second wave - written confirmation pending". No broker row records permission to put one
  credential's data in a store served to other users. A licensed vendor (option 2) already has a seam: `HistoryVendor`
  (`market_lake/ingest.py:219-240`, SCREENER.md SC-4 "no subscription is bought; Abhi chooses").
  `market_data/broker_data_terms.json` + `data_terms.py` is the registry the code reads; a terms PR that changes a
  status must change both (test `test_bt1_byob_data_tier.py`, BROKER_DATA_TERMS.md:31-35).

### PL-6 proof
- Baseline "calls per day before" is not measurable today: `BROKER_CALLS` (`observability/metrics.py:125`) is
  incremented only by `RateLimitedBroker._call` and the order router (`rate_budget.py:313,318`, `router.py:376,383`).
  API-side and raw-adapter calls (finding 1) are not counted. A counter at the adapter's HTTP layer (e.g.
  `upstox.py:_request`) is needed first, deployed for a day, before any switch.

## 2. REST history call sites, ranked by API cost (derived from code)

| # | Feature / path (file:line) | Calls per request | How often | Budget today |
| --- | --- | --- | --- | --- |
| 1 | Charts / multi-chart / Terminal / dashboard sparks: `POST /api/market-data/candles` (`candles_routes.py:448-504`, `service.get_candles` `:298`), `MAX_SYMBOLS=50` (`:83`) | per symbol: intraday history in <=30-day chunks (`service.py:45`) + 1 today (+2 for an index's future volume, `index_volume.py:84-85`) | every pane re-polls every 60 s (`useChartCandles.ts:202,251`) unless a fresh live tick; today copy lives <=60 s | API Redis budget (`candles_routes.py:499`). A 50-pane grid = ~50 "today" calls / min -> Upstox API share 500 / 30 min used in ~10 min, then "budget used" |
| 2 | Screener run (`screener/routes.py:778-779` -> `fetch_frames` `:469-612`), breakouts / library / ranking, Copilot screener tool | per symbol 2-4 (history chunks + today + Daily attach `:594-596` + today bar `:561` + future 2), <=50 symbols -> ~100-200 per run | per user click; history re-fetched after 300 s | NONE unless batch (finding 1) |
| 3 | Bar-close alerts (`alerts/barclose.py:479-490`) | 2 `fetch_frames` per rule (base tf + a 15m fetch), x symbols | every closed bar of each rule (worker, `trading_worker.py:586`) | NONE (raw adapter in the worker) |
| 4 | Worker strategies (`trading_worker.py:1882` `_history_source(...).get_frames`) | 2 per symbol (history + today, `service.py:16-18`) | every 60 s cycle (`config.py:412`); 60 s cache shared across tenants (`service.py:5-8`) | worker RateBudget (`acquire`) |
| 5 | Copilot / AI: `ai/routes.py:1061-1069` (1min 12 d + day 400 d), `ai/tools/market.py:134` get_candles, `ai/evidence.py:57-60`, `ai/market_memory.py:144,155` | 2-4 per symbol | per question / per memory capture | NONE |
| 6 | M66 engine (`engine_provider/sources.py:96-105` `broker_bars`, `:118-129` futures, `engine_runner.py:403`) | per symbol per signal bar: 1m history (one request for the whole window) + intraday + Daily if not stored + future 1m (2) | every signal bar (flag `trade_engine`) | worker RateBudget via `acquire` = exit priority (finding 4) |
| 7 | Option chain (`option_chain/analyses_routes.py:160-161`, `ask_adapter.py:158`, worker `trading_worker.py:234,1036,2693`, `market_lake/chain_recorder.py`) | 1 per (underlying, expiry) + spot `option_chain/probe.py:103` | per page refresh (hub `shared_chain` dedups a few seconds), worker OI slots | API budget / worker / spare |
| 8 | VIX gate `risk_engine/vix_gate.py:165`; LTP `/ltp` (`candles_routes.py:644-725`, 4 s Redis cache `api_budget.py:64`) | 1 | per cycle / poll | worker / API |
| 9 | Backfills: HB2 (`intraday_history.py:558-561`), DATA-H (`index_history.py:434-436,695`), `ingest.py:213` | 1 per window | off-hours / nightly | `acquire_spare`, 600 / 30 min; BudgetedAdapter |

Backtests: `backtest/bar_source.py:1-27` - broker first (`evidence.server_frame`, `ai/evidence.py:38`), stored Daily
or stored index intraday only as fallback, "one source per run, never mixed".

## 3. Risks / traps for the slices

- **Double feeds.** Three owners of `TickStream` exist (finding 6). Upstox limits connections per user; a fourth
  stream (PL-1b) on the same token can push an existing one off. PL-1b should be the hub's org connection (or extend
  `StreamManager`) and feed `LakeRecorder` via `stream.tick_listeners` (`stream.py:141-142,565`). BYOB per-user
  streams must never feed the shared store (`fanin.py:19-20`, BROKER_DATA_TERMS.md:87).
- **Tick bars vs REST bars.** LTPC bars have volume 0 and sampled H/L; REST 1m bars are the exchange's. Never mix the
  two in one series without a rule (parity scan = backtest = live). Suggest: feed bars provisional (`stream:*`), the
  REST gap/after-close fill writes a new version (`md_candles.version`, `models.py:2864-2865`) and reads prefer REST.
- **Bar labels.** Lake / tenant store label bars by END (`models.py:2863`, `tenant_store.py:9-10`); Charts and the
  screener use START labels (`intervals.py:74`, `service.build_frames` `:82`). A store-first reader that hands END-
  labelled bars to START-label code shifts every bar one period = look-ahead. Six resamplers exist
  (`core/resampling.py:6`, `service.py:82`, `tenant_store.py:89`, `price_action/zones/job.py:111`,
  `screener/breakouts/style.py:61`, `screener/runtime.py:134`); PL-3 must reuse one and test identical frames.
- **DH3 / DP flags.** `own_bars` applies `md_bar_flags` on read by default (drop `post_close` intraday, hold Daily
  extremes inside continuous ones, `own_bars.py:9-12,36-38`); the engine reads AS STORED with `dh3=False` and turns flags
  into marks (`engine_provider/sources.py:6-7,85-89`). A new read path must keep each consumer's existing mode. Flags
  are written today only by `index_history.py:245-281` for index keys; tick-built stock bars have none.
- **Look-ahead.** Store only closed bars (CandleBuilder writes after the end, `recorder.py:3-4`); every as-of read
  filters `ingested_at` (HB2 rule `intraday_history.py:25-27`). A gap fill must not stamp old `ingested_at`.
- **Zero-filled gaps.** `index_volume.replace_volume` sets volume 0 where the future has no bar
  (`index_volume.py:13-15,84-85`); the engine refuses it (owner decision, `engine_provider/sources.py:9-12`). Store-first
  must not persist those zeros, and a missing minute must stay a gap, never a synthetic flat bar (index_history flags
  "flat bar after continuous trading", `:269`).
- **Exits never blocked (ADR-0004).** Keep: entry-only refusals (`rate_budget.py:144-163`, `phase0_lock`, ops
  throttle `:338-342`); `ALWAYS_READ_CALLS` (`:71`); `exit_reads()` for exit price reads. Fix: lock-held sleep (finding
  4); engine / alert / strategy data reads must draw the data pool, never the exit reserve.
- **Redis down / evicted.** Redis is `volatile-lru` with a cap (`workers/redis_guard.py:3-11`): rate-window keys with a
  TTL can be evicted -> windows under-count -> over-limit. Redis down: API falls back to per-process shares
  (`api_budget.py:10-14`); a Redis-held ORDER pool must fail open to an in-process bucket for exits (never refuse an
  exit because Redis is down), and fail closed / conservative for data.
- **Per-user vs shared credential.** Budgets must key by credential id (as `api_budget` does), not by
  (tenant, broker@account) (`trading_worker.py:2371-2380`); BYOB sessions keep their own namespace keys
  (BROKER_DATA_TERMS.md:77-80). Shared candle copies are per broker (`md:shared:<broker>:...`, LD-10, :81-85).
- **Off-hours rule.** HB2 never runs 08:00-16:00 IST on a trading day (`intraday_history.py:23-24`); PL-1c "gaps only
  by REST" during the session must use a separate, paced data pool, not loosen HB2's rule.
- **Migrations.** Alembic has one head on main: `a7d3e5c9f214` (`deployment_wait_reason`), 139 revisions. Each slice
  needing a table (bookkeeping per (symbol, interval), gap log) must chain on the then-current head; two parallel
  slices with migrations = two heads -> rebase before merge. Say "migration yes" in the PR (CLAUDE.md §4/§7).
- **Breaker.** Keep data-pool 429s out of `observe_call` / the order breaker (finding 5).
- **Flags.** Every new path behind a flag, OFF (STREAMING_QUOTES_ENABLED, data_hub, LAKE_TICK_WRITER_ENABLED,
  history_intraday, backtest_stored_history are all OFF by default in code); production values are unknown.

## 4. M70

- Not in the repository: no `ATP_ORDER_M70.md`, no INBOX / DECISIONS / ROADMAP / README row on origin/main; the inbox
  branch's own DECISIONS row says "M70's text is not in this repository" (`DECISIONS.md:214` on claude/inbox-m71). No
  remote branch name contains m70.
- "SC-1..SC-5" in the repo are Screener design questions (SCREENER.md:1873-1884: SC-1 order placement, SC-2 columnar
  store vs lake, SC-3 parser, SC-4 paid vendors, SC-5 NSE archives; renumbering in WORK_LOG.md:1363). They are NOT M70.
  What M70 SC-1/SC-4 (superseded) and SC-2/SC-3/SC-5 (kept) contain cannot be stated from the repo -> owner question:
  "paste the M70 text"; default: treat M71 as complete on its own, supersede nothing in the repo.

## 5. Wave-2 plan (after PL-2 / PL-1b / PL-1c / PL-3 merge; non-overlapping files)

Precondition W2-0 (small, first): one budgeted data-adapter factory from PL-2 (e.g. `brokers/rate_budget.py` data pool
+ `market_data/api_budget.py`) and an HTTP-level call counter in `brokers/upstox.py` for the PL-6 baseline. Owned by
the PL-2 slice; all wave-2 PRs call only its public function.

| PR | Scope | Files owned (only these) |
| --- | --- | --- |
| W2-A Charts + Terminal + sparks | `/candles` reads `market_store.read_bars` first, REST only for missing newest bars via `request_gap_fill`; forming bar from the feed; Daily from bhavcopy + `adjust`; keep `index_volume` behaviour | `market_data/candles_routes.py`, `market_data/index_volume.py` (read only), `frontend/src/chart/useChartCandles.ts`, `frontend/src/dashboard/indexSparks.ts`, their tests |
| W2-B Copilot / AI | candles, evidence, market memory store-first; remove raw `build_adapter` data reads | `ai/routes.py` (1040-1080), `ai/tools/market.py`, `ai/evidence.py`, `ai/market_memory.py`, `ai/worker_bridge.py`, tests |
| W2-C Engine | `broker_bars` / `futures_contracts` -> store + gap fill; runner's broker from the data pool, not `_rate_limited` | `engine_provider/sources.py`, `workers/engine_runner.py`, tests (the `trading_worker.py:1859-1880` hook change goes in W2-D) |
| W2-D Worker strategies + alerts + worker wiring | `_history_source` / `get_frames` store-first; `barclose` via `read_bars`; `_spawn_engine_runner.broker_for` -> data pool; VIX gate | `workers/trading_worker.py`, `alerts/barclose.py`, `risk_engine/vix_gate.py`, tests |
| W2-E Option chain | chain reads through the data pool + hub `shared_chain`; spot/future from the feed | `option_chain/*.py`, `market_lake/chain_recorder.py`, tests |
| W2-F Admin feed status (PL-4) | `GET /api/admin/feed-status`: per connection subscribed count, last tick age, reconnects, gaps found / filled today, budget use per window per pool; admin-only (server-enforced), plain words, phone layout, before/after screenshots | new `admin/feed_status.py` + router include in `main.py`, new `frontend/src/admin/FeedStatusCard.tsx` + `AdminSectionsV2.tsx` line, tests |
| W2-G Screener family remainder | breakouts / library / ranking / Copilot screener tool onto `read_bars` (if PL-3 switched only `fetch_frames`) | `screener/breakouts/routes.py`, `screener/library_routes.py`, `screener/ranking_routes.py`, `ai/tools/screener.py`, tests |

Order: W2-0 -> {A, B, C, E, F, G in parallel} -> D last (it touches `trading_worker.py`, which C must not). At most one
migration among them (W2-F needs none if gap counters live in Redis + the store's bookkeeping table from PL-1c).

## 6. Owner questions (with defaults)

1. M70 text (section 4) - default: nothing in the repo superseded.
2. PL-5 shared store scope beyond indices - default: M55 T4 rule (index 1-minute only, `byob_data_tier` off) until
   M71Q-1 / M55Q-1 are answered; stocks per user only.
3. Feed mode for volume (Upstox `full` vs `ltpc`, lower instrument cap) - default: `full` for the subscribed universe up
   to the documented cap, REST after-close fill for volume truth.
