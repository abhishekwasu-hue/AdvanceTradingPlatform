# Hostinger runbook (Z11 deploy bundle)

Production host: **Hostinger KVM 2** (2 vCPU / 8 GB RAM / 100 GB NVMe, Ubuntu 24.04, Mumbai, static IPv4). Phase 0
runs PAPER only. Everything on the server goes through one command, `deploy/atp.sh`, run by the owner. The session
that built it never runs it, never logs in to the server, and never sees `.env`.

The Marathi runbook `docs/DEPLOY_HOSTINGER_MR.md` describes the earlier three blocks (`deploy/hostinger/deploy.sh`,
`rollback.sh`, `status.sh`). They still work, but `atp.sh` replaces them. Do not mix the two on the same server:
`atp.sh` tags images by release, and the old blocks do not.

**Owner blockers:** the server IP (`<SERVER_IP>` below), and the R2 bucket and token in the server's `.env`
(section 6). Until R2 is filled in, everything works and backups stay on the server. Every `atp.sh` run then prints
a warning.

## 1. The commands

Run these as the `atp` user on the server (`ssh atp@<SERVER_IP>`). Each one is safe to run again.

| What | Command |
|---|---|
| First install, after the bootstrap (bootstrap checks, deploy, health, first backup and restore check) | `/opt/atp/deploy/atp.sh install` |
| Update to the newest merged code | `/opt/atp/deploy/atp.sh deploy` |
| Deploy a tag or a commit (must be on `main`) | `/opt/atp/deploy/atp.sh deploy <tag-or-sha>` |
| Check everything (read-only) | `/opt/atp/deploy/atp.sh health` |
| See the steps without changing anything | add `--dry-run` to any command, e.g. `/opt/atp/deploy/atp.sh deploy --dry-run` |
| Go back one release (code only) | `/opt/atp/deploy/atp.sh rollback` |
| Go back one release and put the database back to the backup taken just before that release migrated it | `/opt/atp/deploy/atp.sh rollback --restore-db` |
| Back up now (database and files), copy to R2, then verify | `/opt/atp/deploy/atp.sh backup --verify` |
| Prove the newest R2 backup restores (scratch database, row counts) | `/opt/atp/deploy/atp.sh restore-check` |
| Release history | `/opt/atp/deploy/atp.sh releases` |

Exit codes: `0` ok, `1` failed, `2` wrong usage, `3` migration refused because NSE is open, `10` owner action needed
(the message names it), `20` `SECRETS_ENCRYPTION_KEY` changed.

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
3. It prints a read-only deploy key and stops (exit 10). Add the key at GitHub → AdvanceTradingPlatform → Settings →
   Deploy keys, with **Allow write access off**. Then paste the same line again.
4. It clones the repository into `/opt/atp` and hands over to `/opt/atp/deploy/atp.sh install`. That run stops
   (exit 10) until `.env` exists (section 4).

## 4. `.env` (secrets live only on the server)

The script never creates or changes `.env`, and never prints a value from it. You create the file:

```
cp /opt/atp/.env.example /opt/atp/.env && chmod 600 /opt/atp/.env && nano /opt/atp/.env
```

Required. `atp.sh` refuses to run while any of these is empty, `CHANGE_ME`, or a dev default:

| Key | Value |
|---|---|
| `POSTGRES_PASSWORD`, `JWT_SECRET_KEY`, `METRICS_TOKEN`, `BACKUP_ENCRYPTION_PASSPHRASE` | `python3 -c "import secrets;print(secrets.token_urlsafe(48))"` (a different value for each key). Keep a copy of `BACKUP_ENCRYPTION_PASSPHRASE` off the server: without it no backup can be restored. |
| `SECRETS_ENCRYPTION_KEY` | The **same** key the PAPER PC uses. Never a new one: every stored broker and AI credential depends on it. The first deploy records a fingerprint (a hash, not the key). After that, any run with a different key stops with exit 20. |
| `DOMAIN`, `ACME_EMAIL` | Section 5. |

Also set:

```
ENVIRONMENT=production
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

## 5. DNS and TLS

- **No domain yet:** `DOMAIN=<SERVER_IP>`, `ACME_EMAIL=ops@localhost`, `CADDYFILE=Caddyfile.ip`. Caddy serves HTTPS
  on the IP with its own internal certificate. The browser warns once. Plain HTTP only redirects, because the login
  cookie is Secure.
- **Domain:** add an `A` record `atp.<your-domain>` → `<SERVER_IP>` (and `AAAA` only if the server has IPv6). When
  `dig +short atp.<your-domain>` shows the IP, set `DOMAIN=atp.<your-domain>`, `ACME_EMAIL=<your email>` and
  `CADDYFILE=Caddyfile`, then run `/opt/atp/deploy/atp.sh deploy`. Caddy gets a Let's Encrypt certificate over
  ports 80/443 and renews it itself.
- `health` checks `https://$DOMAIN/api/system/health` through Caddy on the server itself, so it does not depend on
  DNS from outside.

## 6. R2 off-site backups (owner blocker)

