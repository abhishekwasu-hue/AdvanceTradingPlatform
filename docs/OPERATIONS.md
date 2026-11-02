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
| Database crash / corruption | 5 minutes (one WAL archive interval, Phase O5 - in force) | 30 minutes |
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
  `docker compose run --rm --entrypoint sh backup /scripts/verify_backup.sh latest`. CI runs the same
  rehearsal on every push (`tests/test_backup_scripts.py`), including the encrypted and tampered
  cases.
- **Restore for real**: `scripts/backup/restore.sh <file|latest>` (asks you to type the database
  name; `RESTORE_CONFIRM=yes` for scripts) replaces the configured database with the backup, then
  start the API (`alembic upgrade head` runs on start and is a no-op for a same-version dump) and
  follow 1.3 for open positions. Point-in-time recovery between daily dumps is not provided: for
  that, add WAL archiving or use a managed Postgres with PITR, and keep these dumps as the
  provider-independent copy.

### 1.2a Point-in-time recovery (Phase O5)

- **Continuous WAL archiving** is on in `docker-compose.yml` (`archive_mode=on`, five-minute
  `archive_timeout`, segments copied into the `wal_archive` volume). On the Hostinger host it is **off**
  unless `PG_ARCHIVE_MODE=on` is in `.env` (A1, `docs/deploy/HOSTINGER_RUNBOOK.md` section 6c). The
  archive holds raw database pages: it is never copied off the host (the off-site copy does not mount it).
- **Weekly base backup**: on Hostinger `deploy/atp.sh base-backup` (scheduled every Saturday when
  `PG_ARCHIVE_MODE=on`); elsewhere `docker compose run --rm --entrypoint sh backup /scripts/base_backup.sh`
  (or from cron). It writes `backups/base/<stamp>/` (encrypted and sealed when
  `BACKUP_ENCRYPTION_PASSPHRASE` is set, the first WAL file in `start_wal`), keeps the newest
  `BASE_BACKUP_KEEP` (4) and prunes WAL older than the oldest kept base backup. Alert when
  `LAST_BASE_BACKUP_OK` is older than 8 days (`atp.sh health` fails then).
- **Recover to an instant** (e.g. just before a bad bulk change at 11:04 IST):
  1. stop the API and worker (`docker compose stop backend worker`);
  2. `docker compose run --rm -v atp_pitr:/pitr --entrypoint sh backup /scripts/pitr_restore.sh latest "2026-09-28 11:03:30+05:30" /pitr`;
  3. start a scratch Postgres on that directory (`docker run --rm -v atp_pitr:/var/lib/postgresql/data postgres:17-alpine`),
     which replays WAL to the target and promotes; verify `alembic current`, open trades and
     `python -m app.audit.verify_chain` against it;
  4. swap volumes (or `pg_dump` the scratch and `restore.sh` into production), start the worker
     last and follow 1.3 for open positions.
- **RPO/RTO per data class** (section 52): orders, trades, risk events and the audit chain
  (transactional, WAL-archived) RPO 5 min / RTO 30 min; notifications and alert deliveries the
  same RPO, RTO best effort (they regenerate); market-data caches and the worker lock live in
  Redis and are not backed up (RPO n/a, rebuilt on the next cycle); broker credentials and
  tenant keys are in Postgres under the same guarantees, plus the master key in your secret
  store (loss of the master key is unrecoverable by design).

### 1.2b Resource budget - 8 GB host (Hostinger KVM 2)

The production host is 2 vCPU / 8 GB RAM / 100 GB NVMe (`docker-compose.hostinger.yml`, runbook
`docs/DEPLOY_HOSTINGER.md`). Every container has a memory limit so a runaway process is OOM-killed and restarted
instead of pushing the host into swap. All values are `.env` overrides (`MEM_*`, `PG_*`, `REDIS_MAXMEMORY`).

| Container | Limit | Sized for |
|---|---|---|
| postgres | 2 GB | `shared_buffers` 512 MB, `effective_cache_size` 1.5 GB, `work_mem` 8 MB (x `max_connections` 75 worst case 600 MB, real use far lower; about 10 MB per connection), `maintenance_work_mem` 128 MB, WAL archiving on |
| backend (API) | 1.5 GB | one uvicorn process (2 vCPU: the event loop + the threadpool for backtests); a 2-year 1-min backtest peaks near 600 MB |
| worker | 1.5 GB | one replica (the Redis lock forbids two); per-cycle candles + indicator caches per deployment |
| redis | 512 MB | `maxmemory` 384 MB, `volatile-lru`: at the cap only keys with a TTL are evicted - caches, quote ticks, rate windows, and also the worker lock (see below) (H-1) |
| backup | 512 MB | `pg_dump` custom format + encryption |
| caddy, offsite | 256 MB each | TLS edge; rclone copy |
| frontend | 128 MB | nginx serving static files |
| **sum of limits** | **≈ 6.6 GB** | leaves ≈ 1.4 GB for the OS, Docker and page cache; 2 GB swap (swappiness 10) is a safety net, not working memory |

**Redis at its cap (H-1, answered 2026-10-10).** `volatile-lru` evicts only keys with a TTL, and the worker's replica
lock has one (ADR-0010). What can and cannot be lost:

| Key | Where | At the cap |
|---|---|---|
| worker replica lock | Redis, TTL = 2 x cycle | can be evicted: the worker notices at the next renewal and **fails closed** - no new entries for the rest of the cycle (exits continue); if another worker has taken it, this one stops the cycle. One CRITICAL "Worker lock lost - failed closed" to the operators (SUPER_ADMIN organisations; none yet: the organisations with an ACTIVE deployment), metric `atp_worker_lock_lost_total`, alert `WorkerLockLost` |
| alert outbox, order idempotency keys | Postgres (`alert_deliveries`, `orders.idempotency_key`) | not in Redis - eviction cannot touch them |
| caches, quote ticks, rate windows | Redis, TTL ≤ 1 h (`cache_set` caps at `CACHE_MAX_TTL_SECONDS`) | evicted first; recomputed |

The worker reads `used_memory / maxmemory` every `REDIS_MEMORY_CHECK_SECONDS` (300) into `atp_redis_memory_used_ratio`
and sends one WARNING an hour at `REDIS_MEMORY_WARN_RATIO` (0.70) - before eviction starts (alert `RedisMemoryHigh`).
Exactly one worker replica runs (`deploy.replicas: 1` in `docker-compose.hostinger.yml`); never `--scale worker=2`.

Concurrency: one API process and one worker on 2 vCPU. `WORKER_CYCLE_SECONDS` stays 60. Heavy research (long
backtests, optimisation) shares the API's threadpool - more than two at once slows the API; the job queue (spec part
E) moves them off it. When `deploy/hostinger/status.sh` shows a container near its limit, raise its `MEM_*` and keep
the sum under ≈ 7 GB.

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
   **Pre-open reminder (D5, rule IN-SEBI.login.daily):** on NSE trading days, from
   `reminder_minutes_before_open` before 09:15 (rule-set data, default 30), every organisation with an
   ACTIVE deployment whose broker session will not last until the close gets one WARNING
   ("Log in to <broker> before the open") naming how that broker logs in (OAuth / login code / paste
   token). Once per organisation per day, also across a worker restart; API-key venues are skipped.
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

### 1.6a Billing routine (Phase K1)

Two providers sit behind the same seam. **Razorpay** (recommended for launch) collects
automatically; **manual** means the operator records bank/UPI payments by hand.

**Razorpay setup (once):**

1. In the Razorpay dashboard create API keys (*Settings → API Keys*; use test-mode keys first) and
   a webhook (*Settings → Webhooks*) pointing at `https://<api-host>/api/billing/webhooks/razorpay`
   with the events `subscription.charged`, `subscription.activated`, `subscription.halted`,
   `subscription.cancelled`, `subscription.completed`, `payment.failed`. Note the webhook secret.
2. Set `BILLING_PROVIDER=razorpay`, `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`,
   `RAZORPAY_WEBHOOK_SECRET` on the **backend** service only (the worker never talks to the
   gateway). These are the operator's platform secrets, not tenant data - never in Settings.
3. Our plans are mirrored as Razorpay Plans lazily (`billing_gateway_plans`); a price change
   creates a new Razorpay plan automatically. Existing manual subscriptions move to the gateway
   on their next plan change.
4. Flow: a tenant chooses a plan → a Razorpay Subscription is created (first charge deferred to
   the trial end) and the hosted checkout link appears on the Billing card as **Pay / set up
   autopay** → the tenant authorises the mandate on Razorpay's page → each cycle's charge arrives
   as `subscription.charged` and books the payment (idempotent on the payment id and on the
   webhook event id) → `payment.failed` / `subscription.halted` raise notices and start the grace
   period → `subscription.cancelled` drops the tenant to Free.
5. Every delivery is in `billing_webhook_events` (event id, type, payload, result). A rejected
   signature is a 400 and is not recorded; an unknown event is recorded as `ignored`.
   `GET /api/admin/billing/{tenant_id}` still shows the full state; the manual payment endpoint
   still works for a bank transfer that bypasses the gateway.

**Manual provider** - the operator closes the loop by hand:

1. A tenant subscribes under *Settings → Plan & billing*; a plan with trial days entitles at
   once and an invoice (OPEN) is raised, payable after the trial. Without a trial the tenant
   stays on Free until the payment is recorded.
2. When the bank transfer / UPI arrives, record it:
   `POST /api/admin/billing/{tenant_id}/payment {"amount": 2999, "reference": "UPI/..."}`
   (SUPER_ADMIN, MFA). Open invoices flip to PAID, the subscription becomes ACTIVE and the
   period is extended by the billing cycle.
3. The worker's daily sweep moves ended periods to PAST_DUE with a 7-day grace (tenant keeps
   the plan, gets a "Payment due" notification) and, when grace runs out, back to Free with
   `tenants.status_reason` saying why. `GET /api/admin/billing/{tenant_id}` shows the state.
4. Refunds are recorded as REFUND transactions by the operator; nothing is ever deleted from
   `billing_transactions`.

Marketplace review (Phase K2): `GET /api/admin/marketplace/pending`, then
`POST /api/admin/marketplace/{id}/publish|reject {"note": "..."}` - the note reaches the creator.
Never publish a listing without an attached backtest run; the API refuses the submission anyway.

### 1.6b AI layer routine (Phase L)

- **Provider keys** are tenant data: entered under *Settings → AI provider* by an OWNER, encrypted
  with `SECRETS_ENCRYPTION_KEY`, never in `.env`, logs or support tickets. The recommended provider
  is Claude (Anthropic) through the official SDK, default model `claude-opus-5` with adaptive
  thinking and Anthropic's default refusal fallbacks; the Settings card pre-selects it. Rotating the platform
  key re-encrypts them with the same script as broker credentials. A tenant with no provider (or on
  Free) runs the rule-based parser; nothing leaves the platform.
- **Egress**: allow `api.anthropic.com` and `api.openai.com` from the API service only (the worker
  never calls a model). Provider errors show on the Settings card (`last_error`) and in the draft's
  status (FAILED).
- **Proposals**: the worker raises `AI_PROPOSAL` notifications; unanswered proposals expire after 24 h.
  A tenant asking "why did it pause?" - read `ai_actions` (rule, evidence, decided_by) and the
  deployment's `pause_reason`, which names the proposal id. The agent never acts unapproved; if a
  deployment paused without a decided proposal, that was the worker's own auto-pause after 5
  consecutive failures (Phase A).
- **Lineage** (V4 governance): `ai_strategy_drafts` keeps prompt, provider, model and raw response;
  `custom_strategies.origin = ai:<draft>` links a live strategy back to it.

### 1.6c Platform controls and incidents (Phase M)

- **Maintenance mode** (*Admin Console → Platform controls*): turn on with a message before a
  planned DB or broker-integration change. No new entries anywhere; exits, monitoring and the API
  keep running; the top bar shows the message to every user. Turn off afterwards - it is not
  lifted automatically. Both changes are audit events (`maintenance_mode_on|off`).
- **Disabled brokers**: list a broker while its API is degraded or its credentials rotate; LIVE
  entries through it are refused and the deployment says so; exits still go through. Remove it
  from the list to resume.
- **Per-user trading disable** is the tenant OWNER's tool (*Team → disable trading*) and yours
  for a member who must stop opening positions while keeping access.
