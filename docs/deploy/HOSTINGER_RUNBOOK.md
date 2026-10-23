# Hostinger runbook (Z11 deploy bundle)

Production host: **Hostinger KVM 2** (2 vCPU / 8 GB RAM / 100 GB NVMe, Ubuntu 24.04, Mumbai, static IPv4). Phase 0
runs PAPER only. Everything on the server goes through one command, `deploy/atp.sh`, run by the owner. The session
that built it never runs it, never logs in to the server, and never sees `.env`.

The earlier runbook `docs/DEPLOY_HOSTINGER.md` (Marathi until Oct 2026, now English) describes the earlier blocks (`deploy/hostinger/deploy.sh`,
`rollback.sh`, `status.sh`). `atp.sh` replaces the deploy and rollback blocks. Their one-line forms are gone from
`docs/DEPLOY_HOSTINGER_ONELINERS.txt`, and once `atp.sh` has recorded a release they refuse to run, so the two cannot
be mixed on one server.

**Owner blockers (the four in docs/ROADMAP_STATUS.md):** (1) the Hostinger server IP (`<SERVER_IP>` below);
(2) the R2 bucket and token in the server's `.env` (section 6); (3) the Dhan partner application; (4) company
registration. This runbook needs (1) and (2). Until R2 is filled in, everything works and backups stay on the server.
Every `atp.sh` run then prints a warning.

## 1. The commands

Run these as the `atp` user on the server (`ssh atp@<SERVER_IP>`). Each one is safe to run again.

| What | Command |
|---|---|
| First install, after the bootstrap (bootstrap checks, deploy, health, first backup and restore check, flag posture) | `/opt/atp/deploy/atp.sh install` |
| Update to the newest merged code | `/opt/atp/deploy/atp.sh deploy` |
| Deploy a tag or a commit (must be on `main`) | `/opt/atp/deploy/atp.sh deploy <tag-or-sha>` |
| Check everything (read-only) | `/opt/atp/deploy/atp.sh health` |
| See the steps without changing anything | add `--dry-run` to any command, e.g. `/opt/atp/deploy/atp.sh deploy --dry-run` |
| Go back one release (code only, when every migration in between is rollback-safe) | `/opt/atp/deploy/atp.sh rollback` |
| Go back one release and put the database back to the backup taken just before that release migrated it | `/opt/atp/deploy/atp.sh rollback --restore-db` |
| Back up now (database and files), copy to R2, then verify | `/opt/atp/deploy/atp.sh backup --verify` |
| Prove the newest R2 backup restores (scratch database, row counts, audit chain) | `/opt/atp/deploy/atp.sh restore-check` |
| The last good backup, local and off-site, as one JSON line (exit 1 when older than `BACKUP_MAX_AGE_HOURS`) | `/opt/atp/deploy/atp.sh backup-status` |
| Install (or refresh) the nightly backup, the weekly restore check and the 5-minute server checks in the crontab (every `deploy` does it too) | `/opt/atp/deploy/atp.sh schedule` |
| The server checks once now (backups, disk, certificate, worker, data feed, errors...; messages you only on a change, section 10b) | `/opt/atp/deploy/atp.sh ops-check` |
| Empty the WAL archive left from before archiving was turned off (section 6c) | `/opt/atp/deploy/atp.sh wal-cleanup` |
| A new server after the old one is lost: fill its empty database from R2 (section 6b) | `/opt/atp/deploy/atp.sh restore-offsite` |
| Point-in-time recovery only: an encrypted base backup now (section 6c) | `/opt/atp/deploy/atp.sh base-backup` |
| Release history | `/opt/atp/deploy/atp.sh releases` |
| The 500-user load test against this server (its own copy of the stack, section 12a) | `/opt/atp/deploy/ci/loadtest_500.sh` |
| The 500-session load test against the production stack itself, in NSE market hours, with memory sampled (section 12b) | `/opt/atp/deploy/atp.sh loadtest` |
| Remove a load-test run's synthetic organisation by hand (section 12b) | `/opt/atp/deploy/atp.sh loadtest-cleanup --list` |

Exit codes:

| Code | Meaning |
|---|---|
| `0` | ok |
| `1` | failed (the message says what, and the checkout is back on the release that still runs) |
| `2` | wrong usage |
| `3` | refused because of a market session: a migration while NSE is open, or a worker restart while any session (NSE, MCX until 23:30 IST, crypto) is open **and** positions are open. For `loadtest` the other way round: refused **outside** NSE market hours |
| `4` | `loadtest` only: the cleanup of its synthetic organisation was not verified - outranks every other outcome (section 12b) |
| `5` | `loadtest` only: its watchdog stopped the run because production degraded; cleanup verified |
| `6` | `loadtest` only: refused, not enough free memory; nothing created |
| `10` | owner action needed (the message names it: a `.env` key or a deploy key; a flag in Admin → Controls only if someone changed one after install - section 4a). For `loadtest`: refused, the deployment is not PAPER-only Phase 0 |
| `20` | `SECRETS_ENCRYPTION_KEY` changed |

Never run `atp.sh` with `bash -x`. Tracing would print the values it reads from `.env`; its normal output never does.

## 2. First time, step 1: bootstrap (as root, once)

1. In hPanel: note the static IPv4 (`<SERVER_IP>`), add your SSH public key (VPS → Settings → SSH keys), and open
   22/80/443 in the VPS firewall if the panel has one.
2. `ssh root@<SERVER_IP>`
3. Paste the `bootstrap.sh` line from `docs/DEPLOY_HOSTINGER_ONELINERS.txt`.

This creates the `atp` user with root's SSH key, turns on key-only SSH (no root login once `atp` has a key), sets
ufw to allow only 22, 80 and 443, and installs fail2ban, unattended security upgrades (no automatic reboot), Docker
with the compose plugin, 2 GB of swap, the Asia/Kolkata time zone and chrony. It also creates `/opt/atp` and
`/var/backups/atp-{offsite,restore-test,files}`.

Before you close the root window, open a **new** terminal and check that `ssh atp@<SERVER_IP>` works.

If you clone first, you can re-run the same hardening later with `sudo /opt/atp/deploy/atp.sh bootstrap`.

## 3. First time, step 2: get the code (as atp, once)

1. `ssh atp@<SERVER_IP>`
2. Paste the `install.sh` line from `docs/DEPLOY_HOSTINGER_ONELINERS.txt`.
3. It checks GitHub's SSH host key against GitHub's published ed25519 fingerprint
   (`SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU`) and refuses on a mismatch.
4. It prints a read-only deploy key and stops (exit 10). Add the key at GitHub → AdvanceTradingPlatform → Settings →
   Deploy keys, with **Allow write access off**. Then paste the same line again.
5. It clones the repository into `/opt/atp` and hands over to `/opt/atp/deploy/atp.sh install`. That run stops
   (exit 10) until `.env` exists (section 4).

## 4. `.env` (secrets live only on the server)

The script never creates or changes `.env`, never prints a value from it, and never puts one on a command line. You
create the file:

```
cp /opt/atp/.env.example /opt/atp/.env && chmod 600 /opt/atp/.env && nano /opt/atp/.env
```

`atp.sh` reads `.env` the way docker compose does: the last assignment wins, a quoted value runs to its closing
quote, and in an unquoted value a space followed by `#` starts a comment.

Required. `atp.sh` refuses to run while any of these is empty, `CHANGE_ME`, or a dev default:

