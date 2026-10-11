# ATP — Trading Journal & Method-Discipline Module (Spec v1)

You are working in the AdvanceTradingPlatform (ATP) repo. Build a new, self-contained module called **Journal** inside ATP. Read this whole spec first, then study the existing ATP codebase (auth, tenancy, broker integrations, DB layer, React app structure, ADRs) before writing any code. Follow the existing ATP conventions, ADRs and CI rules. All code, comments, UI strings, docs and commit messages in English only.

## 0. Working rules (standing)
- Work continuously. Never stop to wait for the user. If something is unclear, log the question with a provisional default in `docs/journal/OPEN_QUESTIONS.md` and continue with the default.
- Self-review every diff before pushing (correctness, security, tests, naming). Merge PRs yourself once CI is green and reviewed. Do not deploy to the server — that stays with the user.
- No hardcoded business values. Everything a user might want to change (plan limits, charge rates, lot sizes, AI prompt templates, checklist templates) lives in config/DB and is editable from the admin/settings UI.
- ADR-0006 applies: the AI in this module only reviews and explains; it never places, modifies or suggests live orders. ADR-0004/0005 are unaffected — the Journal module must not import from or modify execution/strategy code.
- Log progress in `docs/journal/PROGRESS.md` after each milestone.

## 1. Product summary
A journal + analytics + method-discipline tool for Indian F&O retail traders (NIFTY, BANKNIFTY, SENSEX, FINNIFTY, MIDCPNIFTY, BANKEX, stock F&O, plus equity). Users import trades from their broker, the system groups legs into strategies, computes correct Indian charges and performance stats, checks each trade against the user's own written trading rules, and an AI coach reviews trades against those rules. Positioning: "the only journal that checks your trades against *your* method". Sold as a standalone ATP plan; later bundled with the user's Marathi trading course.

Target users: Indian retail F&O traders, majority outside top-30 cities. UI must be multilingual: English, Hindi, Marathi (i18n framework, English strings as source; translation files for hi/mr).

## 2. Architecture
- Backend: new FastAPI package `atp/journal/` (routers, services, models, importers, analytics, ai_review). Own DB tables, prefixed `journal_`. Tenant-scoped via existing ATP tenancy.
- Frontend: new React area `/journal/*` using the existing design system and layout shell; its own route group and state slice.
- Feature flag + plan gating via existing ATP plan/entitlement mechanism (add if missing). Plans: Free, Pro, Coach (see §8).
- Background jobs (imports, AI reviews, nightly stats rebuild) via ATP's existing task/queue mechanism.
- Mobile: responsive web / PWA first. Native app is out of scope for v1.

## 3. Data model (minimum)
- `journal_accounts`: user-defined trading accounts (broker, nickname, capital base, currency INR).
- `journal_imports`: one row per import (source broker, file hash, row counts, status, errors).
- `journal_fills`: raw executions (symbol, exchange, segment, instrument type, underlying, expiry, strike, option type, side, qty, price, timestamp, order id, trade id, brokerage/charges if present, source import id). Idempotent on (account, broker trade id); fall back to a content hash.
- `journal_positions`: a position = all fills of one contract (symbol+expiry+strike+type) from open to flat.
- `journal_trades`: a *trade* = user-facing grouping of one or more positions (e.g. Iron Condor = 4 legs; Bull Put spread = 2 legs; rolled/adjusted legs attached). Fields: strategy type (auto-detected, user-editable), underlying, direction (bullish/bearish/neutral), entry/exit timestamps, net premium, max risk, planned R:R, realised P&L gross/net, R-multiple, holding time, tags, notes, screenshots (file storage via existing ATP storage), mood/psychology fields, checklist result, AI review.
- `journal_rulesets`: a user's trading method as an ordered checklist (each rule: text, type = boolean/select/number, required yes/no, phase = pre-entry/management/exit). Versioned; a trade stores the ruleset version it was checked against. Provide starter templates (seeded, editable), including one "Trend → buyer/seller area → correction pattern → commitment candle" pullback template with rules like: trend decided from Daily (top-down W/D/1H/15M); entry only on pullback in trend direction, never on breakout; price entered a pre-marked zone; reversal/commitment candle closed at the zone with above-average volume; planned R:R ≥ 1:3; weekly expiry rule (if today is expiry, next weekly).
- `journal_checklist_answers`: per trade, per rule.
- `journal_daily_stats` / `journal_summary_cache`: precomputed analytics per account and period.
- `journal_ai_reviews`: prompt version, model, input snapshot, output, cost, created_at.
- `journal_charge_schedules`: STT, exchange txn charges, SEBI fee, stamp duty, GST, brokerage caps — per segment, per broker, with effective-from dates, editable in admin UI. Seed with current published rates and cite the source in a comment/doc; do not hardcode in code.
- `journal_lot_sizes`: per underlying with effective-from dates, editable.