- **Incidents** (*Admin Console → Incidents*, `/api/admin/incidents`): engaging the global kill
  switch opens one automatically; open one by hand for any outage. Resolve it with the root cause
  and the actions taken; downtime is computed from the start time, data loss is what the restore
  drill measured. The record keeps the audit-log id range it spans - that is the post-mortem's
  evidence trail (section 1.1's RPO/RTO targets are judged against these numbers).

### 1.6d Secrets, scopes, verification, flags and migrations (Phase N)

- **Encryption estate** (*Admin Console → Feature flags* footer, `GET /api/system/encryption`):
  after upgrading to Phase N run `python scripts/reencrypt_secrets.py status`, then `reencrypt`
  once (outside market hours; it is idempotent) so no secret depends directly on the master key.
- **Master key rotation**: (1) `reencrypt` under the current key, (2) set the new
  `SECRETS_ENCRYPTION_KEY` and put the previous value in `OLD_SECRETS_ENCRYPTION_KEY`, (3)
  `python scripts/reencrypt_secrets.py rotate-master`, (4) restart API and worker, (5) remove
  `OLD_SECRETS_ENCRYPTION_KEY`. The script never prints a key. A start-up error mentioning
  `rotate-master` means step 3 was skipped.
- **Platform mailer**: set `PLATFORM_SMTP_HOST/PORT/USERNAME/PASSWORD/FROM`; until then
  verification links appear in the API log (`Email verification link for ...`) and the Account
  tab tells users to ask you. Turn `EMAIL_VERIFICATION_REQUIRED=true` on only after the mailer
  works (production config validation refuses the combination otherwise). A user without mail
  access can be stamped verified from `POST /api/team/members/{id}/verify-email` (admin).
- **Scopes**: owners manage them under *Team → permissions*. A member reporting 403 with
  `X-Missing-Scope` needs that scope granted or the denial lifted; SUPER_ADMIN is never limited.
- **Feature flags** (*Admin Console → Feature flags*): turn a feature off during an incident
  (`ai_copilot` when the provider misbehaves, `public_api` under abuse, `live_trading` when a
  broker-wide problem is not limited to one broker, `self_signup` to go invite-only). An
  allow-list keeps it on for named tenants. Every change is an audit event (`feature_flag_set`).
- **Migrations during market hours**: the backend container runs `scripts/migrate_guard.py`
  before uvicorn. A new image with a pending migration deployed between 09:15 and 15:30 IST on a
  trading day exits with code 3 and the container restarts until you either deploy after close
  or set `MIGRATION_FORCE=1` for that one start (accepting the risk to open positions). Restarts
  without pending migrations are unaffected.

### 1.6e Database roles, exchange sessions, push and SMS (Phase O)

- **Least-privilege roles** (once per database): `SUPERUSER_DATABASE_URL=... ATP_APP_PASSWORD=...
  ATP_MIGRATOR_PASSWORD=... python scripts/init_db_roles.py`, then set `DATABASE_URL` to `atp_app`
  and `MIGRATION_DATABASE_URL` to `atp_migrator` and restart. Re-run the script after restoring
  a dump made under a single role (it re-owns tables). A migration failing with "must be owner"
  means `MIGRATION_DATABASE_URL` is unset.
- **Exchange sessions**: the worker now trades MCX deployments until 23:30 IST and crypto around
  the clock; the Admin/System status shows each venue's state. Load MCX holidays into
  `market_holidays` with `exchange = 'MCX'` (the NSE list does not apply to commodities).
  Retention still runs after the NSE close.
- **Browser push**: generate a VAPID key pair once (`python -m app.alerts.webpush`), put both
  values in the environment of the API *and* the worker, and never rotate casually (every device
  must re-subscribe). Users enable push per device under *Settings → Alert delivery*. Endpoints
  the push service reports gone are pruned automatically. Safari needs the site installed to the
  home screen on iOS.
- **SMS**: a tenant pastes its own gateway credentials (MSG91 auth key, Twilio basic auth) into a
  request template; the platform holds no SMS account. In India a DLT-registered template id is
  required; the MSG91 preset shows where it goes. Gateway errors appear as the channel's
  `last_error` with the HTTP status. Template rules: in a JSON body every placeholder must sit
  inside quotes (`"text":"{text}"`) - values are JSON-escaped, not quoted; in a form body or a GET
  query the template is URL-encoded text, so a literal `+` (e.g. a `From=` number) must be written
  `%2B` (a bare `+` arrives as a space), while the placeholder values are encoded by the platform.
  Changing the gateway URL to another scheme / host / port drops the stored headers (type them
  again); changing the SMTP host, port, user or turning TLS off drops the stored SMTP password.
  Header names `Host`, `Content-Length`, `Transfer-Encoding`, `Connection` and the like are refused.
- **Performance probe**: `python scripts/loadtest.py --base https://<api> --users 20 --seconds 30`
  after every release that touches the request path; paste the table into docs/PERFORMANCE.md.

### 1.6f Stop guard, tax report, currencies, drift gate, staging (Phase P)

- **Stop guard**: a WARNING "Protective stop re-armed" means the broker-side SL-M was missing or
  cancelled and has been re-placed; look at who cancelled it (audit `protective_stop_rearmed`).
  A CRITICAL "No broker-side stop" means re-placing failed (margin, session): the software stop
  still runs every cycle, but close the position by hand if the broker session is gone.
- **Tax report** (*Analytics → Tax report*): users pick the financial year and LIVE/PAPER/ALL and
  download the CSV for their CA. Every statutory rate comes from the dated table
  `backend/app/execution/india_costs.py` (the one PAPER fills and backtests use): add a new dated row
  with its source when a Finance Act or circular changes a rate. F&O turnover method:
  `TAX_FNO_TURNOVER_METHOD` (`ABS_PNL` default, or `ABS_PNL_PLUS_PREMIUM`, the older method); the report
  and CSV name the method used.
- **FX rates** (`PUT /api/admin/fx-rates`, admin): set `USD/INR`, `USDT/INR`, etc. when a tenant
  reports in a non-INR currency or trades a non-INR instrument; portfolio exposure lists
  `fx_missing` pairs it could not convert. Owners choose the reporting currency under *Team*.
- **Drift gate**: a `DEGRADATION` proposal under *AI Copilot* says the live record has diverged
  from the backtest; approve to pause, reject to keep trading, or re-run the backtest with recent
  data if the market has simply changed.
- **Staging**: `scripts/deploy.sh staging` on the host (ports 18000/18080, database
  `advance_trading_platform_staging`); set `STAGING_ENABLED=true` plus `STAGING_SSH_*` secrets
  to have GitHub deploy every push to main. Production deploys are `scripts/deploy.sh production`
  after 15:30 IST; the migration guard refuses schema changes during the session and the script
  restarts the API only after the deep health check passes; if it never does, the API is restarted from the previous image (P0.7).

### 1.6g Order pre-checks (Phase Q)

- **"is an index, not a tradable instrument"** on a LIVE deployment: the deployment trades the
  index spot. Set contract rules (option BUY/WRITE or future) under *Autopilot*; paper
  deployments on the spot keep working.
- **"not in <broker>'s NSE instrument master"**: the symbol is misspelt or delisted, or the
  master is stale. Check *Instruments → search*; the worker re-syncs the master daily, and
  `POST /api/instrument-master/sync` forces it.
- **"Insufficient margin for ..."**: the broker's calculator says the account cannot carry one
  lot/share. Add funds or reduce `max_lots`; the order was never sent.
- **"Margin not verifiable with <broker>"** (note, not a refusal): the broker has no margin
  calculator in its adapter (Shoonya) or it errored; the broker still enforces margin when the
  order is placed, as it always did.
- **"Could not read available margin"**: the funds endpoint failed - usually an expired broker
  session. Re-login under *Settings → Broker*; entries resume on the next signal.
- **PARTIAL_FILL** on an order's trail: the position and its stop use the filled quantity; the
  remainder of a market order is not left working. Reconciliation confirms against the broker.

### 1.6h Streaming quotes (Phase S)

- **Turning it on**: set `STREAMING_QUOTES_ENABLED=true` on the worker (and API, for the Redis
  mirror) and restart. Watch the worker log for `stream error` lines and the metrics
  `ticks_received_total{broker}` (should climb during the session) and
  `stream_reconnects_total{broker}` (should stay near zero). Leave it off until one paper day has
  shown ticks arriving; polling continues underneath either way.
- **First live verification** (not possible in the sandbox): with a valid Upstox session the
  authorise call must return `authorized_redirect_uri` and the first binary frame must decode to
  the subscribed instrument keys with a plausible `ltp`. If `ticks_received_total` stays at zero
  while `stream_reconnects_total` climbs, the feed URL or the frame layout differs from what
  `app/market_data/stream.py` expects; keep the flag off and file the frame bytes.
- **"cannot resolve X - REST fallback"** in the log: the symbol is not in the broker's instrument
  dump under that exchange; that symbol is polled, the rest stream.
- **Stale ticks**: a tick older than `TICK_MAX_AGE_SECONDS` is ignored and the REST quote used;
  the Phase G1 gate then decides. A silent socket therefore costs one REST call per symbol per
  cycle, never a decision on an old price.
- **Kite**: the ticker accepts at most 3 connections per API key and 3,000 tokens per
  connection; one stream per tenant session stays well inside that.

### 1.6i Account routing (Phase T)

- **Choosing a policy**: on the Autopilot form (per deployment) or the Team page (tenant
  default). `EXPLICIT` keeps today's behaviour. The capital policies need the accounts' balances
  synced within 15 minutes; the worker does this itself every 5 minutes through the sessions it
  holds, so a fresh worker restart may route the first cycle by the default account.
- **"needs a balance synced within 15 min, none is - default account used"** in the card's
  routing line: no candidate had a fresh balance. Check *Settings → Broker accounts* for
  `last_sync_error` (funds endpoint down, session expired) and re-login; the policy takes over
  again on the next refresh.
- **A position in the wrong account**: cannot happen from this version on - exits and stop
  re-arms are placed through the account recorded on the trade. Trades from before this
  version have no account and are handled through the broker's default account; if such a
  trade actually sits in another account, close it by hand at the broker and mark it exited in
  the journal.
- **Disabling an account** (Settings) removes it from every policy's candidates and from the
  worker's sessions; its open positions are still exited through it as long as its token is
  valid, because the exit uses the trade's account, not the candidate list.

### 1.6j Ratio spreads, butterflies and custom legs (Phase U)

- **Reading the card**: these structures show "net credit/debit, max loss (or UNDEFINED), max
  profit, breakevens; exit at P&L >= x or <= y per unit". Multiply by lot size and lots for
  rupees. "UNDEFINED max loss" means one side has more sold than bought; the lots were sized
  off the stop, and the group also closes beyond that side's breakeven.
- **"cannot profit at expiry at these premiums"**: the legs, at the quotes just fetched, lose
  everywhere - typically a custom set with the roles inverted, or a ratio spread whose short
  strike is too close. Check the legs on the deployment; nothing was placed.
- **"shows no loss at expiry ... quotes inconsistent"**: a defined-risk set priced as free
  money, which only happens with stale or crossed quotes. Nothing was placed; it retries on
  the next signal.
- **LIVE margin**: the broker's requirement is asked for each short leg at its full ratio
  quantity, no spread benefit assumed. A 1:2 ratio spread therefore needs the margin of two
  naked shorts to pass the cap; SPAN benefit at the broker is a bonus, never relied on.
- **Custom legs and filters**: strike filters are refused on a CUSTOM structure because its
  legs name their strikes; use OTM/ITM steps per leg instead.

### 1.6k Risk Guardian rules (Phase V1)

- **"Cool-down: X was stopped out N min ago"**: rule R10. The next signal in that underlying
  is refused until `stop_cooldown_minutes` have passed. Shorten the setting on the Risk page
  if it is too conservative for the strategy's timeframe; the platform minimum applies.
- **"Drawdown ladder ... risk per trade halved" / "... new entries paused"**: the mode's equity
  is 5% / 10% below its peak. Paused is not a kill switch: exits keep running and the pause
  lifts on its own when equity recovers above the level. To resume earlier, review the journal
  and raise `dd_level_2_pct` (within the ceiling) - deliberately, once, not per trade.
- **"Event blackout" / "Event risk ... cut"**: an entry on a `market_events` day. Tenants keep
  their own events on the Risk page; the operator adds global ones (budget, RBI policy,
  expiry) with `global_event: true`. An event with a time window applies only inside it (IST).
- **"Portfolio risk: open risk at the stops ... exceeds"**: rule R4. Either close something or
  raise `max_portfolio_risk_pct` within the ceiling. Index positions share one bucket; the
  message says how much that bucket already holds.
- **"... capped at the platform ceiling"** on an order: the tenant's saved setting is above a
  ceiling the operator lowered later. The engine used the ceiling; the tenant should re-save
  its settings. Ceilings: `GET/PUT /api/admin/controls/risk-ceilings` (SUPER_ADMIN + MFA).

### 1.6l AI draft compliance (Phase V2)

- **"Auto-fixed: stop_loss_atr_mult 0.4 -> 1 (M2)"** on a draft: the model's stop sat inside
  one ATR; the platform raised it. The fix is recorded on the draft and in the checklist.
- **"Confirm that you accept the risk before approving"**: the approval needs the tick under
  the "You must accept" statement (API: `accept_risk: true`). This is the spec's requirement
  that the human states the maximum loss before going live, not a UI nicety.
- **"Resolve the compliance failures first"**: only possible for a draft generated before this
  version whose config fails M1/M2 - regenerate it.
- **Weak evidence warnings (E1)** do not block approval; the human gate decides. They stay on
  the record so a later review can see what was known at approval time.

### 1.6m AI prompt and context (Phase V3)

- **Prompt version** is on every draft (`prompt_version`) and in `GET /api/ai/context`. Change
  the template only by bumping `PROMPT_VERSION` in `app/ai/prompt.py`; drafts keep the version
  that answered them.
- **"What will the AI be told?"**: `GET /api/ai/context?language=mr&regime=RANGING&symbol=...`
  returns exactly the runtime block - capital, open risk, drawdown, recent trades, events. No
  broker or AI credential ever enters the prompt.
- **A reply in the wrong language**: the page sends the browser language; pass `language`
  explicitly on `POST /api/ai/drafts` to override. The JSON is always English.
- **"deployment: option_strategy dropped"** in a draft's warnings: the model proposed a
  structure the platform does not have; the rest of the suggestion is kept.

### 1.6n Historical option backtests and recorded chains (Phase W)

- **Synthetic vs recorded premiums**: a run's `options.pricing` says which model priced it. Synthetic
  (Black-Scholes) is for structure mechanics - strikes, expiries, exits, lot sizing - never for
  claiming an edge; the result and the page say so. Recorded quotes are what the market showed.
- **Recording**: the worker samples the chains of ACTIVE option deployments every
  `CHAIN_SNAPSHOT_INTERVAL_MINUTES` (default 5; `0` disables), `CHAIN_SNAPSHOT_ATM_SPAN` strikes
  either side of the money, market hours only. One extra broker call per underlying per interval
  from the tenant's own session (its rate budget applies). `GET /api/backtest/option-chain/coverage`
  shows what exists; `chain_rows_recorded` is on the worker heartbeat report.
- **Growth**: roughly 50 rows a capture, a few thousand a day per underlying. Retention trims rows
  older than `RETENTION_CHAIN_SNAPSHOTS_DAYS` (default 400, floor 7). The table is platform-wide;
  it is not tenant data and not personal data.
- **"No recorded option-chain quotes ... in the candle span" (400)**: the run asked for
  `pricing=snapshots` with the fallback off over a span nothing was recorded. Allow the fallback,
  upload rows (`POST /api/backtest/option-chain/snapshots`, CSV columns timestamp, expiry, strike,
  right, ltp) or use synthetic pricing.
- **Wrong expiries in an old year**: for the other indices conventions default to the exchange's current
  listings - override `expiry_weekday` (0 = Monday) and `weekly_expiry` for the period being tested, and
  `lot_size` / `strike_step` when those differed. **NIFTY and BANKNIFTY have no rule** (a pinned
  `expiry_weekday` on them is a what-if run on a made-up calendar, not the exchange's dates): their expiries are the dates NSE printed in its F&O bhavcopies
  (`backend/app/instruments/data/nse_index_expiries.csv`, every one confirmed by the bhavcopy of its own
  day; a far contract the exchange re-dated is listed only under the date its newest file prints).
  `weekly_expiry=false` keeps the monthlies only; the MONTHLY rule always takes the monthly. Bars after
  the file's last day (`options.expiry_calendar` says "data through ...") see only the contracts listed by
  then - nothing is invented; a bar with none left counts as "no expiry". A run before 2016 fails. Refresh:
  GitHub > Actions > "NSE expiry data" > Run workflow (it also runs every Saturday); the result arrives on
  the `data/nse-expiries` branch and the job opens the pull request itself and starts CI on it - review and
  merge it once CI is green. For the job to open the pull request, the repository setting Settings > Actions >
  General > "Allow GitHub Actions to create and approve pull requests" must be on; without it the job fails
  with an error naming that setting (the data is still pushed to the branch). The build refuses (nothing committed) when a week has
  no file, a NIFTY / BANKNIFTY expiry has no file on its day and no move explains it, or the result knows less than
  the committed file - re-run later (NSE may have been blocking) and read the job log. On a machine that reaches nsearchives.nseindia.com the same build is
  `python backend/app/instruments/nse_expiries.py --start 2016-01-01 --out backend/app/instruments/data`.
- **Signals not traded**: `options.signals_skipped` counts why - the sizer refusing a lot bigger than
  the risk per trade, no quote for a leg, or quotes that make the structure a debit where a credit
  is required. Raise capital or risk per trade, widen the recorded strikes, or change the structure.

### 1.6o Marketplace revenue share (Phase X)

- **Terms**: `PUT /api/admin/controls/marketplace-terms` (Admin page or the Marketplace page's admin card)
  sets `platform_fee_pct`, `min_payout`, `max_listing_price`. A published listing keeps the fee it was
  published under; changing the fee only affects listings published afterwards.
- **Manual provider (no gateway)**: a purchase opens a charge and the buyer is told to pay the operator
  quoting the charge number. Confirm it under "Open charges" (Mark paid, with the UTR/UPI reference);
  the buyer's copy is made at that moment. Void a charge the buyer abandoned.
- **Razorpay**: the buyer gets a hosted payment link; `payment_link.paid` settles the charge through the
  same signed webhook as subscriptions. Enable the `payment_link.paid` event on the Razorpay webhook.
  A webhook result reading "ignored: paid X < charge Y" means a partial payment - refund it at Razorpay
  or confirm manually once the balance arrives.
- **Payouts**: creators request everything available (>= `min_payout`), one request at a time. Open
  "Show destination" (audited), make the transfer, "Mark paid" with the reference. Reject with a note if
  the destination is unusable; the earnings become available again. The platform never holds funds:
  charges and payouts are ledgers of real transfers.
- **Revenue**: `GET /api/admin/marketplace/revenue` - gross, fees, creators' share, paid out, open charges,
  requested payouts.

### 1.6p AI scanner (Phase Y)

- **Which model answered**: every plan and read carries `provider`/`model`/`prompt_version`; `rule_based`
  means no external provider was used (none configured under Settings, the plan lacks AI features, or
  the model's answer was unusable - the first warning then names the provider and the error).
- **Cost**: one provider call per plan and one per read (at most 40 matches); metered as `ai_scanner` in
  `GET /api/billing/usage`. Candles are never sent to the model - only the labels, closes and regimes.
- **Kill switch**: the `ai_copilot` feature flag turns both endpoints off; the deterministic scanner keeps
  working.
- **"X cannot be screened" warnings** are expected: the scanner has indicator, structure and option-chain
  filters only. Anything else in the request is reported, not silently dropped.

### 1.6q Factor Lab (Phase Z)

- **Pure endpoints**: `/api/quant/factors` and `/api/quant/risk` compute on the candles in the request and
  store nothing; no login, no metering. `/api/quant/exposure` reads the tenant's open trades for weights.
- **Warnings are the contract**: "no data for value, quality" means the caller sent no fundamentals;
  "need at least 20 overlapping bars" means the symbols' timestamps barely overlap - align the candle
  windows before comparing.
- **Sample data**: the page scores deterministic sample candles until a data source feeds it real
  histories (the backtest CSV/ broker candles); the demo banner says so.

### 1.6r Broker candles on the research pages (Phase AA)

- **Prerequisite**: a broker whose API key is stored under Settings > Brokers and whose session token
  is VALID today. Until then the Data switch on Signals, Scanner, Backtest and Factor Lab stays on
  Sample and the Broker option shows why it is disabled.
- **409 "No broker session with a valid token"**: log in to the broker (Upstox OAuth from Settings);
  tokens expire every trading day, so this is the morning routine, not a fault.
- **Per-symbol errors** ("Instrument NSE:XYZ not found"): use the broker's trading symbols; indices are
  `NIFTY`/`BANKNIFTY` on Upstox as mapped by the instrument master.
- **Rate**: candles are cached 60 s platform-wide per broker/symbol/interval/lookback, shared with the
  worker; a 50-symbol scan is at most 100 broker calls the first time and none for the next minute.
  Fetches are metered as `market_data_candles` (billing usage).

### 1.6s Go-live checklist (Phase AB)

- **Where**: Dashboard (each organisation, PAPER or LIVE target) and Admin console (platform). Both are
  read-only views of the platform's own state; re-check after each step.
- **Order that works**: platform list first (secrets, Postgres, migrations, Redis, SMTP, CORS, frontend
  URL), then the worker, then the instrument master and holidays, then each organisation's broker key
  and login, a PAPER deployment, risk settings and an alert channel. Run PAPER for a few sessions;
  switch the toggle to LIVE and clear MFA, email verification and the algo id before the first LIVE
  deployment.
- **API**: `GET /api/readiness?target=LIVE`, `GET /api/admin/readiness` (SUPER_ADMIN, MFA session).
- **Go-live evidence (D6, rule IN-SEBI.golive.checklist)**: the platform checklist also lists `golive.*` items (LIVE
  scope; `warn` at most, so PAPER is never blocked): ISO 27001 / SOC 2, CERT-In VAPT, incident register review, strategy
  white-box / black-box filing, AI disclosure review, DPO contact, breach runbook (notify within 72 h) - each `ok` while
  its latest evidence is valid. Record where the evidence lives (a document id or URL, never the document or a secret)
  with `PUT /api/compliance/golive/evidence {item_id, reference, valid_until?}` (SUPER_ADMIN, audited); `GET
  /api/compliance/golive` lists them. Two are checked automatically: login history kept at least 365 days, and AI trade
  ideas unpublished (`marketplace_ai_listings` off) unless an RA registration is on record for `ai.ra_gate`.

### 1.6t Angel One (SmartAPI) setup (Phase AC)

- **Create the app** at smartapi.angelbroking.com (a "Trading API" app); note its API key. Enable TOTP
  for the client on the SmartAPI portal and keep the base32 secret it shows.
- **Enter under Settings > Add / update broker credentials** (broker `angel_one`): API Key, Client ID
  (client code), PIN, TOTP Secret. Nothing goes in `.env`; the platform generates the daily TOTP itself.
- **Log in** from the Broker session health card; the JWT is stored encrypted and expires around 05:00
  IST, so the login is a morning step like Upstox's.
- **Symbols**: use platform symbols (RELIANCE, NIFTY, BANKNIFTY); the adapter maps to `RELIANCE-EQ`,
  `Nifty 50` and the numeric tokens. Sync the instrument master from the adapter (Instruments page) so
  option contracts resolve.
- **Verified against a mocked transport only.** Confirm one PAPER session with real quotes before any
  LIVE deployment routes to this broker.

### 1.6u Exchange holidays (Phase AD)

- **Where**: Admin console > Exchange holidays (SUPER_ADMIN to edit; everyone can read). Pick the exchange and
  year, paste the NSE annual circular as `YYYY-MM-DD description` lines, add. Duplicates are skipped and reported.
- **Why it matters**: without the list the worker treats every weekday as a trading day and the
  go-live checklist warns. Load next year's list each December when NSE publishes it.
- **Live option chains** on the Scanner and Option Chain pages need a broker session with a valid token,
  like broker candles; a stub broker reports "no option-chain endpoint" per underlying instead of failing.

### 1.6v Fyers setup (Phase AE)

- **Create the app** at myapi.fyers.in (App ID like `ABCD1234-100`, secret key, a redirect URL you control).
- **Daily login**: open the Fyers auth URL for your app, log in, copy the `auth_code` from the redirect, paste
  it under Settings > Add / update broker credentials (broker `fyers`) as Request Token with the API Key
  (App ID) and API Secret, then Log in from the session-health card. The exchanged token lasts the day.
- **Symbols**: platform symbols (SBIN, NIFTY, BANKNIFTY) map to `NSE:SBIN-EQ`, `NSE:NIFTY50-INDEX`,
  `NSE:NIFTYBANK-INDEX`; option contracts resolve through the public symbol master.
- **Verified against a mocked transport only.** Run a PAPER session with real quotes before any LIVE routing.

### 1.6w Dhan setup (Phase AF)

- **Generate the access token** on the Dhan web console (DhanHQ > Access token); note your client id.
- **Enter under Settings > Add / update broker credentials** (broker `dhan`): Access Token and Client ID. Nothing
  goes in `.env`. The token expires daily; regenerate and re-enter it as the morning step, then Log in from the
  session-health card to verify it.
- **Symbols**: platform symbols map to Dhan security ids through the public scrip master; index ids are built in.
  Option contracts use the master's trading symbols (for example `NIFTY-Oct2026-26000-CE`).
- **Rate limits**: Dhan allows one option-chain call every three seconds; the Scanner's chain filters on many
  underlyings will be slow on Dhan by design.
- **Verified against a mocked transport only.** Run a PAPER session with real quotes before any LIVE routing.

### 1.6x Factor Lab fundamentals coverage (Phase AG)

- The Factor Lab's value and quality factors read the Fundamentals module's stored data: a symbol needs a
  company profile (`POST /api/fundamentals/companies` or the Fundamentals page) and at least one financial
  period with PAT, EPS or shares, shareholders' equity and total debt; PAT growth needs two periods of the
  same type. Symbols without a profile are named in the response note and carry no value/quality score.
- PE and PB use the last close of the window scored, so intraday and daily runs differ slightly by design.
- Nothing is fetched on the fly; load or refresh financials under Fundamentals first.

### 1.6y Loading fundamentals (Phase AH)

- **From NSE**: on the Fundamentals page type symbols into "Add from NSE" and Fetch, or use
  `POST /api/fundamentals/refresh {"symbols": [...]}`. That creates the company profile and pulls the
  shareholding pattern and announcements. NSE serves its JSON only to a browser-like session and may
  block automated access without notice; a blocked symbol is named in the response, nothing is stored
  for it. Smoke-test one symbol from the production host before relying on it.
- **Financial statements**: NSE does not publish them as JSON. Export them from your data source as a
  CSV with the `FinancialPeriod` column names (see the Financials tab for the required and optional
  columns) and paste it into "Import financial periods"; re-importing a period updates it.
- **Provider**: `FUNDAMENTALS_PROVIDER` (default `nse`). A commercial vendor is a new
  `FundamentalDataProvider` registered through `ingest.set_provider_factory`.
- The Factor Lab's value/quality factors (Phase AG) read whatever is loaded here.

### 1.6z F&O on non-Upstox brokers (Phase AI)

- Contract symbols are stored and shown platform-wide in the Upstox master's spelling
  (`NIFTY 26000 CE 30 OCT 26`). On Zerodha, Angel One, Fyers, Dhan and Shoonya the adapter wrapper
  translates them into that broker's own symbol from its instrument list before every order, stop, exit,
  margin probe and quote, and translates positions and order books back.
- An entry refused with "lists no instrument for NIFTY ... on NFO" means the broker's master has no such
  contract (expiry not yet listed, strike outside the broker's band, master download failed). Check the
  broker's scrip master for that expiry; nothing is guessed.
- The Upstox master still drives contract resolution (`INSTRUMENT_SYNC_EXCHANGES`), so it must be synced
  even when no tenant trades through Upstox.

### 1.6aa First live confirmation of a broker session (Phase AJ)

- After entering a broker's key under Settings and logging in, press **Read-only check** on that account
  card. It probes profile, funds, instruments, a NIFTY quote, the nearest NIFTY option (resolved and quoted
  through the worker's own symbol translation), the broker's option chain (optional: skipped where the broker
  has no chain endpoint), positions and today's order book. It never places, modifies or cancels an order.
- Green on all nine steps is the adapter's first live confirmation; do it once per broker before the first
  PAPER session and again after any adapter upgrade. A red `profile` means the token; a red `derivatives` or
  `contract_quote` means the scrip master or symbol translation, and its message names the contract.
- Each run is audited as `broker_smoke_test` with its summary.
- **Daily login without OAuth (Fyers, Kite).** These brokers redirect to the URL registered on *their* console,
  so the platform cannot receive the code. The session banner's **Open <broker> login** builds the login page
  from the stored App ID and Redirect URI (`GET /api/broker/{name}/login-url`; no secret leaves the server);
  paste the returned `auth_code` / `request_token` (or the whole redirected address) and **Login with code**
  (`POST /api/broker/{name}/login-code`) exchanges it and stores the day's token encrypted. Pasting a new code
  always drops yesterday's access token first - the auth-code adapters skip the exchange while a token is on
  file, which used to make every pasted code "fail" with the stale token. Fyers needs the app's Redirect URL
  stored once as `redirect_uri`. Local PC runbook: `docs/LOCAL_PC.md`.
- **Kite and Fyers login without pasting (UI2-5).** Register ATP's callback as the app's Redirect URL on the
  broker's console: `<PUBLIC_BASE_URL>/api/broker/zerodha/oauth/callback` (Kite) or
  `.../api/broker/fyers/oauth/callback` (Fyers); `GET /api/broker/specs` shows the exact address. The origin is
  `BROKER_OAUTH_REDIRECT_BASE_URL` when set (an optional override), else `PUBLIC_BASE_URL` (D2, as for Upstox), else
  the address the request came in on. `GET /api/broker/{zerodha|fyers}/oauth/start` returns the login page; the broker sends the browser back to
  the callback, which exchanges the code and stores the session as the paste flow does. The link works once, for the
  user who started it, within 10 minutes (Upstox's callback too, since the UI2-5 review), and only while that user is
  still active with a trading role. Each client IP gets 20 callback requests per 5 minutes; past that the browser
  lands back in the app with `error=too_many_attempts`. The paste-the-code routes stay for the old Settings layout.
- **Operator note (UI2-5 review): nothing to set when `PUBLIC_BASE_URL` is already the public https origin.**
  `BROKER_OAUTH_REDIRECT_BASE_URL` is only needed when brokers must reach the API at a different origin (for example
  `https://atp.example.com`, no trailing path). With neither set, the API logs a `CONFIGURATION ACTION NEEDED` warning
  at startup and takes callback addresses from the request's Host header. A value that does not start with `https://` stops a production or staging start.
  The address must match the Redirect URL registered on each broker's console exactly.

### 1.6ab-0 Hardened environments (P0.1)

- `ENVIRONMENT=production` **and** `ENVIRONMENT=staging` refuse to boot with the default JWT secret, no
  `SECRETS_ENCRYPTION_KEY`, `ALLOWED_ORIGINS=*` or an empty `METRICS_TOKEN` (the error lists every problem).
  Neither creates tables at startup: run `alembic upgrade head` (the Dockerfile's migration guard does) - a model
  ahead of its migration shows up as a failing `alembic check`, not as a silently created table.
- Staging publishes Postgres/Redis/API/UI on `127.0.0.1` only: reach it through an SSH tunnel or the host's proxy.
- The worker needs Redis for LIVE entries in these environments: while Redis is unreachable the cycle log says
  "Redis unreachable - the replica lock is off" and every LIVE deployment shows "Redis replica lock unavailable -
  LIVE entries paused this cycle"; exits, PAPER and housekeeping continue. Fix Redis, the next cycle resumes.
- Backtest (incl. Monte Carlo, walk-forward, optimizer), scanner, price-action, S/R and option-chain analysis
  endpoints need a login; bodies over `MAX_REQUEST_BODY_BYTES` (8 MB, forwarded by compose) get a 413, and Caddy
  caps chunked uploads to `/api/*` at the same size. The UI asks for a sign-in on those pages instead of showing
  a raw 401.

### 1.6ab-1 Login protection, outbound URLs, authenticator codes (P0.2)

- Behind Caddy the API sees the real client IP: the prod overlay trusts `X-Forwarded-For` only from 127.0.0.1 and the
  `edge` network Caddy is on (`EDGE_SUBNET`, M55 O1); `127.0.0.1` otherwise.
  Request limits count in Redis across replicas in production/staging.
- A user who fails the password 3 times must wait 1 s, then 2, 4 ... up to 60 s between attempts (429 with
  `Retry-After`); nobody is ever locked out of their own account by someone else's attempts. One IP spraying 50
  failures in 15 minutes is refused (423). Optional CAPTCHA after N failures: set `CAPTCHA_PROVIDER`
  (`turnstile` or `hcaptcha`), `CAPTCHA_SECRET` and `LOGIN_CAPTCHA_AFTER_FAILURES` in `.env`; the login then needs
  `captcha_token` (the API answers 403 `captcha_required` until the UI sends one - widget not shipped yet).
- Alert webhooks, SMS gateways and push endpoints must be https and public: a private, loopback or link-local
  destination is refused at save time, and in production/staging the host is resolved right before every send
  and refused if it points inside. `EGRESS_ALLOWED_HOSTS` (comma-separated hosts or `.suffix`) narrows it further.
  Gateway error bodies are not shown in the delivery error any more (status code only).
- An authenticator code works once: the same code pasted twice (enrolment then login, or two step-ups) is refused;
  use the next code. A refresh token presented again within 30 s of its rotation is honoured (two tabs); later
  reuse still ends the session.

### 1.6ab-2 Sessions, audit anchors, webhook URL (P0.3)

- The browser keeps the refresh token in an HttpOnly cookie and the access token in memory; a reload refreshes
  from the cookie. Rotating `JWT_SECRET_KEY`: put the old value in `JWT_PREVIOUS_SECRET_KEYS`, restart, remove it
  a day later. Set `REFRESH_TOKEN_IN_BODY=false` once every browser runs the new UI; `JWT_ACCEPT_LEGACY=false`
  a day after the P0.3 deploy.
- The worker pins the audit chain head daily (`audit_anchors`); the monthly check and `verify_backup.sh` can
  verify from the last anchor. Re-run `scripts/db_roles.sql` after the P0.3 migrations so `atp_app` loses
  DELETE on the ledgers (audit_logs, audit_anchors, orders, order_events, trades). Users and organisations with
  audit rows can no longer be deleted (deactivate instead).
- TradingView webhook URL: stored hashed. Settings shows it only while an organisation still has its pre-P0.3
  plaintext; otherwise the **owner** presses Rotate and copies the URL once. Public API keys resolve by hash.
  The Telegram inbound webhook path now carries the stored hash (an identifier; the secret header is the
  credential), so rotating the TradingView URL changes it too: press **Register webhook** again afterwards.
- Passwords longer than 72 bytes are refused for new passwords only; existing accounts keep logging in
  (verification truncates exactly as bcrypt did when the hash was made).

### 1.6ab-3 Exact money, AES-GCM secrets (P0.4)

- Migration `c4d6e8f0a2b4` turns money columns into NUMERIC (amounts 2 decimals, prices 4). Postgres casts in
  place and rounds; nothing to do besides the usual off-hours `git pull` + `up --build`.
- `SECRETS_WRITE_FORMAT` stays `fernet` for the PAPER week (any image since Phase N reads it, so a rollback is
  safe). Afterwards set `SECRETS_WRITE_FORMAT=aesgcm` in `.env`, `up -d`, then
  `docker compose exec backend python scripts/reencrypt_secrets.py reencrypt` - rows move to AES-256-GCM bound to
  the organisation and the column. `status` shows `formats` and `pending_rewrite`. Never set it back to
  `fernet` after rows are in `aesgcm` *and* an older image is in play: the old image cannot read them.
- `SECRETS_ENCRYPTION_KEY` as a passphrase (not a Fernet key) gains a scrypt-stretched key; it takes over for
  new wraps only once `SECRETS_WRITE_FORMAT=aesgcm`, and secrets wrapped under the older SHA-256 derivation
  always open. The 44-character Fernet key most deployments use is unaffected.

### 1.6ab-4 Fills, stops, emergency exit (P0.5)

- A LIVE entry that does not fill inside the confirmation window is cancelled at the broker and the order ends
  REJECTED with the reason on its trail; nothing is booked. A partial fill books the filled part and cancels
  the rest. If the broker's book cannot be read or a cancel fails, the organisation goes broker-uncertain
  (readiness shows it) and LIVE entries pause until reconciliation clears it.
- Protective stops are SL-M where the broker takes them; on Zerodha options they are SL with the limit 1% past
  the trigger (Kite refuses SL-M there). The Deployments form has an **Entry** selector: market (default) or
  protected limit with a band.
- The daily-loss limit counts realised P&L plus the marked-to-market P&L of open positions on the Indian trading
  day (00:00 IST). Expect it to bite earlier than before on a bad morning; that is the intent.
- Emergency exit now cancels working LIVE orders at the broker and buys shorts back before selling longs; the
  response lists `broker_cancelled` and `broker_cancel_failures`.
- Migration `d5e7f9a1b3c5` on the next off-hours `git pull` + `up --build`.

### 1.6ab-5 Backtest numbers after P0.6

- Backtests re-run after P0.6 can differ from stored runs: daily limits reset each day (more trades on long runs),
  gaps fill at the open (worse stops, no better-than-open targets), written options carry STT on the entry premium,
  index option lots follow the dated table, and the optimizer's `best` is the in-sample winner with its
  out-of-sample `validation` beside it. Stored runs carry `engine_version`; compare like with like.

### 1.6ab-6 Deploy rollback, log rotation, static gates (P0.7)

- `scripts/deploy.sh` now really rolls back: if the new API never passes the deep health check, the previous
  backend image is retagged as the service image, the API is recreated from it (no rebuild) and the script waits
  for health again before exiting 4. The worker and the frontend were not restarted yet at that point, so they
  keep running the previous release; the schema is **not** rolled back (every migration since Phase N is additive
  and the previous image reads it). If there is no previous image (first deploy) the new container is left up
  for inspection. The PAPER PC does not use the script (`git pull` + `up --build`): nothing changes there.
- Container logs rotate: Docker's json-file driver keeps at most 5 x 20 MB compressed files per service
  (`x-logging` in `docker-compose.yml`, inherited by the local, staging and production overlays; Caddy and the
  off-site copier too). `docker compose logs --tail 100 backend worker` works as before; history older than ~100 MB
  per service is gone, which is the point. Roll-out: run the full `docker compose ... up -d` once, off-hours, after
  pulling this release - every container (Postgres and Redis included) is recreated once because its logging
  options changed. `deploy.sh` alone recreates only postgres/redis/backend/worker/frontend, and it does so on its
  first run after the merge (a short database/Redis restart while the old API still serves), so run that first
  deploy off-hours too; `backup`, `caddy` and `offsite` need the full `up -d` to pick the options up.
- CI has a `lint` job next to the test job: ruff (`backend/ruff.toml`, the rule set the code is clean on), mypy
  (`backend/mypy.ini`, the packages that are clean - add one when you make it clean), bandit (medium severity and
  confidence or worse, `app/` only) and a gitleaks scan of the pushed commits (`.gitleaks.toml`). A red `lint`
  job blocks a merge like a red test. Run them locally from `backend/` with `ruff check app tests`, `mypy`,
  `bandit -q -r app -ll -ii` (`pip install ruff mypy bandit`, pinned versions in the workflow).
- Every GitHub Action in both workflows is pinned to a commit SHA with the tag in a comment; bump by editing
  both. Dependabot-style tag updates are not automatic.
- RSS/Atom feeds are parsed with defusedxml (new dependency, in the image on the next build).

### 1.6ab-R AI research studies (H-C3c)
- **What it is.** The research loop is off by default (flag `ai_research`). It runs in its own process,
  `research-worker`, which is also off by default:
  ```
  docker compose --profile research up -d research-worker
  ```
  Without that process, studies stay "queued".
- **What it does.** It runs one study at a time; each organisation can have one study queued or running. Every draft
  is a backtest on server bars, with the sealed holdout cut off. It places no orders and saves no strategies.
- **Watch.**
  - `research_studies.status`.
  - A study `running` with no heartbeat for 20 minutes becomes `interrupted` on the worker's next step. Its trials
    are kept.
  - Model spend shows on the study (`usage`) and in the usual AI metering.
- **Stopping.** Stop the service. A study cut off mid-way becomes `interrupted`; nothing else is affected.

### 1.6ab-7 AI Copilot approvals and hand-offs (P0.8-A)

- An approved AI **exit** that the broker did not complete now shows **FAILED** with the reason (the position is still
  open - close it from the Positions page); the rule may propose again. A LIVE exit squares off at the broker the trade
  went through; without a usable session it is FAILED, never booked closed.
- Approving a proposal on a **LIVE** deployment or position on the web needs the authenticator (same step-up as
  creating a LIVE deployment, when `require_mfa_for_live` is on). PAPER proposals are unchanged.
- Strategist "Save as a strategy" and the interview's "Deploy in PAPER" now need the **risk checkbox** (maximum loss
  per trade) and go through the server's candidate: a candidate without a simulation/backtest with trades, or one that
  fails the compliance checklist, is refused with the reason. Candidates expire after 7 days - build again.
- **Telegram approvers**: on the Telegram card list who may press Approve/Reject as `<Telegram user id> <team e-mail>`
  (one per line). Without the list, only a *private* chat with a whitelisted chat id decides, as the owner; in a group
  every other member is refused ("not an authorised approver") and the refusal is audited. The PAPER PC's private-chat
  setup keeps working unchanged. Find a Telegram user id with @userinfobot.
- News: a stock at its upper/lower circuit is company news (severity 3), not a market halt; a severity-5 pause is
  proposed only for a deployment the news names (or an index deployment for index-wide news).
- Migration `e6f8a0b2c4d6` (new table `ai_candidates`, one open proposal per rule, `custom_` -> `custom:` on stored
  deployments) on the next off-hours `git pull` + `up -d --build`; nothing else to do. If two open proposals for one
  rule already exist (the race this closes), the migration marks the newer one EXPIRED ("Duplicate open proposal")
  and keeps the oldest.
- Expired candidates nobody adopted are deleted by the retention job after `RETENTION_AI_CANDIDATES_DAYS` (default 30);
  adopted and deployed candidates are kept.

### 1.6ab-8 What the AI may say (P0.8-B)

- Every AI-written text (market thesis, Copilot answers on the web and in Telegram, the knowledge guide) is checked
  before you see it: every number must be one of the platform's own figures (same sign; `25k` and `1.2 lakh` count as
  the full number) and every symbol must be one the facts or your question named. A text that fails gets one rewrite
  and then the rule-based text with a note such as "AI answer not used (numbers not in the facts: 26000)". Nothing
  else changes - the rule-based text was always there.
- News headlines reach the thesis model inside an `untrusted_data` block and no number from a headline is accepted,
  so a headline that "instructs" the model cannot change the thesis. If you see many "not used" notes, the provider
  is inventing figures: switch the model under Settings or stay on the rule-based text.

### 1.6ab-9 AI models, spend and the monthly budget (P0.8-C)

- **Which model does what**: the operator sets the models in the host `.env` - `AI_ANTHROPIC_STRONG_MODEL` (writes
  strategies, strategist proposals, scanner plans) and `AI_ANTHROPIC_FAST_MODEL` (narration, Copilot and knowledge
  answers, the thesis text) and, from P0.10, `AI_ANTHROPIC_CHEAP_MODEL` (default `claude-haiku-5-5`: news
  classification and scanner reads - frequent, short jobs); the same three for OpenAI (`gpt-4.1-nano` for the cheap
  tier). The model an organisation types under Settings > AI provider replaces the strong one for that organisation
  only (leave it blank to follow your default); the fast and cheap tasks always use the operator's models. The
  Settings card lists every task with its tier, model and an estimated cost per call. When the budget is spent, Copilot
  and guide answers carry the note "AI not used (the plan's monthly AI budget ... is spent)".
- **Spend**: every call is recorded (calls, tokens, USD at list price, INR at `AI_USD_INR_RATE`, default 84) per
  organisation, feature and model; the card shows this month's figure against the plan's budget. The Pro plan has
  1,500 INR a month, Business 10,000 (`AI_BUDGET_INR_PRO`, `AI_BUDGET_INR_BUSINESS`; 0 removes the cap). When the
  budget is spent the organisation gets the rule-based answers with a note until the 1st - nothing stops, nothing is
  charged beyond the budget. New model prices: `AI_MODEL_PRICES_JSON='{"model-prefix": [input, output, cache_read]}'`
  (USD per million tokens); an unknown model is priced at the provider's most expensive row and marked estimated.
- **Platform AI key (M72 PR-1b, flag `ai_platform_key`)**: the operator's own key answers a user-started AI call when
  neither the user nor the organisation has a key of its own. Host only: `AI_PLATFORM_PROVIDER` (anthropic / openai) and
  `AI_PLATFORM_API_KEY_FILE` (or `AI_PLATFORM_API_KEY`); the key is never logged or returned. Limits are Not set until the
  owner sets them in Admin › Controls › Limits (or `AI_PLATFORM_BUDGET_INR_<PLAN>`, `AI_PLATFORM_USER_DAILY_CAP_INR`):
  a plan with no budget, or no per-user daily cap, gets the rules. Both are checked before every call; a cap reached is a
  hard stop, then the rules. A user may set a lower daily limit of his own in Settings › AI. Platform spend is metered on
  the `ai_platform_*` metrics and never counts against the organisation's own-key budget.
- **Live eval of the Copilot planner (M72Q-7, one paid run the owner starts)**: `/opt/atp/deploy/atp.sh ai-eval` runs
  `python -m evals.planner_live` in the backend container - the 60 trader questions and the safety block through the real
  planner, with the recorded synthetic tool results (no user data leaves) and the server's own AI key (`--key auto`: the
  platform key, else the one organisation with a saved key; `--key org --tenant ID` picks one). It prints the eval table,
  the score per job, the safety result, tokens and cost per answer and the total (USD and INR). No call cap; nothing is
  written. `--offline` checks the wiring with a fake model at zero cost. Long run: start it detached
  (`nohup ... > /tmp/atp-ai-eval.log 2>&1 &`) and read the log.
- **Cut-off answers**: an answer the model could not finish inside its token budget is retried once with a bigger
  budget and otherwise dropped with "cut off" in the note - you never see half a sentence presented as the answer.
  Long drafts get a longer timeout (`AI_TIMEOUT_PER_1K_SECONDS`, default 8 s per 1,000 tokens of budget, on top of
  `AI_PROVIDER_TIMEOUT_SECONDS`).
- **Rate limit (H-C1 b)**: every `/api/ai/*` and `/api/scanner/ai/*` call costs units, by default 1. The heavy jobs cost
  more: a strategist build costs 20, and an interview plan or market-memory refresh costs 10. Units are counted per
  user and per organisation in a 60 s window (`AI_RATE_WINDOW_SECONDS`). The defaults per plan are:

  | Plan | User | Organisation |
  |---|---|---|
  | free | 60 | 120 |
  | pro | 180 | 600 |
  | business | 360 | 3,000 |

  - Change the limits with `AI_RATE_LIMITS='{"pro": {"user": 180, "tenant": 600}}'`; a value of 0 means no limit for
    that scope.
  - Change the costs with `AI_RATE_WEIGHTS='{"POST strategist/build": 20}'`.
  - A refusal is 429 with `Retry-After`, and is counted in `atp_ai_rate_limited_total{scope}`.
  - Counts live in Redis when it is configured; otherwise they live in the process.
- **AI call log and personal data (H-C1 e, DPDP)**:
  - Every LLM call stays in `llm_calls` for the audit; the rows are never deleted.
  - The stored copy has personal data masked: e-mails, mobile numbers, PAN, Aadhaar, and account/client ids written
    after a label. The SHA-256 hashes are of the original text, so a trader's own copy can still be matched against the
    record.
  - When a trader uses "Forget everything" (Copilot profile delete), or an account is erased, the text of that person's
    calls is replaced with `[erased]`. The row, hashes, model, prompt version and cost stay.
  - `RETENTION_LLM_TEXT_DAYS` scrubs older text to `[expired]` in the daily retention run. The default is 0, which
    keeps the text: removing it is your data-retention decision. Write the period you choose into your privacy notice.
- **Output filter (H-C1 c)**: every Copilot, guide and thesis text is checked on the server before it is shown.
  - Guarantee and advice wording blocks the AI text: "guaranteed", "sure-shot", "risk-free", "I recommend", "you
    should buy" and the like. The lists are English (the owner's ruling: ATP answers in English, so the Marathi /
    Hindi Devanagari lists were removed; an AI text in another script is withheld outright), plus a fixed `romanised`
    list of Hinglish advice in Latin script ("abhi kharido", "pakka profit"), read always - ASCII text is not caught
    by the script check. A ticker or instrument name followed by a romanised trade verb ("RELIANCE kharido",
    "HDFCBANK le lo", "SBIN vika", also past a few linking words, an option tag or a price: "RELIANCE ko jaldi se le lo",
    "BANKNIFTY CE le lo", "RELIANCE 2900 par le lo") is a blocked call too. The model gets one retry; after that the
    rule-based answer is shown with a note.
  - A text that names a specific buy/sell level is kept, with an educational line (English) added.
  - The word list is data, in `backend/app/ai/data/advice_terms.json`. To use your own list, point
    `AI_ADVICE_TERMS_FILE` at a copy.
  - Results are counted in `atp_ai_output_filtered_total{kind, where}`.
- Prometheus: `atp_ai_tokens_total{provider,model,kind}` and `atp_ai_cost_usd_total{provider,model}`.

### 1.6ab-11 English dashboard and AI answer language (P0.9)

- The dashboard is English only, and so are the AI's written answers (the owner's ruling, Oct 2026: the per-user
  answer-language choice and the Telegram reply in the message's language were removed - every AI answer is English,
  whatever language the question is written in; only the strategy interview is trilingual). `GET/PUT /api/ai/preferences`
  answer English only; a `PUT` from an older client is accepted and ignored (nothing is stored).
- Migration `a1c3e5f7b9d2` adds `users.ai_language` (constant default, no table rewrite). Run it outside 09:15-15:30 IST.
- Migration `e6b8d0f2a4c7` (EO-4) drops `users.ai_language` again. It is not ROLLBACK_SAFE: a code-only rollback past it
  is refused (the previous release still maps the column); use `rollback --restore-db`, or run `alembic downgrade
  c4e8a2d6f0b1` first, which re-adds the column (NOT NULL, default `en`, no table rewrite). Run it outside 09:15-15:30 IST.
- Sample-data figures are stamped and blurred on purpose; switch Data to broker candles to read real numbers.
- Optional: the per-call cost estimates on the AI provider card use `AI_USD_INR_RATE` and `AI_MODEL_PRICES_JSON` (P0.8-C).


### 1.6ab-13 Dated costs, NSE contracts and order-safety switches (Trade port)

- Paper and backtest charges use the statutory rates of each trade's own date (STT 0.15% on option sales from
  1 Apr 2026). Contract notes still replace the estimate. NIFTY's fallback lot is 65; the live instrument master wins.
- New optional `.env` keys, all off by default (no LIVE behaviour changes until an operator sets them):
  `ORDER_MARKET_PROTECTION_PCT` (1-25: Upstox `market_protection` on MARKET / SL-M orders),
  `LIVE_STRICT_WING_FILL=true` (a multi-leg entry sends its shorts only after every wing filled in full, else unwinds),
  `BACKTEST_HOLDOUT_START` (e.g. `2026-04-01`: the optimizer never uses bars from that date on).
- No migration in this release.

### 1.6ab-14 G-LIVE order switches (all off; LIVE unchanged until set)

| Key | Off (today) | On |
|---|---|---|
| `LIVE_MARKET_PROTECTION` | Zerodha sends no `market_protection`; Upstox only `ORDER_MARKET_PROTECTION_PCT` when set | every Zerodha / Upstox MARKET and SL-M order carries `market_protection` = `ORDER_MARKET_PROTECTION_PCT` (1-25) or `-1` (the broker's automatic band); limit / stop-limit orders never do |
| `LIVE_UPSTOX_OPTION_STOP_LIMIT` | Upstox protective stops are SL-M everywhere | on option contracts the stop is SL (stop-limit): trigger = the stop, limit = trigger -/+ `STOP_LIMIT_BAND_PCT` (sell stop below, buy stop above, at least one tick); stocks / futures keep SL-M |
| `LIVE_EXIT_IF_NO_STOP` | a stop that fails at entry or cannot be re-armed raises a CRITICAL alert; the software stop keeps watching | when the broker **clearly rejected** the stop (`BrokerOrderRejected` or a 4xx other than 408/429), the position is closed at once with a market exit and ONE CRITICAL alert says whether the exit worked. Not after a timeout / 5xx / rate limit (the stop may be standing - a market exit next to it could later open a position the other way), not while the tenant is broker-uncertain, not while the exchange is shut, and at most 3 tries per position (alerts on a 30-minute cooldown). An exit also skips cancelling a stop the broker already rejected / cancelled / expired / lapsed (a fresh book read must agree; a stop still working is cancelled first), which today fails and blocks the exit. An exit order that errors without a clear answer flags the tenant broker-uncertain (no further tries until reconciliation passes) |
| `STOP_LIMIT_BAND_PCT` | empty = 1%, nearest tick | the stop-limit band, 0-20 (Zerodha options today; Upstox options with the switch above), rounded outward (down for a sell stop, up for a buy stop) and at least one 0.05 tick past the trigger (a sell stop triggered at 0.05 keeps 0.05: there is no lower price). An unreadable value is ignored with a warning |
| `STOP_REARM_MAX_REJECTS` (default 3) | - | the stop guard counts, per position, re-armed stops the broker accepted and then REJECTED (it reads each new stop once right after placing it). After this many in a row it stops re-arming: with `LIVE_EXIT_IF_NO_STOP` the position is closed as a clear rejection, otherwise one CRITICAL "Stop keeps being rejected" (30-minute cooldown). A stop cancelled by hand or lost breaks the streak. Every protective stop trigger (entry, re-arm, trailing modify) is put on the 0.05 tick (the registry tick for MCX / crypto), rounded away from the market |

With `LIVE_UPSTOX_OPTION_STOP_LIMIT` or `LIVE_EXIT_IF_NO_STOP` on, an exit nets off what a stop-limit already filled
(as last read before the cancel - the exit goes out at once, it never waits on the cancel to settle): only the rest is
sent at market, and the booking blends both fills. A fill the stop made after that read is read once the exit is out,
netted in the booking, and flags the tenant broker-uncertain so reconciliation settles the excess the exit sent. If the stop's fill cannot be read, the full quantity goes out and the tenant is flagged broker-uncertain for reconciliation. A trailing
stop that was placed as SL-M before the Upstox switch went on is modified as SL-M (its type is kept).

A stop-limit that triggers but does not fill (price gapped through the limit) is still covered: the position monitor's
software stop sees the price past the stop on its next cycle (`WORKER_CYCLE_SECONDS`, default 60), cancels the
broker stop and sends a market exit (with `market_protection` when that switch is on). Suggested order of switching
on, in PAPER first: `LIVE_MARKET_PROTECTION`, `LIVE_UPSTOX_OPTION_STOP_LIMIT` with `STOP_LIMIT_BAND_PCT=3`, then
`LIVE_EXIT_IF_NO_STOP`. Upstox's `-1` (automatic) value is from its market-protection announcement; check it once
against the Upstox sandbox before switching `LIVE_MARKET_PROTECTION` on for Upstox.

### 1.6ab-12 Interview language, data age and the cheap AI tier (P0.10)

- The strategy interview is the one bilingual screen (English line + muted Marathi line; the Hindi bundle's slot is
  `INTERVIEW_HI`); everything else is English. The second line is switched on the interview screen itself (not in
  Settings). CI: `scripts/check_english_only.py` fails on Devanagari anywhere else in the repository.
- Today's market shows how old the market memory is; a stale read (older than 45 minutes while the market is open,
  20 hours while it is closed) or placeholder figures are stamped and their prices hidden. "Read now" (with a broker
  session) or the worker refreshes it.
- Risk settings never change with experience; until an organisation saves its own, the briefing labels them
  "Default risk settings (not set yet)".
- New optional `.env` keys: `AI_ANTHROPIC_CHEAP_MODEL` (default `claude-haiku-5-5`) and `AI_OPENAI_CHEAP_MODEL`
  (default `gpt-4.1-nano`) for news classification and scanner reads. No migration in this release.
### 1.6ab-10 AI Copilot compliance switches (P0.8-D)

- **First use**: every user sees the AI Copilot acknowledgement once (not a SEBI-registered adviser; explains rules and
  data; the decision is yours) and ticks it; until then the AI routes answer 428. A new version of the text (changed
  in `ai/compliance_terms.py`) asks everyone again. Acceptances are in `ai_acknowledgements` and the audit log.
- **External AI provider**: the owner must tick the data-sharing consent on the AI provider card before Anthropic or
  OpenAI can be saved. Switching to the rule-based provider is the opt-out. **After this update an organisation that
  already had Anthropic/OpenAI saved answers from the rules** ("AI not used: data-sharing consent") until its owner
  opens Settings > AI provider once, ticks the consent and presses Save. Nothing else changes; the key stays stored.
- **Telegram**: free text, `/brief` and `/thesis` answer "accept the acknowledgement in the web app first" until that
  user accepted it; `/positions`, `/risk`, `/news`, `/levels`, `/why` and the approve/reject buttons work as before.
- **Flags (Admin > Feature flags), both OFF by default**: `marketplace_ai_listings` (AI-drafted / strategist strategies
  may be listed for sale - keep off until the SEBI research-analyst question is settled) and `thesis_stock_targets`
  (show price reference levels and a confidence % for a single stock - in the thesis, the strategist market study
  and as a score in the AI scanner read; indices always show them).
- **Records kept for good**: decision to confirm with the lawyer - `llm_calls` keeps what a user typed to the AI for
  good (regulatory record), so a DPDP erasure request (`DELETE /api/ai/profile`) does not remove those rows.
- **Records kept for good**: `llm_calls` (every AI prompt and answer) and `ai_acknowledgements` are never touched by
  retention. They grow with use; include them in backups (they are, with the rest of the database).
- Migration `f7a9b1c3d5e7` (two new tables, nothing changed in existing rows) on the next off-hours pull + build.

### 1.6ab CoinDCX setup (Phase AK)

- **Create an API key** in the CoinDCX web app (Profile > API dashboard) with trading permission and,
  ideally, your server's IP allow-listed. **Enter under Settings > Add / update broker credentials**
  (broker `coindcx`): API Key and API Secret. Nothing goes in `.env`.
- Press **Log in** once (it proves the key with `users/info`) and then **Read-only check**. The key does not
  expire daily, so no morning login is needed; revoke it on the CoinDCX side to end the session.
- Symbols are the market names (`BTCINR`, `ETHINR`); deployments use exchange `CRYPTO`, which trades 24x7 on
  the worker's crypto clock (Phase O2). Quantities are fractional and floored to the market's step.
- Protective stops are stop-limit orders with the limit 0.5% past the trigger (CoinDCX has no stop-market);
  in a gap the fill may be worse than the trigger, exactly as with an exchange stop-market.
- Verified against a mocked transport only; the Read-only check on your key is the first live confirmation.

### 1.6ac Reconciliation with several broker accounts (Phase AL)

- Each account is compared with its **own** session; the `[label]` on a mismatch line names the account at
  fault. Trades recorded before accounts existed count against the broker's default (★) account.
- **Reconcile <broker>** on the banner runs every account at that broker and settles the block on the joint
  result; `POST /api/reconciliation/{broker}?account_label=` checks one. A LIVE block lifts only when every
  account with a usable session is clean; an account whose positions cannot be fetched keeps it standing.

### 1.6ad Live charts and the broker's quote budget (Phase AN)

- A chart with a live badge polls `GET /api/market-data/ltp` every 5 s while its tab is visible: one quote request per
  chart per 5 s through the tenant's own session (Dashboard market pulse: one per symbol; Positions: the underlying and,
  for an option or future, the contract too). Candles behind a chart refresh every 60 s and are cached 60 s platform-wide.
  With streaming on (Phase S) the tick cache answers and no REST call is made.
- A `stale` badge means the broker's quote carries an exchange timestamp older than the Phase G1 limit (market closed,
  or a feed lag); the price is still shown with its age. The worker's exit logic never uses this endpoint.