| Key | Value |
|---|---|
| `POSTGRES_PASSWORD`, `JWT_SECRET_KEY`, `METRICS_TOKEN`, `BACKUP_ENCRYPTION_PASSPHRASE` | `python3 -c "import secrets;print(secrets.token_urlsafe(48))"` (a different value for each key). Keep a copy of `BACKUP_ENCRYPTION_PASSPHRASE` off the server: without it no backup can be restored or verified (section 6a lists every value to keep). `POSTGRES_PASSWORD` sits inside a database URL, so it must be URL-safe: letters, digits, `-` and `_` only (`token_urlsafe` gives exactly that), never `@ : / ? # %`. |
| `SECRETS_ENCRYPTION_KEY` | The **same** key the PAPER PC uses. Never a new one: every stored broker and AI credential depends on it. The first deploy records a salted scrypt fingerprint of it (`.deploy/secrets_key.fingerprint` with a random per-host salt). After that, any run with a different key stops with exit 20. `health` also checks inside the API container that the key opens every organisation's data key. |
| `DOMAIN`, `ACME_EMAIL` | Section 5. |

Also set:

```
ENVIRONMENT=production
SUPER_ADMIN_EMAILS=<the email you will register with>   # your account gets Admin -> Controls (section 4a)
CADDYFILE=Caddyfile.ip            # Caddyfile once DNS points at the server (section 5)
OFFSITE_REMOTE=offsite:atp-backups   # section 6: "offsite:" + your bucket name
OFFSITE_S3_PROVIDER=Cloudflare
OFFSITE_S3_ENDPOINT=              # section 6 (owner blocker until filled)
OFFSITE_S3_ACCESS_KEY=
OFFSITE_S3_SECRET_KEY=
OFFSITE_LOCK_DAYS=14              # section 6 step 3: the bucket lock's days (never less than what you set there)
# COMPOSE_PROFILES=research       # optional services; atp.sh never adds a profile on its own
# BACKUP_EXTRA_DIRS=/srv/atp-blobs   # optional: host directories to include in the encrypted files archive
```

The script reads these optional settings too, but only to report on them (it never changes them):

- `LIVE_*` switches: a warning when any is on (Phase 0 is PAPER only).
- `HISTORY_OWNER_TENANT_ID` and `HISTORY_OWNER_TENANT_NAME` (HB0): a warning when the ID is set without the name.
- The four `R2_*` history-import keys (HB0): a warning when only some are set. This is a read-only token, separate
  from the backup token.
- `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET` (Google sign-in, GS-1): optional. Both empty = no Google
  button (the default, never a failure); a warning when only one is set. Create them in the Google Cloud console
  (docs/OPEN_QUESTIONS.md GS-1): a Web application OAuth client, authorised JavaScript origin `https://aialgodesk.tech`,
  authorised redirect URI `https://aialgodesk.tech/api/auth/google/callback`. The button appears after the next deploy.
  Google sign-in signs in an existing, active account whose e-mail equals the Google account's verified e-mail; while
  the `self_signup` flag is off it never creates an account, and two-factor authentication is still asked for.
- `ATP_TELEGRAM_BOT_TOKEN` (UX2 fix 5, "Connect Telegram" in one tap): optional. Empty = the Connect Telegram button
  says Telegram alerts are not set up yet (never a failure). To turn it on: in Telegram, open @BotFather, send
  `/newbot`, name it (for example "AiAlgoDesk alerts") and give it a username ending in `bot`; put the token it gives
  you here (never in git or chat). `ATP_TELEGRAM_BOT_USERNAME` may stay empty (the app asks Telegram). Without
  `ATP_TELEGRAM_WEBHOOK_SECRET` the app reads the bot's new messages itself while a user waits on the Connect screen,
  so nothing else is needed. With a secret (`python3 -c "import secrets;print(secrets.token_urlsafe(32))"`), register
  the webhook once after the deploy as the platform admin: `POST /api/admin/telegram/platform/register`. Users then
  tap Connect Telegram, press Start in Telegram, and their alerts arrive there; no token or chat id is typed by anyone.

- **Browser and phone notifications (readiness B8).** `health` prints one line for each.
  - `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` (browser notifications, Web Push). Without the key the server makes a
    throwaway one at every start, so a browser that subscribed would stop getting notifications after the next deploy
    without anyone noticing. The app therefore **refuses new browser subscriptions** until the key is set, and
    `health` warns: `browser notifications (Web Push): VAPID_PRIVATE_KEY is not set`. To set it, once:
    1. Make a key pair with the backend image (it prints two lines and changes nothing):
       `su - atp -c "cd /opt/atp && docker compose -p atp exec -T backend python -m app.alerts.webpush"`
    2. `nano /opt/atp/.env`: paste the `VAPID_PRIVATE_KEY=...` line, and set `VAPID_SUBJECT=mailto:<your contact e-mail>`.
       The `VAPID_PUBLIC_KEY` line is for your notes only (the app works it out from the private key).
    3. Keep the private key in your password manager (section 6a). Never change it later: a new key drops every
       browser's subscription (users must press Enable again).
    4. Deploy (the one deploy line). `health` then says `VAPID_PRIVATE_KEY set - subscriptions survive restarts`.
  - `FCM_SERVICE_ACCOUNT_JSON_BASE64` (phone notifications for the Android app; optional, MA-2 in
    docs/OPEN_QUESTIONS.md): the Firebase service account key file, base64 on one line (`base64 -w0 <file>` on your
    own computer), pasted as the value. `FCM_PROJECT_ID` may stay empty (it is read from the key).
    `FCM_SERVICE_ACCOUNT_FILE` is an alternative path inside the containers and needs a mount; the base64 value needs
    none. Empty = nothing is sent to phones (an `info` line, never a failure). Phones get notifications only after the
    `mobile_push` flag is also turned on in Admin → Controls.

Then run `/opt/atp/deploy/atp.sh install`.

## 4a. Phase 0 flags (nothing to set)

There is no flag for you to set. You cannot reach Admin → Controls before the app is deployed, so a Phase 0 install
starts in the safe state by itself (owner decision of 2026-10-12):

- The host runs with `ATP_DEPLOYMENT_PHASE=phase0`. `docker-compose.hostinger.yml` and `atp.sh` set it by default, so
  `.env` needs no line for it.
- On first boot the API (and the worker) store **`live_trading` OFF** and **`phase0_paper_only` ON**, wherever the
  stored flags have no entry for them yet. Each write is in the audit log as `phase0_flag_seeded`.
- They never overwrite a stored entry. A change you make later in Admin → Controls survives every restart and
  deploy.
- While `phase0_paper_only` is on:
  - new LIVE entries are refused at the entry checks and at the broker-order chokepoint;
  - the worker does not evaluate LIVE deployments;
  - Admin → Controls refuses to turn `live_trading` on (audited as `feature_flag_refused`).
- Exits of any existing LIVE position always run (ADR-0004).
- `/api/system/health/deep` shows why under `checks.live_paths`.

`atp.sh` itself still never writes a flag. It reads them with `scripts/flag_posture.py` inside the API container:

- `install` and `health` end with `health: OK` (exit 0), with the line
  `deployment phase: phase0 ... nothing for the owner to set`.
- They stop with **exit 10** only if someone later turns `live_trading` on, or `phase0_paper_only` off (for
  example, an owner ending Phase 0).
- A deploy compares the stored flags before and after and fails if they changed. The one change it accepts is the
  release's own first-boot seed of an entry that was absent (`live_trading` OFF, `phase0_paper_only` ON).

Leaving Phase 0 is a later owner decision, not an install step:

1. Set `ATP_DEPLOYMENT_PHASE=standard` in `.env`.
2. Turn `phase0_paper_only` off in Admin → Controls.
3. Turn `live_trading` on.

`health` then warns "the Phase 0 defaults are not seeded on this host".

Set both flags explicitly in Admin when you leave Phase 0. If the first-boot seed never stored them (for example, the
stored flags row was unreadable and the seed skipped it), switching the mode puts any absent entry back on the normal
default: `live_trading` ON (it is a kill flag) and `phase0_paper_only` OFF. In Phase 0 mode those absent entries
resolved to the safe values only because of the mode.

