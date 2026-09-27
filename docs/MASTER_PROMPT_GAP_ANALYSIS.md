# Master prompt (Sep 2026 revision) vs the codebase - gap analysis

The revised master prompt adds Part III (V1-V4 release phasing, V3.14 and V4 now in full), a
consolidated safety-rule list and a per-module Definition of Done. This file maps every part of
it onto what is built on `main` + Phase F, so the next phases are chosen against facts. Status
words: **done** (built and tested), **partial** (built, with named gaps), **missing**.

## Part I - product spec (Sections 1-46)

| Section | Status | Notes |
| --- | --- | --- |
| 1-7 objective, stack, pipeline, monolith, tenancy, RBAC, auth | done | React/Vite instead of Next.js; int ids instead of uuid. Roles: SUPER_ADMIN/OWNER/USER/STRATEGY_CREATOR/SUPPORT/VIEWER. Email verification missing. |
| 8-9 BrokerInterface, token security | partial | Upstox, Zerodha, Shoonya real; stubs for others. `get_balance()` / `disconnect()` (Phase G3), `exit_position()` (market square-off default) and `subscribe_market_data()` (raises until an adapter streams) added (Phase M). Tokens envelope-encrypted under per-tenant data keys (Phase N1). |
| 10 market data | partial | Candles/LTP via broker REST with Redis cache; staleness gate on candles and quotes (Phase G1); no websocket feed. |
| 11 instrument master | done (Phase F1) | NSE equity/index/F&O; MCX/crypto specs are a static registry, not master rows. `active` flag and ISIN not stored. |
| 12-14 indicators, DSL, visual builder | done | Rule-based DSL and builder; versioned reference `docs/STRATEGY_DSL.md` (Phase N5). |
| 15-22 signal, risk, sizing, order, idempotency, position, reconciliation | done / partial | Order state machine, idempotency, reconciliation exist. §17 checks: data fresh (Phase G1) and broker healthy (Phase G2 circuit) done; **margin available** for buys and **instrument/expiry validity** still missing. `PARTIAL_FILL` handled on the trade (F3) but not as an order status. |
| 23-25 options engine, strategies, Greeks | done (Phase F + H) | Strike-selection pipeline with liquidity/OI/IV/delta/premium filters (H1); bull put, bear call, iron condor with max-loss sizing and group exits (H2); Greeks per leg and per structure on open positions. Future structures (straddle, strangle, ratio, calendar, butterfly, custom builder) not built. |
| 26-27 paper, live | done | Same pipeline; configurable slippage; execution delay/bid-ask simulation not modelled. |
| 28 kill switches, emergency exit | done | Global/tenant/strategy + emergency exit. |
| 29 SL/target engine | done (Phase J1) | Fixed levels, premium floor/ceiling (F4), trailing %, break-even at R, time exits, spread max risk/profit (H2). ATR-based and structure-based stops remain the strategy's job at signal time. |
| 30 TradingView webhook | done | |
| 31-32 backtest | done (Phase J2) | Engine + UI, exit rules, analytics views, Monte Carlo, walk-forward, run records. **Parameter optimisation** and historical option-chain backtests not built. |
| 33-36 fundamentals, news, AI analysis, scanner | done | AI analysis rule-based; LLM provider seam + generator with review gate added (Phase L). Scanner stays rule-based. |
| 37-42 UI, versions, notifications | done | Telegram + email + HMAC-signed webhook (Phase K4); **SMS/push channels missing** (no provider decision). |
| 43-46 schema, indexes, API, security | done | `/api/v1` canonical with `/api` alias. `billing_transactions` missing. |

## Part II - non-functional (Sections 47-61)

| Section | Status | Gaps |
| --- | --- | --- |
| 47 compliance | partial | Algo tagging, retention, exports, erasure done. Disclaimers on backtest/signal/score/AI screens (Phase G3); advisory-vs-execution classification is a business decision. |
| 48 security | mostly done | Rate limits, hash-chained audit, sessions/MFA, scanning, circuit breaker (G2), per-tenant envelope encryption with master rotation (N1), email verification (N3), least-privilege DB roles (Phase O1). **An external pentest remains.** |
| 49 reliability | mostly done | Structured logs, metrics, heartbeat, deep health, circuit breaker, written SLOs, staleness gate; chaos tests for Redis loss, broker socket errors, candle timeouts and outbox failure (Phase O4). Heartbeat alert wiring is the operator's (Prometheus rule given). |
| 50 testing | mostly done | Unit, golden path, fuzz, idempotency, disaster simulation, exit parity, chaos (O4), load/latency probe with baseline (O4). **Staging environment and a model-drift gate remain.** |
| 51 CI/CD | partial | Pipeline with scans done; feature flags with tenant allow-lists and the migration-hour guard (Phase N4). **Staging environment, IaC, blue-green remain infra tasks.** |
| 52 DR | mostly done | Daily verified backups (E3), WAL archiving + base backups + PITR restore script and per-data-class RPO/RTO (Phase O5), restart-reconcile-before-signals (G1). **Broker-side GTT backstop remains.** |
| 53 governance | partial | Retention, erasure, audit immutability done. AI/ML lineage only for what exists. |
| 54 performance | done (Phase O4) | SLO-1 thresholds enforced by `scripts/loadtest.py`; measured baseline in docs/PERFORMANCE.md (worst p95 150 ms at 20 users on a 2-vCPU sandbox). |
| 55 docs | done | Architecture/operations/README, OpenAPI, ten ADRs (`docs/adr/`) and the versioned DSL reference (Phase N5). |
| 56 conversational builder | done (Phase L) | LLM-backed generator (Anthropic/OpenAI/rule-based) emitting the DSL behind the backtest + approval gate, with lineage. |
| 57-61 global, multi-asset, brokers, performance engineering | partial | MCX/crypto contract specs and sizing; per-exchange sessions with own hours, cut-offs and holiday calendars (Phase O2). **Multi-currency P&L, FIU/TDS module and the hot-path split remain (business decisions on currencies and tax reporting first).** |