### 1.6ae First PAPER day: the check script, the EOD summary, the production overlay (Phase AX)

- **`backend/scripts/first_paper_day_check.py`** (run inside the backend container, see `docs/GO_LIVE_RUNBOOK.md` §3):
  read-only. It prints the platform and organisation checklists narrowed to a PAPER day, today's NSE session, the
  read-only broker smoke test on every stored session with a VALID token, whether the active deployments are being
  evaluated (market open only) and, with `--send-test-alert`, one test message per alert channel. Exit 0 = no ❌,
  1 = a blocker, 2 = no organisation could be chosen (`--tenant <id|email>`). `--no-smoke` skips the broker probes,
  `--json` prints the report; the report is English. Nothing it does changes trading state.
- **EOD summary**: from 15:35 IST on weekdays the worker raises one `EOD_SUMMARY` notification per organisation with an
  active/paused deployment or a trade today: signals, entries, exits with reasons, net P&L of the day's exits,
  positions still open after the square-off, reconciliation state, worker errors. INFO on a clean day, WARNING when
  a position is still open, a deployment is paused or erroring, or the organisation is broker-uncertain - so a
  Telegram channel with a WARNING floor gets it only when something needs a look; set the floor to INFO to get it daily.
- **Production overlay** `docker-compose.prod.yml` (ADR-0011): Caddy on 80/443 with an automatic certificate for
  `DOMAIN`, the API bound to 127.0.0.1, the frontend unpublished, `/metrics` hidden from the internet, and the `offsite`
  service copying `backups/` and the WAL archive to an S3-compatible bucket hourly (`scripts/backup/offsite_sync.sh`;
  `--once` for a manual pass). `scripts/deploy.sh production` includes the overlay. Needs Compose v2.24+.