## 5. DNS and TLS

- **No domain yet:** `DOMAIN=<SERVER_IP>`, `ACME_EMAIL=ops@localhost`, `CADDYFILE=Caddyfile.ip`. Caddy serves HTTPS
  on the IP with its own internal certificate. The browser warns once. Plain HTTP only redirects, because the login
  cookie is Secure.
- **Domain:** add an `A` record `atp.<your-domain>` → `<SERVER_IP>` (and `AAAA` only if the server has IPv6). When
  `dig +short atp.<your-domain>` shows the IP, set `DOMAIN=atp.<your-domain>`, `ACME_EMAIL=<your email>` and
  `CADDYFILE=Caddyfile`, then run `/opt/atp/deploy/atp.sh deploy`. Caddy gets a Let's Encrypt certificate over
  ports 80/443 and renews it itself.
- **Production (done 2026-10-21):** the platform runs at **https://aialgodesk.tech** (`DOMAIN=aialgodesk.tech`,
  `CADDYFILE=Caddyfile`). `www.aialgodesk.tech` gets its own certificate and only sends visitors to the apex with a
  permanent (301) redirect, path and query kept. Its DNS record may be an `A` record to the server or a `CNAME` to the
  apex; both work. `docker-compose.prod.yml` derives `ALLOWED_ORIGINS`, `FRONTEND_URL` and `PUBLIC_BASE_URL` from
  `DOMAIN`, so no address is written anywhere else. The Upstox redirect URL is
  `https://aialgodesk.tech/api/broker/upstox/oauth/callback`: set it in the Upstox developer app, and if Upstox was
  connected while the server answered on its IP, store the Upstox credentials again with this Redirect URI (the one
  saved then still wins).
- A deploy or rollback recreates the `caddy` container when the Caddyfile it runs differs from the checkout's (about a
  second without the edge; certificates are kept), because Caddy reads the file through a single-file mount.
  **Once, for the release that brings this:** it is deployed by the previous `atp.sh`, which does not do that yet - run
  `atp.sh deploy` a second time right after (outside sessions with open positions), then `atp.sh health`.
- **Recovery without DNS:** `CADDYFILE=Caddyfile.ip` with `DOMAIN=<SERVER_IP>` still works exactly as before (internal
  certificate, no www site). Switch back to `CADDYFILE=Caddyfile` and `DOMAIN=aialgodesk.tech` when DNS is back.
