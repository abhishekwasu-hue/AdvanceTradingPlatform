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
| `POSTGRES_PASSWORD`, `JWT_SECRET_KEY`, `METRICS_TOKEN`, `BACKUP_ENCRYPTION_PASSPHRASE` | `python3 -c "import secrets;print(secrets.token_urlsafe(48))"` (a different value for each key). Keep a copy of `BACKUP_ENCRYPTION_PASSPHRASE` off the server: without it no backup can be restored or verified. `POSTGRES_PASSWORD` sits inside a database URL, so it must be URL-safe: letters, digits, `-` and `_` only (`token_urlsafe` gives exactly that), never `@ : / ? # %`. |
| `SECRETS_ENCRYPTION_KEY` | The **same** key the PAPER PC uses. Never a new one: every stored broker and AI credential depends on it. The first deploy records a salted scrypt fingerprint of it (`.deploy/secrets_key.fingerprint` with a random per-host salt). After that, any run with a different key stops with exit 20. `health` also checks inside the API container that the key opens every organisation's data key. |
| `DOMAIN`, `ACME_EMAIL` | Section 5. |

Also set:

```
ENVIRONMENT=production
SUPER_ADMIN_EMAILS=<the email you will register with>   # your account gets Admin -> Controls (section 4a)
CADDYFILE=Caddyfile.ip            # Caddyfile once DNS points at the server (section 5)
OFFSITE_REMOTE=offsite:atp-backups
OFFSITE_S3_PROVIDER=Cloudflare
OFFSITE_S3_ENDPOINT=              # section 6 (owner blocker until filled)
OFFSITE_S3_ACCESS_KEY=
OFFSITE_S3_SECRET_KEY=
# COMPOSE_PROFILES=research       # optional services; atp.sh never adds a profile on its own
# BACKUP_EXTRA_DIRS=/srv/atp-blobs   # optional: host directories to include in the encrypted files archive
```

The script reads these optional settings too, but only to report on them (it never changes them):

- `LIVE_*` switches: a warning when any is on (Phase 0 is PAPER only).
- `HISTORY_OWNER_TENANT_ID` and `HISTORY_OWNER_TENANT_NAME` (HB0): a warning when the ID is set without the name.
- The four `R2_*` history-import keys (HB0): a warning when only some are set. This is a read-only token, separate
  from the backup token.

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
- A deploy or rollback whose Caddyfiles differ from the running release's recreates the `caddy` container (about a
  second without the edge; certificates are kept), because Caddy reads the file through a single-file mount.
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

1. Cloudflare → R2 → **Create bucket** `atp-backups` (location hint Asia-Pacific). Public access stays off.
2. Bucket → Settings: turn on **object versioning**, and a **bucket lock** (retention rule) on `backups/` and `files/`
   for at least 14 days if your plan offers it. An overwritten or deleted object can then still be recovered, even
   by someone holding the token.
3. R2 → **Manage API tokens** → **Create API token**: permission **Object Read & Write**, only bucket
   `atp-backups`, no TTL. Not an admin token: this token must not be able to delete the bucket or change its lock.
   It is a different token from the read-only `R2_*` one used for the history import.
4. Put the endpoint `https://<account id>.r2.cloudflarestorage.com`, the access key ID and the secret into `.env` as
   `OFFSITE_S3_ENDPOINT`, `OFFSITE_S3_ACCESS_KEY` and `OFFSITE_S3_SECRET_KEY`. Do this on the server only.
5. Bucket → Settings → **Object lifecycle rules**: delete `backups/` and `files/` objects after 35 days. Add no rule
   for `wal_archive/`: the sync prunes it.
6. Run `/opt/atp/deploy/atp.sh backup --verify`. Expected: `copied to offsite:atp-backups`, then
   `seal (HMAC) verified`, `R2 backup ... restored`, `rows (copy / live): ...`, `audit hash chain intact`,
   `restore-check: OK`.

Until then, `backup` and `restore-check` work on the local copies and print `R2 not configured yet`. Pass
`--require-offsite` to make that a failure.

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
- **Backups:** the age of the last backup (warning after 26 h), the last off-site sync and the last restore check.
- **Host:** public ports and disk use.

It prints `health: OK` (exit 0), `health: FAILED` (exit 1), or `OWNER ACTION NEEDED` (exit 10). The status page
(Z10 G7) will read the same endpoints.

## 11. Backups and how they are verified

| What | When | Where |
|---|---|---|
| Postgres dump (`pg_dump -Fc`, AES-256, SHA-256 sidecar, HMAC seal). Retention 14 days, never fewer than 7 files. | Compose `backup` service every 24 h; on `atp.sh backup`, which also seals the newest | volume `backups` |
| Pre-migration dumps | before every migration | `backups/pre-migration/` (last 3 releases, outside the 14-day retention) |
| WAL archive (point-in-time recovery, recovery point within 5 min) | continuous | volume `wal_archive` |
| Files archive: `.deploy/` state plus `BACKUP_EXTRA_DIRS`, tar.gz, AES-256, SHA-256, HMAC seal. The newest 14 are kept. | cron 02:17 IST daily (`backup --scheduled`, which skips the dump if the service dumped within 20 h and seals the newest) | `/var/backups/atp-files` |
| Off-site copy of all of the above | `offsite` service hourly; `atp.sh backup` | R2 `atp-backups/{backups,wal_archive,files}` |
| Restore check | cron Sunday 03:43 IST; `atp.sh restore-check` | `.deploy/restore-checks.log` |

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
