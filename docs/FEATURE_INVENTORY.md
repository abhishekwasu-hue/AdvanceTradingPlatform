# ATP feature inventory (M63 I-2)

Every backend module, every FastAPI router prefix, the endpoints moved out of `backend/app/main.py`, and every page /
address of the web app, with its flag and its status **from the code defaults**. The column "On production" says what
the live site shows. It stays "unknown until the owner's check" (M63 I-1: the owner's read-only check of the server)
except where the code alone proves STUB or HIDDEN. No production values, keys, organisation data or server details live
in this file.

**Keep it current:** every PR that adds or changes a feature updates its row here. CI enforces the coverage:
`backend/tests/test_feature_inventory_coverage.py` (every router prefix; `main.py` declares no endpoint of its own; every
endpoint moved out of it is listed under the router file that serves it) and
`frontend/src/featureInventory.test.ts` (every page address in `routes.ts` and every v2 shell address). Each one must
appear in this file in backticks.

## Legend

| Column | Values |
|---|---|
| Code status | WORKS = real data path + tests + reachable; PARTIAL = works with gaps; STUB = "coming soon" / 409 / 503 / NotImplemented / placeholder text; EMPTY-BY-DEFAULT = needs a flag or data that is off by default; DUPLICATE = same job as another page; INTERNAL = library / infrastructure, no route |
| On production | WORKS / EMPTY / STUB / HIDDEN, or "unknown until the owner's check" |
| Flag (default) | the flag(s) in `backend/app/platform/controls.py` FEATURE_FLAGS; (ON) = in DEFAULT_ON_FLAGS, every other flag is OFF by default |

Facts from the code that the tables rely on:
- Ten flags are ON by default (DEFAULT_ON_FLAGS): `ai_copilot`, `backtest_optimizer`, `colour_v2`, `colour_v3`,
  `marketplace`, `options_builder`, `phase0_paper_only`, `public_api`, `self_signup`, `terminal`. `live_trading` is OFF.
  `ui_v2` is OFF by default, so every v2-only page needs it.
- The admin trader pack (`backend/app/platform/trader_pack.py`, POST `/api/admin/controls/trader-pack`) turns the
  pack flags ON for everyone in one audited step; it never touches `marketplace`, `public_api`, `self_signup`,
  `scan_auto_paper`, `live_trading`, `phase0_paper_only`. Whether it was applied on production is part of the owner's check.
- `vision_byok_v3` is removed from view (flag_catalog REMOVED_FROM_VIEW): it cannot be turned on.
- Analyze a chart needs the private engine package; without it the three `chart_analyze*` flags answer OFF for users.
- The signal engine (M66, `trade_engine`, OFF) needs the private engine package too; without it the flag answers OFF.
- Vendor settings are empty by default: Razorpay, VAPID (browser push), FCM (phone push), platform SMTP, the ATP
  Telegram bot token, CAPTCHA, the history import object store; with no AI key the AI answers from built-in rules.
- No sample market data: market data comes only from the user's own logged-in broker session.
- The trading worker must run for exits, alerts, news feed, universe sync, zones, rhythm, OI snapshots.

## 1. Backend modules (A-M)

| Module | What it does | Router prefixes | Flag (default) | Code status | On production |
|---|---|---|---|---|---|
| accounts | Multi-broker account registry, fan-out plan | `/api/accounts` | `ui_v2` + `multi_account` | PARTIAL | unknown until the owner's check |
| admin | Platform admin console, bootstrap, view-as | `/api/admin`, `/api/admin/console`, `/api/admin/view-sessions` | `growth_v2` (console cards only) | WORKS | unknown until the owner's check |
| ai | Copilot chat, strategist, own AI keys, research, vision | `/api/ai`, `/api/ai/byok` | `ai_copilot` (ON); `ai_one_key`, `ai_agent`, `ai_research` | PARTIAL (no key: rule-based answers) | unknown until the owner's check |
| alerts | Alert rules, channels, webhooks, push, outbox | `/api/alerts`, `/api/alert-channels`, `/api/alert-webhooks`, `/api/admin/alerts` | `alert_webhooks`, `alerts_reliable_outbox`, `mobile_push`, `screener_intrabar` | PARTIAL (browser push needs VAPID) | unknown until the owner's check |
| audit | Hash-chained audit log | - | - | INTERNAL | - |
| auth | Sign-in, sessions, two-factor, Google sign-in, lockout | `/api/auth` | `self_signup` (ON); `beta_cohort_gate` | WORKS | unknown until the owner's check |
| backtest | Backtests, optimiser, Monte Carlo, walk-forward, option snapshots | routes `/api/backtest/...`, `/api/backtests` (no router prefix) | `backtest_optimizer` (ON); `backtest_models`, `backtest_stored_history` | PARTIAL (intraday needs a broker session or stored history) | unknown until the owner's check |
| beta | Invite codes, cohort stage, feedback, app vitals | `/api/admin/beta`, `/api/feedback`, `/api/pwa` | `beta_cohort_gate` | WORKS | unknown until the owner's check |
| billing | Plans, Razorpay subscribe / cancel, usage, webhook | `/api/billing`, `/api/billing/webhooks`, `/api/admin/billing` | - | PARTIAL (no payments without Razorpay keys) | unknown until the owner's check |
| brokers | Indian broker adapters, connect, token lifecycle | `/api/broker` | `byob_data_tier` | WORKS | unknown until the owner's check |
| cache | Redis client wrapper | - | - | INTERNAL | - |
| chart_analyze | Chart image / link analysis, strikes, share | `/api/chart-analyze`, `/api/admin/chart-analyze` | `chart_analyze`, `chart_analyze_strikes`, `chart_analyze_share` | STUB (hybrid mode answers 409 "coming soon"; 503 without the private engine) | STUB (hybrid mode); image mode: unknown until the owner's check |
| charts | Drawings, layouts, price-action layer, studies, trade / contract / basis layers | `/api/charts`, `/api/charts/contracts`, `/api/charts/drawings`, `/api/charts/layers`, `/api/charts/layouts` | `chart_workspace`, `chart_price_action`, `chart_studies`, `chart_trades_layer`, `chart_contracts`, `chart_futures_basis`, `market_zones`, `read_chart` | PARTIAL (every layer off by default) | unknown until the owner's check |
| compliance | Static IPs, algo id, go-live evidence | `/api/compliance` | - | WORKS | unknown until the owner's check |
| context_panel | Market context panel | `/api/context` | `context_panel` | EMPTY-BY-DEFAULT (404 while off) | unknown until the owner's check |
| contract_notes | Broker tradebook CSV upload and match | `/api/contract-notes` | - | WORKS (CSV only) | unknown until the owner's check |
| core | Config, egress, logging, rate limit, resampling | - | - | INTERNAL | - |
| custom_strategies | Strategy text / rule parse, drafts, versions, rollback | `/api/custom-strategies` | `strategy_templates` (templates only) | WORKS | unknown until the owner's check |
| db | Models, session | - | - | INTERNAL | - |
| decision_tools | Ruin calculator, what-if, exposure, expected move | `/api/decision` | `decision_tools` | EMPTY-BY-DEFAULT (404 while off) | unknown until the owner's check |
| deployments | PAPER / LIVE deployments, why-no-trade | `/api/deployments` | `live_trading`; `phase0_paper_only` (ON) | WORKS (PAPER; LIVE locked) | unknown until the owner's check |
| engine_provider | M66 EN-1: the generic boundary to the private package's signal engine (typed interface, loader, state on the admin System view `/api/admin/console/system` and in `atp.sh health`); EN-2: the data adapter (`frames.py`, `sources.py`: closed bars in India time from the stored history and the user's broker, the near-month future's live volume beside an index, closing-auction marks, the market calendar, and every gap listed - never filled - with the admin's gap probe); no route of its own, no method content | - | `trade_engine` (OFF; admin / owner only; unavailable without the private engine package) | INTERNAL (boundary only; no signal path reads it yet) | unknown until the owner's check |
| execution | Order state machine, paper broker, smart / multi-leg, incomplete structures | `/api/trading/incomplete` | `execution_core`, `smart_execution`, `approval_only_entries` (+ `ui_v2`) | PARTIAL; paper practice chain raises NotImplemented | STUB (paper practice chain); rest: unknown until the owner's check |
| expiry_checklist | Expiry-day ITM / OTM, settlement view | `/api/fno` | `ui_v2` + `expiry_checklist` | EMPTY-BY-DEFAULT | unknown until the owner's check |
| exports | CSV / JSON exports of audit, orders, trades | routes `/api/exports/{dataset}`, `/api/admin/exports/{dataset}` (no router prefix) | - | WORKS | unknown until the owner's check |
| fundamentals | Company profiles, statements, scores | `/api/fundamentals` | `screener_fundamentals` (screener side) | WORKS (equities) | unknown until the owner's check |
| fx | Operator FX rates and convert | `/api/fx`, `/api/admin/fx-rates` | - | PARTIAL (no UI) | unknown until the owner's check |
| growth | Plan usage, referrals, onboarding mails, sharing | `/api/growth`, `/api/admin/growth` | `growth_v2`, `referrals`, `onboarding_emails` | PARTIAL (needs SMTP) | unknown until the owner's check |
| idea_library | Curated read-only idea list | `/api/ideas` | `ui_v2` + `idea_library` | EMPTY-BY-DEFAULT | unknown until the owner's check |
| incidents | Admin incident list (status page) | `/api/admin/incidents` | `public_status_page` (status page) | WORKS | unknown until the owner's check |
| indicators | Indicator math | - | - | INTERNAL | - |
| instruments | Instrument master sync, expiries, strikes | `/api/instrument-master` | - | WORKS (needs the daily sync) | unknown until the owner's check |
| journal | Trade journal, tags, weekly review, discipline score | `/api/journal` | `discipline_engine` | EMPTY-BY-DEFAULT | unknown until the owner's check |
| kill_switch | Global / organisation / strategy kill, emergency exit | `/api/kill-switch` | - | WORKS | unknown until the owner's check |
| lab | Pine script translate / run as screen | `/api/lab/pine` | `strategy_lab_pine` | EMPTY-BY-DEFAULT | unknown until the owner's check |
| learn | Lessons and video links, site pages | routes `/api/learn`, `/api/site/pages`, `/api/admin/learn/settings` (no router prefix) | `ui_v2` + `learn`; `marketing_site` | EMPTY-BY-DEFAULT | unknown until the owner's check |
| legal | Terms / privacy documents and acceptance | `/api/legal` | `legal_pages` | STUB (terms and privacy still hold open placeholders) | STUB (lawyer text needed) |
| live | Live stream, tick push, data hub | `/api/live` | `ui_v2` + `data_hub` + `live_push` | EMPTY-BY-DEFAULT | unknown until the owner's check |
| market_data | Broker candles, LTP, holidays, sources | `/api/market-data`, `/api/market`, `/api/market-holidays`, `/api/admin/market-data` | `byob_data_tier`, `data_hub` | WORKS (needs the user's broker login) | unknown until the owner's check |
| market_lake | Daily-file / object-store backfill, intraday history, chain history | `/api/market-data`, `/api/chain-history`, `/api/admin/chain-history`, `/api/admin/history-backfill`, `/api/admin/history-import`, `/api/admin/history-intraday` | `history_import`, `history_backfill`, `history_intraday`, `chain_history` | PARTIAL (no stored history until flags + import) | unknown until the owner's check |
| marketplace | Strategy listings, subscriptions, payouts | `/api/marketplace`, `/api/admin/marketplace` | `marketplace` (ON); `marketplace_ai_listings` | PARTIAL (plan-gated; the last phase) | unknown until the owner's check |
| markets | Sector / index overview | `/api/markets` | `ui_v2` + `markets_view` | EMPTY-BY-DEFAULT | unknown until the owner's check |
| me | Profile, preferences, notices, setup health, account delete | `/api/me` | `ui_v2` (preferences) | WORKS | unknown until the owner's check |
| mobile | Phone device registry, test push | `/api/mobile` | `mobile_push` | PARTIAL (needs FCM) | unknown until the owner's check |

## 2. Backend modules (N-Z)

| Module | What it does | Router prefixes | Flag (default) | Code status | On production |
|---|---|---|---|---|---|
| news_events | Sourced news / event rows | `/api/news-events` | - | WORKS (manual rows unless `news_feed`) | unknown until the owner's check |
| news_feed | Public feed ingest into news events | `/api/news-feed` | `news_feed` | PARTIAL (worker must run) | unknown until the owner's check |
| notifications | In-app notifications | `/api/notifications` | `byob_data_tier` (badge) | WORKS | unknown until the owner's check |
| observability | Health probes, metrics, ops alerts, status page | `/api/admin/ops-alerts`; routes `/api/system/health/...`, `/api/status` (no router prefix) | `public_status_page`, `status_page` | INTERNAL | unknown until the owner's check |
| option_chain | Broker option chain, OI banners / alerts, analyses, charts, Ask AI | `/api/option-chain`, `/api/admin/option-chain` | `ui_v2` (analyses, charts, ask, hub); `ai_copilot` (ON) | PARTIAL (needs the user's broker session) | unknown until the owner's check |
| options_builder | Payoff, T+0, PoP, strike suggestions, saved strategies | `/api/options-builder`, `/api/options-builder/strategies` | `options_builder` (ON); `options_builder_saved` | WORKS (save / load off) | unknown until the owner's check |
| paper | PAPER account, ticket, F&O orders / baskets / exits, report card | `/api/paper`, `/api/paper/fno`, `/api/paper/fno/journal` | `paper_account`, `risk_by_default`, `ui_v2`, `experience_levels`, `paper_report_card` | PARTIAL (F&O desk 404 until flags on) | unknown until the owner's check |
| plans | Plan registry and limits | - | `plan_limits_v2` | INTERNAL | - |
| platform | Feature flags, controls, trader pack, readiness, system status | `/api/admin/controls`, `/api/readiness`, `/api/system`, `/api/team/members` | `phase0_paper_only` (ON), `live_trading` | WORKS | unknown until the owner's check |
| performance | The Performance page's summary and results against backtest, over the user's OWN trades (REV-1 M2) | `/api/performance` | - | WORKS (read-only) | unknown until the owner's check |
| portfolio | PAPER book risk view | `/api/portfolio` | `portfolio_risk`, `portfolio_risk_caps` | EMPTY-BY-DEFAULT (503 while off) | unknown until the owner's check |
| price_action | Structure library (swings, legs, zones) | routes `/api/price-action/...` (section 3) | `market_zones` (zones job) | INTERNAL; gap context raises NotImplemented (not ported) | STUB (gap context) |
| public_api | API keys and `/api/public/v1` | `/api/api-keys`, `/api/public/v1` | `public_api` (ON) | PARTIAL (no docs UI) | unknown until the owner's check |
| quality | Look-ahead test helpers | - | - | INTERNAL | - |
| quant | Factor table, risk, exposure | `/api/quant` | - | WORKS (equities) | unknown until the owner's check |
| reconciliation | Broker vs ATP positions mismatch | `/api/reconciliation` | - | PARTIAL (needs LIVE positions) | unknown until the owner's check |
| retention | Data retention / erasure | - | - | INTERNAL | - |
| rhythm | Pre-market brief, pulse, closing wrap, weekly review | `/api/rhythm` | `ui_v2` + `daily_rhythm` | EMPTY-BY-DEFAULT (worker must run) | unknown until the owner's check |
| risk_engine | Risk config, ceilings, loss streak, guardian events | `/api/risk`, `/api/risk-guardian`, `/api/risk-settings` | `vix_gate`, `risk_by_default` | WORKS | unknown until the owner's check |
| scanner | AI scan plan / read; custom scan run (code in `backend/app/screener/scanner`, `backend/app/scanner` is an import shim) | `/api/scanner/ai`, routes `/api/scanner/run` (section 3) | `ai_copilot` (ON), `thesis_stock_targets` | PARTIAL (needs an AI key) | unknown until the owner's check |
| screener | Screens, runs, alert rules, breakouts, evidence, history, rankings, ETF, library | `/api/screener`, `/api/screener/board`, `/api/screener/breakouts`, `/api/screener/etf`, `/api/screener/evidence`, `/api/screener/history`, `/api/screener/library`, `/api/screener/rankings`, `/api/screener/universes`, `/api/alerts` | `screener_v2` + sub-flags | EMPTY-BY-DEFAULT (503 until `screener_v2`) | unknown until the owner's check |
| secrets_store | Envelope encryption of broker / AI keys | - | - | INTERNAL | - |
| setup_recall | Past trades with the same stored setup | `/api/setup-recall` | `ui_v2` + `setup_recall` | EMPTY-BY-DEFAULT | unknown until the owner's check |
| signal_scoring | Signal quality score | - | - | INTERNAL | - |
| signals | Signal language guard | - | `signal_language_v2` | INTERNAL | - |
| signals_feed | Signals inbox, day-one setup card | `/api/signals`, `/api/signals/day-one` | `ui_v2`; `strategy_templates` (card) | EMPTY-BY-DEFAULT (404 until `ui_v2`) | unknown until the owner's check |
| strategy_engine | Built-in strategies, registry, chart run | `/api/strategies` | - | WORKS | unknown until the owner's check |
| strategy_templates | Signal templates adopted as a PAPER bot | `/api/strategy-templates` | `strategy_templates`, `screener_waves` | EMPTY-BY-DEFAULT | unknown until the owner's check |
| support | Support bot, Help centre, Report a problem, desk | `/api/support`, `/api/ai/support`, `/api/admin/support` | `support_bot`, `help_centre` (+ `ui_v2`) | STUB (catalog: help pages not complete) | unknown until the owner's check |
| support_resistance | S/R zone library | routes `/api/support-resistance/zones` (section 3) | - | WORKS | unknown until the owner's check |
| tax | FY P&L / turnover report | `/api/tax` | - | WORKS | unknown until the owner's check |
| team | Organisation, members, scopes, invites | `/api/team` | - | WORKS | unknown until the owner's check |
| telegram_inbound | Telegram commands and approve / reject buttons | `/api/telegram` | `telegram_inbound` | PARTIAL (needs the bot token) | unknown until the owner's check |
| terminal | Terminal status bar | `/api/terminal` | `terminal` (ON) + `ui_v2` | PARTIAL (404 while `ui_v2` off) | unknown until the owner's check |
| today | "My day" card | `/api/today` | `ui_v2` + `my_day_card`; `market_zones` | EMPTY-BY-DEFAULT | unknown until the owner's check |
| trading | Trades, positions, greeks, exit now, analytics, orders, cost meter | `/api`, `/api/trades` | `cost_meter` (+ `ui_v2`); `approval_only_entries` | WORKS | unknown until the owner's check |
| universe | Exchange list sync, classification | - | - (env switch, worker job) | INTERNAL | unknown until the owner's check (last run) |
| watchlists | Own and system watchlists | `/api/watchlists`, `/api/watchlists/system`, `/api/admin/system-watchlists` | `watchlists`, `plan_limits_v2` | PARTIAL (own lists 404 until flag) | unknown until the owner's check |
| webhooks | TradingView webhook intake | `/api/webhooks` | `approval_only_entries` | WORKS (PAPER) | unknown until the owner's check |
| workers | Trading worker loop, research worker, EOD summary | `/api/system` | `phase0_paper_only`, `byob_data_tier` | INTERNAL (separate process) | unknown until the owner's check |

## 3. Endpoints moved out of `backend/app/main.py` (M55 Q1)

`backend/app/main.py` only includes routers; it declares no endpoint of its own. The 18 endpoints that used to live
there are served, unchanged (same paths, methods, bodies, dependencies and gates), by these module routers. Each
router has no prefix: it declares the full path.

| Group | Endpoints | Router file | Code status | On production |
|---|---|---|---|---|
| strategies | `/api/strategies`, `/api/strategies/{strategy_id}`, `/api/strategies/{strategy_id}/signal`, `/api/strategies/{strategy_id}/signal/enrich`, `/api/strategies/{strategy_id}/paper-execute` | `backend/app/strategy_engine/console_routes.py` | WORKS (candles from the browser) | unknown until the owner's check |
| backtest | `/api/backtest` | `backend/app/backtest/console_routes.py` | WORKS (409 without broker or stored history) | unknown until the owner's check |
| price action | `/api/price-action/structure`, `/api/price-action/patterns`, `/api/price-action/reversal`, `/api/price-action/reversal-markers` | `backend/app/price_action/routes.py` | WORKS (pure functions) | unknown until the owner's check |
| support / resistance | `/api/support-resistance/zones` | `backend/app/support_resistance/routes.py` | WORKS | unknown until the owner's check |
| option chain | `/api/option-chain/analyze`, `/api/option-chain/greeks` | `backend/app/option_chain/analyze_routes.py` | WORKS (pure functions) | unknown until the owner's check |
| scanner | `/api/scanner/run` | `backend/app/screener/scanner/run_routes.py` | WORKS | unknown until the owner's check |
| broker list | `/api/broker/available` | `backend/app/brokers/available_routes.py` | WORKS | unknown until the owner's check |
| contract specs | `/api/instruments`, `/api/instruments/{symbol}` | `backend/app/instruments/reference_routes.py` | WORKS | unknown until the owner's check |
| health | `/api/system/health` | `backend/app/platform/health_routes.py` | WORKS | unknown until the owner's check |

## 4. Frontend pages and tabs (v2 shell, `ui_v2` on)

With `ui_v2` off none of the v2-only pages exist (they fall back to the old shell). A page whose flag is off leaves the
navigation and shows "Coming soon" at its address (`frontend/src/shell/pageFlags.ts`).

| Page / tab | Address | Where in nav | Flag (default) | Code status | On production |
|---|---|---|---|---|---|
| Today | `/` | Markets (bottom bar) | - (My day: `my_day_card`; brief: `daily_rhythm`) | WORKS | unknown until the owner's check |
| Charts | `/charts`, `/charts/:symbol` | Markets (bottom bar) | - (multi-chart: `chart_workspace`) | WORKS | unknown until the owner's check |
| Watchlist | `/watchlist` | Markets | `watchlists` | EMPTY-BY-DEFAULT + DUPLICATE (Charts panel) | unknown until the owner's check |
| Option chain (Chain, Charts, OI, Analyses) | `/option-chain` | Markets | - (broker gate) | WORKS; OI view PARTIAL (needs recorded snapshots) | unknown until the owner's check |
| Sectors and indices | `/markets` | Markets | `markets_view` | EMPTY-BY-DEFAULT | unknown until the owner's check |
| Find setups | `/screener` | Markets | `screener_v2` | EMPTY-BY-DEFAULT | unknown until the owner's check |
| Custom scan | `/screener/custom` (old `/scanner`) | search only | - (merged view: `screener_v2`) | PARTIAL; M49 U6: opens Find setups with its Conditions block open, the custom scanner inside Conditions ("More filters"); with `screener_v2` off the old page alone | unknown until the owner's check |
| Plan an options trade | `/option-chain/plan` (old `/options-builder`) | extras | `options_builder` (ON); save: `options_builder_saved` | PARTIAL ("Adjustments and compare" marked coming later) | STUB (adjustments / compare) |
| Analyze a chart | `/chart-analyze` | extras | `chart_analyze` + private engine | EMPTY-BY-DEFAULT; Hybrid "Coming soon" | STUB (hybrid); rest: unknown until the owner's check |
| Terminal | `/terminal` | Trading | `terminal` (ON) | PARTIAL | Bots and Payoff panels HIDDEN (never in the Panels menu); rest: unknown until the owner's check |
| Signals | `/signals` | Trading (bottom bar) | - (day-one card: `strategy_templates`) | WORKS | unknown until the owner's check |
| Trading bot | `/deployments` | Trading | - | WORKS | unknown until the owner's check |
| My trades - Positions | `/positions` | Trading (bottom bar) | - | WORKS | unknown until the owner's check |
| My trades - Orders | `/positions/orders` (old `/orders`) | tab | - | WORKS | unknown until the owner's check |
| My trades - Money | `/positions/money` (old `/portfolio`) | tab | - | WORKS + DUPLICATE (tiles = Positions tiles) | unknown until the owner's check |
| Performance - Results | `/analytics` | redirect → `/positions/results` (M60) | - | WORKS (old page) | unknown until the owner's check |
| Performance - Journal | `/analytics/journal` (old `/journal`) | redirect → `/positions/results/journal` (M60) | `discipline_engine` | EMPTY-BY-DEFAULT | unknown until the owner's check |
| Performance - Coach | `/analytics/coach` (old `/coach`, retired `/journal/coach`) | redirect → `/copilot/coach` while `ai_copilot` is on (M60); else a part of My trades › Results | - (AI acknowledgement) | WORKS (old page) | unknown until the owner's check |
| Strategies - Library | `/strategies` | Insights | - | WORKS (old page) | unknown until the owner's check |
| Strategies - Ideas | `/ideas` | redirect → `/learn/ideas` (M60) | `idea_library` | EMPTY-BY-DEFAULT | unknown until the owner's check |
| Strategies - Build | `/strategies/build` (old `/strategy-builder`) | tab | - (template cards: `strategy_templates`) | PARTIAL (one area-pullback card and the 1W timeframe marked "soon") | STUB (the "soon" card and 1W) |
| Strategies - Test | `/strategies/test` (old `/backtest`) | tab | - (broker gate) | WORKS (old page) | unknown until the owner's check |
| AI Copilot - Ask | `/copilot` | Insights | `ai_copilot` (ON) | WORKS | unknown until the owner's check |
| AI Copilot - Brief / Review / Signals | `/copilot/brief`, `/copilot/review`, `/copilot/signals` | redirect → `/copilot/read`, `/copilot/coach`, `/signals` (M60) | `ai_copilot` (ON) | DUPLICATE (Today brief + News / Coach / Signals) | unknown until the owner's check |
| Old Copilot (7 tabs) | `/copilot/market-pulse`, `/copilot/strategy-lab`, `/copilot/idea-builder`, `/copilot/ask`, `/copilot/watchtower`, `/copilot/news-radar`, `/copilot/coach` | Advanced only | `ai_copilot` (ON) | DUPLICATE (kept until the owner's ✅) | unknown until the owner's check |
| News and events | `/news-events` | redirect → `/learn/news` (M60) | - (feeds: `news_feed`) | WORKS (old page) | unknown until the owner's check |
| Learn | `/learn` | Insights | `learn` | EMPTY-BY-DEFAULT | unknown until the owner's check |
| Company research | `/fundamentals` | extras | - | PARTIAL (manual entry, equities) | unknown until the owner's check |
| Broker | `/broker` | redirect → `/settings/broker` (M60) | - | WORKS | unknown until the owner's check |
| Settings (index) | `/settings` | Account | - | WORKS | unknown until the owner's check |
| Settings - AI | `/settings/ai` | Settings | - (`ai_one_key`) | WORKS | unknown until the owner's check |
| Settings - Alerts | `/settings/alerts` | Settings | - (`alert_webhooks`, `mobile_push`, `daily_rhythm`) | WORKS | unknown until the owner's check |
| Settings - Appearance | `/settings/appearance` | Settings | - | WORKS | unknown until the owner's check |
| Settings - Help and tips | `/settings/help` | Settings | - (centre: `help_centre`) | WORKS | unknown until the owner's check |
| Settings - Advanced | `/settings/advanced` | Settings | - (`public_api` (ON)) | WORKS | unknown until the owner's check |
| Account and plan | `/settings/account` (old `/account`) | Settings | - | WORKS (old page) | unknown until the owner's check |
| Setup wizard | `/settings/setup` | Settings | - | WORKS + DUPLICATE (Today "Get started") | unknown until the owner's check |
| My trading defaults | `/settings/trading-defaults` | Settings | - | WORKS | unknown until the owner's check |
| Risk limits (M64: every check a switch, off by default) | `/settings/risk-limits` | Settings | - | WORKS | unknown until the owner's check |
| Trade settings (M68 PR-0: the seven sections, one registry, every field Not set; API `/api/me/trade-settings`; fields not wired yet show only to a platform admin) | `/settings/trade-settings` | Settings | `ui_v2` | SHELL (no field wired yet) | unknown until the owner's check |
| Risk status (old Risk limits page: today, guardian, events; limits link to `/settings/risk-limits`) | `/risk-management` | Settings | - | WORKS | unknown until the owner's check |
| Team | `/team` | extras | - | WORKS (old page) | unknown until the owner's check |
| Notifications | `/notifications` | extras | - | WORKS (old page) | unknown until the owner's check |
| Factor Lab | `/quant` | More | - | WORKS (equities) | unknown until the owner's check |
| Contract specs | `/instruments` | More | - | WORKS (static reference) | unknown until the owner's check |
| Marketplace | `/marketplace` | More | `marketplace` (ON; never in the trader pack) | EMPTY-BY-DEFAULT (no listings) | unknown until the owner's check |
| Activity log | `/system-logs` | More | - | WORKS (old page) | unknown until the owner's check |
| Admin console | `/admin` | Admin (SUPER_ADMIN only) | role | WORKS | unknown until the owner's check |
| Status (public) | `/status` | outside the shell | - | WORKS | unknown until the owner's check |

### M60 / M57 addresses (PR #784: sidebar in five sections, tab rows of four, show / hide rail)

The sidebar is Trade · Find · Build · Learn & AI · Account; the phone bottom bar is the first four entries + More. The
sidebar's show / hide choice is stored per user in the `shell` namespace of `/api/me/prefs` (router prefix `/api/me`,
already listed). Every old address above that moved forwards to the row below (query and `#part` kept).

| Page / tab | Address | Where in nav | Flag (default) | Code status | On production |
|---|---|---|---|---|---|
| My trades - Results (Performance) | `/positions/results` | My trades tab | - | WORKS (old page) | unknown until the owner's check |
| My trades - Results - Journal | `/positions/results/journal` | Results part | `discipline_engine` | EMPTY-BY-DEFAULT | unknown until the owner's check |
| AI Copilot - Read the chart | `/copilot/read` | Copilot tab | `ai_copilot` (ON) | WORKS (Market Pulse) | unknown until the owner's check |
| AI Copilot - Analyze a chart | `/copilot/analyze` | Copilot tab | `ai_copilot` (ON) + `chart_analyze` + private engine | EMPTY-BY-DEFAULT (same tool as `/chart-analyze`) | unknown until the owner's check |
| AI Copilot - Coach | `/copilot/coach` | Copilot tab | `ai_copilot` (ON) | WORKS (Review + Guide) | unknown until the owner's check |
| Learn - Words explained | `/learn/words` | Learn tab | `learn` | EMPTY-BY-DEFAULT | unknown until the owner's check |
| Learn - Ideas | `/learn/ideas` | Learn tab | `idea_library` | EMPTY-BY-DEFAULT | unknown until the owner's check |
| Learn - News and events | `/learn/news` | Learn tab (sidebar stand-in while `learn` is off) | - (feeds: `news_feed`; news part: `ai_copilot`) | WORKS (old page) | unknown until the owner's check |
| Settings - Broker accounts | `/settings/broker` | Settings › Account & broker | - | WORKS | unknown until the owner's check |
| Charts, Option chain | `/charts`, `/option-chain` | search only while `terminal` is on (sidebar stand-ins while it is off); no redirect until the Terminal has a Chart-only layout | - | WORKS | unknown until the owner's check |
| Watchlist | `/watchlist` | search only; no redirect until the Terminal has a Watchlist panel | `watchlists` | EMPTY-BY-DEFAULT | unknown until the owner's check |

## 5. Old-shell page addresses (`frontend/src/routes.ts`)

Every page id in `routes.ts` is served at `/<id>` (the dashboard at `/`). With `ui_v2` on, the merged ones forward to
their v2 address (section 4).

| Page id | Address | v2 address |
|---|---|---|
| dashboard | `/` | `/` |
| broker | `/broker` | same |
| strategies | `/strategies` | same |
| strategy-builder | `/strategy-builder` | `/strategies/build` |
| deployments | `/deployments` | same |
| fundamentals | `/fundamentals` | same |
| signals | `/signals` | same |
| scanner | `/scanner` | `/screener/custom` |
| screener | `/screener` | same |
| news-events | `/news-events` | same |
| quant | `/quant` | same |
| backtest | `/backtest` | `/strategies/test` |
| marketplace | `/marketplace` | same |
| copilot | `/copilot` | same (tabs `/copilot/<tab>`) |
| coach | `/coach` | `/analytics/coach` |
| option-chain | `/option-chain` | same |
| options-builder | `/options-builder` | `/option-chain/plan` |
| instruments | `/instruments` | same |
| positions | `/positions` | same |
| portfolio | `/portfolio` | `/positions/money` |
| orders | `/orders` | `/positions/orders` |
| analytics | `/analytics` | same |
| journal | `/journal` | `/analytics/journal` |
| risk-management | `/risk-management` | same |
| settings | `/settings` | same (sections `/settings/<section>`) |
| team | `/team` | same |
| admin | `/admin` | same |
| system-logs | `/system-logs` | same |
| notifications | `/notifications` | same |
| account | `/account` | `/settings/account` |
| chart-analyze | `/chart-analyze` | same |

## 6. Stubs, hidden items and owner inputs (from the code)

| Item | Where | Proven by the code | Needs |
|---|---|---|---|
| Analyze a chart: hybrid mode | chart_analyze | STUB (409 "coming soon") | build (M59) |
| Analyze a chart: engine | chart_analyze | 503 when the private package is absent | the private package on the server |
| Signal engine (M66) | engine_provider | boundary and data adapter only (EN-1, EN-2); unavailable when the private package is absent | the private package on the server; EN-2..EN-7 |
| Legal pages | legal | STUB (open placeholders in terms and privacy) | lawyer text |
| Help centre / support bot | support | STUB per the flag catalog (help pages not complete) | write the pages or hide |
| Terminal Bots and Payoff panels | terminal | HIDDEN (never in the Panels menu) | build (T2 / T3) |
| Build flow "soon" card and 1W | strategies build | STUB (shown, not selectable) | build or hide |
| Options builder adjustments / compare | option chain plan | STUB ("coming later") | build or hide |
| Gap context | price_action | STUB (NotImplemented, not ported) | implement or remove |
| Paper practice chain | execution | STUB (NotImplemented) | remove the path |
| Vision reviews (`vision_byok_v3`) | ai | HIDDEN (removed from view) | - |
| Vendor settings | config | empty by default | Razorpay, VAPID, FCM, SMTP, Telegram bot token, history object store, AI key |