- `health` checks `https://$DOMAIN/api/system/health` through Caddy on the server itself, so it does not depend on
  DNS from outside. With `CADDYFILE=Caddyfile` it also checks:
  - through Caddy on the server (a FAIL if wrong, since it comes from the release's Caddyfile): the apex's security
    headers (HSTS, CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy), and that `www.$DOMAIN/...` answers
    301 to `https://$DOMAIN/...` with the path and query kept;
  - through public DNS with the certificate verified (a WARN only - DNS and certificates are not part of a release, so
    they never fail a deploy or a rollback): the apex health address and the www redirect.

## 6. R2 off-site backups (owner blocker)

Everything that goes to R2 is encrypted and sealed on this server first (AES-256 with `BACKUP_ENCRYPTION_PASSPHRASE`,
plus an HMAC seal). The WAL archive never goes (section 6c). Do these steps in the Cloudflare dashboard, then on the
server. Values go only into `/opt/atp/.env` and your password manager (section 6a), never into chat or git.

1. **Bucket.** Cloudflare → R2 → **Create bucket**: name `atp-backups`, location hint Asia-Pacific. Public access
   stays off (no public URL, no custom domain).
2. **Versioning.** If the bucket's Settings offer **object versioning**, turn it on: an overwritten or deleted object
   can then still be recovered. `atp.sh` works the same either way (OPEN_QUESTIONS RDY-14).
3. **Bucket lock (required).** Bucket → Settings → **Bucket lock rules** → Add rule: prefix empty (the whole
   bucket), retention **14 days**. Inside those days nobody can delete or overwrite an object, not even someone who
   has stolen the token. If you choose more than 14 days, put the same number in `.env` as `OFFSITE_LOCK_DAYS`:
   `atp.sh` never asks R2 to delete an object younger than that number plus one day.
4. **Lifecycle rules.** Bucket → Settings → **Object lifecycle rules**: keep only the rule that aborts incomplete
   multipart uploads (R2 adds it by default). Add **no** rule that deletes objects by age: retention is done by
   `atp.sh` by count (step 8), and an age rule would delete the monthly copies. If an older "delete after 35 days"
   rule exists from the earlier version of this runbook, delete that rule.
5. **API token, this bucket only.** R2 → **Manage API tokens** → **Create API token**: permission
   **Object Read & Write**, **Apply to specific buckets only** → `atp-backups`, TTL none, no IP filter (or the
   server's IP only). Not an admin token: this token cannot delete the bucket or change its lock. It is a different
   token from the read-only `R2_*` one used for the history import. Cloudflare shows the **Access Key ID**, the
   **Secret Access Key** and the **S3 endpoint** (`https://<account id>.r2.cloudflarestorage.com`) **once**: copy all
   three straight into your password manager (section 6a).
6. **`.env` on the server** (`nano /opt/atp/.env`, as `atp`). Set these names; `atp.sh` and the compose files read
   exactly these:

   | Name | What to put |
   |---|---|
   | `OFFSITE_REMOTE` | `offsite:atp-backups` (`offsite:` and the bucket name from step 1) |
   | `OFFSITE_S3_PROVIDER` | `Cloudflare` |
   | `OFFSITE_S3_ENDPOINT` | the S3 endpoint from step 5 |
   | `OFFSITE_S3_ACCESS_KEY` | the Access Key ID from step 5 |
   | `OFFSITE_S3_SECRET_KEY` | the Secret Access Key from step 5 |
   | `OFFSITE_LOCK_DAYS` | `14`, or the retention days from step 3 if you chose more |

   Optional, defaults shown (retention, section 11): `BACKUP_RETENTION_DAYS=7`, `BACKUP_KEEP_MIN=7`,
   `BACKUP_KEEP_WEEKLY=4`, `BACKUP_KEEP_MONTHLY=6`, `BACKUP_FILES_KEEP=14`, `OFFSITE_KEEP_PRE_MIGRATION=6`, and the
   health / alert limit `BACKUP_MAX_AGE_HOURS=26`.
7. **First copy and check:** `/opt/atp/deploy/atp.sh backup --verify && /opt/atp/deploy/atp.sh health`. Expected:
   `encrypted, sealed database dumps (daily, weekly, monthly) and the files archive copied to offsite:atp-backups`,
   `old R2 copies pruned`, `seal (HMAC) verified`, `R2 backup ... restored`, `rows (copy / live): ...`,
   `audit hash chain intact`, `backup: done`; then in `health`: `last off-site copy (R2) ... (0 h ago)`.
8. **From then on, nothing to do.** Every night at 02:17 IST the cron block runs `backup --scheduled --verify`: the
   dump, its R2 copy, the R2 retention, and a restore of the newest R2 copy into a scratch database. Every Sunday at
   03:43 IST `restore-check` runs once more. `health` shows the last good backup, local and off-site, and fails when
   either is older than 26 hours.

Until step 6 is done, `backup` and `restore-check` work on the local copies and print `R2 not configured yet`. Pass
`--require-offsite` to make that a failure.

What R2 holds (prefixes) and how long:

| Prefix | What | Kept (settings in `.env`) |
|---|---|---|
| `backups/` | the nightly dumps (`atp_<db>_<UTC stamp>.dump.enc` + `.sha256` + `.hmac`) | the newest `BACKUP_KEEP_MIN` (7) and everything younger than `BACKUP_RETENTION_DAYS` + 1 (8) days |
| `backups-weekly/` | the first dump of each week | the newest `BACKUP_KEEP_WEEKLY` (4) |
| `backups-monthly/` | the first dump of each month | the newest `BACKUP_KEEP_MONTHLY` (6) |
| `backups/pre-migration/` | the dump taken before each migration | the newest `OFFSITE_KEEP_PRE_MIGRATION` (6) |
| `files/` | the encrypted files archive (`.deploy/` + `BACKUP_EXTRA_DIRS`) | the newest `BACKUP_FILES_KEEP` (14) |
| `backups/base/` | only with point-in-time recovery on (section 6c) | the newest `BASE_BACKUP_KEEP` (4) |

Nothing younger than `OFFSITE_LOCK_DAYS` + 1 days is ever deleted, whatever the counts say (so R2 briefly holds a
few more daily copies than the server). A monthly dump is about the size of one daily dump; with the defaults the
bucket holds about 30 dumps, well inside R2's free 10 GB for a database of a few hundred MB.

## 6a. Key escrow: what you keep off the server (do this once, now)

Every R2 object is ciphertext. If the server is lost, R2 alone is useless without the keys, and the server's `.env`
is the only other place they exist (the files archive leaves `.env` out on purpose). Keep each of these names' values
in **your password manager** and in **one offline copy** (printed on paper in a sealed envelope, or an encrypted USB
stick kept away from the laptop), never in git, chat, e-mail or a screenshot:

| Name in `/opt/atp/.env` | Why it cannot be replaced |
|---|---|
| `BACKUP_ENCRYPTION_PASSPHRASE` | opens and authenticates every backup; a different value opens none of them |
| `SECRETS_ENCRYPTION_KEY` | opens every stored broker / AI credential and MFA secret inside the database |
| `JWT_SECRET_KEY` | a new value works, but signs everyone out; keep it to restore without that |
| `POSTGRES_PASSWORD` | not inside the dumps; keep it so a restored server needs no new database login |
| `OFFSITE_S3_ENDPOINT`, `OFFSITE_S3_ACCESS_KEY`, `OFFSITE_S3_SECRET_KEY`, `OFFSITE_REMOTE` | how the new server reads R2. If they are lost, create a new bucket-scoped token (section 6 step 5) from your Cloudflare account |

Everything else in `.env` can be set again from its provider's console (`METRICS_TOKEN`: a new random value;
`ATP_TELEGRAM_BOT_TOKEN`: @BotFather; `GOOGLE_OAUTH_*`: Google Cloud console; `FCM_SERVICE_ACCOUNT_JSON_BASE64`: a new
key from the Firebase console; `DOMAIN`, `ACME_EMAIL`: you know them). Keep `VAPID_PRIVATE_KEY` too: a new one works,
but every browser must turn notifications on again.

**Check your copy matches.** `health` prints `escrow: SECRETS_ENCRYPTION_KEY unchanged since <date>` and
`escrow: BACKUP_ENCRYPTION_PASSPHRASE unchanged since <date>`. If you change the passphrase later, `health` says so
and the next backup records it: backups taken before the change still need the **old** value, so keep the old one in
escrow too until those backups have left R2 (6 months with the defaults, because of the monthly copies).

## 6b. Restore on a clean machine, from R2 alone

When the server is lost (disk, account, or the box itself). You need: a new Ubuntu 24.04 server, the escrowed values
(section 6a), and the R2 bucket. CI rehearses exactly this on every change to the deploy bundle (section 11a).

1. Bootstrap the new server (section 2) and get the code (section 3).
2. Create `/opt/atp/.env` (section 4) with the **escrowed values** for every name in the section 6a table, and the
   same `DOMAIN`, `ACME_EMAIL`, `CADDYFILE` as before. A new random `METRICS_TOKEN` is fine.
3. Fill the empty database from R2:

   ```
   su - atp -c "/opt/atp/deploy/atp.sh restore-offsite"
   ```

   It builds the images, starts Postgres only, refuses unless the database is **empty**, fetches the newest encrypted
   dump from R2, checks its seal (a wrong `BACKUP_ENCRYPTION_PASSPHRASE` stops here with nothing written), asks you
   to type the database name, restores it and prints the schema revision. It also fetches and checks the newest
   files archive and leaves it in `/var/backups/atp-restore-test` (unpack your `BACKUP_EXTRA_DIRS` from it by hand if
   you used them; `.deploy/` is rebuilt by the deploy).
4. Start the release on it, as always:

   ```
   su - atp -c "/opt/atp/deploy/atp.sh deploy && /opt/atp/deploy/atp.sh health"
   ```

   `health` runs `check_secrets_key.py`: `secrets key: ok - N organisation data keys open` proves the escrowed
   `SECRETS_ENCRYPTION_KEY` is the right one. Then point DNS at the new server (section 5) and sign in.

Data written after the newest R2 dump (at most one day) is not in it. The restore never touches a database that
already holds tables: on a running server use `rollback --restore-db` or `docs/OPERATIONS.md` 1.2.

## 6c. WAL archiving and point-in-time recovery (off on this host)

Postgres used to copy every WAL segment into the `wal_archive` volume, and the hourly off-site copy synced it to R2
raw. Nothing ever took a base backup, so the archive only grew (up to about 4.6 GB a day) and could never be
replayed (readiness review R1). Now:

- **Off by default** (`PG_ARCHIVE_MODE` unset or `off`). The recovery point is the nightly encrypted dump plus the
  dump taken before each migration. `archive_mode` is a restart setting: the first deploy with this change
  recreates the Postgres container once (a clean shutdown, data kept on its volume). Run that deploy after 15:30 IST.
- **What piled up before** stays on disk until you run `/opt/atp/deploy/atp.sh wal-cleanup` once. It refuses while
  the running database still archives. `health` lists the files and MB left, with the command.
- **The WAL archive never leaves the server**, in either mode: the off-site copy no longer mounts it.
- **To turn point-in-time recovery on later:** put `PG_ARCHIVE_MODE=on` in `.env`, run
  `/opt/atp/deploy/atp.sh deploy` (after 15:30 IST: Postgres restarts once), then
  `/opt/atp/deploy/atp.sh base-backup`. The deploy adds `29 4 * * 6 ... base-backup --scheduled` to the cron block:
  every Saturday an encrypted, sealed base backup (`pg_basebackup` inside the Postgres container) that is copied to
  R2 and prunes the archive before the oldest of the newest `BASE_BACKUP_KEEP` (4). `health` shows the last base
  backup and fails after 8 days without one. The archive itself stays on this server: recovery to an instant is for
  a mistake on a living server (`docs/OPERATIONS.md` 1.2a), and the off-site recovery point stays the nightly dump.

## 7. Firewall

- ufw (from the bootstrap) allows 22, 80, 443 (and 443/udp). The Hostinger panel firewall, if used, must allow
  the same ports.
- Docker publishes container ports **past ufw**. The compose files bind Postgres, Redis and the API to 127.0.0.1;
  only Caddy listens publicly. `health` fails if any public TCP port other than 22, 80 or 443 is listening.
- SSH is key-only, root login is off, and fail2ban watches sshd.

## 8. What `deploy` does, step by step

1. **Preflight.** Checks the tools and docker compose ≥ 2.24, that the checkout is clean (no edited files on the
   server), the `.env` rules from section 4, and that the key is unchanged. Reports profiles, LIVE switches, R2 and
   history settings without changing them.
2. **Release.** Fetches, resolves the ref to a commit, refuses anything not on `origin/main`, and checks out that
   commit (detached). Every failure from here until the new API is up puts the checkout back on the release that
   still runs.
3. Starts Postgres and Redis and waits for them to be healthy.
4. **Images.** Builds `atp-backend`, `atp-worker`, `atp-frontend` (and `atp-research-worker` with that profile),
   tagged with the first 12 characters of the commit (`deploy/compose.release.yml`). If the images already exist, it
   reuses them. `.deploy/images.log` records the image IDs and the infrastructure image digests for each release.
5. **Session gate.** `scripts/session_check.py` reads every exchange session from the app's own calendar (NSE/BSE,
   MCX until its 23:30 IST close, crypto, holidays included) and the open positions. While any session is open
   **and** positions are open, the deploy stops with exit 3 and nothing is restarted. Run it after every open
   session has closed, or set `ATP_ALLOW_SESSION_RESTART=1` for that one run if you accept a worker restart under
   open positions. Crypto never closes, so any open crypto position blocks the gate until it is closed or the
   override is set.