### 1.6af Live news feed (Phase BB)

- **Off by default.** Admin console > Feature flags > `news_feed` turns the worker's fetch on. Then News & Events >
  Live feed sources: RBI and SEBI are on; NSE/BSE announcements and publisher RSS stay off until you have read the
  terms note on each (`docs/DATA_SOURCES.md`) - turning one on is audited as `news_feed_source_toggled`.
- **What arrives**: `news_events` rows marked "unverified feed" with the headline, the publisher's link and the
  time. Keyword severity >= 4 raises a `NEWS_ALERT` (WARNING; CRITICAL at 5) to every organisation with a
  deployment or a Copilot profile; set the Telegram floor accordingly. No trade changes on its own.
- **Proposals**: the monitoring agent proposes REDUCE_RISK (severity 4) or PAUSE_DEPLOYMENT (5, new entries only)
  only when the organisation's own AI classification says so or two sources report the same event; one per
  event per organisation; approve or reject under AI Copilot like any other proposal.
- **Cadence**: 15 minutes; 5 minutes within an hour of a global macro event on the Risk page calendar (add RBI
  MPC / Budget / FOMC / CPI days there with "global event" as the operator).
- **AI classification** uses each organisation's own key (Settings > AI) and is metered as `ai_news_classify`;
  `POST /api/news-feed/classify` runs it on demand. No key: keyword classification only.
