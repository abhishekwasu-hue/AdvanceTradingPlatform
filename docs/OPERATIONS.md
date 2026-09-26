# Operations: Disaster Recovery, Business Continuity & Data Governance

Master prompt Sections 52 (DR/BCP) and 53 (Data Governance). This document is written for the
Phase 1 scope (NSE F&O core, single-region deployment) and is honest about what already holds
today in code versus what is a target/runbook for whoever operates the first real deployment -
nothing here has been exercised against real production infrastructure, since none exists yet in
this environment. Where a claim is backed by an actual test or a real tool run, it says so; where
it's a target number or a procedure to follow once real infra exists, it says that instead.

## 1. Disaster Recovery / Business Continuity

### 1.1 Recovery objectives (targets, not yet measured against real infra)

| Scenario | RPO (max acceptable data loss) | RTO (max acceptable downtime) |
|---|---|---|
| Database crash / corruption | 5 minutes (one WAL-shipping interval) | 30 minutes |
| Application server crash | 0 (stateless - DB is the only state) | 2 minutes (restart / redeploy) |
| Full region outage | 15 minutes (last cross-region backup) | 4 hours (restore into a new region) |

These are targets for whoever stands up the first real production deployment to design backup
frequency and failover automation against - they are not currently enforced or measured by any
code or infrastructure in this repository, since no production deployment exists yet. Once a real
deployment exists, add automated, periodic restore-drills (Section 50's own "disaster-simulation
tests" category) that measure actual RPO/RTO against these targets rather than trusting them by
assumption.

### 1.2 Backups

- **What must be backed up**: the Postgres database (every table in `app/db/models.py` - this is
  the *only* stateful component; the FastAPI process itself is stateless and disposable).
- **Target mechanism**: continuous WAL archiving plus daily full snapshots (e.g. `pg_basebackup`
  or the managed equivalent of whichever hosting provider is used - AWS RDS/GCP Cloud SQL/etc. -
  all support this natively; prefer the managed offering over hand-rolled `pg_dump` cron jobs).
- **Verification, not just creation**: per Section 52's own requirement, a backup that has never
  been restored is unverified. Schedule a periodic (at minimum monthly) automated restore of the
  latest backup into a scratch instance, followed by a basic sanity check (row counts on
  `tenants`/`orders`/`trades` are non-zero and roughly match the source within the backup window,
  and `alembic check`/`verify_audit_chain` both pass against the restored copy). This is not yet
  automated anywhere in this repo - it is the first piece of real DR tooling to build once a real
  deployment exists to back up.
- **Encryption**: backups must be encrypted at rest using the hosting provider's standard
  encryption-at-rest offering (this is a hosting/infra configuration, not application code).

### 1.3 Crash-recovery runbook: open positions

The scenario Section 52 is most concerned with: the application crashes (or the DB connection is
lost) while a paper/live position is open. What already exists to make this recoverable, and what
an operator does when it happens:

1. **State survives a crash by construction.** Every open position is a row in `trades` with
   `exit_time IS NULL`; every order attempt is a row in `orders` with its full state-machine
   history in `order_events` (`app/execution/order_state_machine.py`). Nothing about an open
   position lives only in application memory - a crashed and restarted process can always
   reconstruct "what's currently open" with `SELECT * FROM trades WHERE exit_time IS NULL`, which
   is exactly what `app/reconciliation/engine.py` and `app/trading/persistence.py::
   build_trading_day_state` already do on every request rather than trusting an in-memory cache.
2. **On restart, before accepting new orders**: run `POST /api/reconciliation/{broker_name}` for
   every tenant with a live broker connected (see `app/reconciliation/` - already implemented and
   tested) to compare internally-recorded open positions against the broker's own position book.
   Any `MISSING_AT_BROKER`/`EXTRA_AT_BROKER`/quantity-mismatch result is logged to the audit trail
   and raised as a `SYSTEM_FAILURE` notification (already wired) - an operator resolves each one
   by hand (typically: trust the broker's book, since it is the source of truth for what actually
   filled) before resuming automated trading for that tenant.
3. **If the crash happened mid-order** (state stuck in `SUBMITTED`/`PENDING`, never reaching a
   terminal status): the order-state-machine's terminal states
   (`app/execution/order_state_machine.py::TERMINAL_STATUSES`) don't include these, so such an
   order is visibly "stuck" rather than silently lost - an operator's runbook step is to query for
   orders in a non-terminal state older than a few minutes and reconcile each one against the
   broker manually (for PAPER mode, mid-order states can simply be transitioned to `FAILED` with a
   detail note, since nothing external ever actually happened).
4. **Broker-side failsafe (built - Phase A4)**: an open LIVE position never depends solely on
   this platform staying up to be closed. Every live fill is immediately followed by a stop-loss
   market order (`SL-M`, opposite side, at the signal's stop - `BrokerInterface.place_stop_loss_
   order`), and its broker order id is stored as `trades.sl_order_id`. If the platform's process
   is down, the exchange still closes the position at the stop. If placing that stop fails the
   fill is kept (it is real) and a CRITICAL `SYSTEM_FAILURE` notification says so - the operator
   places a manual stop at the broker as backup. On exit, `app/trading/position_monitor.py`
   checks whether the stop already fired before placing anything else.
5. **Deployments after a restart**: nothing needs re-arming. `strategy_deployments` rows are
   durable; the worker picks every ACTIVE one up on its next cycle, and the position sweep covers
   every open trade for the tenant whether or not its deployment is still ACTIVE. A deployment the
   worker auto-PAUSED (5 consecutive failures) stays paused until someone resumes it from the
   Autopilot tab - deliberately, so a restart never silently re-enables something that was
   failing.

### 1.4 "Platform down during market hours" runbook

1. Check `GET /api/system/health` (API), `GET /api/system/worker-status` (trading worker
   heartbeat - `running=false` means no heartbeat for 3 cycles) and the process/container status
   first - most outages are a crashed process or a lost DB connection, not data loss. The API and
   the worker are separate processes: the console being down does not stop trading, and the
   worker being down does not stop the console (the Dashboard/Autopilot tab show a red banner).
2. If the app is down but the DB is healthy: redeploy/restart the stateless app tier. No data is
   at risk (state lives entirely in Postgres). Run the reconciliation step (1.3.2) before resuming
   automated order placement.
3. If the DB itself is down: fail over to a replica if one exists (see 1.5), or restore the latest
   verified backup (1.2) into a fresh instance if not. Every tenant with an open LIVE position
   during the outage window needs a manual broker-side check (their position is sitting on the
   broker's book regardless of whether this platform is reachable - the broker, not this
   platform, is the durable source of truth for a filled position).
4. Engage the GLOBAL kill switch (`POST /api/kill-switch/global/engage` - already implemented,
   Section 47/97 work) immediately on any outage that leaves order-placement state uncertain, so
   no tenant's automated strategy places a new order against a system that might replay or
   duplicate it, until reconciliation (1.3.2) confirms it's safe to resume.
5. Post-incident: write an incident note referencing the relevant `audit_logs` rows (their hash
   chain - see `app/audit/log.py` - makes the timeline itself tamper-evident) and the
   `order_events`/reconciliation-report rows covering the incident window.

### 1.5 Daily operating routine (autonomous trading)

Every trading morning, before 09:15 IST:

1. **Log in to the broker.** Broker tokens expire daily (Upstox 03:30 IST, Kite/Shoonya 06:00
   IST) with no refresh token. Settings -> Broker session health -> **Login to Upstox** completes
   the OAuth round-trip and stores the new token (encrypted); for other brokers paste the day's
   token and click Authenticate. The banner must read `VALID`. Until it does, LIVE deployments
   take no entries and PAPER ones have no candles - each deployment's row on the Autopilot tab
   says exactly that in its Note column, and a `TOKEN_EXPIRED` CRITICAL notification is raised
   once. Nothing needs to be resumed afterwards: deployments pick up on the next cycle.
2. **Check the worker.** Autopilot tab: "Trading worker: Running" and "Market: Open" once the
   session starts. A stale heartbeat during market hours is an incident (1.7).
3. **Check risk limits and kill switches** (Risk Management tab) - the worker enforces the
   tenant's saved limits on every entry, exactly like a manual paper execute.
4. **Have an alert channel configured** (Settings -> Alert delivery): Telegram and/or email, with
   a floor of WARNING or CRITICAL, and press "Send test" once. Every CRITICAL the worker raises is
   queued for delivery and sent by the worker's next cycle (retried with backoff up to 5 times;
   the outbox on the same card shows SENT/FAILED and the error). No channel means CRITICAL alerts
   are in-app only - i.e. invisible until someone opens the console.

During the session the worker takes no new entries after 15:00 IST and flattens every open
position at 15:15 IST (before brokers' own forced MIS square-off). After the session review
Positions/Orders/Notifications; a `SYSTEM_FAILURE` notification always needs a human look.

**First real run:** the Upstox integration has only been exercised against mocked HTTP. Run the
first real session as a PAPER deployment with real Upstox credentials entered in Settings, watch
it for a full day (candles arriving, signals evaluated, exits firing, square-off at 15:15), and
only then create a LIVE deployment - starting with the smallest lot the risk settings allow.

### 1.6 Team and access

* Registration creates an organisation (tenant) with the registering user as **Owner**. Owners
  invite teammates from the Team tab as Trader, Strategy creator or Viewer (read-only); the
  invite is a link valid for 48 hours, shown once, to be shared by the owner.
* **Two-factor authentication**: every platform administrator must enable it (Account tab) before
  the Admin Console or global kill switch will respond. Owners should turn on "Require two-factor
  authentication" on the Team tab so LIVE deployments and broker credentials always need a fresh
  authenticator check. Keep the backup codes somewhere safe; an owner with a lost phone and no
  backup codes needs a platform administrator to reset MFA in the database.
* **Contract notes**: after each LIVE trading day, download the broker's contract note /
  tradebook CSV and upload it on the Positions tab (Preview, then Apply). Trades then show the
  broker's actual charges (`actual`) instead of the platform estimate (`est.`), and the file, its
  SHA-256 and every matched leg are kept for audit. Unmatched legs are listed - a leg that should
  have matched usually means the order id column was not in the export.
* **Exchange algo id (SEBI)**: before an organisation trades LIVE, its broker registers the
  algo with the exchange and hands back an algo id. The owner enters it on the Team tab; from
  then on every entry, stop-loss and exit order is tagged `<algo id>-<strategy>-<leg>` at the
  broker and the tag is stored on the order. Set `ALGO_ID_REQUIRED_FOR_LIVE=true` on the API and
  worker once live trading is offered so an organisation cannot go LIVE without one.
* **Locked-out teammate**: they use "Forgot password?" (a link arrives if your organisation has
  an email alert channel), or an owner issues a one-hour reset link from the Team tab and hands
  it over on a trusted channel. Either way every existing session of theirs ends.
* Removing a member deactivates them immediately (their session stops working on the next
  request); their trades, orders and audit rows stay. A tenant always keeps one owner.
* Platform operators are `SUPER_ADMIN`: list their emails in `SUPER_ADMIN_EMAILS` (promoted at
  startup and on registration; never demoted automatically). They get the Admin Console:
  every tenant's plan/status (changes are audited on the tenant's trail and notified to it),
  the platform audit trail, what the worker is running, and the global kill switch. `SUPPORT`
  staff placed in a tenant can see everything and change nothing.
* **Suspending a tenant** (chargeback, abuse, KYC failure): Admin Console -> status ->
  suspended, with a reason. Effect on the next request/cycle: every write refused, no new
  entries; open positions keep being monitored and exit normally, and the tenant can still log
  in to see them. Reactivate the same way.

### 1.7 Trading worker runbook

* **Start / restart:** `docker compose up -d worker` (or `python -m app.workers.trading_worker`
  from `backend/` with the same `.env` as the API). It is safe to restart at any time: all state
  is in Postgres, positions keep their broker-side stops, and the next cycle resumes monitoring.
* **Heartbeat stale (`running=false`) during market hours:** restart the worker first, then check
  its logs (`docker compose logs worker`) - a cycle that crashes is logged with a full traceback
  and the worker loop itself survives it, so a *stopped* heartbeat normally means the container
  is gone, not a bug in a cycle. Open positions are still protected by their broker-side stops
  meanwhile; if the worker cannot be brought back before 15:15 IST, square off by hand at the
  broker or via Emergency Exit (Risk Management tab) with current prices.
* **`last_error` on the heartbeat / a deployment:** per-tenant and per-deployment failures never
  stop the cycle; they are recorded on the row the Autopilot tab shows. A deployment auto-pauses
  after 5 consecutive failures with a CRITICAL notification - fix the cause (usually an unknown
  symbol at the broker or an expired token) and Resume.
* **Replicas:** run exactly one worker unless Redis is reachable. The cycle lock
  (`atp:trading_worker:lock`) is a Redis `SET NX`; with Redis down it fails open so the single
  worker keeps going, which also means two workers without Redis would trade every deployment
  twice.
* **Holidays:** the worker treats weekends and the `market_holidays` table as closed. Add next
  year's NSE list (published each December) via `POST /api/market-holidays` as a SUPER_ADMIN
  before January, or the worker will try to trade on Republic Day.
* **Cadence:** `WORKER_CYCLE_SECONDS` (default 60, one base candle). Shorter mostly re-reads the
  60-second candle cache; longer delays exits.

### 1.8 Multi-AZ / high-availability readiness

Not implemented today (this is a Phase 1, single-region, single-instance deployment target) but
the application is already written not to block it later: the app tier is fully stateless (no
in-process session state beyond the per-process rate limiter noted in `app/core/rate_limit.py`,
which is explicitly documented there as needing a shared store like Redis before running more than
one instance), so horizontal scaling and multi-AZ app-tier deployment is an infrastructure change,
not an application rewrite. The trading worker is a single active instance by design (1.7); a
standby replica is safe only with Redis providing the cycle lock. The database is the one component that needs real multi-AZ
replication (a managed Postgres offering's standard multi-AZ/read-replica feature) before this
claim extends to the data layer too.

## 2. Data Governance

### 2.1 Data classification

| Class | Examples | Handling |
|---|---|---|
| **Secrets** | Broker API keys/secrets/access tokens, `JWT_SECRET_KEY`, `SECRETS_ENCRYPTION_KEY` | Never logged, never returned in any API response body. Broker credentials are Fernet-encrypted at rest (`app/secrets_store/encryption.py`) and only ever decrypted in memory for the duration of a broker call. |
| **PII** | User email, IP address (only ever held in-process for rate limiting, never persisted) | Email is the only PII persisted (`users.email`); no other personal identifiers (phone, PAN, address) are collected in Phase 1. |
| **Trading data** | Strategies, signals, orders, trades, positions | Tenant-isolated (every table carries `tenant_id`); this is the platform's core business data and the primary subject of the immutable-audit-trail requirement below. |
| **Public/reference** | Instrument contract specs, strategy definitions' non-secret fields | No special handling required. |

### 2.2 Immutable audit trail

Already real, not aspirational: `audit_logs` rows are hash-chained (`app/audit/log.py`,
`verify_audit_chain`) so tampering is detectable, and `order_events`/`signal_history` rows are
append-only by construction (nothing in the codebase ever `UPDATE`s or `DELETE`s a row in either
table - a state change is always a new row referencing the previous state, never an edit to it).
This satisfies Section 53's "audit_logs/order_events/signals never updated/deleted, only appended"
requirement for the tables that exist today.

**Handing records to an auditor or regulator.** Owners use the export card on System Logs
(their organisation), platform administrators the one on the Admin Console (platform-wide or one
tenant). Pick the date range, download CSV or JSON, and record the SHA-256 the UI shows (it is
also in the `X-Content-SHA256` header and the `export_generated` audit row). The audit-log export
includes every row's `prev_hash`/`hash` and the chain verdict at export time; the recipient can
recompute `sha256(prev_hash|tenant_id|user_id|event|detail|created_at)` per row to verify it
offline. Exports above 50,000 rows are truncated (the manifest says so) - narrow the range.

### 2.3 Retention and deletion (Phase D3 - in force)

Two regimes, both enforced by `app/retention/` and the trading worker:

| Data | Retention | Why |
| --- | --- | --- |
| Orders, order events, trades, signal history, strategy versions, audit logs | **Never deleted** by the platform | SEBI requires order-level trading records for at least five years; the audit trail is hash-chained |
| Users, tenants | Never deleted; personal data erasable | Trading records must stay attributed to a stable id |
| Login attempts (IP, user agent) | 365 days (`RETENTION_LOGIN_EVENTS_DAYS`) | Security forensics; personal data under the DPDP Act |
| Alert deliveries (sent/failed) | 90 days (`RETENTION_ALERT_DELIVERIES_DAYS`) | Operational |
| In-app notifications | 180 days (`RETENTION_NOTIFICATIONS_DAYS`) | Operational |
| Sessions | 30 days after expiry/revocation (`RETENTION_SESSIONS_DAYS`) | Security forensics |
| Password resets / invites | 7 / 30 days after expiry or use | Spent tokens |

The worker runs one retention batch per IST day while the market is closed and writes a
`retention_run` audit row with the counts; the Admin Console API (`GET /api/admin/retention`)
shows the policy, what is eligible right now and the last run, and `POST /api/admin/retention/run`
runs a batch on demand. Values under 7 days are raised to 7; `RETENTION_ENABLED=false` turns the
job off without removing the policy.

**Right to erasure (DPDP).** When a teammate leaves, the owner removes them (Team tab) and,
once any dispute window has passed, uses "Erase data": email, password and MFA are replaced with
inert values, every session ends, and the email on their login attempts is rewritten. Their
trades, orders and audit rows stay under the anonymous id `erased-<id>@erased.invalid`. Audit
rows written *before* the erasure that quote the email are left as they are - they are part of
the hash chain and cannot be edited - and this is the documented trade-off between erasure and an
immutable trail. Erasure is irreversible and audited (`user_erased`, by user id).

Still manual: whole-tenant offboarding (export the tenant's records first - section 2.2 - then
erase each member; the tenant row and its trading records remain for the retention period).

### 2.4 Data lineage for AI/ML outputs

The one AI-adjacent feature that exists today (`app/strategy_engine/nlu_parser.py`, the rule-based
Strategy Builder chat parser - see `docs/ARCHITECTURE.md`'s own note that this is a deterministic
parser, not an LLM) already carries lineage implicitly: every condition it produces reuses the
exact same `Condition`/`Operand` objects the manual Strategy Builder produces, with no separate
"AI-generated" data path to lose track of. Once a real LLM-based builder (master prompt Section 56)
is built, it must tag every strategy version it produces with `source: ai_chat` and record which
model/prompt version generated it, precisely so an operator can answer "which live strategies came
from an AI suggestion, and from which model version" after the fact - this is called out here as a
requirement for that future work, not something retrofitted onto the current regex parser (which
has no model version to record).

### 2.5 Backup encryption

Covered under 1.2 above - inherits whatever the DB's own backup-encryption story is
(cross-referenced here so this section is complete without duplicating it).