6. **Migrations.** Compares the database revision with the image's alembic head. If they differ:
   1. It takes an **encrypted, sealed pre-migration backup**. A copy is kept in `/backups/pre-migration/`, which the
      daily retention does not touch, for the last 3 releases that migrated. The backup is copied to R2 when
      configured.
   2. It runs `scripts/migrate_guard.py`. The guard refuses while NSE is open (exit 3) and otherwise runs
      `alembic upgrade head`.

   The script never runs `alembic downgrade`. A failed migration is logged as `failed` with its backup, so
   `rollback --restore-db` can undo it.
7. **Rolling restart, API first.** If the new API does not pass `/api/system/health/deep` in time, the run puts the
   checkout back. It then starts the previous API again, but only if the database is still safe for it (see
   section 9). The worker and frontend are never touched, the run is logged as `failed`, and it fails.
8. **Worker at once, then the rest.** It starts the worker (exactly one; `stop_grace_period` 90 s lets it finish the
   cycle and release its Redis lock), then the frontend, Caddy, backup and off-site services. It records the release
   in `/opt/atp/.deploy/releases.log`. Deploying the running release again is logged as `converge` and does not
   change what `rollback` goes back to.
9. **Health** (section 10). The restarted worker must report a heartbeat newer than its restart. The feature flags
   must be byte-for-byte the same as before; if they cannot be read, that counts as a failure, not as "unchanged".
10. It installs or refreshes the schedule (section 11), and keeps the images and pre-migration dumps of the last 3
    releases (`ATP_KEEP_RELEASES`).

## 9. Rollback, and when a migration makes it unsafe

- `rollback` starts the previous release's images, which are still on the server, so nothing is rebuilt. The
  session gate (section 8, step 5) applies.
- **Every migration declares `ROLLBACK_SAFE`.** `True` means the previous release's code keeps working on the
  upgraded schema (purely additive). `False` means it drops, renames, retypes or tightens something.
  - CI lints this: `scripts/migration_rollback_safety.py lint` fails on a missing marker, or on `True` next to a
    drop, rename, type change or NOT NULL without a server default, unless a reviewed `ROLLBACK_SAFE_REASON` is
    given.
  - The lint also flags: a dropped server default; a new UNIQUE / CHECK / FOREIGN KEY constraint or unique index on
    a table that existed before; SQL that adds constraints or rewrites rows (UPDATE / DELETE).
  - 18 older migrations are marked `False` (unknown or risky means False). Two are `True` with a reviewed reason,
    because their new key is on a new nullable column.
  - An author adding a migration chooses the line; the lint error shows both options.
- **Code-only (default).** If the database is ahead of the target release, the **newer** image checks two things:
  that the database revision descends from the target's head, and that every migration in between is
  `ROLLBACK_SAFE`.
  - If both hold, the older API starts with `MIGRATION_ALLOW_DB_AHEAD=<that exact revision>`. The guard accepts
    nothing else: no other revision and no "1".
  - `health` then warns `database is ahead` until the newer release is deployed again.
  - If either check fails, the code-only rollback is refused (exit 1, nothing restarted). Use `--restore-db`.
- **`rollback --restore-db`:**
  1. It shows the plan and asks you to type the database name. Nothing changes if you type anything else.
  2. It lists what the restore would orphan: positions open now, and orders placed after the backup was taken. If
     there are any, it refuses unless you type `ORPHAN`. Close the open positions first whenever you can.
  3. It checks the backup's seal, takes a safety backup, and restores the pre-migration backup into a **new**
     database next to the live one. It checks the copy's revision. The live database keeps serving meanwhile.
  4. Only then does it stop the API and worker, end every connection to both databases, and swap the two names in
     **one transaction**: the live database becomes `<db>_pre_rollback_<time>` (**kept, never dropped**), and the
     restored copy takes the live name.
  5. It starts the previous release's API and, as soon as that is healthy, the worker.

  If the swap fails, nothing was renamed. The release that was running before the rollback is started again on the
  unchanged database.
- After a deploy that **failed** after migrating, `rollback --restore-db` puts that pre-migration backup back and
  keeps the running code.
- `--restore-db` goes back exactly one release. To go further, run `rollback` again, once per release.
  `rollback --to <sha>` (code only) goes straight to an older release.
- After a `--restore-db`: if the least-privilege roles are in use (`MIGRATION_DATABASE_URL`, Phase O1), run
  `python scripts/init_db_roles.py` again, because restores do not carry grants.

**Rollback rehearsal.** This is a Phase 0 exit criterion. Do it outside market hours, before the first 50 users, and
again after any migration marked `ROLLBACK_SAFE = False`. Write the results in the go-live record.

- *Code-only, on the production server.* This is safe: nothing is restored, and the database is not touched.
  1. `atp.sh releases`: note the running release N and the one before it, N-1.
  2. `atp.sh rollback --dry-run`: read the plan.
  3. `atp.sh rollback`: expect `rolled back to <N-1>` and `health: OK`. Log in and open the dashboard.
  4. `atp.sh deploy <N>`: expect `deployed <N>`, `health: OK` and no `database is ahead` warning.
- *`--restore-db`, never on production data.* Rehearse it in one of two places:
  - On a scratch server (or the same bundle on a test VPS) restored from a copy of the latest backup.
  - On production, only right after a fresh deploy that migrated, when the pre-migration backup is minutes old and
    the book is flat (no open positions, so the orphan check shows 0/0).

  After the rehearsal, swap the kept database back so production keeps every row. Do not drop it:

  ```
  cd /opt/atp && C="docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.hostinger.yml -f deploy/compose.release.yml -p atp"
  $C stop backend worker
  $C exec -T postgres psql -U atp_user -d postgres \
    -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname IN ('advance_trading_platform', 'advance_trading_platform_pre_rollback_<time>') AND pid <> pg_backend_pid()" \
    -c 'BEGIN; ALTER DATABASE "advance_trading_platform" RENAME TO "advance_trading_platform_rehearsal_<time>"; ALTER DATABASE "advance_trading_platform_pre_rollback_<time>" RENAME TO "advance_trading_platform"; COMMIT;'
  /opt/atp/deploy/atp.sh deploy <N>
  ```

  Then run `atp.sh health` and `atp.sh restore-check`. The rehearsal copy stays until you decide it can go; the
  bundle never drops a database.

## 10. Health

`health` is read-only. It checks:

- **Containers:** each one is running and healthy, with exactly one worker.
- **API:** `/api/system/health` and `/api/system/health/deep` (database, migrations, worker heartbeat, Redis). After
  a deploy or rollback, the worker's heartbeat must be newer than its restart.
- **Public HTTPS:** the health URL through Caddy.
- **Release:** the running release, and whether the database is ahead of the code.
- **Encryption key:** the `SECRETS_ENCRYPTION_KEY` fingerprint, and `scripts/check_secrets_key.py` inside the API
  container. That script opens every organisation's data key and prints only counts.
