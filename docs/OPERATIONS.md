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
  `docker compose run --rm backup sh /scripts/verify_backup.sh latest`. CI runs the same
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
  `archive_timeout`, segments copied into the `wal_archive` volume). Copy that volume off the host
  with the dumps.
- **Weekly base backup**: `docker compose run --rm backup sh /scripts/base_backup.sh` (or from
  cron). It writes `backups/base/<stamp>/`, keeps the newest `BASE_BACKUP_KEEP` (4) and prunes WAL
  older than the oldest kept base backup. Alert when `LAST_BASE_BACKUP_OK` is older than 8 days.
- **Recover to an instant** (e.g. just before a bad bulk change at 11:04 IST):
  1. stop the API and worker (`docker compose stop backend worker`);
  2. `docker compose run --rm -v atp_pitr:/pitr backup sh /scripts/pitr_restore.sh latest "2026-09-28 11:03:30+05:30" /pitr`;
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
  `last_error` with the HTTP status.
- **Performance probe**: `python scripts/loadtest.py --base https://<api> --users 20 --seconds 30`
  after every release that touches the request path; paste the table into docs/PERFORMANCE.md.

### 1.6f Stop guard, tax report, currencies, drift gate, staging (Phase P)

- **Stop guard**: a WARNING "Protective stop re-armed" means the broker-side SL-M was missing or
  cancelled and has been re-placed; look at who cancelled it (audit `protective_stop_rearmed`).
  A CRITICAL "No broker-side stop" means re-placing failed (margin, session): the software stop
  still runs every cycle, but close the position by hand if the broker session is gone.
- **Tax report** (*Analytics → Tax report*): users pick the financial year and LIVE/PAPER/ALL and
  download the CSV for their CA. Rates live in `backend/app/tax/report.py::RATES`; update them
  in the same commit as a Finance Act change and note the date in the docstring.
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
  restarts the API only after the deep health check passes.

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
- **Wrong expiries in an old year**: conventions default to the exchange's current listings.
  Override `expiry_weekday` (0 = Monday) and `weekly_expiry` for the period being tested (NIFTY
  weeklies were Thursdays before September 2025; BANKNIFTY had weeklies until November 2024), and
  `lot_size` / `strike_step` when those differed.
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
  through the worker's own symbol translation), positions and today's order book. It never places, modifies or
  cancels an order.
- Green on all eight steps is the adapter's first live confirmation; do it once per broker before the first
  PAPER session and again after any adapter upgrade. A red `profile` means the token; a red `derivatives` or
  `contract_quote` means the scrip master or symbol translation, and its message names the contract.
- Each run is audited as `broker_smoke_test` with its summary.

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