- **Retention**: `RETENTION_NEWS_FEED_DAYS` (default 365) for feed rows; manual cited entries are never deleted.
- **Fetch failures** show on `GET /api/news-feed/status` (`last_run.errors`) and on the sources card; a feed that
  is down is skipped, the others continue.

### 1.6ag Market sentiment score (Phase BC)

- **Where**: Copilot > Market memory card (gauge, components, news score) and the daily brief chip; the plan's
  market-background lines. Captured every 15 minutes with the market memory through the organisation's broker
  (option chain + heavyweight quotes: one chain call and one resolved quote per heavyweight per read, inside the broker budget).
- **Reading it**: +25 and above = risk-on, -25 and below = risk-off; `coverage` says how much of the weight had
  data (no broker session = no PCR/breadth; the global cues and VIX still count). A struck-through component had
  no data and the weights renormalised - the score is never padded with zeros.
- **Tuning**: `SENTIMENT_WEIGHTS='{"pcr":0.3,"vix":0.2,"breadth":0.2,"global":0.2,"fii_dii":0.1}'` in the
  environment overrides the defaults per deployment; restart the worker and API.
- **FII/DII**: off until `FII_DII_SOURCE` names a source whose terms allow automated use (`docs/DATA_SOURCES.md`);
  until then the component shows "off" and is simply missing.
