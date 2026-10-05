# Go-live runbook

The ordered operator sequence from a fresh deploy to the first LIVE order. Every code-side item of the
master prompt is built (see `MASTER_PROMPT_GAP_ANALYSIS.md`); what remains is this sequence, done by a
person with the keys. The Dashboard go-live checklist (per organisation) and the Admin console checklist
(platform) compute most of these from live state and link to the page that fixes each one.

Marathi step-by-step version with commands and expected output: `docs/GO_LIVE_MR.md` (Phase AX); the morning
check for the first PAPER day is `backend/scripts/first_paper_day_check.py`.

## 0. Before the first deploy (platform operator)

1. **Secrets in the environment**, never in the repo or in chat: `JWT_SECRET_KEY`, `SECRETS_ENCRYPTION_KEY`
   (per-tenant envelope master), `DATABASE_URL` / `MIGRATION_DATABASE_URL` (least-privilege roles,
   OPERATIONS 1.6e), `REDIS_URL`, SMTP for the platform mailer, VAPID keys for browser push, Razorpay key/secret
   and webhook secret (operator secrets, not tenant data). `ENVIRONMENT=production` makes every default
   secret fail fast.
2. **Migrations**: `alembic upgrade head` with the migration role; the migration-hour guard refuses during
   market hours (OPERATIONS 1.6d).
3. **Services**: API, one trading worker (a second replica is safe only with Redis reachable; it skips the
   cycle while the lock is held), Redis, Postgres with WAL archiving and the backup service (OPERATIONS 1.2, 1.2a),
   Prometheus scraping API and worker metrics with the alert rules in `docs/SLO.md`.
4. **Reference data**: the worker syncs the Upstox instrument master daily from `INSTRUMENT_SYNC_HOUR_IST`;
   run `POST /api/instruments/sync` once before the first session. Load this year's NSE holidays on the Admin
   console (OPERATIONS 1.6u). Load company profiles and financials for the Fundamentals module and Factor Lab
   if you use them (OPERATIONS 1.6y).
5. **Staging first**: the staging overlay and rolling deploy (OPERATIONS 1.6f); run the load baseline and
   chaos tests (docs/PERFORMANCE.md) against it.
6. **Security sign-off**: rotate the envelope master once to prove the tooling (OPERATIONS 1.6d), confirm
   the CI container scan is green, and book the external penetration test; it is the one section-48 item
   the code cannot supply.

## 1. First organisation (owner)

1. Register, verify the email, enable MFA on the owner account (Account page). MFA is required for LIVE and
   for entering broker credentials.
2. **Broker credentials under Settings > Brokers**: API key and secret (plus client id / PIN / TOTP secret
   where the broker needs them, OPERATIONS 1.6t-1.6w, 1.6ab). They are encrypted at rest and never leave the
   server. Log in (Upstox: the OAuth button). Tokens expire daily for the equity brokers; CoinDCX keys do not.
3. **Read-only check** on each broker account card (OPERATIONS 1.6aa): eight probes, no order placed. Green
   on profile, funds and quote is the first live confirmation of that adapter; a green `contract_quote` proves
   F&O symbol translation on that broker.
4. Alert channels (Telegram, email, webhook, push, SMS) under Settings; send a test alert.
5. Risk limits: review the tenant and account limits on the Risk page; the platform ceilings and the Risk
   Guardian rules are on by default (OPERATIONS 1.6k).

## 2. PAPER before LIVE

1. Create a strategy (built-in or the builder / AI Copilot with its backtest and approval gate).
2. Deploy it in PAPER with the contract rules you intend to trade (underlying, option or future, strike and
   expiry rules, premium stop). Preview the contract; the preview names the strike and why.
3. Run at least one full session in PAPER. Watch the Dashboard heartbeat, the Positions page exits, the
   square-off at the venue's cut-off, the alerts, and the trade journal. Reconcile once (Positions > Reconcile).
4. Read the Dashboard go-live checklist for the LIVE target: everything must be green or a deliberate skip.

## 3. LIVE

1. Step-up MFA, switch the deployment (or a new one) to LIVE with `max_lots` set small.
2. First LIVE entry: confirm the protective stop is standing at the broker (Orders page / broker app). The stop
   guard re-arms a missing stop every cycle (OPERATIONS 1.6f).
3. After the first day: upload the contract note (Phase D4) so charges and P&L are the broker's numbers, and
   check the live-vs-backtest degradation view (Analytics).
4. Kill switches (strategy / user / global) and the emergency exit are on the Risk page and the Admin console;
   they never need a broker session to stop new entries.

## 4. Every trading day

OPERATIONS 1.5 (daily operating routine) and 1.7 (trading worker runbook) are the standing checklists:
broker login before 09:00 IST where the token expired overnight, worker heartbeat, token banner, positions
reconciled, backups verified.
