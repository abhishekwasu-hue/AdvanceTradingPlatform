# Production readiness order: the independent review (23 Oct)

The order `ATP_SESSION_PRODUCTION_READINESS.md` (worktree `wt-ready0`), reviewed against main `aa175fea` (#412) by a
separate agent that only read the code (CLAUDE.md "independent review"). Where this review differs from the order,
the finding wins until the owner says otherwise. Each finding should become an open question in
`docs/OPEN_QUESTIONS.md` with the default given here.

Context, not a finding: main CI is red at `aa175fea` (mypy, and the gitleaks full-history scan in
`secret-scan.yml`); this is being fixed separately. Several items below (A3 especially) depend on main being green
first.

**Overall:** the order is right that most of the machinery exists. In some places it understates what exists: the
STEP 0 list leaves out about fifteen built modules (R9). In a few places it overstates it: off-site backups are not
yet a working recovery path (R1, R2), "staging with the same images" does not exist (R4), and "billing stays OFF" has
no switch (R7). Four of its sentences contradict the code, the data terms or the product rule (R5, R6, R8, and the
DPDP 48-hour point under A5).

**Migrations the work will need (for the deploy lines):** none for A1–A3. A4: possibly one (`user_sessions`
`mfa_verified_at` is there; an absolute session lifetime needs a `created_at` check only, so no migration). A5:
probably one (consent records may reuse `ai_acknowledgements` as legal acceptance does: no migration). C11: yes
(prices / trial / referral in a table, GST invoice numbers). C12: yes (tickets). B8: yes (device tokens,
`app/mobile/devices.py` in MOBILE_APP_PLAN).

## Blocking findings and their resolution

| # | Finding | Resolution |
|---|---|---|
| R1 | **The WAL archive grows without a bound, is not encrypted, and is synced raw to R2.** Postgres archives every WAL segment (`archive_mode=on`, `archive_timeout=300`, `cp %p /wal_archive/%f`: `docker-compose.hostinger.yml:28-31`, `docker-compose.yml:21-22`). The only pruning (`pg_archivecleanup`, keep 4) is in `scripts/backup/base_backup.sh:26-30`, and **nothing schedules `base_backup.sh`**: it does not appear in atp.sh, compose, cron (`atp.sh:1300-1319`) or a workflow, only in `docs/OPERATIONS.md:61` as a manual step. So (a) the `wal_archive` volume only grows. Any write within 5 minutes forces a 16 MB segment; worker heartbeats mean that is roughly every 5 minutes, up to about 4.6 GB a day on a 100 GB disk. (b) Point-in-time recovery cannot work, because there is no base backup. (c) `offsite_sync.sh:21-22` `rclone sync`s `/wal_archive` to R2 **unencrypted**. Every row written (e-mails, journal text, broker metadata) would leave the host in clear, unlike the AES-256 + HMAC dumps. That would make the A1 "encrypted backup" claim false the moment R2 is switched on. The runbook says "Add no rule for `wal_archive/`: the sync prunes it" (`HOSTINGER_RUNBOOK.md:220-221`), but nothing prunes the source. Health checks disk % (`atp.sh:607-609`) but not the archive size. | **Before R2 is turned on (first A1 PR):** pick one. (i) Default: turn WAL archiving off on Hostinger (`archive_mode=off` in the overlay). The recovery point is then the daily dump plus the pre-migration dump, which matches what the runbook promises in practice. Exclude `wal_archive` from `offsite_sync.sh`. (ii) Or keep PITR, but schedule an encrypted, sealed weekly `base_backup.sh` from `atp.sh schedule`, encrypt each segment before it goes off-site (an `archive_command` that runs `openssl enc` + seal), prune the archive, and add "WAL archive size / last base backup age" to `atp.sh health`. Abhi pastes `atp.sh health` (disk %) at STEP 0 so we know how much has piled up. If the disk is filling, a one-off cleanup goes into the consolidated block (after 15:30 IST). Owner decision RD-1, default (i). |
| R2 | **Off-site backups cannot be restored if the server is lost: the keys exist only on the server.** `BACKUP_ENCRYPTION_PASSPHRASE` (dump + files encryption and the HMAC seal key, `scripts/backup/backup.sh:27-31`, `common.sh:51-53`) and `SECRETS_ENCRYPTION_KEY` (broker tokens, MFA secrets, AI keys) live only in `/opt/atp/.env`. The files archive deliberately leaves out `.env` (`atp.sh:1138-1150`: `.deploy` + `BACKUP_EXTRA_DIRS`). atp.sh never rotates the key and only keeps a fingerprint of it (`atp.sh:24-26`). If the host dies, every R2 object is ciphertext nobody can open. The order puts "encryption keys backed up separately" under A4. It belongs to A1, and A1 is not done without it. | A1 is done only when (1) Abhi keeps both values, plus `JWT_SECRET_KEY` and `POSTGRES_PASSWORD`, in his password manager **and** in one offline copy, never in git or chat; and (2) a restore rehearsal on a **clean machine** succeeds using only R2 plus those escrowed values (the CI staging stack or a throwaway VM: download → seal check → decrypt → restore → `verify_chain`). The A1 runbook step and READINESS_STATUS list both. A `health` line shows the key fingerprint date, so Abhi can see that his escrow copy matches. |
| R3 | **"Deploy only a commit whose full suites passed on main" cannot work with today's CI.** (a) Main is red. (b) Main's CI concurrency group lets only one run queue (`ci.yml:13-15`, `staging-stack.yml` concurrency: `cancel-in-progress` false on main). Each later push cancels the queued one: `3c7c043c`, `f6a43c10`, `a345cbd0` and `b5353a8e` all show CI **cancelled** on main. So most main commits never get a full result, and an exact-sha gate would refuse almost every deploy. (c) Today atp.sh only checks that the commit is on origin/main (`atp.sh:761`). | Build the gate in three parts. (1) Main CI must produce a result for every merge commit: put main pushes in a per-sha concurrency group, or add a `release-gate` workflow keyed by sha that `needs` every job (lint, backend, backend-lookahead, perf, frontend, container-scan, android, staging-stack acceptance + rollback). (2) `atp.sh deploy` reads that sha's combined check result from the **public** GitHub API without a token (the repo is public: no secret on the server). It refuses with a plain message ("this commit has not passed its checks; the newest commit that has: <sha>"). (3) An override `ATP_ALLOW_UNTESTED=1`, written into `releases.log` and shown by `health`. Do the A3 gate PR only after main is green; until then the deploy line stays as it is. |
| R4 | **"Staging first: the same images on docker-compose.staging" does not exist and would not fit.** Production builds its images on the host from the commit (`atp.sh:776`, `build --pull`; no registry), so no two hosts run "the same images", and `--pull` lets base layers drift. `docker-compose.staging.yml` has no Caddy, no Hostinger memory limits and no subdomain. atp.sh does not deploy it: the old `scripts/deploy.sh staging` does, from `deploy-staging.yml:43`, which runs only when `vars.STAGING_ENABLED` is set (`:24`, so every main run shows "skipped"). Its smoke test cannot fail (`:48` `\|\| echo`). On the same KVM 2 the production limits already add up to about 6.2 GB of 8 GB (`docker-compose.hostinger.yml`: Postgres 2g, backend 1536m, worker 1536m, Redis 512m, backup 512m, frontend 128m), so a second stack on that host would put the trading worker at risk. It would also share `/opt/atp/.env`, and with it the production keys. | Default (RD-2): **staging = the CI staging stack that already exists.** `staging-stack.yml` builds images from the commit, runs the acceptance flow, the rollback rehearsal and the 500-user load, and becomes part of the R3 gate. A real staging host is an owner cost decision. If he buys one: images pushed to GHCR by digest and pulled on both hosts (no `--pull` build in production), atp.sh on the staging host with its own `.env` and keys, a `staging.<domain>` Caddy site, and a smoke test that fails. Retire `deploy-staging.yml` and `scripts/deploy.sh` for production use. |
| R5 | **MFA step-up coverage is not what the order says, and the order's rule collides with the beginner-first rule.** The step-up is in `app/auth/dependencies.py:131-162`, not in `mfa.py`. (a) Storing broker credentials and broker logins use `ensure_live_step_up` (`brokers/routes.py:471,804,1026`, `brokers/connect.py:294`), which bites only for SUPER_ADMIN or when the tenant set `require_mfa_for_live`, and that defaults to False (`db/models.py:50`). So no ordinary user needs MFA to store an Upstox token today. "Required before any broker-credential action" would force every beginner to set up TOTP before connecting the one-year Analytics Token. (b) Some SUPER_ADMIN writes sit outside the MFA-gated admin routers: `PUT /api/news-feed/sources/{id}` and `POST /api/news-feed/refresh` (`news_feed/routes.py:46-56`); `POST /api/team/members/{id}/verify-email` (`platform/routes.py:213-214`); `PUT /api/compliance/golive/evidence` (`compliance/routes.py:76-77`); `POST` and `DELETE /api/market-holidays` (`market_data/routes.py:47-68`); and the reads `GET /api/system/encryption` (`platform/routes.py:156-157`) and `/api/audit-logs/verify` (`trading/routes.py:448-450`). (c) The step-up has no freshness: `mfa_verified_at is None` is the only test (`dependencies.py:143`). A refresh slides the session another 30 days each time (`auth/sessions.py:112`, `REFRESH_TOKEN_DAYS=30` in `config.py:75`), so one TOTP entry can cover admin actions indefinitely. | Default (RD-3), owner decision: MFA is **offered** to every user. It is **required** for SUPER_ADMIN (already the case), for any LIVE action, and for storing an **order-capable** broker token once LIVE exists. Under Phase 0 PAPER, storing the data-only Analytics Token asks for the step-up only when the user has MFA on. Add `require_mfa_session` to every SUPER_ADMIN write in (b), with a test that lists every `require_role()` route and fails if one lacks it. Give the step-up a lifetime (`MFA_STEP_UP_MINUTES`, a setting, default 30 for admin) and give sessions an absolute lifetime (`SESSION_MAX_DAYS`) on top of the sliding refresh. |
| R6 | **"The app shows 'draft' nowhere to users" contradicts the legal module, and it is the riskier way round.** The privacy notice, terms and risk disclosure exist (`app/legal/content/{privacy,terms,risk}.md`), with versioned acceptance at sign-up (`app/legal/acceptance.py`). They carry the "Draft — to be reviewed by a lawyer before launch" banner on purpose (`legal/documents.py:26,50-51`), have `[COMPANY NAME]` / `[GRIEVANCE OFFICER]` placeholders, and sit behind the flag `legal_pages`, OFF (`legal/routes.py:10,45`). Showing unreviewed legal text to real users without the banner would present a draft as a binding agreement. | Keep the banner and the flag OFF until Abhi's lawyer signs off and the company facts exist. Then the lawyer-approved text goes in as a new `version`, the banner goes, and everyone accepts again. In READINESS_STATUS, "real users" is gated on that, not on code. The A5 / A6 PRs extend the existing documents (processors, AI / Vision consent, retention via `{{retention.*}}`). They do not write new pages. |
| R7 | **"Billing stays OFF until Abhi turns it on" has no switch, and prices are hard-coded.** `POST /api/billing/subscribe` is open to every organisation owner (`billing/routes.py:76-86`; no flag). `subscribe()` with the default `manual` provider (`config.py:448`) gives the plan's 14-day trial **at once**: `tenant.plan = plan.id` (`billing/service.py:120-135`), which means more live strategies and marketplace access (`plans/registry.py:70-86`). Prices ₹2,999 / ₹14,999 and the trial length are constants in `plans/registry.py:74,84,76,86`. | First C11 step, and earlier if the Billing card can be reached under `ui_v2`: a server flag `billing` (OFF) that guards subscribe / cancel / checkout (404 while off), with the card hidden. Prices, cycles, trial days and referral rewards move to an admin-edited table (migration), read by `price_for` / `plans_catalogue`. Every change is audited. |
| R8 | **B7's "platform's own feed" fallback breaks the data terms, and its premise is out of date.** `docs/BROKER_DATA_TERMS.md` (Upstox row): Upstox data may be shown only because "each call needs that user's own access token". Tier A is "through the user's own session only" (`market_data/broker_data_terms.json`, `entitlement.py`). Serving one token's quotes (the platform's or anyone's) to other users would be redistribution. Also, CLAUDE.md makes the **one-year Analytics Token** the default data connection, so "the Upstox token ends daily" is true only for the order-capable daily login (`IN-SEBI.login.daily`), not for data. | Fallback order: the user's own Tier A session → licensed Tier B/C data for everyone (bhavcopy EOD, delayed sources that `DATA_SOURCES.md` allows), always labelled with its time ("Prices as of 15:30 yesterday"). Never another user's or the platform's broker token. B7's "renew" path covers both the Analytics Token's yearly expiry and the daily order login. |
| R9 | **The STEP 0 list misses about fifteen built modules, so items risk being built a second time** (CLAUDE.md: keep what is built). Not listed but present: the public status page `app/observability/status_page.py` (flag `public_status_page` OFF) and incidents `app/incidents` (A2); the in-process recent-errors list `observability/recent_errors.py`; Prometheus alert rules `scripts/monitoring/prometheus-alerts.yml` (17 rules), with **no Prometheus or Alertmanager in any compose file**, so the rules never fire; the daily backup / Sunday restore-check cron `atp.sh schedule` (`:1300-1319`); the last-backup line in health (`atp.sh:585-595`); the step-by-step R2 setup (`HOSTINGER_RUNBOOK.md` §6, `:209-227`); login lockout `auth/lockout.py` and the auth rate limits (`auth/routes.py:52-55,301,663`, `google_routes.py:41`, AI `app/ai/rate_limit.py`); the session list with log-out-one / log-out-all (`auth/routes.py:446-500`); pip-audit, npm audit and Trivy in CI (`ci.yml:171-174,290-291,297-325`); CSP / HSTS at the edge (`deploy/Caddyfile:42-50`); legal documents + acceptance (R6); owner exports `app/exports`; Web Push (`alerts/webpush.py`) and the FCM plan (`docs/design/MOBILE_APP_PLAN.md` B2, owner items MA-1..MA-9); the support bot `app/support` and the feedback inbox (`beta/routes.py:129`); the glossary `frontend/src/ui/glossary.ts`; the load-test reports and command (`docs/reports/load_test_500.md`, `atp.sh loadtest`, `LOADTEST_REAL_HOST.md`); the compliance rule set (`compliance/rulesets/in_sebi.json`); and the go-live checklist with DPDP items (`compliance/golive.py`). | READINESS_STATUS gets one row per existing module, not only per order item. Each A/B/C PR names what it extends. Specific reuses: A2 in-app alerts are **evaluated in the worker / a small checker** and sent through the existing platform Telegram bot (`alerts/platform_bot.py`) to Abhi's own chat. Adding Prometheus on the 8 GB host is an option only after a memory check. The status page for outages has to be hosted off the box (see A2 below). |

## Other findings, per item

### STEP 0
- `atp.sh health` already shows most of what the table needs: containers, deep health, TLS, key, flags, last backup
  age, R2 state, last restore check, disk (`atp.sh:534-610`). Ask Abhi for it once, as the order says. Also ask for
  `atp.sh releases` and `crontab -l` (shows whether `schedule` ran). Neither prints a secret.
- The owner-orders README's last numbered row is 15 (`docs/owner-orders/README.md`). "README row 17" should be 16,
  unless another order takes 16 first. CLAUDE.md has no numbered sections, so "§6" / "§8" in the order mean the
  "Independent review" and "Keep what is built" lines.
- READINESS_STATUS must not hold host details (IP, host names): `docs/PUBLIC_REPO_POLICY.md` §2.

### A1 Off-site backup
- Exists: `backup [--verify]`, `restore-check [--local|--require-offsite]`, `--scheduled`, AES-256-CBC + PBKDF2 +
  HMAC seal (`atp.sh:1084-1131`, `1182-1298`), the R2 remote (`docker-compose.hostinger.yml:109-133`, bucket-scoped
  token, `NO_CHECK_BUCKET`), and the cron (daily 02:17, Sunday 03:43). The order's "write the step-by-step" is
  largely done (runbook §6). Update it rather than writing a new one.
- Retention: dumps are kept 14 days locally (`common.sh:9`), the files archive 14 (`atp.sh:1159`), and pre-migration
  dumps for the last 3 releases. Off-site: `rclone copy` **never deletes** (`offsite_sync.sh:10-11,21`), so R2 is
  bounded only by the runbook's 35-day lifecycle rule (§6 step 5). The order's "7 daily / 4 weekly / 6 monthly"
  is not implemented and contradicts the 35 days. R2 lifecycle rules work by prefix and age, so GFS needs prefixes:
  copy the Sunday dump to `backups-weekly/` and the first-of-month dump to `backups-monthly/`, each with its own rule.
  The counts are settings in `.env`. Mind R2's free 10 GB (OPEN_QUESTIONS H-2).
- Alerting: a failed cron run only writes `.deploy/logs/backup.log`. Nothing tells Abhi. The A2 checker should read
  `LAST_BACKUP_OK` (26 h rule, `atp.sh:588-590`), `.deploy/last_restore_check` and the offsite "synced / FAILED"
  line. Health takes that line from the last 300 log lines of the `offsite` container (`atp.sh:593`), so it is lost
  after a container restart. Persist the sync result to a file instead.
- R2 token: "Object Read & Write" can delete and overwrite. The bucket lock and versioning in runbook §6 step 2 are
  the protection against a compromised host. Make them a required step, not "if your plan offers it", and check them
  in the restore rehearsal.
- WAL / base backups: R1.

### A2 Monitoring and alerts
- External uptime check from a GitHub Actions cron in this public repo: free, no secret in git (the Telegram bot token
  and chat id go in repository secrets). But GitHub runs scheduled workflows late (often 5–20 min), skips some under
  load, and **disables them after 60 days without repository activity**. A free uptime service (two checks: `/` and
  `/api/system/health`, alert after 2 failures, Telegram + e-mail) is more reliable. Abhi creates that account. If
  the cron is used, use it as a second opinion only.
- The status page (`/api/status`, `/status`) is served **from the same host**, so it is down exactly when it is
  needed. For "up / degraded / maintenance + incident notes" during an outage, use the uptime service's hosted status
  page (or a static page off the box) linked from the footer, and keep the in-app page for "degraded" states. The
  in-app page already refuses to show internals (`status_page.py:1-24`). Incident notes on a public page follow the
  same rule (no hosts, versions, users).
- Error tracker: a self-hosted Sentry / GlitchTip will not fit the 8 GB budget next to production (R4 numbers).
  Sentry's free tier sends data abroad: list it as a processor (A5), scrub e-mails, tokens and query strings on the
  server side (`before_send`), and extend the CSP `connect-src` (`Caddyfile:48`, the frontend nginx CSP too) for the
  browser SDK. Alternative with no new processor: backend only, built on `recent_errors.py` + structured logs, with
  the request id.
- In-app signals already measured (`observability/metrics.py`): worker heartbeat age, last cycle, market data stale,
  broker circuit, outbox backlog / dead letters, login failures (credential stuffing), Redis memory. Missing: broker
  token expiry counts across users (exists per tenant in `brokers/token_lifecycle.py`), backup age, DB size,
  certificate expiry (health checks TLS but not the days left), disk.
- Admin alert channel: reuse the platform bot, with a separate "operator" chat registered from the admin console.
  Never the user alert path, so a user setting cannot mute an ops alert.

### A3 Safe deploys
- Gate: R3. Staging: R4.
- Rollback is one command and is rehearsed: `rollback [--to SHA] [--restore-db]` (`atp.sh:940-1082`), the
  `migration_rollback_safety.py` refusal, and `migrate_guard.py` with the NSE-session refusal (exit 3,
  `atp.sh:822`). `deploy/ci/rollback_rehearsal.sh` **does run in CI**: job `rollback` in `staging-stack.yml:250-276`,
  on every main push, and on other branches only when stack files change (`:49-69`). It passed on `aa175fea`;
  `docs/reports/rollback_rehearsal.md` records 12/12. What the rehearsal cannot cover: the owner's one real rehearsal
  on the host (`PHASE0_EXIT_CRITERIA.md:15`).
- The PR template does not exist (`.github/` holds only `workflows/`). Add `.github/pull_request_template.md` with
  "Migration: yes / no (revision)", "Rollback-safe: yes / no (ROLLBACK_SAFE set?)", "Deploy line", "Flags". A CI
  check can cross-check the migration answer against `backend/alembic/versions` changes in the diff.
- Branch protection / required checks on `main` could not be read with this session's token (HTTP 403). Abhi checks
  them in GitHub settings once the gate exists.

### A4 Security
- MFA: R5. TOTP with a ±1 step window, last-step replay protection and 8 hashed backup codes (`auth/mfa.py:26-29,
  54-131`). MFA failures count toward the login lockout (`auth/routes.py:706-714`). Good.
- Rate limits: login 10/min, register 10/min, forgot 5/15 min, verify-email 5/5 min, MFA 10/min (all per IP), Google
  20/min, the AI limiter per plan, and many per-user data limits. The client IP is trusted only from the named
  proxies (`scripts/serve_api.sh:8-27`). Missing: a per-account limit on `password/forgot` (per IP only, so e-mail
  bombing one user from many IPs is possible) and on `verify-email/resend`. `password/reset/{token}` borrows the
  login limiter (`auth/routes.py:612`). Acceptable.
- Sessions: access tokens last 15 min (`config.py:74`) and refresh tokens slide for 30 days, with no absolute cap or
  idle timeout (R5 c). Device list and log-out-others exist.
- Secrets: atp.sh refuses a changed `SECRETS_ENCRYPTION_KEY` (exit 20). `backend/scripts/reencrypt_secrets.py` exists
  for a real rotation. The rotation steps per secret are not written yet. Note in them: rotating `JWT_SECRET_KEY`
  logs everyone out; rotating `BACKUP_ENCRYPTION_PASSPHRASE` makes the existing backups need the **old** passphrase
  (keep it in escrow until they expire, R2); rotating `POSTGRES_PASSWORD` needs the role altered, not only `.env`.
- Scanning is already in CI (pip-audit, npm audit for production dependencies, Trivy on the backend and frontend
  images). Not scanned: `deploy/backup.Dockerfile` and the pinned third-party images (postgres, redis, caddy, rclone).
  `container-scan` `needs: [backend, frontend]`, so it is skipped while main is red.
- Headers: HSTS (no `preload`), CSP, XFO, nosniff, Referrer-Policy and Permissions-Policy at Caddy
  (`Caddyfile:42-50`), checked by `public_domain_check`. `includeSubDomains` means any future `staging.` or `status.`
  subdomain must be HTTPS from day one.
- Pen-test scope document: keep it in `atp-private` or with Abhi, not in this public repo. An attack-surface list is
  useful to an attacker. The go-live checklist already expects "VAPT by a CERT-In empanelled auditor"
  (`docs/COMPLIANCE_IN.md:33`).

### A5 Data privacy (DPDP)
- `erase_user` (`retention/service.py:176-209`) exists but cannot be the self-service "delete my account":
  - it refuses self-erasure and needs an already-deactivated teammate (`team/routes.py:255-274`), while every
    sign-up owns its own organisation;
  - it leaves the user's broker credentials (`BrokerCredentialRecord`, the `broker_credentials` relationship on
    `User`), the organisation's alert channels (Telegram chat ids, push subscriptions), journal entries, watchlists,
    notifications, and the IP / user agent on revoked sessions;
  - `llm_calls` text is kept by default (`llm_text_days = 0`, `retention/policy.py:68`), and `llm_calls` /
    `ai_acknowledgements` are never deleted (`policy.py:20-30`).

  Wire it as "delete my account" = deactivate, a cooling-off period, then erase at the organisation level (a sole
  owner's organisation goes, apart from the rows the regulatory period keeps under a stable id), with MFA step-up if
  MFA is on. The privacy notice must say that backups keep erased data until they expire (14 days locally, 35 days
  on R2, longer for GFS monthly).
- The "48-hour notice before erasure" in the DPDP Rules applies to erasure **at the end of the retention period for
  the classes of data fiduciaries named in the Rules' schedule** (large e-commerce, gaming and social-media
  platforms), not to a user's own deletion request. ATP is most likely not in that class. Treat it as a lawyer
  question, and default to a 48-hour "your account will be deleted; cancel here" e-mail anyway, which is
  user-friendly.