- **News score** is reported separately (unverified feed items, Phase BB); it is not part of the market score.
- Sentiment never gates an entry or an exit; it is background for the Copilot's plan and the Phase BD thesis.

### 1.6ah Two-way Telegram (Phase BE)

- **Default off.** Platform admin enables the `telegram_inbound` flag (Admin > Controls); then the organisation's
  owner, in Settings > Alert delivery > Telegram, turns "Two-way Telegram" on, saves the allowed chat ids and
  presses **Register webhook** (HTTPS domain required - the production overlay's Caddy provides it). The alert
  chat id is always allowed; add other chats sparingly.
- **What it does**: chat commands (`/brief /positions /risk /news /levels /thesis /why`, or a question) answered
  as the owner; Approve/Reject buttons on monitor proposals for **PAPER** deployments and only for pause /
  reduce-risk / review-strategy. Exits and anything LIVE never get buttons - decide those on the web with the
  authenticator. A button is single-use and expires with the proposal (24 hours).
- **Rotating**: turning inbound off and on again generates a new secret; press Register webhook afterwards.
  Removing the Telegram channel removes inbound with it. Telegram's `setWebhook` answer is shown verbatim.
- **Audit**: `telegram_decision`, `telegram_callback_replayed`, `telegram_inbound_rejected` and ignored-chat
  entries in the audit log; usage metered as `telegram_inbound`.
- **If messages stop arriving**: check the webhook URL in Settings matches `FRONTEND_URL`, that the flag is still
  on, and the audit log for rejected secrets (a bot token change at BotFather needs the channel re-saved and the
  webhook re-registered).

### 1.6ai Market thesis (Phase BD-lite)

- **Default off.** Platform admin enables `market_thesis` (Admin > Controls); then the Copilot "Today's market" tab
  shows the thesis card and `/thesis NIFTY 50` works on Telegram. Needs a market read of the symbol (watchlist +
  market memory).
- **Shadow only.** The "shadow x0.75 (not applied)" figure is what a reduce-only overlay would have done. Nothing
  reads it: no order, risk check or deployment changes because of a thesis. Turning the overlay into a real control
  is a separate, later decision taken on the scoreboard, not a setting anyone can flip today.
- **Scoreboard**: the worker scores every thesis against the symbol's last read of the next session (+1 / -1 / 0).
  Judge the thesis by `hit_rate` over at least a few weeks before trusting it for anything.
- **Narrative**: `?narrate=true` uses the organisation's own AI key (metered as usual) and is refused when the model
  invents a number; the rule-based sentences are then shown and `narrative_note` says why.
- **Weekly scoreboard (Phase BD-2)**: every Friday after the close a `THESIS_REPORT` notification (INFO; reaches the
  Telegram/email channels whose floor is INFO) states the week's hit rate per symbol and the average shadow
  multiplier. A Friday the worker was down is not caught up (the report is Friday-only); the API preview always works. Read it
  for a few weeks before thinking about the overlay. `GET /api/ai/thesis/report` shows the same text any day.
- **News feedback (Phase BD-2)**: on the News page, members mark feed items useful / noise / wrong direction. After
  10 verdicts the organisation's "news trust" (useful share, floor 0.25) scales the thesis news factor; the
  summary endpoint shows precision per source so a noisy source can be switched off in the feed settings.

### 1.6aj Phase 0 PAPER-only lock (Z10-c)

- **Nothing to set at install (Phase 0 deployment defaults, owner decision 2026-10-12).** The Hostinger host runs with
  `ATP_DEPLOYMENT_PHASE=phase0`, the default of `docker-compose.hostinger.yml` and `deploy/atp.sh`, so `.env` needs no
  line for it. On first boot the API and the worker store `live_trading` OFF and `phase0_paper_only` ON wherever the
  stored flags have no entry yet (`app/platform/deployment_phase.py`). Each write is audited as `phase0_flag_seeded`.
  Until the seed is written, the same two values are the defaults in this mode.
- **Re-boots never override.** Only absent entries are seeded. An owner's later change in Admin Console -> Feature
  flags (Admin > Controls) survives every restart and deploy. Changing the flag there is an audit event
  (`feature_flag_set`).
- **Outside that mode nothing changes.** The flag is OFF by default (`DEFAULT_OFF_FLAGS`), `live_trading` stays a
  kill flag, and no other code, migration or script turns either one on. `atp.sh` itself never writes a flag; its
  deploy check accepts only the release's own first-boot seed of an absent entry.
- **Why the exception is allowed.** It is the owner's instruction (Admin is unreachable before the app is deployed),
  it applies only in this explicit deployment mode, and both writes are restrictive: they refuse new LIVE entries and
  new LIVE deployments. Neither can enable anything, place an order or block an exit.
- **Admin refusal (Phase 0 mode).** Turning `live_trading` on, globally or for any organisation, is refused with
  "live_trading cannot be turned on while phase0_paper_only is on" (HTTP 400, audited as `feature_flag_refused`).
  To leave Phase 0, the owner first sets `ATP_DEPLOYMENT_PHASE=standard` and turns `phase0_paper_only` off
  ([runbook section 4a](deploy/HOSTINGER_RUNBOOK.md#4a-phase-0-flags-nothing-to-set)).
- **What it does while on**: every new LIVE entry is refused - at the entry checks (the reason
  "Phase 0 PAPER-only lock ..." on the order trail, single-leg and multi-leg alike) and again at the broker-order
  chokepoint in the worker (an order the platform built as an entry - `intent` ENTRY - gets a REJECTED answer without
  reaching the broker; the worker re-reads the flag every cycle). PAPER is
  untouched. With the flag off and tenants listed in its allow-list, only those tenants are locked.
- **No LIVE entry path started**: the worker does not evaluate a LIVE deployment while the lock holds for its
  organisation. Its `last_error` reads "... (LIVE deployment not evaluated)", and the worker log says so at start-up
  and whenever the lock changes. `GET /api/system/health/deep` shows `checks.live_paths` (`paper_only`, why, and
  "exits: always allowed"). No long-lived LIVE order client exists to stop: broker sessions are opened per cycle
  for market data and exits only.
- **What it never does**: block an exit. Exits of an existing LIVE position, protective stops, stop moves and cancels
  pass (ADR-0004). It does not change the `live_trading` flag (still a kill flag, ON by default - owner decision
  Z10-3 / Z11-7), a plan, or a deployment's mode.
- **Check it**: Admin Console -> Feature flags shows the state. `atp.sh health` prints
  "deployment phase: phase0" and passes with no owner action. A LIVE deployment's `last_error` and any refused
  entry's order trail name the lock.
- **"No API order path reachable"** (Phase 0 exit criterion) is also a test: `tests/test_z10c_order_paths.py` fails
  when any API route or process entry point can reach a broker order method outside its allow-list, and no API route
  may reach an entry at all. The allow-listed routes are exits only (close a position, emergency exit, an approved
  exit proposal).
- **Tenancy and scope sweep** (`tests/test_z10c_tenancy_sweep.py`): every `/api/*` route is classified in
  `tests/tenancy_route_table.py`; a new route fails the build until it is. The sweep then checks, for every route,
  that an anonymous call, a VIEWER, a member denied every scope, a member denied the route's catalogue scope and
  another organisation are refused as the class says, and that another organisation never sees or changes this
  organisation's rows. A member reporting 403 with `X-Missing-Scope: risk:write` (or another scope) after this
  release was denied that scope on the Team page - since Z10-c the denial is enforced. STRATEGY_CREATOR members no
  longer edit risk settings / limits or broker accounts (their role never had `risk:write` / `brokers:write`); an
  owner who wants that grants the scope on the Team page.

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
* **Short straddle / strangle** (Phase R) have **no max loss**. The deployment card says
  "undefined risk, sized off the stop": the lots come from `stop_credit_pct` of the credit, so a
  100% stop on a 230-point straddle risks 17,250 per NIFTY lot. Keep `max_lots` on these and
  watch the "Upper/Lower breakeven breached" exits; the broker's full SPAN+exposure margin
  applies and the margin cap sizes to it.
* **Long straddle / strangle / calendar** are debit structures: `option_position` BUY, the debit
  is the max loss and the sizing basis, target/stop are "% of debit" (stop at most 100). A
  `would be a net credit` refusal on a calendar means the far expiry quoted below the near one:
  stale or crossed quotes, check the chain before re-enabling.
* A **calendar spread**'s near leg expires first; the per-deployment entry cutoff and the
  square-off close both legs together, so the far leg is never left alone past the near expiry
  unless someone closes a leg by hand.

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

### 1.9 On-call runbooks and the status page (Z10-e, G7)

One page per alert. Each says what fired, what the platform already does on its own, what to check and what to do.
Server commands come from the Z11 deploy runbook, [`docs/deploy/HOSTINGER_RUNBOOK.md`](deploy/HOSTINGER_RUNBOOK.md)
(PR #186; its section 1 lists every `deploy/atp.sh` command). They are linked here, not repeated. Until #186 is
merged, the older `deploy/hostinger/status.sh` and `rollback.sh` blocks still work (`docs/DEPLOY_HOSTINGER.md`).

Phase 0 is PAPER only. Nothing below places, modifies or blocks an exit: exits are never gated (ADR-0004).

**Where to look first**

| Source | Who | What it shows |
|---|---|---|
| `/status` (`GET /api/status`) | anyone, no login; flag `public_status_page`, off by default | API, trading engine, market data (EOD date, NSE open, number of live sessions through users' own brokers), alert delivery lag, maintenance and incident banner. States and short sentences only. |
| `GET /api/system/health/deep`, `/health/dependencies` | operator (unauthenticated, no secrets) | database, migrations, worker heartbeat, Redis, broker circuits, broker-uncertain organisations |
| `deploy/atp.sh health` | owner, on the server | containers, API, HTTPS, release, key, flags, backups, host ([runbook section 10](deploy/HOSTINGER_RUNBOOK.md#10-health)); `OWNER ACTION NEEDED` (exit 10) names a flag to set |
| `backend/scripts/flag_posture.py` | owner (run by `atp.sh health` inside the API container) | the stored feature flags against the Phase 0 posture ([runbook section 4a](deploy/HOSTINGER_RUNBOOK.md#4a-phase-0-flags-nothing-to-set)): `live_trading` off, `phase0_paper_only` on (seeded at first boot in Phase 0 mode), and the deployment phase. Read-only. |
| `backend/scripts/session_check.py` | owner (run by `atp.sh deploy` / `rollback`) | whether any exchange session is open with open positions; a deploy or restart stops with exit 3 while it is ([runbook section 8](deploy/HOSTINGER_RUNBOOK.md#8-what-deploy-does-step-by-step)) |
| Prometheus rules `scripts/monitoring/prometheus-alerts.yml` | operator | each rule's `runbook` annotation points to a page below |
| Admin console | SUPER_ADMIN | incidents, maintenance mode, flags, beta rollout stage |

**The public status page.** Turn it on in Admin > Feature flags (`public_status_page`), the same owner step as the
other Phase 0 flags (runbook section 4a; `atp.sh` never changes a flag). The page and `atp.sh health` read the same
probes (`/api/system/health/deep`). It reads only the probes above
and `app/incidents`; it shows no error text, version, host, latency, user or organisation. Incidents scoped to one
organisation are left out. A platform-wide incident shows as "Incident" (WARNING) or "Major incident" (CRITICAL,
EMERGENCY), "investigating" (OPEN) or "monitoring" (MITIGATED), and its start time; its title and summary stay in the
admin console. The answer is cached for 15 seconds (an outage answer for 5), built by one request at a time, and rate-limited to 60
calls per minute per IP; turning the flag on or off clears the cache. With the flag off it answers a plain 404. While
the database does not answer within 2 seconds, or the flag cannot be read, the endpoint answers 503 with an outage body.

**Every incident.** Open one in Admin > Incidents for anything users notice (that is what puts the banner on
`/status`), set it to MITIGATED when the fix is in, and resolve it with the root cause. Downtime is measured from the
start.

#### 1.9.1 Data stale

*Fires:* Prometheus `MarketDataStale` (decisions refused on stale candles or quotes, SLO-4); `/status` shows Market
data "Degraded" when the newest EOD file is older than the last closed session; data-tier badges say "stale".

*Already happening:* signals on stale candles are skipped and logged on the deployment (`Skipped: market data stale`);
alerts on stale data are held back (BT1 `alerts_suppressed`, G5); an exit decision on a stale quote waits one cycle.
Nothing to reset when the data is fresh again.

*Check and act:*
1. Intraday (candles / quotes): is it one user or everyone? One user: their broker token (Settings > Broker session
   health; the daily login). Everyone on one broker: see 1.9.4. Everyone: the worker (1.9.2) and the clock on the host.
2. End of day: the nightly EOD job runs from 19:00 IST with the `history_backfill` flag on (HB1). Check
   `GET /api/admin/history-backfill/progress` for `blocked` / `failed` days. NSE sometimes publishes late; the next
   night's catch-up picks it up. To load it now: upload the day's file on that endpoint, or run
   `docker compose exec backend python -m app.market_lake.bhavcopy_backfill nightly` (docs/design/HB_HISTORY_BACKFILL.md).
3. If users are affected for more than a session, open an incident (WARNING) so `/status` says so.

#### 1.9.2 Worker stale

*Fires:* `TradingWorkerStalled` (heartbeat older than 180 s) or `TradingWorkerNeverSeen` (SLO-2); `/status` shows
Trading engine "Outage" during market hours, "Degraded" when the market is closed.

*Already happening:* the API keeps serving. Open PAPER positions are only re-evaluated when the worker runs again; no
order is sent meanwhile.

*Check and act:*
1. `deploy/atp.sh health` (one worker container, running and healthy). Restart the worker container only (the full
   `docker compose` file list is in the runbook's section 9), then follow section 1.7 above.
2. Read the worker log lines around the last heartbeat; `last_error` on `GET /api/system/worker-status` names a
   failing cycle step. A stopped heartbeat usually means the container is gone, not a cycle bug.
3. `WorkerLockLost` at the same time: Redis memory or a second worker (section 1.2b).
4. If it cannot be back before 15:15 IST, open an incident (CRITICAL) and say so on `/status`; PAPER positions can be
   closed by hand from the Positions page.

#### 1.9.3 Outbox backlog

*Fires:* `AlertOutboxBacklog` (more than 20 alerts waiting for 10 minutes) or `AlertDeliveriesFailing` (SLO-8);
`/status` shows Alert delivery "Degraded" above a 3-minute lag, "Outage" above 15 minutes.

*Already happening:* raising an alert only writes outbox rows, so nothing upstream slows down. Each delivery is retried
with backoff, then dead-lettered (`reason_code = dead_letter`) with its last error kept.

*Check and act:*
1. Is the worker running (1.9.2)? The worker drains the outbox every cycle; a stalled worker is the usual cause.
2. One channel or all? Telegram down, an SMTP password changed or a web-push key rotated shows in the failed rows'
   `last_error` (`GET /api/alerts/dead-letters` per organisation; `atp_alert_deliveries_total{channel,status}`).
3. Fix the channel, then retry the dead letters (`POST /api/alerts/deliveries/{id}/retry`, or the dead-letter list
   in the alert rules panel). In-app notifications were never affected.

#### 1.9.4 Broker down

*Fires:* many users' sessions at one broker failing at once (logins, quotes, candles; `atp_broker_calls_total{broker,
outcome}`), or, on the order path, `BrokerCircuitOpen` / `OpsThrottle429Burst` (SLO-5). Phase 0 sends nothing to a
broker's order API, so the last two should stay silent; if one fires, treat it as an incident (G6, "no API order path").

*Already happening:* live data through that broker (Tier A) stops updating for its users; the freshness checks mark it
stale and alerts on it are held back (1.9.1). Other brokers and EOD data are not affected. "Trade in your broker app"
links still open the broker's own app.

*Check and act:*
1. The broker's own status page or support channel. `GET /api/system/health/dependencies` shows each circuit.
2. If it stays down for more than a few minutes in market hours: open an incident (WARNING) naming no user, and
   optionally add the broker in Admin > Platform controls > Disabled brokers so the UI says LIVE is paused there.
3. When it is back: remove it from Disabled brokers, resolve the incident.

#### 1.9.5 Rollback

*When:* a deploy made things worse (health checks fail after the deploy, error budget burning, a feature broke for
users) and a fix forward is not minutes away.

*Before:* `scripts/session_check.py` (the session gate) refuses a restart while an exchange session is open with
open positions (exit 3); the migration guard refuses while NSE is open. Wait for the close unless the runbook's
override is accepted for that one run.

*Do:* follow the Z11 runbook, [section 9 "Rollback, and when a migration makes it unsafe"](deploy/HOSTINGER_RUNBOOK.md#9-rollback-and-when-a-migration-makes-it-unsafe):
`deploy/atp.sh rollback --dry-run`, then `deploy/atp.sh rollback` (code only), or `rollback --restore-db` when the
release's migration was not additive (since Z11 every migration declares `ROLLBACK_SAFE`; `False` means a code-only
rollback is unsafe). Then `deploy/atp.sh health`, which must end `health: OK`. Open an incident (`/status` shows it), and resolve it
once the fixed release is deployed again. The rollback rehearsal (a Phase 0 exit criterion) is in the same section.

#### 1.9.6 Staged beta rollout (50 -> 200 -> 500)

The beta cohort grows in stages. Sign-up through invite codes (flag `beta_cohort_gate`) stops at the effective cap,
min(stage, `BETA_COHORT_CAP`). Admin > Beta rollout shows the stage, the effective cap, the places used and the
headroom. **Moving up** is one stage at a time, needs a reason and is written to the audit trail
(`beta_cohort_stage_raised`). Move up only after the current stage has run a full week of trading days with: no open
incident, `/status` green, `deploy/atp.sh health` ending `health: OK` (flag posture included, runbook section 4a),
the runbooks above exercised at least once, and the rollback rehearsed. **Moving down**
(`beta_cohort_stage_lowered`) closes sign-up sooner; existing accounts are never touched. The `BETA_COHORT_STAGE`
setting (default 50) only applies until a stage has been set in the console. Details: `docs/BETA_COHORT.md`.

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

### 2.6 Legal pages, their acceptance and the support bot (Z10-f)

**Flag `legal_pages` (off by default).** While it is off nothing below exists for users: `/api/legal/*` answers 404,
sign-up ignores `legal_acceptance`, the form has no consent box, nobody sees the banner and the support bot leaves the
legal pages out. The admin API (`PUT /api/admin/controls/flags/legal_pages`) refuses `on` - or an organisation
allow-list - while any document still has an open `[PLACEHOLDER]`; send `"override_open_placeholders": true` to switch
it on anyway (audited as `legal_pages_placeholder_override`). Fill the placeholders and have the text reviewed first.

**Legal pages.** The Privacy notice (DPDP Act 2023), Terms of use and Risk disclosure are versioned Markdown in
`backend/app/legal/content/{privacy,terms,risk}.md`, each headed "Draft — to be reviewed by a lawyer before launch".
With the flag on they are public: `/legal/privacy`, `/legal/terms`, `/legal/risk` in the app (no sign-in, no app shell) and
`GET /api/legal/documents[/{slug}]`. The privacy notice does not copy configuration: retention days come from the
retention policy (section 2.3, `RETENTION_*`) and the breach-notice hours and log retention from `in_sebi.json`
(`IN-SEBI.golive.checklist`), filled in when the page is served. Company facts that do not exist yet stay as
`[SQUARE BRACKET]` placeholders (`open_placeholders` in the API response) until the company is registered.

To change a document: edit its file, bump its `version` (a date), ship it. Everyone is asked again on their next
sign-in - also when the text changes without a version bump (the acceptance is bound to the text's SHA-256).

**Acceptance.** With the flag on, sign-up and team join require the acceptance (400 `legal_acceptance_required`
without it): the form's box sends each document's version and the SHA-256 of the text it showed, and a version or text
that is no longer current gets 409 `legal_version_changed` (the form reloads the text - the documents are served
`no-cache` with an ETag). Accounts created before the flag was on, and everyone after a document changes, see a banner
after signing in: "I have read and accept" records it, "Later" hides it until the next sign-in (no limit on how often -
OPEN_QUESTIONS Z10F-7). It is a banner, never a modal or a route gate: positions, exits and every other screen keep
working. Each acceptance is a row in `ai_acknowledgements` (`kind` = `legal_terms` / `legal_privacy` / `legal_risk`,
`version`, `text_sha256`, IP, browser, `accepted_at`; kept for good) plus a `legal_accepted` audit row.

**Support bot** (flag `support_bot`, **off by default**; off = `POST /api/ai/support/ask` answers 404 and the help
button is hidden). How-to answers from `backend/app/support/content/help.md` and, while `legal_pages` is on, the legal
pages (help entries marked `requires: legal_pages` are left out while it is off). It refuses advice
questions (what to buy or sell, targets, tips, "will X go up" and the advice / guarantee words), questions about the
user's own positions, orders or P&L (it reads none) and requests to place, change, cancel or exit orders (it has no
tool that can). Below the confidence floor (`SUPPORT_BOT_MIN_CONFIDENCE`, default 0.5) it hands the question to the
feedback form instead of guessing. With the organisation's own AI provider configured (Settings > AI provider, data
consent accepted, the user has accepted the AI Copilot acknowledgement, `ai_copilot` on) the provider may reword the
matching help passage; the reworded text is shown only if it passes the advice / guarantee filter, the banned-word and
the numbers checks, else the help text is shown. Review H1 hardening: the question is normalised first (NFKC, no
zero-width characters, Cyrillic/Greek look-alikes mapped); advice is also recognised in Hinglish written in Latin script and
in spaced-out words (a question in Devanagari is not read for advice: it never reaches the model and gets the help
text or a hand-off, in English); order and account requests are checked clause by clause; a question with letters outside English
never goes to the model; and a model text is rejected when it has letters outside English, a ticker or number that is
not in the help passages (the question is not trusted), a trade verb the top passage does not use, a claimed action
("I closed", "has been placed"), or fewer than 80 % of its words from the passages. Personal data in the question is
masked before it is sent. There is no platform key: one organisation's key is never used for
another. Limits (checked after the flag, so a 404 spends nothing): the AI endpoints' per-user / per-organisation units
(`AI_RATE_WEIGHTS` "POST support/ask" = 1) plus `SUPPORT_BOT_RATE_PER_MINUTE` (default 20) per user; model tier: the
`support` task (cheap). Metric: `atp_support_bot_answers_total{outcome,source}`.

To add or fix a help answer, edit `help.md` (one `## ` question per entry, `keywords:`, `page:` = an app path). The
tests screen every entry through the advice filter.
