# Terminal master order: independent review against the code (CLAUDE.md section 6)

Order: `docs/owner-orders/ATP_SESSION_TERMINAL_MASTER.md` (PR #461, README row 20). Review of 23 Oct 2026 against
`origin/main` at `aa1aa5d2`. Docs only: nothing is built here, no flag and no migration. Questions TM-1..TM-32 are in
`docs/OPEN_QUESTIONS.md` with a provisional default each.

Checked against: CLAUDE.md (sections 2-8), `docs/owner-orders/DECISIONS.md` (incl. the owner principle of 23 Oct on the
PR #461 branch), ADR-0002 / 0004 / 0006 / 0013 / 0014, `docs/BROKER_DATA_TERMS.md` (Tier A, BT1 / BT1b isolation rules),
RDY-6 (answered 23 Oct: never the platform's or another user's feed), FNO-1-2, FNO-1-3, FNO-2-1, FNO-4-10, CH4-4.

## 0. Summary

The order fits the code better than its wording suggests: a large part of the foundation exists (a per-user fan-in with
its own websocket, per-credential budgets, a chain snapshot collector, hedge-first multi-leg sequencing, a what-if
engine, journal auto-capture, bar replay with a causal price-action layer). Four things in the order collide with the
code or with standing rules and need the owner before the parts that depend on them are built:

1. **PAPER is not a broker adapter today** (TM-1, TM-2). The router branches on the mode
   (`execution/router.py:149`, `execution/multileg.py:253` vs `:265`), the F&O ticket is a separate PAPER path
   (`paper/fno_ticket.py:449`), and PAPER keeps no resting order (FNO-1-3). "Same code path paper and live" and every
   Part 7 order type need a `PaperExecutionAdapter` plus a resting paper order book. That is a change to the order path
   and the data model: CLAUDE.md section 8 approval before PR 3.
2. **Chain history (9.4) and the data terms** (TM-7, TM-8). Recording "for ATP" from one user's token and reusing it for
   everyone is cross-user sharing (RDY-6, BT1b). The existing OI collector already stores platform-wide rows read through
   "the first tenant with a usable session" (`workers/trading_worker.py:837-874`) - a pre-existing tension that is
   flagged, not changed here. Provisional: record through the owner's own session into a tenant-private store.
3. **SEBI retail-algo framework and the two new brokers** (TM-3..TM-6). Both brokers require a registered static IP for
   orders; Kotak's page says market orders are not allowed for retail API orders; many unrelated users ordering from one
   ATP server IP is a question for the lawyer. Both brokers' data terms are "pending" in
   `market_data/broker_data_terms.json:29-31`, so their market data may not be shown yet.
4. **The browser channel** (TM-12). The order says "the existing websocket"; there is none. The browser has one
   Server-Sent Events stream that carries reload signals, no data (`live/routes.py:1-11, :91`).

## 1. Per-part status

| Part | Status | Evidence (file:line) | Reuse / extend |
|---|---|---|---|
| 1 Data hub | partial | `market_data/stream.py:93` TickCache, `:392` TickStream (backoff `:47`), `:556` Upstox V3, `:596` Kite, `:654` StreamManager (one stream per tenant session key); `market_data/fanin.py:168` SessionSymbols (priority, cap), `:301` UserSessionFanIn (one session + stream per user credential, behind `byob_data_tier`); `market_data/api_budget.py:350` BudgetedAdapter (Redis counters per credential); `brokers/rate_budget.py:75`; `market_data/shared_candles.py`; `market_data/live_access.py:93` one door for live reads; `live/routes.py:91` SSE; metrics `observability/metrics.py:113-117` | Extend StreamManager + UserSessionFanIn into one hub; SSE gets a ticks topic |
| 1B Kotak Neo | not started | only `broker_data_terms.json:31` (pending) and `docs/BROKER_DATA_TERMS.md` row; not in `brokers/registry.py:15-23` or `brokers/catalog.py` | `brokers/base.py:135-301`, `catalog.py`, `field_specs.py`, `totp.py`, `token_lifecycle.py`, `contract_symbols.py`, `smoke.py`; pattern `brokers/angel_one.py` (TOTP) / `dhan.py` |
| 1B Groww | not started | `broker_data_terms.json:29` (pending) only | as Kotak; pattern `brokers/upstox.py` (REST + websocket) |
| 2 Performance | partial | `trading/analytics.py:49` (win rate, PF, avg win / loss, by strategy / symbol), `trading/routes.py:396`; `backtest/analytics.py:39` drawdown, `:48` streaks, `:59` Sharpe; `decision_tools/own_stats.py` expectancy; `journal/review.py`; `TradeRecord.exit_reason` free text `db/models.py:330`; page `frontend/src/pages/AnalyticsPage.tsx` (summary + tax card); `components/EquityCurveChart.tsx` | Reuse backtest analytics on closed trades; one exit-reason mapper |
| 3 Sector heatmap | partial | `markets/service.py:233` overview (EOD, index + sector groups, breadth, leaders); `frontend/src/markets/MarketsPageV2.tsx:166` tile grid by group; `screener/ResultViews.tsx:24` + `boardViews.ts:81,105` stock treemap by sector with a diverging fill; weights parsed only if the file has them `universe/indices.py:66-79` | Stock-level tiles from the screener heatmap; groups from the system lists; live % through the hub |
| 4 Positions | partial | `frontend/src/trading/PositionsV2.tsx:276-317` groups of legs placed together (`paper/fno_routes.py:247`), cockpit; Greeks `trading/routes.py:129`; exit now `:336`; emergency exit `kill_switch/routes.py:204`; no "Exit all", no privacy mask, no MTM / premium chart | Group by underlying -> expiry on top of `groupRows`; Exit all = loop over the existing exit-now path |
| 5 Control bar + ladder | partial | pause / resume / stop `deployments/routes.py:710,728,753` (PAUSED keeps exits, `workers/trading_worker.py:411-414`); strategy kill switch `kill_switch/routes.py:128`; `trading/exit_rules.py:30` ExitRules (trail %, break-even R, time) shared with the backtest; JSON column on trade and deployment `db/models.py:405,592`; `strategy_templates/exits.py`; `frontend/src/paperbot/PaperBotV2.tsx` | Freeze = PAUSED; ladder = a new ExitRules field |
| 6 Trade terminal | not started | no grid page; the single ticket `frontend/src/fno/OrderTicketV2.tsx` + `paper/fno_ticket.py:449` | Every row = one ticket call |
| 7 Order types | partial | basket `paper/fno_basket.py:1-19` (hedge legs first, PAPER), `frontend/src/optionsBuilder/BasketDialog.tsx`, `fno/BasketPanelV2.tsx`; idempotency `execution/order_persistence.py:19`; intent queue `execution/core/intents.py`; OPS throttle `execution/ops_throttle.py`; algo tag `execution/tagging.py`; no GTT / OCO / ATO / iceberg / SIP | Basket + intent queue + a paper resting book |
| 7 Smart execution | partial | LIVE wings-first with confirmed fills and unwind `execution/multileg.py:376-448`; PAPER basket hedge-first `paper/fno_basket.py:15-19`; freeze qty missing `paper/fno_ticket.py:60` (FNO-1-2); margin estimate only (FNO-2-1), Upstox margin API exists `brokers/upstox.py:548`; no physical-settlement warning (STT on exercise only, `execution/india_costs.py`) | One sequencing state machine for both modes |
| 8 Safety gates | partial (strong) | ADR-0004 / 0006; `platform/phase0_lock.py:85` + chokepoint `brokers/rate_budget.py` (ENTRY intent refused); `execution/order_state_machine.py`; idempotency keys; `reconciliation/`; tests `tests/test_z10c_order_paths.py`, `test_z10c_phase0_lock.py`; load test `backend/scripts/loadtest.py` (B10) | Every new path declares its intent and goes through the chokepoint |
| 9.1 What-if | partial | `decision_tools/whatif.py` (T+0, a later day, IV shift), `decision_tools/routes.py:89`, expected move `:126`; builder payoff `options_builder/payoff.py`, `frontend/src/optionsBuilder/PayoffCanvas.tsx`; basket evaluate `paper/fno_basket.py` | Sliders + 1 sigma band + compare on the existing engine |
| 9.2 Stress test | partial | aggregated Greeks `trading/routes.py:129`; portfolio risk `portfolio/risk.py`, `/api/portfolio/risk`; no spot x IV x days grid | Grid = whatif over each subject |
| 9.3 Volatility | partial | ATM IV, ATM straddle, IV smile, session series `option_chain/charts.py:1-30`; India VIX with rank in the context panel; no IV Rank / Percentile per underlying | Daily ATM IV from F&O bhavcopy (CH4-4 Black-76) |
| 9.4 Chain history | partial | `option_chain/snapshots.py:110` collect (5-min slot, `OI_BANNER_COLLECT_SPAN` 15 strikes either side, nearest expiry, OI / LTP / IV only), tables `db/models.py:2083-2131`; worker `workers/trading_worker.py:837-874`; retention 400 days `retention/policy.py:52`; tenant-private pattern `market_lake/tenant_store.py` | See section 4 |
| 9.5 OI + participants | partial | build-up classes `option_chain/oi_regime.py:47-48,258`; OI change by strike `option_chain/charts.py`; participant file not ingested (`charts/futures_basis.py:18`, CH4-4) | New NSE EOD ingest next to `market_lake/bhavcopy.py` |
| 10 Replay | partial | `frontend/src/charting/ReplayBar.tsx` + `replay.ts` (step / play, price-action layer as of each bar); no speed, no paper orders in replay, no options replay | Extend ReplayBar; a replay-only order sandbox |
| 11 Helpers | partial | roll target for watchlist contracts `paper/fno_contracts.py:143`; "expires today" warnings `paper/fno_ticket.py:370`, `execution/prechecks.py:84`; no roll what-if, no expiry-day reminder | Roll = two baskets through 9.1 |
| 12 AI on top | partial | read tools `ai/tools/copilot.py`, `ai/tools/copilot_trades.py:115,153,198`; journal auto-capture `journal/capture.py`; detectors `journal/detectors.py`; weekly review `journal/review.py`; PR #460 (own AI key) | A read-only stress-test tool |

## 2. Findings (each has a TM question with the provisional default)

### Cross-cutting

- **F1 Paper is a branch, not an adapter (TM-1).** The owner principle (Part 8: "paper = a broker adapter, not a
  separate feature") is not how the code works. Resolution: a `PaperExecutionAdapter(BrokerInterface)` with
  `place_order / modify_order / cancel_order / get_order_book / get_positions` over the existing `PaperBroker` fill and
  cost model, so the router, the basket and the new order types call one interface. Existing paths move onto it one at
  a time, each with a parity test (old path = adapter path, same fills, charges, order trail). Major change: owner
  approval (CLAUDE.md section 8).
- **F2 No resting PAPER order (TM-2).** FNO-1-3 refuses an unmarketable PAPER limit. GTT, OCO, ATO, SIP, iceberg and the
  terminal's limit rows need a resting book and one trigger engine. Additive migration (`paper_resting_orders`), after
  15:30 IST.
- **F3 The phase0 lock and resting orders (TM-23).** The lock refuses ENTRY at the chokepoint when an order is sent. A
  native broker GTT / SIP placed while LIVE is allowed fires later at the broker, where ATP's kill switch cannot stop it.
  Default: the platform's own trigger engine for every type (it passes the chokepoint and the kill switch at fire time);
  a native broker order only as a per-user opt-in, and engaging any kill switch cancels the user's native resting
  orders first.
- **F4 Market orders (TM-3).** The Kotak Neo page says market orders are not allowed for retail algo orders. Today's
  LIVE legs, unwind and many exits are MARKET (`execution/multileg.py:409,442`). Default: an adapter capability
  `market_orders_allowed`; where false, a marketable limit with a protection band from the user's settings, repriced
  until filled. Exits keep priority and are never blocked (ADR-0004).
- **F5 SEBI OPS / algo registration (TM-4).** Every bot, SIP, GTT engine and the terminal's multi-row send is
  automation. Default: per-user OPS kept under the broker's threshold through `execution/ops_throttle.py`; exchange algo
  id through `execution/tagging.py`; on the lawyer's list before LIVE.

### Part 1

- **F6 The hub exists in two halves behind two switches (TM-13).** `StreamManager` (per tenant session, env
  `STREAMING_QUOTES_ENABLED`, default false, `core/config.py:254`) and `UserSessionFanIn` (per user credential, flag
  `byob_data_tier`, off). The API process calls the broker per request (charts, chain `/live`, quotes) through the
  budget and the shared candle cache. Default: one hub keyed by the credential row; `byob_data_tier` keeps deciding who
  may read (the hub never relaxes the data terms).
- **F7 No browser data channel (TM-12).** Default: a `ticks` topic on the existing SSE stream, only the symbols that
  page registered for that user, at most one update per symbol per second; no new websocket.
- **F8 Cap vs breadth (TM-14).** `BYOB_SESSION_SYMBOL_CAP` 50 (`core/config.py:349`). A Nifty 500 heatmap cannot be
  streamed. Default: batched quotes for the open index only, on the budget, EOD labelled with its time otherwise.
- **F9 Streams for two brokers only (TM-27).** Decoders exist for Upstox and Kite; Dhan, Angel One, Fyers, Shoonya have
  none; Groww's daily caps (below) make REST polling impossible. Each new adapter brings its stream.

### Part 1B

- **F10 Data terms pending (TM-6).** Kotak Neo and Groww are "pending" = treated as execution-only until written
  confirmation. The adapters can be built and tested on recorded fixtures; their prices are not shown until the JSON
  row is "confirmed".
- **F11 Static IP and one server IP (TM-5).** See section 3. Default: PAPER and the account reads work; LIVE orders
  through Kotak Neo / Groww are not offered until the lawyer and the brokers confirm the model.
- **F12 The order's Kotak facts are partly out of date (TM-26).** The current SDK (`kotakneoapi` 3.0.9, 15 Oct 2026)
  accepts only CNC / MIS / NRML / MTF and says Bracket and Cover orders are no longer supported. Default: no CO / BO
  product; brokerage is a per-broker plan setting (currently one number per order, `execution/india_costs.py:292`); ATP
  shows only what the broker states, with the date read.

### Parts 2-6

- **F13 Exit reasons are free text (TM-16).** `exit_reason` is `String(100)`. Default: a pure mapper to the order's
  categories with an "Other" bucket and tests; no migration.
- **F14 Ladder needs rule-driven partial exits (TM-17).** The monitor handles partial broker fills
  (`trading/position_monitor.py:308`) but no rule closes part of a position, and both backtest engines close whole
  trades. Default: `ExitRules.profit_ladder` (JSON, no migration for the setting); a tier closes N lots by splitting
  the trade row; identical in the monitor and both backtest engines (parity tests). Order-path change: owner approval.
- **F15 "Force start" (TM-18).** Default: start / resume evaluation now; never an entry without a signal (CLAUDE.md
  section 3). "Freeze" = the existing PAUSED.
- **F16 "Pro tool" wording (TM-29).** Exactly two groups (DECISIONS 21 Oct). Default: the Trade terminal is a Trade-menu
  page for every USER with a page tour; no tier.
- **F17 Heatmap weights (TM-15).** Shown only where the constituent file carries a weight; never guessed.
- **F18 Privacy mask scope (TM-19).** Default: app screens only, per device; exports and Telegram unchanged and say so.
- **F19 MTM overlay history (TM-20).** No marks history is stored. Default: rebuilt on read from the legs' contract
  candles through the user's own session (no migration), "builds through the session" when missing.

### Parts 7, 9-12

- **F20 Freeze quantity (TM-9).** Not in the master (FNO-1-2). Default: an additive `freeze_qty` column from NSE's
  published file; until present, refuse above the ticket cap (500 lots) - never guess a limit.
- **F21 Margin with hedge benefit (TM-10).** Contradicts FNO-2-1 ("no broker margin API"). Default: the broker's
  basket margin API where the adapter has one (Upstox `/charges/margin`), through the user's own session, shown in
  PAPER too; else ATP's labelled estimate. Supersedes FNO-2-1 only on the owner's word.
- **F22 Incomplete multi-leg (TM-11).** LIVE auto-unwinds (`multileg.py:433`); the PAPER basket never undoes a fill
  (`fno_basket.py:17-19`). Default: one state machine for both: an uncovered SHORT is never left (no short is sent
  before its hedge is confirmed); otherwise the group shows "Incomplete" with "Complete the rest" / "Close the filled
  legs", the user's tap only.
- **F23 Physical-settlement band (TM-24).** "Near-ITM" is a number: a user setting.
- **F24 IV Rank needs a year of IV (TM-21).** Default: daily ATM IV from the stored F&O bhavcopy (CH4-4 method), shared
  licensed EOD; the intraday chart from the owner's own recording only.
- **F25 Participant OI (TM-25).** NSE's daily file is not ingested; public EOD reference data, source check in
  `docs/DATA_SOURCES.md`; one line in the morning prep in structure words.
- **F26 Replay orders (TM-22).** Replay orders never touch the real PAPER book or the journal; a replay sandbox.
- **F27 Signal language.** Performance, positions and the ladder show the user's own stop / target settings (allowed:
  his own records). The roll what-if, stress test and Copilot answers stay numbers and options, never a recommendation;
  the existing advice-word filters apply. Public / tour screenshots: no names or prices.
- **F28 Small things (TM-30..TM-32).** `ReplayBar.tsx` uses `text-xs` (below the 14 px rule) - fix in Part 10. The
  "moved the stop closer" pattern is a new detector (`moved_stop_against` measures the other direction). The earlier
  `ATP_SESSION_TERMINAL_FEATURES.md` named in the order is not on main (nothing to supersede in git).

## 3. Part 1B: the brokers' current official facts

Network note: on 23 Oct 2026 the build machine could not open kotakneo.com, kotaksecurities.com, groww.in or sebi.gov.in
(the proxy refused them). The facts below come from the official SDK pages that could be opened and from search
results quoting the official pages. **Everything marked "unverified" must be checked on the official page before the
adapter PR, and the date read written in `docs/BROKER_DATA_TERMS.md`.**

### Kotak Neo Trade API

| Topic | Fact | Source (read 23 Oct 2026) | State |
|---|---|---|---|
| Auth | Consumer key from the NEO app (More -> Trade API); `totp_login(mobile, ucc, totp)` gives a view token + session id; `totp_validate(mpin)` gives the trade token; TOTP registered once on the API dashboard (QR valid 5 min, 6 digits / 30 s) | https://pypi.org/project/kotakneoapi/ (v3.0.9, 15 Oct 2026); https://github.com/Kotak-Neo/Kotak-neo-api-v2 (archived) | verified (SDK) |
| Static IP | Order APIs only from a whitelisted IP from 1 Apr 2026; the session must be created from the same IP; up to 10 family members per IP, only the parent changes it | https://www.kotakneo.com/platform/kotak-neo-trade-api/static-ip-details/ (search extract) | unverified |
| Market orders | "Not allowed for retail algo trading"; limit orders recommended | same page (search extract) | unverified |
| Products | CNC, MIS, NRML, MTF; Bracket / Cover "no longer supported" | pypi page | verified (SDK) |
| Order types | L, MKT, SL, SL-M; validity DAY / IOC / GTC / EOS / GTD (v2 README); AMO flag; GTT / OCO not documented | GitHub README | verified (SDK); GTT unverified |
| Websocket | SFeed async client (`subscribe_scrips`, market status), order feed `wss://<baseurl>/realtime`; no published subscription cap | pypi page | partly verified |
| Rate limits | no number on the SDK pages; the order's "10 orders / sec" not found | - | unverified |
| Option chain / Greeks / history | option chain and historical candles listed as SDK docs; Greeks not mentioned | pypi page | unverified |
| Margin | `margin_required(...)` | GitHub README | verified (SDK) |
| Charges | Rs 0 API brokerage on Trade Free "at present" (one third-party comparison dated 13 Oct 2026) | search result only | unverified |

### Groww Trade API

| Topic | Fact | Source | State |
|---|---|---|---|
| Auth | daily access token by API key + secret (daily approval), TOTP (token + secret from the API Keys page) or OAuth2 | https://groww.in/trade-api/docs/curl (search extract) | unverified |
| Rate limits | by type, shared within the type: Orders 15 / s, 250 / min, 3,000 / day; Live data 10 / s, 300 / min, 5,000 / day; Non-trading 10 / s, 250 / min, 3,000 / day | same (search extract of the official table) | unverified (the daily caps matter: TM-27) |
| Static IP | order placement must come from a static IP registered on the API Keys page | https://groww.in/blog/static-ip-api-trading-setup | unverified |
| Websocket | 1,000 subscriptions (third party) vs 3,000 per user (Groww blog) | https://groww.in/blog/api-trading-on-groww ; chittorgarh.com review | conflicting |
| GTT / OCO | "Smart orders" (GTT, OCO) | Groww blog | unverified |
| Option chain / Greeks / history | the order says option chain + Greeks and 3 months of history; not confirmed | - | unverified |
| Price | Rs 499 / month + tax (marketcalls.in, undated) | search result | unverified |

### SEBI retail-algo framework (for the lawyer's list, A6)

- SEBI circular of 4 Feb 2025 (safer participation of retail investors in algorithmic trading): no open APIs; access
  only with a vendor-client-specific API key and a static IP whitelisted by the broker; brokers are principals, algo
  providers their agents (empanelled with the exchange); client algos above an orders-per-second threshold registered
  with the exchange; an exchange algo id on every algo order. Original date 1 Aug 2025; later extended (secondary
  sources say 1 Apr 2026). Sources: indialaw.in note, Upstox community guide
  (https://community.upstox.com/t/a-guide-for-our-api-algo-partners-and-clients/11826), NSE implementation standards of
  5 May 2025 (secondary). Unverified on sebi.gov.in / nseindia.com.
- Questions for the lawyer: (1) may unrelated users' orders leave from ATP's one server IP, and must ATP be an
  empanelled algo provider with each broker for that; (2) is each user's own bot / SIP / GTT on ATP a "client algo"
  needing registration; (3) the OPS threshold per user vs per IP; (4) Kotak's family-of-10 per IP rule vs a platform IP;
  (5) whether the PAPER path (no broker order) is outside the framework.

## 4. Part 9.4: chain history

**What exists.** `option_chain/snapshots.py:110` stores one slot per underlying (5 minutes, `OI_BANNER_SLOT_MINUTES`),
the nearest expiry only, 15 strikes either side, with call / put OI, LTP and IV (`strike_oi_snapshots`) and the first OI
of the day (`oi_day_baselines`). `day_chains` (`:166`) rebuilds a day for the banner, the charts and the alerts. It runs
only for underlyings some tenant enabled for the OI banner, through the first tenant with a usable broker session, and
is skipped for a tenant with `byob_data_tier` on. Rows are Postgres, uncompressed, deleted after 400 days. The broker
chain row already carries bid / ask / volume / OI change and delta (`brokers/models.py:73-92`); these are dropped.
`market_lake` holds candles only (`recorder.py` ticks -> 1-minute bars; `tenant_store.py` the tenant-private candles).

**Whose token.** Upstox data may be shown because "each call needs that user's own access token"; RDY-6 (answered):
never the platform's or another user's feed. So a recording made through one user's token is that user's. Default
(TM-7): the recorder reads only through the owner's own Upstox session (the Analytics Token, data-only) and writes to a
tenant-private store readable by the owner's tenant only (his research, backtests, replay). Other users get chain
history only from a licensed source or after Upstox confirms in writing; the existing OI banner collector is left as it
is and listed for the owner / lawyer.

**Minimal design (one PR, additive migration).**
- `market_lake/chain_store.py`: table `md_tenant_chain_snapshots` (tenant_id, underlying, expiry, slot_start,
  captured_at, spot, future, source, `payload` = zlib-compressed JSON of every strike: LTP, bid, ask, volume, OI, OI
  change, IV, the broker's Greeks, plus computed Greeks flagged as computed). Unique (tenant, underlying, expiry, slot).
  Every read takes the tenant id and refuses None (as `tenant_store.py`).
- A worker step next to `_oi_banner`: NIFTY, BANKNIFTY, SENSEX (BSE chain: verify on the owner's token, TM-32), the
  nearest two expiries, every `CHAIN_HISTORY_SLOT_MINUTES` (setting, default 1), through `api_budget` - about 6 calls per
  minute. Settings: `CHAIN_HISTORY_TENANT_ID` (empty = off), the underlying list.
- Retention: exempt (TM-8); a nightly Parquet roll-up to R2 later (ADR-0013), when the off-site store exists.
- Size check: about 200 strikes x 2 expiries x 3 underlyings x 375 slots a day, compressed, is a few MB a day (measure
  on the first day and report).
- Tests: idempotent slot, tenant isolation (another tenant reads nothing), BYOB isolation, compression round-trip, no
  order path reachable.

## 5. Part 1: the data hub gap and how to measure it

**Gap to "one session + one stream per user connection, every consumer subscribes":** (a) consumers in the API process
call the broker per request (`/api/option-chain/{u}/live`, `/api/market-data/quotes`, candles) instead of reading the
hub; (b) the hub's two halves need one owner keyed by the credential; (c) streams only for Upstox and Kite; (d) no data
to the browser; (e) the chain is polled per request, not shared within the user's own session; (f) one "reconnecting"
state and one "renew your login" prompt for all pages (token lifecycle exists in `brokers/token_lifecycle.py`,
`login_reminder.py`).

**Measurement (in the Part 1 PR, before = main, after = the hub, same script):**
- A script `backend/scripts/hub_probe.py` that opens, for one user, a chart, the chain, a 50-symbol watchlist, a scan
  and two alerts for 10 minutes against a recorded fake broker (CI) and against the owner's token on the host (owner
  runs it after asking).
- Broker calls per user per minute: the per-credential counters `api_budget` already keeps in Redis, plus
  `atp_broker_calls_total` deltas; rate-limit errors: a new `outcome="rate_limited"` label on `BROKER_CALLS`.
- One price: at one instant, LTP read from three surfaces compared (tick `received_at` equal).
- Reconnect: the fake socket drops; `atp_stream_reconnects_total`, the time to resubscribe, the UI state.
- Token expiry: the fake returns 401; one notification, everything resumes after the renew.

## 6. Build plan (the order's ORDER; one draft PR each)

| # | PR | Scope / files | Tests | Migration | Size |
|---|---|---|---|---|---|
| 1 | Review (this) | this file, TM-1..32 | guards | no | S |
| 2a | Part 1 hub | `market_data/hub.py` (owns StreamManager + UserSessionFanIn), consumers read the hub, SSE `ticks` topic in `live/routes.py`, `frontend/src/live/`, metrics label, `hub_probe.py` | fake socket reconnect, 401 renew, cap, isolation (BT1b), one-price test, load (B10) | no | L |
| 2b | 9.4 chain recorder | `market_lake/chain_store.py`, worker step, settings | as section 4 | yes, additive | M |
| 3 | Part 7 smart execution | `PaperExecutionAdapter`, one sequencing state machine (hedge first, reverse on exit, Incomplete state), freeze slicing, margin API, settlement warning | parity old path = adapter, idempotent slices, partial fills, unwind, ADR-0004 exits | yes (freeze_qty, resting book) | L - owner approval first (TM-1/2) |
| 4 | 1B Kotak Neo, then 4b Groww | adapter, catalog + connect guide, stream decoder, contract symbols, rate budget (incl. daily caps), recorded fixtures (secrets built at runtime) | smoke, mapping, budget, chokepoint | no | M-L each |
| 5 | 9.1 + 9.2 | sliders, 1 sigma band, compare, stress grid, "add this trade" | pure-function goldens | no | M |
| 6 | Part 2 / Part 3 (parallel) | performance API + page; heatmap tiles + index panel | metric goldens, empty states; tile colour scale | no | M / M |
| 7 | Part 4 | grouping, Exit all, privacy mask, MTM + premium charts | exit never blocked, mask everywhere | no | L |
| 8 | Part 5 | control bar, `profit_ladder` | parity monitor = both backtests | probably no (JSON) | L - owner approval (TM-17) |
| 9 | 9.3 + 9.5 | IVR / IVP, IV charts, VIX overlay, participant OI ingest | ingest fixtures, no look-ahead | yes (participant table) | M |
| 10 | Part 7 rest | GTT / OCO / ATO / iceberg / SIP on the resting book + trigger engine | fire once, kill switch at fire, restart safety | yes | L |
| 11 | Part 10 | speed, replay sandbox orders, reader overlay, options replay | strictly causal | no | M |
| 12 | Parts 11, 12, 6 | roll what-if, expiry reminders; Copilot stress tool + new detector; trade terminal | read-only tools (ADR-0006), each row = ticket path | no | M / M / L |

Every PR: behind `ui_v2` plus its own flag (registered in `platform/flag_catalog.py`), routes in
`tests/tenancy_route_table.py`, screenshots 360 / 390 / 1440 light / dark, the deploy line and the migration answer.
