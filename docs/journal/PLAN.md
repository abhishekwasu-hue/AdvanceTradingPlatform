# Journal module - plan (owner spec v1 + v1.1, mapped onto ATP)

Spec: `docs/specs/ATP_JOURNAL_MODULE_SPEC.md` (v1 and the v1.1 addendum, verbatim). Open questions:
`docs/journal/OPEN_QUESTIONS.md` (`JM-n`). Progress: `docs/journal/PROGRESS.md`.

This document maps the spec onto the code that exists in ATP today, so that nothing is built twice. It is a plan only:
no application code ships with it.

## 0. Owner decisions (2026-10-11) - these override the spec where they differ

| # | Decision | Effect on the spec |
|---|----------|--------------------|
| OD-1 | **Plan now, build after Phase 0.** Phase 0 is Z1-Z11 (`docs/specs/ATP_PHASE0_FREEZE.md`), and its order does not change. The journal milestones go into the N-list, next to N2 (P1-f/g/h), after Z11 merges. | Supersedes the spec's "Start now with Milestone 1" and "continue from the current milestone without waiting". Milestone 0 (this plan) is the only journal work before Z11. A Phase 0 bug, beta feedback or a security issue pre-empts journal work, as for every N item. |
| OD-2 | **English only for now.** Build the i18n plumbing with English source strings only. Hindi and Marathi translation files come later, and only with the owner's approval. | When they are approved, the English-only CI check (`frontend` `npm run check:devanagari`) is changed to exempt exactly one folder, `frontend/src/journal/locales/`, and nothing else. Until then the UI, the AI review language, and the Telegram and email templates are English. |
| OD-3 | **Follow ATP's real structure.** Z6 already builds `backend/app/journal` (PR #171 Z6-a, PR #172 Z6-b). The module is an **extension of that package**, not a parallel one. | The spec's `atp/journal/` becomes `backend/app/journal/` (backend) and `frontend/src/journal/` (frontend). Every spec table is mapped to an existing Z6 or ATP table or column, or to a new `journal_*` table whose name does not collide (section 3). |

## 1. What already exists (reuse, do not rebuild)

Read at `origin/main` ea07286, plus the open Phase 0 PRs named below. Their schema may still change while they are
being fixed, so section 3 is re-checked against them when they merge (`J1-b` starts with that check).

