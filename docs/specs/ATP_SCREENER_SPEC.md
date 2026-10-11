From Abhi — ATP "Screener" section spec v2 (English). Replaces v1 entirely.

Context: today ATP has only a basic Market Scanner (backend/app/scanner, ~744 lines: indicator, structure and option-chain filters; frontend ScannerPage.tsx). Build a full Screener section on that foundation. Extend the scanner, do not duplicate it; keep the scanner API backward-compatible and merge "Market Scanner" into "Screener" in the sidebar. All MASTER SPEC rules apply: never stop to wait (log questions with a provisional default in OPEN_QUESTIONS and continue), multitask during tests, self-review every diff before pushing, WORK_LOG, data lake (part B), global-first (v1.2), crypto (v1.3), Copilot spec (C2 tools), ADR-0004 (exits never blocked), ADR-0006 (AI acts only on human approval). Draft PRs only; no merge, no LIVE. Every change ships with tests. No hardcoded dates or prices.

══════════════════════════════════
0. v1 review — gaps fixed in v2
══════════════════════════════════
R1 Compute model was vague → Pipeline-style Factor/Filter/Classifier runtime + Chartink-style functions + Groww-style in-memory live engine (§1).
R2 Alerts were one line → full Alert & Notification System (§4): Telegram, email, in-app, web/mobile push, webhook, SMS seam.
R3 No ranking systems → Portfolio123-style weighted ranking trees (§2.5).
R4 No storage/performance plan → hot in-memory state + columnar store (DuckDB/Parquet with a ClickHouse seam) + result cache (§1.4, §1.5).
R5 No data-vendor seams → §1.3 (broker WebSocket default; TrueData / Global Datafeeds / Accelpix adapters; NSE archives).
R6 No SEBI research-analyst boundary → §6 (screener is a research tool, never a recommendation; wording filter).
R7 No data quality / observability / SLOs → §5.
R8 No transparent "screen fitness" (open version of Trade Ideas Holly) → §2.6.
R9 Shallow universe handling → §1.2 (instrument master history, delistings, renames, index membership as-of).
R10 Price-action screens were implicit → §2.1 now names them explicitly (pullback with power shift, commitment candle at level, liquidity sweep, trapped-trader retest, S/R flip, channel edge).

══════════════════════════════════
1. Architecture
══════════════════════════════════
1.1 Compute model (Quantopian Pipeline + Chartink + Screener.in):
 - Primitives: Factor (asset × time → number: Close, RSI(14), ROCE, OI change, PercentileRank), Filter (→ bool), Classifier (→ category: sector, industry, index membership, F&O member, market-cap bucket). Each primitive declares inputs, window_length, timeframe, as_of semantics and a cost estimate.
 - Expression language (typed, versioned grammar `screenql/1`): fields; functions (SMA, EMA, RSI, ATR, VWAP, Max/Min/Greatest/Least, Count(n, cond), CountStreak(cond), CrossAbove/CrossBelow, PctChange, Rank, PercentileRank(x, by=sector), ZScore, Lag); candle offsets `[0] [1] [n]`; timeframes `@1m … @1M`, mixable in one screen; groups ALL/ANY/NOT; comparisons incl. between/in; user custom ratios and formulas (saved, reusable, versioned); parameters (`$lookback`) for presets. Parser → typed AST → validator (type/unit/timeframe compatibility, cost cap, look-ahead guard) → plan → executor. Visual builder ⇄ query text ⇄ natural language (Copilot) share one AST; round-trip parity tests.
 - Pine-style: any platform indicator or Strategy DSL condition can be run as a screen; Strategy Builder entry conditions become a screen in one click and vice versa.
 - Execution modes: (a) EOD batch, vectorized over the universe on the columnar store; (b) intraday bar-close incremental (1m/3m/5m/15m/1h; only finalized candles — Chartink rule); (c) live event stream (ticks → in-memory 1-min aggregates + rolling metrics; transient state stays in memory, only minute aggregates are persisted — Groww pattern); (d) historical as_of run (point-in-time).
 - Cheap universe filters first, expensive factors last; per-tenant cost budget; result cache key = hash(AST, universe, as_of, timeframe) with short TTL (live 15–30 s, EOD until next bar).
