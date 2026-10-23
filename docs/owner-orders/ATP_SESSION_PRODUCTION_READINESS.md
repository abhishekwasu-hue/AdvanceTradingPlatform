From Abhi — PRODUCTION READINESS MASTER ORDER (safety, trust, product quality, business), design + coding order (English)

Goal: make AiAlgoDesk ready for real users. Today it has backups not yet off-site, no outside alerting, deploys without a full test gate, and the legal / privacy groundwork still open. Much of the machinery already EXISTS in the repo (see STEP 0); this order is mostly "verify, configure, close the gaps", not rebuild (CLAUDE.md §8: keep what is built).

Rules: CLAUDE.md applies (PAPER only, LIVE untouched, signals only, no secrets in git / chat, never run anything on the server — give Abhi ONE consolidated block, migrations after 15:30 IST). Record this order in docs/owner-orders first (INBOX verbatim, a DECISIONS line, README row 17). Before building: an independent review of this order against the code by a separate agent (CLAUDE.md §6); fix the findings. One draft PR per item, merge on Abhi's word; with each PR the deploy line and whether a migration is needed.

STEP 0 — READINESS INVENTORY (report first, no code)
Produce docs/READINESS_STATUS.md: one row per item A1–C13 below with: what exists (file / function), whether it is CONFIGURED in production (from atp.sh health output Abhi pastes — ask him once for `su - atp -c "/opt/atp/deploy/atp.sh health"`), what is missing, the work, and what Abhi must do himself (accounts, keys, legal). Known to exist already (verify): deploy/atp.sh backup [--verify] → R2, restore-check, rollback [--to SHA] [--restore-db], migrate_guard + rollback-safety; backend/app/auth/mfa.py (TOTP MFA); backend/app/billing (Razorpay); backend/app/retention/service.py (retention + erase_user); backend/app/observability/metrics.py; backend/app/platform/readiness.py; docker-compose.staging.yml; deploy/ci/rollback_rehearsal.sh; frontend/android (Capacitor). Send Abhi the Marathi summary of this table with a short "what you must do" list.

PART A — BEFORE REAL USERS (in this order)
A1 Off-site backup (URGENT). R2 is not set up (owner). Write the step-by-step for Abhi: create a Cloudflare R2 bucket + an API token scoped to that bucket only, put the values in /opt/atp/.env (names only in the docs, never values). Then the schedule: a daily encrypted backup (atp.sh backup --verify) + a weekly restore-check, retention (e.g. 7 daily / 4 weekly / 6 monthly — settings), and an alert on a failed or missing backup (A2). Health shows the last good backup time. Test: a restore rehearsal on staging documented in the runbook.
A2 Monitoring and alerts to Abhi (Telegram + e-mail):
 - outside uptime check of https://aialgodesk.tech and the API health endpoint (an external free monitor, e.g. a GitHub Actions cron or a free uptime service — pick one with no secrets in git), alert after 2 failures;
 - in-app: error rate, worker heartbeat, data-feed staleness, broker-token expiry across users, backup age, disk space, DB size, certificate expiry → an admin alert channel (reuse metrics.py / readiness.py; the admin console shows them);
 - an error tracker (self-hosted or free tier; PII scrubbed — no tokens, keys, emails in events);
 - a simple public status page (up / degraded / maintenance + incident notes) linked from the app footer.
A3 Safe deploys:
 - a CI gate: deploy only a commit whose FULL backend + frontend suites and checks passed on main (atp.sh refuses otherwise, with a clear message; an emergency override flag documented);
 - staging first: the same images on docker-compose.staging (a staging subdomain), a smoke test, then production;
 - rollback = one command (atp.sh rollback), rehearsed in CI (rollback_rehearsal.sh) — confirm it runs;
 - the PR template always states: migration yes / no, rollback-safe yes / no, the one deploy line.
A4 Security:
 - MFA (TOTP) offered to every user, required for ADMIN and before any LIVE-related or broker-credential action (mfa.py step-up exists — verify coverage);
 - rate limits on login / signup / password reset / OTP / AI endpoints; lockout with plain messages;
 - secrets: rotation steps for every server secret; the encryption keys backed up separately from the data backup;
 - dependency and container scanning in CI (pip-audit / npm audit / image scan), bandit already present;
 - security headers (CSP, HSTS) checked; session timeout; device / session list with "log out other devices";
 - before public launch: an external penetration test (Abhi engages it; the session prepares the scope document).