## 4. Broker import (v1: file import; API sync later)
- Importers for Zerodha (Console tradebook CSV/XLSX), Upstox, Angel One, Groww, Dhan, Fyers. Each importer: detects format, normalises columns, parses Indian option/future symbols (weekly and monthly formats on NSE/BSE), infers side/qty/price, returns normalised fills. Build a pluggable importer interface so adding a broker is one file.
- Also a generic CSV mapper (user maps columns once; mapping saved per account).
- Manual trade entry form (fast, mobile-friendly) for the Free plan and for users without files.
- Import preview → user confirms → fills saved → positions and trades rebuilt for that account. Re-import of the same file is a no-op; overlapping files dedupe.
- Phase 2 (after v1 ships): Upstox and Zerodha API sync using ATP's existing broker connections, read-only.

## 5. Trade grouping & charges
- Build positions from fills (FIFO). Auto-group into strategies by same underlying, overlapping time window and recognised shapes: single leg, vertical spread (bull put / bear call / bull call / bear put), straddle, strangle, iron condor, iron fly, calendar, ratio, covered call, naked future. Unmatched legs stay as single-leg trades. User can merge/split/rename groups; manual edits persist across rebuilds.
- Detect adjustments/rolls (close one strike, open another in the same strategy) and attach them to the parent trade.
- Charges engine: compute STT, exchange charges, SEBI fee, stamp duty, GST and brokerage from `journal_charge_schedules` by trade date; where the broker file already contains actual charges, prefer actual and show a "estimated vs actual" flag. Unit-test against worked examples for options sell/buy, futures, and delivery.

