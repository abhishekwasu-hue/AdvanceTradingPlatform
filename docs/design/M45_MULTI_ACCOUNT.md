# M45 multi-broker accounts (Primary / Secondary): review vs the order path + design note (MB-9) - for the owner's ✅

Independent review (owner order 26 Oct, review/design session) of `docs/owner-orders/ATP_ORDER_M45_MULTI_BROKER.md`
(MB-0..MB-10) against `origin/main` (26 Oct, after #550). M45 touches the order path (CLAUDE.md §8): nothing is built
before the owner's ✅ on this note. No code change here; no server addresses or credentials.

## 1. What already exists (reuse, do not rebuild)

| Need | Exists | Where |
|---|---|---|
| Several accounts per broker, encrypted | Phase I2: one credential row per (tenant, broker, `account_label`); encrypted payload; token status / expiry / last verified per row | `db/models.py` `BrokerCredentialRecord` l.262-287, `brokers/routes.py` `save_credentials`, `brokers/token_lifecycle.py` |
| Account registry | `broker_accounts`: label, broker id, display name, ACTIVE / DISABLED, `is_default` (per broker), synced balance / margin / P&L | `BrokerAccountRecord` l.1062-1088, `accounts/service.py`, `accounts/routes.py` |
| Position knows its account | `trades.broker_account_id` (Phase T) | l.351 |
| Exit goes to the position's account | the exit adapter is resolved from the trade's own `broker_account_id`, one adapter per credential per cycle | `trading/position_monitor.py` ~l.200-230 |
| Per-account limits | rate budget + SEBI OPS throttle keyed `(tenant, broker@label)` | `workers/trading_worker.py` l.2043-2064, `brokers/rate_budget.py` |
| Per-account reconcile | `reconcile_accounts` (one savepoint per account, ADR-0004) + TM2-4 tag lookup at the broker | `reconciliation/service.py` l.275, `brokers/base.py` l.179 |
| Account choice at signal time | Phase T routing: EXPLICIT / MOST_MARGIN / LEAST_UTILISED / FEWEST_POSITIONS, stale balances never decide, `last_route` recorded | `accounts/routing.py` |
| Broker health | circuit breaker per broker: refuses new LIVE entries only; exits, cancels, stops always attempted | `brokers/circuit_breaker.py` l.5-16 |

**Adapters (MB-9 / MB-10 report).** Real adapters: Upstox, Zerodha, Dhan, Angel One, Fyers, Kotak Neo, Shoonya, Groww,
CoinDCX. Every adapter is built per credential row (`token_lifecycle.build_adapter(record)`), so in code **all of them
can hold several simultaneous connections**, also mixed brokers. What code cannot settle: each broker's own terms
(one API app logging in several client ids, static-IP registration per app, daily re-login per account). ATP uses its
own Upstox app (CLAUDE.md §4): whether that app may log in several of the owner's client ids must be checked with
Upstox before LIVE; the same for every broker (a lawyer + broker check, MB-0). PAPER is unaffected.

## 2. Gaps and risks found

| # | Finding | Risk | Design answer |
|---|---|---|---|
| G1 | Ownership is the organisation, not the user: credentials "belong to the org" (model docstring); routing candidates are the tenant's accounts; a member can use a connection another member linked (`brokers/routes.py` l.300). | MB-0 (same user's own accounts only) is not enforced: a team member's account could become a Secondary. | Registry, matrix and fan-out read only accounts whose `user_id` = the user; existing org connections stay as today for single-account use. |
| G2 | Routing is one-of-N (`choose_account` picks ONE account). M45 is one-to-N (Primary + groups). | Bolting fan-out into the router would touch every gate. | New fan-out layer ABOVE `OrderRouter`: it resolves the account list, then sends one child order per account through the existing single-account path (all gates, kill switch, risk, OPS per account unchanged). Each child = its own `TradeRecord` with its `broker_account_id`. |
| G3 | **Exit fallback hole**: a LIVE trade with no `broker_account_id`, no deployment broker and more than one credential gets NO exit adapter (`records[0] if len(records) == 1 else None`). | With several accounts this is an exit not sent - against ADR-0004. Rare today (one account), real under M45. | With `multi_account` ON: every LIVE child order records its account before sending (refused otherwise); the flag cannot be turned on for a user who still has an open LIVE trade without an account (listed for him). Never a guessed account. |
| G4 | Primary is `is_default` per BROKER; MB-1 wants exactly one Primary per USER across brokers + Secondary groups + Enabled. | Two notions of "default" disagree. | New fields: `role` (PRIMARY / SECONDARY), `group_name`, `enabled`; `is_default` kept for single-account callers. Migration yes (after 15:30 IST once practice starts). |
| G5 | PAPER has ONE account per user (`paper_accounts.user_id` unique). MB-7: paper behaves identically with simulated accounts. | No paper / live split rule (CLAUDE.md §4) breaks if paper has no accounts. | Simulated accounts = `broker_accounts` rows with broker `paper` and their own label / capital; the existing paper account becomes the Primary simulated one. Migration yes. |
| G6 | No manual LIVE ticket today: LIVE entries come only from deployments; the Terminal / F&O ticket is PAPER (`paper/fno_routes.py`). | MB-3's per-order override has no LIVE ticket to live in yet. | The override ships on the PAPER ticket (simulated accounts) and the deployment form; the LIVE ticket inherits it when it exists. |
| G7 | No per-adapter order-type capability list (MB-10: "skip the account if the order type is not supported"). | A silent substitution or a broker reject at send time. | A static capability table per adapter (order types, products, AMO, SL-M, iceberg) checked in MB-6 pre-send; unsupported = skip + alert. |
| G8 | Circuit breaker is per broker for all tenants (health), not per account. | Fine: it reflects broker health and never blocks exits; one account's 4xx does not trip it. | Keep. Per-account failures (token, margin, reject) go through MB-5 / MB-6 alerts, not the breaker. |
| G9 | "Secondaries only after Primary accepted" (default ON for entries) delays Secondaries by one round-trip. | Price moves between Primary and Secondary fills. | Shown on the result rows (time per account); the setting stays the owner's default; exits always in parallel. |
| G10 | Combined / strategy exits (CX / SX) are being specified in their own order (not touched here). | MB-4 needs per-account legs. | Interface only: a combined exit resolves positions per account and sends one exit per account (shorts first inside each); CX / SX keep their one overall rule. |

