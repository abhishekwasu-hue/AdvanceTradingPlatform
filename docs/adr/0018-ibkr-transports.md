# ADR-0018: Interactive Brokers (IBKR) behind `BrokerInterface`, with two transports

**Date:** 2026-10-11 · **Status:** provisional · **Parts:** E, D, B (MASTER SPEC v1.2 §2, §1f; v1.3)

## Context
v1.2 §2 makes IBKR the first part of global markets Phase 2: US equities and ETFs, US index options (multi-leg through
IBKR combo orders), futures and FX.

IBKR offers two ways in:
- **TWS API.** A socket protocol to a running Trader Workstation or IB Gateway process that is logged in as one IBKR
  user. The Python client is `ib_async`, the maintained fork of `ib_insync`. It is mature and complete, but it is one
  login per Gateway process. IBKR forces a daily restart, and a periodic second-factor login.
- **Web API (Client Portal / OAuth).** REST plus WebSocket. Third parties use OAuth, which needs vendor registration
  with IBKR (an entity and a compliance review). This is the only shape that fits many tenants, each with their own
  IBKR account.

Fixed constraints:
- ADR-0002: one broker interface.
- ADR-0004: exits are never blocked.
- ADR-0006: the AI places no orders.
- ADR-0008: per-tenant secrets are envelope-encrypted.
- ADR-0016: provider seams with capabilities and contract tests.
- ADR-0017: global-first identity, time, money, costs and rules.

Delivery order (v1.2/v1.3): Indian PAPER go-live first, crypto Phase 1 second, IBKR after both. This ADR is the design
step; no IBKR code lands before that order allows it.

## Decision (provisional)
1. **One adapter, a transport seam.** `app/brokers/ibkr/broker.py::IBKRBroker(BrokerInterface)` holds the platform
   logic: mapping, order rules, the jurisdiction check and the event to state-machine bridge. It talks only to an
   `IBKRTransport` protocol (`app/brokers/ibkr/transport.py`). The protocol covers:
   - connect and disconnect, plus health;
   - contract details;
   - quotes and history;
   - place, modify and cancel;
   - open orders and executions;
   - positions and account values;
   - an async event stream for order status, executions and commission reports.

   Three implementations:
   - **`TwsTransport`** (`ib_async`, through IB Gateway). Built first, because it works without vendor registration.
   - **`WebApiTransport`** (REST/WS plus OAuth 2.0; tokens per tenant, encrypted per ADR-0008). Only the interface and
     a stub land now. The stub raises `TransportUnavailable("IBKR Web API needs vendor registration")`. The real
     implementation follows registration (OPEN_QUESTIONS IB-1).
   - **`FakeTransport`** (tests). It replays recorded payloads and drives the shared venue contract suite.

   The transport is chosen per broker account (`transport: tws | webapi`). It is never inferred.
2. **IB Gateway as an optional service.** Compose service `ib-gateway` under profile `ibkr`, so it is off unless
   started.
   - **Mode.** `TRADING_MODE=paper` by default. Live mode is a separate, explicit host setting, off by default.
   - **Network.** Bound to the internal network or 127.0.0.1 only.
   - **Credentials.** Only in the host `.env`, as operator secrets. They are never stored in the database and never
     sent through chat.
   - **Image.** Pinned by digest after a review (IB-2).
   - **Tenancy.** A Gateway is one IBKR login, so the TWS transport serves the operator's own account(s) only. Tenants
     with their own IBKR accounts wait for the Web API.
3. **Daily re-login and disconnects are routine, not errors.**
   - **The Gateway's scheduled restart.** A configured time outside the venue session, from data.
   - **While disconnected.**
     - The account is broker-uncertain (Phase G1): new entries are blocked with a reason.
     - Exits are queued with retries and an EMERGENCY alert. They are never dropped and never silently cancelled
       (ADR-0004).
   - **Reconnect.** Backoff with jitter.
   - **After a reconnect.** Reconciliation runs per account (open orders, executions, positions; Phase AL) before the
     uncertain flag clears.
   - **Second factor.** A weekly second-factor prompt is an operator task. `docs/BROKERS_IBKR.md` has the runbook, and
     the health check says "waiting for login" rather than "down".
4. **Capabilities, declared rather than discovered by failure** (ADR-0016). They extend `BrokerCapabilities`:
   - asset classes: `STK`, `ETF`, `OPT`, `FUT`, `CASH` (FX);
   - order types: LIMIT, STOP, STOP-LIMIT and combo (`BAG` with leg conIds, ratios and actions);
   - native bracket and OCO;
   - fractional quantity per contract;
   - `market_orders_default = False`.

   Order rules:
   - **No MARKET order unless the request names it**, and then only with the platform's price protection.
   - Outside-regular-hours trading is an explicit per-order flag, off by default.
   - A combo is one order for one spread.
   - **Legging is not done**: if the combo is refused, the spread is refused, with the reason.
