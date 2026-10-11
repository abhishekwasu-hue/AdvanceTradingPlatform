From Abhi - detail of MASTER SPEC v1.2 §2: IBKR adapter (the first part of global markets Phase 2; crypto Phase 1 unchanged).

1. `IBKRBroker` on BrokerInterface, behind a seam with two transports:
   - (a) TWS API via `ib_async` + IB Gateway: an optional Docker service, paper mode by default, credentials only in
     .env, daily re-login handling;
   - (b) Web API (REST/WS, OAuth 2.0, per-tenant tokens encrypted): interface + stub now, implementation after vendor
     registration.
2. Capabilities:
   - US equities/ETFs;
   - US index options (multi-leg spreads via IBKR combo orders);
   - futures;
   - FX.

   Supporting pieces:
   - a global instrument master (ISIN/FIGI, conId);
   - contract specs;
   - exchange calendars/sessions (NYSE/CME);
   - USD base + FX conversion (P3);
   - a CostModel per venue (commissions/fees; india_costs stays separate).
3. Jurisdiction engine (v1.2 §1f), config-driven:
   - India resident: foreign cash equity/ETF allowed (LRS note); foreign derivatives/margin blocked, with a reason;
   - US resident: PDT rule check.
4. Orders:
   - LIMIT / STOP / STOP-LIMIT / combo; no MARKET by default;
   - the existing idempotency and order state machine;
   - order-update stream via ib_async events (E3);
   - reconciliation per account (AL).
5. Tests:
   - mocked transport (contract tests shared with other adapters);
   - a read-only smoke test (Phase AJ steps);
   - jurisdiction tests;
   - FX P&L tests;
   - truncation/holiday calendar tests.

   No live keys in CI.
6. Docs:
   - docs/BROKERS_IBKR.md: paper account setup, Gateway Docker, daily login, Web API vendor onboarding checklist;
   - OPEN_QUESTIONS: vendor-registration items (entity/compliance) - Abhi.

Order: after the Indian PAPER go-live and crypto Phase 1; the design PR comes first (ADR-0018 IBKR transports). No
merge, no LIVE.