1.2 Instrument master & universe: symbol history (renames, ISIN), listing/delisting dates, suspensions, index membership history (as-of), F&O list history, lot sizes, price bands, ASM/GSM flags, sector/industry taxonomy (NSE + GICS-style mapping), crypto pairs per exchange, global venues (v1.2). Historical screens include delisted names (survivorship). Corporate-action-adjusted series with an unadjusted flag.
1.3 Data & providers (provider seams; every source carries a freshness timestamp, SLA and fallback):
 - Live: tenant's broker WebSocket (existing Upstox/Zerodha/Angel/… adapters) by default; paid vendor adapters (TrueData, Global Datafeeds, Accelpix — NSE-authorised vendors); IBKR for global venues; crypto exchange WebSockets (v1.3).
 - EOD India: NSE bhavcopy (cash + F&O), security-wise delivery, participant-wise OI, FII/DII flows, India VIX, F&O ban list, price bands, ASM/GSM, index constituents, corporate actions/announcements/results calendar, shareholding patterns, SAST/PIT disclosures, bulk/block deals; BSE equivalents. Fundamentals from Phase AH ingestion, point-in-time by announcement date, as-reported vs restated flag.
 - Crypto: funding, OI, liquidations, basis (exchange APIs / CoinGlass-type seam).
 - Ingest jobs are idempotent, retried, checksummed, schema-versioned; data-quality gates (§5).
1.4 Storage: hot state in memory per worker (rebuilt from the minute store on restart); minute/daily bars and derived factors in a columnar store — v1 DuckDB over Parquet (fits Hostinger KVM 2), seam for ClickHouse (materialized aggregate states) when scale demands; Postgres for metadata (screens, alerts, runs, deliveries); Redis for pub/sub, dedupe keys and rate limits.
1.5 Performance targets (measured in a CI perf job): EOD, 2,500 symbols × 50-factor screen < 3 s (cached < 200 ms); intraday bar-close scan on the F&O universe (~200) < 1 s after bar finalize; live alert latency (tick → notification handoff) < 2 s p95; concurrent screens per tenant set by plan.

══════════════════════════════════
2. Categories, pre-built screens, scores, rankings
══════════════════════════════════
Every pre-built screen ships with: definition text, ScreenQL source, fields, data sources, editable default parameters, a docs page and a snapshot test of example results.

2.1 Technical / price-action (A)
 Structure and levels:
 - Support/resistance proximity: price within an ATR-scaled distance of a significant level or zone from the price_action engine (zones, swing highs/lows).
 - S/R flip retest: a broken resistance now being tested as support (or the reverse); first and second retest flagged separately.
 - Break of structure / change of character (BOS/CHoCH): the structural swing beyond a zone has broken (zone invalidated) or a counter-trend swing has broken (trend change signal).
 - Trendline / channel edge touch: price at a sloping trendline or at the upper/lower edge of a channel; channel midline cross.
 - 52-week high/low breakout with volume confirmation; near 52-week high/low.
 - Liquidity sweep / false break: wick beyond a swing high/low or zone with the close back inside (stop hunt, failed breakout).
 - Trapped-trader zone retest: price returns to the zone where a failed breakout left trapped buyers/sellers.
 Pullback and impulse:
 - Pullback in trend with power shift: trend up (or down), corrective leg with shrinking candle bodies/ranges and lower volume (futures volume for indices), then the first candle that closes back in the trend direction (power shift). Daily trend via Dow structure, 1H level, 15m trigger — generic parameters, editable.
 - Commitment candle at level: a decisive candle (body ≥ configurable fraction of range, closing near the extreme) at a significant level, in the trend direction.
 - Impulse → correction: large impulse candle(s) followed by contracting candles (flag/ABC style).
 - Pullback to rising/falling 20/50 DMA while the trend stays intact.
 - Inside bar, NR7 (range contraction).
 - Gap up/down; gap-fill.
 Candles and patterns:
 - Candlestick patterns only at significant levels (engulfing, pin bar, inside bar) — never without a level.
 - Chart patterns via a rule-based detector with confidence and drawn lines: triangle, flag, head-and-shoulders, double top/bottom.
 - RSI divergence (regular and hidden).
 - Multi-timeframe alignment: weekly/daily trend agree, 1H level, 15m trigger (top-down).