## 3. Design (build follows this after ✅)

- **Data**: `broker_accounts` + `role`, `group_name`, `enabled`, owner `user_id` required for M45 rows; simulated paper
  accounts; settings per user: sizing mode per account (Fixed lots / Multiplier x Primary / % of capital, default
  Multiplier 1), routing matrix (order class x Primary / groups), "Secondaries after Primary" (entries ON, exits OFF).
- **Resolver** (pure function, no I/O): order class + matrix + per-order override -> account list; exits ignore the
  matrix and return every account holding the position (MB-4).
- **Sizing** (pure): lots per account, round down to the lot multiple, < 1 lot -> skip with reason; freeze-limit
  slicing per account via the existing smart-execution slicer.
- **Fan-out executor**: per account pre-send checks (MB-6: token valid, margin, lot / freeze, segment, capability G7),
  then one child order through the existing path with its own client order id (tag carries the account); result row
  per account; no rollback; failure -> critical alert (in-app + Telegram) with one-tap retry; mismatch flag on the
  Dashboard from the existing per-account reconcile.
- **Views**: Positions / Orders / P&L tabs "All accounts" + per account, account badge, Primary / Secondary badge.
- **Audit**: one log row per routing decision (what, which accounts, why) and per result; no secrets.
- Flag `multi_account` (OFF, behind `ui_v2`); PAPER first; LIVE only on the owner's words; AI never places orders.

PR plan (one `claude/` branch each, draft): **MA-1** data + registry page (migration) -> **MA-2** sizing + resolver
(pure, tests: matrix, rounding / skip) -> **MA-3** fan-out executor on PAPER simulated accounts (tests: partial failure,
token expiry, one account failing on exit) -> **MA-4** exits follow positions + G3 guard (tests across brokers) ->
**MA-5** checks, alerts, retry, capability table -> **MA-6** views + ticket "Run on" list + screenshots.

## 4. Owner decisions (✅ 26 Oct: defaults accepted, DECISIONS)

| # | Question | Default |
|---|---|---|
| M45-1 | One Primary per user across ALL brokers (MB-10), the per-broker default kept only for old single-account callers? | Yes. |
| M45-2 | When a deployment uses the matrix, do the Phase T policies (MOST_MARGIN ...) still apply? | No: matrix replaces the policy for that deployment; single-account deployments keep the policies. |
| M45-3 | G3: block turning `multi_account` ON while an open LIVE trade has no recorded account? | Yes, listed for the user; exits are never blocked, entries wait. |