- **Flags (section 4a):** the deployment phase (`phase0`: the first boot seeded the safe flags, nothing to set), then
  exit 10 only if `live_trading` is on (for anyone) or `phase0_paper_only` is off. The script never changes a flag.
- **Settings:** the LIVE switches, R2 and the HB0 settings.
- **Backups:** the last good backup, local (`last database backup <time> (N h ago)`) and off-site
  (`last off-site copy (R2) <time> (N h ago)`); either one older than `BACKUP_MAX_AGE_HOURS` (26) is a FAIL here and a
  WARN inside a deploy or rollback. Also the hourly copy's last line, the last restore check, the WAL archive (off and
  empty, or the files left with `wal-cleanup`; with point-in-time recovery on, the last base backup), whether the
  cron block is installed and current, and the escrow lines (section 6a). `atp.sh backup-status` gives the same ages
  as one JSON line for an alert.
- **Host:** public ports and disk use.

It prints `health: OK` (exit 0), `health: FAILED` (exit 1), or `OWNER ACTION NEEDED` (exit 10). The status page
(Z10 G7) will read the same endpoints.

## 10b. Alerts to you (A2): the outside check, Telegram, e-mail and the error tracker

`health` answers only when someone runs it. Four things now watch the platform by themselves; the first one is the
**primary** outside monitor (RDY-21):

0. **A free outside uptime service** (step e below; you create it once). It lives outside GitHub and outside the
   Hostinger host, checks every few minutes and alerts you by Telegram and e-mail. This is the one to trust for
   "the site is down".