2.2 Intraday real-time (B): ORB 15/30, first-candle break, VWAP reclaim/rejection, new high/low of day, relative volume, sector top movers, circuit proximity, position in day range, gap-and-go / gap-fill, unusual volume burst (event windows, Trade Ideas style).
2.3 F&O / options (C, India first): the four buildups (long buildup, short buildup, short covering, long unwinding) on stock and index futures; max-OI strikes with OI change (walls); PCR (OI and volume) extremes; max-pain distance; IV rank/percentile; IV skew; unusual options activity (volume/OI multiple with minimum liquidity); FII index-futures long/short from participant-wise OI; rollover % and cost of carry; straddle premium / expected move; IV crush after events; ban-list proximity; premium-decay candidates for sellers (theta/day, IV rank, liquidity); flagship "credit spread finder" (daily trend + significant level + pullback confirmation + IV + liquidity; style-agnostic, all parameters editable).
2.4 Fundamental (D): Coffee Can, debt-free compounders, Piotroski ≥ 8, Magic Formula, Graham number/net-net, PEG < 1, high ROCE with low capex, dividend safety, turnaround (loss → profit), margin expansion, cash-flow quality (CFO/PAT), EPS acceleration (CAN SLIM C/A).
 Ownership & events (E): promoter stake up, pledge down/up, FII/DII/MF accumulation QoQ, SAST/insider buying, bulk/block deals by tracked investors, results this week, upcoming ex-dates, board meetings (fund-raise/buyback), index inclusion candidates.
 Relative strength & rotation (F): RS rating 1–99 vs index, industry group rank, RRG (sectoral indices and F&O stocks vs NIFTY, daily/weekly, tails).
 Breadth (G): advance/decline, % above 50/200 DMA, new highs − new lows, McClellan, breadth thrust, sector heatmap, delivery % trend, FII/DII flows.
 Crypto (H): funding extremes, OI rising with flat price (squeeze), liquidation clusters near price, basis (spot vs perp), volume spikes, new listings.
 Global (I): same engine per venue with a field-availability matrix.
 ETF/MF (J): later.
2.5 Ranking systems (Portfolio123 style): weighted multi-factor trees (node weight %, higher/lower-is-better, sector-neutral percentiles) producing a 0–100 rank; usable as a column, a filter (`Rank(MyQuality) > 80`) or a sort key. Platform rankings (Quality/Valuation/Momentum, RS, Piotroski, Altman Z, Magic Formula, Graham) with documented formulas and explainable components. No black boxes.
2.6 Screen fitness (transparent version of Holly): for every saved screen, rolling forward-return statistics of passers (1/5/20 bars), hit rate vs benchmark, turnover, regime-wise breakdown, sample size and a confidence note; search-adjusted via the trial ledger (deflated). Informational only, never a recommendation; low samples shown greyed.
2.7 History & backtest: "who passed on date X" (point-in-time: prices as-of, fundamentals by announcement date, universe as-of); rolling screen backtest (rebalance weekly/4-weekly/monthly, top-N by rank, equal or rank weighting, cost/slippage models from part C); benchmark comparison; trial ledger and disclaimers.

══════════════════════════════════
3. UI
══════════════════════════════════
Sidebar "Screener" (Market Scanner merged in). Home: category tiles, popular and my screens, alerts inbox, data-freshness strip. Builder: visual tabs (Descriptive / Technical / Intraday / F&O / Fundamental / Ownership / Events / Rank / Custom) ⇄ query editor (syntax highlighting, autocomplete, inline errors, cost estimate, live result count) ⇄ natural-language box (Copilot → AST, user confirms and can edit). Results: table (column presets, custom columns, sparklines), heatmap, chart grid with levels drawn, RRG view, F&O dashboard; data timestamps visible; CSV export; shareable screen URL within the tenant. Row actions: open chart, add to watchlist, backtest, send to Strategy Builder, ask Copilot, create alert. Mobile/PWA layout. English UI.

