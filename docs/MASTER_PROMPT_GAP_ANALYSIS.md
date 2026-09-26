# Master prompt (Sep 2026 revision) vs the codebase - gap analysis

The revised master prompt adds Part III (V1-V4 release phasing, V3.14 and V4 now in full), a
consolidated safety-rule list and a per-module Definition of Done. This file maps every part of
it onto what is built on `main` + Phase F, so the next phases are chosen against facts. Status
words: **done** (built and tested), **partial** (built, with named gaps), **missing**.

## Part I - product spec (Sections 1-46)

| Section | Status | Notes |
| --- | --- | --- |
| 1-7 objective, stack, pipeline, monolith, tenancy, RBAC, auth | done | React/Vite instead of Next.js; int ids instead of uuid. Roles: SUPER_ADMIN/OWNER/USER/STRATEGY_CREATOR/SUPPORT/VIEWER. Email verification missing. |
| 8-9 BrokerInterface, token security | partial | Upstox, Zerodha, Shoonya real; stubs for others. `get_balance()` / `disconnect()` / `exit_position()` / `subscribe_market_data()` from V3.14 and §8 not on the interface. Tokens Fernet-encrypted with one app key, not envelope-encrypted per tenant (§48). |
| 10 market data | partial | Candles/LTP via broker REST with Redis cache; **no staleness detection** (`data_age > threshold -> block`), no websocket feed. |
| 11 instrument master | done (Phase F1) | NSE equity/index/F&O; MCX/crypto specs are a static registry, not master rows. `active` flag and ISIN not stored. |
| 12-14 indicators, DSL, visual builder | done | Rule-based DSL and builder; DSL schema not formally versioned (§55). |
| 15-22 signal, risk, sizing, order, idempotency, position, reconciliation | done / partial | Order state machine, idempotency, reconciliation exist. Risk checks missing from §17: **data fresh**, **broker connected** (only token validity), **margin available** for buys, **instrument/expiry validity**. `PARTIAL_FILL` handled on the trade (F3) but not as an order status. |
| 23-25 options engine, strategies, Greeks | partial | Single-leg options (Phase F). **Strike-selection pipeline filters (liquidity, OI, IV, delta) missing**; **multi-leg strategies (bull put, bear call, iron condor) missing**; Greeks engine exists per leg. |
| 26-27 paper, live | done | Same pipeline; configurable slippage; execution delay/bid-ask simulation not modelled. |
| 28 kill switches, emergency exit | done | Global/tenant/strategy + emergency exit. |
| 29 SL/target engine | partial | Fixed levels from the strategy, premium floor/ceiling (F4). **Trailing SL, break-even, time-based exit, ATR-based SL missing.** |
| 30 TradingView webhook | done | |
| 31-32 backtest | partial | Engine + UI; **no walk-forward, Monte Carlo, parameter optimisation, or V2.10 analytics views**. |
| 33-36 fundamentals, news, AI analysis, scanner | done | AI analysis is rule-based, no LLM. |
| 37-42 UI, versions, notifications | done | Telegram + email; **SMS/push/webhook channels missing**. |
| 43-46 schema, indexes, API, security | done | `/api/v1` canonical with `/api` alias. `billing_transactions` missing. |

## Part II - non-functional (Sections 47-61)

| Section | Status | Gaps |
| --- | --- | --- |
| 47 compliance | partial | Algo tagging, retention, exports, erasure done. **Disclaimers on backtest/AI/score screens missing**; advisory-vs-execution classification is a business decision. |
| 48 security | partial | Rate limits, hash-chained audit, sessions/MFA, scanning done. **Secret manager + per-tenant envelope encryption, least-privilege DB roles, pentest, broker-call circuit breakers missing.** |
| 49 reliability | partial | Structured logs, metrics, heartbeat, deep health done. **Circuit breaker on broker error rate, written SLOs, chaos tests, staleness alert missing.** Heartbeat alert wiring is the operator's (Prometheus rule given). |
| 50 testing | partial | Unit, golden path, fuzz, idempotency, disaster simulation, exit parity done. **Load/latency tests, full backtest-vs-paper parity run, staging env, model-drift gate missing.** |
| 51 CI/CD | partial | Pipeline with scans done. **Staging environment, IaC, feature flags, blue-green, migration hour guard missing.** |
| 52 DR | partial | Daily verified backups (E3), runbooks done. **PITR/WAL, broker-side GTT backstop, numeric RPO/RTO per data class, restart-reconcile-before-signals missing.** |
| 53 governance | partial | Retention, erasure, audit immutability done. AI/ML lineage only for what exists. |
| 54 performance | missing | No stated SLO numbers or load measurements. |
| 55 docs | partial | Architecture/operations/README, OpenAPI. **ADRs and a versioned DSL reference missing.** |
| 56 conversational builder | partial | Rule-based chat-to-strategy exists; **no LLM, no Dynamic Condition Type Pipeline / review gate**. |
| 57-61 global, multi-asset, brokers, performance engineering | partial | MCX/crypto contract specs and sizing; **no multi-currency, per-exchange sessions, FIU/TDS module, hot-path split**. |