1. **The server checks** (`atp.sh ops-check`, every 5 minutes from the crontab that every `deploy` installs). The host
   facts (the backup status of section 11, disk use, the HTTPS certificate's days left) go to
   `backend/scripts/ops_check.py`, which adds the app's own checks: the API's error rate, the trading worker's
   heartbeat, the data feed, broker logins (counts across all users), the database size, user alert delivery and
   failed sign-ins. A problem is messaged once it has held for two runs (10 minutes); a failure that stays is
   repeated every 6 hours; "OK again" is messaged once. A run while a deploy or a backup holds the lock is skipped.
   Thresholds are settings (`OPS_*` in `backend/app/core/config.py`); the log is `.deploy/logs/ops-check.log`.
   Admin console → System → **Alerts** shows the same checks, "Check now" and "Send a test".
2. **The second-opinion outside check** (GitHub Actions, `.github/workflows/uptime.yml`, every 10 minutes): `/` and
   `/api/system/health` from GitHub's network. Two failed rounds in a row (90 s apart) send **one** private Telegram
   message to you; when the site answers again, **one** "back up" message. Nothing is repeated while it stays down.
   The repository is public, so it **never** opens a GitHub issue or comment about an outage, and the workflow can
   only read the code (`contents: read`). What it already sent is remembered between runs in the Actions cache.
   Without the two Telegram secrets (step d) it sends nothing and only writes a plain notice in its run log; the run
   stays green either way. The run log of a public repository can be read by anyone, so it holds only "up / down"
   and the two public addresses - never a chat id or a token.
3. **The error tracker** (off until you give it a DSN): server errors with personal data removed.

Your steps (once; names only - the values go into `/opt/atp/.env` or GitHub settings, never into git or chat):

| Step | Where | What |
|---|---|---|
| a. Telegram for server alerts | Admin console → System → Alerts → **Connect Telegram** | Press Start in the chat that opens. Needs the platform bot (`ATP_TELEGRAM_BOT_TOKEN`, section 4) - the same bot users connect to; your operator chat is kept apart from every organisation. Alternative: the chat id in `OPS_ALERT_TELEGRAM_CHAT_ID`. Then **Send a test**. |
| b. E-mail for server alerts | `.env` | `PLATFORM_SMTP_HOST`, `PLATFORM_SMTP_PORT`, `PLATFORM_SMTP_USERNAME`, `PLATFORM_SMTP_PASSWORD`, `PLATFORM_SMTP_FROM` (any SMTP provider, e.g. Brevo or Zoho free tiers). Alerts go to `OPS_ALERT_EMAIL` (comma-separated) or, when empty, to `SUPER_ADMIN_EMAILS`. |
| c. (no longer needed) | - | The outside check used to open a GitHub issue; since RDY-21 it never does (public repository). There is nothing to set in GitHub's notification settings. |
| d. Telegram for the second-opinion check | Repository → Settings → Secrets and variables → Actions → New repository secret | `UPTIME_TELEGRAM_BOT_TOKEN` (a bot from @BotFather - a separate one is best, so a server problem with the platform bot cannot silence it) and `UPTIME_TELEGRAM_CHAT_ID` (your private chat with that bot: send it any message first; your chat id is your own Telegram user id, which @userinfobot shows). Never a group or channel others can read. To test: Actions → "Uptime (outside check)" → Run workflow (it only messages you when the site is down). |
| e. **The primary monitor: a free uptime service** (RDY-8 / RDY-21) | e.g. UptimeRobot or Better Stack, free plan | See "Set up the primary monitor" below. |
| f. Error tracker (optional) | GlitchTip (glitchtip.com, free plan) or Sentry (free plan) → new project, platform Python | Copy the project's DSN into `.env` as `ERROR_TRACKER_DSN` (optionally `ERROR_TRACKER_ENVIRONMENT=production`). Deploy. Admin → System → Alerts then says "Error tracker: On". Leave empty to keep it off. |
| g. Keep the scheduled check alive | Repository → Actions | See "When GitHub pauses the scheduled check" below. |

**Set up the primary monitor (step e, once, about 10 minutes).**

1. Sign up for a free uptime service (UptimeRobot or Better Stack free plan; any service with HTTP checks and
   Telegram / e-mail alerts works). Use your own e-mail; turn on its two-factor sign-in if offered.
2. Add the alert contacts: your e-mail (confirm the link it sends) and Telegram (the service's own bot: open the link
   it shows and press Start in your private chat).
3. Add **two HTTP(S) monitors**, interval 5 minutes (the free plan's shortest is fine):
   - `https://aialgodesk.tech/` - expect HTTP 200;
   - `https://aialgodesk.tech/api/system/health` - expect HTTP 200; if the service offers a keyword check, the keyword
     `"ok"` (the body is `{"status": "ok"}`).
4. Alert only after **2 failures in a row** (the setting is usually called "confirmation", "confirm after" or
   "alert after N failures"; where the service counts time instead of checks, use twice the interval), so one dropped
   packet does not wake you. Turn on the "back up" (recovery) message too.
5. Optional: its free hosted status page with both monitors can be the footer's Status link - build the frontend with
   `VITE_STATUS_PAGE_URL=https://...` (RDY-11). Do not put the status page link anywhere that implies a promise of
   uptime.
6. Test once: pause the `/api/system/health` monitor's expectation (e.g. change the keyword to something wrong), wait
   for two checks, check that the Telegram and e-mail alerts arrive, then set it back and wait for the recovery message.

**When GitHub pauses the scheduled check (60 days).** GitHub disables a scheduled workflow in a public repository
after 60 days without repository activity (no push, no merged PR). The uptime cron then stops silently - no message
says so. How this is handled:

- The **primary monitor is outside GitHub** (step e), so an outage is still reported while the cron is paused.
- To re-enable: github.com → the repository → **Actions** → "Uptime (outside check)" in the list on the left → the
  banner "This scheduled workflow is disabled" → **Enable workflow**. Then **Run workflow** once to check it.
- Once a month (with the other monthly checks), open Actions → "Uptime (outside check)" and check that the newest run
  is less than an hour old. An automatic reminder in the server checks (`ops-check`) was considered and left out for
  now: it would add a call to GitHub's public API and an eleventh check to Admin → System → Alerts for a case the
  primary monitor already covers (RDY-21).

What the messages never contain: an e-mail address, a name, a chat id, a token or a host name - states and counts
only. The error tracker removes tokens, keys, e-mail addresses, phone numbers, cookies, query strings and request
bodies before an event leaves the server (`backend/app/observability/error_tracker.py`).

The public status page (`/status`, flag `public_status_page` in Admin → Controls, off until you turn it on) shows
up / degraded / maintenance and the open incidents. Open an incident in the admin console → System → Incidents and give it a
**public note** (one sentence, at most 280 characters) - only that note is shown, never the title or the cause. The
footer's Status link appears once the page is on (or at once with `VITE_STATUS_PAGE_URL`). The in-app page lives on
this server, so during a full outage use the uptime service's page.

## 10a. PWA install on Android (manual check, D3)

The automated test (`frontend/e2e/pwa.e2e.ts`) proves the manifest, the service worker and Chromium's installability
check on localhost. Only a real phone proves the install on the production domain. Do this once after the domain
deploy, and again after any change to `frontend/public/manifest.webmanifest`, `frontend/public/sw.js` or the TLS setup.

You need an Android phone with Chrome, and a PAPER account you can sign in to.

1. **Open the site.** In Chrome on the phone, open `https://aialgodesk.tech`. The padlock must show with no warning.
   Sign in.
2. **Install.** Either tap **Install app** in the banner at the top of the page, or open Chrome's menu (the three dots
   at the top right) and tap **Install app** (older Chrome: **Add to Home screen**, then **Install**). Confirm.
   - Expected: Chrome says the app is installed, and an **ATP** icon appears on the home screen (or in the app
     drawer).
   - If the menu shows only **Add to Home screen** with no **Install** step, the browser did not see an installable
     app. Open `chrome://serviceworker-internals` on the phone to check that a worker for `https://aialgodesk.tech/`
     is listed and running, then report what it shows.
3. **Open it from the home screen.** Tap the **ATP** icon.
   - Expected: the app opens in its own window with no Chrome address bar, on the dashboard. The Android recent-apps
     view shows it as **ATP**, separate from Chrome.
   - Optional, new layout (ui_v2) only: the More menu no longer shows **Install the app** inside the installed app.
4. **Turn on notifications.** In the installed app: **Settings → Alert delivery → Push notifications → Enable on this
   device**, read the explanation, press **Continue**, then tap **Allow** when Android asks.
   - Expected: the section lists this device. If Android shows "Notifications are blocked", open the phone's
     **Settings → Apps → ATP → Notifications** (or Chrome's site settings for `aialgodesk.tech`) and allow them.
5. **Send a test alert.** In the same section press **Send test**.
   - Expected: within a few seconds a system notification from ATP arrives, even with the app in the background.
     Tapping it opens the app. The **Last push deliveries** list shows the test as delivered.
6. **Write down** the phone model, Android version, Chrome version (Chrome menu → Settings → About Chrome) and the
   result of each step, and send them to the team.

To uninstall: long-press the ATP icon → **Uninstall** (or App info → Uninstall). It removes only the installed
window; the account and its alert settings stay on the server.

## 11. Backups and how they are verified

| What | When | Where |
|---|---|---|
| Postgres dump (`pg_dump -Fc`, AES-256, SHA-256 sidecar, HMAC seal). Daily: older than `BACKUP_RETENTION_DAYS` (7) go, never fewer than `BACKUP_KEEP_MIN` (7). | Compose `backup` service every 24 h; on `atp.sh backup`, which also seals the newest | volume `backups` |
| Weekly and monthly copies: the first dump of each week / month, as a hard link (the same file, no extra space). The newest `BACKUP_KEEP_WEEKLY` (4) / `BACKUP_KEEP_MONTHLY` (6) are kept. | with each dump | `backups/weekly/`, `backups/monthly/` |
| Pre-migration dumps | before every migration | `backups/pre-migration/` (last 3 releases, outside the daily retention) |
| WAL archive | **off** (section 6c); with `PG_ARCHIVE_MODE=on`: continuous, pruned by the weekly base backup, never copied off the server | volume `wal_archive` |
| Files archive: `.deploy/` state plus `BACKUP_EXTRA_DIRS`, tar.gz, AES-256, SHA-256, HMAC seal. The newest 14 are kept. | cron 02:17 IST daily (`backup --scheduled --verify`, which skips the dump if the service dumped within 20 h and seals the newest) | `/var/backups/atp-files` |
| Off-site copy of the encrypted, sealed files above | `offsite` service hourly; `atp.sh backup` (then prunes R2 to the same counts) | R2 `atp-backups/{backups,backups-weekly,backups-monthly,files}` (section 6) |
| Restore check | cron daily 02:17 IST (`--verify`, the newest R2 copy) and Sunday 03:43 IST; `atp.sh restore-check` | `.deploy/restore-checks.log` |

**Only encrypted, sealed dumps are restored.**

- With `BACKUP_ENCRYPTION_PASSPHRASE` set, `restore.sh` refuses a plain `.dump`, an unsealed dump, and a dump that
  does not match its seal.
- `restore-check` fetches only `.dump.enc` files from R2.
- The seal is the protection: a dump that was not sealed by `backup.sh` with your passphrase is never restored, and
  the seal covers the file's name, so a sealed dump cannot be replayed under a newer name.
- As a second line, the scratch copy is restored over a connection that **logs in as** `atp_restore`, a
  non-superuser role that owns the scratch database. It gets a one-run random password and is set back to NOLOGIN
  afterwards. Archive SQL cannot get superuser rights back (a `SET ROLE` would not do, because `RESET ROLE` undoes
  it), so even a bad dump cannot run `COPY ... TO PROGRAM` on the cluster.
- Every check that reads the copy afterwards (row counts, `alembic_version`, the audit hash chain) logs in as
  `atp_restore` too, never as the superuser: a view or function the dump created runs with the rights of whoever
  queries it. `restore-check` gives the role a fresh password for the chain check and sets it back to NOLOGIN right
  after; if the role cannot log in, the chain is reported `not_checked` (a failure), never re-run as the superuser.

**Seal.** `openssl enc` has no authenticated mode. A `.sha256` file can be recomputed by whoever changed the dump, so
`scripts/backup/backup.sh` seals every dump where it is made, and `atp.sh` seals the files archive. A seal is a
`.hmac` sidecar: HMAC-SHA256 over the ciphertext, with a key derived from `BACKUP_ENCRYPTION_PASSPHRASE` under its own
label, so the MAC key is separate from the encryption key. A missing seal, or one that does not match, fails the
check.

The backup service runs its own image, `atp-backup:<release>` (`deploy/backup.Dockerfile`): `postgres:17-alpine`
plus the `openssl` command. The base image ships only the OpenSSL libraries, and without the command no dump could be
encrypted or sealed. CI runs the backup scripts inside that image.

The database URL's password never reaches a command line. The backup scripts take it from `DATABASE_URL` into
`PGPASSWORD` (`scripts/backup/common.sh` `pg_setup`).

`restore-check` does the following:

1. Downloads the newest dump and its `.sha256` and `.hmac` from R2 into `/var/backups/atp-restore-test`, and checks
   the seal.
2. Runs `scripts/backup/verify_backup.sh` on the dump. That script checks the SHA-256, decrypts, restores into a
   **scratch** database, and compares the alembic revision and the `tenants`, `orders`, `trades` and `audit_logs`
   row counts with the live database. The copy may hold fewer rows than live, but not more, and not fewer than half
   (`VERIFY_MIN_RATIO`).
3. Runs the audit hash-chain check (`python -m app.audit.verify_chain`) against the scratch copy inside the backend
   image, then drops the copy.
4. Right after a migrating deploy, the newest daily dump predates the migration. That is reported as a warning
   (the restore worked); `atp.sh backup --verify` makes a current restore point.
5. Downloads the newest files archive, checks its seal, decrypts it and lists its contents.

Production is never written to. The market data lake is in Postgres (ADR-0013 hot tier), so the dump covers it.

**A real restore** (the database is lost): `atp.sh rollback --restore-db` covers "undo the last release". For a lost
database, follow `docs/OPERATIONS.md` 1.2 and 1.2a (`restore.sh` or the PITR drill) with the backup service's
entrypoint overridden:
`docker compose ... run --rm --entrypoint sh backup /scripts/restore.sh <file>`.

## 11a. The restore rehearsal (CI)

`deploy/ci/offsite_rehearsal.sh` runs in the `offsite` job of `.github/workflows/staging-stack.yml` (on every push to
`main` and on any branch that changes the deploy bundle). It uses the real `atp.sh` and compose files on a throwaway
clone, with an S3 stand-in for R2 (`rclone serve s3` in the offsite service's own image, over a directory on the
runner):

1. `deploy`, with R2 configured; a user signs up through the API;
2. `backup --verify`: the encrypted dump and files archive go to the bucket, come back, and restore into a scratch
   database (rows, schema, audit chain); the bucket is checked: sealed dumps under `backups/`, `backups-weekly/`,
   `backups-monthly/`, the files archive, nothing in the clear, no WAL;
3. `health` and `backup-status` show the off-site copy at 0 h;
4. the server is lost: `down -v` removes the database and the backups volume; the clone, `.env` and files archive
   are deleted. Only the bucket and the section 6a values are kept;
5. a new clone with a `.env` built from those values only, then `restore-offsite` and `deploy`: deep health, the
   secrets key opens every organisation's data key, the user from step 1 signs in, the audit chain is intact.

The report is the job summary (artifact `offsite-rehearsal`). `backend/tests/test_backup_scripts.py` runs the same
path at script level on every PR (backup -> off-site copy -> server gone -> fetch -> seal -> restore into a new
database -> rows and audit chain). The bucket lock itself cannot be rehearsed in CI: check it once in the Cloudflare
dashboard (section 6 step 3).

## 12a. The 500-user load test on this server (one command)

CI runs the 500-user load test on every push to main against a copy of the stack on a GitHub runner
(`docs/reports/load_test_500.md`). To measure this server itself - its CPU, its disk, its Caddy - run, as `atp`:

```
/opt/atp/deploy/ci/loadtest_500.sh
```

- It brings up its own copy of the stack beside production: compose project `atp-ci`, the release's images, a
  throwaway database and settings, a fake Upstox / Dhan broker, ports on 127.0.0.1 only (28000, 28443). Production
  (project `atp`), its `.env`, database, flags and ports are never touched.
- 500 synthetic users (an organisation and a fake Dhan session each) log in over 150 s from 25 client IPs, then use the
  app for 5 minutes (a page load every 30 s on average). The per-IP login limit applies, as for real users.
- The copy and its volumes are removed at the end (`--keep` leaves them up; `deploy/ci/stack.sh down` removes them).
- Run it when no beta user is on: it shares the CPU with production. It needs about 3 GB of free memory.
- The report is `~/atp-loadtest/load-hostinger.md`: p95 per route, server errors, the SLO verdict and the CPU per
  container. Send it, or paste it into `docs/reports/load_test_500.md`.
- `--think 10` runs the busier profile CI also runs; `--users N` another size.

## 12b. The load test against the production stack, in market hours (`atp.sh loadtest`)

Section 12a measures a copy of the stack. `atp.sh loadtest` measures production itself while NSE is open: its API,
worker, Postgres, Redis and Caddy. It uses 500 synthetic PAPER sessions in a throwaway organisation `loadtest-<stamp>`
(no broker credentials) and samples every container's memory and the host's memory and swap (LAB-6). Every exit -
the end, a failure, Ctrl-C - removes what it made; the append-only audit trail stays intact. The command block, the
safety rules, the verdict (API p95 <= 0.5 s, login p95 <= 2 s, zero errors), how to stop it and the manual cleanup are in
`docs/deploy/LOADTEST_REAL_HOST.md`. Deploy the release first, outside market hours (`atp.sh deploy`).

## 12. Troubleshooting

| You see | Do |
|---|---|
| `exit 10 ... fill these in .env` | Fill the named keys (section 4). Only names are printed. |
| `exit 10 OWNER ACTION NEEDED: turn live_trading OFF / phase0_paper_only ON` | Not expected on a Phase 0 install, which seeds both at first boot. Someone changed a flag afterwards (the audit log says who), or `.env` sets `ATP_DEPLOYMENT_PHASE` to something other than `phase0`. Put the flag back in Admin → Controls (section 4a), then run `atp.sh health`. |
| `exit 20 SECRETS_ENCRYPTION_KEY ... NOT the key` | Put the previous key back in `.env`. Rotating it is a separate procedure (`scripts/reencrypt_secrets.py`, docs/OPERATIONS.md), never part of a deploy. |
| `exit 3 migration refused: NSE session open` | Nothing was restarted. Run again after the close. |
| `exit 3 ... session open and N position(s) open` | Run after every open session has closed (NSE 15:30, MCX 23:30 IST), or close the positions. `ATP_ALLOW_SESSION_RESTART=1` overrides for one run. |
| `code-only rollback refused ... not ROLLBACK_SAFE` | Use `atp.sh rollback --restore-db`. |
| `tracked files ... were changed on the server` | Someone edited the checkout. `git -C /opt/atp diff` shows it. Move the setting to `.env`, then `git -C /opt/atp checkout -- .` |
| `failed at the API health check` | The checkout is back on the running release. Read the printed backend log lines, fix, and deploy again. If it migrated, `rollback --restore-db` undoes the migration. |
| `does not match its seal` | The file changed after it was written. Do not restore it. Check R2 versioning for the earlier object, and use another backup. |
| `health: FAILED ... public TCP ports besides 22/80/443` | A container publishes a port on 0.0.0.0. Bind it to 127.0.0.1 in the compose file. |
| `https://... -> 000` | DNS not there yet, or `DOMAIN` and `CADDYFILE` don't match. Check `docker compose ... logs caddy`. |
| `off-site copy failing` | Check the R2 token scope and the endpoint in `.env`, then `atp.sh backup`. |
| `database is ahead of the running code` | Expected after a code-only rollback of a migrating release. Deploy the fixed newer release. |
| `migration failed (exit ...)` | The old release still runs. `atp.sh rollback --restore-db` puts back the pre-migration backup. |
| Container `OOMKilled` | Raise that service's `MEM_*` in `.env` (keep the total under about 7 GB), then `atp.sh deploy`. |

Logs from scheduled runs are in `/opt/atp/.deploy/logs/`. The release history is `atp.sh releases`.

## 13. What `atp.sh` never does

- It never writes, creates or prints `.env` or any secret, and never puts a secret on a command line.
- It never rotates `SECRETS_ENCRYPTION_KEY`.
- It never turns a feature flag on or off: not `live_trading`, not `phase0_paper_only`, not any other. The Phase 0
  first-boot seed (section 4a) is done by the application in its own deployment mode, never by this script.
- It never turns on a LIVE switch or a compose profile.
- It never runs `alembic downgrade`.
- It never drops a production database: `--restore-db` keeps the old one under a new name, and the scratch copy
  that `restore-check` makes is the only database it drops.
- It never pulls application images from a registry.
- It never runs as root, except `bootstrap`.
- `loadtest` creates one synthetic organisation and removes it afterwards; it never touches another organisation, a
  flag, `.env` or a role, and never deletes from or edits the audit ledgers (section 12b).