══════════════════════════════════
4. Alert & Notification System
══════════════════════════════════
4.1 Alert types (rule engine on a screen or on an instrument):
 - Screen alerts: new entrant, exit, re-entry, result-count threshold (useful for breadth screens), top-N change.
 - Instrument alerts: price level cross (including S/R zones and trendlines from the price_action engine), % move in N minutes, volume burst, OI/IV change, any ScreenQL condition on one symbol, option-chain events (OI wall shift, PCR cross), F&O ban add/remove, corporate events (results date, ex-date), news keyword (untrusted-wrapped, data channel only).
 - Portfolio/risk alerts from the existing risk engine — routed through the same Notification Service.
 - System alerts: data stale, feed down, ingest failed (ops channel).
4.2 Triggers & scheduling: EOD after bhavcopy ingest completes (event-driven, not clock-driven); every N minutes during the session (per-venue session calendar; crypto 24×7); bar-close per timeframe; live event stream; one-shot vs recurring; alert expiry date; pause/resume; quiet hours per user (timezone-aware); optional market-closed suppression.
4.3 Dedupe, throttle, grouping: idempotency key (alert_id, symbol, condition_hash, bar_time) in Redis with TTL; cooldown per (alert, symbol); max notifications per hour per user per channel (by plan); burst grouping ("12 stocks entered 'Volume shocker' at 10:15" as one message with the list); digest mode (hourly/EOD) per alert; priority levels (critical/normal/low) drive channel routing.
4.4 Channels (Notification Service with provider seams; per-user channel preferences with per-alert override):
 - In-app inbox (persistent, read/unread, filters, deep link to results/chart) plus real-time toast via SSE/WebSocket.
 - Web push (VAPID, PWA service worker); mobile push seam (FCM/APNs) for the future mobile app.
 - Telegram: per-user bot linking (deep-link code, chat-id verification; tenant bot-token seam). Templates with symbol, condition, value, timestamp, optional chart snapshot. Inline buttons limited to Open / Add to watchlist / Snooze 1h / Mute symbol. Trade-proposal buttons exist only through the monitor state machine (ADR-0006); an alert never places an order. Abhi's approver flow stays separate.
 - Email: transactional provider seam (SMTP/Resend/SES), HTML + text templates, per-alert unsubscribe link, digest emails, bounce and complaint handling.
 - Webhook: signed payload (HMAC, timestamp, replay window), versioned JSON schema {alert_id, screen_id, event, symbols[], trigger_values, bar_time, as_of, data_timestamps}, retries with backoff, dead-letter queue, per-webhook secret rotation; optional Chartink-compatible payload shape (stocks, trigger_prices, scan_name) for existing tools.
 - SMS/WhatsApp: seam only (cost, plan-gated, DLT compliance in India) — later.
 - Strategy/bot hand-off: alert → Strategy Builder signal input (PAPER by default) via the monitor state machine; never a direct order.
4.5 Delivery guarantees: outbox pattern (alert_events → deliveries per channel), at-least-once with idempotent consumers, per-channel retry policy, status (queued/sent/delivered/failed/suppressed) with reason codes; user-visible delivery log per alert; SLO p95 handoff < 2 s live, < 60 s batch.
4.6 Management UI: alert list (status, last fired, fire count, channels), create from any screen, result row or chart level; edit/pause/snooze/mute symbol; test-send; history with "what matched"; per-channel preferences; quiet hours; plan quota meter.
4.7 Templates & i18n: Jinja templates per channel, English default (Marathi optional per user), consistent fields, no advice wording (§6 output filter applies here too), locale number formatting.
4.8 Security & privacy: channel verification before first send; tenant isolation; secrets in vault/env only; webhook URL validation (no private IPs — SSRF guard); rate limits; audit log of alert create/edit/delete; data-residency note.
4.9 Tests: rule engine (entrant/exit/count), dedupe/cooldown, grouping, quiet hours across timezones, outbox retry and dead-letter, webhook signature, Telegram linking flow, email unsubscribe, load test (1,000 alerts × 200 symbols at bar close), chaos test (stale feed ⇒ alerts suppressed + system alert raised).

