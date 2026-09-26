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

### 1.2 Backups (Phase E3 - in force)

- **What is backed up**: the Postgres database - the only stateful component (every table in
  `app/db/models.py`; the API and worker are stateless). Redis holds only caches and the worker
  lock and is not backed up.
- **How**: the compose `backup` service (`scripts/backup/run_scheduled.sh`) takes a
  `pg_dump --format=custom` of the whole database every `BACKUP_INTERVAL_SECONDS` (daily) into the
  `backups` volume, writes a SHA-256 sidecar and a `latest` pointer, and updates `LAST_BACKUP_OK`.
  With `BACKUP_ENCRYPTION_PASSPHRASE` set every dump is AES-256 encrypted (`openssl enc -pbkdf2`);
  keep that passphrase somewhere other than the server. Retention keeps `BACKUP_RETENTION_DAYS`
  (14) of files and never fewer than `BACKUP_KEEP_MIN` (7). Copy the volume off the host
  (object storage, another machine) - a backup on the same disk as the database is not a backup.
- **Monitoring**: alert when `LAST_BACKUP_OK` is older than 26 hours (the script never touches it
  on failure); the service logs `backup FAILED` and retries at the next interval.
- **Verification, not just creation**: `scripts/backup/verify_backup.sh [file|latest]` restores
  the backup into a fresh scratch database, checks `alembic_version` matches the source, that
  `tenants`/`orders`/`trades`/`audit_logs` counts are within the source's, re-verifies the audit
  hash chain in the copy (`python -m app.audit.verify_chain`), drops the scratch database and
  prints a one-line JSON report with `status: ok`. Run it monthly and keep the report:
  `docker compose run --rm backup sh /scripts/verify_backup.sh latest`. CI runs the same
  rehearsal on every push (`tests/test_backup_scripts.py`), including the encrypted and tampered
  cases.