1. Cloudflare → R2 → **Create bucket** `atp-backups` (location hint Asia-Pacific). Public access stays off.
2. R2 → **Manage API tokens** → **Create API token**: permission **Object Read & Write**, only bucket
   `atp-backups`, no TTL. Not an admin token: this token must not be able to delete the bucket. It is a different
   token from the read-only `R2_*` one used for the history import.
3. Put the endpoint `https://<account id>.r2.cloudflarestorage.com`, the access key ID and the secret into `.env` as
   `OFFSITE_S3_ENDPOINT`, `OFFSITE_S3_ACCESS_KEY` and `OFFSITE_S3_SECRET_KEY`. Do this on the server only.
4. Bucket → Settings → **Object lifecycle rules**: delete `backups/` and `files/` objects after 35 days. Add no rule
   for `wal_archive/`: the sync prunes it.
5. Run `/opt/atp/deploy/atp.sh backup --verify`. Expected: `copied to offsite:atp-backups`, then
   `R2 backup ... restored`, `rows (copy / live): ...`, `restore-check: OK`.

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
   commit (detached).
3. Starts Postgres and Redis and waits for them to be healthy.
4. **Images.** Builds `atp-backend`, `atp-worker`, `atp-frontend` (and `atp-research-worker` with that profile),
   tagged with the first 12 characters of the commit (`deploy/compose.release.yml`). If the images already exist, it
   reuses them.
5. **Migrations.** Compares the database revision with the image's alembic head. If they differ, it first takes an
   **encrypted pre-migration backup** (copied to R2 when configured), then runs `scripts/migrate_guard.py`. The guard
   refuses while NSE is open (exit 3; nothing restarted, so retry after 15:30 IST) and otherwise runs
   `alembic upgrade head`. The script never runs `alembic downgrade`.
6. **Rolling restart, API first.** If the new API does not pass `/api/system/health/deep` in time, the previous API
   image is started again, the worker and frontend are never touched, and the run fails.
7. Starts the worker (exactly one), the frontend, Caddy, backup and off-site services, then records the release in
   `/opt/atp/.deploy/releases.log`.
8. **Health** (section 10), plus a check that the feature flags are byte-for-byte the same as before the deploy.
   `atp.sh` never turns on a flag or a LIVE switch.
9. Installs or refreshes the schedule (section 11) and keeps the images of the last 3 releases (`ATP_KEEP_RELEASES`).

## 9. Rollback, and when a migration makes it unsafe

- `rollback` starts the previous release's images, which are still on the server, so nothing is rebuilt.
- **Code-only (default).** If the release being undone migrated the schema, the older API starts with
  `MIGRATION_ALLOW_DB_AHEAD=1` and runs on the newer schema. This is safe because every migration in this repository
  is additive. `health` then warns `database is ahead` until the newer release is deployed again.
- **When a code-only rollback is unsafe:** the release's migration dropped or renamed a column or table, or changed
  data that the old code reads. Read the migration in `backend/alembic/versions/` first. In that case use
  **`rollback --restore-db`**:
  1. It shows the plan and asks you to type the database name. Nothing changes if you type anything else.
  2. It takes a safety backup.
  3. It restores the pre-migration backup into a **new** database next to the live one, and checks its revision.
  4. Only then does it stop the API and worker, rename the live database to `<db>_pre_rollback_<time>` (**kept**,
     never dropped), give the restored copy the live name, and start the previous release.
  5. Rows written after the pre-migration backup exist only in the kept database. For PAPER this means trades,
     journal entries and settings from that window.
- `--restore-db` goes back exactly one release. To go further, run `rollback` again, once per release.
  `rollback --to <sha>` (code only) goes straight to an older release.
- After a `--restore-db`: if the least-privilege roles are in use (`MIGRATION_DATABASE_URL`, Phase O1), run
  `python scripts/init_db_roles.py` again, because restores do not carry grants. Once you are satisfied, drop the
  kept copy:

  ```
  cd /opt/atp && docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.hostinger.yml \
    -f deploy/compose.release.yml -p atp exec -T postgres psql -U atp_user -d postgres \
    -c 'DROP DATABASE "advance_trading_platform_pre_rollback_<time>"'
  ```

**Rollback rehearsal.** This is a Phase 0 exit criterion. Do it outside market hours, on the server, once before the
first 50 users and again after any non-additive migration. Write the results in the go-live record.

1. `atp.sh releases`: note the running release N and the one before it, N-1.
2. `atp.sh rollback --dry-run`: read the plan.
3. `atp.sh rollback`: expect `rolled back to <N-1>` and `health: OK`. Log in and open the dashboard.
4. `atp.sh deploy <N>`: expect `deployed <N>`, `health: OK` and no `database is ahead` warning.
5. If N migrated the schema, repeat with `rollback --restore-db`, and drop the kept database afterwards.
6. `atp.sh restore-check`: expect `restore-check: OK`.

## 10. Health

`health` is read-only. It checks:

- **Containers:** each one is running and healthy, with exactly one worker.
- **API:** `/api/system/health` and `/api/system/health/deep` (database, migrations, worker heartbeat, Redis).
- **Public HTTPS:** the health URL through Caddy.
- **Release:** the running release, and whether the database is ahead of the code.
- **Encryption key:** the `SECRETS_ENCRYPTION_KEY` fingerprint, and `scripts/check_secrets_key.py` inside the API
  container. That script opens every organisation's data key and prints only counts.
- **Flags:** the flags stored as on, and the `live_trading` flag. It warns while that flag is on, which it is by
  default (kill-flag semantics). Turn it off in Admin → Controls; the script never changes it.
- **Settings:** the LIVE switches, R2 and the HB0 settings.
- **Backups:** the age of the last backup (warning after 26 h), the last off-site sync and the last restore check.
- **Host:** public ports and disk use.

It prints `health: OK` or `health: FAILED`. The status page (Z10 G7) will read the same endpoints.

## 11. Backups and how they are verified

| What | When | Where |
|---|---|---|
| Postgres dump (`pg_dump -Fc`, AES-256, SHA-256 sidecar). Retention 14 days, never fewer than 7 files. | Compose `backup` service every 24 h; before every migration; on `atp.sh backup` | volume `backups` |
| WAL archive (point-in-time recovery, recovery point within 5 min) | continuous | volume `wal_archive` |
| Files archive: `.deploy/` state plus `BACKUP_EXTRA_DIRS`, tar.gz, AES-256, SHA-256. The newest 14 are kept. | cron 02:17 IST daily (`backup --scheduled`, which skips the dump if the service dumped within 20 h) | `/var/backups/atp-files` |
| Off-site copy of all three | `offsite` service hourly; `atp.sh backup` | R2 `atp-backups/{backups,wal_archive,files}` |
| Restore check | cron Sunday 03:43 IST; `atp.sh restore-check` | `.deploy/restore-checks.log` |

`restore-check` does the following:

1. Downloads the newest dump and its SHA-256 sidecar from R2 into `/var/backups/atp-restore-test`.
2. Runs `scripts/backup/verify_backup.sh` on it. That script checks the SHA-256, decrypts the dump, restores it into
   a **scratch** database, and compares the alembic revision and the `tenants`, `orders`, `trades` and `audit_logs`
   row counts with the live database (the copy may have fewer rows, never more). Then it drops the scratch database.
3. Downloads the newest files archive, decrypts it and lists its contents.

Production is never written to. The market data lake is in Postgres (ADR-0013 hot tier), so the dump covers it.

**A real restore** (the database is lost): `atp.sh rollback --restore-db` covers "undo the last release". For a lost
database, follow `docs/OPERATIONS.md` 1.2 and 1.2a (`restore.sh` or the PITR drill) with the backup service's
entrypoint overridden:
`docker compose ... run --rm --entrypoint sh backup /scripts/restore.sh <file>`.

## 12. Troubleshooting

| You see | Do |
|---|---|
| `exit 10 ... fill these in .env` | Fill the named keys (section 4). Only names are printed. |
| `exit 20 SECRETS_ENCRYPTION_KEY ... NOT the key` | Put the previous key back in `.env`. Rotating it is a separate procedure (`scripts/reencrypt_secrets.py`, docs/OPERATIONS.md), never part of a deploy. |
| `exit 3 migration refused: NSE session open` | Nothing was restarted. Run again after 15:30 IST. |
| `tracked files ... were changed on the server` | Someone edited the checkout. `git -C /opt/atp diff` shows it. Move the setting to `.env`, then `git -C /opt/atp checkout -- .` |
| `failed at the API health check` | The previous API runs again. Read the printed backend log lines, fix, and deploy again. |
| `health: FAILED ... public TCP ports besides 22/80/443` | A container publishes a port on 0.0.0.0. Bind it to 127.0.0.1 in the compose file. |
| `https://... -> 000` | DNS not there yet, or `DOMAIN` and `CADDYFILE` don't match. Check `docker compose ... logs caddy`. |
| `off-site copy failing` | Check the R2 token scope and the endpoint in `.env`, then `atp.sh backup`. |
| `database is ahead of the running code` | Expected after a code-only rollback of a migrating release. Deploy the fixed newer release. |
| `migration failed (exit ...)` | The old release still runs. The pre-migration backup name is printed. Fix forward, or use `rollback --restore-db` if the release was already recorded. |
| Container `OOMKilled` | Raise that service's `MEM_*` in `.env` (keep the total under about 7 GB), then `atp.sh deploy`. |

Logs from scheduled runs are in `/opt/atp/.deploy/logs/`. The release history is `atp.sh releases`.

## 13. What `atp.sh` never does

- It never writes, creates or prints `.env` or any secret.
- It never rotates `SECRETS_ENCRYPTION_KEY`.
- It never turns on a feature flag, a LIVE switch or a compose profile.
- It never runs `alembic downgrade`.
- It never drops a database: `--restore-db` keeps the old one under a new name.
- It never pulls application images from a registry.
- It never runs as root, except `bootstrap`.