- Logs: the Rules expect logs kept for at least one year. `IN-SEBI.golive.checklist` already checks
  `log_retention_days = 365`. Confirm that `login_events_days` and `alert_deliveries_days` defaults are at least 365,
  or that they are not personal-data logs in the lawyer's reading.
- Export: `GET /api/exports/{dataset}` (owner, `exports/routes.py:59`) covers trading records, not a personal-data
  bundle (profile, sessions, consents, AI conversations, journal). Add a "Download my data" bundle.
- Breach runbook: the go-live checklist has `dpdp.breach_runbook` and `dpdp_breach_notify_hours = 72`. The runbook
  must also say "intimate the Board **without delay**" (the 72-hour report is the detailed one). Keep the contact
  list itself out of the public repo.
- Processors: hosting (Hostinger), Cloudflare (R2; DNS if used), AI providers (platform and BYOK; the BYOK user is
  his own processor relationship), an e-mail / SMTP provider (not chosen yet: `atp.sh reset-password` exists "until
  e-mail (SMTP) is configured"), Razorpay, Google (sign-in, FCM), Telegram, GitHub (Android build artifacts, MA-9),
  an error tracker / uptime service if chosen.

### A6 SEBI / legal
- `docs/COMPLIANCE_IN.md` is **generated** from `backend/app/compliance/rulesets/in_sebi.json` by
  `backend/scripts/compliance_doc.py`, and `tests/test_compliance_rules.py` fails when the page is out of date
  (`COMPLIANCE_IN.md:3-7`). The update is a new rule-set version (`in_sebi.json:4-5` says version 1, effective
  2025-08-01; the order says the framework applies from 1 Apr 2026), then a regenerated page, not a hand edit. Most of
  the framework is mapped already: algo-id tagging, static IP primary + backup, daily login, white / black box on the
  go-live checklist. The "code gaps for LIVE day" list should start from those rule rows.
- The "most individual F&O traders lose money" disclosure: `legal/content/risk.md` exists. Showing it before F&O
  features and the Paper bot is new UI. Keep it as text without figures unless the lawyer approves citing SEBI's
  numbers.
- A one-line disclaimer on every signal surface: run it through the signal-language formatter and its CI guard (the
  `signal_language_v2` module). No buy / sell words, the same line everywhere.

### B7 Market-data reliability
- R8. Staleness exists: `alerts/staleness.py`, the `MARKET_DATA_STALE` metric, the acceptance check "stale data ⇒
  alerts suppressed" (`PHASE0_EXIT_CRITERIA.md:13`), and the status page's data-feed band. The renew path and the
  pre-open reminder exist (`brokers/login_reminder.py`, `token_lifecycle.py`). Verify both under `ui_v2` on Today /
  Broker before building anything.

### B8 Mobile app
- `frontend/android` (Capacitor 8.5.2, appId `tech.aialgodesk.app`) builds in `android.yml`. The push / deep-link /
  store plan is already written in `docs/design/MOBILE_APP_PLAN.md` (B2 FCM, B3 biometric unlock, B4 release), with
  owner items MA-1..MA-9 and decisions MA-7 (minimal push content: no symbol, price or P&L) and MA-8. B8 should point
  to that plan and not restate it. Web Push cannot run in the Android WebView (`MOBILE_APP_PLAN.md:147-148`), so the
  new channel is FCM (`app/alerts/fcm.py`, `app/mobile/devices.py` as planned).
- Check that the bundled app's origin can reach the API (CORS `ALLOWED_ORIGINS`) and that the refresh cookie works
  from the WebView's origin (SameSite / secure). This is a common Capacitor trap. Cover it in the first B2 PR with an
  e2e check.

### B9 Private reader
- Plan only. `PRIVATE_PACKAGE.md` and `TRADE_TAKEOVER.md` exist. Parity fixtures stay out of this repo
  (`PUBLIC_REPO_POLICY.md` §3). Nothing blocking.

### B10 Capacity
- Largely done. The 500-session load test runs in CI on the KVM 2 shape (`staging-stack.yml`, `stack.sh pin kvm2`),
  `atp.sh loadtest` runs it on the real host in market hours, and `docs/reports/load_test_500.md` gives the verdict:
  **SLO FAIL**, with login p95 at 11–28 s because bcrypt saturates 2 vCPU (LD-9) and a cold chart at 2.0 s (LD-10).
  B10's question "what size handles N users" is mostly answered. The work is LD-9 / LD-10 and the owner's real-host
  run. Do not start a new load test.
- Frontend budgets: `npm run check:bundle` exists. "Interactive ≤ 2 s on a mid-range phone on 4G" needs a throttled
  Lighthouse / Playwright trace in CI. New work, and only a statistical guard on shared runners.

### C11 Payments
- R7. Razorpay Subscriptions exist: hosted `short_url` checkout, webhook HMAC over the raw body, idempotent on the
  event id (`billing/razorpay.py:1-14`, `webhooks.py`). Not built: GST invoices (sequential numbers per financial
  year, GSTIN, HSN/SAC 998431-type service code, the lawyer / CA to confirm), refunds, dunning e-mails (need SMTP),
  receipts, and plan-change proration.
- RBI e-mandate: for a recurring debit above ₹15,000 the bank asks for extra authentication on each charge. The
  Business plan yearly (₹1,49,990) is above it, the monthly (₹14,999) just under. The pricing table should carry this
  as a note to the owner.
- Razorpay activation (KYC) wants public Terms, Privacy, Refund / Cancellation and Contact pages on the domain, so C11
  depends on R6 and a refund-policy text.

### C12 Support
- Exists: the support bot (`/api/support/ask`, rate-limited), "Send feedback" with an admin inbox
  (`beta/routes.py:129`), and the glossary and page tours. A ticket list can extend `FeedbackRecord` (migration for
  status / assignee). Context capture: page, time and the last request id from `X-Request-ID`
  (`recent_errors.py`). Never headers, bodies or query strings.
- The WhatsApp number shown in the app: in Abhi's `.env` or admin settings, never in git (`PUBLIC_REPO_POLICY.md`
  §2, phone numbers).

