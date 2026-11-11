# FN1 checklist - definition of done, per feature

Owner order P2-F, FN1 (docs/owner-orders/ATP_ORDER_P2F_FUNCTIONAL.md). The seven points D1-D7 are defined in
docs/WORKING_RULES.md §4. FN7 ticks this list: one proof page per feature, a box is ticked only with its evidence
(spec name, screenshot, log line, issue search). Nothing is ticked today: no feature has been checked on production
with real broker data yet (FN0 summary). Features and rows: docs/design/FN0_FUNCTIONAL_AUDIT.md (same numbers).

- **D1** e2e journey spec passes · **D2** production, own broker data, phone 390 + desktop 1440 · **D3** help behind (?)
  · **D4** every flag ON for users, or removed from view · **D5** log line with request id + error alert · **D6** no open
  sev-1 / sev-2 · **D7** plain error sentences with the reason.
- "Specs today" = the Playwright specs in `frontend/e2e/` that cover the feature now (mocked server); FN7 names the one
  that walks the main journey. "-" = none yet.
- Flag states are the admin list's M80 states (`FLAG_STATUS` in `backend/app/platform/flag_state.py`): R ready, BTS behind the
  scenes, SET needs setup, DEC owner decision, NB not built yet. A flag's code default is not its production value.
  Every flag of `FLAG_STATUS` appears in exactly one row (`backend/tests/test_fn1_checklist.py`).

## The list