5. **Orders keep the platform's rules.**
   - Idempotency: the platform client order id goes in IBKR's `orderRef`.
   - The existing order state machine is used (Phase 96).
   - `ib_async` events (`orderStatusEvent`, `execDetailsEvent`, `commissionReportEvent`) feed the E3 order-update
     stream.
   - Partial fills follow P0.5.
   - Every order carries the platform's algo tag where the venue has a field for it.
6. **Instruments and sessions as data** (ADR-0013 and ADR-0017).
   - **Instrument identity.**
     - Venue listing: (MIC, symbol), with `conId` as the IBKR key.
     - ISIN and FIGI are kept when known.
     - Specs are versioned as-of: multiplier, minimum tick through IBKR market rules, trading class, currency,
       primary exchange, expiry and strike.
   - **Calendars.** NYSE/Nasdaq and CME come from a calendar data source with half days and DST, stored per venue in
     UTC.
     - Bars are truncated at an early close.
     - A holiday gives no bars and is not a gap.
   - **Money.** USD is the quote currency. Reporting currency comes through the FX table (P3).
   - **P&L.** Kept in the instrument currency and the reporting currency, with the FX rate and its time stored with
     the trade.
7. **Costs per venue.** The `CostModel` registry gains `ibkr_us_fixed` and `ibkr_us_tiered`.
   - It covers commissions, exchange and clearing fees, regulatory fees and per-contract option fees.
   - Every value lives in a data file with its source and the date it was checked. None is hardcoded in code.
   - `india_costs.py` is not touched.
   - The plan in force is per account (IB-5).
8. **Jurisdiction engine** (v1.2 §1f, part D `app/compliance/rules.py`). Rule-sets are data files, chosen by the
   tenant's residence, and the engine runs before sizing.
   - **India resident.**
     - Foreign cash equity and ETFs are allowed, with an LRS note in the UI and reports.
     - Foreign derivatives and margin are **blocked with the reason** (rule text and source in the file; IB-4).
   - **US resident.** A pattern-day-trader check on margin accounts: day-trade count over the rolling window against
     the equity threshold, both parameters in the file.
   - **Every block names its rule.**
   - **Exits are never blocked by a jurisdiction rule** (ADR-0004).
9. **AI.** Same as every venue (ADR-0006). The AI can draft and explain, and never places, modifies or cancels.

## Delivery (after Indian PAPER go-live and crypto Phase 1; each PR a draft, merge only by the owner)
| PR | Scope |
|---|---|
| I1 | This ADR, `docs/BROKERS_IBKR.md`, OPEN_QUESTIONS IB-1..IB-5 (design only) |
| I2 | `IBKRTransport` protocol, `FakeTransport`, `IBKRBroker` mapping and capabilities, shared venue contract suite, `WebApiTransport` stub |
| I3 | `TwsTransport` (`ib_async`), compose profile `ibkr` (paper), reconnect/uncertain/reconciliation, read-only smoke test (Phase AJ steps: authenticate, profile, positions, quote, history; no orders) |
| I4 | Instrument master rows (conId/ISIN/FIGI, specs as-of), NYSE/CME calendars, FX P&L, `ibkr_us_*` cost models |
| I5 | Jurisdiction rule-sets (IN-resident, US-resident PDT) and their tests |
| I6 | Combo orders for US index option spreads |
| I7 | `WebApiTransport` once vendor registration is done (IB-1) |

## Tests (no live keys in CI)
- The shared venue contract suite on `FakeTransport`: the same cases every adapter passes.
- Mapping and capability tests, including no MARKET by default and a combo refused as a whole.
- Disconnect, uncertain and reconcile cases: entries blocked, exits queued and retried.
- Jurisdiction cases from the rule files: allowed, blocked with reason, PDT counter at the threshold, exit never
  blocked.
- FX P&L.
- Calendars: holiday, early close truncation, DST weeks.
- The read-only smoke test runs only by hand against a paper account. It is never in CI.

## Consequences
- **One code path for the platform logic.** Adding the Web API later changes the transport only.
- **Tenancy.** Operator-only through TWS until vendor registration; the UI says so for tenants.
- **Operations.** The Gateway brings daily re-login work, and its runbook is part of I3.
- **Global plumbing** (identity, calendars, FX, costs, rules) built here is reused by later venues.