## Part III - V1-V4

| Item | Status | Gaps |
| --- | --- | --- |
| V1 acceptance (real Upstox account end to end) | **blocked on operator** | Needs credentials via Settings. |
| V2.1-2.6 options depth | partial | See §23-25. |
| V2.10 analytics views | done (Phase J2) | Monthly, day-of-week, hour-of-day, exit-reason, direction, holding, slippage, costs, streaks, ratios, drawdown curve. Strategy comparison = compare saved runs in the Backtest page's history. |
| V3.1-3.5 multi-account, routing, risk hierarchy | done (Phase I) | Labelled credentials give several accounts per broker; `broker_accounts` with sync/enable/default; deployments route to an account; `risk_limits` at six scopes with strictest-wins and `risk_events`. Broker-selection *rules* (capital/risk-based routing across brokers) not built - routing is explicit per deployment. |
| V3.6-3.8 plans, billing, metering | done (Phase K1) | Priced plans with feature flags, subscriptions with trial/grace lifecycle behind a `BillingProvider` (manual provider; a gateway plugs in at the same seam), invoices/payments, usage metering (orders, backtests, webhook events, API calls). Razorpay Subscriptions gateway (hosted checkout, autopay, signed idempotent webhooks) behind the seam; manual remains for bank transfers. |
| V3.9-3.12 marketplace, public API, developer portal | done (Phase K2-K3) | Listings freeze one version, need documented performance, are operator-reviewed; subscribing copies into the subscriber's strategies. Scoped, hashed, rate-limited API keys; `/api/public/v1/*`; `GET /docs` + `docs/PUBLIC_API.md`. Revenue share / creator payouts not built. |
| V3.13 notifications | done (Phase O3) | Telegram, email, webhook (K4), browser Web Push (VAPID, RFC 8291, no third-party service) and SMS through any HTTP gateway (MSG91/Twilio presets). |
| V3.14 rule 2 `get_balance/disconnect` | done (Phase G3) | `POST /api/broker/{name}/disconnect` revokes the session at the broker. |
| V4.1-4.3 AI agent, AI scanner, AI generator | done / partial (Phase L) | Monitoring agent with PROPOSED→APPROVED→EXECUTED/REJECTED/EXPIRED state machine and human approval (L4); LLM-backed generator behind backtest+approval gate with lineage (L2); provider seam Anthropic/OpenAI/rule-based with encrypted per-tenant keys (L1). AI scanner (V4.2) still rule-based. |
| V4.4-4.5 portfolio engine, 8-level risk hierarchy | done (Phase M2) | Portfolio engine (gross/net notional, concentration, unrealised, risk at stops) at `GET /api/portfolio/exposure`; eight scopes GLOBAL/TENANT/USER/ACCOUNT/PORTFOLIO/STRATEGY/DEPLOYMENT/INSTRUMENT with gross-exposure and symbol-concentration limits. |
| V4.6-4.8 quant, regime, advanced backtesting | done / partial | Regime engine + filter (L3); Monte Carlo/walk-forward/analytics (J); grid parameter optimisation with out-of-sample ranking and overfit gap (Phase M4). Factor/quant models not built. |
| V4.9 HA | partial | Health endpoints exist under `/api/system/...`; `/health/live|ready|dependencies` aliases and the broker-uncertain block done (Phase G). |
| V4.10 DR | partial | Restore-test script; RPO/RTO targets table in OPERATIONS; incident records with measured data-loss/downtime and audit range (Phase M4). PITR/WAL still an infra task. |
| V4.11 monitoring | partial | Trading metrics; severities INFO/WARNING/CRITICAL/EMERGENCY (Phase M3); AI/billing metrics not exported. |
| V4.12 security scopes | done (Phase N2) | Scope catalogue over roles, per-member deny/grant by the owner, enforced at the trading/team/LIVE/credential gates; `/api/auth/me` exposes effective scopes. |
| V4.13 enterprise admin | done (Phase M1) | Tenant/plan/status, kill switch, retention, exports, maintenance mode, broker disable, per-user trading disable, incidents. |
| V4.14 trade journal | done (Phase M3) | Strategy, deployment, execution quality, charges source, regime at entry, notes, tags. |
| V4.15 performance intelligence | done (Phase M3) | Analytics by strategy/symbol; live-vs-backtest degradation status per strategy (`GET /api/analytics/degradation`). |