| # | Feature | Main journey (owner's production check) | Specs today | Flags (state) | D1 | D2 | D3 | D4 | D5 | D6 | D7 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | App shell, navigation, phone bar, colour | open on phone + desktop; every nav entry opens | shell-v2, colour-v2, palette-v3-v2, sidebar-m57-m60-v2 | `ui_v2` R · `colour_v2` R · `colour_v3` R | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| 2 | Today, market context, My day | Today shows your own broker's index prices, not "–"; My day lists each open position | dashboard-v2, d1-today-v2, today-text-v2 | `context_panel` R · `my_day_card` R | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| 3 | Daily rhythm (brief, pulse, wrap, weekly) | a brief arrives on Telegram before the open | daily-rhythm-v2 | `daily_rhythm` R | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| 4 | Broker connect, daily renew, several accounts | connect Upstox, renew next morning, status turns green | broker, broker-guides | `broker_app_links` DEC · `byob_data_tier` DEC · `multi_account` R | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| 5 | Charts (core, multi-chart, studies, zones, own trades) | NIFTY 5m chart loads from your broker and scrolls both axes | charts-v2, chart-axes-v2, chart-window-inplace-v2 | `chart_workspace` R · `chart_price_action` R · `chart_trades_layer` R · `chart_studies` R · `market_zones` R · `decision_tools` R | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| 6 | Contract charts with OI, futures basis | an option contract chart shows OI | - | `chart_contracts` R · `chart_futures_basis` R | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| 7 | Live prices everywhere | prices tick during market hours; the delayed chip shows on a stale feed | live-prices-v2 | `data_hub` SET · `live_push` SET · `alerts_stale_guard` BTS · `alerts_reliable_outbox` BTS | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| 8 | Watchlists | NIFTY 50 list filled; add a symbol to your own list | watchlist-v2 | `watchlists` R · `watchlist_screen` R | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| 9 | Option chain, analyses, Ask AI | spot on the chain = spot on the chart, same "as of" time | option-chain-v2 | `chain_history` BTS | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| 10 | F&O workspace: ticket, basket, hub, futures, insights, check card | place a paper iron condor; hedge legs fill first; margin shown | chain-ticket-v2, basket-v2, fno-workspace-v2, hub-futures-v2, fno-insights-v2, fno8b-slice2/3/4-v2, tt8-basket-group-v2, check-card-v2 | `options_builder` R · `options_builder_saved` R · `smart_execution` R · `term_margin_pair` R · `trade_warnings` R | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| 11 | Plan an options trade (payoff) | payoff shows max loss for a spread on the current chain | optionsBuilder | (options_builder, row 10) | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| 12 | Terminal, roll, what-if, price plans | Terminal opens (phone + desktop); a paper order from the ticket; Exit all | terminal-v2, copilot-plan-v2 | `terminal` R · `term_roll` R · `term_strike_area` R · `term_whatif` R · `trigger_plans` R · `broker_gtt` NB | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| 13 | Combined exits on positions | set a ₹ stop on a spread; it fires in paper | combined-exit-v2 | - | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| 14 | Sectors & indices | sector heat map shows the last closes | markets-v2 | `markets_view` R | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| 15 | Find setups - core screener | run "RSI(14) < 30" on NIFTY 50 with your broker; results in < 1 min | d1-find-setups-v2, m49-find-setups-v2, screener-versions-v2, scan-universe-v2, screener-phone-guards-v2 | `screener_v2` R · `screener_builder_u2` R · `screenql_amw_functions` R · `m99_primitives` NB · `screener_intrabar` R · `screener_vectorized` BTS · `screener_venues` BTS · `market_store_reads` BTS | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| 16 | Screen library: gallery, legends, breakouts | open a ready screen, run it, see matches | screenerGallery | `screener_gallery` R · `screener_legends` R · `screener_legends_lg2` R · `screener_legends_lg3` DEC · `screener_breakouts` R · `screener_breakouts_bk3` R · `screener_breakouts_bk4` R · `screener_breakouts_fitness` R · `intraday_pack` NB | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| 17 | Screen views, ranking, backtest, history, ETF | "when did this screen hit" for a past month returns dates | - | `screener_board_views` R · `screener_rankings` R · `screener_backtest` R · `screener_history` R · `screener_tx2` R · `screener_etf` R | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| 18 | Screen evidence charts (+ Telegram) | a match shows its chart; the Telegram copy arrives | - | `evidence_charts` R · `evidence_context` R | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| 19 | Screens: fundamentals, ownership, waves | - (kept OFF until its data exists, FN0) | - | `screener_fundamentals` DEC · `screener_ownership` DEC · `screener_waves` DEC | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| 20 | Scan alerts, watch-then-trigger, auto paper | an armed screen alert reaches Telegram once per new match | - | `scan_then` R · `execution_core` BTS · `scan_auto_paper` DEC | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| 21 | Screens from plain words (Copilot) | type "stocks above 200 EMA", review, save | screener-idea-v2 | `screener_copilot` R | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| 22 | Custom scan (older scanner) | keep one scanner, or remove this one from view | - | - | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| 23 | Strategy Lab (Pine subset) | translate a short Pine script; plain error on an unsupported line | d1-find-setups-v2 (Strategy Lab) | `strategy_lab_pine` R | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| 24 | Signals (approve / reject), Telegram buttons | a paper signal arrives; Approve from Telegram | signals-v2, signals-day-one-v2, d1-signals-bot-v2 | `signal_language_v2` R · `approval_only_entries` R · `telegram_inbound` SET | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| 25 | Paper bot, paper account, risk, report card, why no trade | start a paper bot on NIFTY; first entry or a "why no trade" reason | paper-bot-v2, paper-below-zero-v2, risk-v2, risk-limits-v2 | `paper_account` R · `risk_by_default` R · `vix_gate` R · `ladder_evidence` R · `experience_levels` R · `portfolio_risk` R · `portfolio_risk_caps` R · `paper_report_card` R · `why_no_trade` R | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| 26 | My trades, orders, money, cost of trading, expiry check | P&L on each card matches your paper fills | positions-v2, portfolio-v2, orders-v2, mytrades-text-v2 | `cost_meter` R · `expiry_checklist` R | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| 27 | Performance, journal, coach, similar setups | after 5 paper trades: weekly score shows; a journal trade lists similar setups | journal-v2 | `discipline_engine` R · `setup_recall` R | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| 28 | Strategies: build, backtest, optimizer, cost models | build "EMA20 crosses EMA50", backtest 1 year with costs | d1-template-backtest-v2, strategy-flow-v2, strategy-advanced-v2, strategy-library-v2, chart-backtest-v2 | `backtest_optimizer` R · `backtest_models` R · `backtest_stored_history` DEC | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| 29 | Strategy templates + idea library | adopt a template, get a paper signal | strategy-templates-v2, idea-library-v2, d1-adopt-form-v2 | `strategy_templates` R · `idea_library` R | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| 30 | AI Copilot (chat, tools, agent, workspace, studies, watch) | add a key, Test, ask "what is NIFTY's trend" | copilot, copilot-*-v2, connect-ai-v2, key-guides-v2, platform-ai-v2, study-export-v2 | `ai_copilot` R · `ai_agent` SET · `ai_multiview` R · `ai_one_key` R · `ai_research` NB · `ai_platform_key` SET · `market_thesis` DEC · `thesis_stock_targets` DEC · `copilot_agent` SET · `copilot_memory` R · `copilot_languages` SET · `copilot_ticket` DEC · `copilot_watch` R · `copilot_workspace` R · `study_export` R · `shared_daily_report` SET | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| 31 | Analyze a chart (vision), own-key vision | - (P5-1: stays hidden with an honest status) | chart-analyze-v2, chart-analyze-engine-v2 | `chart_analyze` SET · `chart_analyze_strikes` DEC · `chart_analyze_share` DEC · `vision_byok` R · `vision_byok_v3` DEC | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| 32 | News & events | headlines appear, marked unverified | - | `news_feed` R | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| 33 | Company research | open RELIANCE: figures shown with a date | - | - | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| 34 | Factor Lab, Contract specs | keep, or remove from view (Advanced / admin) - FN0 decision | - | - | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| 35 | Settings, alerts, webhooks, notifications, team, account, activity log | rename the organisation; set Telegram; a test alert and a webhook test arrive | settings-index-v2, trading-defaults-v2, trade-settings-v2, setup-v2, text-size-v2, tours-v2, support-desk-v2 | `public_api` R · `help_centre` SET · `mobile_push` SET · `alerts_whatsapp_dlt` SET · `support_bot` NB · `growth_v2` DEC · `referrals` DEC · `plan_limits_v2` DEC · `alert_webhooks` R | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| 36 | Public site, sign-up, legal, learn, status page | - (Phase 5) | front-page, d1-before-login-v2, learn-growth-v2, growth-v2, ops-alerts-v2 | `self_signup` DEC · `beta_cohort_gate` DEC · `legal_pages` DEC · `marketing_site` DEC · `guest_demo` DEC · `onboarding_emails` SET · `learn` DEC · `public_status_page` R · `status_page` R | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| 37 | Marketplace; LIVE trading | - (closed on purpose: marketplace last phase; LIVE is the owner's switch, FN6) | - | `marketplace` DEC · `marketplace_ai_listings` DEC · `live_trading` DEC · `phase0_paper_only` BTS | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| R | Chart reader output (read this chart, advanced analysis, signal engine) | - (P5-1: stays hidden with an honest status) | (read_chart inside copilot-*-v2, m75-m78 captures) | `read_chart` R · `chart_analyze_hybrid` DEC · `trade_engine` SET · `preopen_charts` SET · `chart_reader` SET | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| A | Admin console (system, flags, trader pack, users, support, view as user, diagnostics) | "Enable trader features" once; the flag list shows ON | admin-console-v2, admin-feed-status-v2, view-as-v2, ops-alerts-v2 | `history_import` SET · `history_intraday` BTS · `history_backfill` BTS | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |

## Owner production check list (FN7 uses it; nothing here changes anything)

**1. Read-only server facts** - ONE block, after the close. It reads only; it changes no flag, order or row.

```sh
su - atp -c "nohup sh -c '/opt/atp/deploy/atp.sh health; /opt/atp/deploy/atp.sh probe-candles NIFTY 5m; /opt/atp/deploy/atp.sh probe-chain NIFTY; /opt/atp/deploy/atp.sh journal-status; /opt/atp/deploy/atp.sh lake-backfill --check; /opt/atp/deploy/atp.sh lists-sync --check' > /tmp/atp-fn1.log 2>&1 &"
```

Then `tail -n 80 /tmp/atp-fn1.log`. What each line proves: `health` - containers, deep health, flags, the chart
engine (D4, D5); `probe-candles` / `probe-chain` - your own broker session returns bars and a chain (D2 rows 5, 9);
`journal-status` - row 27; `lake-backfill --check` - stored history (rows 6, 15, 28); `lists-sync --check` - the
system lists (row 8).

**2. In the app, per row** - signed in as yourself with your own broker connected, on the phone and on a desktop:
do the row's "Main journey" above, take one screenshot per width, and note what failed and its error sentence.
For D5 also open Admin › System › Alerts and Diagnostics (the last server errors with their reference) once after
the run. A row with a "-" journey is not checked (closed on purpose, P5-1, or waiting for an owner decision).