## 6. Analytics (per account, filterable by period, underlying, strategy, direction, tag, weekday, expiry-distance)
- Equity curve (gross and net), cumulative R, drawdown and recovery.
- Win rate, profit factor, expectancy (₹ and R), average win/loss, largest win/loss, R-multiple histogram, Sharpe-like per-trade ratio.
- Breakdowns: by strategy, by underlying, by day of week, by days-to-expiry at entry, by holding time, by rule-compliance (compliant vs non-compliant trades — this is the headline chart: "your P&L when you followed your rules vs when you didn't").
- Premium capture % for credit trades (realised / max credit).
- Charges as % of gross P&L; charges per trade.
- Streaks, revenge-trade detector (new trade within N minutes of a loss, N configurable), overtrading flag (trades/day above the user's own stated max).
- All charts in the existing ATP charting stack; every number must be traceable (tooltip shows the trades behind it).

## 7. Method checklist & AI coach
- Pre-entry checklist: user can fill it when logging a trade (optional); post-trade, missing required rules are shown as "not checked".
- Rule compliance score per trade; filters and stats use it.
- AI review (Claude API via ATP's existing AI client; prompt templates stored in DB and editable by admin): input = trade details, legs, timing vs expiry, user's ruleset, checklist answers, user notes, optional screenshot. Output = structured JSON {summary, rules_followed[], rules_broken[], risk_observations[], one_question_for_the_trader, severity}. Rendered as a card on the trade page. Daily and weekly AI summaries ("this week you broke rule X 4 times; those trades lost ₹Y"). Cost cap per user per day (configurable). AI never recommends an entry, strike or exit.
- Trades can be reviewed in the user's UI language.

## 8. Plans & billing
- Free: manual entry + CSV import, up to a configurable number of trades/month, basic stats.
- Pro: unlimited imports, all analytics, checklist, charges engine, exports (CSV/PDF).
- Coach: Pro + AI reviews and weekly AI summaries.
- Prices are configurable (defaults: Pro ₹299/month, Coach ₹599/month, annual with discount). Payments via Razorpay (subscriptions + webhooks). If ATP already has a billing provider, reuse it; otherwise add Razorpay behind an interface.

## 9. UI (mobile-first; most users will open it on a phone after market close)
- Dashboard: today/this week P&L, equity curve, compliance %, open trades, "biggest leak" insight.
- Trades list with filters; trade detail page (legs, payoff at entry, timeline, checklist, notes, screenshots, AI review).
- Import wizard. Rulesets editor. Analytics page with the breakdowns in §6. Settings: accounts, language, plan, AI budget.
- Onboarding: pick broker → import or add first trade → pick or write a ruleset → first insight shown within 5 minutes.
- Language switch EN/HI/MR; dates in IST; currency ₹ with Indian grouping.

## 10. Non-functional
- Tests: unit tests for symbol parsing (weekly/monthly, NSE/BSE, all index underlyings), FIFO positions, strategy detection, charges engine with worked examples, importer per broker with sample files (create anonymised fixtures). Integration tests for import → trade rebuild → stats.
- Performance: 50k fills per account should rebuild in seconds; analytics served from cache tables.
- Security: files scanned/validated, size limits, tenant isolation tests, no broker credentials stored by this module in v1.
- Observability: use existing ATP logging/metrics; import failures visible to the user with row-level errors.
- Docs: `docs/journal/README.md` (architecture, data model, how to add a broker importer, how to update charge schedules), ADR for "Journal as isolated module".

## 11. Milestones (ship each as a PR set; update PROGRESS.md)
1. Scaffold module, DB migrations, feature flag, plan gating, i18n plumbing, empty UI shell.
2. Fills → positions → trades engine + charges engine + Zerodha and Upstox importers + manual entry. Tests.
3. Analytics §6 + dashboard + trades pages.
4. Rulesets + checklist + compliance analytics + starter templates.
5. AI coach + daily/weekly summaries + cost caps.
6. Billing + plans + exports + remaining importers (Angel One, Groww, Dhan, Fyers, generic mapper).
7. Hardening: performance, security, mobile polish, docs. Then Phase 2 broker API sync.

Start now with Milestone 1. Before coding, write `docs/journal/PLAN.md` mapping this spec to concrete files in the current ATP structure, then proceed without waiting.


---

# ATP Journal Module — Spec v1.1 Addendum

This addendum extends `ATP_journal_module_spec.md` (v1). Where it conflicts with v1, v1.1 wins. Read it fully, update `docs/journal/PLAN.md` and `OPEN_QUESTIONS.md`, and re-sequence milestones as given in §9. Same standing rules: English only, no hardcoded values, log questions with defaults and keep going, self-review every diff, AI never places or suggests live orders (ADR-0006).

## 1. Broker API auto-sync moves into v1 (was Phase 2)
- Primary data source is now the broker API, read-only: orders, trades/fills, positions and daily P&L, pulled through ATP's existing broker connections (Upstox and Zerodha first; Angel One, Dhan, Fyers next). File import stays as fallback and for history before the connection date.
- Sync job: incremental, idempotent (broker trade id), runs every N minutes during market hours and once after close (N configurable, default 15). On each sync: new fills → positions → trades rebuilt → charts generated → checklist/AI/vision review queued → notifications sent.
- Sync status and last-sync time visible per account; errors surfaced to the user.
- Daily P&L statement from the broker is stored alongside computed P&L; show "broker vs computed" reconciliation with a tolerance (configurable).

## 2. Automatic trade charts
- For every trade, generate a chart set at entry time and update it at exit: top-down Weekly, Daily, 1H and 15M of the underlying (spot; NIFTY futures volume pane where relevant), plus the traded option contract's own candles when available.
- Marks: entry (per leg, price/time), exit, adjustments/rolls, stop and target if the user entered them in the plan, and the planned zone if the user drew/entered levels. Keep charts clean: only trade-relevant marks, no indicator clutter.
- Candle data via ATP's existing market-data layer (broker historical APIs); cache candles per symbol/timeframe/date; render server-side (reuse the chart renderer from the Trade repo if it fits ATP; otherwise port it as `atp/journal/charts/`).
- Store rendered images in ATP file storage, attached to the trade permanently; regenerate on demand. Chart viewer in the trade page with timeframe tabs.
- Trade replay: candle-by-candle playback on the 15M chart from entry to exit (client-side, from cached candles).

## 3. Strategy box, trade story and vision-based discipline review
- Each trade has a Strategy field (select from the user's saved strategies or type a new one) and a structured "trade story" box: thesis, trend read, zone/level, trigger (e.g. commitment candle), planned risk and reward, exit plan. Also free notes and mood.
- Strategies are user-defined objects (name, description, linked ruleset/checklist, intended market condition). Analytics per strategy.
- Vision review: send the generated charts plus the trade story, strategy and ruleset to the Claude vision model (reuse the Vision audit pipeline pattern from the Trade repo; prompt templates in DB). Output JSON: did the chart support the stated strategy, which rules were met/violated as seen on the chart (trend direction, pullback vs breakout entry, zone presence, commitment candle, R:R), confidence, one question for the trader. Rendered beside the AI text review. Per-user daily cost cap for vision (configurable, default small).
- Discipline dashboard: compliance % over time, P&L of compliant vs non-compliant trades, per-strategy adherence, "most expensive rule you break".

## 4. Trade Plan (pre-market) and Plan vs Actual
- Daily (and optional weekly) plan form, available before market open and prompted via Telegram/email at a configurable time: market bias (bullish/bearish/range) per underlying, key levels/zones, planned setups and strategies, max number of trades, max daily loss, max risk per trade, "no-trade" conditions (events, expiry day rule), notes.
- After close: Plan vs Actual report — trades taken vs planned, bias vs realised move, limits respected or broken, levels that held. Stored with the day; feeds the discipline stats.
- Plan templates and the user's recurring rules (e.g. weekly expiry rule, R:R ≥ 1:3) prefill the form.

## 5. Delivery: Telegram and email
- Telegram: user links the ATP Telegram bot with a one-time code (reuse ATP's existing Telegram integration if present). Deliverables: per-trade review (chart image + AI/vision summary) shortly after exit, end-of-day report (P&L, charges, compliance, plan vs actual), weekly report, pre-market plan reminder, guardrail alerts (§6). User chooses which to receive and the send times.
- Email: same reports as HTML email with charts inline; via ATP's existing mailer.
- All messages generated from templates in the user's UI language (EN/HI/MR).

## 6. Risk guardrails (alert-only, never blocking)
- User-defined limits from the trade plan: daily loss, max trades/day, max risk per trade, no-trade windows (events, first N minutes, expiry-day rule), max open positions. On sync, evaluate and send Telegram/email alerts when crossed. The module never cancels or places orders.

## 7. Additional features (build after §1–§6)
- Tax report: financial-year F&O and equity statement for ITR — turnover as per Indian rules (absolute P&L method for options and futures; keep the method configurable with a documented default), gross/net P&L, charges by category, segment-wise summary, exportable PDF/Excel for the user's CA. Add intraday vs delivery split for equity.
- Mistake taxonomy: a curated, editable tag set (chased breakout, no commitment candle, early exit, late exit, revenge trade, oversized, traded against daily trend, ignored event, expiry-day violation...). AI suggests tags; user confirms. Report: cost of each mistake type.
- Event calendar overlay: expiry days, RBI/Fed/US CPI, index results season, budget — seeded and admin-editable; analytics by event day vs normal day.
- Calendar heatmap of daily P&L and compliance; streaks; weekly review ritual (Sunday checklist: review week, update rules, write next week's plan) with reminder.
- Mentor mode: a user can share their journal (read + comment) with a mentor account; mentor sees a roster of students, each student's compliance and P&L summary, and can comment on trades. Designed for the course bundle. Permissions strictly opt-in and revocable.
- Verified P&L card: shareable image/link of a period's P&L with a "broker-synced" badge when all trades in the period came from API sync (not manual/CSV). No account numbers on the card.
- Payoff at entry: show the multi-leg payoff diagram and max risk/reward as at entry; compare planned R:R to realised.
- Expiry-distance analytics (already in v1) extended with "premium captured vs days held" for credit trades.

## 8. Data model additions
`journal_broker_sync_runs`, `journal_broker_daily_pnl`, `journal_charts` (trade id, timeframe, storage key, generated_at, marks json), `journal_strategies`, `journal_trade_stories`, `journal_vision_reviews`, `journal_trade_plans` (date, fields json, template id), `journal_plan_vs_actual`, `journal_guardrail_events`, `journal_notification_prefs`, `journal_telegram_links`, `journal_mistake_tags` + trade mapping, `journal_events_calendar`, `journal_mentor_shares`, `journal_tax_reports`, `journal_candle_cache`.

## 9. Revised milestones
1. Scaffold, migrations, feature flag, plans, i18n shell (unchanged).
2. Broker API sync (Upstox, Zerodha) + fills/positions/trades engine + charges engine + manual entry + CSV fallback. Tests.
3. Automatic charts + trade page + trade replay.
4. Strategies, trade story, rulesets/checklist, text AI review, vision review, discipline dashboard.
5. Trade Plan + Plan vs Actual + guardrail alerts + Telegram/email delivery.
6. Analytics completion (v1 §6) + mistake taxonomy + event calendar + heatmap/streaks/weekly ritual.
7. Billing + plans (Free/Pro/Coach; vision and Telegram reports in Coach) + tax report + exports + verified P&L card.
8. Mentor mode + remaining broker APIs + hardening, performance, mobile polish, docs.

Update PLAN.md now, then continue from the current milestone without waiting.
