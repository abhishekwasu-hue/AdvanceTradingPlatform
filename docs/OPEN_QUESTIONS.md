# Open questions

Only the OPEN items that are current (token budget, owner 24 Oct: this file stays under 30 KB). Each row has a provisional
decision taken so work continues. When answered: mark it, then move the row to docs/archive/OPEN_QUESTIONS_CLOSED.md.
Older open rows whose provisional default is already in effect: docs/archive/OPEN_QUESTIONS_PROVISIONAL.md. Answered /
closed rows: docs/archive/OPEN_QUESTIONS_CLOSED.md. Both are moved, never deleted - grep them by ID.

## Terminal master Part 9.4: chain history and the OI collector fix (TM94, 23 Oct)

| # | Question | Provisional default | Where | Status |
|---|---|---|---|---|
| TM94-1 | Who is "the owner" for the recorder: the SUPER_ADMIN's organisation or a configured tenant? | The configured one, HB0 / HB2's rule: `HISTORY_OWNER_TENANT_ID` confirmed by `HISTORY_OWNER_TENANT_NAME`, the session of `HISTORY_OWNER_USER_ID` (else the lowest-id active OWNER). A role edit can never move the recording; one owner rule for every owner-only data path. | `market_lake/chain_recorder.py` | open (default applied) |
| TM94-2 | OI rows stored before the fix carry no organisation. | Read only by the confirmed owner (they were read through the first organisation with a session - in production the owner's); nobody when no owner is confirmed. Deleted by the 400-day retention as before. | `option_chain/snapshots.py` | open (default applied) |
| TM94-4 | SENSEX chain on Upstox (TM-32). | The adapter now looks SENSEX / BANKEX up in the BSE master; verify on the owner's token on the first day (the admin card shows a gap with the reason if the read fails). | `brokers/upstox.py` | open (verify on the host) |
| TM94-5 | With `byob_data_tier` on, the OI collector still skips that organisation, although its slots are now its own. | Unchanged (no widening in this PR); can be allowed later through the member's own session. | `workers/trading_worker.py` | open |
| TM94-6 | Greeks the broker does not send (only Upstox sends them). | Stored as missing; a later reader computes them on read and labels them as computed. Nothing is stored that the broker did not send. | `market_lake/chain_store.py` | open |
| TM94-7 | The OI collector now reads once per organisation (was once for all). | Each read is on that organisation's own rate budget (one chain call per enabled underlying per 5 minutes); fine for the beta cohort; revisit with the Part 1 data hub. | `workers/trading_worker.py` | open |

## Terminal master Part 1: the data hub (TMH, 23 Oct)

| # | Question | Provisional default | Where | Status |
|---|---|---|---|---|
| TMH-1 | "ONE renew prompt": a hub notification as well as the worker's existing "session expired" notification would make two notices for one ended login. | The hub sends no notification of its own. The prompt is the app's one notice (above every page, one "Log in again" button, hidden while the page's own broker card asks) plus the worker's existing single notification. | `market_data/hub.py`, `frontend/src/live/LivePricesBanner.tsx` | open |
| TMH-2 | byob off: the organisation's connection. The worker streams its first usable adapter; the API's `/ltp` picks its first usable row - with two brokers linked they can differ. | One connection per organisation (`org.t<tenant>`); its ticks answer for the organisation whichever of its own rows the API picked (same organisation, never another's). | `hub.org_feed` | open |
| TMH-3 | The symbol cap per connection: `BYOB_SESSION_SYMBOL_CAP` 50 (an ATP figure, not the broker's). Upstox and Kite allow far more per socket. | Keep 50 (positions first); the rest stays on one batch call. Raise by environment after the owner's host run shows the CPU and memory. | `core/config.py` | open |
| TMH-4 | Per-tick Redis writes do not scale (25,000 a second at 500 x 50). | Hub streams publish one snapshot per connection per second; the API reads at most a second behind the stream (the worker's decisions read the tick in its own process, at once). | `HubTickCache` | open |
| TMH-5 | The option chain itself has no stream, and the chain page's spot still comes with the chain (15 s). | Shared chain snapshots (5 s) now; the chain on a stream and ticks on the chain page with Part 4 / 9.3. | `hub.shared_chain` | open |
| TMH-6 | A hub stream stops on a refused token (401 / 403); a stream outside the hub keeps retrying as before. | Only hub streams stop (opt-in flag on the stream); network failures are retried on the same backoff everywhere. | `market_data/stream.py` | open |
| TMH-8 | Found in passing: the Phase S org stream (`_stream_quotes`, env `STREAMING_QUOTES_ENABLED`, off in production) hands the worker's rate-budget wrapper to the stream, which has no `_request` / instrument lookup - an Upstox stream there would fail on connect. | Not changed here (that path stays as it is). Hub streams get the broker adapter under the wrappers (`hub.stream_adapter`); the old path is fixed only if the owner wants the env switch used. | `workers/trading_worker.py` | open |
| TMH-7 | Trader pack: `data_hub` should join the "Enable trader features" list. | Added to `trader_pack.PACK` (after `ui_v2`; #455 merged before this PR): "Enable trader features" turns it on, "Undo" turns it off again. | `platform/trader_pack.py` | open |

## Signals day one (SIG1, 23 Oct)

| # | Question | Provisional default | Where | Status |
|---|---|---|---|---|
| SIG1-1 | What does "adopt as a PAPER strategy in approval-only mode" create? | His own watch (one internal `user_prefs` row: the template's strategy id with its default settings, the list, `PAPER`, entries `APPROVAL` - server constants). No deployment rows: a deployment is one symbol and needs a broker session a new user may not have, and 50 bots would hit the plan limits and the broker budget. The watch only describes; "Start a paper bot with it" opens the existing form with the template picked (approval-only entries there under `approval_only_entries`). | app/signals_feed/day_one.py | open (owner may choose "create the bots") |
| SIG1-2 | Which ready strategies does the card offer? | A5 Structure flip retest, B1 Range breakout, D3 Moving-average pullback in trend, on the Daily chart, with their own defaults; only those switched on for the organisation (A5 / B1 need the breakout screen flags, D3 none). | `OFFERS` | open |
| SIG1-3 | Which lists? | The NIFTY 50 system list first (default), the other index-stock, sector and F&O lists, and his own lists with `watchlists` on. The Indices list is not offered (not a list of shares). At most 60 symbols are read; a longer list says so. | `list_choices`, `MAX_WATCH` | open |
| SIG1-4 | Data for the first run. | The shared daily lake only (NSE bhavcopy / vendor / import, closed bars, split-adjusted), read in one batch (`load_daily`, the one seam a shared history resolver replaces). No broker call; no history = "No Daily history for this symbol yet", never sample data. | `load_daily` | open |
| SIG1-5 | "Formed recently". | A display window of 5 Daily bars (not a reading rule; the logic is the template's own). | `RECENT_BARS` | open |
| SIG1-6 | When is the watch read again? | On adopting; on opening Signals when the last read is older than 4 hours; on "Refresh" (at most once a minute). No worker job, no notification. | `STALE_SECONDS`, `MIN_REFRESH_SECONDS` | open |

## Copilot on the user's own key, tools wired (OKT, 23 Oct)

| # | Question | Provisional default | Where | Status |
|---|---|---|---|---|
| OKT-1 | The order's screens and multi-view in the chat: new jobs or inside the eight? | Two new jobs after the handover's eight: 9 "Find setups" (screens) and 10 "Full view of a symbol". Jobs 1 and 4 also read the news feed and market memory. | `app/ai/copilot_chat.py` JOBS | open |
| OKT-2 | Which flag lets the chat read news and market memory? | `ai_agent` (its description already names them); screens `screener_copilot` (+ `screener_v2`); the full view `ai_multiview`. Off: "I don't know yet - ... is not set up"; the flag name only in the SUPER_ADMIN's diagnostics. | `CHAT_TOOL_FLAGS`, `JOB_FLAGS` | open |
| OKT-3 | The universe of a chat screen. | The user's own first watchlist (his order, NSE symbols, at most 50), else the "NIFTY 50 stocks" system list; the answer names the list. | `tools/screener.get_screen_universe` | open |
| OKT-4 | May the chat's rules propose an alert? | Yes, only when the trader's own words ask ("alert me when ..."), on one named symbol, through the agent's own guard (his words, one per question, not from third-party text); it is only PROPOSED and he approves it under AI Copilot (ADR-0006). | `chat_routes._runner` | open |
| OKT-5 | News headline text in a chat answer. | Not shown: a count and the high-severity count, with a pointer to the News page (third-party text may carry trade words). | `copilot_chat._news_line` | open |
| OKT-6 | The full view's technical part. | ATP's structure reader per timeframe, not the indicator regime (handover 5 C: the regime is an admin diagnostic only). | `copilot_chat.full_view` | open |
| OKT-7 | "Why did these pass" in the chat. | Each stage's pass / not passed only; the screen engine's own numbers stay on the Screener page (no single prices in the chat). | `copilot_chat.screen` | open |
| OKT-8 | The user's own key at its own limit, on the Copilot page. | The page still opens and answers from ATP's rules with the plain limit line; never the organisation's key (SR-4). | `chat_routes._provider` | open |
| OKT-9 | `ai_one_key` catalog status. | `ready` (one key works end to end on any plan, the Free plan included). Switching it ON in production is the owner's step (Admin → Feature flags; it needs `ui_v2`, which is ON). | `platform/flag_catalog.py` | open (owner) |
| OKT-10 | The scan read ("read this scan") was still on the organisation's key only. | Moved to the user's own key first (user-started, SR-6). The research loop stays on the organisation's key (it queues a background job). | `app/scanner/ai.py` | open |

## The scan universe is never empty (SU, owner order 23 Oct)

| # | Question | Provisional default | Where | Status |
|---|---|---|---|---|
| SU-1 | NIFTY 50 when the server has no NIFTY 50 list (the system lists not built yet, or the call failed). | First the system list `index-nifty50`; else the current NIFTY 50 membership the universe sync stores (`index_membership` + `securities`); only when neither exists, a static names-only list in the page (`NIFTY50_FALLBACK`, 50 NSE symbols, no prices or weights; may lag a reconstitution until the system list exists - a stale name just reads "no bars"). | `universe.ts`, `universes.py` | open |
| SU-2 | A universe over 50 symbols: raise the run cap or batch? | Batch; the run cap stays 50. Each batch is a `/run` with `batch: true`, one in flight at a time; every broker call of a batch takes a token from the credential's shared API budget (`api_budget.BudgetedAdapter`, the budget charts use: half of `rate_budget.py`'s conservative limits), so a batch can never spend past it - a symbol without a token reads "waited for the broker's limit" and the page offers to run again. Batch size = what the per-second share fetches in about 10 s (Upstox 40, Zerodha / others 10); the page waits after each batch for the calls it made at the per-minute share (cached bars cost nothing, so a warm NIFTY 500 runs at once). Raising the cap would make one request hold up to 500 broker calls (minutes on Zerodha) and could not report partial results. Ordinary runs of 50 or fewer are unchanged (no token taken). | `universes.py`, `routes.py` (`fetch_frames budget_out`), `runInBatches` | open |
| SU-3 | Upstox's 30-minute window: the API budget allows 500 calls per 30 minutes per credential; a cold NIFTY 500 scan on an intraday timeframe (bars + daily context) can use it, and charts then read "budget used" until it frees. | Kept: the budget refuses past the window (never over the broker's allowance), the default stays NIFTY 50, and daily bars are cached and shared. If it bites, cap a batch run's share of the 30-minute window (e.g. half). | `api_budget.py` | open |
| SU-4 | Scan alerts on a list over 50 stocks. | An alert saved from a run keeps the chosen universe, up to the alert engine's 50 symbols (the first 50 of the list, said in the dialog). Raising the alert cap adds bar-close fetches to the trading worker's budget (which its exits share), so it waits for the owner. | `ScanAlertPanel.tsx`, `alerts/rules_routes.py` | open |
| SU-5 | A long list's answer (over 50 rows). | Matches first: the matches (and any stock the broker's limit held back) are listed, the others are counted ("425 stocks did not match"), with "Show all". | `ScreenerPage.tsx` | open |

## Backtests without a daily broker login (BSH, 23 Oct)

| # | Question | Provisional default | Where | Status |
|---|---|---|---|---|
| BSH-4 | Prices and window of stored history. | Split / bonus adjusted (as a broker's chart history; dividends only with `LAKE_ADJUST_DIVIDENDS`); the broker path's Daily window exactly: at least 400 calendar days, up to yesterday (today's bar is never read); each bar stamped like a broker's Daily candle, so equal bars give an identical result (parity test). Weekly is built from Daily by the India calendar week. | `bar_source.window`, `to_frame`, `weekly` | open |
| BSH-5 | Which entry points use it. | Every server-side backtest read: the Copilot `run_backtest` / `backtest_draft` tools, the AI draft backtest route, the interview plan (intraday: the plain refusal), the research study job, and the new `POST /api/backtest/bars` that the Backtesting page and the setup "Test it" call when no broker is usable. With a usable broker each path reads exactly what it read before. Unchanged: the screener backtest / history (already lake-only), the strategy chart-run (the chart's own candles), the template adopt route (runs no backtest), Monte Carlo / walk-forward / optimise (they re-use the bars the run was given). | see the list | open |
| BSH-6 | The v2 broker gate on the Backtesting page. | With the flag on, `/strategies/test` and `/backtest` show their own broker state (the data bar's sentence and "Open Broker") instead of the gate; every other data page keeps the gate. | `ui/dataPages.STORED_HISTORY_PATHS`, `AppShellV2` | open |

## Trader pack: "Enable trader features" (TP, 23 Oct)

| # | Question | Provisional default | Where | Status |
|---|---|---|---|---|
| TP-1 | Ready flags the owner did not list (screener_intrabar, screener_vectorized, screener_venues, screener_breakouts_fitness, screener_etf, ladder_evidence, experience_levels, portfolio_risk_caps, news_feed, telegram_inbound, backtest_optimizer, backtest_models, vision_byok). | In the pack: each is "ready" / "behind the scenes" and serves a trader. experience_levels and portfolio_risk_caps only ever hold back a new PAPER entry (restrictive); exits are never blocked. | `trader_pack.PACK` | open |
| TP-2 | "Unfinished" flags in the pack: markets_view, strategy_templates, idea_library, ai_one_key, daily_rhythm, strategy_lab_pine. | In: their routes and pages are merged and work end to end today (the catalog status is the 22 Oct audit). Out: market_thesis, ai_research, vision_byok_v3, support_bot, growth_v2, screener_waves, chart_analyze (+ strikes, share), chart_contracts, chart_futures_basis, and the data-less ones (history_import, history_intraday, screener_fundamentals, screener_ownership, screener_legends_lg3, alerts_whatsapp_dlt); reasons in `trader_pack.EXCLUDED_REASONS`. | `trader_pack.py` | open |
| TP-3 | Undo after a switch was changed by hand after the enable. | Undo still restores the stored state from before the FIRST enable, for every flag the pack changed (exactly what the order says); a second enable never overwrites that saved state. | `trader_pack.undo` | open |
| TP-4 | A pack flag that was OFF with an organisation allow-list. | Enable turns it ON for everyone and keeps the list; Undo puts back OFF with the same list. | `trader_pack.enable` | open |
| TP-5 | The A4 admin write step-up (`ensure_admin_write_step_up`) is not on main. | The two POSTs use the admin router's SUPER_ADMIN + two-factor session (`require_mfa_session`); when A4 lands they take its step-up like every admin write (note in `routes.py`). | `app/platform/routes.py` | open |
| TP-6 | "Working" checks are platform-wide counts (organisations with a broker connection, AI keys of organisations and users, ready-made lists, instrument rows, stored daily bars), not one organisation's. | Platform-wide: the admin turns the flags on for the whole platform. A key is never read or decrypted, only counted. | `trader_pack._facts` | open |

## Smart execution and the PAPER adapter (SE, 23 Oct)

| # | Question | Provisional default | Where | Status |
|---|---|---|---|---|
| SE7-6 | Not in this PR: freeze slicing of structure legs, the timer for the F&O basket's PAPER incomplete card, a stuck COMPLETING_SENT after a worker crash mid-send (the legs stay closable; reconciliation flags the books). | Next, one at a time. | - | open |

## TM94-3: the Phase W recorder per organisation (23 Oct)

| # | Question | Provisional default | Where | Status |
|---|---|---|---|---|
| TM943-1 | Rows recorded before the fix carry no organisation. | Read only by the confirmed owner (`HISTORY_OWNER_TENANT_ID` + `HISTORY_OWNER_TENANT_NAME`, the same `legacy_owner` rule as #465); nobody when no owner is confirmed; retention (400 days) deletes them as before. Not assigned to an organisation by guess. | `backtest/chain_recorder.py` | open |
| TM943-2 | Uploaded rows (`source = upload:<tenant id>`) before the fix have no `tenant_id`. | No backfill (no row rewrite, rollback-safe): the readers keep honouring the source; new uploads also carry `tenant_id`. | migration `3c5e7a9b1d24` | open |
| TM943-3 | Two organisations trading options on the same underlying now each read the chain (was once for all). | Each read is on that organisation's own session and rate budget (one chain call per underlying per 5 minutes, usually the chain its strike filter fetched that cycle anyway). Fine for the beta cohort. | `workers/trading_worker.py` | open |
| TM943-4 | A backtest without an organisation (no login) used to price from the platform-wide rows. | It gets no recorded rows (the synthetic fallback or uploaded rows still work). | `chain_recorder.load_rows` | open |
| TM943-5 | Migration chain: `3c5e7a9b1d24` and #465's `250d796123b7` both follow `e8a0c2d4f6b9`. | Whichever PR merges second re-chains its `down_revision` onto the other (one head; `alembic heads` must print one line). | `alembic/versions` | open |

## Kotak Neo adapter (KN, 23 Oct)

| # | Question | Provisional default | Where | Status |
|---|---|---|---|---|
| KN-1 | When may the Kotak Neo gates open? | Both stay closed in code (no flag): LIVE orders until the lawyer and Kotak Neo confirm the one-server-IP model in writing (TM-5); market data until the data-terms row is "confirmed" with Kotak's letter (TM-6). | `brokers/gates.py` | open (owner: lawyer / broker letter) |
| KN-2 | A position the user opened himself in the Kotak app: may ATP close it? | No: while the LIVE-order gate is closed ATP opens no LIVE position at Kotak Neo and places nothing there; the user's own positions are shown read only and closed in the Kotak app. ADR-0004 is unaffected (no ATP position to exit). | `kotak_neo.py` | open |
| KN-3 | F&O order quantity: units or lots? | Units (ATP's convention; the positions API reports units). Confirm on the broker's page before the LIVE gate opens. | `kotak_neo.build_order_payload` | open (verify) |
| KN-4 | When does a Kotak Neo session end? | 06:00 IST default (the conservative one) with a TOTP login each day; the "is it alive" check (get-client-ip) catches an earlier end. | `token_lifecycle.TOKEN_DAILY_EXPIRY_IST` | open (verify) |
| KN-5 | Rate limits. | 3 / s, burst 5, 120 / min (a third of "10 orders / s", which only a search extract of Kotak's FAQ states); quotes in batches of 50 (SDK docs, 25 / s). No per-day bucket (none published). | `rate_budget.py` | open (verify) |
| KN-6 | The protection band for a MARKET order Kotak does not take (TM-3) when the user set none. | The platform's stated default for a protected limit, 0.5% (`router.DEFAULT_MARKET_PROTECTION_PCT`); the deployment's `market_protection_pct` when set. Re-pricing attempts and wait come from the caller (not wired to a LIVE path yet: the gate is closed). | `execution/marketable_limit.py` | open |
| KN-7 | SL-M orders: Kotak's SDK accepts SL-M, but its page says market orders are not allowed for retail algo orders. | Treated as not allowed: a stop goes as SL with its limit a band past the trigger (`stop_market=False`). | `kotak_neo.capabilities` | open (verify) |
| KN-8 | Which Kotak Neo plan is the user on (brokerage)? | No plan setting yet: PAPER uses the stated row only when the caller names the plan (`venue_plan`); otherwise the user's own brokerage setting. A per-broker plan setting comes with the Performance part (TM-26). | `broker_charges.py`, `paper_adapter.py` | open |
| KN-9 | The feed URL: the SDK may learn a data-centre-specific URL from Kotak's config service. | ATP uses the SDK's production default; calling the config service is added when the data gate opens. | `market_data/kotak_feed.py` | open |
| KN-10 | A PAPER deployment on a Kotak Neo account: where do its prices come from? | Not offered until the data gate opens; the organisation's other connection (e.g. Upstox) prices charts, chains and the read-only worker paths (`gates.data_sources`). A deployment bound to a Kotak account is a later step. | `workers/trading_worker.py` | open |
| KN-11 | Scrip master lot / tick columns (`lLotSize`, `dTickSize`) and units. | Read defensively (tick in paise when >= 1, else as is; 0.05 when odd). Confirm on the first real file. | `kotak_neo.instrument_from_row` | open (verify) |

## Groww adapter (GW, 23 Oct)

| # | Question | Provisional default | Where | Status |
|---|---|---|---|---|
| GW-1 | When may the Groww gates open? | Both stay closed in code (no flag): LIVE orders until the lawyer and Groww confirm the one-server-IP / static-IP model in writing (TM-5); market data until the data-terms row is "confirmed" with Groww's letter (TM-6). | `brokers/gates.py` | open (owner: lawyer / broker letter) |
| GW-2 | When does a Groww token end? Docs (extract): "daily at 06:00", no time zone. | 06:00 IST; the user-details check catches an earlier end. | `token_lifecycle.TOKEN_DAILY_EXPIRY_IST` | open (verify) |
| GW-3 | Does Groww still cap calls per day (3,000 orders / 5,000 live data / 3,000 non-trading)? Not found in any extract read 23 Oct; newer extracts show per-second / per-minute limits only. | Kept as the conservative per-day budget (TM-27), ATP using at most 90% of each, 10% of the orders kept for exits; remove or change when Groww's page is read. | `rate_budget.DAILY_CAPS` | open (verify) |
| GW-4 | Does Groww take a plain MARKET / SL-M order from an API client? | Treated as not allowed (TM-3): a marketable limit with the user's band, a stop as SL with a band. | `groww.capabilities`, `in_sebi.json` | open (verify) |
| GW-5 | Feed subscription limit: 1,000 (docs extract) or 3,000 per user (blog)? | 1,000 per connection (the lower). | `market_data/groww_feed.MAX_SUBSCRIPTIONS` | open (verify) |
| GW-6 | History: the order says 3 months, the changelog says "from 2020"; the range per call is not published. | 90 days per daily request; intraday per the platform's existing chunking; nothing read while the data gate is closed. | `groww.daily_days_per_request` | open (verify) |
| GW-7 | The per-day counters are per worker process; the API's chart reads have their own share; an exit's price checks would draw on the live-data cap. | Enough while the data gate is closed (no price read goes to Groww); with the data gate: prices for exits from the stream (TM-27), a Redis-shared daily counter. | `rate_budget.DailyBudget` | open |
| GW-8 | LTP batch size and the order-reference length. | 50 symbols per LTP call; the reference cut to 20 characters. | `groww.LTP_BATCH`, `max_tag_length` | open (verify) |
| GW-9 | The tile offers only the TOTP key; the approval key (secret + a daily approval on Groww) is in the adapter and the field map (Advanced) but not in the two-field connect form. | TOTP only in the tile (one click each morning, no daily approval step); add the approval way to the tile only if a user asks. | `catalog.py`, `field_specs.py` | open |
| GW-10 | Which Groww plan is the user on (brokerage)? | One plan, `standard`, from Groww's help page; PAPER uses it only when the caller names it (`venue_plan`); otherwise the user's own brokerage setting. | `broker_charges.py` | open |
| GW-11 | Holdings carry no price in Groww's answer (reference). | Holdings show quantity and average only; the mark comes from ATP's own data source. | `groww.get_holdings` | open (verify) |
| GW-12 | Every response field name is from extracts of Groww's API reference (the SDK passes answers through). | Read defensively; confirm on the first real answers (the fixture is hand-made). | `groww.py` | open (verify) |

## TT-2 follow-up (24 Oct)

| # | Question | Provisional decision + reason | Applies to | Status |
|---|---|---|---|---|
| TT2-1 | TT-2 names the ticket, the basket and the Terminal. The older plan-before-trade PAPER ticket (`paper/ticket.py`, Z5 / P1-d) is also the user's own order, but under approval-only mode it still raises an entry proposal he then approves himself. | Unchanged for now: only the F&O ticket and basket routes (and later the Terminal) set the origin "manual", as the decision lists. If Abhi wants the plan ticket to count as his approval too, it is a one-line route change plus a test. Checked against CLAUDE.md §3-4, DECISIONS (TT-2) and ATP_SESSION_TRADING_TERMINAL.md. | `paper/ticket.py:288`, `paper/fno_routes.py` | open (provisional default in use) |

## P1-2b: the organisation's min reward:risk OFF (24 Oct, `claude/p1-2b-org-min-rr-off`)

Checked against CLAUDE.md (sections 3, 4, 8), DECISIONS 24 Oct (TT12-4, TT12-1) and ADR-0004.

| # | Question | Provisional default | Where | Status |
|---|---|---|---|---|
| P12B-1 | `risk_settings.min_risk_reward` is NOT NULL and held the built-in 1.2 in every row nobody changed; the row cannot tell "set" from "inherited". | TT12-1 rule, no migration: a stored 1.2 reads as not set (0). An organisation that now sets exactly 1.2 itself is stored one float step above 1.2 (`math.nextafter`), which reads back as 1.2 and applies. Any other stored value is kept. | `risk_engine/routes.py` `org_min_rr_read` | open (provisional default in use) |
| P12B-2 | The options backtest dropped the signal's reward:risk for a single contract, while the live / paper contract order keeps it: with a minimum set, the two disagreed. | The options backtest keeps it for a single contract (as live) and not for a multi-leg structure (as live, `execution/multileg.py`); engine label 9-options. No effect while no minimum is set. | `backtest/options_engine.py` `_size_lots` | open (provisional default in use) |
| P12B-3 | Custom (no-code) strategies had a built-in min R:R 1.2 (`CustomStrategyConfig`, the text parser, the Builder form pre-filled 1.2). | Done in #497 (owner: no hidden filter anywhere): not set by default; a stored 1.2 = not set (TT12-1 rule); any other stored value kept; a 1.2 the user types in the Builder is kept (stored one float step above, applies as 1.2). The interview's 1.5 / 2.0 and the AI drafts' stated minimum stay visible proposals. | `strategy_engine/declarative.py`, `nlu_parser.py`, `StrategyBuilderPage.tsx` | answered 24 Oct (DECISIONS) |
| P12B-4 | The level gate (`price_action/level_gate/settings.py`) default `min_rr` 3.0 is Trade's own value (golden parity with the Trade repo) and is shown as a visible pre-filled setting ("Default: 3") in the Scan Then drawer (flag `scan_then`, off). | Not changed in #497. Proposal: keep 3.0 as the visible default for Abhi's own method (it is his stated rule, golden-pinned); for other users' Scan Then templates show the field empty ("not set" = no R:R filter) once `scan_then` opens to users. | `level_gate/settings.py:15`, `screener/then/ThenPanel.tsx` | open (owner) |
## TT-12: platform-default limits start off (24 Oct, `claude/tt12-limits-off`, #494)

Checked against CLAUDE.md (sections 3, 4, 8), DECISIONS 24 Oct (TT-12, TT12-1, TT12-4) and ATP_SESSION_TRADING_TERMINAL.md (T6).

| # | Question | Provisional default | Where | Status |
|---|---|---|---|---|
| TT12-1 | A settings row saved before TT-12 cannot tell "the user set this" from "the platform default he inherited". | No migration. New saves list the user's own keys under `_set`; an old row counts a value only when it differs from the old default (equal = unset). A one-time notice tells the affected users: "Your risk limits are now not set — set your own in Settings › Risk". | `risk_engine/by_default.py`, `me/notices.py` | answered 24 Oct: accepted + notice (DECISIONS) |
| TT12-1a | Who gets the notice: a user whose old row mixes old-default values with values of his own? | Only users with NO value of their own (every stored limit equal to the old default, at least one of them on), behind `ui_v2` and `risk_by_default`. A mixed user keeps his own values and sees no notice (the text "your risk limits are now not set" would be half wrong). Seen = dismissed or any limit saved; the mark lives in prefs `notices`. | `by_default.reset_by_tt12` | open (provisional default in use) |
| TT12-2 | Should "loosen only from tomorrow" also apply when a limit goes from off to a value, or when he clears one? | Off -> any value is a tightening: at once. Clearing (null) a limit he set is a loosening: from the next IST day; clearing an unset key is at once. | `by_default.plan_change` | open |
| TT12-3 | With no risk per trade of his own, what sizes the equity plan ticket and the F&O ticket? | The organisation's own risk per trade (RiskConfig, after the platform ceilings), exactly as with the flag off. No platform number is added. | `paper/ticket.py` assess | open |
| TT12-4 | The organisation-level min reward:risk 1.2 (RiskConfig). | Decided 24 Oct: OFF too, own PR with backtest parity (roadmap P1-2b). | `risk_engine/risk_manager.py` | answered 24 Oct (DECISIONS) |
| TT12-5 | Where does the user see / change his limits? | Setup step 2 (empty boxes, "Not set") and the "Your paper limits" card on the v2 Risk page. | `setup/StepsV2.tsx`, `trading/RiskV2.tsx` | open |

## TT-8: a basket's legs as one group (24 Oct, `claude/tt8-basket-group`, #501)

Checked against CLAUDE.md (sections 3, 4, 8), DECISIONS 24 Oct (TT-8) and docs/design/TRADING_TERMINAL_REVIEW.md (gap 5).

| # | Question | Provisional default | Where | Status |
|---|---|---|---|---|
| TT8-1 | Where is the group stored? `leg_group_id` makes the monitor judge the legs as one strategy structure (group stop from `group_meta`), which would switch off each manual leg's own stop / target. | No migration, no `leg_group_id`: `group_meta` {"basket_group": basket id} + `leg_role` SHORT / LONG on each filled leg. Older baskets are found from their order keys. Risk counting, journal and monitor are unchanged. | `paper/fno_groups.py` | open (provisional default in use) |
| TT8-2 | LIVE: today's "Exit now" refuses LIVE ("close it in the broker app"). The new exits are built LIVE-ready: a LIVE leg goes out through its own broker session (`broker_for_trade`), as the monitor's and the kill switch's LIVE exits do. | Kept so (owner principle 23 Oct: one code path). Manual baskets are PAPER only today, so no LIVE group exists until TT-1; the Phase 0 lock still applies. | `fno_groups.exit_leg / exit_group` | open |
| TT8-3 | Partial exit of a leg (N of M lots). | Not in this PR: it needs the trade-row split (TT-9 / TM-17 method) for P&L, journal and orders - its own PR. | - | open (TT-9) |
| TT8-4 | "Exit this leg" on a leg of a strategy structure (deployment). | Not offered: the structure keeps "Exit now", which closes it whole (its group stop stays valid); the API answers 409 with that sentence. | `fno_groups._check` | open |