A5 Data privacy (India DPDP Act 2023 + DPDP Rules notified 13 Nov 2025; most obligations phase in about 18 months later — build now):
 - a plain privacy notice (what data, why, retention, rights, grievance contact) and itemised consent at signup and for AI / Vision (data sent to the AI provider);
 - user rights in Settings → Account: download my data (export), delete my account (erase_user exists — wire it, with the 48-hour notice the Rules describe before erasure where applicable), withdraw consent;
 - a breach-response runbook (detect → contain → notify the Board and affected users without delay; detailed report within 72 hours), with an admin checklist;
 - retention policies documented per table (retention/service.py), logs kept as the Rules require;
 - processors list (hosting, R2, AI providers, e-mail, Razorpay) with their data terms.
 Legal text is drafted for review by Abhi's lawyer; the app shows "draft" nowhere to users.
A6 SEBI / legal groundwork (no LIVE work):
 - update docs/COMPLIANCE_IN.md with the retail algo framework in force since 1 Apr 2026 (client-specific API keys + static IP, OAuth + 2FA, exchange algo IDs, broker audit trails, algo-provider empanelment through brokers, white / black box) and map ATP: today = information + paper + white-box signals; LIVE / auto-orders later = the empanelment path through a broker + exchange algo IDs + static IP — list the code gaps for that day, build nothing live;
 - Terms of Service, risk disclosure (SEBI's study that most individual F&O traders lose money, shown before F&O features and the paper bot), disclaimer on every signal surface (one line, consistent);
 - the questions for the lawyer in one list (what ATP may say / do as a non-RA, non-RIA SaaS; signals vs advice; templates; AI chat). Abhi takes it to a lawyer before LIVE / public launch.

PART B — PRODUCT QUALITY
B7 Market-data reliability: the Upstox token ends daily — a clear "renew" path (exists on Broker / Today: verify), a fallback data source for quotes / candles when the user's token is missing (the platform's own feed where allowed), staleness shown in plain words on every price surface, cached last good data with its time; alerts to admin when the feed is stale for all users.
B8 Mobile app: the Android build from frontend/android (Capacitor) — push notifications (signals, approvals, exits, renew reminders) through the existing alerts system as a new channel, deep links into the right page, app-store listing prep (privacy labels from A5); iOS later. Telegram stays.
B9 Abhi's own reader (atp-private): plan the port of the Trade decision3 reader as a private package (docs/design/PRIVATE_PACKAGE.md, TRADE_TAKEOVER.md exist — update them): the interface, parity tests (scan = backtest = live), when (after Trade's PAPER results are stable — Abhi decides). No strategy-sensitive content in this public repo.
B10 Capacity: a load test on staging (concurrent users on Today / Signals / chain / chart, the workers, websockets) → the user count the current Hostinger KVM 2 handles, the bottleneck, and the next size up with its cost. Performance budgets (Today / Signals interactive ≤ 2 s on a mid-range phone, 4G) in CI.

PART C — BUSINESS
C11 Payments: Razorpay subscriptions (billing/ exists — verify), GST invoices (company details from Abhi once the company is registered), plan changes, refunds per policy, dunning e-mails, receipts; all prices / trial length / referral rewards admin-configurable, nothing hard-coded; billing stays OFF until Abhi turns it on.
C12 Support: a help centre / FAQ (searchable, from the glossary + page tours), in-app "Report a problem" with an automatic context capture (page, time, last error id — never secrets), a ticket list in the admin console, and a WhatsApp / e-mail support link shown during market hours (Abhi provides the number); response-time targets in the admin view.
C13 Learning and growth: a Learn section (short lessons, the glossary, videos — links to Abhi's course content when ready), onboarding e-mails for the first week (connect broker → first paper bot → first signal review), a marketing site page set with SEO basics (no performance claims, no security names with recent prices), referral flow (C11 settings). Funnel metrics (signup → broker → paper bot → active week 1) in the admin console.

ORDER
1. STEP 0 inventory + the Marathi "what you must do" list for Abhi.
2. A1 → A2 → A3 (each its own PR; A1 needs Abhi's R2 values in the server .env — give him the exact steps).
3. A4 → A5 → A6.
4. B7, B10, then B8, B9 (B9 = plan only).
5. C11 → C12 → C13.
Each PR: self-review, tests, screenshots for UI (360 / 390 / 1440, light / dark), the deploy line, migration yes / no. Reports to Abhi in Marathi; log questions in OPEN_QUESTIONS with a provisional default and keep going.