══════════════════════════════════
5. Production grade: quality, observability, SLOs
══════════════════════════════════
- Data-quality gates: row counts vs expected, duplicate/missing symbols, price spikes (> X σ flagged), unadjusted split/bonus detection, late or missing bhavcopy ⇒ screens marked "stale" (UI banner, alerts suppressed) — never silent.
- Freshness per source shown in the UI; staleness thresholds configurable.
- Observability: metrics (scan latency, cache hit rate, alerts fired/delivered/failed per channel, ingest lag), structured logs with run_id, tracing; dashboards and ops alerts.
- Reproducibility: every run stores AST version, universe snapshot id and data version so the result can be replayed.
- Multi-tenant plan limits: saved screens, alerts, channels, intraday timeframes, history depth, API calls; graceful degradation messages.
- API: REST + SSE for screens and alerts (OpenAPI); Copilot tools `build_screen`, `run_screen(as_of)`, `explain_results`, `create_alert` (proposal → user confirms).
- Pitfalls covered by tests: survivorship, fundamentals look-ahead (announcement date), restatement flag, corporate-action adjustment, indices without volume (use futures volume), illiquid strikes (min OI/volume/premium), over-fitted screen backtests (trial ledger), timezone and session calendars (NSE holidays, crypto 24×7, global venues), partial intraday candles, duplicate alerts, symbol renames.

══════════════════════════════════
6. Compliance (SEBI) wording
══════════════════════════════════
The screener is a research/education tool. Results are "matches", "candidates", "passed filters" — never "buy", "sell", "recommended", "target". The Copilot output filter (C1c) applies to alerts and explanations. Disclaimer on every screen page and alert footer. User-published screens (marketplace, later) carry "not investment advice" and a research-analyst-boundary flag (business decision for Abhi). AI-usage disclosure (Copilot C11) on the natural-language feature.

══════════════════════════════════
7. Build order (one draft PR each; design note first — ADR-0021 Screener engine, ADR-0022 Notification Service)
══════════════════════════════════
S1 ScreenQL parser/AST/validator + Factor/Filter/Classifier runtime + field registry + visual builder ⇄ query parity + migration of existing scanner filters. Tests: grammar fuzz, parity, offsets, mixed timeframes, look-ahead guard.
S2 Instrument master history + ingest jobs (bhavcopy cash/F&O, delivery, participant OI, FII/DII, VIX, ban list, bands, ASM/GSM, constituents, corporate actions/results calendar, shareholding, SAST/PIT, bulk/block) with provider seams, data-quality gates, freshness; columnar store (DuckDB/Parquet) + ClickHouse seam.
S3 Notification Service (§4): outbox, channels in-app + Telegram + email + webhook + web push, dedupe/throttle/grouping/digest, management UI, delivery log, tests. Migrate risk-engine alerts onto it.
S4 Screen alerts + instrument alerts + bar-close incremental engine + live event stream (broker WebSocket) + result cache.
S5 Categories A (all price-action screens in §2.1), C, F, G + F&O dashboard + RRG + breadth panel + heatmap.
S6 History as-of + rolling backtest + trial ledger + screen fitness (§2.6, §2.7).
S7 Ranking systems (§2.5) + categories B, D with scores.
S8 Categories E, H, I.
S9 Copilot tools (build_screen, run_screen, explain_results, create_alert) + natural-language box.
S10 J (ETF/MF), SMS/WhatsApp, mobile push, marketplace — later.
Acceptance: 50+ pre-built screens with docs; parity tests green; §1.5 performance targets measured in CI; point-in-time tests (no look-ahead); F&O buildup / RRG / breadth unit tests; price-action screens validated against the price_action engine fixtures (BANKNIFTY test data; NIFTY holdout stays sealed); alert pipeline load and chaos tests; stale data ⇒ zero alerts + banner; every number in an alert or explanation traceable to a data timestamp.