- **Restore for real**: `scripts/backup/restore.sh <file|latest>` (asks you to type the database
  name; `RESTORE_CONFIRM=yes` for scripts) replaces the configured database with the backup, then
  start the API (`alembic upgrade head` runs on start and is a no-op for a same-version dump) and
  follow 1.3 for open positions. Point-in-time recovery between daily dumps is not provided: for
  that, add WAL archiving or use a managed Postgres with PITR, and keep these dumps as the
  provider-independent copy.

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
1a. **Instrument master.** The worker downloads Upstox's public instrument master once a day from
   08:00 IST (`INSTRUMENT_SYNC_EXCHANGES`, default NSE). `GET /api/instrument-master/status`
   shows what is loaded and when; if it is stale on an F&O trading day (a download failure is on
   the worker's cycle report), a platform administrator runs `POST /api/instrument-master/sync`.
1b. **F&O deployments (Phase F).** An option/future deployment picks its contract at signal
   time from the master and the spot - check the Autopilot preview once the master is loaded.
   Bought options exit on the strategy's underlying levels with the premium floor as the safety
   net; written options carry open-ended risk until the underlying stop or the premium ceiling
   exits, need the broker's margin calculator LIVE, and default to one lot - keep `max_lots`
   explicit. All F&O positions are squared off at 15:15 IST like everything else, which also
   covers expiry day. Positions show `on <underlying>: SL / T1 · premium floor` so you can see
   both legs of the exit rule.
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

#### Phase I: risk limits and broker accounts

* A deployment whose `last_error` names a risk limit (`MAX_ORDER_VALUE (tenant): order value
  ... exceeds limit ...`) was refused by the risk hierarchy; the Risk page's event log shows the
  measured value against every limit that applied. A `Strategy stopped` or `Daily loss limit
  reached` CRITICAL notification means a loss limit engaged a kill switch: review, then
  disengage it from the Risk page only once the cause is understood - the limit will trip again
  otherwise.
* Broker accounts (Settings): **Sync** pulls balance, margin and P&L through that account's own
  session; a `DISABLED` account refuses new LIVE entries (deployments say
  `broker account #N ... is DISABLED`); ★ marks the default account a deployment on that broker
  uses when it names none. A second account at the same broker is a second credential with its
  own label, and its token expires and is renewed independently of the first.

#### Phase H: option structures and strike filters

* A deployment with **strike filters** whose `last_error` reads `Contract not resolved: No CE
  strike within N steps ... passes (...)` found no liquid enough strike in the live chain that
  cycle - by design. Loosen the filters or widen `search_steps`; nothing is traded meanwhile.
  `Option chain ... unavailable` means the broker's chain endpoint failed: check the session.
* A **spread/condor** shows on the Positions page as two or four legs sharing a group badge
  (credit, max loss, exit levels). They are closed together by the worker; closing one leg by
  hand at the broker leaves the others naked - if you must intervene, close the *short* legs
  first, then run reconciliation. `Structure not built: ... not entered on a SHORT signal` on
  a bull put deployment is normal: the strategy leaned the wrong way that bar.
* A LIVE structure that reads `Structure failed: ... unwound N filled leg(s)` had a leg fail
  mid-placement; the filled wing was sold back, and the tenant is broker-uncertain until
  reconciliation passes (see the Phase G notes below).

#### Phase G safety gates in the worker

* **Stale market data** - a deployment whose `last_error` reads `Skipped: market data stale: ...`
  was not evaluated because the newest candle is more than `MARKET_DATA_MAX_STALE_BARS` (3) bars
  behind the clock. Check the broker's data feed / your own clock; the deployment trades again on
  the first fresh cycle, nothing to reset. Exits: a quote older than `QUOTE_MAX_STALE_SECONDS`
  (120) is refused for that cycle (`Price unavailable: ... quote stale ...` in the logs); LIVE
  positions keep their broker-side stop meanwhile.
* **Broker uncertain** (red banner on the Autopilot page, `GET /api/reconciliation/status`) - a
  LIVE order FAILED (the broker call raised or timed out), so the platform does not know what the
  broker holds. New LIVE entries for that organisation are refused until a reconciliation comes
  back with zero mismatches. The worker reconciles every cycle by itself; if the flag persists,
  the CRITICAL notification names the mismatch (`UNTRACKED_AT_BROKER X` = a position at the
  broker the platform has no record of; `MISSING_AT_BROKER X` = the reverse). Square off or
  record the difference at the broker / on the Positions page, then press **Reconcile** or wait
  one cycle. Never clear the flag by editing the database.
* **Reconciliation on start** - every worker start reconciles each tenant with an open LIVE trade
  before the first cycle (`Start-up reconciliation:` log lines). A tenant with no usable broker
  session at that moment is skipped with a warning and picked up by the per-cycle run once they
  log in.
* **Circuit open** (`atp_broker_circuit_state{broker} == 2`, `dependencies` health `degraded`) -
  more than half the calls to that broker failed in the last minute. New LIVE entries to that
  broker are paused for every tenant for two minutes, then one probe entry is tried. Exits still
  go through. Nothing to do unless it stays open: then the broker is down, and the question is
  whether to flatten LIVE positions by hand at the broker's own terminal.
* **Ending a broker session on purpose** - `POST /api/broker/{name}/disconnect` revokes today's
  token at the broker and marks it EXPIRED; LIVE deployments stop until the next login. Use it
  when a token may have leaked or when handing a machine over.

### 1.7a Monitoring (Phase E1)

* **Scrape targets**: `backend:8000/metrics` (send `Authorization: Bearer $METRICS_TOKEN` when
  set) and `worker:9102/metrics`. Both are Prometheus text format; the worker one is only
  reachable on the compose network.
* **Alerts worth having** (PromQL sketches):
  * engine down on a trading day: `atp_worker_heartbeat_age_seconds > 3 * 60` while
    `/api/system/health/deep` reports `market_open: true` (or use the worker's own
    `time() - atp_worker_last_cycle_timestamp_seconds`);
  * broker/API trouble: `increase(atp_orders_total{status="FAILED"}[15m]) > 0`;
  * alerts not leaving the building: `atp_alert_outbox_pending > 20` for 10 minutes, or
    `increase(atp_alert_deliveries_total{status="FAILED"}[1h]) > 0`;
  * credential stuffing: `atp_login_failures_15m > 50`;
  * API latency: `histogram_quantile(0.95, sum(rate(atp_http_request_duration_seconds_bucket[5m])) by (le, route)) > 1`.
* **Health probes**: `GET /api/system/health` (liveness), `GET /api/system/ready` (readiness,
  database only), `GET /api/system/health/deep` (operator detail; `degraded` names the component).
  Phase G3 adds the master-prompt spellings `GET /api/system/health/live|ready|dependencies`;
  `dependencies` is `deep` plus every broker circuit breaker's state and the number of
  organisations with LIVE entries blocked pending reconciliation.
  The Docker `HEALTHCHECK` uses liveness on purpose - a stale worker must not restart the API.
* **SLOs and alert rules** (Phase G2): `docs/SLO.md` states nine objectives and the metric behind
  each; `scripts/monitoring/prometheus-alerts.yml` is the matching Prometheus rule file (load it
  with `rule_files`). The PromQL sketches above are superseded by that file.
* **Finding one request in the logs**: every response carries `X-Request-ID`; ask the user for
  it (browser dev tools, or the error toast) and grep the API logs for `request_id=<id>`.

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

Set `BACKUP_ENCRYPTION_PASSPHRASE` (section 1.2): dumps are then AES-256-CBC encrypted with a
PBKDF2-derived key before they touch disk, and `restore.sh`/`verify_backup.sh` require the same
passphrase (a wrong one fails the restore, it does not produce a plausible-looking database). The
passphrase is the one secret that must survive losing the server - store it with the same care as
`SECRETS_ENCRYPTION_KEY`, and rehearse a restore with it at least once a quarter.