| Need in the spec | What ATP has | Where | Use in the journal |
|------------------|--------------|-------|--------------------|
| Journal package, auto-capture, behaviour tags, score, weekly review, page | Z6-a: `journal_entries` (one per ATP trade: plan, fills, stop history, chart refs, Greeks/IV, MAE/MFE), `journal_tags` (AUTO/USER, SUGGESTED/CONFIRMED/DISMISSED), `journal_settings` (per-trader thresholds/weights), capture hooks after commit (`persist_trade`, `position_monitor`), pure `detectors.py` and `score.py`, flag `discipline_engine` (off), ADR-0026. Z6-b: `review.py` (weekly review, rule lines, Copilot narration with `advice_in`), page **Journal & Discipline** (Week / Weekly review / Settings) | PR #171 (`claude/z6-journal-discipline`), PR #172 (`claude/z6b-weekly-review`); `backend/app/journal/{capture,detectors,score,service,settings,review,routes}.py`; `frontend/src/journal/{api,types,format}.ts`, `frontend/src/pages/JournalPage.tsx`; `docs/design/DISCIPLINE_ENGINE.md` | The base of the module. New code goes into the same package. The detectors, score, review and settings are extended, not copied. |
| Auth, tenancy | Row-level tenancy (ADR-0001); `get_current_user`; scopes (ADR-0009); Z6 personal scope (`user_id` and `tenant_id` on every query, 404 for other users) | `backend/app/auth/`, `backend/app/db/models.py` | The same personal scope for every journal table. Mentor mode is the only cross-tenant read (section 5, ADR needed). |
| Feature flags | Operator flags with per-tenant enablement; `DEFAULT_OFF_FLAGS`; `require_flag`, `flag_enabled` | `backend/app/platform/controls.py` | New flags, all **off by default** (section 4). |
| Plans and entitlements | Plan catalogue in code (`free`, `pro`, `business`) with limits and prices; `tenants.plan`; 402 checks with a clear message | `backend/app/plans/registry.py`, `backend/app/plans/limits.py` | Journal tiers added as fields of `Plan`, plus standalone journal plans (J7-a, JM-4, JM-6). |
| Billing provider | `BillingProvider` seam (ADR-0007), `ManualProvider`, **Razorpay Subscriptions already implemented** (K1b: hosted checkout, HMAC-verified idempotent webhooks, `billing_gateway_plans`, `billing_transactions`, `subscriptions`); Settings **BillingCard** | `backend/app/billing/{service,razorpay,webhooks,routes}.py`, `frontend/src/components/BillingCard.tsx` | **Reuse as is.** No new provider and no new interface: the spec's "if ATP already has a billing provider, reuse it" applies. |
| Usage metering | `usage_records` and `meter()` | `backend/app/billing/service.py` | Free-plan monthly trade count, imports and AI usage. |
| Broker connections | Encrypted `broker_credentials` (per tenant, `user_id` attribution), `broker_accounts` (Phase I2), adapters with `get_trade_book`, `get_order_book`, `get_positions` (Upstox, Zerodha, Angel One, Dhan, Fyers, Shoonya), `ContractSymbolBroker` (one contract, six spellings), `rate_budget`, `token_lifecycle`, daily login reminder (D5) | `backend/app/brokers/` | Broker API sync reads through these adapters, wrapped read-only (J2-d). The journal stores **no** credentials, only references to existing rows. |
| Read-only live data access per user | BT1 entitlement guard (`byob_data_tier`, Tier A on the user's own session, Tier C otherwise); BT1b `live_access.user_source` (one door for every live read; an organisation-level job gets no live data while the flag is on) | `backend/app/market_data/entitlement.py`, `fanin.py`; `live_access.py` in PR #177 (open) | Intraday candles for trade charts come only from the user's own session through `user_source` (J3-b, JM-17). |
| Charges engine | `india_costs`: STT, exchange, SEBI, stamp duty, GST by trade date and segment (option, future, equity intraday and delivery, exercise); brokerage is a per-order setting. Golden-checked; the Z5 builder and the PAPER broker use it (#180) | `backend/app/execution/india_costs.py` | **Wrapped, not duplicated** (section 3.2, decision C-1). |
| Actual charges from broker files | Contract-note and tradebook CSV parser with column aliases and charge components; contract-note matching to ATP trades | `backend/app/contract_notes/{parser,service,routes}.py` | The generic CSV mapper and the broker importers start from these aliases (J2-c). Contract-note matching to ATP trades stays as it is. |
| Lot sizes, instrument master, expiries | `fo_membership` (dated lot per underlying, NSE-sourced), `instruments` master (daily sync), `expiry_calendar` (dated lots, weekly-listing rules, DTE), `nse_expiries` | `backend/app/universe/`, `backend/app/instruments/` | Replace the spec's `journal_lot_sizes` (section 3). |
| Symbol parsing | `parse_contract` for the Upstox spelling, and attribute matching for every broker's spelling | `backend/app/brokers/contract_symbols.py` | The normalised fill parser reuses it and adds file-only spellings (Console, Groww) (J2-a). |
| Multi-leg structures, payoff | Options builder: payoff curve, breakevens, max P&L, 38 templates, metrics (`net_premium`, `reward_to_risk`, costs); `instruments/spreads.py` structures; Z5 payoff canvas (frontend) | `backend/app/options_builder/`, `backend/app/instruments/spreads.py`, `frontend/src/optionsBuilder/` | Payoff at entry, max risk and reward, planned vs realised R:R. The template shapes drive structure detection (J2-a, J3-c). |
| Candles | Shared lake `md_candles` (EOD bhavcopy backfill and nightly job, HB1 #169) with as-of reads; tenant-private `md_tenant_candles` (HB0 #164); history API (B5) | `backend/app/market_lake/` | Replace the spec's `journal_candle_cache` (section 3). |
| Server-side chart images | `chart_png` (deterministic PNG, standard library only, entry/stop/target marks); U8 EV2 (Z7) adds Telegram evidence images with the same minimal-layer rule; Z6 chart references (redraw "as of" from the lake) | `backend/app/ai/chart_png.py`; `docs/specs/ATP_SCREEN_EVIDENCE_CHARTS_ADDENDUM.md` | Journal chart sets render with the same renderer (extended for W/D/1H/15M marks), not a second port of the Trade renderer (J3-b). |
| Client charts | lightweight-charts v5 behind the ChartEngine (ADR-0023), drawings, ProChart; `EquityCurveChart`; CH3 replay is parked in N4 | `frontend/src/charting/`, `frontend/src/components/` | Chart viewer tabs, equity curve, replay (J3-c). |
| Vision on the user's own key | BYOK-V1/V2: `user_ai_keys` (encrypted per user), `user_vision_prefs` (daily and monthly caps, audit scopes, channels), `byok_allowed`, `vision_audit` (pending -> running claim, sanitised errors, numbers from the engine only, Telegram photo) | `backend/app/ai/{byok,vision_audit}.py`, `docs/BYOK.md` | The vision review uses **only** the user's own key (decision C-5). |
| AI client, prompts, metering | Provider seam (`providers.py`), tenant-configured provider keys (`ai_provider_configs`), `MeteredProvider` with the plan's AI budget, `llm_calls` (append-only log with prompt version), hash-based prompt versions, evals in CI (ADR-0020), Copilot `narrate` with grounding | `backend/app/ai/` | The text review and the summaries narrate rule-built facts through this path (J4-b). |
| No advice wording | `output_filter` (blocked and framed), `wording.banned_terms`, Z6-b `review.advice_in` (no instructions, nothing about next week) | `backend/app/ai/output_filter.py`, `backend/app/ai/wording.py`, `backend/app/journal/review.py` | Every AI text in the module goes through all three. |
| Telegram | Organisation bot (alert channel), inbound commands (`telegram_inbound`), per-user private chat link by one-time code (S3b-2 `notification_links`), outbox with retries (`alerts/dispatcher.py`), Telegram photos (BYOK-V2), "Open in app" deep links (PW4) | `backend/app/alerts/`, `backend/app/telegram_inbound/` | Replaces the spec's `journal_telegram_links` (section 3). |
| Email | Platform mailer (`PLATFORM_SMTP_*`), signed one-click unsubscribe and `email_opt_outs` (S3b-2) | `backend/app/notifications/mailer.py`, `backend/app/alerts/links.py` | Journal reports by email, with their own unsubscribe category (J5-c). |
| Web push | PW3 categories and deep links | `frontend/src/pwa/`, `backend/app/alerts/webpush.py` | Optional third channel for journal reports. |
| Events calendar | `market_events` (global or tenant, kind, window; the Risk Guardian acts on BLOCK and SIZE_CUT); `earnings_calendar_events`; Z6 `held_through_event` reads `market_events` | `backend/app/db/models.py`, `backend/app/risk_engine/guardian.py` | Replaces the spec's `journal_events_calendar` (section 3, JM-15). |
| Tax report | Financial-year report (equity intraday, F&O, crypto) over closed ATP trades | `backend/app/tax/report.py` | Extended with the journal's trades as a second source (J7-b). It has two problems to fix first: its own STT table differs from `india_costs`, and it uses the legacy options-turnover method (JM-1, JM-32). |
| Exports | CSV/JSON compliance exports with a SHA-256 header | `backend/app/exports/` | The pattern for journal CSV exports. No PDF library is installed yet (JM-21). |
| Background work | No Celery or RQ. The pattern is DB rows as queue entries, claimed with a conditional UPDATE (`vision_reports`, `research_studies`), run by a worker process (`research_worker` under its own compose profile); the trading worker's loop is never used for slow work (ADR-0004, ADR-0010) | `backend/app/workers/`, `backend/app/ai/research_jobs.py` | A `journal` worker process with its own compose profile (J2-d). |
| File storage | **None for user files.** R2 is read-only (history import) and used for backups; contract notes store parsed rows, not files; Vision stores only image hashes | `backend/app/market_lake/object_store.py` | A new small write-capable blob-store seam (J3-a, JM-18). |
| Analytics formulas | `trading/analytics.py` (summary, group stats), `ai/coach.py` (win rate, expectancy, profit factor, streaks, behaviour flags), Z6-b review totals | `backend/app/trading/analytics.py`, `backend/app/ai/coach.py`, `backend/app/journal/review.py` | One shared stats core, so profit factor and expectancy are not defined three times (J6-a). |
| Plan-before-trade and risk caps (PAPER) | Z5 P1-d ticket, risk-by-default caps, daily loss lockout with exits allowed | PR #179 (open) | ATP's own PAPER guard. Journal guardrails never feed into it (decision C-7). |
| i18n | `i18next` and `react-i18next` with one instance per namespace (`copilot/i18n.ts`, `locales/en.ts`); `check:devanagari` | `frontend/src/copilot/`, `frontend/scripts/check-devanagari.mjs` | A `journal` namespace with `en` only (OD-2). |

## 2. Conflicts with standing rules, and how each is resolved

| # | Spec says | Standing rule | Resolution |
|---|-----------|---------------|------------|
| C-0 | "Start now with Milestone 1" | Phase 0 freeze; OD-1 | Build after Z11. Only this plan ships now. |
| C-1 | `journal_charge_schedules` in the DB, admin-editable, statutory rates included | One reviewed source for costs (`india_costs`, used by PAPER fills, backtests and the builder); "no hardcoded values" | Statutory rates stay in `india_costs`, which is dated and golden-tested, so the journal, PAPER and backtests agree. A rate change is a reviewed PR with a test. **Brokerage**, the only per-broker and per-plan part, goes into a new DB table, `journal_brokerage_schedules`, which an admin can edit. `backend/app/journal/charges.py` wraps both and prefers actual charges from the broker. Why: a DB copy of the statutory tables would drift from the engine that PAPER and backtests use, and a wrong rate in the DB would change journal numbers with no review. The admin page shows the statutory table read-only, with its sources. Whether statutory overrides are ever editable is JM-9. |
| C-2 | Module "must not import from or modify execution/strategy code" | ADR-0004/0005; Z6 already hooks `persist_trade` and `position_monitor` | Z6's hooks are owner-approved Phase 0 work and stay as they are. The journal module adds **no new hook** in the trade path. It may import only pure modules: `app.execution.india_costs`, `app.options_builder.payoff`/`metrics`, `app.instruments.expiry_calendar`, `app.brokers.contract_symbols` (parsing only). A boundary test (`tests/test_journal_import_boundary.py`, J1-b) fails on any import of the order path (`execution.router`, `paper_broker`, `order_*`, `signal_execution`, `contract_execution`, `multileg`, `strategy_engine`, `deployments`, `workers.trading_worker`). |
| C-3 | AI coach output, daily and weekly summaries, AI-suggested tags | ADR-0006: AI never suggests entries, strikes or exits; Copilot no-advice wording | The facts are built by rules from the trader's data. The model only explains them. Every AI text passes `output_filter`, `wording.banned_terms` and `review.advice_in`, with one retry and then the rule text. `one_question_for_the_trader` must end with "?" and pass the same checks. The weekly ritual's "write next week's plan" is the trader's own text: the AI never drafts a plan, a level, a strike or an exit. AI-suggested tags describe past trades and start SUGGESTED (the Z6 rule). |
| C-4 | Risk guardrails | ADR-0004: alert-only, never block exits | Journal guardrails only send alerts. They never cancel, place or block anything, at a broker or in ATP. They run in the journal worker, never in the trading worker. |
| C-5 | Vision review "send the charts to the Claude vision model"; text review "via ATP's existing AI client" | BYOK: API keys per user, never shared; never one user's key for another user or for the organisation | **Vision runs only on the user's own key** (`byok_allowed`, the user's caps and a new audit scope). **Text review** uses the tenant's own configured provider (`ai_provider_configs`; in the beta one tenant is one user) or the user's BYOK key, and never a platform-shared key. A rule-based review (checklist and facts) is always shown, so the feature works with no key at all. A Coach plan unlocks the feature, not a platform-paid key (JM-7). |
| C-6 | Prompt templates "stored in DB and editable by admin" | ADR-0020: a prompt change updates its eval baseline in the same PR; a model registry and shadow mode | Templates are reviewed data files in `backend/app/journal/prompts/`, versioned by hash (`prompt_versions`), with golden sets in `backend/evals/`. An admin can pick between approved versions but cannot type a new prompt into production (JM-8). |
| C-7 | Trade plan limits (daily loss, max trades) | Z5 P1-d enforces caps and lockouts for ATP PAPER entries; ADR-0004 | The two stay separate. The journal's limits are the trader's own, alert-only, and apply to broker accounts. The ATP PAPER account keeps P1-d. The journal reads P1-d's events for display and never writes risk settings. |
| C-8 | PAPER-only Phase 0 | No API order path reachable from ATP | The module is built after Phase 0 and never places orders. Broker sync goes through a read-only adapter wrapper that allows only `get_profile`, `get_trade_book`, `get_order_book`, `get_positions` and daily P&L reads, and raises on every other method (test). Imported trades are records of the user's own broker activity, never ATP orders. They are never written to `trades`, so the risk engine, position monitor, reconciliation and kill switch never see them. |
| C-9 | "No hardcoded business values" | ATP keeps plans and statutory rates in reviewed code by design (`plans/registry.py` docstring) | Thresholds, limits, intervals, tolerances, templates, brokerage and event rows are settings or DB/data files. Plan prices and statutory rates stay in reviewed code with env overrides, as ATP does today, unless the owner decides otherwise (JM-4, JM-9). |
| C-10 | EN/HI/MR UI | OD-2: English only for now | i18n plumbing with English source strings only. |
| C-11 | "No broker credentials stored by this module" | ADR-0006 (AI never sees credentials), envelope encryption (ADR-0008) | No credential columns in any journal table. Only `credential_id` and `broker_account_id` references. Broker files often carry a client id: it is replaced by a salted hash on import and never stored or shown in clear. |
| C-12 | Charts "stored in file storage" | ADR-0026 (Z6): chart snapshots are references, not images | The journal stores the reference **and** the PNG with its SHA-256, because intraday and option-contract bars come from the user's own broker session and cannot be redrawn from the shared lake later. The new journal ADR amends ADR-0026 for journal chart sets only (JM-18). |
| C-13 | Mentor sees students' journals | ADR-0001 row-level tenancy; Z6 personal scope | Explicit, opt-in, revocable `journal_mentor_shares`. Every mentor read goes through one share check, is limited to the shared scopes, and writes an audit row. Needs its own ADR before J8-a (JM-11). |
| C-14 | Live and broker data for charts and sync | BT1/BT1b: per-user sessions only; organisation-level jobs get no live data while `byob_data_tier` is on; data terms per broker | Sync and intraday candles are per-user jobs on the user's own credential, through `live_access.user_source`. Candles are cached tenant-private only. Brokers whose terms are not confirmed are file-import only (JM-16). |
| C-15 | Spec ADR "Journal as isolated module" | OD-3 | The ADR becomes "Journal module as an extension of the Z6 journal package". It covers the import boundary, the blob store, chart images and the read-only sync. The number is the next free one when J1-a is written (0026 is Z6's). |

## 3. Data model mapping

### 3.1 Every spec table (v1 §3 and v1.1 §8)

Z6 tables are `journal_entries`, `journal_tags` and `journal_settings`. No new table reuses those names. The spec's
`journal_trades` is renamed **`journal_trade_groups`**: Z6 already uses `trade_id` for the ATP `trades` row, and a
second meaning of "journal trade id" in the same tables (`journal_tags.trade_id` next to `journal_tags.journal_trade_id`)
invites wrong joins. The UI still says "Trades".

| Spec table | Maps to | New or existing | Notes |
|------------|---------|-----------------|-------|
| `journal_accounts` | `journal_accounts` | new | Per user: `kind` = `ATP_PAPER` (one per user, the Z3 PAPER account, created on first use) / `BROKER_SYNC` / `FILE_ONLY`; `broker_name`, `nickname`, `capital_base`, `currency` (INR); optional `broker_account_id` -> `broker_accounts.id` and `credential_id` -> `broker_credentials.id` (references only); `sync_interval_minutes` (default from settings); `client_id_hash`. PAPER and broker P&L are never summed into one number (JM-28). |
| `journal_imports` | `journal_imports` | new | One row per file or per sync batch: source, file SHA-256 (unique per account), row counts, status, row-level errors JSON. Separate from `contract_notes`, which matches files to ATP trades. |
| `journal_fills` | `journal_fills` | new | External executions only. ATP's own fills stay in `trades` and Z6 `journal_entries.fills_json`. Unique on (`account_id`, `broker_trade_id`), else on a content hash. `source` = `BROKER_API` / `FILE` / `MANUAL` (drives the verified badge). Charge components when the broker gave them. |
| `journal_positions` | `journal_positions` | new | One contract from open to flat (FIFO), with `group_id` -> `journal_trade_groups`. |
| `journal_trades` | **`journal_trade_groups`** | new (renamed) | `structure_type` (auto, editable; the spec's "strategy type"), `strategy_id` -> `journal_strategies` (the trader's method), underlying, direction, times, net premium, max risk, planned R:R, gross and net P&L, R-multiple, holding time, compliance score, `user_locked` (manual edits survive rebuilds, JM-14), `charges_source` (ESTIMATED / ACTUAL / MIXED). For ATP PAPER trades the group is derived from the `trades` rows and their Z6 entries (read-only), one per structure, as in the Z6 score. |
| (Z6 link) | `journal_entries.group_id` | **Z6 column added** | Nullable FK, so ATP PAPER legs belong to their group. |
| (Z6 link) | `journal_tags.group_id` | **Z6 column added** | `trade_id` becomes nullable. A CHECK requires exactly one of `trade_id` / `group_id`, with unique (`group_id`, `tag`) next to Z6's (`trade_id`, `tag`). Source gains `AI`. The status rules stay Z6's. |
| `journal_rulesets` | `journal_rulesets` + `journal_ruleset_versions` | new | The version rows are immutable (`rules_json`: text, type boolean/select/number, required, phase pre-entry/management/exit). A group stores the version it was checked against. Starter templates are seed data files (the pullback template from the spec included), copied per user and editable. |
| `journal_checklist_answers` | `journal_checklist_answers` | new | Per group, per rule of that version. |
| `journal_daily_stats` / `journal_summary_cache` | `journal_daily_stats` | new (one table) | Per account and IST date. Period views roll up from it. Rebuilt nightly and after each import. |
| `journal_ai_reviews` | `journal_ai_reviews` + `llm_calls` | new + existing | `llm_calls` already logs prompt version, model, input, output and cost (append-only). `journal_ai_reviews` keeps the parsed JSON, status (pending/running/ok/skipped/failed), `llm_call_id`, and `kind` = TEXT / VISION / DAILY / WEEKLY. |
| `journal_charge_schedules` | `india_costs` (statutory) + **`journal_brokerage_schedules`** | existing + new | See C-1 and 3.2. |
| `journal_lot_sizes` | `fo_membership` + `instruments` + `expiry_calendar.dated_lot_size` | existing | Dated, NSE-sourced. An admin correction is a new dated row with `source="admin"` and an audit entry (JM-10). |
| `journal_broker_sync_runs` | `journal_broker_sync_runs` | new | Also the sync job queue: queued -> running -> done / failed, claimed with a conditional UPDATE. |
| `journal_broker_daily_pnl` | `journal_broker_daily_pnl` | new | The broker's statement, next to the computed P&L, with the reconciliation status and tolerance. |
| `journal_charts` | `journal_charts` | new | Group, timeframe, `storage_key`, `image_sha256`, `generated_at`, marks JSON, the candle source and the as-of. Z6 `chart_entry_json` / `chart_exit_json` stay the references for ATP trades. |
| `journal_strategies` | `journal_strategies` | new | The trader's own methods (name, description, linked ruleset, market condition). Different from `custom_strategies` (DSL), `option_strategies` (builder saves) and Z6's "setup" label. The UI says "Strategy (your method)"; the auto shape is "Structure". |
| `journal_trade_stories` | `journal_trade_stories` | new | One per group: thesis, trend read, zone, trigger, planned risk and reward, exit plan, mood. Z6 `plan_json` stays the platform's plan; this is the trader's words. |
| `journal_vision_reviews` | `journal_ai_reviews` (`kind = VISION`) | new (merged) | The same claim and drain as `vision_reports`, on the user's key. No separate table. |
| `journal_trade_plans` | `journal_trade_plans` | new | Date, scope (day / week), fields JSON, template id. |
| `journal_plan_vs_actual` | `journal_plan_vs_actual` | new | A snapshot per day, stored so that later edits do not rewrite history. |
| `journal_guardrail_events` | `journal_guardrail_events` | new | Never `risk_events` (that table feeds ATP's risk views and the guardian). |
| `journal_notification_prefs` | `journal_notification_prefs` | new | Per user: which reports, which channels, send times. The tenant's quiet hours (`notification_policies`) still apply. |
| `journal_telegram_links` | `notification_links` | existing | The S3b-2 per-user link by one-time code. |
| `journal_mistake_tags` + mapping | **`journal_tag_catalog`** + `journal_tags` | new + existing | The catalogue is the curated, editable taxonomy (seeded, with an optional Z6 score component per tag). The mapping is Z6's `journal_tags` (source AUTO / USER / AI, status SUGGESTED / CONFIRMED / DISMISSED). The 8 Z6 detector tags are catalogue rows that cannot be deleted. |
| `journal_events_calendar` | `market_events` (+ action `INFO`) | existing | A one-line Risk Guardian change: `INFO` events never change a verdict (test). Today any action other than BLOCK falls into the size-cut branch, so this change is needed before any informational event is seeded (JM-15). |
| `journal_mentor_shares` | `journal_mentor_shares` (+ `journal_comments`) | new | Student, mentor (user and tenant ids), scopes, status, `revoked_at`. Comments are their own table. |
| `journal_tax_reports` | `journal_tax_reports` | new | A stored snapshot (inputs hash, method, JSON, file key). The computation lives in `app/tax/report.py`. |
| `journal_candle_cache` | `md_candles` (shared EOD) + `md_tenant_candles` (tenant-private intraday) | existing | Daily and weekly from the lake. 1H, 15M and option-contract bars from the user's own broker session, cached tenant-private (JM-17). |
| (not in spec) | `journal_attachments` | new | Screenshots and other uploads: storage key, SHA-256, type, size. |
| (not in spec) | `journal_brokerage_schedules` | new | Per broker, segment and plan: flat or percent per order, cap, effective-from, source URL, checked-on date. |

### 3.2 Charges (decision C-1 in detail)

`backend/app/journal/charges.py`:
1. For each fill: `india_costs.segment_for(...)`, then `india_costs.leg_cost(day, side, price, qty, segment,
   brokerage_per_order=0)` for the statutory part.
2. Brokerage comes from `journal_brokerage_schedules` (broker, segment, the account's plan, as of the trade date, cap
   applied). GST is applied on brokerage + exchange + SEBI, using the same `india_costs` GST rate.
3. Where the broker gave actual charges (file or API), the actual numbers are used and the group is flagged
   `ACTUAL` or `MIXED`, with "estimated vs actual" shown.
4. Tests use worked examples for option buy and sell, futures, and equity intraday and delivery. A golden test checks
   that the statutory part equals `india_costs` for the same leg, so the two can never drift.

Before journal estimates are shown, the "(verify)" lines in `india_costs` (the 2026-04-01 STT changes) are confirmed
against the circulars (JM-32).

## 4. Flags (all off by default, added to `DEFAULT_OFF_FLAGS`)

| Flag | Gates |
|------|-------|
| `journal_module` | Every new `/api/journal/*` route and the new journal pages. With it off, Z6 behaves exactly as today. It needs `discipline_engine` on for the ATP PAPER account. |
| `journal_broker_sync` | The sync worker jobs and the "Connect for sync" action. |
| `journal_ai_review` | Text reviews and the daily and weekly AI summaries. Rule-based reviews do not need it. |
| `journal_vision_review` | Vision reviews (also needs `vision_byok`). |
| `journal_delivery` | Telegram, email and push reports and the pre-market reminder. |
| `journal_guardrails` | Guardrail alerts. |
| `journal_mentor` | Mentor shares and the roster. |
| `journal_public_cards` | Shareable verified P&L cards. |
| `journal_plans` | The journal tiers in the plan catalogue and the BillingCard. |

## 5. Backend layout (extends `backend/app/journal/`)

Existing Z6 files stay where they are: `capture.py`, `detectors.py`, `score.py`, `service.py`, `settings.py`,
`review.py`, `routes.py`. New files:

| File | Content |
|------|---------|
| `accounts.py` | Journal accounts, linking an existing broker account (reference only), the ATP PAPER account. |
| `fills.py` | Normalised fill model and the symbol parser (wraps `contract_symbols.parse_contract`, adds file spellings). |
| `positions.py`, `grouping.py` | Pure FIFO positions; structure detection (single leg, verticals, straddle, strangle, iron condor and fly, calendar, ratio, covered call, naked future) from the options-builder template shapes; rolls and adjustments; `user_locked` merges and splits. |
| `charges.py` | Section 3.2. |
| `importers/base.py`, `importers/{zerodha,upstox,angel_one,groww,dhan,fyers}.py`, `importers/generic.py` | Pluggable interface: `detect`, `parse`, normalised fills, row errors. Adding a broker means adding one file. The generic mapper builds on the `contract_notes` aliases. |
| `sync.py` | Broker API sync: the read-only adapter wrapper (C-8), incremental and idempotent, the daily P&L statement, reconciliation. |
| `analytics.py` | The shared stats core (J6-a), plus breakdowns and traceable numbers (every figure carries its group ids). |
| `rules.py` | Rulesets, versions, checklist, compliance score, starter templates. |
| `charts.py` | Chart sets per group (J3-b). |
| `ai_review.py`, `prompts/` | Text, vision, daily and weekly reviews (rule facts, then optional narration). |
| `plans.py` | Trade plans, plan vs actual. |
| `guardrails.py` | Alert-only limit checks after sync. |
| `delivery.py` | Report building and queueing on the existing outbox, mailer and push. |
| `tax.py` | Adapter feeding journal groups into `app/tax/report.py`. |
| `mentor.py`, `cards.py` | Shares, comments and the roster; verified P&L cards. |
| `routes_*.py` | New routers under `/api/journal/` with nouns that do not clash with Z6's (`week`, `trades/{id}`, `tags`, `settings`, `review`): `accounts`, `imports`, `sync`, `groups`, `rulesets`, `strategies`, `plans`, `analytics`, `reviews`, `charts`, `attachments`, `tax`, `mentor`, `cards`. |
| `backend/app/workers/journal_worker.py` | Claims sync runs, imports, chart renders and AI reviews; nightly stats; compose profile `journal`. It is never part of the trading worker loop. |
| `backend/app/storage/` | The blob-store seam: `LocalDirBlobStore` (dev and tests) and `R2BlobStore` (env credentials, its own bucket or prefix). It is shared with U8 EV2 if that has not added one by then. |

Migrations: one Alembic revision per PR, reversible, chained after the last Phase 0 revision. The `alembic check` and
Postgres upgrade CI jobs stay green.

## 6. Frontend layout (extends `frontend/src/journal/`)

- The `journal` page from Z6-b becomes the module's home. Two-segment routes (`routes.ts` supports `/area/sub`):
  `/journal` (dashboard), `/journal/trades`, `/journal/import`, `/journal/rules`, `/journal/analytics`,
  `/journal/plan`, `/journal/week` (Z6 week), `/journal/review` (Z6-b), `/journal/settings`. The trade detail is a
  drawer or query parameter on `/journal/trades`.
- `frontend/src/journal/i18n.ts` + `locales/en.ts` (namespace `journal`, the same setup as `copilot/i18n.ts`). Dates
  in IST; rupee amounts with Indian grouping (`format.ts` from Z6-b).
- Charts: lightweight-charts through the ChartEngine for candles, the equity curve and replay; the Z5 payoff canvas for
  payoff at entry; simple SVG bars for breakdowns, in the existing style. Every number has a tooltip with its trades.
- Settings: Accounts, Language (English only for now), Plan (the existing BillingCard), AI budget (the existing
  VisionByokCard and AI provider card).
- Mobile first: the PW2 layout rules (bottom nav, 44 px targets, keyboard-aware fields). The manual entry form fits
  one phone screen.
- The UI strings pass the banned-word list (as Z6-b's `format.test.ts` does).

## 7. Milestones re-sequenced into PRs (after Z11; N-list slot "N2-J")

Sizes: S < 400 changed lines, M 400-1200, L > 1200 (split if review needs it). "Touches" lists existing modules that
change. Everything else is new code in the journal package. Each PR: tests, self-review note, docs, flags off.

| PR | Milestone (v1.1 §9) | Content | Size | Depends on | Touches |
|----|---------------------|---------|------|------------|---------|
| J0 | 0 | This plan, spec copy, open questions, roadmap entry | S | - | docs only |
| J1-a | 1 | ADR "Journal module as an extension of the Z6 journal package" (C-2, C-12, C-15) and `docs/journal/README.md` (architecture, data model, adding an importer, updating brokerage schedules) | S | Z6-a/b merged, Z11 merged | `docs/adr/` |
| J1-b | 1 | Migration and models: accounts, imports, fills, positions, trade groups, Z6 link columns, brokerage schedules, daily stats; flags; import-boundary test; isolation tests | M | J1-a | `db/models.py`, `platform/controls.py`, Z6 `journal_entries` / `journal_tags` |
| J1-c | 1 | Frontend shell: `journal` i18n namespace (en), sub-routes, empty states, flag-off notice | M | J1-b | `routes.ts`, `Sidebar.tsx`, `JournalPage.tsx` |
| J1-d | 1 | Plan gating plumbing: journal tier fields on `Plan`, `require_journal_tier`, Free monthly trade count via `meter()` (prices and Razorpay come in J7-a) | S | J1-b | `plans/registry.py`, `plans/limits.py` |
| J2-a | 2 | Pure engine: fill model, symbol parser (all index underlyings, weekly and monthly, NSE and BSE), FIFO positions, structure detection, rolls; golden fixtures | L | J1-b | - |
| J2-b | 2 | Charges wrapper and brokerage schedules (admin CRUD and seed with sources); worked-example tests | M | J2-a | `admin` routes (read-only statutory view) |
| J2-c | 2 | File import: interface, Zerodha (CSV, XLSX after JM-20) and Upstox importers, generic mapper, preview -> confirm, idempotent re-import, row errors; manual entry API and form | L | J2-a, J2-b, J3-a (for kept files, optional) | `contract_notes/parser.py` (aliases shared, behaviour unchanged) |
| J2-d | 2 | Broker API sync (Upstox, Zerodha): read-only wrapper, `journal_worker`, sync runs, daily P&L and reconciliation, status UI, login-reminder tie-in | L | J2-c, BT1b (#177), D5 login reminder | `docker-compose*.yml` (profile `journal`), `brokers/` (read-only wrapper only) |
| J2-e | 2 | ATP PAPER bridge: groups from `trades` and Z6 entries (read-only), Z6 tags shown on groups | M | J1-b, Z3 merged | `journal/capture.py` (`group_id` set on capture) |
| J3-a | 3 | Blob-store seam (local + R2), attachments (screenshots: type sniffing, size cap, re-encode per JM-19) | M | J1-b | `core/config.py`, `.env.example` |
| J3-b | 3 | Chart sets: W/D from the lake, 1H/15M and the option contract from the user's session, marks, stored PNG and SHA, regenerate on demand | L | J3-a, J2-d, U8 EV2 | `ai/chart_png.py` (extended renderer), `market_lake` reads |
| J3-c | 3 | Trade page: legs, timeline, payoff at entry (builder payoff + Z5 canvas), chart viewer tabs, client-side replay on 15M | L | J3-b | `frontend/src/optionsBuilder/` (canvas reuse), `charting/` |
| J4-a | 4 | Strategies, trade story, rulesets and versions, checklist, compliance score, starter templates; discipline dashboard (compliance over time, compliant vs non-compliant P&L, per-strategy adherence, costliest rule) | L | J2-e, J2-c | `journal/score.py` (optional method component, JM-33) |
| J4-b | 4 | Text AI review: rule facts -> optional narration (tenant provider or BYOK), filters, prompts as files, evals golden set, per-user daily cost cap | M | J4-a | `ai/` (no change to shared modules expected), `backend/evals/` |
| J4-c | 4 | Vision review on BYOK: new scope `audit_journal_trades` on `user_vision_prefs`, drain in the journal worker | M | J4-b, J3-b, Z9 | `ai/byok.py` (scope), `db/models.py` (`user_vision_prefs` column) |
| J5-a | 5 | Daily and weekly trade plan form, templates and prefill, plan vs actual snapshot | M | J4-a | - |
| J5-b | 5 | Guardrail alerts (alert-only) evaluated after each sync | S | J5-a, J2-d | - |
| J5-c | 5 | Delivery: prefs, Telegram (`notification_links` + outbox + photos), email (mailer + unsubscribe category), push; per-trade review after exit, EOD, weekly, pre-market reminder | M | J5-a, J4-b | `alerts/dispatcher.py` (new categories), `alerts/links.py` |
| J6-a | 6 | Analytics completion (v1 §6) on `journal_daily_stats`; shared stats core used by coach, Z6-b review and the journal | L | J2-e, J4-a | `ai/coach.py`, `trading/analytics.py`, `journal/review.py` (call the shared core; outputs unchanged, golden test) |
| J6-b | 6 | Mistake taxonomy (catalogue, AI-suggested tags), event calendar (`market_events` INFO + seeds), heatmap, streaks, weekly ritual and reminder | M | J6-a | `risk_engine/guardian.py` (INFO never acts, test), `news_events` admin |
| J7-a | 7 | Plans and billing: journal tiers and prices in the catalogue, the existing Razorpay provider, BillingCard, Free limits | M | J1-d | `plans/`, `billing/` (catalogue only), `BillingCard.tsx` |
| J7-b | 7 | Tax report (journal source, `india_costs` STT, configurable turnover method) and exports (CSV; printable report per JM-21) | M | J6-a | `tax/report.py` |
| J7-c | 7 | Verified P&L card (signed, revocable link; no account numbers) | S | J2-d, J3-a | - |
| J8-a | 8 | Mentor mode: ADR, shares, roster, comments, audit | L | J6-a | `audit/` |
| J8-b | 8 | Remaining broker APIs (Angel One, Dhan, Fyers) and file importers (Angel One, Groww, Dhan, Fyers) | L | J2-d | - |
| J8-c | 8 | Hardening: 50k-fill rebuild benchmark, tenancy and security tests (with Z10 G6), mobile polish, docs | M | all | `docs/PERFORMANCE.md`, `docs/SLO.md` |

Order and parallelism: J1 (a-d) -> J2-a -> {J2-b, J2-e, J3-a} -> J2-c -> J2-d -> {J3-b, J4-a} -> ... WIP stays at
10 or fewer across all work. An engine, data or security PR (J2-a, J2-b, J2-d, J3-a, J8-a) gets an independent review
pass before merge.

## 8. Tests the spec asks for, and where they go

- `tests/test_journal_symbols.py`: weekly and monthly, NSE and BSE, NIFTY, BANKNIFTY, SENSEX, FINNIFTY,
  MIDCPNIFTY, BANKEX, stock F&O and equity, every broker's spelling.
- `tests/test_journal_positions.py`, `test_journal_grouping.py`: FIFO, partial fills, flips, each structure, rolls,
  `user_locked` surviving a rebuild.
- `tests/test_journal_charges.py`: worked examples, plus the golden equality with `india_costs`.
- `tests/test_journal_importers_<broker>.py`: anonymised fixtures in `tests/fixtures/journal/` (no real client ids).
- `tests/test_journal_import_flow.py`: import -> rebuild -> stats (integration); re-import is a no-op; overlapping
  files dedupe.
- `tests/test_journal_isolation.py`: same-tenant and other-tenant users get 404 on every new route; mentor reads
  only within a share.
- `tests/test_journal_import_boundary.py`, `test_journal_sync_readonly.py`: C-2 and C-8.
- `tests/test_journal_ai_wording.py`: every AI and rule text against the three filters; the question ends with "?".
- Frontend: `journal/*.test.ts` (formats, banned words); a Playwright journey on the mocked API (mobile and desktop).
- Performance: a 50k-fill rebuild benchmark (`backend/scripts/bench_journal.py`, next to `bench_backtest.py`, plus a CI
  guard test like `test_realism_benchmark.py`), with a target in `docs/PERFORMANCE.md`.