## Consolidated safety rules - where each stands

1-6 (signal ≠ order, AI ≠ order, scanner ≠ order, backtest ≠ proof, every live order through risk, kill switch unbypassable): **done** by construction (one execution path, kill switches checked before risk).
7 stale/uncertain data fails safe: **done** (Phase G1 staleness gate on candles before signals and on quote age before exits).
8 broker-state uncertainty blocks new live orders until reconciled: **done** (Phase G1 tenant flag; worker reconciles every cycle while flagged).
9-10 duplicate protection, idempotency: **done**.
11 critical actions audited: **done**.
12 one DSL everywhere: **done** (rule engine shared; exit-parity test).
13 server-side tenant isolation: **done**.
14 no hard-coded secrets: **done** (fail-fast on defaults).
15 AI never holds broker credentials: **done** - providers receive prompt text only; the monitoring agent decides from records and executes through the ordinary services after human approval (Phase L).
16 human approval for AI live strategies: **done** (Phase L2) - a draft becomes a strategy only through a human approve call that requires an attached backtest; `custom_strategies.origin`/`ai_approved_by` record it.
17 partial fills explicit: **done** (F3).
18 restart reconciles with broker before new signals: **done** (Phase G1 `TradingWorker.reconcile_on_start` before the first cycle).
19 multi-level risk limits: **missing** (tenant level only).
20 no module silently changes another's risk config: **done**.

## Proposed next phases (recommendation order)

- **Phase G - safety and reliability closure** - **DONE** on this branch (ARCHITECTURE.md Phase G, docs/SLO.md). Was: market-data staleness gate before signals and exits; "broker uncertain" tenant flag set on a FAILED/timeout order that blocks new LIVE entries until reconciliation passes; reconciliation on worker start before the first cycle; broker-call circuit breaker (error-rate window -> pause submissions platform-wide, distinct from the kill switch); written SLOs with the metrics that measure them; `/health/live|ready|dependencies` aliases; `get_balance`/`disconnect` on BrokerInterface; disclaimers on backtest/AI/score screens.
- **Phase H - options depth** - **DONE** on this branch (ARCHITECTURE.md Phase H). Was: strike-selection filters from the option chain (liquidity, OI, IV, delta), multi-leg deployments (bull put, bear call, iron condor) with max-loss/max-profit/breakeven/margin sizing and group exits, Greeks per position.
- **Phase I - risk hierarchy and accounts** - **DONE** on this branch (ARCHITECTURE.md Phase I). Was: `risk_limits` with scopes and "strictest wins", `risk_events` append-only, multiple accounts per broker with routing rules.
- **Phase J - exits and backtesting depth** - **DONE** on this branch (ARCHITECTURE.md Phase J). Was: trailing/break-even/time exits; walk-forward, Monte Carlo, analytics views; backtest run records with engine/data versions.
- **Phase K - commercial SaaS** - **DONE** on this branch (ARCHITECTURE.md Phase K, docs/PUBLIC_API.md). Was: billing abstraction and metering, marketplace, public API + developer portal, webhook notifications. SMS/push still need a provider decision.
- **Phase O - reliability and delivery closure** - **DONE** (ARCHITECTURE.md Phase O): least-privilege DB roles, per-exchange sessions, browser push + SMS, chaos tests, load baseline, AI/billing metrics, PITR.
- **Phase N - security and platform hardening** - **DONE** (ARCHITECTURE.md Phase N): per-tenant envelope encryption + rotation tooling, fine-grained scopes, email verification + platform mailer, feature flags, migration-hour guard, ADRs, DSL reference.
- **Phase M - gap closure** - **DONE** (ARCHITECTURE.md Phase M): maintenance mode, broker disable, per-user trading disable, portfolio engine + PORTFOLIO/DEPLOYMENT scopes, trade journal, EMERGENCY severity, degradation baselines, incidents, `exit_position`, parameter optimisation.
- **Phase L - AI layer** - **DONE** on this branch (ARCHITECTURE.md Phase L). Was: LLM-backed strategy generator with the review gate, market regime engine, monitoring agent with the action-state machine. Provider keys are entered per tenant on the Settings page (encrypted); Anthropic, OpenAI or the built-in rule-based fallback.
- **Continuous**: the V1 exit gate - a real Upstox account run - as soon as credentials are entered in Settings.