### C13 Learning and growth
- Onboarding e-mails need a mail provider (Abhi). The funnel can build on the MO4 slice-3 activation events in the
  admin Business view. Marketing pages: no performance claims, no security names with prices (CLAUDE.md signal
  language). The marketplace stays OFF (handover 11l), so referral rewards must not involve the marketplace.

### CI workflows (context)
- `ci.yml`: lint (ruff, mypy gated packages, bandit, gitleaks push range, english-only, public-repo guard), backend
  (pytest, `alembic check`, pip-audit), backend-lookahead, perf, frontend (vitest, colour / mobile checks, build,
  bundle, npm audit), container-scan. `secret-scan.yml`: the blocking full-history gitleaks scan, currently failing.
  `staging-stack.yml`: acceptance, load-500, rollback. `android.yml`: debug build. `nse-expiries.yml`: weekly data.
  `deploy-staging.yml`: R4.

## What Abhi must do himself
1. Paste `atp.sh health`, `atp.sh releases` and `crontab -l` once (STEP 0; no secrets in them).
2. Decide RD-1 (WAL archiving off by default, or PITR with an encrypted weekly base backup), RD-2 (staging = the CI
   stack by default, or buy a second host), and RD-3 (MFA required for which actions).
3. Cloudflare: create the R2 bucket `atp-backups` with versioning **and** a bucket lock, a bucket-scoped Object Read &
   Write token, and lifecycle rules per prefix. Put the values only in `/opt/atp/.env` (runbook §6).
4. Keep `BACKUP_ENCRYPTION_PASSPHRASE`, `SECRETS_ENCRYPTION_KEY`, `JWT_SECRET_KEY` and `POSTGRES_PASSWORD` in his
   password manager plus one offline copy (R2).
5. Make an uptime-service account (Telegram + e-mail alerts), and choose an error tracker or none. Register the
   operator Telegram chat with the platform bot.
6. Choose an e-mail (SMTP) provider for resets, dunning and onboarding, and set up its domain records (SPF / DKIM /
   DMARC).
7. Check branch protection / required checks on `main` once the gate exists.
8. Legal: register the company, name the grievance officer, have the lawyer review terms / privacy / risk / refund
   texts and the question list, and engage a CERT-In empanelled VAPT firm before public launch.
9. Payments: complete Razorpay KYC with the company, get a GSTIN, and confirm prices, trial and refund policy.
10. Mobile: the Firebase project (MA-2), the upload keystore with two offline backups (MA-3), and the Play Console
    account after company registration (MA-4).
11. The WhatsApp support number and the support hours.