## Part III - V1-V4

| Item | Status | Gaps |
| --- | --- | --- |
| V1 acceptance (real Upstox account end to end) | **blocked on operator** | Needs credentials via Settings. |
| V2.1-2.6 options depth | partial | See §23-25. |
| V2.10 analytics views | missing | Strategy comparison, monthly heatmap, time-of-day, exit and slippage analysis (slippage now recorded per trade). |
| V3.1-3.5 multi-account, routing, risk hierarchy | missing | One credential per broker per tenant; risk limits tenant-wide only. |
| V3.6-3.8 plans, billing, metering | partial | Plans and limits done; **billing provider abstraction and usage metering missing**. |
| V3.9-3.12 marketplace, public API, developer portal | missing | |
| V3.13 notifications | partial | |
| V3.14 rule 2 `get_balance/disconnect` | missing | |
| V4.1-4.3 AI agent, AI scanner, AI generator | missing / partial | Rule-based scanner and builder exist; no LLM, no action-state machine, no approval gate. |
| V4.4-4.5 portfolio engine, 8-level risk hierarchy | missing | Greeks per leg exist; no aggregation or hierarchy. |
| V4.6-4.8 quant, regime, advanced backtesting | missing | |
| V4.9 HA | partial | Health endpoints exist under `/api/system/...`; `/health/live|ready|dependencies` aliases and failure rules (broker uncertain -> block new live orders) missing. |
| V4.10 DR | partial | Restore-test sequence exists as script; RPO/RTO per tier, incident record schema missing. |
| V4.11 monitoring | partial | Trading metrics exist; AI/billing domains n/a; severities INFO/WARNING/CRITICAL (no EMERGENCY). |
| V4.12 security scopes | missing | Roles, not fine-grained scopes. |
| V4.13 enterprise admin | partial | Tenant/plan/status, kill switch, retention, exports; **maintenance mode, broker disable, per-user trading disable missing**. |
| V4.14 trade journal | partial | Trades carry strategy, deployment, execution quality (F3), charges source; **market/regime context, notes, tags missing**. |
| V4.15 performance intelligence | partial | Analytics by strategy/symbol; **degradation baselines missing**. |

## Consolidated safety rules - where each stands

1-6 (signal ≠ order, AI ≠ order, scanner ≠ order, backtest ≠ proof, every live order through risk, kill switch unbypassable): **done** by construction (one execution path, kill switches checked before risk).
7 stale/uncertain data fails safe: **missing** (no candle/LTP age check before a signal).
8 broker-state uncertainty blocks new live orders until reconciled: **missing** (a FAILED order raises CRITICAL but does not block the tenant's next LIVE entry).
9-10 duplicate protection, idempotency: **done**.
11 critical actions audited: **done**.
12 one DSL everywhere: **done** (rule engine shared; exit-parity test).
13 server-side tenant isolation: **done**.
14 no hard-coded secrets: **done** (fail-fast on defaults).
15 AI never holds broker credentials: **done** (no AI execution path exists).
16 human approval for AI live strategies: n/a until an LLM builder exists.
17 partial fills explicit: **done** (F3).
18 restart reconciles with broker before new signals: **partial** (reconciliation engine exists, not run automatically on worker start).
19 multi-level risk limits: **missing** (tenant level only).
20 no module silently changes another's risk config: **done**.

## Proposed next phases (recommendation order)

- **Phase G - safety and reliability closure** (small, high value, closes rules 7, 8, 18 and §17/§49 gaps): market-data staleness gate before signals and exits; "broker uncertain" tenant flag set on a FAILED/timeout order that blocks new LIVE entries until reconciliation passes; reconciliation on worker start before the first cycle; broker-call circuit breaker (error-rate window -> pause submissions platform-wide, distinct from the kill switch); written SLOs with the metrics that measure them; `/health/live|ready|dependencies` aliases; `get_balance`/`disconnect` on BrokerInterface; disclaimers on backtest/AI/score screens.
- **Phase H - options depth** (§23-25, V2.1-2.6): strike-selection filters from the option chain (liquidity, OI, IV, delta), multi-leg deployments (bull put, bear call, iron condor) with max-loss/max-profit/breakeven/margin sizing and per-leg exits, Greeks per position.
- **Phase I - risk hierarchy and accounts** (V3.1-3.5, V4.5): `risk_limits` with scopes and "strictest wins", `risk_events` append-only, multiple accounts per broker with routing rules.
- **Phase J - exits and backtesting depth** (§29, §31, V2.10, V4.8): trailing/break-even/time exits; walk-forward, Monte Carlo, analytics views; backtest run records with engine/data versions.
- **Phase K - commercial SaaS** (V3.6-3.13): billing abstraction and metering, marketplace, public API + developer portal, SMS/push/webhook notifications.
- **Phase L - AI layer** (§56, V4.1-4.3, V4.7): LLM-backed strategy generator with the review gate, market regime engine, monitoring agent with the action-state machine (needs a model provider decision and API key handling).
- **Continuous**: the V1 exit gate - a real Upstox account run - as soon as credentials are entered in Settings.
